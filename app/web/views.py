"""Модели представлений U1/U3: страницы и API читают ТОЛЬКО эти функции.

Честность прогресса (У1.1/У1.2): отображаемые величины — чистые функции
состояния в БД (``jobs.progress`` + артефакт ``validation.json``) и
измеренных отчётов задания. Процент показывается только для измеренных
величин (охват выравнивания E6, доли QC); остальное — реальные единицы
«глава N / M · сегментов X / Y». Фиктивного процента нет по построению.

Дублирования бизнес-логики нет: статусы пар — ``services.languages``
(L0-5), CEFR-подпись — ``languages.cefr.label`` (L1-8), фазы перевода —
``workers.pipeline.translation_phases`` (E5.3, единый источник),
реестр задач и резолвер промптов — ``app.llm`` (E4.5).

U3: строки интерфейса — только из ``app.web.strings`` (критерий U3-1);
статусы заданий, коды языков, имена задач/файлов и причины отказов
выводятся verbatim (3.1.3).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import PROFILE_NAMES, Settings
from app.core.errors import AppError
from app.domain.job import LEARNING_LEVELS, TERMINAL_STATES, WORKING_STATES, Job, JobStatus
from app.languages.cefr import CEFR_ORDER, language_cefr_label
from app.languages.registry import load_registry
from app.learning import SUBSTAGE_LEVELS
from app.llm.prompts import (
    LEVEL_GENERIC,
    LEVEL_PAIR,
    LEVEL_SOURCE,
    VERSION_FILE_RE,
    PromptResolver,
)
from app.llm.tasks import all_tasks
from app.services.jobs import create_baseline
from app.services.languages import matrix_report
from app.storage.jobs_repo import JobRepository
from app.web.strings import UI_STRINGS
from app.workers.pipeline import CHAPTER_LABEL_PREFIX, translation_phases

_S = UI_STRINGS

# Серия R §4: 16 статусов сводятся к пяти семантическим группам, по одному
# токену окраски на группу (--status-<group>-fg, style.css :root).
# Единственная таблица соответствия — здесь; в шаблонах и в JS условий
# по статусу нет (шаблоны и клиент используют статус_group/статус_glyph).
# Цвет — вторичный признак: первичные — текст (имя статуса verbatim)
# и глиф группы.
STATUS_GROUP: dict[str, str] = {
    "CREATED": "wait",
    "NEEDS_CONFIRMATION": "wait",
    "VALIDATING": "run",
    "PLANNING": "run",
    "TRANSLATING": "run",
    "ANALYZING": "run",
    "GENERATING": "run",
    "VALIDATING_OUTPUT": "run",
    "GLOSSARY_FIX": "run",
    "LEARNING": "run",
    "COMPLETED": "ok",
    "COMPLETED_WITH_WARNINGS": "warn",
    "PAUSED": "warn",
    "INTERRUPTED": "warn",
    "FAILED": "err",
    "CANCELLED": "err",
}

#: Глиф группы (серия R §4): до статуса — текст полного имени verbatim.
STATUS_GLYPH: dict[str, str] = {
    "wait": "○",
    "run": "◐",
    "ok": "✓",
    "warn": "!",
    "err": "✕",
}


def status_group(status: str) -> str:
    """Статус задания → семантическая группа (единая таблица STATUS_GROUP)."""
    return STATUS_GROUP[status]


def status_glyph(status: str) -> str:
    """Глиф группы статуса (серия R §4)."""
    return STATUS_GLYPH[STATUS_GROUP[status]]


# Серия R §10: группы строк списка заданий. Единственная точка расчёта —
# здесь: сервер кладёт готовое значение в поле ``group`` API-строки и в
# no-JS разметку; JS раскладывания по группам читает поле, не пересчитывает.
#: run/wait-статусы всегда «сейчас»; остальные — по времени последнего
#: обновления: сегодня (с локальной полуночи) / раньше.
RUN_WAIT_GROUPS = ("run", "wait")


def today_start_epoch() -> float:
    """Локальная полуночь сегодня, epoch — для группировки строк (§10)."""
    now = datetime.now()
    return datetime(now.year, now.month, now.day).timestamp()


def job_list_group(
    status: str, updated_at: float, today_start: float | None = None
) -> str:
    """Группа строки списка: ``now`` / ``today`` / ``earlier`` (§10)."""
    if status_group(status) in RUN_WAIT_GROUPS:
        return "now"
    if today_start is None:
        today_start = today_start_epoch()
    return "today" if updated_at >= today_start else "earlier"


def human_duration(total_seconds: float) -> str:
    """§10: «12 мин», после часа — «1 ч 20 мин» (единая точка формата)."""
    minutes = int(max(0.0, total_seconds) // 60)
    if minutes < 60:
        return _S["time_min"].format(n=minutes)
    hours, minutes = divmod(minutes, 60)
    return _S["time_hm"].format(h=hours, m=minutes)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _tail(path: Path, lines: int) -> list[str]:
    if not path.is_file() or lines <= 0:
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines()[-lines:]
    except OSError:
        return []


def human_ts(epoch: float | None) -> str:
    """Эпоха БД → человекочитаемая строка локального времени (или «—»).

    До V2 UI показывал сырые epoch-секунды (``created_at | round(0)``) —
    нечитабельно для пользователя; единый формат в HTML и JSON."""
    if not epoch:
        return "—"
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def human_size(num_bytes: int | None) -> str:
    """Размер файла в человекочитаемых единицах (измеренное, не оценка)."""
    if num_bytes is None:
        return "—"
    for divider, key in (
        (1024 * 1024 * 1024, "common_size_gb"),
        (1024 * 1024, "common_size_mb"),
        (1024, "common_size_kb"),
    ):
        if num_bytes >= divider:
            return f"{num_bytes / divider:.1f} {_S[key]}"
    return f"{num_bytes} {_S['common_size_b']}"


def day_to_epoch(value: str | None, *, end_of_day: bool = False) -> float | None:
    """Дата YYYY-MM-DD (локальное время) → epoch; некорректная — None.

    Единый источник для страницы /jobs и фильтра GET /api/jobs (U3 3.4.17)."""
    if not value:
        return None
    try:
        moment = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    if end_of_day:
        moment = moment.replace(hour=23, minute=59, second=59)
    return moment.timestamp()


# ---------------------------------------------------------------- прогресс (U1-1/U1-2)


def _phase_weights(phases: list[tuple[str, str]]) -> list[float]:
    """Веса фаз движка (epub-translator): TOC 0.05, metadata 0.05,
    остальные 1.0 — поровну между главами. Та же формула, что в движке —
    она зафиксирована E5-3 (progress_mapping), здесь она нужна только для
    отображения «какая фаза активна по дробной БД»."""
    chapters = sum(1 for label, _doc in phases if label.startswith(CHAPTER_LABEL_PREFIX))
    toc = 0.05 if any(label == "TOC" for label, _doc in phases) else 0.0
    meta = 0.05 if any(label == "metadata" for label, _doc in phases) else 0.0
    chapter_weight = (1.0 - toc - meta) / chapters if chapters else 0.0
    weights: list[float] = []
    for label, _doc in phases:
        if label == "TOC":
            weights.append(toc)
        elif label == "metadata":
            weights.append(meta)
        else:
            weights.append(chapter_weight)
    return weights


def progress_facts(
    job: Job, validation: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Отображаемый прогресс — чистая функция (jobs.progress, validation.json).

    Активная фаза определяется по дробной движка (веса — как в движке,
    E5.3): ``completed`` — число фаз с накопленным весом <= progress.
    Возврат: ``display`` (строка для UI), ``phase`` (активная фаза),
    ``completed``/``total_phases``, ``segments`` (обработанные/всего).
    """
    if validation is None:
        return {
            "display": _S["progress_awaiting"],
            "phase": None,
            "completed": 0,
            "total_phases": 0,
            "segments": None,
        }
    phases = translation_phases(validation)
    total = len(phases)
    if total == 0:
        return {
            "display": _S["progress_no_phases"],
            "phase": None,
            "completed": 0,
            "total_phases": 0,
            "segments": None,
        }
    weights = _phase_weights(phases)
    cumulative: list[float] = []
    acc = 0.0
    for weight in weights:
        acc += weight
        cumulative.append(acc)
    p = min(max(job.progress, 0.0), 1.0)
    completed = sum(1 for c in cumulative if c <= p + 1e-9)
    per_chapter = [int(x) for x in validation.get("per_chapter_segments", [])]
    total_segments = sum(per_chapter)
    processed = 0
    for label, _doc in phases[:completed]:
        if label.startswith(CHAPTER_LABEL_PREFIX):
            index = int(label.split()[1]) - 1
            if 0 <= index < len(per_chapter):
                processed += per_chapter[index]
    if completed >= total:
        display = _S["progress_all_finished"].format(total=total_segments)
        phase: str | None = None
    else:
        label, _doc = phases[completed]
        phase = label
        if label.startswith(CHAPTER_LABEL_PREFIX):
            chapter_no = int(label.split()[1])
            chapter_count = int(validation["stats"]["chapter_count"])
            display = _S["progress_chapter"].format(
                no=chapter_no,
                count=chapter_count,
                processed=processed,
                total=total_segments,
            )
        else:
            display = _S["progress_phase"].format(
                label=label, processed=processed, total=total_segments
            )
    return {
        "display": display,
        "phase": phase,
        "completed": completed,
        "total_phases": total,
        "segments": {"processed": processed, "total": total_segments},
    }


