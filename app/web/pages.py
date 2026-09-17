"""HTML-страницы U1/U3: разделы Books/Jobs/Languages/Models/Prompts/Settings.

Каждая страница — серверный shell + данные своего API-endpoint'а
(каждый endpoint из ТЗ имеет потребителя, U1-6). Страница задания
дополнительно рендерит фактические данные сервером (без JS тоже
читается) и обновляет блок прогресса поллингом ``GET /api/jobs/{id}``.

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
from app.languages.cefr import CEFR_LEVELS
from app.languages.registry import load_registry
from app.services.languages import matrix_report
from app.storage.jobs_repo import JobRepository
from app.web import views
from app.web.strings import UI_STRINGS

_S = UI_STRINGS

_SORT_FIELDS = ("created_at", "updated_at")


def _page_title(key: str, **kwargs: object) -> str:
    return _S[key].format(**kwargs) if kwargs else _S[key]


def _jobs_query_pairs(
    status: list[str] | None,
    *,
    book: str | None,
    pair: str | None,
    created_from: str | None,
    created_to: str | None,
    sort_field: str,
    direction_norm: str,
) -> list[tuple[str, str]]:
    """Текущий фильтр списка заданий как пары query-параметров (пагинация)."""
    pairs: list[tuple[str, str]] = []
    for value in status or []:
        pairs.append(("status", value))
    if book:
        pairs.append(("book", book))
    if pair:
        pairs.append(("pair", pair))
    if created_from:
        pairs.append(("created_from", created_from))
    if created_to:
        pairs.append(("created_to", created_to))
    if sort_field != "created_at":
        pairs.append(("sort", sort_field))
    if direction_norm != "desc":
        pairs.append(("direction", direction_norm))
    return pairs


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
        }

    def registry_codes() -> list[str]:
        return sorted(load_registry(settings.languages_root))

    def matrix_pairs() -> list[dict[str, str]]:
        return [
            {
                "source": report.source,
                "target": report.target,
                "value": f"{report.source}|{report.target}",
            }
            for report in matrix_report(settings)
        ]

    def books_list() -> list[dict[str, object]]:
        return views.list_books(settings)

    @router.get("/", include_in_schema=False)
    def index(request: Request) -> Response:
        return RedirectResponse("/jobs", status_code=302)

    # ------------------------------------------------------------- Books
    @router.get("/books")
    def books_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "books.html",
            ctx(request, _page_title("title_books"), "books")
            | {
                "books": books_list(),
                "csrf": getattr(request.state, "csrf_token", ""),
            },
        )

    # ------------------------------------------------------------- Jobs (список)
    @router.get("/jobs")
    def jobs_page(  # noqa: PLR0917 — query-параметры FastAPI в сигнатуре
        request: Request,
        status: Annotated[list[str] | None, Query()] = None,
        book: str | None = Query(default=None),
        pair: str | None = Query(default=None),
        created_from: str | None = Query(default=None),
        created_to: str | None = Query(default=None),
        sort: str = Query(default="created_at"),
        direction: str = Query(default="desc"),
        page: int = Query(default=1, ge=1),
    ) -> Response:
        page_size = settings.ui.page_size
        statuses = tuple(status) if status else None
        source_lang = target_lang = None
        if pair and "|" in pair:
            source_lang, _sep, target_lang = pair.partition("|")
            source_lang, target_lang = source_lang or None, target_lang or None
        created_from_ts = views.day_to_epoch(created_from)
        created_to_ts = views.day_to_epoch(created_to, end_of_day=True)
        sort_field = sort if sort in _SORT_FIELDS else "created_at"
        direction_norm = "asc" if direction == "asc" else "desc"

        total = repo.count_jobs(
            statuses=statuses,
            book=book,
            source_language=source_lang,
            target_language=target_lang,
            created_from=created_from_ts,
            created_to=created_to_ts,
        )
        offset = (page - 1) * page_size
        jobs = repo.list_jobs(
            page_size,
            offset,
            statuses=statuses,
            book=book,
            source_language=source_lang,
            target_language=target_lang,
            created_from=created_from_ts,
            created_to=created_to_ts,
            sort=sort_field,
            direction=direction_norm,
        )
        rows: list[dict[str, object]] = []
        for job in jobs:
            validation = views.read_json(settings.job_dir(job.id) / "validation.json")
            rows.append(
                {
                    "job": job,
                    "book_name": Path(job.source_path).name,
                    "progress_display": views.progress_facts(job, validation)["display"],
                    "created_at_human": views.human_ts(job.created_at),
                    "updated_at_human": views.human_ts(job.updated_at),
                }
            )
        total_pages = max(1, -(-total // page_size)) if total else 1
        worker_status, worker_detail = worker_alive(settings)
        sort_label = (
            _S["jobs_sort_created"] if sort_field == "created_at" else _S["jobs_sort_updated"]
        )
        direction_label = (
            _S["jobs_sort_asc"] if direction_norm == "asc" else _S["jobs_sort_desc"]
        )
        query_pairs = _jobs_query_pairs(
            status,
            book=book,
            pair=pair,
            created_from=created_from,
            created_to=created_to,
            sort_field=sort_field,
            direction_norm=direction_norm,
        )
        prev_href = (
            "/jobs?" + urlencode([*query_pairs, ("page", str(page - 1))]) if page > 1 else None
        )
        next_href = (
            "/jobs?" + urlencode([*query_pairs, ("page", str(page + 1))])
            if page < total_pages
            else None
        )
        return templates.TemplateResponse(
            request,
            "jobs.html",
            ctx(request, _page_title("title_jobs"), "jobs")
            | {
                "rows": rows,
                "books": books_list(),
                "registry_codes": registry_codes(),
                "cefr_levels": sorted(CEFR_LEVELS),
                "worker_status": worker_status,
                "worker_detail": worker_detail,
                "all_statuses": [st.value for st in JobStatus],
                "terminal_statuses": [st.value for st in TERMINAL_STATES],
                "matrix_pairs": matrix_pairs(),
                "f_status": list(status or []),
                "f_book": book or "",
                "f_pair": pair or "",
                "f_created_from": created_from or "",
                "f_created_to": created_to or "",
                "sort_field": sort_field,
                "sort_direction": direction_norm,
                "sort_label": sort_label,
                "direction_label": direction_label,
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "page_size": page_size,
                "has_filters": bool(statuses or book or pair or created_from or created_to),
                "prev_href": prev_href,
                "next_href": next_href,
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
                "facts": facts,
                "job_id": job_id,
                "registry_codes": registry_codes(),
                "detected": detected,
                "log_tail_lines": settings.ui.log_tail_lines,
            },
        )

    # ------------------------------------------------------------- Languages
    @router.get("/languages")
    def languages_page(request: Request) -> Response:
        data = views.languages_view(settings)
        return templates.TemplateResponse(
            request,
            "languages.html",
            ctx(request, _page_title("title_languages"), "languages") | {"data": data},
        )

    # ------------------------------------------------------------- Models
    @router.get("/models")
    def models_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "models.html",
            ctx(request, _page_title("title_models"), "models")
            | {"models": views.models_view(settings)},
        )

    # ------------------------------------------------------------- Prompts
    @router.get("/prompts")
    def prompts_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "prompts.html",
            ctx(request, _page_title("title_prompts"), "prompts")
            | {
                "rows": views.prompts_view(settings),
                "tasks": views.prompts_tasks_view(settings),
                "registry_codes": registry_codes(),
            },
        )

    # ------------------------------------------------------------- Settings
    @router.get("/settings")
    def settings_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request, "settings.html", ctx(request, _page_title("title_settings"), "settings")
        )

    return router
