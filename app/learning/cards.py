# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""S1 (ТЗ_03): заполнение словарных карточек LLM + детерминированная
валидация перед выпуском.

LLM (задача vocabulary_card) видит детерминированных кандидатов (S1.1) и
заполняет/ранжирует карточки (S1.2). Перед выпуском — детерминированные
проверки (ТЗ_03 S1, «допускаются только после проверки»):

1. словоформа реально присутствует в указанном сегменте (S1-1);
2. лемма соответствует словоформе по spaCy (S1.5);
3. карточка валидна по card_schema.json языка (проекция на поля схемы +
   проверка плейсхолдеров, S1-4);
4. пример — из книги (текст главы) либо помечен `example_generated` (S1-6);
5. проверка призраков: лемма карточки — среди детерминированных кандидатов
   (подброшенная моделью карточка отклоняется, S1-5).

Отклонённые карточки не выпускаются и учитываются в отчёте с причинами
(S1-4/S1-6). Поля карточки, приходящие от LLM, проходят только валидацию —
измеренные поля (лемма/словоформа/POS/сегмент/полоса/CEFR) берутся из
кандидата, не из ответа модели.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from spacy.language import Language

from app.domain.vocabulary import LexisCandidate
from app.glossary.consistency import contains_form
from app.languages.cards import validate_card
from app.languages.cefr import CEFR_ORDER
from app.languages.nlp import lemma_of

_WS_RE = re.compile(r"\s+")

#: Поля ответа LLM, имеющие отдельную семантику (не переносятся в карточку
#: как есть).
_ITEM_CORE_FIELDS: frozenset[str] = frozenset(
    {"lemma", "meaning", "translation", "example", "example_generated", "usefulness"}
)


def _norm_text(text: str) -> str:
    """Нормализация для проверки вхождения примера: NFC + casefold +
    схлопнутые пробелы (более устойчива к переносам, чем L2-нормализация)."""
    return _WS_RE.sub(" ", unicodedata.normalize("NFC", text)).casefold().strip()


def example_in_book(example: str, chapter_text: str) -> bool:
    """Пример — в тексте главы (нормализованное вхождение, S1.6)."""
    return _norm_text(example) in _norm_text(chapter_text)


def cefr_out_of_range(card_cefr: str, user_cefr: str | None, distance: int) -> bool:
    """Карточка вне CEFR-диапазона пользователя: дистанция уровней >=
    `distance` (конфигурация, Rule 1). Диапазон — НЕ абсолютный фильтр
    (ТЗ_03): материал допускается с меткой. user_cefr не задан — False."""
    if user_cefr is None or card_cefr not in CEFR_ORDER or user_cefr not in CEFR_ORDER:
        return False
    return abs(CEFR_ORDER.index(card_cefr) - CEFR_ORDER.index(user_cefr)) >= distance


def _salvage_main_forms(projection: dict[str, Any], schema: Mapping[str, Any]) -> None:
    """Спасение глагольных полей (S1): `main_forms` — конструкция германской
    схемы; для других языков модель иногда выдаёт её, хотя в схеме нет
    (`additionalProperties: false` → отклонение). Переносим подполя в поля
    глагола схемы (en: past_tense/past_participle) и убираем лишнее поле."""
    main_forms = projection.get("main_forms")
    if not isinstance(main_forms, Mapping):
        return
    props = schema.get("properties") or {}
    if "main_forms" in props:
        return  # германская схема — main_forms легитимен, не трогаем
    for target, src in (("past_tense", "past"), ("past_participle", "participle")):
        if target in props and target not in projection and main_forms.get(src):
            projection[target] = main_forms[src]
    projection.pop("main_forms", None)