def elapsed_and_eta(job: Job, transitions: list[Any]) -> tuple[float, float | None]:
    """Время работы (E7/M7) и оценка остатка (модельная, помечена в UI).

    «Прошло» — сумма интервалов в рабочих статусах (``WORKING_STATES``),
    восстановленных по таблице transitions: с момента создания задание
    считается в CREATED, далее — в статусе последнего перехода. Паузы и
    ожидания не засчитываются. Задание без переходов (ещё не запущено) —
    wall-clock с момента создания: текущий расчёт для него корректен (M7).
    В статусе PAUSED ETA не выводится (UI показывает «на паузе с …»).
    """
    if not transitions:
        elapsed = max(0.0, time.time() - job.created_at)
    else:
        elapsed = 0.0
        status = JobStatus.CREATED
        t_start = job.created_at
        for record in transitions:
            t_end = record.at
            if status in WORKING_STATES:
                elapsed += max(0.0, t_end - t_start)
            status = record.to_status
            t_start = t_end
        if status in WORKING_STATES:
            elapsed += max(0.0, time.time() - t_start)
    eta: float | None = None
    if (
        job.status is not JobStatus.PAUSED
        and 0.0 < job.progress < 1.0
        and elapsed > 0
    ):
        eta = elapsed * (1.0 - job.progress) / job.progress
    return elapsed, eta


