# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Глоссарий книги (L2): кандидаты (детерминированно), аннотация LLM,
парадигма, хранилище с привязкой к версии книги, проверка согласованности.

LLM — только перевод/пояснение/парадигма отобранных кандидатов (ТЗ L2.2);
поиск кандидатов и проверка парадигмы/согласованности — детерминированы.
L2 текст не правит (ТЗ L2.6): правка — L3 по списку расхождений.
"""

from app.glossary.annotation import (
    Annotation,
    GlossaryError,
    annotate_candidates,
    check_paradigm,
    terms_from_annotations,
)
from app.glossary.candidates import RULE_PRIORITY, Candidate, extract_candidates
from app.glossary.consistency import check_consistency
from app.glossary.service import (
    BookGlossaryBundle,
    GlossaryDisabledError,
    ensure_book_glossary,
    glossary_prefix,
)
from app.glossary.store import BookStore, BookVersion, book_key_of

__all__ = [
    "RULE_PRIORITY",
    "Annotation",
    "BookGlossaryBundle",
    "BookStore",
    "BookVersion",
    "Candidate",
    "GlossaryDisabledError",
    "GlossaryError",
    "annotate_candidates",
    "book_key_of",
    "check_consistency",
    "check_paradigm",
    "ensure_book_glossary",
    "extract_candidates",
    "glossary_prefix",
    "terms_from_annotations",
]
