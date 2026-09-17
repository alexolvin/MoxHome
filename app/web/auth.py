"""Доступ U1 + безопасность мутирующих операций U3.

Правила (ТЗ U1.10): привязка к Tailscale-адресу — через ``server.bind_address``
из конфигурации (load_settings fail closed); дополнительно — общий секрет.
Полноценной системы пользователей нет.

Выбор Basic (а не cookie с секретом): секрет существует только в
заголовке запроса — он не попадает ни в HTML-ответы, ни в URL, ни в
access-лог (критерии U1-7/U1-8). Имя пользователя любое (проверка —
только пароль), сравнение — константное время (hmac.compare_digest).

U3 (3.9, Rule 6 fail closed):
- при пустом ``SERVER_AUTH_SECRET`` чтение остаётся открытым (как в U1),
  а ВСЕ мутирующие запросы (POST/PUT/DELETE/PATCH) — ``403`` «мутирующие
  операции требуют SERVER_AUTH_SECRET»: удаление заданий и запись промптов
  не должны быть доступны без аутентификации только потому, что стенд
  стоит в tailnet;
- при заданном секрете каждый мутирующий запрос обязан нести CSRF-токен,
  привязанный к сессии Basic-auth (паре «секрет + имя пользователя»):
  бессостоятельный ``HMAC(secret, «u3-csrf|<username>»)`` — без серверного
  стора сессий. Токен выдаётся на страницах (скрытое поле форм ``u3_csrf``
  и meta ``u3-csrf``), принимается в заголовке ``X-U3-CSRF``, поле формы
  ``u3_csrf`` или JSON-поле ``csrf``. Отсутствие/несоответствие — ``403``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from fastapi import Request
from starlette.responses import JSONResponse

from app.core.config import Settings
from app.web.strings import UI_STRINGS

_WWW_AUTHENTICATE = 'Basic realm="epubtranslator"'
#: Методы, изменяющие состояние (U3 3.9.40).
MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "DELETE", "PATCH"})
#: Заголовок CSRF для fetch-запросов (app.js).
CSRF_HEADER = "X-U3-CSRF"
#: Имя скрытого поля токена в формах.
CSRF_FORM_FIELD = "u3_csrf"
#: Имя поля токена в JSON-теле.
CSRF_JSON_FIELD = "csrf"


def _basic_parts(header: str | None) -> tuple[str, str] | None:
    """(username, password) из Basic-заголовка; None — заголовок некорректен."""
    if not header or not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header.removeprefix("Basic ").strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    username, _sep, password = decoded.rpartition(":")
    return username, password


def _authorized(settings: Settings, header: str | None) -> tuple[bool, str]:
    """(разрешён ли запрос, имя пользователя). Секрет — пароль (U1)."""
    secret = settings.server.auth_secret
    parts = _basic_parts(header)
    if not secret:
        # Без секрета аутентификация не требуется (чтение открыто, U1).
        return True, parts[0] if parts is not None else ""
    if parts is None:
        return False, ""
    username, password = parts
    return hmac.compare_digest(password.encode("utf-8"), secret.encode("utf-8")), username


def csrf_token(settings: Settings, username: str) -> str:
    """CSRF-токен сессии Basic-auth: HMAC(secret, «u3-csrf|<username>»).

    Пустой секрет — пустой токен (мутирующие запросы без секрета получают
    403 до проверки CSRF).
    """
    secret = settings.server.auth_secret
    if not secret:
        return ""
    message = f"u3-csrf|{username}".encode()
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()[:32]


async def _request_csrf(request: Request) -> str:
    """Токен из запроса: заголовок → поле формы → JSON-поле."""
    token = request.headers.get(CSRF_HEADER, "")
    if token:
        return token
    content_type = request.headers.get("content-type", "")
    try:
        if "application/x-www-form-urlencoded" in content_type:
            form = await request.form()
            return str(form.get(CSRF_FORM_FIELD, ""))
        if "application/json" in content_type:
            data = await request.json()
            if isinstance(data, dict):
                return str(data.get(CSRF_JSON_FIELD, ""))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return ""
    return ""


def _denied(message: str, status_code: int, *, www_authenticate: bool = False) -> JSONResponse:
    headers = {"WWW-Authenticate": _WWW_AUTHENTICATE} if www_authenticate else {}
    return JSONResponse(
        {
            "error": "unauthorized" if status_code == 401 else "forbidden",
            "message": message,
        },
        status_code=status_code,
        headers=headers,
    )


async def auth_check(settings: Settings, request: Request) -> JSONResponse | None:
    """Middleware-проверка: None — пропустить, JSONResponse — отклонить.

    Порядок (fail closed): 401 (секрет задан, но нет/неверен Basic) →
    403 (мутация при пустом секрете) → 403 (мутация без действительного
    CSRF-токена при заданном секрете). Чтение (GET/HEAD/OPTIONS/…) —
    как в U1.
    """
    authorized, username = _authorized(settings, request.headers.get("Authorization"))
    if not authorized:
        return _denied(UI_STRINGS["auth_required_message"], 401, www_authenticate=True)
    mutating = request.method in MUTATING_METHODS
    if mutating and not settings.server.auth_secret:
        return _denied(UI_STRINGS["err_mutation_requires_secret"], 403)
    if mutating:
        expected = csrf_token(settings, username)
        provided = await _request_csrf(request)
        if not expected or not hmac.compare_digest(provided, expected):
            return _denied(UI_STRINGS["err_csrf"], 403)
    # Для страниц: имя пользователя и токен сессии (скрытое поле форм u3_csrf,
    # meta u3-csrf, заголовок X-U3-CSRF в app.js).
    request.state.username = username
    request.state.csrf_token = csrf_token(settings, username)
    return None
