"""Доступ U1 + безопасность мутирующих операций U3.

Правила (ТЗ U1.10): привязка к Tailscale-адресу — через ``server.bind_address``
из конфигурации (load_settings fail closed); дополнительно — общий секрет.
Полноценной системы пользователей нет.

Выбор Basic (а не cookie с секретом): секрет существует только в
заголовке запроса — он не попадает ни в HTML-ответы, ни в URL, ни в
access-лог (критерии U1-7/U1-8). Имя пользователя любое (проверка —
только пароль), сравнение — константное время (hmac.compare_digest).

TЗ UI §7.1 (публичный доступ): 401 отдаётся страницей входа, а не
системным диалогом браузера. Для этого форма /login ставит cookie
``u3-token`` = HMAC(secret, «u3-ui-login») — бессостоянный токен,
пересчитываемый сервером; сам секрет в cookie не попадает. HTTP Basic
остаётся в силе для API-клиентов (curl -u, аудит).

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

WWW_AUTHENTICATE = 'Basic realm="epubtranslator"'
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


def login_token(settings: Settings) -> str:
    """Токен cookie-логина: HMAC(secret, «u3-ui-login») — бессостоянный
    (TЗ UI §7.1: 401 отдаётся страницей входа, а не системным диалогом
    браузера; секрет в cookie не попадает — токен пересчитывается на
    сервере). Однопользовательская модель: токен не зависит от пароля,
    потому что пароль один для всех."""
    secret = settings.server.auth_secret
    if not secret:
        return ""
    return hmac.new(secret.encode("utf-8"), b"u3-ui-login", hashlib.sha256).hexdigest()[:32]


#: Имя cookie сессионного логина (TЗ UI §7.1).
LOGIN_COOKIE = "u3-token"


def _authorized(
    settings: Settings, header: str | None, cookie_value: str
) -> tuple[bool, str, str]:
    """(разрешён ли запрос, имя пользователя, причина отказа).

    Причины: "ok" — разрешён; "wrong" — креденшелы были, но неверны;
    "none" — креденшелов нет вовсе (первый визит). Секрет — пароль
    Basic (U1) либо токен cookie после формы /login (TЗ UI §7.1).
    """
    secret = settings.server.auth_secret
    parts = _basic_parts(header)
    if not secret:
        # Без секрета аутентификация не требуется (чтение открыто, U1).
        return True, parts[0] if parts is not None else "", "ok"
    if parts is not None:
        username, password = parts
        if hmac.compare_digest(password.encode("utf-8"), secret.encode("utf-8")):
            return True, username, "ok"
        return False, "", "wrong"
    token = login_token(settings)
    if token and hmac.compare_digest(cookie_value or "", token):
        return True, "admin", "ok"
    return False, "", "none"


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


class Denial:
    """Отказ middleware (TЗ UI §7.1: 401/403 отдаются либо JSON — для
    API/fetch, либо страницей в общем оформлении — для навигации браузера;
    выбор делает middleware по заголовкам запроса)."""

    __slots__ = ("status_code", "message", "www_authenticate", "reason")

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        www_authenticate: bool = False,
        reason: str = "none",
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.www_authenticate = www_authenticate
        self.reason = reason  # 401: "none" (нет креденшелов) | "wrong" (неверны)

    @property
    def is_unauthorized(self) -> bool:
        return self.status_code == 401

    def as_json(self) -> JSONResponse:
        """Ответ для API/fetch-клиентов — формат как в U3 (без изменений)."""
        headers = (
            {"WWW-Authenticate": WWW_AUTHENTICATE} if self.www_authenticate else {}
        )
        return JSONResponse(
            {
                "error": "unauthorized" if self.is_unauthorized else "forbidden",
                "message": self.message,
            },
            status_code=self.status_code,
            headers=headers,
        )


async def auth_check(settings: Settings, request: Request) -> Denial | None:
    """Middleware-проверка: None — пропустить, Denial — отклонить.

    Порядок (fail closed): 401 (секрет задан, но нет/неверны Basic или
    cookie-логин) → 403 (мутация при пустом секрете) → 403 (мутация без
    действительного CSRF-токена при заданном секрете). Чтение
    (GET/HEAD/OPTIONS/…) — как в U1.
    """
    authorized, username, reason = _authorized(
        settings,
        request.headers.get("Authorization"),
        request.cookies.get(LOGIN_COOKIE, ""),
    )
    if not authorized:
        return Denial(
            401,
            UI_STRINGS["auth_required_message"],
            www_authenticate=True,
            reason=reason,
        )
    mutating = request.method in MUTATING_METHODS
    if mutating and not settings.server.auth_secret:
        return Denial(403, UI_STRINGS["err_mutation_requires_secret"])
    if mutating:
        expected = csrf_token(settings, username)
        provided = await _request_csrf(request)
        if not expected or not hmac.compare_digest(provided, expected):
            return Denial(403, UI_STRINGS["err_csrf"])
    # Для страниц: имя пользователя и токен сессии (скрытое поле форм u3_csrf,
    # meta u3-csrf, заголовок X-U3-CSRF в app.js).
    request.state.username = username
    request.state.csrf_token = csrf_token(settings, username)
    return None
