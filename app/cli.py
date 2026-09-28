"""Командный интерфейс ``epubtranslator`` (U2, ТЗ_04 §U2).

CLI поверх тех же сервисов, что API (U2-1: собственной бизнес-логики нет —
проверка по вызываемым сервисам):
- ``app.services.jobs`` — ``create_job`` (тот же вызов, что ``POST /api/jobs``);
- ``app.services.analyze`` — ``scan_book`` (тот же вызов, что ``POST /api/books``);
- ``app.storage.jobs_repo`` — ``list_jobs``/``get``/``request_resume``/
  ``request_cancel`` (то же, что job-endpoint'ы API, E3.1);
- ``app.web.views`` — ``job_facts``/``progress_facts``/``languages_view``
  (то же, что ``GET /api/jobs/{id}``/``GET /api/jobs``/``GET /api/languages``);
- ``app.epub.reader`` + ``app.adapters.epub_engine.epubcheck`` — ``validate``
  (тот же ридер E2.5 и тот же EPUBCheck E5.7, что стадия валидации выхода);
- ``app.languages.resolver`` — предотбор пары (тот же механизм, что
  предпроверка ``dc:language`` в ``create_job``, L0.7).

Коды выхода (``app.core.errors``, U2-2): 0 — успех; 1 — неуспешное задание
(FAILED/CANCELLED/INTERRUPTED) либо прочие; 2 — конфигурация; 3 — валидация
входа; 4 — недоступность LLM; 5 — отказ валидации выхода. Успешный код выхода
при неуспешном задании — дефект: исход задания отражают ``translate --wait``
и ``job status``.

Запуск: ``python -m app.cli <команда> …`` (после установки —
``epubtranslator <команда>``). Воркер — отдельный процесс
(``python -m app.workers.run --loop``); CLI не запускает его.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.adapters.epub_engine.epubcheck import run_epubcheck
from app.core.config import Settings, load_settings
from app.core.errors import (
    AppError,
    ConfigurationError,
    InputValidationError,
    JobStateError,
)
from app.domain.job import TERMINAL_STATES, Job, JobStatus
from app.epub.reader import read_epub
from app.languages.resolver import PairResolver, PairStatus
from app.learning import card_export
from app.services.analyze import scan_book
from app.services.jobs import create_job
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.web import views

_OK_STATES = (JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_WARNINGS)
_UNOK_TERMINAL = (JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED)


def _open_repo(settings: Settings) -> tuple[Database, JobRepository]:
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    return db, JobRepository(db)


def _job_or_input_error(repo: JobRepository, job_id: str) -> Job:
    job = repo.get(job_id)
    if job is None:
        raise InputValidationError(f"задание не найдено: {job_id}")
    return job


# ------------------------------------------------------------------ команды


def cmd_analyze(settings: Settings, args: argparse.Namespace) -> int:
    """Разбор книги: заголовок, статистика, L0-решение по языку (тот же
    сервис, что загрузка в UI)."""
    path = Path(args.input)
    if not path.is_file():
        raise InputValidationError(f"файл не найден: {path}")
    meta = scan_book(settings, path)
    confidence = (
        f" (уверенность {meta['confidence']})" if meta["confidence"] is not None else ""
    )
    print(f"заголовок: {meta['title']}")
    print(f"dc:language: {meta['dc_language'] or '—'}")
    print(f"определён: {meta['detected'] or '—'}{confidence}")
    probabilities = ", ".join(f"{k} {v}" for k, v in meta["probabilities"].items())
    print(f"вероятности: {probabilities or '—'}")
    accepted = (
        f" (принят: {meta['accepted_language']})" if meta["accepted_language"] else ""
    )
    print(f"решение L0.5: {meta['decision']}{accepted}")
    if meta["reasons"]:
        print(f"причины: {'; '.join(meta['reasons'])}")
    stats = meta["stats"]
    print(
        f"статистика: глав {stats['chapter_count']}, "
        f"сегментов {stats['segment_count']}, слов {stats['word_count']}"
    )
    return 0


def cmd_translate(settings: Settings, args: argparse.Namespace) -> int:
    """Создание задания перевода (тот же ``create_job``, что API)."""
    path = Path(args.input)
    if not path.is_file():
        raise InputValidationError(f"файл не найден: {path}")
    # L0.7: предпроверка ЗАЯВЛЕННОЙ пары до создания (fail closed; тот же
    # резолвер, что предпроверка dc:language внутри create_job). Язык задания
    # при этом определяет L0.5 (детектор + dc:language + подтверждение).
    resolver = PairResolver(
        registry_root=settings.languages_root, prompts_root=settings.prompts_dir
    )
    report = resolver.resolve_pair(args.source, args.target)
    if report.status is PairStatus.UNSUPPORTED:
        raise InputValidationError(
            f"языковая пара {report.source} → {report.target} не поддерживается "
            f"(L0.2); не хватает: {'; '.join(report.missing)}"
        )
    db, repo = _open_repo(settings)
    try:
        job = create_job(
            repo,
            settings,
            source_path=path,
            target_language=args.target,
            mode=args.mode,
            learning_level=args.level,
            learning_cefr=args.cefr,
        )
    finally:
        db.close()
    print(f"задание: {job.id}")
    print(f"статус: {job.status.value}")
    # MTPE TZ §6: способ перевода — на отдельной строке (из конфига; создание
    # задания на неполных настройках отклонено выше — SettingsIncompleteError).
    print(f"способ перевода: {job.pipeline}")
    if args.wait is None:
        print("воркер — отдельный процесс: python -m app.workers.run --loop")
        return 0
    return _wait_terminal(settings, job.id, args.wait)


def _wait_terminal(settings: Settings, job_id: str, timeout_seconds: float) -> int:
    """Ожидание терминального статуса (опрос БД — тот же источник, что UI)."""
    deadline = time.monotonic() + timeout_seconds
    db, repo = _open_repo(settings)
    try:
        while time.monotonic() < deadline:
            job = repo.get(job_id)
            if job is not None and job.status in TERMINAL_STATES:
                break
            time.sleep(0.5)
        job = repo.get(job_id)
    finally:
        db.close()
    if job is None:
        raise InputValidationError(f"задание не найдено: {job_id}")
    if job.status not in TERMINAL_STATES:
        print(
            f"таймаут: задание не достигло терминального статуса за "
            f"{timeout_seconds:.0f} c — запущен ли воркер?",
            file=sys.stderr,
        )
        return 1
    if job.status in _OK_STATES:
        print(f"исход: {job.status.value}")
        return 0
    print(f"исход: {job.status.value}", file=sys.stderr)
    if job.error_message:
        print(f"причина: {job.error_message}", file=sys.stderr)
    return 1


def cmd_job_list(settings: Settings, args: argparse.Namespace) -> int:
    db, repo = _open_repo(settings)
    try:
        jobs = repo.list_jobs()
        rows = []
        for job in jobs:
            validation = views.read_json(settings.job_dir(job.id) / "validation.json")
            display = views.progress_facts(job, validation)["display"]
            rows.append((job, display))
    finally:
        db.close()
    if not rows:
        print("заданий нет")
        return 0
    for job, display in rows:
        level = f", {job.learning_level}" if job.learning_level else ""
        source = job.source_language or "—"
        print(
            f"{job.id}  {job.status.value:<24}  {source}→{job.target_language}"
            f"  {job.mode}{level}  {display}"
        )
    return 0


def _way_phase_line(facts: dict[str, Any]) -> str:
    """Строка «способ перевода» + текущая фаза (MTPE M6, req 10).

    mt_pe в LLM-фазе — способ + `m_draft`/`m_postedit` + дробь §4.4;
    `m_assemble` (не LLM) и single — только способ."""
    way = facts["pipeline"]
    phase = facts["phase"] if way == "mt_pe" else None
    if phase is None:
        return f"способ перевода: {way}"
    frac = facts["phase_progress"]
    frac_str = f" · {frac * 100:.0f}%" if frac is not None else ""
    return f"способ перевода: {way} · фаза: {phase}{frac_str}"


def cmd_job_status(settings: Settings, args: argparse.Namespace) -> int:
    """Факты задания (тот же ``views.job_facts``, что API). Код выхода по
    исходу: неуспешное терминальное задание — ≠ 0 (дефект — иначе)."""
    db, repo = _open_repo(settings)
    try:
        job = _job_or_input_error(repo, args.job_id)
        facts = views.job_facts(settings, repo, job, log_lines=0)
    finally:
        db.close()
    print(f"задание: {facts['id']}")
    print(f"статус: {facts['status']}")
    # MTPE M6 (req 10): способ перевода + текущая фаза (mt_pe — m_draft/
    # m_postedit/m_assemble и дробь §4.4; single — только способ).
    print(_way_phase_line(facts))
    source = facts["source_language"] or "—"
    print(f"пара: {source} → {facts['target_language']}  режим: {facts['mode']}")
    level = f", уровень: {facts['learning_level']}" if facts["learning_level"] else ""
    cefr = f", CEFR: {facts['learning_cefr']}" if facts["learning_cefr"] else ""
    if level or cefr:
        print(f"learning{level}{cefr}")
    print(f"прогресс: {facts['progress_display']}")
    segments = facts["segments"]
    if segments is not None:
        print(f"сегменты: {segments['processed']} / {segments['total']}")
    alignment = facts["alignment"]
    if alignment["coverage"] is not None:
        print(
            f"охват выравнивания (E6): {alignment['aligned']} / "
            f"{alignment['total']} ({alignment['coverage'] * 100:.1f}%)"
        )
    print(f"токенов всего: {facts['tokens']['total']}")
    print(f"прошло: {facts['elapsed_seconds']:.0f} c")
    if facts["eta_seconds"] is not None:
        print(f"остаток (оценка): {facts['eta_seconds']:.0f} c")
    if facts["failed_segments"]:
        print(f"неудавшиеся сегменты: {facts['failed_segments']}")
    if facts["retries"]:
        print(f"повторов: {facts['retries']}")
    for warning in facts["warnings"]:
        print(f"предупреждение: {warning}")
    if facts["error"]:
        print(f"ошибка: {facts['error']}")
    if facts["status"] == "NEEDS_CONFIRMATION":
        print("требуется подтверждение (выбор исходного языка) — через UI /jobs/<id>")
    if facts["result_ready"]:
        files = [
            n for n, exists in facts["files"].items() if exists and n != "quality.json"
        ]
        print(f"результат готов: {', '.join(files) or '—'}")
    if facts["status"] in _UNOK_TERMINAL:
        return 1
    return 0


def cmd_job_resume(settings: Settings, args: argparse.Namespace) -> int:
    db, repo = _open_repo(settings)
    try:
        _job_or_input_error(repo, args.job_id)
        job = repo.request_resume(args.job_id)
    finally:
        db.close()
    print(f"задание {job.id}: запрос возобновления принят (исполнит воркер)")
    return 0


def cmd_job_cancel(settings: Settings, args: argparse.Namespace) -> int:
    db, repo = _open_repo(settings)
    try:
        _job_or_input_error(repo, args.job_id)
        job = repo.request_cancel(args.job_id)
    finally:
        db.close()
    print(f"задание {job.id}: запрос отмены принят (исполнит воркер)")
    return 0


def cmd_validate(settings: Settings, args: argparse.Namespace) -> int:
    """Валидация EPUB: структура (тот же ридер E2.5) + EPUBCheck (E5.7).

    Повреждённый файл — отказ валидации входа (код 3); недоступный
    EPUBCheck — отказ конфигурации (код 2, fail closed, Rule 6).
    """
    path = Path(args.file)
    if not path.is_file():
        raise InputValidationError(f"файл не найден: {path}")
    scan_dir = settings.working_dir / f"validate-{uuid.uuid4().hex[:8]}"
    try:
        read_epub(path, scan_dir, settings)
    finally:
        shutil.rmtree(scan_dir, ignore_errors=True)
    outcome = run_epubcheck(
        settings.epubcheck_jar_path, path, settings.epubcheck.timeout_seconds
    )
    if not outcome.available:
        raise ConfigurationError(
            f"EPUBCheck недоступен (fail closed): {outcome.unavailable_reason}"
        )
    fatal_errors = [*outcome.fatals, *outcome.errors]
    if fatal_errors:
        for message in fatal_errors[:20]:
            print(message, file=sys.stderr)
        if len(fatal_errors) > 20:
            print(f"… ещё {len(fatal_errors) - 20}", file=sys.stderr)
        raise InputValidationError(
            f"{outcome.version}: FATAL {len(outcome.fatals)}, "
            f"ERROR {len(outcome.errors)} — валидация входного файла не пройдена"
        )
    print(f"{outcome.version}: 0 FATAL, 0 ERROR, WARNING {len(outcome.warnings)}")
    return 0


def cmd_languages(settings: Settings, args: argparse.Namespace) -> int:
    """Языки и пары (тот же ``views.languages_view``, что ``GET /api/languages``)."""
    data = views.languages_view(settings)
    print("языки:")
    for lang in data["languages"]:
        spacy = lang["spacy_model"] or "—"
        print(
            f"  {lang['code']:<4} {lang['name']:<12} spacy: {spacy:<28} "
            f"{lang['cefr_label']}"
        )
    print("пары (резолвер L0):")
    for pair in data["pairs"]:
        line = (
            f"  {pair['source']} → {pair['target']}: {pair['status']} "
            f"(создаётся: {'да' if pair['creatable'] else 'нет'})"
        )
        if pair["missing"]:
            line += f" — не хватает: {'; '.join(pair['missing'])}"
        print(line)
    return 0


def cmd_export_cards(settings: Settings, args: argparse.Namespace) -> int:
    """Экспорт карточек задания (артефакты S5; имена — ``CARDS_FILES``)."""
    db, repo = _open_repo(settings)
    try:
        job = _job_or_input_error(repo, args.job_id)
        if job.status not in _OK_STATES:
            raise JobStateError(
                f"карточки не готовы: статус задания {job.status.value} "
                f"(доступно только COMPLETED/COMPLETED_WITH_WARNINGS)"
            )
        name = card_export.CARDS_FILES[args.format]
        path = settings.job_dir(job.id) / name
        if not path.is_file():
            raise JobStateError(
                f"артефакт карточек отсутствует: {name} "
                f"(уровень задания может не включать карточки)"
            )
        data = path.read_bytes()
    finally:
        db.close()
    if args.output:
        out = Path(args.output)
        out.write_bytes(data)
        print(f"записано: {out} ({len(data)} байт, формат: {args.format})")
    else:
        sys.stdout.buffer.write(data)
    return 0


# ------------------------------------------------------------------ main


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="epubtranslator",
        description="EPUBTranslator: перевод EPUB и учебные материалы (CLI, U2)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="YAML-конфигурация (по умолчанию config/config.yaml)",
    )
    parser.add_argument(
        "--env", type=Path, default=Path(".env"), help="env-файл (по умолчанию .env)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="разбор книги: заголовок + язык (L0)")
    p_analyze.add_argument("input", help="путь к EPUB")
    p_analyze.set_defaults(func=cmd_analyze)

    p_translate = sub.add_parser("translate", help="создать задание перевода")
    p_translate.add_argument("input", help="путь к EPUB")
    p_translate.add_argument("--source", required=True, help="исходный язык (предотбор пары)")
    p_translate.add_argument("--target", required=True, help="целевой язык")
    p_translate.add_argument(
        "--mode", required=True, help="режим задания (bilingual|translation)"
    )
    p_translate.add_argument("--level", default=None, help="уровень learning (опционально)")
    p_translate.add_argument(
        "--cefr", default=None, help="CEFR-уровень пользователя (опционально)"
    )
    p_translate.add_argument(
        "--wait",
        nargs="?",
        const=600.0,
        default=None,
        type=float,
        metavar="SECONDS",
        help="ждать терминальный статус (без числа — 600 c); исход отражается кодом выхода",
    )
    p_translate.set_defaults(func=cmd_translate)

    p_job = sub.add_parser("job", help="задания")
    job_sub = p_job.add_subparsers(dest="job_command", required=True)
    job_sub.add_parser("list", help="список заданий").set_defaults(func=cmd_job_list)
    p_status = job_sub.add_parser("status", help="состояние задания")
    p_status.add_argument("job_id")
    p_status.set_defaults(func=cmd_job_status)
    p_resume = job_sub.add_parser("resume", help="возобновить задание с паузы")
    p_resume.add_argument("job_id")
    p_resume.set_defaults(func=cmd_job_resume)
    p_cancel = job_sub.add_parser("cancel", help="отменить задание")
    p_cancel.add_argument("job_id")
    p_cancel.set_defaults(func=cmd_job_cancel)

    p_validate = sub.add_parser("validate", help="валидация EPUB (структура + EPUBCheck)")
    p_validate.add_argument("file", help="путь к EPUB")
    p_validate.set_defaults(func=cmd_validate)

    p_lang = sub.add_parser("languages", help="языки и пары (резолвер L0)")
    p_lang.set_defaults(func=cmd_languages)

    p_export = sub.add_parser("export", help="экспорт артефактов задания")
    export_sub = p_export.add_subparsers(dest="export_command", required=True)
    p_cards = export_sub.add_parser("cards", help="экспорт карточек")
    p_cards.add_argument("job_id")
    p_cards.add_argument(
        "--format",
        required=True,
        choices=sorted(card_export.CARDS_FILES),
        help="формат экспорта (имена — card_export.CARDS_FILES)",
    )
    p_cards.add_argument(
        "--output", default=None, help="файл-приёмник (по умолчанию stdout)"
    )
    p_cards.set_defaults(func=cmd_export_cards)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        settings = load_settings(args.config, args.env)
    except ConfigurationError as exc:
        print(f"ошибка конфигурации: {exc.message}", file=sys.stderr)
        return exc.exit_code
    try:
        code: int = args.func(settings, args)
    except AppError as exc:
        print(f"ошибка: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # непредвиденное: fail closed (Rule 6)
        print(f"непредвиденная ошибка: {exc!r}", file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
