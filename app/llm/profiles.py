"""Показ LLM-профилей (E4.3): провайдер, эндпоинт, модель, профиль — по каждому.

Показывается CLI перед стартом задания (app.workers.run) и доступна UI (U1).
Значения секретов НЕ показываются — только факт наличия.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.core.config import PROFILE_NAMES, Settings


def describe_profiles(settings: Settings) -> str:
    """Текст для показа: провайдер, эндпоинт, модель, профиль — по всем трём (E4.3)."""
    lines: list[str] = ["LLM profiles (E4.3):"]
    for name in PROFILE_NAMES:
        profile = settings.llm.profile(name)
        host = urlparse(profile.base_url).netloc or "(пусто)"
        key_state = "задан" if settings.llm.api_key_for(name) else "не задан"
        lines.append(f"  profile: {name}")
        lines.append(f"    provider: openai-compatible (host: {host})")
        lines.append(f"    endpoint: {profile.base_url}")
        lines.append(f"    model: {profile.model}")
        lines.append(f"    api_key: {key_state}")
    return "\n".join(lines)
