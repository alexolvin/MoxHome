"""Сервисы языкового слоя (L0): статус пар и матрица.

Единая точка для API/UI (L0-5): статусы берутся только из резолвера —
UI/API не пере-вычисляют их (расхождение UI-статуса и резолвера = 0, L0-5).
"""

from __future__ import annotations

from app.core.config import Settings
from app.languages.resolver import PairReport, PairResolver, load_matrix


def _resolver(settings: Settings) -> PairResolver:
    return PairResolver(
        registry_root=settings.languages_root,
        prompts_root=settings.prompts_dir,
    )


def pair_status(settings: Settings, source: str, target: str) -> PairReport:
    """Статус пары (L0-5): UI/API читают ровно этот отчёт."""
    return _resolver(settings).resolve_pair(source, target)


def matrix_report(settings: Settings) -> tuple[PairReport, ...]:
    """Статусы всех заявленных в матрице пар (L0.2/L0.3)."""
    resolver = _resolver(settings)
    pairs = load_matrix(settings.language_matrix_path)
    return tuple(resolver.resolve_pair(source, target) for source, target in pairs)