def job_facts(
    settings: Settings,
    repo: JobRepository,
    job: Job,
    *,
    log_lines: int = 200,
) -> dict[str, Any]:
    """Все фактические данные задания (У1.5) — только из БД и артефактов."""
    jdir = settings.job_dir(job.id)
    validation = read_json(jdir / "validation.json")
    alignment_report = read_json(jdir / "alignment_report.json")
    quality = read_json(jdir / "quality.json")
    fill = read_json(jdir / "fill_failures.json")
    progress = progress_facts(job, validation)
    transitions = repo.list_transitions(job.id)
    elapsed, eta = elapsed_and_eta(job, transitions)
    tokens: dict[str, Any] = {}
    for profile, pt in repo.profile_tokens(job.id).items():
        tokens[profile] = {
            "input": pt.input_tokens,
            "input_cache": pt.input_cache_tokens,
            "output": pt.output_tokens,
            "total": pt.total,
        }
    tokens["total"] = job.tokens_total
    failed = int((fill or {}).get("count", 0))
    retries = sum(int(e.get("retried_count", 0)) for e in (fill or {}).get("events", []))
    aligned = (
        int(alignment_report["aligned"]) if alignment_report is not None else None
    )
    total_segments = (
        int(alignment_report["total"]) if alignment_report is not None else None
    )
    warnings: list[str] = list((quality or {}).get("warnings", []))
    files = {
        name: (jdir / name).is_file()
        for name in (
            "target.epub",
            "learning.epub",
            "cards.csv",
            "cards.anki.tsv",
            "cards.json",
            "quality.json",
        )
    }
    result_ready = job.status in (
        JobStatus.COMPLETED,
        JobStatus.COMPLETED_WITH_WARNINGS,
    )
    # R7 (ТЗ §9): данные листа экспорта — доступность объектов (отчёты —
    # по любому из трёх, карточки — по любой из трёх) и макеты билингвы
    # (только bilingual + APPEND_BLOCK: только там есть оригинальные
    # блоки; единый источник — alignment_report, как в export_artifacts).
    export_facts = {
        "objects": {
            "translation": files["target.epub"],
            "learning": files["learning.epub"],
            "cards": any(
                files[name]
                for name in ("cards.csv", "cards.anki.tsv", "cards.json")
            ),
            "reports": any(
                (jdir / name).is_file()
                for name in ("quality.json", "alignment_report.json", "validation.json")
            ),
        },
        "layouts": bool(
            job.mode == "bilingual" and alignment_report is not None
            and alignment_report.get("submit_mode") == "APPEND_BLOCK"
        ),
    }
    paused_at = next(
        (r.at for r in reversed(transitions) if r.to_status is JobStatus.PAUSED),
        None,
    )
    return {
        "id": job.id,
        "status": job.status.value,
        # Серия R §4: группа и глиф — из единой таблицы (выше); клиент
        # не содержит собственного соответствия статус→группа.
        "status_group": status_group(job.status.value),
        "status_glyph": status_glyph(job.status.value),
        "status_terminal": job.status in TERMINAL_STATES,
        "source_language": job.source_language,
        "target_language": job.target_language,
        "mode": job.mode,
        "learning_level": job.learning_level,
        "learning_cefr": job.learning_cefr,
        # Серия R §12: чипы «параметры, отличающиеся от умолчания»
        # (считаны в create_job против базовой линии; '' — пусто).
        "param_chips": _param_chips(job.param_chips),
        "priority": job.priority,
        "progress": job.progress,
        "progress_display": progress["display"],
        "phase": progress["phase"],
        "phases": {
            "completed": progress["completed"],
            "total": progress["total_phases"],
        },
        "segments": progress["segments"],
        "alignment": {
            "coverage": job.alignment_coverage,
            "aligned": aligned,
            "total": total_segments,
        },
        "tokens": tokens,
        "elapsed_seconds": round(elapsed, 1),
        "eta_seconds": round(eta, 1) if eta is not None else None,
        "pause_requested": bool(job.pause_requested),
        "cancel_requested": bool(job.cancel_requested),
        "paused_at_human": human_ts(paused_at) if paused_at is not None else None,
        "failed_segments": failed,
        "retries": retries,
        "warnings": warnings,
        "error": job.error_message,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "created_at_human": human_ts(job.created_at),
        "updated_at_human": human_ts(job.updated_at),
        # Таймлайн стадий — из БД (transitions), тот же источник, что и CLI
        # (E3.3): пользователь видит, какие этапы пройдены и когда.
        "stages": [
            {
                "from": record.from_status.value,
                "to": record.to_status.value,
                "at_human": human_ts(record.at),
                "reason": record.reason,
            }
            for record in transitions
        ],
        "language_detection": validation.get("language_detection") if validation else None,
        "files": files,
        "result_ready": result_ready,
        "export": export_facts,
        "logs": {
            "job": _tail(jdir / "job.log", log_lines),
            "errors": _tail(jdir / "errors.log", log_lines),
            "quality": _tail(jdir / "quality.log", log_lines),
        },
    }


