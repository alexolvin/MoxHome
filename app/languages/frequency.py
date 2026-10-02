# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Частотность (L1.3): `wordfreq` + Zipf-шкала.

Источник — `wordfreq` (замороженный снимок ~2021 года — ограничение
зафиксировано в docs/LIMITATIONS.md: приемлемо для ранжирования, нет — для
утверждений о «современной употребительности»).

Полосы частотности задаются в `pack.yaml` через Zipf-шкалу (`frequency_bands`),
а не литералами в коде (L1-9): в этом модуле НЕТ числовых порогов — только
сравнение вычисленного Zipf-значения с границами из данных.
`zipf = 1 + log2(rank)` — определение шкалы wordfreq (документация пакета).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Any, Literal, cast

import wordfreq

from app.languages.registry import BandSpec, NormalizationSpec

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str, spec: NormalizationSpec) -> str:
    """Нормализация по правилам языка (pack.yaml: normalization) — данные, не код."""
    # Форма уже валидирована NormalizationSpec (множество форм фиксировано) —
    # cast для mypy (unicodedata.normalize ожидает Literal).
    result = unicodedata.normalize(
        cast("Literal['NFC', 'NFD', 'NFKC', 'NFKD']", spec.unicode_form), text
    )
    if spec.casefold:
        result = result.casefold()
    if spec.collapse_whitespace:
        result = _WHITESPACE_RE.sub(" ", result).strip()
    return result


def zipf_for(word: str, code: str) -> float:
    """Zipf-частотность словоформы/леммы в языке (wordfreq, снимок ~2021).

    Данные wordfreq — нижним регистром (леммы): поиск по casefold-форме.
    API 3.1.1: `zipf_frequency(word, lang)` (wordlist-параметр снят).
    """
    return wordfreq.zipf_frequency(word.casefold(), code)


def band_for(zipf: float, bands: Sequence[BandSpec]) -> str:
    """Полоса для Zipf-значения: первая полоса с `zipf <= zipf_max`;
    полоса с `zipf_max: null` — без верхней границы (запасная, последняя).
    Числовых порогов в коде нет (L1-9) — только сравнение с границами из YAML."""
    for band in bands:
        if band.zipf_max is None or zipf <= band.zipf_max:
            return band.band
    return bands[-1].band


def frequency_band(
    word: str, code: str, bands: Sequence[BandSpec], normalization: NormalizationSpec
) -> tuple[str, float]:
    """(полоса, zipf) для слова: нормализация по pack → wordfreq → bands из pack."""
    normalized = normalize_text(word, normalization)
    zipf = zipf_for(normalized, code)
    return band_for(zipf, bands), zipf


def band_report(bands: Sequence[BandSpec]) -> list[dict[str, Any]]:
    """Справочное представление полос (отчёты/evidence): границы — из данных."""
    return [{"band": band.band, "zipf_max": band.zipf_max} for band in bands]
