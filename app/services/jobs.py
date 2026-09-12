"""Создание задания и управляющие команды (E3.1).

API-сторона пишет только создание задания и управляющие команды — исполнением
занято занимается воркер (единственный писатель рабочих состояний, E3.1).
Управляющие команды (cancel/pause/resume) исполняет JobRepository напрямую —
он и есть авторизованный писатель; дублирующих обёрток нет (Rule 8).
"""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from pathlib import Path

from app.core.config import Settings
from app.core.errors import InputValidationError
from app.core.ids import new_job_id
from app.domain.job import Job, JobStatus
from app.storage.jobs_repo import JobRepository

#: Версия промптов (E4: prompts/<task>/<src>/<tgt>/vN.yaml). В E3 единая версия.
PROMPT_VERSION = "v0"


def config_hash(settings: Settings, target_language: str) -> str:
    """Хэш конфигурации, влияющей на результат (часть ключа идемпотентности, E3.6).

    Другая конфигурация LLM или другой целевой язык — другой ключ: кэш и результаты
    задания не переиспользуются (E3.7).
    """
    payload = {
        "base_url": settings.llm.base_url,
        "model": settings.llm.model,
        "profiles": {
            name: settings.llm.profile(name).model_dump()
            for name in ("translation", "fill")
        },
        "segmentation": settings.segmentation.block_tags,
        "target_language": target_language,
        "prompt_version": PROMPT_VERSION,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def create_job(
    repo: JobRepository,
    settings: Settings,
    *,
    source_path: Path,
    target_language: str,
    priority: int = 0,
) -> Job:
    """Создание задания (CREATED). Лёгкая предпроверка: файл существует и это ZIP."""
    if not source_path.is_file():
        raise InputValidationError(f"файл-источник не найден: {source_path}")
    if not zipfile.is_zipfile(source_path):
        raise InputValidationError(f"не является ZIP-архивом: {source_path.name}")
    now = time.time()
    job = Job(
        id=new_job_id(),
        status=JobStatus.CREATED,
        source_path=str(source_path),
        target_language=target_language,
        priority=priority,
        created_at=now,
        updated_at=now,
        input_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        config_sha256=config_hash(settings, target_language),
        prompt_version=PROMPT_VERSION,
    )
    repo.create(job)
    return job


def confirm_job(repo: JobRepository, job_id: str) -> Job:
    """Подтверждение подозрительной книги: NEEDS_CONFIRMATION → PLANNING."""
    return repo.transition(
        job_id, JobStatus.PLANNING, reason="подтверждено пользователем", actor="api"
    )