def _param_chips(raw: str) -> list[dict[str, str | None]]:
    """Серия R §12: чипы задания из JSON-колонки ('' — пустой список).

    Формат фиксирует create_job: ``[{"f": "mode", "v": "translation"}, …]``;
    испорченное значение не роняет экран задания (пусто, как '' — данные
    чипов производные, не инвариант)."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def create_defaults(
    repo: JobRepository, settings: Settings, book: str | None = None
) -> dict[str, Any]:
    """Серия R §12 (R4): умолчания формы создания — сервером, в разметку.

    Базовая линия — единая точка ``services.jobs.create_baseline`` (последнее
    задание; при отсутствии — системные умолчания): та же, что и чипы
    (create_job). CEFR-подпись по языкам — единая точка
    ``languages.cefr.language_cefr_label`` (ТЗ: не сокращается, не в тултип).
    Подсказки объёма материалов — по уровням из ``learning.SUBSTAGE_LEVELS``
    (единственный источник «уровень → подстадии»; строки — strings.py).

    Серия R §7 (R5): ``book`` — книга, с которой создают (карточка,
    «Перевести»): её ``target_default``/``cefr_default`` (если заданы и
    валидны) перекрывают базовую линию; «язык оригинала» книги в UI —
    метаданные (решение L0.5 по заданию не меняется, TZ §1)."""
    baseline, from_last = create_baseline(repo, settings)
    registry = load_registry(settings.languages_root)
    cefr_labels = {
        code: language_cefr_label(pack) for code, pack in sorted(registry.items())
    }
    level_hints = {
        level: ", ".join(
            _S[f"substage_{stage}"]
            for stage, levels in SUBSTAGE_LEVELS.items()
            if level in levels
        )
        for level in LEARNING_LEVELS
    }
    level_hints[""] = _S["create_level_none_hint"]
    target = baseline["target_language"]
    learning_cefr = baseline["learning_cefr"]
    if book is not None:
        dest = book_epub_path(settings, book)
        if dest is not None:
            meta = read_book_meta(dest)
            book_target = meta.get("target_default")
            book_cefr = meta.get("cefr_default")
            if book_target in registry:
                target = book_target
            if book_cefr in CEFR_ORDER:
                learning_cefr = book_cefr
    return {
        "target": target,
        "mode": baseline["mode"],
        "learning_level": baseline["learning_level"],
        "learning_cefr": learning_cefr,
        "from_last_job": from_last,
        "cefr_labels": cefr_labels,
        "level_hints": level_hints,
        "cefr_levels": list(CEFR_ORDER),
        "learning_levels": list(LEARNING_LEVELS),
    }


# ---------------------------------------------------------------- книги (Books)

# Серия R §15: редактируемые поля sidecar-метаданных (.meta.json) —
# ровно шесть полей ТЗ §7 (один механизм правки: тап по значению).
BOOK_META_FIELDS: dict[str, tuple[str, ...]] = {
    "title": ("str",),
    "author": ("str",),
    "source_language": ("lang",),
    "target_default": ("lang",),
    "cefr_default": ("cefr",),
    "note": ("str",),
}
# Серия R §15: зарезервированные поля (заполняет R6-импорт; в R5 не
# редактируются, но читаются и переносятся миграцией).
BOOK_META_RESERVED: tuple[str, ...] = ("source_format", "cover_path", "imported_at")
# Серия R §7: ключи строк UI по полям (strings.py — единый источник).
BOOK_META_FIELD_KEYS = {
    "title": "book_field_title",
    "author": "book_field_author",
    "source_language": "book_field_source",
    "target_default": "book_field_target",
    "cefr_default": "book_field_cefr",
    "note": "book_field_note",
}
# Серия R §7: лимит длины текстовых полей (fail closed на API-границе).
BOOK_META_MAX_LEN = {"title": 200, "author": 200, "note": 2000}


def book_epub_path(settings: Settings, filename: str) -> Path | None:
    """Файл книги из имени (валидация имени — здесь, единая точка):
    без traversal, только ``*.epub`` внутри ``input/``."""
    if not filename or Path(filename).name != filename or not filename.endswith(".epub"):
        return None
    dest = settings.input_dir / filename
    return dest if dest.is_file() else None


def book_cover_path(settings: Settings, filename: str) -> Path | None:
    """R6: файл обложки по sidecar-метаданным. Guard имени (паттерн
    ``book_epub_path``): только ``<stem>.cover.<ext>`` внутри ``input/``;
    обложки нет или файла нет — ``None``."""
    dest = book_epub_path(settings, filename)
    if dest is None:
        return None
    cover_name = read_book_meta(dest).get("cover_path")
    if not cover_name or Path(cover_name).name != cover_name:
        return None
    stem = filename[: -len(".epub")]
    if not cover_name.startswith(stem + ".cover."):
        return None
    cover = settings.input_dir / cover_name
    return cover if cover.is_file() else None


def book_meta_path(path: Path) -> Path:
    """Sidecar-метаданные книги: ``<имя>.epub`` → ``<имя>.meta.json``."""
    return path.with_suffix(path.suffix + ".meta.json")


def read_book_meta(path: Path) -> dict[str, Any]:
    """Серия R §15: sidecar ``.meta.json`` с ленивой миграцией.

    При первом обращении читает старый ``.detect.json`` и пишет новый
    файл (старый НЕ удаляется — ТЗ §15). Нет ни того, ни другого —
    пустой dict (данные файла книги — единственный источник)."""
    meta = read_json(book_meta_path(path))
    if meta is None:
        legacy = read_json(path.with_suffix(path.suffix + ".detect.json"))
        if legacy is not None:
            meta = {**book_meta_defaults(), **legacy}
            book_meta_path(path).write_text(
                json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        else:
            meta = book_meta_defaults()
    return meta


def book_meta_defaults() -> dict[str, Any]:
    """Новые поля sidecar с дефолтами (ТЗ §15: title, author,
    target_default, cefr_default, note; R5 — source_language;
    R6 — source_format, cover_path, epubcheck)."""
    return {
        "title": None,
        "author": None,
        "source_language": None,
        "target_default": None,
        "cefr_default": None,
        "note": None,
        "source_format": None,
        "cover_path": None,
        "epubcheck": None,
    }


def write_book_meta(path: Path, meta: dict[str, Any]) -> None:
    """Запись sidecar-метаданных (атомарно: tmp + rename)."""
    dest = book_meta_path(path)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(dest)


def delete_book_files(settings: Settings, dest: Path) -> int:
    """Удаление файла книги + sidecar (единая точка: API, страницы,
    каскад). R6: вместе с книгой — оригинал импорта ``<name>.source.*``
    и обложка ``<name>.cover.*``. Возврат — байты файла (аудит)."""
    size = dest.stat().st_size if dest.is_file() else 0
    dest.unlink(missing_ok=True)
    book_meta_path(dest).unlink(missing_ok=True)
    dest.with_suffix(dest.suffix + ".detect.json").unlink(missing_ok=True)
    stem = dest.name[: -len(dest.suffix)] if dest.suffix else dest.name
    if dest.parent.is_dir():
        for sidecar in dest.parent.iterdir():
            if not sidecar.is_file():
                continue
            if sidecar.name.startswith(
                (f"{stem}.source.", f"{stem}.cover.")
            ):
                sidecar.unlink(missing_ok=True)
    return size


def book_purge_token(secret: str, filename: str, job_ids: list[str]) -> str:
    """Серия R §7: токен двухшагового каскада удаления книги — тот же
    механизм, что ``/jobs/purge`` (HMAC от секрета, книги и состава);
    расхождение состава между шагами — 409 (удаляется ровно перечисленное)."""
    payload = json.dumps(
        {"book": filename, "ids": sorted(job_ids)}, sort_keys=True, ensure_ascii=False
    )
    return hmac.new(
        secret.encode("utf-8"), f"u3-bookpurge|{payload}".encode(), hashlib.sha256
    ).hexdigest()[:32]


def list_books(
    settings: Settings,
    repo: JobRepository | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    """EPUB в ``input/`` + результат определения языка (sidecar-JSON).

    Определение — тот же механизм L0, что и стадия validating:
    ``detect_book`` (детектор + dc:language, L0.5) — не дублируется.
    Числа глав/сегментов — из ``services.analyze.scan_book`` (D70),
    повторной реализации сканирования нет (U3 3.6.29).

    Серия R §6/§17 R2: ``q`` — серверный подстроковый фильтр
    (имя файла + название, без учёта регистра); ``repo`` — при заданном
    у каждой книги ``has_active_job`` (есть ли задание в run-группе:
    точка на строке).

    R6 (ТЗ §8): порядок — сначала новые: импортированная книга
    появляется первой строкой списка (``imported_at``, запасной ключ —
    mtime; равенство — по имени).
    """
    input_dir = settings.input_dir
    if not input_dir.is_dir():
        return []
    rows: list[tuple[float, str, dict[str, Any]]] = []
    for path in input_dir.glob("*.epub"):
        meta = read_book_meta(path)
        stats = meta.get("stats") or {}
        st = path.stat()
        row: dict[str, Any] = {
            "name": path.name,
            "path": str(path),
            "size_bytes": st.st_size,
            "size_human": human_size(st.st_size),
            "modified_at": st.st_mtime,
            "uploaded_at_human": human_ts(st.st_mtime),
            "title": meta.get("title"),
            "author": meta.get("author"),
            "dc_language": meta.get("dc_language"),
            "detected": meta.get("detected"),
            "confidence": meta.get("confidence"),
            "probabilities": meta.get("probabilities") or {},
            "decision": meta.get("decision"),
            "accepted_language": meta.get("accepted_language"),
            "reasons": meta.get("reasons") or [],
            # Серия R §7: метаданные карточки (правка — PATCH §15)
            "source_language": meta.get("source_language"),
            "target_default": meta.get("target_default"),
            "cefr_default": meta.get("cefr_default"),
            "note": meta.get("note"),
            # R6: исходный формат импорта + обложка (решение R5 №8)
            "source_format": meta.get("source_format"),
            "cover_path": meta.get("cover_path"),
            "chapters": stats.get("chapter_count"),
            "segments": stats.get("segment_count"),
            "words": stats.get("word_count"),
        }
        # R6 (ТЗ §8): сначала новые — импортированная книга первой
        # строкой (imported_at, запасной ключ — mtime; равенство — имя).
        rows.append((float(meta.get("imported_at") or st.st_mtime), path.name, row))
    rows.sort(key=lambda item: (-item[0], item[1]))
    books = [row for _, _, row in rows]
    if q:
        needle = q.strip().lower()
        if needle:
            books = [
                b
                for b in books
                if needle in b["name"].lower()
                or needle in str(b.get("title") or "").lower()
            ]
    if repo is not None:
        run_statuses = tuple(st for st in JobStatus if status_group(st.value) == "run")
        for b in books:
            b["has_active_job"] = bool(
                repo.list_jobs(1, 0, statuses=run_statuses, book=b["name"])
            )
    return books


def book_detail(
    settings: Settings, repo: JobRepository | None, filename: str
) -> dict[str, Any] | None:
    """Серия R §7: данные карточки книги. None — книга не найдена
    (некорректное имя или файла нет — страница 404)."""
    dest = book_epub_path(settings, filename)
    if dest is None:
        return None
    meta = read_book_meta(dest)
    stats = meta.get("stats") or {}
    st = dest.stat()
    with dest.open("rb") as handle:
        sha256 = hashlib.sha256(handle.read()).hexdigest()
    jobs = repo.list_jobs(3, 0, book=filename, sort="updated_at", direction="desc") if repo else []
    total_jobs = repo.count_jobs(book=filename) if repo else 0
    return {
        "name": filename,
        "path": str(dest),
        "size_bytes": st.st_size,
        "size_human": human_size(st.st_size),
        "sha256": sha256,
        "uploaded_at_human": human_ts(st.st_mtime),
        "meta": meta,
        "chapters": stats.get("chapter_count"),
        "segments": stats.get("segment_count"),
        "words": stats.get("word_count"),
        "jobs": jobs,
        "total_jobs": total_jobs,
    }


def update_book_meta(
    settings: Settings, filename: str, patch: Mapping[str, Any]
) -> dict[str, Any]:
    """Серия R §15: правка метаданных (6 полей ТЗ §7). Fail closed:
    неизвестное поле/язык/CEFR/длина — ``AppError`` (400 на API).

    Пустое значение (None/пустая строка) — очистка поля. Возврат —
    полный обновлённый sidecar."""
    dest = book_epub_path(settings, filename)
    if dest is None:
        raise AppError(_S["err_book_not_found"].format(filename=filename))
    if not patch:
        raise AppError(_S["err_book_meta_empty"])
    registry = load_registry(settings.languages_root)
    for key, value in patch.items():
        if key not in BOOK_META_FIELDS:
            raise AppError(_S["err_book_bad_field"].format(field=key))
        kind = BOOK_META_FIELDS[key][0]
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        text = str(value).strip()
        limit = BOOK_META_MAX_LEN.get(key)
        if limit is not None and len(text) > limit:
            raise AppError(
                _S["err_book_meta_too_long"].format(field=key, limit=limit)
            )
        if kind == "lang" and text not in registry:
            raise AppError(_S["err_book_bad_language"].format(code=text))
        if kind == "cefr" and text not in CEFR_ORDER:
            raise AppError(_S["err_book_bad_cefr"].format(code=text))
    meta = read_book_meta(dest)
    for key, value in patch.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            meta[key] = None
        else:
            meta[key] = str(value).strip()
    write_book_meta(dest, meta)
    return meta


def book_usage(
    settings: Settings, repo: JobRepository | None, filename: str
) -> dict[str, Any] | None:
    """Серия R §7: задания, ссылающиеся на книгу — питает лист удаления.

    None — книга не найдена (404). Порядок — как в списке (updated_at
    desc); для каждого — статус и признак терминальности (каскад)."""
    dest = book_epub_path(settings, filename)
    if dest is None:
        return None
    jobs = (
        repo.list_jobs(None, 0, book=filename, sort="updated_at", direction="desc")
        if repo
        else []
    )
    return {
        "name": filename,
        "count": len(jobs),
        "jobs": [
            {
                "id": job.id,
                "status": job.status.value,
                "status_group": status_group(job.status.value),
                "terminal": job.status in TERMINAL_STATES,
                "created_at": job.created_at,
            }
            for job in jobs
        ],
    }


def book_purge_dry_run(
    settings: Settings, repo: JobRepository | None, filename: str
) -> dict[str, Any]:
    """Серия R §7: шаг 1 каскада — состав + ``confirm_token``.

    Каскад удаляет ТОЛЬКО терминальные задания (по образцу ``/jobs/purge``):
    есть не-терминальное — ``AppError`` (409, сперва отмена). Нет заданий —
    ``AppError`` (409)."""
    dest = book_epub_path(settings, filename)
    if dest is None:
        raise AppError(_S["err_book_not_found"].format(filename=filename))
    jobs = (
        repo.list_jobs(None, 0, book=filename, sort="updated_at", direction="desc")
        if repo
        else []
    )
    if not jobs:
        raise AppError(_S["err_book_purge_empty"])
    non_terminal = sorted(job.id for job in jobs if job.status not in TERMINAL_STATES)
    if non_terminal:
        raise AppError(
            _S["err_book_purge_nonterminal"].format(ids=", ".join(non_terminal))
        )
    ids = sorted(job.id for job in jobs)
    total_bytes = 0
    for job_id in ids:
        job_dir = settings.job_dir(job_id)
        if job_dir.is_dir():
            total_bytes += sum(p.stat().st_size for p in job_dir.rglob("*") if p.is_file())
    return {
        "book": filename,
        "job_ids": ids,
        "count": len(ids),
        "total_bytes": total_bytes,
        "confirm_token": book_purge_token(settings.server.auth_secret, filename, ids),
    }


def book_purge_execute(
    settings: Settings, repo: JobRepository, filename: str, job_ids: list[str]
) -> dict[str, Any]:
    """Серия R §7: шаг 2 каскада — удаляются ровно перечисленные задания
    (строки + каталоги артефактов, по образцу ``/jobs/purge``) и файл книги
    с sidecar'ами."""
    dest = book_epub_path(settings, filename)
    if dest is None:
        raise AppError(_S["err_book_not_found"].format(filename=filename))
    rows = 0
    for job_id in job_ids:
        rows += repo.delete_job(job_id)
        job_dir = settings.job_dir(job_id)
        if job_dir.exists() or job_dir.is_symlink():
            shutil.rmtree(job_dir, ignore_errors=True)
    size = delete_book_files(settings, dest)
    return {"rows": rows, "bytes": size, "purged": sorted(job_ids)}


