"""LLM-настройки (R8, ТЗ §13): валидация, проба соединения, запись.

Единые точки для JS-эндпоинтов (POST /api/llm/probe, PUT /api/
config/llm, GET /api/llm/models) и no-JS-маршрутов страниц —
двух реализаций механизма нет (Rule 8).

Запись (Р8-решение 5): валидация ДО записи (fail-closed) → редактор
строк `app.core.config_edit` (комментарии сохраняются) → валидация
`load_settings` против временных копий → `os.replace` (.env, затем
config.yaml) → повторная загрузка `Settings` (веб-процесс подменяет
`app.state.settings`; воркер релоадит сам перед каждым заданием).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import PROFILE_NAMES, Settings, load_settings
from app.core.config_edit import apply_env_keys, apply_llm_profiles
from app.core.errors import AppError, ConfigurationError
from app.web.strings import UI_STRINGS

logger = logging.getLogger("app.web")

_S = UI_STRINGS

#: Таймаут UI-пробы, с на вызов (Р8-решение 3: ТЗ не даёт конфиг-ключа;
#: таймаут задания (300 с) для пробки из интерфейса не подходит).
PROBE_TIMEOUT_SECONDS = 15.0
#: Число токенов минимального chat-вызова (ТЗ §13: «на 8 токенов»).
PROBE_MAX_TOKENS = 8


def _now_human() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ валидация


def validate_base_url(url: str) -> str:
    """ТЗ §13: обязательно, схема http/https, путь до /v1.

    Возврат — url как есть; AppError — 400 на стороне API."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AppError(_S["err_llm_bad_base_url"].format(url=url))
    if parsed.path.rstrip("/") != "/v1":
        raise AppError(_S["err_llm_bad_base_url"].format(url=url))
    return url


