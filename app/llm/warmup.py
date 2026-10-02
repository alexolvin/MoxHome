# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Прогрев LLM-стадий (MTPE §4.6): один запрос в профиль ДО первой рабочей
LLM-операции стадии/фазы. «Свой клиент» воркера (не движок): прокси держит
запрос на время загрузки модели, а HTTP-таймауты рабочих запросов короче —
прогрев завершает загрузку до рабочих вызовов. Результат на выход не влияет.

Отказ прогрева — `LLMWarmupError` с кодом причины (`warmup_timeout` /
`model_mismatch` / `connection_error` / `http` / `invalid_json`): задание
уходит в `FAILED` штатным механизмом воркера (M6-4).

chat-профили (translation/fill/analysis/postedit): `POST {base}/chat/completions`,
одно сообщение `ping`, `max_tokens: 1`, + `extra_body` профиля. `completions`
(профиль mt): `POST {base}/completions`, шаблон `mt_draft` с текстом `ping`,
`max_tokens: 1`. Таймаут — `profile.warmup_timeout_s`; поле `model` ответа
обязано совпасть с моделью профиля.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import LLMUnavailableError
from app.languages.registry import load_registry
from app.llm.prompts import PromptResolver


class LLMWarmupError(LLMUnavailableError):
    """Отказ прогрева LLM-стадии (MTPE §4.6). `message` = код причины."""

    exit_code = 4


def warmup_profile(
    settings: Settings,
    profile_name: str,
    *,
    source_lang: str | None = None,
    target_lang: str | None = None,
) -> None:
    """Прогревочный запрос в профиль `profile_name` (§4.6).

    Для `completions`-профиля (mt) нужны имена языков, чтобы заполнить шаблон
    `mt_draft`; для chat-профилей они не нужны. Отказ — `LLMWarmupError`
    (причина + `profile` в `details`).
    """
    profile = settings.llm.profile(profile_name)
    base_url, model, api_key = settings.llm.endpoint(profile_name)
    if profile.api == "completions":
        body: dict[str, Any] = {
            "model": model,
            "prompt": _completions_ping(settings, source_lang, target_lang, profile),
            "max_tokens": 1,
            "temperature": 0,
            "stream": False,
        }
        url = f"{base_url.rstrip('/')}/completions"
    else:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "temperature": 0,
            "stream": False,
        }
        body.update(profile.extra_body or {})
        url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with httpx.Client(timeout=profile.warmup_timeout_s) as client:
            response = client.post(url, json=body, headers=headers)
    except httpx.TimeoutException as exc:
        raise LLMWarmupError(
            "warmup_timeout",
            details={"profile": profile_name, "timeout_s": profile.warmup_timeout_s},
        ) from exc
    except httpx.HTTPError as exc:
        raise LLMWarmupError(
            "connection_error",
            details={"profile": profile_name, "error": f"{type(exc).__name__}: {exc}"},
        ) from exc
    if response.status_code != 200:
        raise LLMWarmupError(
            "http", details={"profile": profile_name, "status": response.status_code}
        )
    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise LLMWarmupError("invalid_json", details={"profile": profile_name}) from exc
    resp_model = payload.get("model") if isinstance(payload, dict) else None
    if resp_model and resp_model != model:
        raise LLMWarmupError(
            "model_mismatch",
            details={"profile": profile_name, "expected": model, "actual": resp_model},
        )


def _completions_ping(
    settings: Settings,
    source_lang: str | None,
    target_lang: str | None,
    profile: Any,
) -> str:
    """Шаблон `mt_draft` с текстом `ping` (то же, что `DraftPhase` в прогоне)."""
    registry = load_registry(Path(settings.languages.root))
    src_pack = registry.get((source_lang or "").strip().lower())
    tgt_pack = registry.get((target_lang or "").strip().lower())
    src_name = src_pack.mt_name if src_pack is not None else (source_lang or "")
    tgt_name = tgt_pack.mt_name if tgt_pack is not None else (target_lang or "")
    prompt = PromptResolver(settings.prompts_dir).resolve(
        profile.prompt_task or "mt_draft",
        source_lang or "",
        target_lang or "",
        pair_required=False,
    )
    return prompt.user_template.format(src_name=src_name, tgt_name=tgt_name, text="ping")
