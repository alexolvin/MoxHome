"""S1.1/S1.2 (ТЗ_03): детерминированный отбор лексики (до LLM).

Цепочка (обратный порядок запрещён — ТЗ_03 S1): пары выравнивания E6 →
spaCy (модель пакета) → исключение служебных слов (stopwords модели) →
POS-фильтр (pos_select из pack.yaml — данные, не код) → агрегация по лемме
(дедупликация на всю книгу, S1.2: второе вхождение — в существующего
кандидата) → Zipf-полоса (L1.3) + CEFR (L1.4) → детерминированный
пре-ранк (частота, Zipf, лемма). LLM на этой стадии не участвует.

POS доступен у всех пяти языков: у fr_core_news_sm нет компонента `tagger`
(L1-1), но теги даёт `morphologizer` (измерено 2026-09-13: кандидаты
ненулевые в 5/5 языках). Язык без POS вообще — 0 кандидатов, стадия
работает и репортит метрику (S1-7).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from spacy.language import Language

from app.domain.book import Book
from app.domain.vocabulary import LexisCandidate
from app.epub import segmenter
from app.epub.aligner import AlignedPair
from app.languages.cefr import CefrSource
from app.languages.frequency import frequency_band
from app.languages.registry import PackSpec

#: Универсальный POS (spaCy) → enum частей речи card_schema.json.
_POS_TO_CARD: Mapping[str, str] = {"NOUN": "noun", "VERB": "verb", "ADJ": "adjective"}


@dataclass
class _Accum:
    """Промежуточное накопление по лемме (до вычисления полос/CEFR)."""

    pos: str
    word_form: str
    first_segment_id: str
    chapter_id: str
    example_sentence: str
    occurrences: int = 1
    segment_ids: tuple[str, ...] = ()


def _accumulate_pair(
    doc: Any,
    pair: AlignedPair,
    chapter_id: str,
    pos_set: frozenset[str],
    accum: dict[str, _Accum],
) -> None:
    """Токены одного сегмента → накопление по лемме (S1.1: фильтры)."""
    for token in doc:
        if token.is_space or token.is_punct or token.is_stop:
            continue
        pos_card = _POS_TO_CARD.get(token.pos_)
        if pos_card is None or token.pos_ not in pos_set:
            continue
        lemma = token.lemma_
        if not lemma:
            continue
        acc = accum.get(lemma)
        if acc is None:
            accum[lemma] = _Accum(
                pos=pos_card,
                word_form=token.text,
                first_segment_id=pair.segment_id,
                chapter_id=chapter_id,
                # spaCy 3.8: Token.sent (sent_ появился только в 4.x);
                # границы предложений — sentencizer (load_nlp гарантирует).
                example_sentence=token.sent.text.strip(),
                segment_ids=(pair.segment_id,),
            )
        else:
            acc.occurrences += 1
            if pair.segment_id not in acc.segment_ids:
                acc.segment_ids = (*acc.segment_ids, pair.segment_id)


def select_lexis_candidates(
    *,
    book: Book,
    pairs: Sequence[AlignedPair],
    pack: PackSpec,
    nlp: Language,
    cefr_source: CefrSource,
) -> tuple[LexisCandidate, ...]:
    """Детерминированный отбор по парам выравнивания E6 (ТЗ_03 S1.1/S1.2).

    Порядок итерации — порядок документов/сегментов карты выравнивания
    (стабилен между запусками — воспроизводимость S1-5).
    """
    pos_set = frozenset(pos for pos in pack.pos_select if pos in _POS_TO_CARD)
    seg_chapter: dict[str, str] = {}
    seg_text: dict[str, str] = {}
    for chapter in book.chapters:
        for seg in chapter.segments:
            seg_chapter[seg.segment_id] = chapter.chapter_id
            seg_text[seg.segment_id] = seg.plain_text

    accum: dict[str, _Accum] = {}
    for pair in pairs:
        chapter_id = seg_chapter.get(pair.segment_id)
        if chapter_id is None or not pair.source_text.strip():
            continue
        _accumulate_pair(nlp(pair.source_text), pair, chapter_id, pos_set, accum)

    candidates: list[LexisCandidate] = []
    for lemma, acc in accum.items():
        band, zipf = frequency_band(
            lemma, pack.code, pack.frequency_bands, pack.normalization
        )
        cefr = cefr_source.level_for(lemma)
        if cefr.level is not None:
            cefr_level, cefr_src = cefr.level, cefr.source
        else:
            # derived (или слово вне лексикона): уровень = частотная полоса
            # (L1-9: полосы именованы CEFR-уровнями) — оценка, не истина (L1.4).
            cefr_level, cefr_src = band, "derived"
        candidates.append(
            LexisCandidate(
                lemma=lemma,
                word_form=acc.word_form,
                pos=acc.pos,
                occurrences=acc.occurrences,
                first_segment_id=acc.first_segment_id,
                segment_ids=acc.segment_ids,
                chapter_id=acc.chapter_id,
                source_text_hash=hashlib.sha256(
                    segmenter.normalize_text(seg_text[acc.first_segment_id]).encode("utf-8")
                ).hexdigest(),
                frequency_band=band,
                zipf=zipf,
                cefr_level=cefr_level,
                cefr_source=cefr_src,
                example_sentence=acc.example_sentence,
            )
        )
    candidates.sort(key=lambda c: (-c.occurrences, c.zipf, c.lemma.casefold()))
    return tuple(candidates)
