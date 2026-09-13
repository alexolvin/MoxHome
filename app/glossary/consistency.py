"""Проверка согласованности перевода по глоссарию (ТЗ L2.6, L2-3/L2-4/L2-7).

Механизм (детерминированный, без LLM): по карте выравнивания E6
(выровненные пары «исходный текст ↔ текст перевода» сегмент-к-сегменту) для
каждого термина глоссария:
- термин СЧИТАЕТСЯ в исходном сегменте, если в `source_text` встречается
  хотя бы одна его поверхностная форма (NFC + casefold, по границе слова);
- вхождение СОГЛАСОВАНО, если в `translation_text` встречается хотя бы ОДНА
  ожидаемая форма (канонический перевод + формы ПРОВЕРЕННОЙ парадигмы;
  `paradigm_unverified` — только каноническая, строже; L2.4/L3.2: «в парном
  блоке перевода отсутствует любая форма из парадигмы» — условие
  расхождения совпадает с условием отбора к правке L3);
- иначе — расхождение (TermMismatch) с segment_id.

Перечень вариантов (L2.6): для каждого термина — множества ожидаемых форм,
фактически найденные в переводе (по одним и тем же парам); расхождение —
«ожидаемая форма не найдена». Слово-к-слову выравнивание не используется
(его нет; сегментная карта E6 — единственный источник): фактический
«иной вариант» L2 не извлекает — L3 читает блок перевода при правке
(L3.3) и фиксирует «прежний вариант» в quality.log.

Л2 НЕ ПРАВИТ ТЕКСТ (ТЗ L2.6): проверка читает только исходный файл,
выровненные пары и глоссарий; выходной EPUB не меняется (проверяется
T-L2-3: 0 правок текста на этапе L2).
"""

from __future__ import annotations

import re
import unicodedata

from app.domain.glossary import (
    ConsistencyReport,
    Glossary,
    TermConsistency,
    TermMismatch,
)
from app.epub.aligner import AlignmentReport


def _norm(text: str) -> str:
    """Нормализация сравнения: NFC + casefold (как при отборе кандидатов)."""
    return unicodedata.normalize("NFC", text).casefold()


def _contains_word(haystack: str, needle: str) -> bool:
    """`needle` в `haystack` по границе слова (оба нормализованы)."""
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack) is not None


def check_consistency(glossary: Glossary, alignment: AlignmentReport) -> ConsistencyReport:
    """Проверка всех терминов глоссария по карте выравнивания (L2.6).

    В пары, не попавшие в выравнивание (unaligned), проверка не идёт: нет
    пары — нет и расхождения (сегмент уже учтён в покрытии E6).
    """
    pairs: list[tuple[str, str, str, str, str]] = []
    for doc in alignment.docs:
        for p in doc.aligned:
            pairs.append(
                (
                    p.segment_id,
                    _norm(p.source_text),
                    _norm(p.translation_text),
                    p.source_text,
                    p.translation_text,
                )
            )

    terms_checked = 0
    occurrences_checked = 0
    consistent_occurrences = 0
    term_results: list[TermConsistency] = []
    mismatches: list[TermMismatch] = []

    for term in glossary.terms:
        source_forms = tuple(dict.fromkeys(_norm(f) for f in term.source_forms))
        if not source_forms:
            continue
        terms_checked += 1
        expected = tuple(dict.fromkeys(_norm(f) for f in term.expected_forms()))
        seen_variants: list[str] = []
        mismatch_segments: list[str] = []
        occurrences = 0
        consistent = 0
        for segment_id, src, dst, src_raw, dst_raw in pairs:
            if not any(_contains_word(src, form) for form in source_forms):
                continue
            occurrences += 1
            occurrences_checked += 1
            found = [form for form in expected if _contains_word(dst, form)]
            if found:
                consistent += 1
                consistent_occurrences += 1
                for form in found:
                    if form not in seen_variants:
                        seen_variants.append(form)
            else:
                mismatch_segments.append(segment_id)
                mismatches.append(
                    TermMismatch(
                        term=term.term,
                        translation_expected=term.translation,
                        segment_id=segment_id,
                        source_text=src_raw,
                        translation_text=dst_raw,
                    )
                )
        term_results.append(
            TermConsistency(
                term=term.term,
                translation_expected=term.translation,
                occurrences=occurrences,
                consistent=consistent,
                variants=tuple(seen_variants),
                mismatch_segments=tuple(mismatch_segments),
            )
        )

    return ConsistencyReport(
        terms_checked=terms_checked,
        occurrences_checked=occurrences_checked,
        consistent_occurrences=consistent_occurrences,
        terms=tuple(term_results),
        mismatches=tuple(mismatches),
    )
