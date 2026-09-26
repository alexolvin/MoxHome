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
import mimetypes
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
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from app.adapters.convert import probe as converter_probe
from app.adapters.convert.checks import classify
from app.adapters.epub_engine.epubcheck import facts as epubcheck_facts
from app.adapters.epub_engine.epubcheck import run_epubcheck
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
from app.services.covers import extract_cover
from app.services.export_artifacts import (
    BookFileMissing,
    ExportInProgress,
    ExportPending,
    latest_export,
    start_book_export,
    start_job_export,
)
from app.services.import_book import get_import, start_import
from app.services.jobs import confirm_job, create_job
from app.services.llm_config import (
    clear_app_log,
    list_models,
    probe_profile,
    probe_request,
    update_llm_config,
    validate_base_url,
)
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


def _job_row(
    settings: Settings,
    job: Job,
    elapsed_seconds: float = 0.0,
    book_title: str | None = None,
    eta_seconds: float | None = None,
) -> dict[str, Any]:
    validation = views.read_json(settings.job_dir(job.id) / "validation.json")
    # Серия R §10: строка списка — группа (единый источник
    # views.job_list_group, клиент не пересчитывает), процент прогресса
    # (None — индетерминированные стадии без единиц: PLANNING/
    # VALIDATING_OUTPUT) и время работы (механизм E7/M7, без дубля).
    status = job.status.value
    indeterminate = job.status in (JobStatus.PLANNING, JobStatus.VALIDATING_OUTPUT)
    progress_pct = None if indeterminate else round((job.progress or 0.0) * 100)
    return {
        "id": job.id,
        "status": status,
        # Серия R §4: группа и глиф — из единой таблицы views.STATUS_GROUP;
        # клиент (app.js) рендерит статус по этим полям без собственного
        # соответствия статус→группа.
        "status_group": views.status_group(status),
        "status_glyph": views.status_glyph(status),
        "group": views.job_list_group(status, job.updated_at),
        "source_language": job.source_language,
        "target_language": job.target_language,
        "source_display": views.job_source_display(settings, job),
        "mode": job.mode,
        "learning_level": job.learning_level,
        "learning_cefr": job.learning_cefr,
        # Серия R §11/§15: место в очереди («Вперёд очереди» — PATCH,
        # клиент считает max по списку + 1).
        "queue_rank": job.queue_rank,
        "progress": job.progress,
        "progress_display": views.progress_facts(job, validation)["display"],
        "progress_pct": progress_pct,
        "elapsed_seconds": elapsed_seconds,
        "time_label": views.job_time_label(job, elapsed_seconds, eta_seconds),
        "alignment_coverage": job.alignment_coverage,
        "tokens_total": job.tokens_total,
        "error": job.error_message,
        "book_name": Path(job.source_path).name,
        "book_title": book_title or Path(job.source_path).name,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "created_at_human": views.human_ts(job.created_at),
        "updated_at_human": views.human_ts(job.updated_at),
    }


