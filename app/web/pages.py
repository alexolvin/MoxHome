"""HTML-страницы U1/U3: разделы Books/Jobs/Settings (+Languages/Models/Prompts).

Каждая страница — серверный shell + данные своего API-endpoint'а
(каждый endpoint из ТЗ имеет потребителя, U1-6). Страница задания
дополнительно рендерит фактические данные сервером (без JS тоже
читается) и обновляет блок прогресса поллингом ``GET /api/jobs/{id}``.

Серия R §5: shell — appbar (h1, назад, меню «Ещё») + таб-бар
(Библиотека/Задания/Настройки) + FAB; /settings — корень с
маршрутными строками; старые пути /languages, /models, /prompts —
301 на /settings/*.

U3:
- строки интерфейса — только из ``app.web.strings`` (критерий U3-1);
- все данные-таблицы рендерятся сервером ДВАЖДЫ: таблица
  (``.u3-table``) и карточный список (``.u3-cards``, тот же набор полей
  в том же порядке, 3.2.5) — переключение по ``ui.mobile_breakpoint_px``;
- CSRF-токен сессии Basic-auth — скрытое поле ``u3_csrf`` в каждой форме
  (3.9.41); параметры автообновления — ``ui.*`` (Rule 1).
"""

from __future__ import annotations

import hmac
import logging
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.core.config import Settings
from app.core.errors import AppError
from app.core.worker_signal import worker_alive
from app.domain.job import TERMINAL_STATES, Job, JobStatus
from app.languages.cefr import CEFR_ORDER
from app.languages.registry import load_registry
from app.services.jobs import JOB_MODES, confirm_job, create_job
from app.storage.jobs_repo import JobRepository, TransitionRecord
from app.web import views
from app.web.strings import UI_STRINGS

_S = UI_STRINGS
logger = logging.getLogger("app.web.pages")

_SORT_FIELDS = ("created_at", "updated_at")
_ALL_JOB_STATUS_VALUES = tuple(st.value for st in JobStatus)

# Серия R §5/§6: run-группа — единым источником (views.STATUS_GROUP):
# точка таб-бара («Задания») и точка строки книги.
_RUN_STATUS_VALUES = tuple(
    st.value for st in JobStatus if views.status_group(st.value) == "run"
)


def _page_title(key: str, **kwargs: object) -> str:
    return _S[key].format(**kwargs) if kwargs else _S[key]


# Серия R §10: сегментный фильтр — 5 пилюль на 5 статусных групп (§4).
# «Готовы» — ok+warn (окончательные успехи, включая предупреждения).
_GROUP_STATUS_GROUPS: dict[str, tuple[str, ...]] = {
    "all": ("wait", "run", "ok", "warn", "err"),
    "active": ("run",),
    "waiting": ("wait",),
    "done": ("ok", "warn"),
    "errors": ("err",),
}


def _group_statuses(group: str, exact: frozenset[str]) -> tuple[str, ...] | None:
    """Множество статусов для пилюли «группа» + точных статусов (лист).

    ``None`` — фильтр не нужен (всё); пустой кортеж — по построению
    заданий нет (пересечение пусто) — запрос не выполняется.
    """
    if group not in _GROUP_STATUS_GROUPS:
        group = "all"
    pool = {
        st.value for st in JobStatus if views.status_group(st.value) in _GROUP_STATUS_GROUPS[group]
    }
    if exact:
        pool &= exact
        return tuple(sorted(pool))
    return None if group == "all" else tuple(sorted(pool))