# ---------------------------------------------------------------- языки (Languages)


def languages_view(settings: Settings) -> dict[str, Any]:
    """Матрица пар (статусы — ТОЛЬКО из резолвера, L0-5) + языки с CEFR-подписью.

    Подпись CEFR — единая точка ``cefr.label`` (L1-8): ``derived`` →
    ``Estimated CEFR level`` (критерий U1-5: 100% мест отображения).
    """
    registry = load_registry(settings.languages_root)
    languages = []
    for code, pack in sorted(registry.items()):
        languages.append(
            {
                "code": code,
                "name": pack.name,
                "spacy_model": pack.spacy_model,
                "cefr_source": pack.cefr_source,
                "cefr_label": language_cefr_label(pack),
            }
        )
    pairs = [
        {
            "source": report.source,
            "target": report.target,
            "status": report.status.value,
            "creatable": report.creatable,
            "missing": list(report.missing),
        }
        for report in matrix_report(settings)
    ]
    return {"languages": languages, "pairs": pairs}


# ---------------------------------------------------------------- модели (Models)


def models_view(settings: Settings) -> list[dict[str, Any]]:
    """LLM-профили (У1: раздел Models). Секреты никогда не показываются:
    API-ключ — только «задан/не задан» (U1-8)."""
    profiles: list[dict[str, Any]] = []
    for name in PROFILE_NAMES:
        profile = settings.llm.profile(name)
        has_key = bool(settings.llm.api_key_for(name))
        profiles.append(
            {
                "name": name,
                "base_url": profile.base_url,
                "model": profile.model,
                "api_key_set": has_key,
                "timeout_seconds": profile.timeout_seconds,
                "retry_times": profile.retry_times,
                "max_retries": profile.max_retries,
                "concurrency": profile.concurrency,
                "cost": _S["models_cost_set"] if profile.cost_defined else _S["models_cost_unset"],
            }
        )
    return profiles


