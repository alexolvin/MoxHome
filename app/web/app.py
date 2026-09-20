"""Сборка приложения U1: FastAPI + Jinja2 + статика + auth middleware.

Доступ (ТЗ U1.10): bind-адрес — Tailscale-адрес из конфигурации
(``load_settings`` fail closed; wildcard — только с ALLOW_WILDCARD_BIND);
при заданном ``SERVER_AUTH_SECRET`` — HTTP Basic (пароль = секрет) на
страницах и /api (U1-7). Публичного reverse proxy нет; полноценной
системы пользователей нет.

ТЗ UI §7.1: 401/403 — страницы в общем оформлении, поэтому статика
(/static) публична: она не несёт данных и секретов (CSRF-токены — в
per-page meta), а гостю нужно загрузить style.css для страницы входа.
Данные под auth: страницы и /api.

TЗ UI (публичный доступ):
- 401/403 для навигации браузера — страницы в общем оформлении
  (auth.html / forbidden.html), а не системный диалог браузера;
  для API/fetch — прежний JSON (ТЗ UI §7.1, §7.3: коды не меняются).
- логин через форму POST /login → cookie u3-token (HMAC-токен,
  секрет в cookie не попадает); HTTP Basic для API-клиентов — как в U1.
- catch-all 404: любой неизвестный путь (кроме /api/) отдаёт
  not_found.html (E1); /api/ остаётся JSON.
- /favicon.ico — публичный ассет вне auth (E9: GET → 200), чтобы
  страница входа не роняла console.error на иконке.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler as _default_http_handler
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.web import views
from app.web.api import router as api_router
from app.web.auth import (
    LOGIN_COOKIE,
    WWW_AUTHENTICATE,
    auth_check,
    login_token,
)
from app.web.pages import build_router
from app.web.strings import UI_STRINGS

_DIR = Path(__file__).parent
TEMPLATES_DIR = _DIR / "templates"
STATIC_DIR = _DIR / "static"

_S = UI_STRINGS
#: Публичный ассет вне auth (E9): иконка не несёт данных, а 401 на ней
#: роняет console.error на странице входа (TЗ UI §7.1).
_PUBLIC_PATHS = frozenset({"/favicon.ico"})
#: Срок жизни cookie-логина, с: неделя (TЗ UI срок не фиксирует;
#: очистка — через менеджер браузера или повторный вход).
_LOGIN_COOKIE_MAX_AGE = 7 * 24 * 3600


def _wants_html(request: Request) -> bool:
    """Навигация браузера (документ) — HTML-ответ; fetch/XHR и API-
    клиенты (sec-fetch-mode: cors/xhr, Accept без text/html) — JSON.
    Запросы без Sec-Fetch-* (curl, Playwright ctx.request, тесты) —
    API-клиенты: JSON, поведение U3, зафиксированное тестами."""
    mode = request.headers.get("sec-fetch-mode", "")
    if mode in ("navigate", "document", "form"):
        return True
    accept = request.headers.get("accept", "")
    return "text/html" in accept and "application/json" not in accept


def _login_ctx(request: Request, *, error: bool = False) -> dict[str, object]:
    """Контекст страницы входа: общее оформление (base.html); CSRF не
    нужен — форма /login работает до аутентификации (яйцо-и-курица)."""
    return {
        "request": request,
        "title": _S["auth_title"],
        "auth_on": True,
        "s": _S,
        "current": "",
        "csrf": "",
        "ui": request.app.state.settings.ui.model_dump(),
        "next_path": request.url.path,
        "error": error,
    }


def _forbidden_ctx(request: Request, message: str) -> dict[str, object]:
    return {
        "request": request,
        "title": _S["forbidden_title"],
        "auth_on": bool(request.app.state.settings.server.auth_secret),
        "s": _S,
        "current": "",
        "csrf": "",
        "ui": request.app.state.settings.ui.model_dump(),
        "message": message,
    }


def _not_found_ctx(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "request": request,
        "title": _S["title_not_found"],
        "auth_on": bool(settings.server.auth_secret),
        "s": _S,
        "current": "jobs",
        "csrf": getattr(request.state, "csrf_token", ""),
        "ui": settings.ui.model_dump(),
    }


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
    # Серия R §4: статус→группа и глиф — единая таблица views.STATUS_GROUP;
    # в шаблонах условий по статусу нет, только эти два вызова.
    templates.env.filters["status_group"] = views.status_group
    templates.env.globals["status_glyph"] = views.status_glyph

    @app.middleware("http")
    async def _auth(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if (
            request.url.path in _PUBLIC_PATHS
            or request.url.path == "/login"
            or request.url.path.startswith("/static/")
        ):
            # /favicon.ico — публичная иконка (E9); /login обрабатывает
            # свой 401 сам (иначе до эндпоинта не добраться); /static —
            # публичная статика без данных и секретов (TЗ UI §7.1: 401/403
            # страницы в общем оформлении — гостю нужен style.css).
            return await call_next(request)
        denied = await auth_check(settings, request)
        if denied is not None:
            if _wants_html(request):
                if denied.is_unauthorized:
                    return templates.TemplateResponse(
                        request,
                        "auth.html",
                        _login_ctx(request, error=denied.reason == "wrong"),
                        status_code=401,
                    )
                return templates.TemplateResponse(
                    request,
                    "forbidden.html",
                    _forbidden_ctx(request, denied.message),
                    status_code=403,
                )
            return denied.as_json()
        return await call_next(request)

    @app.get("/login", include_in_schema=False)
    async def login_get(request: Request) -> Response:
        """Прямой адрес страницы входа (без секрета — сразу к заданиям;
        уже авторизованный пользователь — туда же)."""
        if not settings.server.auth_secret or await auth_check(
            settings, request
        ) is None:
            return RedirectResponse("/jobs", status_code=303)
        return templates.TemplateResponse(request, "auth.html", _login_ctx(request))

    @app.post("/login", include_in_schema=False)
    async def login_post(request: Request) -> Response:
        if not settings.server.auth_secret:
            return RedirectResponse("/jobs", status_code=303)
        password = ""
        try:
            form = await request.form()
            password = str(form.get("password", ""))
        except (ValueError, UnicodeDecodeError):
            pass
        if not password or not hmac.compare_digest(
            password.encode("utf-8"), settings.server.auth_secret.encode("utf-8")
        ):
            if _wants_html(request):
                return templates.TemplateResponse(
                    request,
                    "auth.html",
                    _login_ctx(request, error=True),
                    status_code=401,
                )
            return JSONResponse(
                {"error": "unauthorized", "message": _S["auth_required_message"]},
                status_code=401,
                headers={"WWW-Authenticate": WWW_AUTHENTICATE},
            )
        next_path = str(form.get("next", "/jobs"))
        if not next_path.startswith("/") or next_path.startswith("//"):
            next_path = "/jobs"
        response = RedirectResponse(next_path, status_code=303)
        response.set_cookie(
            LOGIN_COOKIE,
            login_token(settings),
            max_age=_LOGIN_COOKIE_MAX_AGE,
            httponly=True,
            samesite="strict",
        )
        return response

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")

    @app.exception_handler(HTTPException)
    async def http_exception(
        request: Request, exc: HTTPException
    ) -> Response:
        """E1: неизвестный путь (404) вне /api/ — not_found.html; всё
        остальное — дефолтный обработчик FastAPI (/api/ — JSON, U3)."""
        if exc.status_code == 404 and not request.url.path.startswith("/api/"):
            return templates.TemplateResponse(
                request,
                "not_found.html",
                _not_found_ctx(request),
                status_code=404,
            )
        return await _default_http_handler(request, exc)

    app.include_router(api_router)
    app.include_router(build_router(settings, repo, templates))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app
