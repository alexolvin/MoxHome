"""HTML-страницы U1: разделы Books/Jobs/Languages/Models/Prompts/Settings.

Каждая страница — серверный shell + данные своего API-endpoint'а
(каждый endpoint из ТЗ имеет потребителя, U1-6). Страница задания
дополнительно рендерит фактические данные сервером (без JS тоже
читается) и обновляет блок прогресса поллингом ``GET /api/jobs/{id}``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.core.config import Settings
from app.domain.job import Job
from app.languages.cefr import CEFR_LEVELS
from app.languages.registry import load_registry
from app.storage.jobs_repo import JobRepository
from app.web import views


def build_router(
    settings: Settings, repo: JobRepository, templates: Jinja2Templates
) -> APIRouter:
    router = APIRouter()

    def ctx(request: Request, title: str) -> dict[str, object]:
        return {
            "request": request,
            "title": title,
            "auth_on": bool(settings.server.auth_secret),
        }

    @router.get("/", include_in_schema=False)
    def index(request: Request) -> Response:
        return RedirectResponse("/jobs", status_code=302)

    @router.get("/books")
    def books_page(request: Request) -> Response:
        return templates.TemplateResponse(request, "books.html", ctx(request, "Books"))

    @router.get("/jobs")
    def jobs_page(request: Request) -> Response:
        rows: list[dict[str, object]] = []
        for job in repo.list_jobs():
            validation = views.read_json(settings.job_dir(job.id) / "validation.json")
            rows.append(
                {
                    "job": job,
                    "progress_display": views.progress_facts(job, validation)["display"],
                }
            )
        return templates.TemplateResponse(
            request,
            "jobs.html",
            ctx(request, "Jobs")
            | {
                "rows": rows,
                "books": views.list_books(settings),
                "registry_codes": sorted(load_registry(settings.languages_root)),
                "cefr_levels": sorted(CEFR_LEVELS),
            },
        )

    @router.get("/jobs/{job_id}")
    def job_page(request: Request, job_id: str) -> Response:
        job: Job | None = repo.get(job_id)
        if job is None:
            return templates.TemplateResponse(
                request, "not_found.html", ctx(request, "Not found"), status_code=404
            )
        facts = views.job_facts(settings, repo, job)
        detection = facts.get("language_detection") or {}
        detected = detection.get("detected") or detection.get("accepted_language")
        return templates.TemplateResponse(
            request,
            "job.html",
            ctx(request, job.id)
            | {
                "job": job,
                "facts": facts,
                "job_id": job_id,
                "registry_codes": sorted(load_registry(settings.languages_root)),
                "detected": detected,
            },
        )

    @router.get("/languages")
    def languages_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request, "languages.html", ctx(request, "Languages")
        )

    @router.get("/models")
    def models_page(request: Request) -> Response:
        return templates.TemplateResponse(request, "models.html", ctx(request, "Models"))

    @router.get("/prompts")
    def prompts_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "prompts.html",
            ctx(request, "Prompts") | {"rows": views.prompts_view(settings)},
        )

    @router.get("/settings")
    def settings_page(request: Request) -> Response:
        return templates.TemplateResponse(request, "settings.html", ctx(request, "Settings"))

    return router
