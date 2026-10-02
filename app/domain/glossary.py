# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Модели глоссария книги (L2): запись термина, парадигма, стиль, расхождения.

Глоссарий строится ДО перевода (L2.1–L2.4) и хранится с книгой, привязанно к
версии книги (хэш исходного файла; L2.8). Проверка согласованности после
перевода — детекция только (L2.6): L2 текст не правит, список расхождений
потребляет L3 (вход L3.1: «список расхождений из L2»).

Парадигма целевого языка (L2.4): для флективного языка одной канонической
формы недостаточно — «Марлоу» в разных падежах — норма, а не расхождение.
Формы парадигмы — данные целевого pack.yaml (список имён форм по частям
речи); значения генерируются LLM один раз (L2.4: «вместе с переводом
термина») и проходят детерминированную проверку (L2.4):
- все формы различимы либо совпадение объяснено (поимённо);
- общая основа у всех форм непустая.
Не прошедшая проверка парадигма ОТКЛАДЫВАЕТСЯ: термин помечается
`paradigm_unverified` и исключается из автоматической правки L3.
Несклоняемые термины помечаются явно (inflected=false), а не получают
идентичные формы (T-L2-11).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Статусы парадигмы записи глоссария (L2.4).
PARADIGM_VERIFIED = "verified"
PARADIGM_UNVERIFIED = "unverified"
PARADIGM_NOT_APPLICABLE = "not_applicable"

PARADIGM_STATUSES: frozenset[str] = frozenset(
    {PARADIGM_VERIFIED, PARADIGM_UNVERIFIED, PARADIGM_NOT_APPLICABLE}
)

#: Части речи записи глоссария (контракт задачи glossary_annotation, L2.3).
GLOSSARY_POS: tuple[str, ...] = ("noun", "verb", "adjective", "proper_noun", "other")


@dataclass(frozen=True)
class Paradigm:
    """Парадигма термина в целевом языке (L2.4).

    `form_names` — именные позиции форм, порядок из pack.yaml целевого языка
    (для ru: шесть падежей единственного и множественного числа).
    `forms` — значения в том же порядке; для несклоняемого термина — пусто
    (явная метка inflected=false, T-L2-11).
    `coincidences` — поимённое объяснение совпадающих форм (L2.4).
    """

    inflected: bool
    form_names: tuple[str, ...] = ()
    forms: tuple[str, ...] = ()
    coincidences: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def coincidence_map(self) -> dict[str, str]:
        return dict(self.coincidences)


@dataclass(frozen=True)
class GlossaryTerm:
    """Запись глоссария (L2.3): исходный термин, перевод (каноническая форма),
    часть речи, контекст (segment_id первого вхождения), число вхождений,
    примечание, признак «утверждено пользователем», парадигма целевого языка."""

    term: str
    #: Поверхностные формы термина в исходном тексте (первая встречающаяся —
    #: каноническая `term`; для склоняемых имён — все формы одной леммы).
    source_forms: tuple[str, ...]
    translation: str
    pos: str
    first_segment_id: str
    occurrences: int
    note: str | None = None
    user_confirmed: bool = False
    paradigm: Paradigm | None = None
    #: verified | unverified | not_applicable (L2.4).
    paradigm_status: str = PARADIGM_NOT_APPLICABLE
    #: Причина отклонения парадигмы / явная метка несклоняемости (аудит).
    paradigm_reason: str | None = None

    def expected_forms(self) -> tuple[str, ...]:
        """Ожидаемые формы перевода в выходном тексте (L2.6, с учётом парадигмы):
        каноническая форма + формы проверенной парадигмы. `paradigm_unverified`
        даёт только каноническую форму (строже; в автоправку L3 термин не
        входит).

        Сравнение статуса — по ЗНАЧЕНИЮ (`==`): после roundtrip через
        хранилище (YAML) строка — новый объект, идентичность (`is`) не
        гарантирована (D63)."""
        forms = [self.translation]
        if self.paradigm is not None and self.paradigm_status == PARADIGM_VERIFIED:
            forms.extend(self.paradigm.forms)
        return tuple(dict.fromkeys(forms))


@dataclass(frozen=True)
class Glossary:
    """Глоссарий книги (версии): привязан к хэшу исходного файла (L2.8)."""

    source_language: str
    target_language: str
    terms: tuple[GlossaryTerm, ...]
    #: sha256 исходного EPUB (версия книги, L2.8).
    source_hash: str = ""

    def term(self, name: str) -> GlossaryTerm | None:
        for entry in self.terms:
            if entry.term == name:
                return entry
        return None


@dataclass(frozen=True)
class StyleProfile:
    """Стилевой профиль книги (L2.7): определяется один раз, хранится с книгой
    и применяется во всех последующих заданиях по ней."""

    style: str  # formal | neutral | literary | conversational
    region: str | None = None
    note: str | None = None


STYLE_KINDS: frozenset[str] = frozenset(
    {"formal", "neutral", "literary", "conversational"}
)


@dataclass(frozen=True)
class TermMismatch:
    """Расхождение (L2.6): термин есть в исходном сегменте, а в парном переводе
    нет НИ ОДНОЙ формы парадигмы (с канонической; L3.2 — то же условие
    отбора к правке). L2 только фиксирует — текст не правится."""

    term: str
    translation_expected: str
    segment_id: str
    source_text: str
    translation_text: str


@dataclass(frozen=True)
class TermConsistency:
    """Итог проверки по одному термину: варианты перевода, найденные в
    выровненных парах (перечень вариантов, L2.6), и сегменты-расхождения."""

    term: str
    translation_expected: str
    occurrences: int  # пар, где термин в исходном тексте
    consistent: int  # пар, где найдена хотя бы одна ожидаемая форма
    variants: tuple[str, ...]  # ожидаемые формы, найденные в переводе
    mismatch_segments: tuple[str, ...] = ()

    @property
    def consistent_share(self) -> float:
        return self.consistent / self.occurrences if self.occurrences else 1.0


@dataclass(frozen=True)
class ConsistencyReport:
    """Проверка согласованности после перевода (L2.6). Детекция только."""

    terms_checked: int
    occurrences_checked: int
    consistent_occurrences: int
    terms: tuple[TermConsistency, ...]
    mismatches: tuple[TermMismatch, ...]

    @property
    def share(self) -> float:
        return (
            self.consistent_occurrences / self.occurrences_checked
            if self.occurrences_checked
            else 1.0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": "L2",
            "note": "детекция только; текст не правится (L2.6), список потребляет L3",
            "terms_checked": self.terms_checked,
            "occurrences_checked": self.occurrences_checked,
            "consistent_occurrences": self.consistent_occurrences,
            "consistent_share": round(self.share, 6),
            "terms": [
                {
                    "term": t.term,
                    "translation_expected": t.translation_expected,
                    "occurrences": t.occurrences,
                    "consistent": t.consistent,
                    "variants": list(t.variants),
                    "mismatch_segments": list(t.mismatch_segments),
                }
                for t in self.terms
            ],
            "mismatches": [
                {
                    "term": m.term,
                    "translation_expected": m.translation_expected,
                    "segment_id": m.segment_id,
                    "source_text": m.source_text,
                    "translation_text": m.translation_text,
                }
                for m in self.mismatches
            ],
        }