def _build_job_rows(
    jobs: list[Job],
    settings: Settings,
    transitions: dict[str, list[TransitionRecord]],
    today_start: float,
) -> dict[str, list[dict[str, object]]]:
    """Серия R §10: строки списка с группировкой (единый источник
    views.job_list_group); время работы — E7/M7 (bulk-переходы, без N+1)."""
    group_rows: dict[str, list[dict[str, object]]] = {
        "now": [],
        "today": [],
        "earlier": [],
    }
    for job in jobs:
        elapsed, _eta = views.elapsed_and_eta(job, transitions.get(job.id, []))
        indeterminate = job.status in (JobStatus.PLANNING, JobStatus.VALIDATING_OUTPUT)
        group_rows[
            views.job_list_group(job.status.value, job.updated_at, today_start)
        ].append(
            {
                "job": job,
                "book_name": Path(job.source_path).name,
                "progress_display": views.progress_facts(
                    job, views.read_json(settings.job_dir(job.id) / "validation.json")
                )["display"],
                "progress_pct": None
                if indeterminate
                else round((job.progress or 0.0) * 100),
                "elapsed_human": views.human_duration(elapsed),
                "status_group": views.status_group(job.status.value),
                "status_glyph": views.status_glyph(job.status.value),
                # Серия R §11: место в очереди (свайп «Вперёд очереди»)
                # и время создания (порядок wait-группы).
                "queue_rank": job.queue_rank,
                "created_at": job.created_at,
            }
        )
    return group_rows


def _sort_waiting_rows(rows: list[dict[str, object]]) -> None:
    """Серия R §11: группа «Ожидание» — queue_rank DESC, created_at ASC
    (значения int/float ставит ``_build_job_rows``)."""
    rows.sort(
        key=lambda row: (
            -int(cast("int", row["queue_rank"])),
            float(cast("float", row["created_at"])),
        )
    )


def _jobs_sheet_prep(
    repo: JobRepository,
    settings: Settings,
    sheet: str,
    job: str | None,
    retry_of: str | None,
) -> tuple[dict[str, object] | None, dict[str, object] | None, bool]:
    """Серия R §11/§12: данные листов /jobs (no-JS ``?sheet=``).

    Возврат — ``(export, create, export_missing)``: export — артефакты
    терминального задания; create — параметры листа создания
    (предзаполнение из ``retry_of``); export_missing — задание не
    найдено (страница 404)."""
    export_data: dict[str, object] | None = None
    export_missing = False
    if sheet == "export":
        target = repo.get(job) if job else None
        if target is None:
            export_missing = True
        else:
            facts = views.job_facts(settings, repo, target, log_lines=1)
            export_data = {
                "job_id": target.id,
                "book_name": Path(target.source_path).name,
                "files": facts["files"],
                "result_ready": facts["result_ready"],
            }
    create_data: dict[str, object] | None = None
    if sheet == "create":
        create_data = {}
        if retry_of:
            original = repo.get(retry_of)
            if original is not None:
                create_data = {
                    "retry_of": original.id,
                    "book": Path(original.source_path).name,
                    "target": original.target_language,
                    "mode": original.mode,
                    # R4: повтор — все параметры исходного задания
                    # (уровень/CEFR тоже, а не только target/mode).
                    "learning_level": original.learning_level,
                    "learning_cefr": original.learning_cefr,
                }
    return export_data, create_data, export_missing


def _jobs_new_location(  # noqa: PLR0917 — форма создания: 4 параметра ТЗ + пути
    settings: Settings,
    repo: JobRepository,
    book: str,
    target_language: str,
    mode: str,
    learning_level: str = "",
    learning_cefr: str = "",
) -> str:
    """Серия R T15: no-JS-создание — Location для 303: успех — список,
    отказ (AppError, verbatim) — обратно на лист с причиной.
    R4: опциональные уровень/CEFR (пустая строка — не задан; валидация —
    в create_job, единая точка с JSON-API)."""
    source = Path(book)
    if not source.is_absolute():
        source = settings.input_dir / book
    try:
        create_job(
            repo,
            settings,
            source_path=source,
            target_language=target_language,
            mode=mode,
            learning_level=learning_level or None,
            learning_cefr=learning_cefr or None,
        )
    except AppError as exc:
        return "/jobs?sheet=create&create_err=" + quote(exc.message)
    return "/jobs"


def _jobs_confirm_location(
    repo: JobRepository, job_id: str, source_language: str | None
) -> str:
    """Серия R T15: no-JS-подтверждение — Location для 303: успех — экран
    задания, отказ (AppError, verbatim) — с причиной."""
    try:
        confirm_job(repo, job_id, source_language)
    except AppError as exc:
        return f"/jobs/{job_id}?confirm_err=" + quote(exc.message)
    return f"/jobs/{job_id}"


