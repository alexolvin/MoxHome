# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Стоимость (E4.7): показывается ТОЛЬКО при заданной в конфигурации цене.

Выдуманная стоимость запрещена: без обеих цен профиля (вход и выход) функция
возвращает ровно "Cost: unknown" — и нечего больше.
"""

from __future__ import annotations

from app.core.config import PROFILE_NAMES, LLMProfile, Settings
from app.domain.job import Job
from app.storage.jobs_repo import JobRepository


def format_cost(profile: LLMProfile, input_tokens: int, output_tokens: int) -> str:
    """Стоимость токенов профиля. Без цен в конфигурации — "Cost: unknown" (E4-6)."""
    if not profile.cost_defined:
        return "Cost: unknown"
    cost = (
        input_tokens / 1_000_000 * (profile.input_price_per_mtok or 0.0)
        + output_tokens / 1_000_000 * (profile.output_price_per_mtok or 0.0)
    )
    return (
        f"Cost: {cost:.4f} USD "
        f"(input {profile.input_price_per_mtok:.2f}/Mtok, "
        f"output {profile.output_price_per_mtok:.2f}/Mtok)"
    )


def job_cost_summary(settings: Settings, repo: JobRepository, job: Job) -> str:
    """Строка(и) стоимости по профилям задания — для отчёта и UI (E4.6/E4-6)."""
    breakdown = repo.profile_tokens(job.id)
    lines: list[str] = []
    for name in PROFILE_NAMES:
        profile = settings.llm.profile(name)
        tokens = breakdown.get(name)
        input_tokens = tokens.input_tokens if tokens is not None else 0
        output_tokens = tokens.output_tokens if tokens is not None else 0
        lines.append(f"{name}: {format_cost(profile, input_tokens, output_tokens)}")
    return "\n".join(f"cost: {line}" for line in lines)
