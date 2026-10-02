# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""LLM-настройки (R8, ТЗ §13): валидация, проба соединения, запись.

Единые точки для JS-эндпоинтов (POST /api/llm/probe, GET /api/llm/models)
и форм страниц (POST /settings/llm — сохранение, /settings/llm/probe —
проба) — двух реализаций механизма нет (Rule 8; M2: API-эндпоинт записи
PUT /api/config/llm удалён, D122).

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

from app.core.config import ALL_PROFILE_NAMES, Settings, load_settings
from app.core.config_edit import (
    apply_env_keys,
    apply_llm_profiles,
    apply_translation_pipeline,
    apply_ui_main_language,
)
from app.core.errors import AppError, ConfigurationError, SettingsIncompleteError
from app.core.llm_settings import validate_llm_settings
from app.domain.job import WORKING_STATES, Job, JobStatus
from app.languages.registry import load_registry
from app.llm.prompts import _best_version, _parse_prompt_file
from app.storage.jobs_repo import JobRepository
from app.web.strings import UI_STRINGS

logger = logging.getLogger("app.web")

_S = UI_STRINGS

#: Таймаут UI-пробы, с на вызов (Р8-решение 3: ТЗ не даёт конфиг-ключа;
#: таймаут задания (300 с) для пробки из интерфейса не подходит).
PROBE_TIMEOUT_SECONDS = 15.0
#: Число токенов минимального chat-вызова (ТЗ §13: «на 8 токенов»).
PROBE_MAX_TOKENS = 8
#: Число токенов пробы mt-модели (MTPE §6.5: «16 токенов»).
PROBE_MT_MAX_TOKENS = 16


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


def _parse_stop(value: Any) -> list[str]:
    """Поле `stop` карточки: список или строка (по строке/пустой строке)."""
    if isinstance(value, list):
        return [str(item) for item in value if str(item) != ""]
    if isinstance(value, str) and value.strip():
        return [line for line in value.split("\n") if line != ""]
    return []


def _mt_probe_prompt(settings: Settings) -> str:
    """Проба mt (§6.5): активный user_template mt_draft, «Good morning.»,
    en → ru. Плейсхолдеры — mt_name из пакетов (MiLMMT-имена)."""
    src_name, tgt_name = "en", "ru"
    try:
        registry = load_registry(settings.languages_root)
        src_name = registry["en"].mt_name or "en"
        tgt_name = registry["ru"].mt_name or "ru"
    except ConfigurationError:
        pass
    template = "Translate this from {src_name} to {tgt_name}:\n{src_name}: {text}\n{tgt_name}:"
    version_file = _best_version(settings.prompts_dir / "mt_draft")
    if version_file is not None:
        try:
            _version, _system, user_template = _parse_prompt_file(version_file)
            template = user_template
        except ConfigurationError:
            pass
    return (
        template.replace("{src_name}", src_name)
        .replace("{tgt_name}", tgt_name)
        .replace("{text}", "Good morning.")
    )


