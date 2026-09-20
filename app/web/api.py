"""API U1/U3 (JSON). Каждый endpoint имеет потребителя в UI (критерий U1-6);
бизнес-логики здесь нет — только сервисы ``app.services``, ``JobRepository``
и чтение артефактов через ``app.web.views`` (Rule 8: дублей механизмов нет).

Секреты в ответах отсутствуют (U1-8): настройки — через ``views.settings_view``,
профили LLM — «ключ задан/не задан».

U3:
- строки интерфейса — только ``app.web.strings`` (критерий U3-1);
- список заданий: фильтры/сортировка/пагинация — SQL через
  ``JobRepository`` (3.4.16-18), без параметров — полное поведение U1;
- удаление терминальных заданий + массовый purge (3.5) — fail closed
  (Rule 6): проверка пути ``is_relative_to(storage.root)`` перед удалением,
  строки — одной транзакцией, каталог — после commit;
- очистка логов (3.5.25-26): усекаются ``job.log``/``errors.log`` + строка
  факта в ``job.log``; ``quality.log`` не затрагивается НИКАКИМ маршрутом;
- удаление книг (3.6.28) и версии промптов (3.7) — перезапись версии
  запрещена, запись атомарна (temp + ``Path.replace``), валидация ДО записи;
- каждое удаление/усечение — строка аудита: операция, id, пользователь
  Basic-auth, число строк и байт (3.5.27).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.job import TERMINAL_STATES, Job, JobStatus
from app.learning import card_export
from app.llm.prompts import (
    LEVEL_GENERIC,
    LEVEL_PAIR,
    LEVEL_SOURCE,
    VERSION_FILE_RE,
)
from app.llm.tasks import task as task_lookup
from app.services.analyze import scan_book
from app.services.jobs import confirm_job, create_job
from app.storage.jobs_repo import JobRepository
from app.web import views
from app.web.health import health_report
from app.web.strings import UI_STRINGS

router = APIRouter(prefix="/api")

logger = logging.getLogger("app.web")

_S = UI_STRINGS

_COMPLETED_STATES = (JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_WARNINGS)
_TERMINAL_VALUES: frozenset[str] = frozenset(s.value for s in TERMINAL_STATES)
_ALL_STATUS_VALUES: frozenset[str] = frozenset(s.value for s in JobStatus)
_LEVELS: tuple[str, ...] = (LEVEL_PAIR, LEVEL_SOURCE, LEVEL_GENERIC)

#: Код языка как имя каталога промптов: без разделителей и спецсимволов.
_LANG_CODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Имена артефактов карточек — единый источник card_export.CARDS_FILES (Rule 8:
# писатель S5, этот endpoint и CLI `export cards` — одна таблица имён).
# V2: именованные артефакты задания (перевод/учебная книга/отчёт QC) —
# тот же endpoint, тот же контроль «только завершённые задания».
_NAMED_RESULT_ARTIFACTS = ("target.epub", "learning.epub", "quality.json")
_RESULT_PATTERN = "^(main|" + "|".join(
    re.escape(name)
    for name in (*_NAMED_RESULT_ARTIFACTS, *card_export.CARDS_FILES.values())
) + ")$"


def get_deps(request: Request) -> tuple[Settings, JobRepository]:
    """Настройки и репозиторий из ``app.state`` (задаёт ``create_app``)."""
    state = request.app.state
    return state.settings, state.repo


def _user(request: Request) -> str:
    """Пользователь Basic-auth (строка аудита 3.5.27)."""
    return str(getattr(request.state, "username", "") or "anonymous")


def _dir_size(path: Path) -> int:
    """Суммарный размер файлов каталога (0 — каталога нет)."""
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _now_human() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _job_or_404(repo: JobRepository, job_id: str) -> Job:
    job = repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=_S["err_job_not_found"].format(id=job_id))
    return job


def _job_row(settings: Settings, job: Job) -> dict[str, Any]:
    validation = views.read_json(settings.job_dir(job.id) / "validation.json")
    return {
        "id": job.id,
        "status": job.status.value,
        # Серия R §4: группа и глиф — из единой таблицы views.STATUS_GROUP;
        # клиент (app.js) рендерит статус по этим полям без собственного
        # соответствия статус→группа.
        "status_group": views.status_group(job.status.value),
        "status_glyph": views.status_glyph(job.status.value),
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
        "book_name": Path(job.source_path).name,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "created_at_human": views.human_ts(job.created_at),
        "updated_at_human": views.human_ts(job.updated_at),
    }


def _split_pair(pair: str | None) -> tuple[str | None, str | None]:
    if pair and "|" in pair:
        source, _sep, target = pair.partition("|")
        return source or None, target or None
    return None, None


def _safe_job_dir(settings: Settings, job_id: str) -> Path:
    """Каталог задания с проверкой Rule 6 (3.5.23): несовпадение — 500,
    НИЧЕГО не удаляется."""
    job_dir = settings.job_dir(job_id)
    if not job_dir.resolve().is_relative_to(settings.storage.root.resolve()):
        raise HTTPException(status_code=500, detail=_S["err_job_dir_unsafe"])
    return job_dir


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
        raise HTTPException(status_code=400, detail=_S["err_expected_epub"])
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


@router.delete("/books/{filename}")
def delete_book(
    filename: str,
    request: Request,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """U3 3.6.28: удаление книги из ``input/``. Fail closed: на книгу
    ссылаются задания — 409 + перечень job_id, книга остаётся."""
    settings, repo = deps
    if not filename or Path(filename).name != filename or not filename.endswith(".epub"):
        raise HTTPException(
            status_code=404, detail=_S["err_book_not_found"].format(filename=filename)
        )
    dest = settings.input_dir / filename
    if not dest.is_file():
        raise HTTPException(
            status_code=404, detail=_S["err_book_not_found"].format(filename=filename)
        )
    job_ids = repo.list_job_ids_by_book(filename)
    if job_ids:
        raise HTTPException(
            status_code=409,
            detail=_S["err_book_referenced"].format(ids=", ".join(job_ids)),
        )
    size = dest.stat().st_size
    dest.unlink()
    dest.with_suffix(dest.suffix + ".detect.json").unlink(missing_ok=True)
    logger.info("book_delete: user=%s name=%s bytes=%d", _user(request), filename, size)
    return {"deleted": filename}


# ------------------------------------------------------------------ jobs


@router.get("/jobs")
def list_jobs(  # noqa: PLR0917 — query-параметры FastAPI обязаны быть в сигнатуре
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
    limit: int | None = Query(default=None, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    status: Annotated[list[str] | None, Query()] = None,
    book: str | None = Query(default=None),
    pair: str | None = Query(default=None),
    created_from: str | None = Query(default=None),
    created_to: str | None = Query(default=None),
    sort: str = Query(default="created_at"),
    direction: str = Query(default="desc"),
) -> list[dict[str, Any]]:
    """Список заданий. Без параметров — все задания (поведение U1: поллинг).

    U3 3.4.16-18: пагинация ``limit``/``offset``, фильтры (статусы, книга,
    пара, диапазон created_at) и сортировка — в SQL через
    ``JobRepository`` (единственный источник, 3.4.16)."""
    settings, repo = deps
    statuses: tuple[str, ...] | None = None
    if status:
        for value in status:
            if value not in _ALL_STATUS_VALUES:
                raise HTTPException(
                    status_code=400, detail=_S["err_status_unknown"].format(status=value)
                )
        statuses = tuple(status)
    source_lang, target_lang = _split_pair(pair)
    try:
        jobs = repo.list_jobs(
            limit,
            offset,
            statuses=statuses,
            book=book,
            source_language=source_lang,
            target_language=target_lang,
            created_from=views.day_to_epoch(created_from),
            created_to=views.day_to_epoch(created_to, end_of_day=True),
            sort=sort,
            direction=direction,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail=_S["err_sort_field"]) from None
    return [_job_row(settings, job) for job in jobs]


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
        raise HTTPException(status_code=400, detail=_S["err_book_required"])
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


@router.delete("/jobs/{job_id}")
def job_delete(
    job_id: str,
    request: Request,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """U3 3.5.21-23: удаление ТОЛЬКО терминального задания. Строки — одной
    транзакцией; каталог — после commit, путь проверен ``is_relative_to``
    (Rule 6). Незавершённое — 409 verbatim, данные не изменяются."""
    settings, repo = deps
    job = _job_or_404(repo, job_id)
    if job.status not in TERMINAL_STATES:
        raise HTTPException(
            status_code=409,
            detail=_S["err_job_not_terminal"].format(status=job.status.value),
        )
    job_dir = _safe_job_dir(settings, job_id)
    dir_bytes = _dir_size(job_dir)
    rows = repo.delete_job(job_id)
    if job_dir.exists() or job_dir.is_symlink():
        shutil.rmtree(job_dir)
    logger.info(
        "job_delete: user=%s job=%s rows=%d bytes=%d", _user(request), job_id, rows, dir_bytes
    )
    return {"deleted": job_id, "rows": rows, "bytes": dir_bytes}


def _purge_token(secret: str, job_ids: list[str], filter_params: dict[str, Any]) -> str:
    """Токен двухшагового purge: HMAC от (секрет, состав задания, фильтр).

    Привязывает перечень шага 1 к подтверждению шага 2: расхождение состава
    между шагами — 409 (3.5.24: «удаляются ровно перечисленные job_id»)."""
    payload = json.dumps(
        {"ids": sorted(job_ids), "filter": filter_params}, sort_keys=True, ensure_ascii=False
    )
    return hmac.new(
        secret.encode("utf-8"), f"u3-purge|{payload}".encode(), hashlib.sha256
    ).hexdigest()[:32]


@router.post("/jobs/purge")
def purge_jobs(
    request: Request,
    body: dict[str, Any] | None = None,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """U3 3.5.24: массовая очистка — ДВА шага. Фильтр: только терминальные
    (подмножество статусов), старше ``ui.purge_min_age_days``, опционально
    книга/пара. Шаг 1 (dry-run) — перечень + confirm_token; шаг 2 — тот же
    фильтр + confirm_token, удаляются ровно перечисленные job_id."""
    settings, repo = deps
    body = body or {}
    raw_statuses = body.get("statuses")
    statuses: list[str]
    if raw_statuses is None:
        statuses = sorted(_TERMINAL_VALUES)
    else:
        statuses = []
        for raw_value in raw_statuses:
            value = str(raw_value)
            if value not in _TERMINAL_VALUES:
                raise HTTPException(
                    status_code=400, detail=_S["err_purge_bad_status"].format(status=value)
                )
            if value not in statuses:
                statuses.append(value)
        statuses.sort()
    book = str(body["book"]) if body.get("book") else None
    source_lang, target_lang = _split_pair(str(body["pair"]) if body.get("pair") else None)
    min_age_days = settings.ui.purge_min_age_days
    updated_before = time.time() - min_age_days * 86400
    jobs = repo.list_jobs(
        None,
        0,
        statuses=tuple(statuses),
        book=book,
        source_language=source_lang,
        target_language=target_lang,
        updated_before=updated_before,
    )
    ids = sorted(job.id for job in jobs)
    total_bytes = sum(_dir_size(settings.job_dir(job_id)) for job_id in ids)
    filter_params = {
        "statuses": statuses,
        "book": book,
        "source": source_lang,
        "target": target_lang,
    }
    token = _purge_token(settings.server.auth_secret, ids, filter_params)
    confirm_token = body.get("confirm_token")
    if confirm_token is None:
        if not ids:
            raise HTTPException(
                status_code=404, detail=_S["err_purge_no_match"].format(days=min_age_days)
            )
        return {
            "dry_run": True,
            "job_ids": ids,
            "count": len(ids),
            "total_bytes": total_bytes,
            "min_age_days": min_age_days,
            "confirm_token": token,
        }
    if not hmac.compare_digest(str(confirm_token), token):
        raise HTTPException(status_code=409, detail=_S["err_purge_token"])
    # Токен гарантирует: текущий состав == составу шага 1 (удаляется ровно он).
    for job_id in ids:
        _safe_job_dir(settings, job_id)
    rows = 0
    for job_id in ids:
        rows += repo.delete_job(job_id)
        job_dir = settings.job_dir(job_id)
        if job_dir.exists() or job_dir.is_symlink():
            shutil.rmtree(job_dir, ignore_errors=True)
    logger.info(
        "jobs_purge: user=%s ids=%s rows=%d bytes=%d",
        _user(request),
        ids,
        rows,
        total_bytes,
    )
    return {"dry_run": False, "purged": ids, "count": len(ids), "total_bytes": total_bytes}


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


@router.post("/jobs/{job_id}/logs/clear")
def job_logs_clear(
    job_id: str,
    request: Request,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """U3 3.5.25-26: ``job.log``/``errors.log`` — до нуля + строка факта
    усечения в ``job.log`` (время, кто, байты). ``quality.log`` —
    доказательная база L3/V1.8a — маршрутом НЕ затрагивается."""
    settings, repo = deps
    _job_or_404(repo, job_id)
    job_dir = settings.job_dir(job_id)
    job_log = job_dir / "job.log"
    errors_log = job_dir / "errors.log"
    job_bytes = job_log.stat().st_size if job_log.is_file() else 0
    errors_bytes = errors_log.stat().st_size if errors_log.is_file() else 0
    for path in (job_log, errors_log):
        if path.is_file():
            with path.open("r+b") as handle:
                handle.truncate(0)
    fact = _S["logs_cleared_fact"].format(
        time=_now_human(),
        user=_user(request),
        job_bytes=job_bytes,
        err_bytes=errors_bytes,
    )
    job_log.parent.mkdir(parents=True, exist_ok=True)
    with job_log.open("a", encoding="utf-8") as handle:
        handle.write(fact + "\n")
    logger.info(
        "logs_clear: user=%s job=%s job_bytes=%d errors_bytes=%d",
        _user(request),
        job_id,
        job_bytes,
        errors_bytes,
    )
    return {"cleared": True, "job_bytes": job_bytes, "errors_bytes": errors_bytes}


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
            status_code=409, detail=_S["err_result_pending"].format(status=job.status.value)
        )
    jdir = settings.job_dir(job.id)
    if artifact == "main":
        learning = jdir / "learning.epub"
        path = learning if learning.is_file() else jdir / "target.epub"
    else:
        path = jdir / artifact
    if not path.is_file():
        raise HTTPException(
            status_code=404, detail=_S["err_artifact_not_found"].format(name=path.name)
        )
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.get("/jobs/{job_id}/quality")
def job_quality(
    job_id: str,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    settings, repo = deps
    _job_or_404(repo, job_id)
    return views.quality_view(settings, job_id)


# ------------------------------------------------------------------ prompts


def _prompt_path(settings: Settings, path: str) -> Path:
    """Абсолютный путь версии промпта + проверка Rule 6 (3.7.35): запись и
    чтение — ровно из ``prompts_root`` резолвера, пути вне — 400."""
    root = settings.prompts_dir
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise HTTPException(status_code=400, detail=_S["err_prompt_unsafe_path"])
    return candidate


@router.get("/prompts/content")
def prompt_content(
    path: str = Query(...),
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """U3 3.7.31: содержимое версии — YAML как в файле, побайтово (pre)."""
    settings, _repo = deps
    candidate = _prompt_path(settings, path)
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=_S["err_prompt_not_found"].format(path=path))
    return {
        "path": candidate.relative_to(settings.prompts_dir).as_posix(),
        "content": candidate.read_text(encoding="utf-8"),
    }


def _prompt_langs(
    task_name: str,
    pair_required: bool,
    level: str,
    raw_source: Any,
    raw_target: Any,
) -> tuple[str, str]:
    """Языки для уровня (3.7.33-34): pair_required — только уровень пары;
    коды — без разделителей путей (имена каталогов prompts_root)."""
    source = str(raw_source or "").strip()
    target = str(raw_target or "").strip()
    if pair_required and level != LEVEL_PAIR:
        raise HTTPException(
            status_code=400,
            detail=_S["err_prompt_pair_required"].format(task=task_name, level=level),
        )
    if level == LEVEL_PAIR:
        missing = [
            name for name, code in (("source", source), ("target", target)) if not code
        ]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=_S["err_prompt_missing_lang"].format(
                    level=level, missing=", ".join(missing)
                ),
            )
    elif level == LEVEL_SOURCE:
        if not source:
            raise HTTPException(
                status_code=400,
                detail=_S["err_prompt_missing_lang"].format(level=level, missing="source"),
            )
        target = ""
    else:
        source = target = ""
    for code in (source, target):
        if code and not _LANG_CODE_RE.match(code):
            raise HTTPException(
                status_code=400, detail=_S["err_prompt_bad_lang"].format(code=code)
            )
    return source, target


def _validate_prompt_yaml(content: Any) -> str:
    """3.7.34: валидация ДО записи — YAML разбирается, обязательные поля
    (system, user_template) — непустые строки. Возврат — контент (str)."""
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(
            status_code=400,
            detail=_S["err_prompt_validation"].format(reason="content: YAML text is empty"),
        )
    try:
        data: Any = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=400, detail=_S["err_prompt_validation"].format(reason=str(exc))
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=400,
            detail=_S["err_prompt_validation"].format(reason="root: expected a mapping"),
        )
    for field in ("system", "user_template"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(
                status_code=400,
                detail=_S["err_prompt_validation"].format(
                    reason=f"{field}: expected a non-empty string"
                ),
            )
    return content


def _next_prompt_version(directory: Path) -> int:
    """Следующий номер версии уровня: max(vN) + 1 (перезапись запрещена)."""
    best = 0
    if directory.is_dir():
        for entry in directory.iterdir():
            match = VERSION_FILE_RE.match(entry.name)
            if match is not None:
                best = max(best, int(match.group(1)))
    return best + 1


@router.post("/prompts")
def prompt_create(
    body: dict[str, Any],
    request: Request,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """U3 3.7.32-35: новая версия ``v(N+1)`` — перезапись существующего
    файла запрещена. Валидация ДО записи (YAML + обязательные поля +
    pair_required): ошибка — 400, созданных файлов 0, LLM-запросов 0.
    Запись атомарна: temp в том же каталоге + ``Path.replace``."""
    settings, _repo = deps
    task_name = str(body.get("task", ""))
    try:
        spec = task_lookup(task_name)
    except KeyError:
        raise HTTPException(
            status_code=400, detail=_S["err_prompt_task_unknown"].format(task=task_name)
        ) from None
    level = str(body.get("level", ""))
    if level not in _LEVELS:
        raise HTTPException(
            status_code=400, detail=_S["err_prompt_bad_level"].format(level=level)
        )
    source, target = _prompt_langs(
        task_name, spec.pair_required, level, body.get("source"), body.get("target")
    )
    content = _validate_prompt_yaml(body.get("content"))
    if level == LEVEL_PAIR:
        directory = settings.prompts_dir / task_name / source / target
    elif level == LEVEL_SOURCE:
        directory = settings.prompts_dir / task_name / source
    else:
        directory = settings.prompts_dir / task_name
    version = _next_prompt_version(directory)
    final = directory / f"v{version}.yaml"
    if final.exists():
        raise HTTPException(
            status_code=409,
            detail=_S["err_prompt_exists"].format(
                path=final.relative_to(settings.prompts_dir).as_posix()
            ),
        )
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".v{version}.tmp{os.getpid()}.yaml"
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(final)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail=_S["err_prompt_validation"].format(reason=f"write failed: {exc}"),
        ) from exc
    rel = final.relative_to(settings.prompts_dir).as_posix()
    logger.info(
        "prompt_create: user=%s path=%s version=%d task=%s level=%s",
        _user(request),
        rel,
        version,
        task_name,
        level,
    )
    return {"path": rel, "version": version}


@router.delete("/prompts/version")
def prompt_version_delete(
    request: Request,
    path: str = Query(...),
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """U3 3.7.36: удаление версии — отдельная операция с подтверждением.
    Последняя версия своего уровня разрешения — 409: иначе задача
    (pair_required) перестанет разрешаться."""
    settings, _repo = deps
    candidate = _prompt_path(settings, path)
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=_S["err_prompt_not_found"].format(path=path))
    rel_parts = candidate.relative_to(settings.prompts_dir).parts
    level = {1: LEVEL_GENERIC, 2: LEVEL_SOURCE, 3: LEVEL_PAIR}.get(len(rel_parts) - 1)
    if level is None or not VERSION_FILE_RE.match(candidate.name):
        raise HTTPException(status_code=400, detail=_S["err_prompt_unsafe_path"])
    others = [
        entry
        for entry in candidate.parent.iterdir()
        if VERSION_FILE_RE.match(entry.name) and entry.name != candidate.name
    ]
    if not others:
        raise HTTPException(
            status_code=409,
            detail=_S["err_prompt_last_version"].format(level=level, task=rel_parts[0]),
        )
    candidate.unlink()
    logger.info("prompt_version_delete: user=%s path=%s", _user(request), path)
    return {"deleted": candidate.relative_to(settings.prompts_dir).as_posix()}


# ------------------------------------------------------------------ models/config


@router.get("/models")
def models(deps: tuple[Settings, JobRepository] = Depends(get_deps)) -> list[dict[str, Any]]:
    settings, _repo = deps
    return views.models_view(settings)


@router.get("/config")
def config(deps: tuple[Settings, JobRepository] = Depends(get_deps)) -> dict[str, Any]:
    settings, _repo = deps
    return views.settings_view(settings)