def _job_rows(settings: Settings, repo: JobRepository, jobs: list[Job]) -> list[dict[str, Any]]:
    """Строки списка заданий: переходы — одним запросом (без N+1),
    время работы — views.elapsed_and_eta (E7/M7, единый механизм)."""
    transitions = repo.list_transitions_bulk([job.id for job in jobs])
    title_map = views.book_title_map([Path(j.source_path) for j in jobs])
    rows: list[dict[str, Any]] = []
    for job in jobs:
        elapsed, _eta = views.elapsed_and_eta(job, transitions.get(job.id, []))
        rows.append(
            _job_row(
                settings,
                job,
                elapsed_seconds=elapsed,
                book_title=title_map.get(Path(job.source_path).name),
                eta_seconds=_eta,
            )
        )
    return rows


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
def books(
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
    q: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    """EPUB из ``input/``. Серия R §6: ``q`` — серверный подстроковый
    фильтр (имя файла + название)."""
    settings, repo = deps
    return views.list_books(settings, repo, q=q)


@router.post("/books", response_model=None)
async def upload_book(
    request: Request,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
    file: UploadFile = File(...),  # noqa: B008 — FastAPI file-параметр
) -> dict[str, Any] | Response:
    """R6 (ТЗ §8): загрузка книги — 16 форматов (``converter.accept``),
    calibre в контейнере без сети (D110).

    ``.epub`` — синхронный путь: пре-проверка (DRM) → EPUBCheck →
    определение языка (тот же механизм L0, что и стадия validating) →
    ``.meta.json`` (Серия R §15) → 200 ``{name, …meta}``. Всё остальное —
    сохранить оригинал ``<name>.source.<ext>`` (хранится и после успеха),
    202 ``{import_id}``, фоновый поток: конвертация → EPUBCheck → язык →
    ``done|error`` (прогресс — ``GET /api/imports/{id}``). no-JS (форма,
    ``Accept: text/html``) — 303: для ``.epub`` → ``/books``, иначе →
    ``/imports/{id}`` (страница с meta-refresh).

    Отказы fail closed (T8): 400 с точной причиной (``strings.py``),
    файл не остаётся в библиотеке.
    """
    settings, _repo = deps
    filename = file.filename or ""
    if not filename:
        raise HTTPException(status_code=400, detail=_S["err_book_required"])
    ext = Path(filename).suffix.lstrip(".").lower()
    if not ext:
        raise HTTPException(
            status_code=400,
            detail=_S["err_format_unsupported"].format(
                ext="",
                formats=", ".join("." + f for f in settings.converter.accept),
            ),
        )
    if (file.size or 0) > settings.limits.upload_max_mb * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail=_S["err_upload_too_big"].format(mb=settings.limits.upload_max_mb),
        )
    name = f"u1-{uuid.uuid4().hex[:8]}-{Path(filename).name}"
    wants_html = "text/html" in request.headers.get("accept", "")
    if ext == "epub":
        meta = await _import_epub_sync(settings, file, name)
        if wants_html:
            return RedirectResponse("/books", status_code=303)
        return {"name": name, **meta}
    # Не-EPUB требует calibre (D110) — без конвертера принимается только EPUB.
    status, _detail = converter_probe(settings)
    if status != "ok":
        raise HTTPException(status_code=400, detail=_S["err_converter_unavailable"])
    src = settings.input_dir / f"{name}.source.{ext}"
    with src.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    verdict = classify(settings, src, ext, file.size or src.stat().st_size)
    if not verdict["ok"]:
        src.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=verdict["detail"])
    import_id = start_import(settings, src, name, verdict["format"])
    if wants_html:
        return RedirectResponse(f"/imports/{import_id}", status_code=303)
    return JSONResponse(
        {"import_id": import_id, "name": f"{name}.epub"}, status_code=202
    )


async def _import_epub_sync(
    settings: Settings, file: UploadFile, name: str
) -> dict[str, Any]:
    """EPUB — первичный формат (ТЗ §8): конвертации нет — пре-проверка
    (DRM) → EPUBCheck → язык → ``.meta.json``. Сбой на любом шаге —
    400 и файл не остаётся в библиотеке (fail closed)."""
    dest = settings.input_dir / name
    with dest.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    try:
        verdict = classify(settings, dest, "epub", file.size or dest.stat().st_size)
        if not verdict["ok"]:
            raise HTTPException(status_code=400, detail=verdict["detail"])
        outcome = run_epubcheck(
            settings.epubcheck_jar_path, dest, settings.epubcheck.timeout_seconds
        )
        if outcome.available and (outcome.fatals or outcome.errors):
            first = " ".join((*outcome.fatals, *outcome.errors)[:3])
            raise HTTPException(
                status_code=400,
                detail=_S["err_import_epubcheck"].format(
                    n=len(outcome.fatals) + len(outcome.errors), first=first
                ),
            )
        meta = scan_book(settings, dest)
        cover_path = extract_cover(dest)
        # Серия R §15: новые загрузки пишут сразу .meta.json (старый формат
        # .detect.json — только миграция при первом обращении, R5).
        # R6: + source_format / epubcheck / cover_path (ТЗ §15).
        meta = {
            **views.book_meta_defaults(),
            **meta,
            "imported_at": time.time(),
            "source_format": "epub",
            "epubcheck": epubcheck_facts(outcome),
            "cover_path": cover_path,
        }
        views.write_book_meta(dest, meta)
    except HTTPException:
        views.delete_book_files(settings, dest)
        raise
    except AppError as exc:
        views.delete_book_files(settings, dest)
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return meta