def probe_request(
    body: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    """MTPE §6.5: тело POST /api/llm/probe → данные пробы. AppError — 400.

    Пустые api_key/extra_body = сохранённые для профиля (UI показывает ключ
    только как «задан»). Для mt параметры top_k/stop — из карточки
    (несохранённые), иначе из сохранённого профиля; `prompt` — активный
    mt_draft (§6.5: «Good morning.», en → ru)."""
    name = str(body.get("profile") or "")
    if name not in ALL_PROFILE_NAMES:
        raise AppError(_S["err_llm_unknown_profile"].format(profile=name or "—"))
    base_url = str(body.get("base_url") or "")
    validate_base_url(base_url)
    model = str(body.get("model") or "")
    if not model.strip():
        raise AppError(_S["err_llm_bad_model"])
    temperature = _as_float(body.get("temperature"))
    if not 0.0 <= temperature <= 2.0:
        raise AppError(_S["err_llm_bad_temperature"])
    profile = settings.llm.profile(name)
    extra_body = _validate_extra_body(body.get("extra_body"))
    if extra_body is None:
        extra_body = profile.extra_body
    api_key = str(body.get("api_key") or "") or settings.llm.api_key_for(name) or ""
    top_k = profile.top_k
    if body.get("top_k") not in (None, ""):
        top_k = int(_as_float(body.get("top_k")))
    stop = _parse_stop(body.get("stop")) or list(profile.stop)
    data: dict[str, Any] = {
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "temperature": temperature,
        "extra_body": extra_body,
        "api": profile.api,
        "top_k": top_k,
        "stop": stop,
    }
    if profile.api == "completions":
        data["prompt"] = _mt_probe_prompt(settings)
    return data


def _model_ids(response: httpx.Response) -> list[str] | None:
    """Имена моделей из GET /models; None — ответ не-JSON или без списка."""
    try:
        data: Any = response.json()
    except ValueError:
        return None
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    return [str(item.get("id")) for item in items if isinstance(item, dict)]


def _chat_body(data: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": data["model"],
        "messages": [{"role": "user", "content": "OK"}],
        "max_tokens": PROBE_MAX_TOKENS,
        "temperature": data["temperature"],
    }
    body.update(data["extra_body"])
    return body


def _completions_body(data: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": data["model"],
        "prompt": data["prompt"],
        "max_tokens": PROBE_MT_MAX_TOKENS,
        "temperature": data["temperature"],
    }
    if data.get("top_k") is not None:
        body["top_k"] = data["top_k"]
    if data.get("stop"):
        body["stop"] = data["stop"]
    return body


def probe_profile(data: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0911, PLR0912
    """MTPE §6.5: проба профиля — 3 шага. Не пишет конфиг, не создаёт задание.

    1) GET {base}/models — модель есть в списке; 2) POST chat/completions
    (chat, 8 токенов) или /completions (mt, активный mt_draft, 16 токенов);
    3) model ответа = модели профиля. Возврат ``{"ok", "message", ...}`` —
    одна строка (успех/точная причина отказа); для mt + `response_text`."""
    base = data["base_url"].rstrip("/")
    model = data["model"]
    headers = {"Authorization": f"Bearer {data['api_key']}"} if data["api_key"] else {}
    # Шаг 1: модель в списке эндпоинта.
    models_response = _probe_response(f"{base}/models", headers, None)
    if not isinstance(models_response, httpx.Response):
        return models_response
    failure = _probe_status(models_response.status_code)
    if failure is not None:
        return failure
    listed = _model_ids(models_response)
    if listed is None:
        return _probe_failure(_S["llm_probe_bad_json"])
    if model not in listed:
        return _probe_failure(_S["llm_probe_model_not_listed"])
    # Шаг 2: минимальный генеративный вызов.
    is_mt = data["api"] == "completions"
    request_url = f"{base}/completions" if is_mt else f"{base}/chat/completions"
    body = _completions_body(data) if is_mt else _chat_body(data)
    start = time.monotonic()
    response = _probe_response(request_url, headers, body)
    if not isinstance(response, httpx.Response):
        return response
    elapsed_ms = round((time.monotonic() - start) * 1000)
    failure = _probe_status(response.status_code)
    if failure is not None:
        return failure
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return _probe_failure(_S["llm_probe_bad_json"])
    # Шаг 3: модель ответа = модели профиля.
    response_model = payload.get("model")
    if response_model and response_model != model:
        return _probe_failure(_S["llm_probe_model_mismatch"])
    model_name = response_model or model
    response_text = ""
    if is_mt:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            response_text = str(choices[0].get("text") or "")
    expected = PROBE_MT_MAX_TOKENS if is_mt else PROBE_MAX_TOKENS
    tokens = expected
    usage = payload.get("usage")
    if isinstance(usage, dict) and isinstance(usage.get("completion_tokens"), int):
        tokens = usage["completion_tokens"]
    result: dict[str, Any] = {
        "ok": True,
        "message": _S["llm_probe_ok"].format(ms=elapsed_ms, model=model_name, tokens=tokens),
        "elapsed_ms": elapsed_ms,
        "model": model_name,
        "tokens": tokens,
    }
    if is_mt and response_text:
        result["response_text"] = response_text
    return result


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


def _stage_profile(job: Job) -> str | None:
    """Профиль текущей LLM-стадии задания; None — LLM-стадии сейчас нет.

    MTPE M6 (D135): лексика фаз = `m_draft`/`m_postedit`/`m_assemble` (M1).
    `m_assemble` — сборка без LLM → None (409 не даёт)."""
    if job.status is JobStatus.ANALYZING:
        return "analysis"
    if job.status is JobStatus.TRANSLATING:
        if job.pipeline == "mt_pe":
            if job.phase == "m_draft":
                return "mt"
            if job.phase == "m_postedit":
                return "postedit"
            return None  # m_assemble — не LLM-стадия
        return "translation"
    return None


def _stage_label(job: Job, profile_name: str) -> str:
    """Имя фазы/стадии для текста 409: mt_pe — jobs.phase, single — по профилю."""
    if job.phase:
        return job.phase
    return {
        "translation": _S["llm_stage_translation"],
        "analysis": _S["llm_stage_analysis"],
    }.get(profile_name, profile_name)


def _snapshot_endpoint(snapshot_json: str, profile_name: str) -> tuple[str, str]:
    """(base_url, model) профиля из снимка задания (§4.5); ('', '') — нет."""
    try:
        data: Any = json.loads(snapshot_json)
    except ValueError:
        return "", ""
    profiles = data.get("profiles") if isinstance(data, dict) else None
    prof = profiles.get(profile_name) if isinstance(profiles, dict) else None
    if not isinstance(prof, dict):
        return "", ""
    return str(prof.get("base_url") or ""), str(prof.get("model") or "")


def probe_conflict(repo: JobRepository, probe_base_url: str, probe_model: str) -> str | None:
    """MTPE §6.5: 409 — задание в LLM-стадии на этом эндпоинте с другой моделью.

    Возврат — текст 409 или None. Критерий: рабочее задание, base_url его
    текущей LLM-стадии (из снимка) == base_url пробы и модель != модели пробы."""
    base = probe_base_url.rstrip("/")
    working = tuple(s.value for s in WORKING_STATES)
    for job in repo.list_jobs(statuses=working):
        profile_name = _stage_profile(job)
        if profile_name is None:
            continue
        snap_base, snap_model = _snapshot_endpoint(job.config_snapshot, profile_name)
        if (
            snap_base
            and snap_base.rstrip("/") == base
            and snap_model
            and snap_model != probe_model
        ):
            return _S["llm_probe_409"].format(
                phase=_stage_label(job, profile_name), job_id=job.id
            )
    return None


# ------------------------------------------------------------------ запись


#: chat-профили (extra_body — только у них; §6.2).
_CHAT_PROFILES = frozenset({"translation", "fill", "analysis", "postedit"})
#: Профиль → переменная .env его ключа (translation/fill — общий LLM_API_KEY).
_KEY_ENV = {
    "translation": "LLM_API_KEY",
    "fill": "LLM_API_KEY",
    "analysis": "LLM_ANALYSIS_API_KEY",
    "mt": "LLM_MT_API_KEY",
    "postedit": "LLM_POSTEDIT_API_KEY",
}


def _profile_changes(  # noqa: PLR0912, PLR0915 — поле-проверка §6.2, каждое — своя граница
    profile_fields: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str | None]]:
    """MTPE §6.2: тело сохранения → (изменения config.yaml, ключи .env).

    base_url/model обязательны; остальные поля — по наличию в теле
    (отсутствует — сохранить текущее, `None` в `apply_llm_profiles`).
    mt: top_k/max_tokens_ratio/max_segment_tokens/stop. Ключ: пустое поле —
    без изменений; «Удалить ключ» — пустая строка (фолбэк на общий ключ).
    AppError — несоответствие (ничего не записано)."""
    changes: dict[str, dict[str, Any]] = {}
    env_keys: dict[str, str | None] = {}
    shared_values: list[str] = []
    shared_delete = False
    # Fail-closed: неизвестное имя профиля в теле — 400 (не молча игнорировать).
    for name in profile_fields:
        if name not in ALL_PROFILE_NAMES:
            raise AppError(_S["err_llm_unknown_profile"].format(profile=name))
    for name in ALL_PROFILE_NAMES:
        raw = profile_fields.get(name)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise AppError(_S["err_llm_unknown_profile"].format(profile=name))
        url = raw.get("base_url")
        if not isinstance(url, str) or not url.strip():
            # Пустой base_url — профиль не менять (M2-1: неполнота для способа
            # ловится в validate_llm_settings → 422, а не 400 здесь).
            continue
        validate_base_url(url)
        model = raw.get("model")
        if not isinstance(model, str) or not model.strip():
            raise AppError(_S["err_llm_bad_model"])
        entry: dict[str, Any] = {"base_url": url, "model": model}
        if raw.get("temperature") is not None:
            temperature = _as_float(raw["temperature"])
            if not 0.0 <= temperature <= 2.0:
                raise AppError(_S["err_llm_bad_temperature"])
            entry["temperature"] = temperature
        if raw.get("retry_times") is not None:
            entry["retry_times"] = _validate_number(
                raw["retry_times"], 0, "err_llm_bad_retries"
            )
        if raw.get("concurrency") is not None:
            entry["concurrency"] = _validate_number(
                raw["concurrency"], 1, "err_llm_bad_concurrency"
            )
        if raw.get("timeout_seconds") is not None:
            timeout_seconds = _as_float(raw["timeout_seconds"])
            if timeout_seconds <= 0:
                raise AppError(_S["err_llm_bad_timeout"])
            entry["timeout_seconds"] = timeout_seconds
        if raw.get("warmup_timeout_s") is not None:
            warmup = _as_float(raw["warmup_timeout_s"])
            if warmup <= 0:
                raise AppError(_S["err_llm_bad_timeout"])
            entry["warmup_timeout_s"] = warmup
        if name in _CHAT_PROFILES:
            extra_body = _validate_extra_body(raw.get("extra_body"))
            if extra_body is not None:
                entry["extra_body"] = extra_body
        if name == "mt":
            if raw.get("top_k") is not None:
                entry["top_k"] = _validate_number(raw["top_k"], 1, "err_llm_bad_top_k")
            if raw.get("max_tokens_ratio") is not None:
                ratio = _as_float(raw["max_tokens_ratio"])
                if ratio < 0:
                    raise AppError(_S["err_llm_bad_max_tokens_ratio"])
                entry["max_tokens_ratio"] = ratio
            if raw.get("max_segment_tokens") is not None:
                entry["max_segment_tokens"] = _validate_number(
                    raw["max_segment_tokens"], 1, "err_llm_bad_max_segment_tokens"
                )
            if raw.get("stop") is not None:
                entry["stop"] = _parse_stop(raw["stop"])
        changes[name] = entry
        key = str(raw.get("api_key") or "")
        delete_key = bool(raw.get("delete_key"))
        if name in ("translation", "fill"):
            if delete_key:
                shared_delete = True
            elif key:
                shared_values.append(key)
        else:
            env_var = _KEY_ENV[name]
            if delete_key:
                env_keys[env_var] = ""
            elif key:
                env_keys[env_var] = key
    if shared_delete:
        env_keys["LLM_API_KEY"] = ""
    elif shared_values:
        if len(set(shared_values)) > 1:
            raise AppError(_S["err_llm_key_conflict"])
        env_keys["LLM_API_KEY"] = shared_values[0]
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
    pipeline: str | None,
    profile_fields: dict[str, dict[str, Any]],
    config_path: Path,
    env_path: Path,
) -> Settings:
    """MTPE §6.4: запись способа (translation.pipeline) + профилей
    (config.yaml) + ключей (.env). Атомарно, fail-closed.

    Возврат — НОВЫЕ `Settings` (веб-эндпоинт подменяет `app.state.
    settings`). AppError — валидация не пройдена (файлы не тронуты).
    `pipeline` — single | mt_pe; None/пусто — не менять (R8-совместимо)."""
    if pipeline not in (None, "", "single", "mt_pe"):
        raise AppError(_S["err_llm_bad_pipeline"])
    changes, env_keys = _profile_changes(profile_fields)
    cfg_text = config_path.read_text(encoding="utf-8")
    env_text = env_path.read_text(encoding="utf-8")
    new_cfg = cfg_text
    if pipeline in ("single", "mt_pe"):
        try:
            new_cfg = apply_translation_pipeline(new_cfg, pipeline)
        except ValueError as exc:
            raise AppError(_S["err_llm_config_invalid"].format(reason=str(exc))) from exc
    new_cfg = apply_llm_profiles(new_cfg, changes)
    new_env = apply_env_keys(env_text, env_keys)

    # Валидация ОБОИХ новых текстов ДО записи (fail-closed).
    cfg_tmp = config_path.with_name(f".u3-cfg-{os.getpid()}.partial")
    env_tmp = env_path.with_name(f".u3-env-{os.getpid()}.partial")
    try:
        cfg_tmp.write_text(new_cfg, encoding="utf-8")
        env_tmp.write_text(new_env, encoding="utf-8")
        try:
            loaded = load_settings(cfg_tmp, env_tmp)
        except ConfigurationError as exc:
            raise AppError(
                _S["err_llm_config_invalid"].format(reason=_load_error_reason(exc))
            ) from exc
        # MTPE TZ §5.3 (точка 1): единая проверка настроек — ДО записи.
        # Неполные для способа (pipeline) → отказ, файлы не тронуты (M1-3).
        issues = validate_llm_settings(loaded)
        if issues:
            raise SettingsIncompleteError(
                "настройки LLM неполны для способа задания",
                details={"issues": [(issue.path, issue.code) for issue in issues]},
            )
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


