# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Создание задания и управляющие команды (E3.1).

API-сторона пишет только создание задания и управляющие команды — исполнением
занято занимается воркер (единственный писатель рабочих состояний, E3.1).
Управляющие команды (cancel/pause/resume) исполняет JobRepository напрямую —
он и есть авторизованный писатель; дублирующих обёрток нет (Rule 8).

L0.7: целевой язык задаёт пользователь; пара (dc:language → целевой) проверяется
резолвером ДО создания задания — unsupported отклоняется с точным перечнем
недостающих (L0.2/L0.3). dc:language читается лёгким ридером (без распаковки);
его отсутствие — не ошибка: язык определит детектор на validating (L0.5), пара
перепроверится там.
"""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from pathlib import Path

from app.core.config import Settings
from app.core.errors import InputValidationError, SettingsIncompleteError
from app.core.ids import new_job_id
from app.core.llm_settings import build_job_config_snapshot, validate_llm_settings
from app.domain.job import LEARNING_LEVELS, Job, JobStatus
from app.epub.reader import read_dc_language
from app.languages.cefr import CEFR_LEVELS
from app.languages.detect import normalize_code
from app.languages.registry import load_registry
from app.languages.resolver import PairResolver, PairStatus, mt_pe_supported
from app.storage.jobs_repo import JobRepository

#: Версия промптов (E4: prompts/<task>/<src>/<tgt>/vN.yaml). В E3 единая версия.
PROMPT_VERSION = "v0"

#: Режимы задания (E5.2). translation → REPLACE, bilingual → submit из YAML.
JOB_MODES: tuple[str, ...] = ("bilingual", "translation")


def config_hash(settings: Settings, target_language: str, mode: str) -> str:
    """Хэш конфигурации, влияющей на результат (часть ключа идемпотентности, E3.6).

    Другая конфигурация LLM, другой целевой язык или другой режим — другой ключ:
    кэш и результаты задания не переиспользуются (E3.7). E4.1: адрес и модель —
    только в YAML-профилях, поэтому в хэше участвуют сами профили (translation/fill);
    анализ в хэш не входит, пока стадия analyzing — заглушка (E4, CONTROL E4).
    E5.2: режим задания и движковый submit-режим bilingual влияют на результат.
    """
    payload = {
        "profiles": {
            name: settings.llm.profile(name).model_dump()
            for name in ("translation", "fill")
        },
        "segmentation": settings.segmentation.block_tags,
        "target_language": target_language,
        "mode": mode,
        "submit_mode_bilingual": settings.translation.submit_mode_bilingual,
        "prompt_version": PROMPT_VERSION,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


#: Серия R §12: чипы — ключи JSON-пары «f» → поля задания (базовая линия).
CHIP_FIELDS: dict[str, str] = {
    "target": "target_language",
    "mode": "mode",
    "level": "learning_level",
    "cefr": "learning_cefr",
}


def create_baseline(
    repo: JobRepository, settings: Settings
) -> tuple[dict[str, str | None], bool]:
    """Серия R §12: базовая линия создания (единая точка для формы и чипов).

    Есть задания — самое свежее (created_at DESC): умолчания «как в прошлый
    раз»; нет — системные (target «ru» или первый реестра, mode bilingual,
    уровень/CEFR не заданы). Возврат — ``(параметры, от_последнего_задания)``.
    Считается ДО вставки нового задания (иначе базой стало бы оно само)."""
    jobs = repo.list_jobs(1, 0, sort="created_at", direction="desc")
    if jobs:
        last = jobs[0]
        return (
            {
                "target_language": last.target_language,
                "mode": last.mode,
                "learning_level": last.learning_level,
                "learning_cefr": last.learning_cefr,
            },
            True,
        )
    codes = sorted(load_registry(settings.languages_root))
    default_target = "ru" if "ru" in codes else (codes[0] if codes else "ru")
    return (
        {
            "target_language": default_target,
            "mode": "bilingual",
            "learning_level": None,
            "learning_cefr": None,
        },
        False,
    )


def compute_param_chips(
    baseline: dict[str, str | None],
    target_language: str,
    mode: str,
    learning_level: str | None,
    learning_cefr: str | None,
) -> str:
    """Серия R §12: чипы «параметры, отличающиеся от умолчания» (ТЗ §12).

    Разница против базовой линии по четырём полям формы; '' — всё по
    умолчаниям. None значения (уровень/CEFR сняты) — тоже разница, если
    в базовой линии было значение (честность: видно и «убрали»)."""
    final = {
        "target": target_language,
        "mode": mode,
        "level": learning_level,
        "cefr": learning_cefr,
    }
    chips = [
        {"f": field, "v": value}
        for field, value in final.items()
        if value != baseline.get(CHIP_FIELDS[field])
    ]
    return json.dumps(chips, ensure_ascii=False) if chips else ""


def create_job(
    repo: JobRepository,
    settings: Settings,
    *,
    source_path: Path,
    target_language: str,
    priority: int = 0,
    mode: str = "bilingual",
    learning_level: str | None = None,
    learning_cefr: str | None = None,
) -> Job:
    """Создание задания (CREATED). Лёгкая предпроверка: файл существует и это ZIP.

    Learning (ТЗ_03): `learning_level` — уровень сложности материалов
    (LEARNING_LEVELS), `learning_cefr` — CEFR-уровень пользователя (A1..C2).
    Оба опциональны; None — learning не включён (стадия LEARNING существует,
    no-op). Параметры не входят в config_hash: перевод не зависит от них, а
    ключ подстадии S1 включает уровень/CEFR явно.
    """
    # MTPE TZ §5.3 (точка 4): создание задания требует валидных настроек LLM
    # для способа (single | mt_pe); неполные — отказ (API 422 / CLI отказ).
    issues = validate_llm_settings(settings)
    if issues:
        raise SettingsIncompleteError(
            "настройки LLM неполны для способа задания",
            details={"issues": [(issue.path, issue.code) for issue in issues]},
        )
    if mode not in JOB_MODES:
        raise InputValidationError(
            f"недопустимый режим задания: {mode!r} (допустимы: {', '.join(JOB_MODES)})"
        )
    if learning_level is not None and learning_level not in LEARNING_LEVELS:
        raise InputValidationError(
            f"недопустимый learning-уровень: {learning_level!r} "
            f"(допустимы: {', '.join(LEARNING_LEVELS)})"
        )
    if learning_cefr is not None and learning_cefr not in CEFR_LEVELS:
        raise InputValidationError(
            f"недопустимый CEFR-уровень пользователя: {learning_cefr!r} "
            f"(допустимы: {', '.join(sorted(CEFR_LEVELS))})"
        )
    if not source_path.is_file():
        raise InputValidationError(f"файл-источник не найден: {source_path}")
    if not zipfile.is_zipfile(source_path):
        raise InputValidationError(f"не является ZIP-архивом: {source_path.name}")
    # L0.7: проверка пары ДО создания задания (fail closed). dc:language может
    # отсутствовать ИЛИ быть "und" (неопределённый, ставит конвертер) — в обоих
    # случаях исходный язык определит детектор на validating (L0.5).
    dc_language = read_dc_language(source_path)
    if dc_language is not None and normalize_code(dc_language) != "und":
        resolver = PairResolver(
            registry_root=settings.languages_root, prompts_root=settings.prompts_dir
        )
        report = resolver.resolve_pair(dc_language, target_language)
        if report.status is PairStatus.UNSUPPORTED:
            raise InputValidationError(
                f"языковая пара {report.source} → {report.target} не поддерживается "
                f"(L0.2); не хватает: {'; '.join(report.missing)}"
            )
        # MTPE M6 (req 6): способ mt_pe — пара должна быть supported (mt_name
        # у обоих языков); иначе 422 pair_unsupported_mt_pe (не 400: это
        # ограничение способа, а не пары в целом).
        if settings.translation.pipeline == "mt_pe" and not mt_pe_supported(
            load_registry(settings.languages_root), dc_language, target_language
        ):
            raise SettingsIncompleteError(
                f"способ mt_pe недоступен для пары {dc_language} → "
                f"{target_language} (pair_unsupported_mt_pe): "
                "нет mt_name у обоих языков",
            )
    now = time.time()
    # Серия R §12: базовая линия ДО вставки (последнее существующее задание
    # или системные умолчания) — чипы фиксируются при создании и не
    # пересчитываются (единая точка, решение R4).
    baseline, _ = create_baseline(repo, settings)
    job = Job(
        id=new_job_id(),
        status=JobStatus.CREATED,
        source_path=str(source_path),
        target_language=target_language,
        mode=mode,
        pipeline=settings.translation.pipeline,
        config_snapshot=build_job_config_snapshot(settings),
        priority=priority,
        created_at=now,
        updated_at=now,
        input_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        config_sha256=config_hash(settings, target_language, mode),
        prompt_version=PROMPT_VERSION,
        learning_level=learning_level,
        learning_cefr=learning_cefr,
        param_chips=compute_param_chips(
            baseline, target_language, mode, learning_level, learning_cefr
        ),
    )
    repo.create(job)
    return job


def confirm_job(
    repo: JobRepository, job_id: str, source_language: str | None = None
) -> Job:
    """Подтверждение NEEDS_CONFIRMATION: подозрительная книга (E2.7) и/или язык (L0.5).

    `source_language` — выбор ПОЛЬЗОВАТЕЛЯ исходного языка (L0.5: авто-выбор
    при конфликте детектора и dc:language запрещён): записывается в задание;
    стадия planning требует подтверждённый язык (invariant, fail closed).
    """
    if source_language is not None:
        repo.set_source_language(job_id, source_language.strip().lower())
    return repo.transition(
        job_id, JobStatus.PLANNING, reason="подтверждено пользователем", actor="api"
    )