@router.get("/imports/{import_id}")
def import_status(import_id: str) -> dict[str, Any]:
    """R6 (ТЗ §15): прогресс импорта —
    ``{id, status, step, format, name, error}``; status —
    ``checking|converting|validating|language|done|error``. Реестр —
    в памяти веб-процесса: после рестарта неизвестный id → 404."""
    rec = get_import(import_id)
    if rec is None:
        raise HTTPException(
            status_code=404, detail=_S["err_import_not_found"].format(id=import_id)
        )
    return rec


@router.get("/books/covers/{filename}")
def book_cover(
    filename: str,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> FileResponse:
    """R6 (ТЗ §8): обложка — файл ``<name>.cover.<ext>``; путь
    валидирует ``views.book_cover_path`` (только ``input/``, имя —
    по книге), traversal отклоняется."""
    settings, _repo = deps
    path = views.book_cover_path(settings, filename)
    if path is None:
        raise HTTPException(
            status_code=404, detail=_S["err_cover_not_found"].format(filename=filename)
        )
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@router.delete("/books/{filename}")
def delete_book(
    filename: str,
    request: Request,
    file_only: bool = Query(default=False),
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """U3 3.6.28: удаление книги из ``input/``. Fail closed: на книгу
    ссылаются задания — 409 + перечень job_id, книга остаётся.

    Серия R §7 (R5): ``file_only=1`` — путь «Удалить только файл» из
    листа удаления: книга удаляется, задания сохраняются со ссылкой на
    (теперь отсутствующий) файл; без параметра — поведение U3 не меняется."""
    settings, repo = deps
    dest = views.book_epub_path(settings, filename)
    if dest is None:
        raise HTTPException(
            status_code=404, detail=_S["err_book_not_found"].format(filename=filename)
        )
    job_ids = repo.list_job_ids_by_book(filename)
    if job_ids and not file_only:
        raise HTTPException(
            status_code=409,
            detail=_S["err_book_referenced"].format(ids=", ".join(job_ids)),
        )
    size = views.delete_book_files(settings, dest)
    logger.info(
        "book_delete: user=%s name=%s bytes=%d file_only=%s",
        _user(request),
        filename,
        size,
        file_only,
    )
    return {"deleted": filename, "file_only": file_only}


@router.get("/books/{filename}/usage")
def book_usage_endpoint(
    filename: str,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """Серия R §7 (R5): задания, ссылающиеся на книгу — интерфейс
    запрашивает ДО вызова удаления и открывает лист по сценарию;
    голый 409 в UI не попадает (T10)."""
    settings, repo = deps
    usage = views.book_usage(settings, repo, filename)
    if usage is None:
        raise HTTPException(
            status_code=404, detail=_S["err_book_not_found"].format(filename=filename)
        )
    return usage


@router.patch("/books/{filename}")
def book_meta_patch(
    filename: str,
    request: Request,
    body: dict[str, Any] | None = None,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """Серия R §15 (R5): правка метаданных книги (6 полей ТЗ §7).
    Fail closed: неизвестное поле/язык/CEFR/длина — 400, файл не тронут."""
    settings, _repo = deps
    try:
        meta = views.update_book_meta(settings, filename, body or {})
    except AppError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    logger.info("book_meta_patch: user=%s name=%s", _user(request), filename)
    return meta


@router.post("/books/{filename}/purge")
def book_purge(
    filename: str,
    request: Request,
    body: dict[str, Any] | None = None,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """Серия R §7 (R5): каскад «книга + N заданий» — ДВА шага, по образцу
    ``POST /jobs/purge``: шаг 1 (без confirm_token) — состав терминальных
    заданий + confirm_token; шаг 2 — тот же состав + confirm_token,
    удаляется ровно перечисленное. Не-терминальное задание — 409 (сначала
    отмена); расхождение состава — 409."""
    settings, repo = deps
    body = body or {}
    confirm_token = body.get("confirm_token")
    if confirm_token is None:
        try:
            return views.book_purge_dry_run(settings, repo, filename)
        except AppError as exc:
            raise HTTPException(status_code=409, detail=exc.message) from exc
    try:
        dry = views.book_purge_dry_run(settings, repo, filename)
    except AppError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    expected = views.book_purge_token(settings.server.auth_secret, filename, dry["job_ids"])
    if not hmac.compare_digest(str(confirm_token), expected):
        raise HTTPException(status_code=409, detail=_S["err_book_purge_token"])
    result = views.book_purge_execute(settings, repo, filename, dry["job_ids"])
    logger.info(
        "book_purge: user=%s name=%s jobs=%s rows=%d bytes=%d",
        _user(request),
        filename,
        dry["job_ids"],
        result["rows"],
        result["bytes"],
    )
    return {"dry_run": False, **dry, **{k: result[k] for k in ("rows", "bytes", "purged")}}


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
    since: float | None = Query(default=None, ge=0),
    sort: str = Query(default="created_at"),
    direction: str = Query(default="desc"),
) -> list[dict[str, Any]]:
    """Список заданий. Без параметров — все задания (поведение U1: поллинг).

    U3 3.4.16-18: пагинация ``limit``/``offset``, фильтры (статусы, книга,
    пара, диапазон created_at) и сортировка — в SQL через
    ``JobRepository`` (единственный источник, 3.4.16).

    Серия R §15: ``since=<rev>`` — дельта (``updated_at > rev``);
    ``limit``/``offset`` и фильтры-диапазоны при ней игнорируются."""
    settings, repo = deps
    statuses: tuple[str, ...] | None = None
    if status:
        for value in status:
            if value not in _ALL_STATUS_VALUES:
                raise HTTPException(
                    status_code=400, detail=_S["err_status_unknown"].format(status=value)
                )
        statuses = tuple(status)
    if since is not None:
        try:
            jobs = repo.list_jobs(
                None,
                0,
                statuses=statuses,
                updated_after=since,
                sort=sort,
                direction=direction,
            )
        except ValueError:
            raise HTTPException(status_code=400, detail=_S["err_sort_field"]) from None
        return _job_rows(settings, repo, jobs)
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
    return _job_rows(settings, repo, jobs)


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
            # "" из селекта «Не задан» = не задан (та же нормализация,
            # что no-JS-путь в pages._jobs_new_location)
            learning_level=body.get("learning_level") or None,
            learning_cefr=body.get("learning_cefr") or None,
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
    (Rule 6). Незавершённое — 409 verbatim, данные не изменяются.
    POST-алиас — для no-JS-формы (?sheet=delete): HTML-форма не может
    отправить DELETE (formmethod=DELETE невалиден, браузер шлёт GET)."""
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


def _bulk_token(secret: str, action: str, job_ids: list[str]) -> str:
    """Серия R §11: токен двухшагового массового удаления — тот же
    механизм, что purge (HMAC от секрета, действия и состава); расхождение
    состава между шагами — 409 (удаляются ровно перечисленные)."""
    payload = json.dumps(
        {"action": action, "ids": sorted(job_ids)}, sort_keys=True, ensure_ascii=False
    )
    return hmac.new(
        secret.encode("utf-8"), f"u3-bulk|{payload}".encode(), hashlib.sha256
    ).hexdigest()[:32]


@router.post("/jobs/purge")
def purge_jobs(
    request: Request,
    body: dict[str, Any] | None = None,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """U3 3.5.24: массовая очистка — ДВА шага. Фильтр: только терминальные
    (подмножество статусов), старше ``ui.purge_min_age_days``, опционально
    книга/пара. R8 (ТЗ §13 «Обслуживание»): опционально ``days`` (число ≥ 0)
    — своё N со страницы «Обслуживание». Шаг 1 (dry-run) — перечень +
    confirm_token; шаг 2 — тот же фильтр + confirm_token, удаляются ровно
    перечисленные job_id. Состав/токен/исполнение — views.jobs_purge_*
    (единая точка с no-JS-маршрутом, Rule 8)."""
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
    raw_days = body.get("days")
    if raw_days is not None:
        if isinstance(raw_days, bool) or not isinstance(raw_days, (int, float)) or raw_days < 0:
            raise HTTPException(status_code=400, detail=_S["err_purge_bad_days"])
        min_age_days = int(raw_days) if float(raw_days).is_integer() else float(raw_days)
    dry = views.jobs_purge_resolve(
        settings,
        repo,
        statuses=statuses,
        book=book,
        source=source_lang,
        target=target_lang,
        min_age_days=min_age_days,
    )
    ids = dry["job_ids"]
    total_bytes = dry["total_bytes"]
    confirm_token = body.get("confirm_token")
    if confirm_token is None:
        if not ids:
            raise HTTPException(
                status_code=404, detail=_S["err_purge_no_match"].format(days=min_age_days)
            )
        return {
            "dry_run": True,
            "job_ids": ids,
            "count": dry["count"],
            "total_bytes": total_bytes,
            "min_age_days": min_age_days,
            "confirm_token": dry["confirm_token"],
        }
    if not hmac.compare_digest(str(confirm_token), dry["confirm_token"]):
        raise HTTPException(status_code=409, detail=_S["err_purge_token"])
    # Токен гарантирует: текущий состав == составу шага 1 (удаляется ровно он).
    for job_id in ids:
        _safe_job_dir(settings, job_id)
    rows = views.jobs_purge_execute(settings, repo, ids)
    logger.info(
        "jobs_purge: user=%s ids=%s rows=%d bytes=%d",
        _user(request),
        ids,
        rows,
        total_bytes,
    )
    return {"dry_run": False, "purged": ids, "count": len(ids), "total_bytes": total_bytes}


def _bulk_jobs(repo: JobRepository, body: dict[str, Any]) -> dict[str, Job]:
    """R3 §11: валидация состава — дубликаты схлопываются; нет ids — 400;
    неизвестное задание — 404."""
    raw_ids = body.get("ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail=_S["err_bulk_no_ids"])
    jobs: dict[str, Job] = {}
    for value in raw_ids:
        job_id = str(value)
        if job_id in jobs:
            continue
        job = repo.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=404, detail=_S["err_job_not_found"].format(id=job_id)
            )
        jobs[job_id] = job
    return jobs


def _bulk_flag(
    repo: JobRepository, request: Request, action: str, jobs: dict[str, Job]
) -> dict[str, Any]:
    """R3 §11: bulk pause/cancel — флаги (переход делает воркер); отчёт
    applied/skipped: терминальные и отказы — пропущены с причиной."""
    applied: list[str] = []
    skipped: list[dict[str, str]] = []
    for job_id, job in jobs.items():
        if job.status in TERMINAL_STATES:
            skipped.append(
                {
                    "id": job_id,
                    "reason": _S["bulk_skip_terminal"].format(status=job.status.value),
                }
            )
            continue
        try:
            if action == "pause":
                repo.request_pause(job_id)
            else:
                repo.request_cancel(job_id)
            applied.append(job_id)
        except AppError as exc:
            skipped.append({"id": job_id, "reason": exc.message})
    logger.info(
        "jobs_bulk: user=%s action=%s ids=%s", _user(request), action, sorted(jobs)
    )
    return {"action": action, "applied": applied, "skipped": skipped}


def _bulk_delete(
    settings: Settings,
    repo: JobRepository,
    request: Request,
    body: dict[str, Any],
    jobs: dict[str, Job],
) -> dict[str, Any]:
    """R3 §11: массовое удаление — fail closed (хоть одно не-терминальное —
    409, ничего нетронуто) и два шага с confirm_token (механизм purge)."""
    non_terminal = sorted(
        (
            f"{job_id} ({job.status.value})"
            for job_id, job in jobs.items()
            if job.status not in TERMINAL_STATES
        )
    )
    if non_terminal:
        raise HTTPException(
            status_code=409,
            detail=_S["err_bulk_not_terminal"].format(ids=", ".join(non_terminal)),
        )
    ids = sorted(jobs)
    token = _bulk_token(settings.server.auth_secret, "delete", ids)
    confirm_token = body.get("confirm_token")
    if confirm_token is None:
        total_bytes = sum(_dir_size(settings.job_dir(job_id)) for job_id in ids)
        return {
            "dry_run": True,
            "job_ids": ids,
            "count": len(ids),
            "total_bytes": total_bytes,
            "confirm_token": token,
        }
    if not hmac.compare_digest(str(confirm_token), token):
        raise HTTPException(status_code=409, detail=_S["err_bulk_token"])
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
        "jobs_bulk: user=%s action=delete ids=%s rows=%d", _user(request), ids, rows
    )
    return {"dry_run": False, "purged": ids, "count": len(ids), "rows": rows}


@router.post("/jobs/bulk")
def jobs_bulk(
    request: Request,
    body: dict[str, Any] | None = None,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """Серия R §11: массовая операция — ОДИН запрос на операцию
    (``{action, ids[], confirm_token}``). pause/cancel — применяются к
    допустимым, недопустимые пропущены с причиной (отчёт). delete —
    только терминальные (fail closed: 409, ничего не удаляется) и два
    шага с confirm_token — тот же механизм, что purge."""
    settings, repo = deps
    body = body or {}
    action = str(body.get("action", ""))
    if action not in ("pause", "cancel", "delete"):
        raise HTTPException(
            status_code=400, detail=_S["err_bulk_bad_action"].format(action=action)
        )
    jobs = _bulk_jobs(repo, body)
    if action in ("pause", "cancel"):
        return _bulk_flag(repo, request, action, jobs)
    return _bulk_delete(settings, repo, request, body, jobs)


# no-JS-форма удаления (job_delete): HTML-форма не умеет DELETE.
# Регистрация ПОСЛЕ /jobs/bulk: иначе путь /jobs/{job_id} перехватил бы
# /jobs/bulk (job_id="bulk") — маршруты матчатся в порядке регистрации.
router.post("/jobs/{job_id}")(job_delete)


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


@router.patch("/jobs/{job_id}")
def job_patch(
    job_id: str,
    body: dict[str, Any],
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """Серия R §15: единственное изменяемое поле задания — ``queue_rank``
    («Вперёд очереди»). Прочие поля в теле — 400 (остальное задание не
    редактируется из UI); не-целое/отрицательное значение — 400."""
    _settings, repo = deps
    _job_or_404(repo, job_id)
    if set(body) - {"queue_rank"}:
        raise HTTPException(status_code=400, detail=_S["err_job_patch_fields"])
    raw = body.get("queue_rank")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise HTTPException(status_code=400, detail=_S["err_queue_rank_bad"])
    try:
        job = repo.set_queue_rank(job_id, raw)
    except AppError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return {
        "id": job.id,
        "status": job.status.value,
        "queue_rank": job.queue_rank,
    }


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


# ------------------------------------------------------------------ export (R7, TZ §9)


def _export_file(edir: Path, artifact: str) -> Path:
    """Файл из каталога экспорта: ровно один элемент пути, файл —
    иначе 404 (traversal невозможен, handoff R7 решение 7)."""
    if (
        not artifact
        or "/" in artifact
        or "\\" in artifact
        or artifact.startswith(".")
        or artifact in ("..",)
    ):
        raise HTTPException(
            status_code=404, detail=_S["err_artifact_not_found"].format(name=artifact)
        )
    path = (edir / artifact).resolve()
    if not path.is_relative_to(edir.resolve()) or not path.is_file():
        raise HTTPException(
            status_code=404, detail=_S["err_artifact_not_found"].format(name=artifact)
        )
    return path


@router.post("/jobs/{job_id}/export", status_code=202)
def job_export_create(
    job_id: str,
    body: dict[str, Any],
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """Экспорт артефактов задания (TZ §9): объекты × форматы + макет
    билингвы. Асинхронно (тот же пул, что импорт): 202 {export_id};
    статус — GET /api/jobs/{id}/export."""
    settings, repo = deps
    job = _job_or_404(repo, job_id)
    raw_objects = body.get("objects")
    raw_formats = body.get("formats")
    objects = [str(v) for v in raw_objects] if isinstance(raw_objects, list) else []
    formats = [str(v) for v in raw_formats] if isinstance(raw_formats, list) else []
    layout = body.get("layout") or "append_block"
    try:
        export_id = start_job_export(settings, job, objects, formats, str(layout))
    except (ExportPending, ExportInProgress) as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except AppError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return {"export_id": export_id}


@router.get("/jobs/{job_id}/export")
def job_export_status(
    job_id: str,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """Состояние последнего экспорта задания (поллинг листа и no-JS)."""
    _settings, repo = deps
    _job_or_404(repo, job_id)
    rec = latest_export(f"job:{job_id}")
    if rec is None:
        raise HTTPException(status_code=404, detail=_S["err_export_not_found"])
    return rec


@router.get("/jobs/{job_id}/export/{artifact}")
def job_export_file(
    job_id: str,
    artifact: str,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> FileResponse:
    """Готовый файл экспорта (TZ §9: download — системный Share)."""
    settings, repo = deps
    _job_or_404(repo, job_id)
    path = _export_file(settings.job_dir(job_id) / "export", artifact)
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.post("/books/{filename}/export", status_code=202)
def book_export_create(
    filename: str,
    body: dict[str, Any],
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """Экспорт исходника книги в другой формат (TZ §15). Асинхронно,
    как экспорт задания."""
    settings, _repo = deps
    fmt = str(body.get("format", ""))
    try:
        export_id = start_book_export(settings, filename, fmt)
    except BookFileMissing as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except ExportInProgress as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except AppError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return {"export_id": export_id}


@router.get("/books/{filename}/export")
def book_export_status(
    filename: str,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    settings, _repo = deps
    if not views.book_epub_path(settings, filename):
        raise HTTPException(
            status_code=404, detail=_S["err_book_not_found"].format(filename=filename)
        )
    rec = latest_export(f"book:{filename}")
    if rec is None:
        raise HTTPException(status_code=404, detail=_S["err_export_not_found"])
    return rec


@router.get("/books/{filename}/export/{artifact}")
def book_export_file(
    filename: str,
    artifact: str,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> FileResponse:
    settings, _repo = deps
    if not views.book_epub_path(settings, filename):
        raise HTTPException(
            status_code=404, detail=_S["err_book_not_found"].format(filename=filename)
        )
    path = _export_file(settings.input_dir / f"{filename}.export", artifact)
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


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


# ------------------------------------------------------------------ LLM (R8)


@router.post("/llm/probe")
def llm_probe(
    body: dict[str, Any],
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """ТЗ §13: проба профиля — GET {base_url}/models + минимальный
    chat-вызов на 8 токенов. Не пишет конфиг, не создаёт задание.

    200 всегда: ``ok``/``message`` — данные (одна строка результата);
    400 — некорректное тело. Пустые ключ/extra_body = сохранённые."""
    settings, _repo = deps
    try:
        base_url, model, api_key, temperature, extra_body = probe_request(body, settings)
    except AppError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return probe_profile(base_url, model, api_key, temperature, extra_body)


@router.put("/config/llm")
def config_llm_update(
    body: dict[str, Any],
    request: Request,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """ТЗ §13: запись ``llm.profiles.*`` (config.yaml) + ключи (.env).

    Fail-closed: валидация ДО записи — AppError → 400, файлы не
    тронуты. Успех — атомарная замена файлов, НОВЫЕ Settings в
    ``app.state`` (применяются к следующему заданию: идемпотентные
    ключи стадий включают конфиг)."""
    _settings, _repo = deps
    config_path = request.app.state.config_path
    env_path = request.app.state.env_path
    if config_path is None or env_path is None:
        raise HTTPException(
            status_code=500, detail=_S["err_llm_paths_missing"]
        )
    profiles_raw = body.get("profiles")
    if not isinstance(profiles_raw, dict):
        raise HTTPException(
            status_code=400,
            detail=_S["err_llm_unknown_profile"].format(profile="—"),
        )
    profile_fields = {
        str(name): dict(raw)
        for name, raw in profiles_raw.items()
        if isinstance(raw, dict)
    }
    shared_url = body.get("base_url")
    try:
        new_settings = update_llm_config(
            shared=bool(body.get("shared_endpoint")),
            shared_url=shared_url if isinstance(shared_url, str) else None,
            profile_fields=profile_fields,
            config_path=config_path,
            env_path=env_path,
        )
    except AppError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    request.app.state.settings = new_settings
    logger.info(
        "llm_config_update: user=%s shared=%s",
        _user(request),
        bool(body.get("shared_endpoint")),
    )
    return {"saved": True, "profiles": views.models_view(new_settings)}


@router.get("/llm/models")
def llm_models(
    base_url: str = Query(...),
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """ТЗ §13: модели эндпоинта для автодополнения (datalist).
    Сервер-прокси (CORS локального LLM не гарантирован). 400 —
    некорректный URL, 502 — эндпоинт недоступен/ответ не-JSON."""
    settings, _repo = deps
    try:
        validate_base_url(base_url)
    except AppError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    try:
        models = list_models(base_url, settings.llm.api_key_for("translation") or "")
    except AppError as exc:
        raise HTTPException(
            status_code=502,
            detail=_S["llm_models_unavailable"].format(reason=exc.message),
        ) from exc
    return {"models": models}


@router.post("/logs/clear")
def app_logs_clear(
    request: Request,
    deps: tuple[Settings, JobRepository] = Depends(get_deps),
) -> dict[str, Any]:
    """R8 «Обслуживание»: очистка ``logs/app.log`` — тот же механизм,
    что per-job (U3 3.5.25-26): truncate(0) + строка факта."""
    settings, _repo = deps
    cleared = clear_app_log(settings, _user(request))
    return {"cleared": True, "bytes": cleared}