def set_main_language(*, config_path: Path, env_path: Path, code: str) -> Settings:
    """Запись основного языка пользователя (ui.main_language). fail-closed.

    ``code`` — код языка из реестра либо "" (сброс). Возврат — НОВЫЕ
    `Settings` (веб-эндпоинт подменяет `app.state.settings`). AppError —
    неизвестный код / невалидный конфиг (файлы не тронуты)."""
    current = load_settings(config_path, env_path)
    if code:
        registry = load_registry(current.languages_root)
        if code not in registry:
            raise AppError(
                _S["err_llm_config_invalid"].format(reason=f"неизвестный язык: {code}")
            )
    cfg_text = config_path.read_text(encoding="utf-8")
    try:
        new_cfg = apply_ui_main_language(cfg_text, code)
    except ValueError as exc:
        raise AppError(_S["err_llm_config_invalid"].format(reason=str(exc))) from exc
    cfg_tmp = config_path.with_name(f".u3-cfg-{os.getpid()}.partial")
    try:
        cfg_tmp.write_text(new_cfg, encoding="utf-8")
        try:
            load_settings(cfg_tmp, env_path)
        except ConfigurationError as exc:
            raise AppError(
                _S["err_llm_config_invalid"].format(reason=_load_error_reason(exc))
            ) from exc
        cfg_tmp.replace(config_path)
    except OSError as exc:
        raise AppError(
            _S["err_llm_config_invalid"].format(reason=f"запись: {exc}")
        ) from exc
    finally:
        cfg_tmp.unlink(missing_ok=True)
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
