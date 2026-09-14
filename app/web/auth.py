"""Доступ U1: общий секрет через HTTP Basic (пароль = SERVER_AUTH_SECRET).

Правила (ТЗ U1.10): привязка к Tailscale-адресу — через
``server.bind_address`` из конфигурации (load_settings fail closed);
дополнительно — общий секрет. Полноценной системы пользователей нет.

Выбор Basic (а не cookie с секретом): секрет существует только в
заголовке запроса — он не попадает ни в HTML-ответы, ни в URL, ни в
access-лог (критерии U1-7/U1-8). Имя пользователя любое (проверка —
только пароль), сравнение — константное время (hmac.compare_digest).

Секрет не задан (пустая строка) — доступ открыт; режим отмечен на
странице Settings («auth: off»), чтобы он не выглядел настроенным.
"""

from __future__ import annotations

import base64
import hmac

from fastapi import Request
from starlette.responses import JSONResponse

from app.core.config import Settings

_WWW_AUTHENTICATE = 'Basic realm="epubtranslator"'


def _authorized(settings: Settings, header: str | None) -> bool:
    secret = settings.server.auth_secret
    if not secret:
        return True
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header.removeprefix("Basic ").strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    password = decoded.rpartition(":")[2]
    return hmac.compare_digest(password.encode("utf-8"), secret.encode("utf-8"))


def auth_check(settings: Settings, request: Request) -> JSONResponse | None:
    """Middleware-проверка: None — пропустить, JSONResponse(401) — отклонить."""
    if _authorized(settings, request.headers.get("Authorization")):
        return None
    return JSONResponse(
        {"error": "unauthorized", "message": "требуется общий секрет (SERVER_AUTH_SECRET)"},
        status_code=401,
        headers={"WWW-Authenticate": _WWW_AUTHENTICATE},
    )