def _jobs_href(  # noqa: PLR0917 — параметры href: group/sort/status/sheet + book (R5)
    group: str,
    sort_field: str,
    direction: str,
    status_list: list[str] | None = None,
    sheet_name: str | None = None,
    book: str | None = None,
) -> str:
    """Href /jobs с текущими группой/сортировкой/точными статусами
    (серия R §10/§12: меню «Ещё», пилюли, сброс, FAB, листы).
    Серия R §7 (R5): ``book`` — фильтр по книге (карточка, «Экспорт»
    и «Все (N)»); сохраняется в каждом derived-href, сбрасывается
    только явной «сброс»-ссылкой."""
    pairs: list[tuple[str, str]] = []
    if group and group != "all":
        pairs.append(("group", group))
    for value in status_list or []:
        pairs.append(("status", value))
    if sort_field != "updated_at":
        pairs.append(("sort", sort_field))
    if direction != "desc":
        pairs.append(("direction", direction))
    if book:
        pairs.append(("book", book))
    if sheet_name:
        pairs.append(("sheet", sheet_name))
    return "/jobs?" + urlencode(pairs) if pairs else "/jobs"


def build_router(  # noqa: PLR0915 — роутер FastAPI: все маршруты в одном замыкании (R1–R5)
    settings: Settings, repo: JobRepository, templates: Jinja2Templates
) -> APIRouter:
    router = APIRouter()

    def ctx(request: Request, title: str, current: str) -> dict[str, object]:
        return {
            "request": request,
            "title": title,
            "auth_on": bool(settings.server.auth_secret),
            "s": _S,
            "current": current,
            "csrf": getattr(request.state, "csrf_token", ""),
            # model_dump: Jinja tojson сериализует plain-dict (установка
            # из YAML — Rule 1; шаблоны числовых дефолтов не имеют).
            "ui": settings.ui.model_dump(),
            # Серия R §5: точка на вкладке «Задания», пока есть run-задание.
            "has_active_job": bool(repo.list_jobs(1, 0, statuses=_RUN_STATUS_VALUES)),
        }

    def registry_codes() -> list[str]:
        return sorted(load_registry(settings.languages_root))

    @router.get("/", include_in_schema=False)
    def index(request: Request) -> Response:
        return RedirectResponse("/jobs", status_code=302)

    # ------------------------------------------------------------- Books
    @router.get("/books")
    def books_page(
        request: Request,
        q: str | None = Query(default=None),
    ) -> Response:
        # Серия R §6: строки книги; `q` — серверный подстроковый фильтр
        # (имя файла + название). Серия R §7 (R5): поиск — строка UI,
        # тот же серверный фильтр (JS — рефетч, no-JS — GET-редирект).
        return templates.TemplateResponse(
            request,
            "books.html",
            ctx(request, _page_title("title_books"), "books")
            | {
                "books": views.list_books(settings, repo, q=q),
                "q": q or "",
                "fab_href": "#u1-upload",
                "fab_label": "fab_add_book",
            },
        )

    @router.get("/books/{filename}")
    def book_detail_page(request: Request, filename: str) -> Response:
        """Серия R §7 (R5): карточка книги. Шесть позиций ТЗ: шапка
        (назад/название/⋯), обложка+название+автор+«Перевести»/«Экспорт»,
        блок «Языки» (правка тапом), до 3 последних заданий + «Все (N)»,
        свёрнутый «Технические сведения» (SHA-256), «Удалить книгу»."""
        detail = views.book_detail(settings, repo, filename)
        if detail is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        meta = detail["meta"]
        jobs: list[Job] = detail["jobs"]
        transitions = repo.list_transitions_bulk([job.id for job in jobs])
        today_start = views.today_start_epoch()
        grouped = _build_job_rows(jobs, settings, transitions, today_start)
        # до 3 последних — плоским списком (row, gname) для шаблона
        job_rows: list[tuple[dict[str, object], str]] = []
        for gname in ("now", "today", "earlier"):
            for row in grouped.get(gname, []):
                job_rows.append((row, gname))
        title = meta.get("title") or detail["name"]
        return templates.TemplateResponse(
            request,
            "book.html",
            ctx(request, title, "books")
            | {
                "detail": detail,
                "meta": meta,
                "title": title,
                "author": meta.get("author"),
                "job_rows": job_rows,
                "total_jobs": detail["total_jobs"],
                "all_jobs_href": "/jobs?book=" + quote(filename),
                "translate_href": "/jobs?sheet=create&book=" + quote(filename),
                "export_href": "/jobs?book=" + quote(filename),
                "back_href": "/books",
                "lang_codes": registry_codes(),
                "cefr_levels": list(CEFR_ORDER),
                # ⋯ карточки (ТЗ §7 п.1): «Удалить книгу» — тот же поток
                # (data-book-delete), что и красный текст внизу; no-JS —
                # ссылка на страницу-сценарий удаления.
                "appbar_menu": [
                    {
                        "label": _S["book_delete_btn"],
                        "href": f"/books/{filename}/delete",
                        "attrs": {"data-book-delete": filename},
                    },
                ],
            },
        )

    # ------------------------------------------------------------- удаление (no-JS, R5)
    # Тот же сценарий, что JS-лист (единый механизм ТЗ §7): состояние —
    # ДО вызова удаления (usage), три пути (файл / каскад / отмена),
    # каскад — два шага с тем же HMAC-токеном, что API. Голый 409
    # пользователю не показывается — только страница с причиной и путём.
    def _basic_user(request: Request) -> str:
        return str(getattr(request.state, "username", "") or "anonymous")

    def _book_delete_ctx(  # noqa: PLR0917 — request + 5 полей сценария удаления
        request: Request,
        filename: str,
        usage: dict[str, Any],
        step: str,
        error: str | None = None,
        dry_run: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        return (
            ctx(request, _page_title("book_delete_title"), "books")
            | {
                "book_name": filename,
                "usage": usage,
                "step": step,
                "error": error,
                "dry_run": dry_run,
                "back_href": f"/books/{filename}",
            }
        )

    @router.get("/books/{filename}/delete")
    def book_delete_page(request: Request, filename: str) -> Response:
        if views.book_epub_path(settings, filename) is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        usage = views.book_usage(settings, repo, filename)
        assert usage is not None  # файл проверен выше
        return templates.TemplateResponse(
            request, "book_delete.html", _book_delete_ctx(request, filename, usage, "usage")
        )

    @router.post("/books/{filename}/delete")
    def book_delete_post(request: Request, filename: str) -> Response:
        """No-JS «Удалить» (сценарий «заданий нет»): 303 /books; если
        задания появились (гонка) — страница сценария, голый 409 — нет."""
        dest = views.book_epub_path(settings, filename)
        if dest is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        if not repo.list_job_ids_by_book(filename):
            size = views.delete_book_files(settings, dest)
            logger.info(
                "book_delete(no-js): user=%s name=%s bytes=%d",
                _basic_user(request),
                filename,
                size,
            )
            return RedirectResponse("/books", status_code=303)
        usage = views.book_usage(settings, repo, filename)
        assert usage is not None
        return templates.TemplateResponse(
            request,
            "book_delete.html",
            _book_delete_ctx(request, filename, usage, "usage"),
            status_code=409,
        )

    @router.post("/books/{filename}/delete_file_only")
    def book_delete_file_only(request: Request, filename: str) -> Response:
        """No-JS «Удалить только файл»: задания сохраняются со ссылкой на
        (теперь отсутствующий) файл — как API ``?file_only=1``."""
        dest = views.book_epub_path(settings, filename)
        if dest is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        size = views.delete_book_files(settings, dest)
        logger.info(
            "book_delete_file_only(no-js): user=%s name=%s bytes=%d",
            _basic_user(request),
            filename,
            size,
        )
        return RedirectResponse("/books", status_code=303)

    @router.post("/books/{filename}/purge")
    def book_purge_page(
        request: Request,
        filename: str,
        confirm_token: str = Form(default=""),
    ) -> Response:
        """No-JS каскад — два шага (та же логика, что ``POST /api/books/
        {filename}/purge``): шаг 1 — страница подтверждения с токеном,
        шаг 2 — сверка токена и удаление ровно перечисленного."""
        dest = views.book_epub_path(settings, filename)
        if dest is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        usage = views.book_usage(settings, repo, filename)
        assert usage is not None
        if not confirm_token:
            try:
                dry = views.book_purge_dry_run(settings, repo, filename)
            except AppError as exc:
                return templates.TemplateResponse(
                    request,
                    "book_delete.html",
                    _book_delete_ctx(request, filename, usage, "usage", error=exc.message),
                    status_code=409,
                )
            return templates.TemplateResponse(
                request,
                "book_delete.html",
                _book_delete_ctx(request, filename, usage, "confirm", dry_run=dry),
            )
        try:
            dry = views.book_purge_dry_run(settings, repo, filename)
        except AppError as exc:
            return templates.TemplateResponse(
                request,
                "book_delete.html",
                _book_delete_ctx(request, filename, usage, "usage", error=exc.message),
                status_code=409,
            )
        expected = views.book_purge_token(settings.server.auth_secret, filename, dry["job_ids"])
        if not hmac.compare_digest(confirm_token, expected):
            return templates.TemplateResponse(
                request,
                "book_delete.html",
                _book_delete_ctx(
                    request, filename, usage, "usage",
                    error=_S["err_book_purge_token"],
                ),
                status_code=409,
            )
        result = views.book_purge_execute(settings, repo, filename, dry["job_ids"])
        logger.info(
            "book_purge(no-js): user=%s name=%s jobs=%s rows=%d bytes=%d",
            _basic_user(request),
            filename,
            dry["job_ids"],
            result["rows"],
            result["bytes"],
        )
        return RedirectResponse("/books", status_code=303)

    # ------------------------------------------------------------- метаданные (no-JS, R5)
    # Та же пара «одно поле + сохранить», что JS-лист (единый механизм
    # ТЗ §7): страница-форма с ОДНИМ полем; отказ — страница с причиной
    # (fail closed, как API 400), файл не тронут.
    def _book_meta_ctx(  # noqa: PLR0917 — request + 5 полей формы метаданных
        request: Request,
        filename: str,
        field: str,
        value: str,
        kind: str,
        error: str | None = None,
    ) -> dict[str, object]:
        return (
            ctx(request, _page_title("book_meta_title"), "books")
            | {
                "book_name": filename,
                "field": field,
                "fkey": views.BOOK_META_FIELD_KEYS[field],
                "value": value,
                "kind": kind,
                "error": error,
                "lang_codes": registry_codes(),
                "cefr_levels": list(CEFR_ORDER),
                "back_href": f"/books/{filename}",
            }
        )

    @router.get("/books/{filename}/meta")
    def book_meta_page(
        request: Request,
        filename: str,
        field: str = Query(default="title"),
    ) -> Response:
        dest = views.book_epub_path(settings, filename)
        if dest is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        if field not in views.BOOK_META_FIELDS:
            field = "title"
        meta = views.read_book_meta(dest)
        kind = views.BOOK_META_FIELDS[field][0]
        return templates.TemplateResponse(
            request,
            "book_meta.html",
            _book_meta_ctx(request, filename, field, str(meta.get(field) or ""), kind),
        )

    @router.post("/books/{filename}/meta")
    def book_meta_post(
        request: Request,
        filename: str,
        field: str = Form(default=""),
        value: str = Form(default=""),
    ) -> Response:
        dest = views.book_epub_path(settings, filename)
        if dest is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        if field not in views.BOOK_META_FIELDS:
            raise HTTPException(
                status_code=400, detail=_S["err_book_bad_field"].format(field=field)
            )
        try:
            views.update_book_meta(settings, filename, {field: value})
        except AppError as exc:
            kind = views.BOOK_META_FIELDS[field][0]
            return templates.TemplateResponse(
                request,
                "book_meta.html",
                _book_meta_ctx(request, filename, field, value, kind, error=exc.message),
                status_code=400,
            )
        logger.info(
            "book_meta(no-js): user=%s name=%s field=%s",
            _basic_user(request),
            filename,
            field,
        )
        return RedirectResponse(f"/books/{filename}", status_code=303)

    # ------------------------------------------------------------- Jobs (список)
    @router.get("/jobs")
    def jobs_page(  # noqa: PLR0917 — query-параметры FastAPI в сигнатуре
        request: Request,
        group: str = Query(default="all"),
        status: Annotated[list[str] | None, Query()] = None,
        sort: str = Query(default="updated_at"),
        direction: str = Query(default="desc"),
        sheet: str = Query(default=""),
        job: str | None = Query(default=None),
        retry_of: str | None = Query(default=None),
        book: str | None = Query(default=None),
        create_err: str | None = Query(default=None),
    ) -> Response:
        """Серия R §10: строки списка, сегментный фильтр (группы §4),
        группировка «Сейчас/Сегодня/Раньше», точный фильтр и очистка —
        в листах (``?sheet=`` — no-JS-вариант той же формы).

        Серия R §11/§12: листы create (создание; ``retry_of`` — повтор
        с параметрами исходного задания) и export (``job`` — артефакты);
        порядок wait-строк отражает queue_rank (§11).

        Пагинации нет: группировка пересекает страницы, всё, что
        подходит фильтру, в списке (``ui.page_size`` остаётся для API)."""
        group_norm = group if group in _GROUP_STATUS_GROUPS else "all"
        exact = frozenset(v for v in (status or []) if v in _ALL_JOB_STATUS_VALUES)
        statuses = _group_statuses(group_norm, exact)
        sort_field = sort if sort in _SORT_FIELDS else "updated_at"
        direction_norm = "asc" if direction == "asc" else "desc"
        # Серия R §7 (R5): фильтр по книге (карточка: «Экспорт», «Все (N)»);
        # незнакомое имя — игнор (как предвыбор мастера, R4).
        book_norm = book if book and views.book_epub_path(settings, book) else None

        if statuses == ():
            jobs: list[Job] = []
        else:
            jobs = repo.list_jobs(
                None,
                0,
                statuses=statuses,
                book=book_norm,
                sort=sort_field,
                direction=direction_norm,
            )

        # Группировка — сервером, единый источник views.job_list_group;
        # время работы — E7/M7 (bulk-переходы, без N+1).
        today_start = views.today_start_epoch()
        transitions = repo.list_transitions_bulk([job.id for job in jobs])
        group_rows = _build_job_rows(jobs, settings, transitions, today_start)

        # Серия R §11: порядок группы «Ожидание» отражает queue_rank
        # (queue_rank DESC, created_at ASC); остальные строки — по §10.
        if group_norm == "waiting":
            for rows in group_rows.values():
                _sort_waiting_rows(rows)

        sheet_norm = sheet if sheet in ("filter", "purge", "create", "export") else ""
        export_data, create_data, export_missing = _jobs_sheet_prep(
            repo, settings, sheet_norm, job, retry_of
        )
        if export_missing:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "jobs"),
                status_code=404,
            )

        books_list = views.list_books(settings, repo)
        # R4 (ТЗ §12): ?book=<имя> — мастер открыт с карточки книги
        # (кнопка «Перевести» — R5): книга предвыбрана (JS — шаг 1 скрыт,
        # no-JS — радио отмечено, список на месте — можно сменить).
        # Незнакомое имя — игнор (шаг 1 целиком).
        if (
            sheet_norm == "create"
            and book
            and create_data is not None
            and any(b["name"] == book for b in books_list)
        ):
            create_data["book"] = book

        status_list = [v for v in (status or []) if v in _ALL_JOB_STATUS_VALUES]
        worker_status, worker_detail = worker_alive(settings)
        appbar_menu: list[dict[str, object]] = [
            {"text": _S["jobs_worker_line"].format(status=worker_status)},
            {"text": _S["jobs_menu_sort"]},
            {
                "label": _S["jobs_sort_updated"] + " ↓",
                "href": _jobs_href(group_norm, "updated_at", "desc", status_list, book=book_norm),
                "current": sort_field == "updated_at" and direction_norm == "desc",
            },
            {
                "label": _S["jobs_sort_created"] + " ↓",
                "href": _jobs_href(group_norm, "created_at", "desc", status_list, book=book_norm),
                "current": sort_field == "created_at" and direction_norm == "desc",
            },
            {
                "label": _S["jobs_sort_created"] + " ↑",
                "href": _jobs_href(group_norm, "created_at", "asc", status_list, book=book_norm),
                "current": sort_field == "created_at" and direction_norm == "asc",
            },
            {
                "label": _S["jobs_menu_filter"],
                "href": _jobs_href(
                    group_norm, sort_field, direction_norm, status_list, "filter", book_norm
                ),
                "sheet": "u3-sheet-filter",
            },
            {
                "label": _S["jobs_menu_purge"],
                "href": _jobs_href(
                    group_norm, sort_field, direction_norm, status_list, "purge", book_norm
                ),
                "sheet": "u3-sheet-purge",
            },
        ]
        pills = [
            ("all", _S["jobs_seg_all"]),
            ("active", _S["jobs_seg_active"]),
            ("waiting", _S["jobs_seg_waiting"]),
            ("done", _S["jobs_seg_done"]),
            ("errors", _S["jobs_seg_errors"]),
        ]
        return templates.TemplateResponse(
            request,
            "jobs.html",
            ctx(request, _page_title("title_jobs"), "jobs")
            | {
                "group_rows": group_rows,
                "total": sum(len(rows) for rows in group_rows.values()),
                "group": group_norm,
                "pills": pills,
                "pill_href": lambda g: _jobs_href(
                    g, sort_field, direction_norm, status_list, book=book_norm
                ),
                "all_statuses": list(_ALL_JOB_STATUS_VALUES),
                "terminal_statuses": [st.value for st in TERMINAL_STATES],
                "f_status": status_list,
                # «Сбросить фильтр» в листе: точные статусы снимаются,
                # группа и сортировка сохраняются.
                "reset_href": _jobs_href(group_norm, sort_field, direction_norm),
                # Серия R §7 (R5): строка фильтра по книге («Книга: <имя>»
                # + сброс — снят ТОЛЬКО фильтр книги, группа/сортировка/
                # статусы сохраняются).
                "book": book_norm,
                "book_reset_href": _jobs_href(
                    group_norm, sort_field, direction_norm, status_list
                ),
                "sort_field": sort_field,
                "sort_direction": direction_norm,
                "worker_status": worker_status,
                "worker_detail": worker_detail,
                "worker_down": not worker_status.startswith("ok"),
                "appbar_menu": appbar_menu,
                "sheet": sheet_norm,
                "export": export_data,
                "create": create_data,
                # причина отказа no-JS-создания (303 c ?create_err=)
                "create_err": create_err if sheet_norm == "create" else None,
                # лист создания: коды языков (реестр) и режимы задания
                "lang_codes": registry_codes(),
                "job_modes": JOB_MODES,
                "books": books_list,
                # R4 (ТЗ §12): умолчания мастера создания (единая точка
                # базовой линии services.jobs.create_baseline — как у чипов);
                # R5 (ТЗ §7): книга карточки — её target_default/cefr_default
                # перекрывают базовую линию (валидные значения только).
                "create_defaults": views.create_defaults(
                    repo, settings, book=book_norm
                ),
                # R4: «Ещё» — глоссарий read-only из конфига (per-job — вне
                # границ ТЗ §1: изменение pipeline.py)
                "glossary_enabled": settings.glossary.enabled,
                # Серия R §12: FAB «Новое задание» → лист создания
                # (JS — <dialog>, без JS — страница-форма ?sheet=create).
                "fab_href": _jobs_href(
                    group_norm, sort_field, direction_norm, status_list, "create", book_norm
                ),
                "fab_label": "fab_new_job",
                "fab_sheet": "u3-sheet-create",
            },
        )

    # ------------------------------------------------------------- создание (no-JS)
    @router.post("/jobs/new")
    def job_new(  # noqa: PLR0917 — поля формы создания (ТЗ §12)
        request: Request,
        book: str = Form(...),
        target_language: str = Form(...),
        mode: str = Form(default="bilingual"),
        learning_level: str = Form(default=""),
        learning_cefr: str = Form(default=""),
    ) -> Response:
        """Серия R §12/T15: no-JS-создание (лист ?sheet=create → форма).

        JSON-API ``POST /api/jobs`` без изменений (потребитель — app.js);
        страница-маршрут принимает форму, создаёт задание тем же
        сервисом (create_job — единый механизм) и уводит 303: успех —
        на список, отказ — обратно на лист с причиной (verbatim).
        R4: опциональные learning_level/learning_cefr (мастер «Ещё»/
        «Уровень»; пустые — не заданы, валидация — create_job)."""
        return RedirectResponse(
            _jobs_new_location(
                settings,
                repo,
                book,
                target_language,
                mode,
                learning_level,
                learning_cefr,
            ),
            status_code=303,
        )

    @router.post("/jobs/{job_id}/confirm")
    def job_confirm_form(
        request: Request,
        job_id: str,
        source_language: str | None = Form(default=None),
    ) -> Response:
        """Серия R T15: no-JS-подтверждение NEEDS_CONFIRMATION (форма
        строки). JSON-API ``/api/jobs/{job_id}/confirm`` без изменений
        (потребитель — app.js); форма — тот же сервис ``confirm_job``,
        303 на экран задания (успех) или с причиной (отказ, verbatim)."""
        if repo.get(job_id) is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "jobs"),
                status_code=404,
            )
        return RedirectResponse(
            _jobs_confirm_location(repo, job_id, source_language), status_code=303
        )

    # ------------------------------------------------------------- Job (страница)
    @router.get("/jobs/{job_id}")
    def job_page(
        request: Request,
        job_id: str,
        sheet: str = Query(default=""),
        confirm_err: str | None = Query(default=None),
    ) -> Response:
        job: Job | None = repo.get(job_id)
        if job is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "jobs"),
                status_code=404,
            )
        facts = views.job_facts(settings, repo, job)
        detection = facts.get("language_detection") or {}
        detected = detection.get("detected") or detection.get("accepted_language")
        return templates.TemplateResponse(
            request,
            "job.html",
            ctx(request, _page_title("title_job", id=job.id), "jobs")
            | {
                "job": job,
                "back_href": "/jobs",
                "facts": facts,
                "job_id": job_id,
                "registry_codes": registry_codes(),
                "detected": detected,
                "log_tail_lines": settings.ui.log_tail_lines,
                # Серия R §11: лист удаления — имя книги (no-JS ?sheet=
                # delete: POST-форма — браузер не умеет DELETE из формы,
                # API принимает POST как алиас; JS: <dialog>).
                "book_name": Path(job.source_path).name,
                "sheet": sheet if sheet == "delete" else "",
                # no-JS-подтверждение: причина отказа (303 c ?confirm_err=)
                "confirm_err": confirm_err,
            },
        )

    # ------------------------------------------------------------- Settings (серия R §5)
    @router.get("/settings")
    def settings_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request, "settings.html", ctx(request, _page_title("title_settings"), "settings")
        )

    @router.get("/settings/llm")
    def settings_llm_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "settings_llm.html",
            ctx(request, _page_title("title_models"), "settings")
            | {
                "back_href": "/settings",
                "models": views.models_view(settings),
            },
        )

    @router.get("/settings/languages")
    def settings_languages_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "settings_languages.html",
            ctx(request, _page_title("title_languages"), "settings")
            | {
                "back_href": "/settings",
                "data": views.languages_view(settings),
            },
        )

    @router.get("/settings/prompts")
    def settings_prompts_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "settings_prompts.html",
            ctx(request, _page_title("title_prompts"), "settings")
            | {
                "back_href": "/settings",
                "rows": views.prompts_view(settings),
                "tasks": views.prompts_tasks_view(settings),
                "registry_codes": registry_codes(),
            },
        )

    # --- старые пути: 301 (серия R §5) ------------------------------------
    @router.get("/languages", include_in_schema=False)
    def languages_redirect() -> Response:
        return RedirectResponse("/settings/languages", status_code=301)

    @router.get("/models", include_in_schema=False)
    def models_redirect() -> Response:
        return RedirectResponse("/settings/llm", status_code=301)

    @router.get("/prompts", include_in_schema=False)
    def prompts_redirect() -> Response:
        return RedirectResponse("/settings/prompts", status_code=301)

    return router