# ---------------------------------------------------------------- промпты (Prompts)

_LEVEL_RANK = {LEVEL_PAIR: 0, LEVEL_SOURCE: 1, LEVEL_GENERIC: 2}


def _prompt_level_label(level: str) -> str:
    return {
        LEVEL_PAIR: _S["prompts_level_pair"],
        LEVEL_SOURCE: _S["prompts_level_source"],
        LEVEL_GENERIC: _S["prompts_level_generic"],
    }[level]


def prompts_view(settings: Settings) -> list[dict[str, Any]]:
    """ВСЕ версии всех промптов на диске (U3 3.7.30).

    Строка = файл ``vN.yaml``; уровень — пара / язык / generic (как у
    резолвера, E4.5). ``selected`` — помечен тот файл, который выберет
    ``PromptResolver`` для пары (max N на выигравшем уровне): перезапись
    версии запрещена, победитель — max N (D20). Резолвер — тот же
    ``app.llm.prompts``, что исполняет задание (Rule 8: нет второй
    реализации разрешения).
    """
    root = settings.prompts_dir
    if not root.is_dir():
        return []
    pairs = [(p.source, p.target) for p in matrix_report(settings)]
    winners = _resolver_winners(root, pairs)

    def _selected(
        task: str, level: str, source: str | None, target: str | None, file_rel: str
    ) -> bool:
        if level == LEVEL_PAIR:
            return (
                source is not None
                and target is not None
                and winners.get((task, source, target)) == file_rel
            )
        if level == LEVEL_SOURCE:
            if source is None:
                return False
            return any(
                winners.get((task, source, t)) == file_rel for _s, t in pairs if _s == source
            )
        return any(w == file_rel for (t, _s, _g), w in winners.items() if t == task)

    # Каталоги уровней: generic (task/), source (task/src/), pair (task/src/tgt/).
    candidates: list[tuple[str, str, str | None, str | None, Path]] = []
    for task_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        task = task_dir.name
        candidates.append((task, LEVEL_GENERIC, None, None, task_dir))
        for src_dir in sorted(p for p in task_dir.iterdir() if p.is_dir()):
            source = src_dir.name
            candidates.append((task, LEVEL_SOURCE, source, None, src_dir))
            for tgt_dir in sorted(p for p in src_dir.iterdir() if p.is_dir()):
                candidates.append((task, LEVEL_PAIR, source, tgt_dir.name, tgt_dir))

    rows: list[dict[str, Any]] = []
    for task_name, level, src, tgt, directory in candidates:
        level_label = _prompt_level_label(level)
        for path in sorted(directory.glob("v*.yaml")):
            version = _prompt_version(path)
            if version is None:
                continue
            file_rel = path.relative_to(root).as_posix()
            rows.append(
                {
                    "task": task_name,
                    "source": src,
                    "target": tgt,
                    "level": level,
                    "level_label": level_label,
                    "version": version,
                    "file_name": path.name,
                    "file_rel": file_rel,
                    "selected": _selected(task_name, level, src, tgt, file_rel),
                }
            )
    rows.sort(
        key=lambda r: (
            r["task"],
            _LEVEL_RANK[r["level"]],
            r["source"] or "",
            r["target"] or "",
            r["version"],
        )
    )
    return rows


