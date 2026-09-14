"""Модели представлений U1: страницы и API читают ТОЛЬКО эти функции.

Честность прогресса (У1.1/У1.2): отображаемые величины — чистые функции
состояния в БД (``jobs.progress`` + артефакт ``validation.json``) и
измеренных отчётов задания. Процент показывается только для измеренных
величин (охват выравнивания E6, доли QC); остальное — реальные единицы
«глава N / M · сегментов X / Y». Фиктивного процента нет по построению.

Дублирования бизнес-логики нет: статусы пар — ``services.languages``
(L0-5), CEFR-подпись — ``languages.cefr.label`` (L1-8), фазы перевода —
``workers.pipeline.translation_phases`` (E5.3, единый источник).
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.core.config import PROFILE_NAMES, Settings
from app.domain.job import TERMINAL_STATES, Job, JobStatus
from app.languages.cefr import language_cefr_label
from app.languages.registry import load_registry
from app.services.languages import matrix_report
from app.storage.jobs_repo import JobRepository
from app.workers.pipeline import translation_phases


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


# ---------------------------------------------------------------- прогресс (U1-1/U1-2)


def _phase_weights(phases: list[tuple[str, str]]) -> list[float]:
    """Веса фаз движка (epub-translator): TOC 0.05, metadata 0.05,
    остальные 1.0 — поровну между главами. Та же формула, что в движке —
    она зафиксирована E5-3 (progress_mapping), здесь она нужна только для
    отображения «какая фаза активна по дробной БД»."""
    chapters = sum(1 for label, _doc in phases if label.startswith("глава "))
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
            "display": "ожидание валидации (validation.json ещё не создан)",
            "phase": None,
            "completed": 0,
            "total_phases": 0,
            "segments": None,
        }
    phases = translation_phases(validation)
    total = len(phases)
    if total == 0:
        return {
            "display": "фазы перевода не обнаружены (книга пуста?)",
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
        if label.startswith("глава "):
            index = int(label.split()[1]) - 1
            if 0 <= index < len(per_chapter):
                processed += per_chapter[index]
    if completed >= total:
        display = f"все фазы перевода завершены (сегментов {total_segments} / {total_segments})"
        phase: str | None = None
    else:
        label, _doc = phases[completed]
        phase = label
        if label.startswith("глава "):
            chapter_no = int(label.split()[1])
            chapter_count = int(validation["stats"]["chapter_count"])
            display = (
                f"глава {chapter_no} / {chapter_count}, "
                f"сегментов {processed} / {total_segments}"
            )
        else:
            display = f"фаза: {label} (сегментов {processed} / {total_segments})"
    return {
        "display": display,
        "phase": phase,
        "completed": completed,
        "total_phases": total,
        "segments": {"processed": processed, "total": total_segments},
    }


def elapsed_and_eta(job: Job, transitions: list[Any]) -> tuple[float, float | None]:
    """Прошедшее время (из БД) и оценка остатка (модельная, помечена как
    оценка в UI): ``elapsed * (1 - p) / p`` при 0 < p < 1, иначе None."""
    started = job.created_at
    if job.status in TERMINAL_STATES and transitions:
        finished = max(record.at for record in transitions)
    else:
        finished = time.time()
    elapsed = max(0.0, finished - started)
    eta: float | None = None
    if 0.0 < job.progress < 1.0 and elapsed > 0:
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
    return {
        "id": job.id,
        "status": job.status.value,
        "status_terminal": job.status in TERMINAL_STATES,
        "source_language": job.source_language,
        "target_language": job.target_language,
        "mode": job.mode,
        "learning_level": job.learning_level,
        "learning_cefr": job.learning_cefr,
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
        "failed_segments": failed,
        "retries": retries,
        "warnings": warnings,
        "error": job.error_message,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "language_detection": validation.get("language_detection") if validation else None,
        "files": files,
        "result_ready": result_ready,
        "logs": {
            "job": _tail(jdir / "job.log", log_lines),
            "errors": _tail(jdir / "errors.log", log_lines),
            "quality": _tail(jdir / "quality.log", log_lines),
        },
    }


# ---------------------------------------------------------------- книги (Books)


def list_books(settings: Settings) -> list[dict[str, Any]]:
    """EPUB в ``input/`` + результат определения языка (sidecar-JSON).

    Определение — тот же механизм L0, что и стадия validating:
    ``detect_book`` (детектор + dc:language, L0.5) — не дублируется.
    """
    books: list[dict[str, Any]] = []
    input_dir = settings.input_dir
    if not input_dir.is_dir():
        return books
    for path in sorted(input_dir.glob("*.epub")):
        meta = read_json(path.with_suffix(path.suffix + ".detect.json")) or {}
        stats = meta.get("stats") or {}
        books.append(
            {
                "name": path.name,
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "modified_at": path.stat().st_mtime,
                "title": meta.get("title"),
                "dc_language": meta.get("dc_language"),
                "detected": meta.get("detected"),
                "confidence": meta.get("confidence"),
                "probabilities": meta.get("probabilities") or {},
                "decision": meta.get("decision"),
                "accepted_language": meta.get("accepted_language"),
                "reasons": meta.get("reasons") or [],
                "chapters": stats.get("chapter_count"),
                "segments": stats.get("segment_count"),
                "words": stats.get("word_count"),
            }
        )
    return books


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
                "cost": "заданы" if profile.cost_defined else "не заданы (Cost: unknown)",
            }
        )
    return profiles


# ---------------------------------------------------------------- промпты (Prompts)


def prompts_view(settings: Settings) -> list[dict[str, Any]]:
    """Промпты на диске: ``prompts/<task>/<src>/<tgt>/vN.yaml`` (данные)."""
    rows: list[dict[str, Any]] = []
    root = settings.prompts_dir
    if not root.is_dir():
        return rows
    for task_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for src_dir in sorted(p for p in task_dir.iterdir() if p.is_dir()):
            for tgt_dir in sorted(p for p in src_dir.iterdir() if p.is_dir()):
                versions = sorted(tgt_dir.glob("v*.yaml"))
                if versions:
                    best = versions[-1]
                    rows.append(
                        {
                            "task": task_dir.name,
                            "source": src_dir.name,
                            "target": tgt_dir.name,
                            "versions": [p.name for p in versions],
                            "file": str(best),
                        }
                    )
    return rows


# ---------------------------------------------------------------- настройки (Settings)


def settings_view(settings: Settings) -> dict[str, Any]:
    """Конфигурация для раздела Settings. Секреты исключены (U1-8):
    ``llm.api_key``/``analysis_api_key``/``server.auth_secret`` не
    отображаются — только признак «задан»."""
    return {
        "server": {
            "bind_address": settings.server.bind_address,
            "port": settings.server.port,
            "auth": "on (общий секрет)" if settings.server.auth_secret else "off",
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
            name: "задан" if settings.llm.api_key_for(name) else "не задан"
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
