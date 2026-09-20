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

from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.core.config import Settings
from app.core.worker_signal import worker_alive
from app.domain.job import TERMINAL_STATES, Job, JobStatus
from app.languages.registry import load_registry
from app.storage.jobs_repo import JobRepository, TransitionRecord
from app.web import views
from app.web.strings import UI_STRINGS

_S = UI_STRINGS

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
            }
        )
    return group_rows


def build_router(
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
        # (имя файла + название); поиск в строке UI — R5.
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

    # ------------------------------------------------------------- Jobs (список)
    @router.get("/jobs")
    def jobs_page(  # noqa: PLR0917 — query-параметры FastAPI в сигнатуре
        request: Request,
        group: str = Query(default="all"),
        status: Annotated[list[str] | None, Query()] = None,
        sort: str = Query(default="updated_at"),
        direction: str = Query(default="desc"),
        sheet: str = Query(default=""),
    ) -> Response:
        """Серия R §10: строки списка, сегментный фильтр (группы §4),
        группировка «Сейчас/Сегодня/Раньше», точный фильтр и очистка —
        в листах (``?sheet=`` — no-JS-вариант той же формы).

        Пагинации нет: группировка пересекает страницы, всё, что
        подходит фильтру, в списке (``ui.page_size`` остаётся для API)."""
        group_norm = group if group in _GROUP_STATUS_GROUPS else "all"
        exact = frozenset(v for v in (status or []) if v in _ALL_JOB_STATUS_VALUES)
        statuses = _group_statuses(group_norm, exact)
        sort_field = sort if sort in _SORT_FIELDS else "updated_at"
        direction_norm = "asc" if direction == "asc" else "desc"

        if statuses == ():
            jobs: list[Job] = []
        else:
            jobs = repo.list_jobs(
                None, 0, statuses=statuses, sort=sort_field, direction=direction_norm
            )

        # Группировка — сервером, единый источник views.job_list_group;
        # время работы — E7/M7 (bulk-переходы, без N+1).
        today_start = views.today_start_epoch()
        transitions = repo.list_transitions_bulk([job.id for job in jobs])
        group_rows = _build_job_rows(jobs, settings, transitions, today_start)

        def jobs_href(
            g: str,
            s_field: str,
            d: str,
            st: list[str] | None = None,
            sheet_name: str | None = None,
        ) -> str:
            """Href /jobs с текущими группой/сортировкой/точными статусами."""
            pairs: list[tuple[str, str]] = []
            if g and g != "all":
                pairs.append(("group", g))
            for value in st or []:
                pairs.append(("status", value))
            if s_field != "updated_at":
                pairs.append(("sort", s_field))
            if d != "desc":
                pairs.append(("direction", d))
            if sheet_name:
                pairs.append(("sheet", sheet_name))
            return "/jobs?" + urlencode(pairs) if pairs else "/jobs"

        status_list = [v for v in (status or []) if v in _ALL_JOB_STATUS_VALUES]
        worker_status, worker_detail = worker_alive(settings)
        appbar_menu: list[dict[str, object]] = [
            {"text": _S["jobs_worker_line"].format(status=worker_status)},
            {"text": _S["jobs_menu_sort"]},
            {
                "label": _S["jobs_sort_updated"] + " ↓",
                "href": jobs_href(group_norm, "updated_at", "desc", status_list),
                "current": sort_field == "updated_at" and direction_norm == "desc",
            },
            {
                "label": _S["jobs_sort_created"] + " ↓",
                "href": jobs_href(group_norm, "created_at", "desc", status_list),
                "current": sort_field == "created_at" and direction_norm == "desc",
            },
            {
                "label": _S["jobs_sort_created"] + " ↑",
                "href": jobs_href(group_norm, "created_at", "asc", status_list),
                "current": sort_field == "created_at" and direction_norm == "asc",
            },
            {
                "label": _S["jobs_menu_filter"],
                "href": jobs_href(group_norm, sort_field, direction_norm, status_list, "filter"),
                "sheet": "u3-sheet-filter",
            },
            {
                "label": _S["jobs_menu_purge"],
                "href": jobs_href(group_norm, sort_field, direction_norm, status_list, "purge"),
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
                "pill_href": lambda g: jobs_href(g, sort_field, direction_norm, status_list),
                "all_statuses": list(_ALL_JOB_STATUS_VALUES),
                "terminal_statuses": [st.value for st in TERMINAL_STATES],
                "f_status": status_list,
                # «Сбросить фильтр» в листе: точные статусы снимаются,
                # группа и сортировка сохраняются.
                "reset_href": jobs_href(group_norm, sort_field, direction_norm),
                "sort_field": sort_field,
                "sort_direction": direction_norm,
                "worker_status": worker_status,
                "worker_detail": worker_detail,
                "worker_down": not worker_status.startswith("ok"),
                "appbar_menu": appbar_menu,
                "sheet": sheet if sheet in ("filter", "purge") else "",
                "books": views.list_books(settings, repo),
                "fab_href": "/books",
                "fab_label": "fab_new_job",
            },
        )

    # ------------------------------------------------------------- Job (страница)
    @router.get("/jobs/{job_id}")
    def job_page(request: Request, job_id: str) -> Response:
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