def _resolver_winners(
    root: Path, pairs: list[tuple[str, str]]
) -> dict[tuple[str, str, str], str | None]:
    """Файл, который выберет резолвер для каждой пары (task, source, target) —
    единая точка разрешения (Rule 8: нет второй реализации). Не разрешается
    (нет уровня для pair_required / нет файлов) — None."""
    resolver = PromptResolver(root)
    winners: dict[tuple[str, str, str], str | None] = {}
    for spec in all_tasks():
        for source, target in pairs:
            try:
                resolved = resolver.resolve(
                    spec.name, source, target, pair_required=spec.pair_required
                )
            except Exception:
                winners[(spec.name, source, target)] = None
            else:
                winners[(spec.name, source, target)] = resolved.file
    return winners


def _prompt_version(path: Path) -> int | None:
    """Номер версии vN.yaml (None — файл не версии)."""
    match = VERSION_FILE_RE.match(path.name)
    return int(match.group(1)) if match else None


def prompts_tasks_view(settings: Settings) -> list[dict[str, Any]]:
    """Реестр задач анализа (``app/llm/tasks.py``) для формы создания промпта.

    Имя задачи проверяется по этому реестру (U3 3.7.33): неизвестная —
    400, файл не создаётся.
    """
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "pair_required": spec.pair_required,
        }
        for spec in sorted(all_tasks(), key=lambda s: s.name)
    ]