def assemble_card(
    candidate: LexisCandidate,
    item: Mapping[str, Any],
    *,
    user_level: str | None,
    user_cefr: str | None,
    source_language: str,
    target_language: str,
    glossary_hash: str,
    prompt_file: str,
    prompt_version: int,
    model: str,
    profile: str,
    timestamp: str,
    cefr_distance: int,
) -> dict[str, Any]:
    """Сборка карточки: измеренные поля из кандидата + заполнение LLM +
    привязка к источнику/версии (ТЗ_03 общее требование 1–2)."""
    card: dict[str, Any] = {
        "lemma": candidate.lemma,
        "word_form": candidate.word_form,
        "pos": candidate.pos,
        "meaning": item["meaning"],
        "translation": item["translation"],
        "example": item["example"],
        "segment_id": candidate.first_segment_id,
        "frequency_band": candidate.frequency_band,
        "cefr_level": candidate.cefr_level,
        "cefr_source": candidate.cefr_source,
    }
    for key, value in item.items():
        if key not in _ITEM_CORE_FIELDS:
            card[key] = value
    card.update(
        {
            "usefulness": int(item["usefulness"]),
            "example_generated": bool(item.get("example_generated", False)),
            "cefr_out_of_range": cefr_out_of_range(
                candidate.cefr_level, user_cefr, cefr_distance
            ),
            "occurrences": candidate.occurrences,
            "chapter_id": candidate.chapter_id,
            "source_text_hash": candidate.source_text_hash,
            "prompt_file": prompt_file,
            "prompt_version": prompt_version,
            "model": model,
            "profile": profile,
            "source_language": source_language,
            "target_language": target_language,
            "user_level": user_level,
            "user_cefr": user_cefr,
            "glossary_hash": glossary_hash,
            "timestamp": timestamp,
        }
    )
    return card


def check_vocab_cards(
    *,
    items: Sequence[Mapping[str, Any]],
    candidates: Mapping[str, LexisCandidate],
    seg_texts: Mapping[str, str],
    chapter_texts: Mapping[str, str],
    card_schema: Mapping[str, Any],
    nlp: Language,
) -> tuple[list[tuple[dict[str, Any], LexisCandidate]], list[dict[str, Any]]]:
    """Детерминированная валидация карточек LLM перед выпуском (S1.5).

    Возвращает ((item, candidate) по порядку items для прошедших,
    отклонённые `[{"lemma", "reasons"}]`). Сборка привязочных полей
    (`assemble_card`) — после ранжирования, только для выпущенных.
    """
    validated: list[tuple[dict[str, Any], LexisCandidate]] = []
    rejected: list[dict[str, Any]] = []
    for item in items:
        lemma = str(item.get("lemma", ""))
        candidate = candidates.get(lemma)
        reasons: list[str] = []
        salvaged = dict(item)
        if candidate is None:
            reasons.append("подброшенная карточка: лемма не среди кандидатов (S1.5)")
        else:
            seg_text = seg_texts.get(candidate.first_segment_id, "")
            if not contains_form(seg_text, candidate.word_form):
                reasons.append(
                    f"словоформа отсутствует в сегменте {candidate.first_segment_id} (S1.1)"
                )
            elif lemma_of(nlp, candidate.word_form) != candidate.lemma:
                reasons.append("лемма не соответствует словоформе по spaCy (S1.5)")
            else:
                _salvage_main_forms(salvaged, card_schema)
                projection = {
                    "lemma": candidate.lemma,
                    "word_form": candidate.word_form,
                    "pos": candidate.pos,
                    "meaning": salvaged.get("meaning"),
                    "translation": salvaged.get("translation"),
                    "example": salvaged.get("example"),
                    "segment_id": candidate.first_segment_id,
                    "frequency_band": candidate.frequency_band,
                    "cefr_level": candidate.cefr_level,
                    "cefr_source": candidate.cefr_source,
                }
                for key, value in salvaged.items():
                    if key not in _ITEM_CORE_FIELDS:
                        projection[key] = value
                ok, errors = validate_card(projection, card_schema)
                if not ok:
                    reasons.extend(f"card_schema.json: {e}" for e in errors)
                elif not bool(salvaged.get("example_generated", False)) and not example_in_book(
                    str(salvaged.get("example", "")), chapter_texts.get(candidate.chapter_id, "")
                ):
                    reasons.append("пример не из книги и не помечен сгенерированным (S1.6)")
        if reasons or candidate is None:
            rejected.append({"lemma": lemma, "reasons": reasons})
        else:
            validated.append((salvaged, candidate))
    return validated, rejected