def _validate_number(value: Any, min_value: int, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < min_value:
        raise AppError(_S[key])
    return value


def _validate_extra_body(value: Any) -> dict[str, Any] | None:
    """«»/None — сохранить текущее; str — JSON-объект; dict — как есть."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            parsed: Any = json.loads(value)
        except ValueError as exc:
            raise AppError(_S["err_llm_bad_extra_body"].format(reason=str(exc))) from exc
        value = parsed
    if not isinstance(value, dict):
        raise AppError(
            _S["err_llm_bad_extra_body"].format(reason="ожидается объект ({}…)")
        )
    return value


def _as_float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AppError(_S["err_llm_bad_temperature"])
    return float(value)


# ------------------------------------------------------------------ проба


def _probe_failure(message: str) -> dict[str, Any]:
    return {"ok": False, "message": message}


def _probe_response(
    url: str, headers: dict[str, str], json_body: dict[str, Any] | None
) -> httpx.Response | dict[str, Any]:
    """HTTP-вызов пробы; сетевая ошибка — сразу результат-отказ."""
    try:
        if json_body is None:
            return httpx.get(url, headers=headers, timeout=PROBE_TIMEOUT_SECONDS)
        return httpx.post(
            url, headers=headers, json=json_body, timeout=PROBE_TIMEOUT_SECONDS
        )
    except httpx.TimeoutException:
        return _probe_failure(_S["llm_probe_unreachable"].format(reason="таймаут"))
    except httpx.HTTPError as exc:
        return _probe_failure(
            _S["llm_probe_unreachable"].format(reason=type(exc).__name__)
        )


def _probe_status(status_code: int) -> dict[str, Any] | None:
    """Точная причина по коду ответа (ТЗ §13); None — код успешный."""
    if status_code == 401:
        return _probe_failure(_S["llm_probe_http_401"])
    if status_code == 404:
        return _probe_failure(_S["llm_probe_http_404"])
    if status_code >= 400:
        return _probe_failure(_S["llm_probe_http"].format(code=status_code))
    return None


def probe_request(
    body: dict[str, Any],
    settings: Settings,
) -> tuple[str, str, str, float, dict[str, Any]]:
    """ТЗ §13: тело POST /api/llm/probe → (base_url, model, api_key,
    temperature, extra_body). AppError — 400 на стороне API.

    Пустые api_key/extra_body = сохранённые для профиля (UI показывает
    ключ только как «задан»; extra_body «пусто — без изменений» —
    семантика та же, что у сохранения)."""
    name = str(body.get("profile") or "")
    if name not in PROFILE_NAMES:
        raise AppError(_S["err_llm_unknown_profile"].format(profile=name or "—"))
    base_url = str(body.get("base_url") or "")
    validate_base_url(base_url)
    model = str(body.get("model") or "")
    if not model.strip():
        raise AppError(_S["err_llm_bad_model"])
    temperature = _as_float(body.get("temperature"))
    if not 0.0 <= temperature <= 2.0:
        raise AppError(_S["err_llm_bad_temperature"])
    extra_body = _validate_extra_body(body.get("extra_body"))
    if extra_body is None:
        extra_body = settings.llm.profile(name).extra_body
    api_key = str(body.get("api_key") or "") or settings.llm.api_key_for(name) or ""
    return base_url, model, api_key, temperature, extra_body


def probe_profile(
    base_url: str,
    model: str,
    api_key: str,
    temperature: float,
    extra_body: dict[str, Any],
) -> dict[str, Any]:
    """ТЗ §13: GET {base_url}/models + минимальный POST chat/completions
    на 8 токенов. Не пишет конфиг, не создаёт задание.

    Возврат: ``{"ok": bool, "message": str, ...}`` — message = одна
    строка (успех: «Ответ N мс · модель X · N токенов», отказ — точная
    причина). URL-контракт chat — тот же, что `app.llm.analysis`
    (base_url + /chat/completions)."""
    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    models_response = _probe_response(f"{base}/models", headers, None)
    if not isinstance(models_response, httpx.Response):
        return models_response
    failure = _probe_status(models_response.status_code)
    if failure is not None:
        return failure

    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "OK"}],
        "max_tokens": PROBE_MAX_TOKENS,
        "temperature": temperature,
    }
    body.update(extra_body)
    start = time.monotonic()
    response = _probe_response(f"{base}/chat/completions", headers, body)
    if not isinstance(response, httpx.Response):
        return response
    elapsed_ms = round((time.monotonic() - start) * 1000)
    failure = _probe_status(response.status_code)
    if failure is not None:
        return failure
    try:
        data: Any = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return _probe_failure(_S["llm_probe_bad_json"])
    model_name = data.get("model") or model
    usage = data.get("usage")
    tokens = PROBE_MAX_TOKENS
    if isinstance(usage, dict) and isinstance(usage.get("completion_tokens"), int):
        tokens = usage["completion_tokens"]
    return {
        "ok": True,
        "message": _S["llm_probe_ok"].format(
            ms=elapsed_ms, model=model_name, tokens=tokens
        ),
        "elapsed_ms": elapsed_ms,
        "model": model_name,
        "tokens": tokens,
    }


def list_models(base_url: str, api_key: str) -> list[str]:
    """GET {base_url}/models — для datalist автодополнения (Р8-решение 4).

    AppError — причина для 400/502 на стороне API."""
    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = httpx.get(f"{base}/models", headers=headers, timeout=PROBE_TIMEOUT_SECONDS)
    except httpx.TimeoutException:
        raise AppError(
            _S["llm_probe_unreachable"].format(reason="таймаут")
        ) from None
    except httpx.HTTPError as exc:
        raise AppError(
            _S["llm_probe_unreachable"].format(reason=type(exc).__name__)
        ) from None
    if response.status_code >= 400:
        raise AppError(_S["llm_probe_http"].format(code=response.status_code))
    try:
        data: Any = response.json()
    except ValueError:
        raise AppError(_S["llm_probe_bad_json"]) from None
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise AppError(_S["llm_probe_bad_json"])
    return sorted(str(item.get("id")) for item in items if isinstance(item, dict))


# ------------------------------------------------------------------ запись


def _profile_changes(
    shared: bool,
    shared_url: str | None,
    profile_fields: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str | None]]:
    """Сверить тело запроса с конфигом → (изменения для config.yaml,
    ключи для .env). AppError — любое несоответствие (ничего не
    записано, Р8-решение 5а)."""
    if shared and (not isinstance(shared_url, str) or not shared_url.strip()):
        raise AppError(_S["err_llm_bad_base_url"].format(url=shared_url or "—"))
    changes: dict[str, dict[str, Any]] = {}
    env_keys: dict[str, str | None] = {}
    shared_keys: dict[str, str] = {}
    for name in PROFILE_NAMES:
        raw = profile_fields.get(name)
        if raw is None:
            raise AppError(_S["err_llm_unknown_profile"].format(profile=name))
        url = shared_url if shared else raw.get("base_url")
        if not isinstance(url, str) or not url.strip():
            raise AppError(_S["err_llm_bad_base_url"].format(url=url or "—"))
        validate_base_url(url)
        model = raw.get("model")
        if not isinstance(model, str) or not model.strip():
            raise AppError(_S["err_llm_bad_model"])
        temperature = _as_float(raw.get("temperature"))
        if not 0.0 <= temperature <= 2.0:
            raise AppError(_S["err_llm_bad_temperature"])
        retry_times = _validate_number(raw.get("retry_times"), 0, "err_llm_bad_retries")
        concurrency = _validate_number(
            raw.get("concurrency"), 1, "err_llm_bad_concurrency"
        )
        extra_body = _validate_extra_body(raw.get("extra_body"))
        changes[name] = {
            "base_url": url,
            "model": model,
            "temperature": temperature,
            "retry_times": retry_times,
            "concurrency": concurrency,
            "extra_body": extra_body,
        }
        key = str(raw.get("api_key") or "")
        if name == "analysis":
            env_keys["LLM_ANALYSIS_API_KEY"] = key or None
        else:
            shared_keys[name] = key
    translation_key = shared_keys.get("translation", "")
    fill_key = shared_keys.get("fill", "")
    if translation_key and fill_key and translation_key != fill_key:
        raise AppError(_S["err_llm_key_conflict"])
    env_keys["LLM_API_KEY"] = (translation_key or fill_key) or None
    return changes, env_keys


def _load_error_reason(exc: ConfigurationError) -> str:
    """Текст причины: первый непустой список в details
    (missing/problems/errors), иначе message."""
    for value in (exc.details or {}).values():
        if isinstance(value, (list, tuple)) and value:
            return "; ".join(str(item) for item in value)
    return exc.message


def update_llm_config(
    *,
    shared: bool,
    shared_url: str | None,
    profile_fields: dict[str, dict[str, Any]],
    config_path: Path,
    env_path: Path,
) -> Settings:
    """Записать профили в config.yaml и ключи в .env (Р8-решение 5).

    Возврат — НОВЫЕ `Settings` (веб-эндпоинт подменяет `app.state.
    settings`). AppError — валидация не пройдена (файлы не тронуты)."""
    changes, env_keys = _profile_changes(shared, shared_url, profile_fields)
    cfg_text = config_path.read_text(encoding="utf-8")
    env_text = env_path.read_text(encoding="utf-8")
    new_cfg = apply_llm_profiles(cfg_text, changes)
    new_env = apply_env_keys(env_text, env_keys)

    # Валидация ОБОИХ новых текстов ДО записи (fail-closed).
    cfg_tmp = config_path.with_name(f".u3-cfg-{os.getpid()}.partial")
    env_tmp = env_path.with_name(f".u3-env-{os.getpid()}.partial")
    try:
        cfg_tmp.write_text(new_cfg, encoding="utf-8")
        env_tmp.write_text(new_env, encoding="utf-8")
        try:
            load_settings(cfg_tmp, env_tmp)
        except ConfigurationError as exc:
            raise AppError(
                _S["err_llm_config_invalid"].format(reason=_load_error_reason(exc))
            ) from exc
        # .env — ПЕРВЫМ: окно ошибки между заменами = новый ключ без
        # новой модели (профиль работает на старом адресе), повтор
        # сохранения идемпотентен.
        env_tmp.replace(env_path)
        cfg_tmp.replace(config_path)
    except OSError as exc:
        raise AppError(
            _S["err_llm_config_invalid"].format(reason=f"запись: {exc}")
        ) from exc
    finally:
        cfg_tmp.unlink(missing_ok=True)
        env_tmp.unlink(missing_ok=True)
    return load_settings(config_path, env_path)


# ------------------------------------------------------------------ логи


def clear_app_log(settings: Settings, user: str) -> int:
    """Очистка `logs/app.log` (Р8-решение 8б): тот же механизм, что
    per-job-очистка (U3 3.5.25-26): truncate(0) + строка факта."""
    path = settings.logs_dir / "app.log"
    size = path.stat().st_size if path.is_file() else 0
    if path.is_file():
        with path.open("r+b") as handle:
            handle.truncate(0)
    fact = _S["logs_cleared_fact_global"].format(
        time=_now_human(), user=user, bytes=size
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(fact + "\n")
    logger.info("logs_clear_global: user=%s bytes=%d", user, size)
    return size
