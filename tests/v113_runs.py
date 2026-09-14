"""V1.13 (ТЗ_04 §V1.13): полный конвейер на реальных книгах, 5 исходных языков.

Запуск: python tests/v113_runs.py [en de fr it nl]   (без аргументов — все)

Книги: books/v1_<lang>.epub (EPUBCheck 0 ошибок, подготовка
.a/reports/v1_assets_prep.md). Пары: <lang> → ru (MVP-пятёрка), режим
bilingual, уровень Learning (ТЗ V1.13).

LLM — детерминированный mock (D3): реальный LLM — BLOCKER (ключей нет),
не утверждается. Остальное — реальные компоненты (spaCy, EPUBCheck, SQLite,
движок epub-translator, CLI и воркер — отдельные процессы ``python -m …``).

Хранилище: data/v1/<lang>/ (gitignored). Перед прогоном каталог языка
удаляется — прогон воспроизводим (артефакты только от этой сессии).

Метрики (всё из артефактов задания и БД, без выдумки): сегменты, охват
выравнивания, термины глоссария, расхождения L2 до/после L3,
применённые/отклонённые правки, карточки + плотность на 1000 слов,
отклонённые карточки по причинам, MWE + грамматические заметки, EPUBCheck,
токены по профилям, время.

Захваты: docs/CONTROL/evidence/V1/v1-13-<lang>.txt (verbatim) +
v1-13-summary.txt (сводная таблица 5 языков).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "probe"))

from fastapi.testclient import TestClient  # noqa: E402
from mock_llm_server import MockLLMServer  # noqa: E402
from test_l3_glossary_fix import _kill_all  # noqa: E402
from test_u1_web import _make_u1_storage  # noqa: E402

from app.storage.jobs_repo import JobRepository  # noqa: E402
from app.web.app import create_app  # noqa: E402

LANGS = ("en", "de", "fr", "it", "nl")
BOOKS = {lang: ROOT / "books" / f"v1_{lang}.epub" for lang in LANGS}
DATA_V1 = ROOT / "data" / "v1"
EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "V1"
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
VERSIONS = f"python={sys.version.split()[0]} spacy={pkg_version('spacy')}"
WAIT_SECONDS = 3600.0

_WORKERS: list[subprocess.Popen[bytes]] = []
_SUMMARY_ROWS: list[str] = []


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_lines(payload: Any, prefix: str = "  | ") -> list[str]:
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    return [f"{prefix}{line}" for line in raw.splitlines()]


def _subset(payload: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: payload.get(key) for key in keys if key in payload}


def _worker(cfg: Path, env_file: Path, log_path: Path) -> subprocess.Popen[bytes]:
    cmd = [
        sys.executable, "-m", "app.workers.run",
        "--config", str(cfg), "--env", str(env_file), "--loop",
    ]
    handle = log_path.open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
    finally:
        handle.close()
    _WORKERS.append(proc)
    return proc


def _stop_worker(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=30)


def _cli(cfg: Path, env_file: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "app.cli", "--config", str(cfg), "--env", str(env_file), *args]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600, check=False)


def _translate_wait(
    client: TestClient,
    cfg: Path,
    env_file: Path,
    *,
    book: Path,
    lang: str,
    log_path: Path,
) -> tuple[int, str, str | None]:
    """CLI ``translate … --wait``; NEEDS_CONFIRMATION — подтверждение через
    API (тот же путь, что U2-доказательство). ``-u`` — «задание: <id>» live."""
    cmd = [
        sys.executable, "-u", "-m", "app.cli",
        "--config", str(cfg), "--env", str(env_file),
        "translate", str(book),
        "--source", lang, "--target", "ru",
        "--mode", "bilingual", "--level", "Learning",
        "--wait", str(WAIT_SECONDS),
    ]
    handle = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
    handle.close()
    job_id: str | None = None
    confirmed = False
    deadline = time.monotonic() + WAIT_SECONDS + 300
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        text = log_path.read_text(encoding="utf-8")
        match = re.search(r"задание: (\S+)", text)
        if match is not None:
            job_id = match.group(1)
            if not confirmed:
                facts = client.get(f"/api/jobs/{job_id}").json()
                if facts.get("status") == "NEEDS_CONFIRMATION":
                    detected = (facts.get("language_detection") or {}).get("detected")
                    client.post(
                        f"/api/jobs/{job_id}/confirm",
                        json={"source_language": detected or lang},
                    )
                    confirmed = True
        time.sleep(0.5)
    code = proc.wait(timeout=300)
    log = log_path.read_text(encoding="utf-8")
    if job_id is None:  # процесс завершился раньше первого чтения лога
        final_match = re.search(r"задание: (\S+)", log)
        job_id = final_match.group(1) if final_match is not None else None
    return code, log, job_id


def _s1_rejected_by_reasons(report: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in report.get("rejected", ()):
        for reason in item.get("reasons", ()):
            counts[str(reason)] = counts.get(str(reason), 0) + 1
    return counts


def _measure(
    repo: JobRepository, job: Any, jdir: Path, wall: float
) -> tuple[list[str], list[str], list[tuple[str, bool]]]:
    """Метрики + вербалистики + пороговая проверка. Возврат:
    (строки метрик, строки вербалистик, проверки (что, ok))."""
    quality = _read_json(jdir / "quality.json") or {}
    l2 = _read_json(jdir / "glossary_consistency.json") or {}
    l3 = _read_json(jdir / "l3_fix_report.json") or {}
    s1 = _read_json(jdir / "vocabulary_cards.json") or {}
    s2 = _read_json(jdir / "mwe_records.json") or {}
    s3 = _read_json(jdir / "grammar_notes.json") or {}
    s5 = _read_json(jdir / "s5_report.json") or {}
    plan = _read_json(jdir / "plan.json") or {}
    tr = _read_json(jdir / "translation_result.json") or {}
    tr_glossary = tr.get("glossary") or {}

    alignment = quality.get("alignment") or {}
    coverage = alignment.get("coverage")
    epubcheck = quality.get("epubcheck") or {}
    s5_epub = (s5.get("epub") or {}).get("epubcheck") or {}
    segments_total = alignment.get("total")
    tokens = dict(repo.profile_tokens(job.id))

    metrics = {
        "статус": job.status.value,
        "сегменты (alignment total)": segments_total,
        "охват выравнивания": coverage,
        "EPUBCheck выхода errors/warnings": (
            epubcheck.get("errors"), epubcheck.get("warnings"),
        ),
        "глоссарий (total/kept/truncated, translation_result)": (
            tr_glossary.get("total_terms"), tr_glossary.get("kept_terms"),
            tr_glossary.get("truncated"),
        ),
        "терминов, проверено на согласованность (L2.6)": l2.get("terms_checked"),
        # Файл перезаписывается каждым _measure_output (QC, старт L3, L3.9):
        # финальное состояние — после L3 при applied > 0; «до L3» — в
        # l3_fix_report.json mismatches_input.
        "расхождений (финальное состояние файла, после L3)": len(l2.get("mismatches") or []),
        "L3 mismatches/attempted/applied/rejected/halted": (
            l3.get("mismatches_input"), l3.get("attempted"), l3.get("applied"),
            l3.get("rejected"), l3.get("halted"),
        ),
        "S1 book_words/candidates/issued/rejected/not_selected": (
            s1.get("book_words"), s1.get("candidates_total"), s1.get("cards_issued"),
            s1.get("cards_rejected"), s1.get("cards_not_selected"),
        ),
        "S1 плотность на 1000 слов (density_actual)": s1.get("density_actual"),
        "S1 отклонённые карточки по причинам": _s1_rejected_by_reasons(s1),
        "S2 candidates/issued/rejected": (
            s2.get("candidates_total"), s2.get("records_issued"), s2.get("records_rejected"),
        ),
        "S3 issued/rejected/not_selected": (
            s3.get("notes_issued"), s3.get("notes_rejected"), s3.get("notes_not_selected"),
        ),
        "S5 coverage/chapters_with_sections/chapters_total": (
            s5.get("coverage"), s5.get("chapters_with_sections"), s5.get("chapters_total"),
        ),
        "S5 learning.epub EPUBCheck errors/warnings": (
            s5_epub.get("errors"), s5_epub.get("warnings"),
        ),
        "learning.epub существует": (jdir / "learning.epub").is_file(),
        "токены по профилям (input/output)": {
            name: (row.input_tokens, row.output_tokens) for name, row in tokens.items()
        },
        "токены всего (БД)": job.tokens_total,
        "время (wall, c)": round(wall, 1),
    }
    metric_lines = ["Метрики (источник: артефакты задания + БД):"]
    metric_lines += _dump_lines(metrics)
    metric_lines.append("")

    verbatim = [
        "--- quality.json (warnings/epubcheck/alignment/glossary)",
        *_dump_lines(_subset(quality, ("warnings", "epubcheck", "alignment", "glossary"))),
        "--- glossary_consistency.json (сводка; mismatches — список)",
        *_dump_lines(_subset(l2, (
            "stage", "terms_checked", "occurrences_checked",
            "consistent_occurrences", "consistent_share", "mismatches",
        ))),
        "--- l3_fix_report.json (сводка)",
        *_dump_lines(_subset(l3, (
            "halted", "fixable_segment_share", "mismatches_input", "attempted",
            "applied", "rejected", "editable_share_before", "editable_share_after",
            "backup",
        ))),
        "--- vocabulary_cards.json (сводка + rejected)",
        *_dump_lines(_subset(s1, (
            "book_words", "density_cap_per_1000", "max_cards", "candidates_total",
            "cards_issued", "cards_rejected", "cards_not_selected",
            "book_example_share", "density_actual", "rejected",
        ))),
        "--- mwe_records.json (сводка + rejected)",
        *_dump_lines(_subset(s2, (
            "candidates_total", "records_issued", "records_rejected",
            "separable_records", "rejected",
        ))),
        "--- grammar_notes.json (сводка)",
        *_dump_lines(_subset(s3, (
            "book_words", "candidates_total", "notes_issued", "notes_rejected",
            "notes_not_selected", "max_notes_per_chapter",
        ))),
        "--- s5_report.json (сводка + zipinfo verbatim)",
        *_dump_lines(_subset(s5, (
            "coverage", "aligned", "total", "chapters_total",
            "chapters_with_sections", "skipped", "exports", "epub", "zipinfo",
        ))),
        "--- plan.json (режим/сегменты)",
        *_dump_lines(_subset(plan, ("mode", "submit_mode", "segments_total", "chapter_count"))),
        "",
    ]

    status = job.status.value
    checks: list[tuple[str, bool]] = [
        (
            "финальный статус COMPLETED/COMPLETED_WITH_WARNINGS",
            status in ("COMPLETED", "COMPLETED_WITH_WARNINGS"),
        )
    ]
    if coverage is not None:
        checks.append(
            (f"охват выравнивания ≥ 0.995 (E5-2/E6): {coverage}", coverage >= 0.995)
        )
    if epubcheck.get("errors") is not None:
        checks.append(
            (
                f"EPUBCheck выхода: 0 ошибок (E5.7): {epubcheck.get('errors')}",
                epubcheck.get("errors") == 0,
            )
        )
    if l3:
        checks.append(("L3: halted = false", l3.get("halted") is False))
    gc = quality.get("glossary_consistency") or {}
    if gc:
        # L3-1: итоговая (после L3) согласованность ≥ 99% — секция
        # quality.json; при applied > 0 L3 пересчитывает отчёт по исправленному
        # файлу (L3.9), иначе секция — измерение до L3 (факт не скрыт).
        checks.append(
            (
                f"L3-1: согласованность (quality.json) ≥ 0.99: "
                f"{gc.get('consistent_share')} "
                f"(расхождений: {gc.get('mismatches')})",
                (gc.get("consistent_share") or 0) >= 0.99,
            )
        )
    if s1:
        checks.append(
            (
                f"S1: issued ≤ max_cards ({s1.get('cards_issued')} ≤ {s1.get('max_cards')})",
                (s1.get("cards_issued") or 0) <= (s1.get("max_cards") or 0),
            )
        )
    if s5:
        checks.append(("S5: learning.epub собран", (jdir / "learning.epub").is_file()))
        checks.append(
            (
                f"S5: learning.epub EPUBCheck 0 ошибок: {s5_epub.get('errors')}",
                s5_epub.get("errors") == 0,
            )
        )
    return metric_lines, verbatim, checks


def _summary_row(lang: str, book: Path, metrics_row: str, verdict: bool) -> None:
    _SUMMARY_ROWS.append(f"{lang}  {book.name}  {metrics_row}  {'PASS' if verdict else 'FAIL'}")


def run_language(lang: str, server: MockLLMServer, base: Path) -> tuple[bool, str]:
    book = BOOKS[lang]
    if not book.is_file():
        return False, f"книга не найдена: {book}"
    work = base / lang
    if work.exists():
        shutil.rmtree(work)
    settings, db, cfg, env_file = _make_u1_storage(base, lang, server.url)
    app = create_app(settings, db=db)
    client = TestClient(app)
    repo = JobRepository(db)
    worker = _worker(cfg, env_file, work / "worker.log")
    failed: list[str] = []
    try:
        t0 = time.monotonic()
        code, cli_log, job_id = _translate_wait(
            client, cfg, env_file, book=book, lang=lang, log_path=work / "cli.log"
        )
        wall = time.monotonic() - t0
        lines = [
            f"дата: {STAMP}\nверсии: {VERSIONS}\n",
            f"K: V1.13 — полный конвейер {lang} → ru, Learning, bilingual; "
            f"книга {book.name}",
            "",
            f"$ python -m app.cli … translate {book.name} --source {lang} --target ru "
            f"--mode bilingual --level Learning --wait {WAIT_SECONDS:.0f}",
            f"rc = {code};  wall = {wall:.0f} c",
            *[f"  | {line}" for line in cli_log.splitlines()[:3]],
            "",
        ]
        if code != 0 or job_id is None:
            lines.append("CLI-прогон не завершился терминальным успехом:")
            lines += [f"  | {line}" for line in cli_log.splitlines()[-15:]]
            _finish(lang, book, lines, False, f"rc={code}, job_id={job_id}")
            return False, f"rc={code}, job_id={job_id}"

        job = repo.require(job_id)
        jdir = settings.job_dir(job.id)
        if job.status.value not in ("COMPLETED", "COMPLETED_WITH_WARNINGS"):
            lines.append(f"статус: {job.status.value}; причина: {job.error_message}")
            _finish(lang, book, lines, False, f"статус {job.status.value}")
            return False, f"статус {job.status.value}: {job.error_message}"

        status_res = _cli(cfg, env_file, "job", "status", job.id)
        lines.append("CLI job status (verbatim):")
        lines += [f"  | {line}" for line in status_res.stdout.splitlines()]
        lines.append("")
        metric_lines, verbatim, checks = _measure(repo, job, jdir, wall)
        lines += metric_lines + verbatim
        lines.append("Пороговая проверка:")
        for what, ok in checks:
            lines.append(f"  {'OK' if ok else 'НЕ СОВПАЛ'}: {what}")
        failed = [what for what, ok in checks if not ok]
        ok = not failed and code == 0
        _summary_row(lang, book, _row_metrics(job, jdir, wall), ok)
        lines.append(
            f"\nВЕРДИКТ: V1.13 {lang} — {'PASS' if ok else 'FAIL'}"
            + ("" if ok else f" (не сошлось: {failed})")
        )
        _finish(lang, book, lines, ok, job.status.value)
        return ok, job.status.value
    finally:
        _stop_worker(worker)
        db.close()


def _row_metrics(job: Any, jdir: Path, wall: float) -> str:
    """Компактные k=v-метрики для сводной строки (источники — те же)."""
    quality = _read_json(jdir / "quality.json") or {}
    l2 = _read_json(jdir / "glossary_consistency.json") or {}
    l3 = _read_json(jdir / "l3_fix_report.json") or {}
    s1 = _read_json(jdir / "vocabulary_cards.json") or {}
    s2 = _read_json(jdir / "mwe_records.json") or {}
    s3 = _read_json(jdir / "grammar_notes.json") or {}
    tr_glossary = (_read_json(jdir / "translation_result.json") or {}).get("glossary") or {}
    alignment = quality.get("alignment") or {}
    epubcheck = quality.get("epubcheck") or {}
    pairs = (
        ("статус", job.status.value),
        ("сегменты", alignment.get("total")),
        ("охват_E6", alignment.get("coverage")),
        ("термины_L2", tr_glossary.get("kept_terms")),
        ("расх_L2", len(l2.get("mismatches") or [])),
        ("правки_L3", l3.get("applied")),
        ("карточки_S1", s1.get("cards_issued")),
        ("плотн_1000", s1.get("density_actual")),
        ("MWE_S2", s2.get("records_issued")),
        ("грамм_S3", s3.get("notes_issued")),
        ("epubcheck_err", epubcheck.get("errors")),
        ("токены", job.tokens_total),
        ("время_c", round(wall)),
    )
    return "  ".join(f"{key}={value}" for key, value in pairs)


def _finish(lang: str, book: Path, lines: list[str], ok: bool, detail: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / f"v1-13-{lang}.txt"
    if not lines or not lines[-1].startswith("ВЕРДИКТ"):
        lines.append(f"\nВЕРДИКТ: V1.13 {lang} — {'PASS' if ok else 'FAIL'} ({detail})")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"[v1.13] {book.name}: {'PASS' if ok else 'FAIL'} "
        f"→ {path.relative_to(ROOT)}"
    )


def write_summary(results: dict[str, tuple[bool, str]]) -> None:
    failed_rows = [
        f"{lang}  FAIL: {detail}"
        for lang, (ok, detail) in sorted(results.items())
        if not ok
    ]
    content = "\n".join(
        [
            f"дата: {STAMP}\nверсии: {VERSIONS}\n",
            "K: V1.13 — сводная таблица 5 языков (детали — v1-13-<lang>.txt)",
            "Колонки: язык  книга  статус  сегменты  охват_E6  термины_L2  расх_L2",
            "         правки_L3  карточки_S1  плотн_1000  MWE_S2  грамм_S3",
            "         epubcheck_err  токены  время_c  итог",
            "",
            *sorted(_SUMMARY_ROWS),
            *failed_rows,
            "",
            f"языков PASS: {sum(1 for ok, _ in results.values() if ok)}/{len(results)}",
            "",
            "ВЕРДИКТ: V1.13 (5/5 языков) — "
            f"{'PASS' if all(ok for ok, _ in results.values()) else 'FAIL'}",
        ]
    )
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / "v1-13-summary.txt"
    path.write_text(content + "\n", encoding="utf-8")
    print(f"[v1.13] сводка → {path.relative_to(ROOT)}")


def main() -> int:
    langs = [arg for arg in sys.argv[1:] if arg in LANGS] or list(LANGS)
    missing = [BOOKS[code].name for code in langs if not BOOKS[code].is_file()]
    if missing:
        print(f"книги отсутствуют: {missing}", file=sys.stderr)
        return 2
    base = DATA_V1
    base.mkdir(parents=True, exist_ok=True)
    server = MockLLMServer()
    server.start()
    results: dict[str, tuple[bool, str]] = {}
    try:
        for lang in langs:
            print(f"[v1.13] запуск {lang} → ru …")
            try:
                results[lang] = run_language(lang, server, base)
            except Exception as exc:
                # сбой одного языка фиксируем и гоняем остальные (честный FAIL)
                results[lang] = (False, f"{type(exc).__name__}: {exc}")
                print(f"[v1.13] {lang}: ОШИБКА ДРАЙВЕРА: {exc}", file=sys.stderr)
        write_summary(results)
        return 0 if all(ok for ok, _ in results.values()) else 1
    finally:
        _kill_all(_WORKERS)
        server.stop()


if __name__ == "__main__":
    sys.exit(main())
