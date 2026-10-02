# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Лексика книги (S1, ТЗ_03): кандидат, прошедший детерминированный отбор.

Детерминированная стадия (ДО LLM, обратный порядок запрещён — ТЗ_03 S1):
spaCy-лемматизация → POS-фильтр из пакета (pos_select) → исключение
служебных слов (stopwords модели) → Zipf-полоса (L1.3) → CEFR-уровень
(L1.4) → повторяемость в книге. LLM только ранжирует и заполняет карточки
(S1.2) — выбор лексики моделью не выдвигается.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LexisCandidate:
    """Кандидат лексики книги (S1.1/S1.2).

    Все поля — измеренные, не оценка LLM: `word_form` — словоформа первого
    вхождения; `first_segment_id`/`chapter_id` — привязка к источнику
    (ТЗ_03 общее требование 1); `source_text_hash` — sha256 нормализованного
    текста первого сегмента (NFC + схлопнутые пробелы, E2.3);
    `example_sentence` — предложение первого вхождения (источник примера из
    книги, S1.6); `frequency_band`/`zipf` — L1.3 (границы из pack.yaml);
    `cefr_level` — L1.4 (лексикон либо частотная полоса: при derived уровне
    равна полосе, полосы именованы CEFR-уровнями); `occurrences` — число
    вхождений леммы в книге (дедупликация по лемме, S1.2: второе вхождение
    идёт в существующего кандидата).
    """

    lemma: str
    word_form: str
    #: "noun" | "verb" | "adjective" (enum card_schema.json) — по POS модели.
    pos: str
    occurrences: int
    first_segment_id: str
    segment_ids: tuple[str, ...]
    chapter_id: str
    source_text_hash: str
    frequency_band: str
    zipf: float
    cefr_level: str
    cefr_source: str
    example_sentence: str