# ---------------------------------------------------------------- настройки (Settings)


def settings_view(settings: Settings) -> dict[str, Any]:
    """Конфигурация для раздела Settings. Секреты исключены (U1-8):
    ``llm.api_key``/``analysis_api_key``/``server.auth_secret`` не
    отображаются — только признак «задан»."""
    return {
        "server": {
            "bind_address": settings.server.bind_address,
            "port": settings.server.port,
            "auth": _S["models_key_set"] if settings.server.auth_secret else _S["models_key_unset"],
            "heartbeat_seconds": settings.server.heartbeat_seconds,
            "lease_seconds": settings.server.lease_seconds,
        },
        "storage": {
            "root": str(settings.storage.root),
            "input": str(settings.input_dir),
            "jobs": str(settings.jobs_dir),
            "output": str(settings.output_dir),
            "books": str(settings.books_dir),
        },
        "llm_keys": {
            name: _S["models_key_set"] if settings.llm.api_key_for(name) else _S["models_key_unset"]
            for name in PROFILE_NAMES
        },
        "limits": settings.limits.model_dump(),
        "glossary": settings.glossary.model_dump(),
        "alignment": settings.alignment.model_dump(),
        "segmentation": {
            "block_tags": list(settings.segmentation.block_tags),
        },
        "language_detection": settings.language_detection.model_dump(),
        "epubcheck": {"jar_path": str(settings.epubcheck_jar_path)},
        "translation": settings.translation.model_dump(),
        "languages": {
            "root": str(settings.languages_root),
            "matrix": str(settings.language_matrix_path),
        },
        "ui": settings.ui.model_dump(),
    }


# ---------------------------------------------------------------- качество (quality)


def quality_view(settings: Settings, job_id: str) -> dict[str, Any]:
    """Отчёт QC + выравнивание + правки L3 (У1.5/У1-9).

    Список правок L3 — из ``l3_fix_report.json`` (структурный источник);
    сверка с ``quality.log`` — критерий U1-9 (расхождение 0 проверяется
    тестом, а не «на глаз» в UI).
    """
    jdir = settings.job_dir(job_id)
    quality = read_json(jdir / "quality.json") or {}
    alignment = read_json(jdir / "alignment_report.json") or {}
    l3_report = read_json(jdir / "l3_fix_report.json")
    fixes = [dict(f) for f in (l3_report or {}).get("fixes", [])]
    return {
        "qc": quality.get("qc", {}),
        "warnings": quality.get("warnings", []),
        "epubcheck": quality.get("epubcheck", {}),
        "alignment": {
            "coverage": alignment.get("coverage"),
            "aligned": alignment.get("aligned"),
            "total": alignment.get("total"),
            "unaligned": alignment.get("unaligned"),
        },
        "glossary_consistency": quality.get("glossary_consistency"),
        "l3": (
            {
                "attempted": l3_report.get("attempted"),
                "applied": l3_report.get("applied"),
                "rejected": l3_report.get("rejected"),
                "halted": l3_report.get("halted"),
                "fixes": fixes,
            }
            if l3_report is not None
            else None
        ),
    }
