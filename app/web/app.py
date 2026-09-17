"""Сборка приложения U1: FastAPI + Jinja2 + статика + auth middleware.

Доступ (ТЗ U1.10): bind-адрес — Tailscale-адрес из конфигурации
(``load_settings`` fail closed; wildcard — только с ALLOW_WILDCARD_BIND);
при заданном ``SERVER_AUTH_SECRET`` — HTTP Basic (пароль = секрет) на
ВСЕХ запросах, включая /api и /static (U1-7). Публичного reverse proxy
нет; полноценной системы пользователей нет.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.core.config import Settings
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.web.api import router as api_router
from app.web.auth import auth_check
from app.web.pages import build_router

_DIR = Path(__file__).parent
TEMPLATES_DIR = _DIR / "templates"
STATIC_DIR = _DIR / "static"


def create_app(settings: Settings, db: Database | None = None) -> FastAPI:
    """App-фабрика U1. ``db`` — существующая база (тесты); иначе —
    ``settings.jobs_db_path`` (та же, что у воркера — единое состояние,
    E3.1: API пишет только создание задания и управляющие команды)."""
    app = FastAPI(title="EpubTranslator", docs_url=None, redoc_url=None)
    if db is None:
        db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    repo = JobRepository(db)
    app.state.settings = settings
    app.state.db = db
    app.state.repo = repo

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.middleware("http")
    async def _auth(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        denied = await auth_check(settings, request)
        if denied is not None:
            return denied
        return await call_next(request)

    app.include_router(api_router)
    app.include_router(build_router(settings, repo, templates))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app
