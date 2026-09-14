"""API U1 (JSON). Каждый endpoint имеет потребителя в UI (критерий U1-6);
бизнес-логики здесь нет — только сервисы ``app.services`` и чтение
артефактов через ``app.web.views`` (Rule 8: дублей механизмов нет).

Секреты в ответах отсутствуют (U1-8): настройки — через ``views.settings_view``,
профили LLM — «ключ задан/не задан».
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.job import Job, JobStatus
from app.learning import card_export
from app.services.analyze import scan_book
from app.services.jobs import confirm_job, create_job
from app.storage.jobs_repo import JobRepository
from app.web import views
from app.web.health import health_report

router = APIRouter(prefix="/api")

_COMPLETED_STATES = (JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_WARNINGS)

# Имена артефактов карточек — единый источник card_export.CARDS_FILES (Rule 8:
# писатель S5, этот endpoint и CLI `export cards` — одна таблица имён).
_RESULT_PATTERN = "^(main|" + "|".join(
    re.escape(name) for name in card_export.CARDS_FILES.values()
) + ")$"


def get_deps(request: Request) -> tuple[Settings, JobRepository]:
    """Настройки и репозиторий из ``app.state`` (задаёт ``create_app``)."""
    state = request.app.state
    return state.settings, state.repo


# ------------------------------------------------------------------ health


@router.get("/health")
def health(deps: tuple[Settings, JobRepository] = Depends(get_deps)) -> dict[str, Any]:
    """Покомпонентное состояние (U1.8): никогда общий флаг (U1-3)."""
    settings, repo = deps
    return health_report(settings, repo)


# ------------------------------------------------------------------ languages


@router.get("/languages")
def languages(deps: tuple[Settings, JobRepository] = Depends(get_deps)) -> dict[str, Any]:
    """Матрица пар со статусами ТОЛЬКО из резолвера (U1-4) + CEFR-подписи."""
    settings, _repo = deps
    return views.languages_view(settings)


# ------------------------------------------------------------------ books


@router.get("/books")
def books(deps: tuple[Settings, JobRepository] = Depends(get_deps)) -> list[dict[str, Any]]:
    settings, _repo = deps
    return views.list_books(settings)


@router.post("/books")
async def upload_book(
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
    file: UploadFile = File(...),  # noqa: B008 — FastAPI file-параметр
) -> dict[str, Any]:
    """Загрузка EPUB в ``input/`` + определение языка (тот же механизм L0,
    что и стадия validating — ``detect_book``, не дублируется)."""
    settings, _repo = deps
    filename = file.filename
    if not filename or not filename.endswith(".epub"):
        raise HTTPException(status_code=400, detail="ожидается файл .epub")
    name = f"u1-{uuid.uuid4().hex[:8]}-{Path(filename).name}"
    dest = settings.input_dir / name
    with dest.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    try:
        meta = scan_book(settings, dest)
    except AppError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=exc.message) from exc
    sidecar = dest.with_suffix(dest.suffix + ".detect.json")
    sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"name": dest.name, **meta}


# ------------------------------------------------------------------ jobs


def _job_or_404(repo: JobRepository, job_id: str) -> Job:
    job = repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"задание не найдено: {job_id}")
    return job


@router.get("/jobs")
def list_jobs(
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> list[dict[str, Any]]:
    settings, repo = deps
    rows: list[dict[str, Any]] = []
    for job in repo.list_jobs():
        validation = views.read_json(
            views.job_dir(settings, job.id) / "validation.json"
        )
        rows.append(
            {
                "id": job.id,
                "status": job.status.value,
                "source_language": job.source_language,
                "target_language": job.target_language,
                "mode": job.mode,
                "learning_level": job.learning_level,
                "learning_cefr": job.learning_cefr,
                "progress": job.progress,
                "progress_display": views.progress_facts(job, validation)["display"],
                "alignment_coverage": job.alignment_coverage,
                "tokens_total": job.tokens_total,
                "error": job.error_message,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
            }
        )
    return rows


@router.post("/jobs")
def create(
    body: dict[str, Any],
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """Создание задания (У1.3): ``book`` — имя файла в ``input/`` (или
    абсолютный путь), остальные поля формы создания."""
    settings, repo = deps
    book_name = str(body.get("book", ""))
    if not book_name:
        raise HTTPException(status_code=400, detail="не выбран файл книги (book)")
    source = Path(book_name)
    if not source.is_absolute():
        source = settings.input_dir / book_name
    try:
        job = create_job(
            repo,
            settings,
            source_path=source,
            target_language=str(body.get("target_language", "")),
            mode=str(body.get("mode", "bilingual")),
            learning_level=body.get("learning_level"),
            learning_cefr=body.get("learning_cefr"),
        )
    except AppError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return {"id": job.id, "status": job.status.value}


@router.get("/jobs/{job_id}")
def job_detail(
    job_id: str,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    settings, repo = deps
    job = _job_or_404(repo, job_id)
    return views.job_facts(settings, repo, job)


@router.post("/jobs/{job_id}/confirm")
def job_confirm(
    job_id: str,
    body: dict[str, Any] | None = None,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    _settings, repo = deps
    _job_or_404(repo, job_id)
    try:
        job = confirm_job(repo, job_id, (body or {}).get("source_language"))
    except AppError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return {"id": job.id, "status": job.status.value}


@router.post("/jobs/{job_id}/pause")
def job_pause(
    job_id: str,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    _settings, repo = deps
    _job_or_404(repo, job_id)
    try:
        job = repo.request_pause(job_id)
    except AppError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return {"id": job.id, "status": job.status.value}


@router.post("/jobs/{job_id}/resume")
def job_resume(
    job_id: str,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    _settings, repo = deps
    _job_or_404(repo, job_id)
    try:
        job = repo.request_resume(job_id)
    except AppError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return {"id": job.id, "status": job.status.value}


@router.post("/jobs/{job_id}/cancel")
def job_cancel(
    job_id: str,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    _settings, repo = deps
    _job_or_404(repo, job_id)
    try:
        job = repo.request_cancel(job_id)
    except AppError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return {"id": job.id, "status": job.status.value}


@router.get("/jobs/{job_id}/logs")
def job_logs(
    job_id: str,
    lines: int = Query(default=200, ge=1, le=5000),
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, list[str]]:
    settings, repo = deps
    job = _job_or_404(repo, job_id)
    facts = views.job_facts(settings, repo, job, log_lines=lines)
    logs: dict[str, list[str]] = {
        str(name): [str(line) for line in lines]
        for name, lines in facts["logs"].items()
    }
    return logs


@router.get("/jobs/{job_id}/result")
def job_result(
    job_id: str,
    artifact: str = Query(default="main", pattern=_RESULT_PATTERN),
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> FileResponse:
    """Файл результата: main — learning.epub (Learning) либо target.epub;
    cards.* — экспорты S5. Только для завершённых заданий."""
    settings, repo = deps
    job = _job_or_404(repo, job_id)
    if job.status not in _COMPLETED_STATES:
        raise HTTPException(
            status_code=409, detail=f"результат не готов (статус: {job.status.value})"
        )
    jdir = settings.jobs_dir / job.id
    if artifact == "main":
        learning = jdir / "learning.epub"
        path = learning if learning.is_file() else jdir / "target.epub"
    else:
        path = jdir / artifact
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"файл не найден: {path.name}")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.get("/jobs/{job_id}/quality")
def job_quality(
    job_id: str,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    settings, repo = deps
    _job_or_404(repo, job_id)
    return views.quality_view(settings, job_id)


# ------------------------------------------------------------------ models/config


@router.get("/models")
def models(deps: tuple[Settings, JobRepository] = Depends(get_deps)) -> list[dict[str, Any]]:
    settings, _repo = deps
    return views.models_view(settings)


@router.get("/config")
def config(deps: tuple[Settings, JobRepository] = Depends(get_deps)) -> dict[str, Any]:
    settings, _repo = deps
    return views.settings_view(settings)
