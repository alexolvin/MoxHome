# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Правка расхождений перевода по глоссарию книги (ТЗ_02 §L3, L3.2–L3.7).

Этап НЕ ПЕРЕВОДИТ КНИГУ: правится ровно то, что измеримо разошлось по списку
расхождений L2 (третье разрешённое исключение из Rule 8, §5.3 файла 00).

Отбор (L3.2) — сегмент правится, только если одновременно:
1. термин присутствует в исходном тексте сегмента (детерминированная
   проверка вхождения — тот же механизм, что в L2.6, Rule 8);
2. в парном блоке перевода отсутствует ЛЮБАЯ форма парадигмы термина;
3. термин имеет ПОДТВЕРЖДЁННУЮ парадигму (paradigm_unverified — только
   сообщается, автоправка запрещена).
Правка «на всякий случай» запрещена.

Механизм (L3.3): собственный клиент профиля `analysis` переписывает сегмент
ЦЕЛИКОМ (замена подстрокой запрещена: ломает согласование и порядок слов).

Валидация каждой правки до применения (L3.4) — 5 пунктов; любой сбой →
правка не применяется, расхождение остаётся и reported как неустранённое:
1. новая версия содержит форму парадигмы термина;
2. число предложений отличается не более чем на порог из конфигурации;
3. внутренняя разметка сохранена — сравнение мультимножества тегов и
   их атрибутов (непарсебый XHTML — сбой п.3);
4. язык новой версии — не исходный (уверенная детекция; D63: положительное
   подтверждение «именно целевой» без LLM недостижимо, BLOCKER-1);
5. версия не пуста и не идентична исходному тексту сегмента.

Идемпотентность (L3.8): повтор прогона по исправленному файлу не находит
расхождений и не порождает правок; ключ стадии включает хэш глоссария.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import lxml.etree

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.glossary import (
    PARADIGM_UNVERIFIED,
    PARADIGM_VERIFIED,
    Glossary,
    GlossaryTerm,
    TermMismatch,
)
from app.epub.aligner import AlignedPair
from app.epub.segmenter import full_text, localname, normalize_text
from app.glossary.consistency import contains_form, norm_text
from app.languages.detect import detect_text, normalize_code
from app.llm.analysis import AnalysisClient
from app.llm.tasks import TaskSpec
from app.storage.jobs_repo import JobRepository

#: Разделитель предложений для п.2 (точки/восклицания/вопросы/многоточие).
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def sentence_count(text: str) -> int:
    """Число предложений (детерминированно, без LLM): по финальной
    пунктуации с межсловным пробелом."""
    text = text.strip()
    if not text:
        return 0
    return len([part for part in _SENTENCE_SPLIT.split(text) if part.strip()])


def markup_signature(
    element: lxml.etree._Element,
) -> Counter[tuple[str, tuple[str, ...]]]:
    """Мультимножество (тег, атрибуты) всех элементов блока — критерий п.3:
    «сравнение множества тегов и их атрибутов» (L3.4). Ключи атрибутов —
    локальные имена (namespace-префикс — сериализационная деталь)."""
    signature: Counter[tuple[str, tuple[str, ...]]] = Counter()
    for el in element.iter():
        if not isinstance(el.tag, str):
            continue
        attrs = tuple(
            sorted(key.split("}", 1)[-1] + "=" + value for key, value in el.attrib.items())
        )
        signature[(localname(el), attrs)] += 1
    return signature


@dataclass(frozen=True)
class FixableSegment:
    """Сегмент, удовлетворивший ВСЕМ трём условиям отбора (L3.2)."""

    segment_id: str
    doc: str
    term: str
    source_xpath: str
    translation_xpath: str
    source_text: str
    translation_text: str


@dataclass(frozen=True)
class UnverifiedTerm:
    """Термин с `paradigm_unverified` (L3-9): автоправки нет — только отчёт
    с собственной долей согласованности."""

    term: str
    translation_expected: str
    occurrences: int
    consistent: int


@dataclass(frozen=True)
class FixSelection:
    """Результат отбора (L3.2) + перечень unverified-терминов (L3-9)."""

    total_segments: int
    fixable: tuple[FixableSegment, ...]
    unverified_terms: tuple[UnverifiedTerm, ...]

    @property
    def fixable_segment_ids(self) -> frozenset[str]:
        return frozenset(item.segment_id for item in self.fixable)


@dataclass(frozen=True)
class FixOutcome:
    """Итог одной попытки правки. `new_html` — None, если правка не применена.

    `validation_points` — пункты валидации L3.4, НЕ пройденные (пусто —
    все пять пройдены); `reason` — человекочитаемая причина отказа."""

    segment_id: str
    doc: str
    term: str
    applied: bool
    validation_points: tuple[int, ...] = ()
    reason: str = ""
    old_text: str = ""
    new_text: str = ""
    new_html: str | None = None
    prompt_file: str = ""
    prompt_version: str = ""
    model: str = ""


def select_fixable(
    glossary: Glossary,
    pairs: dict[str, AlignedPair],
    mismatches: tuple[TermMismatch, ...],
    term_stats: dict[str, tuple[int, int]],
    total_segments: int,
) -> FixSelection:
    """Отбор сегментов к правке (L3.2) + перечень unverified (L3-9).

    `pairs` — segment_id → пара выравнивания E6; `mismatches` — список
    расхождений L2 (с текстами пар); `term_stats` — term →
    (вхождений, согласованных) из отчёта L2 (для собственной доли
    unverified-множества); `total_segments` — число исходных сегментов
    (alignment.total) — знаменатель доли правимых (L3-6)."""
    fixable: list[FixableSegment] = []
    seen: set[tuple[str, str]] = set()
    for mismatch in mismatches:
        pair = pairs.get(mismatch.segment_id)
        entry = glossary.term(mismatch.term)
        if pair is None or entry is None:
            continue
        # Условие 1: термин в исходном тексте сегмента (детерминированно).
        if not any(
            contains_form(norm_text(pair.source_text), norm_text(form))
            for form in entry.source_forms
        ):
            continue
        # Условие 2: в переводе нет ЛЮБОЙ формы парадигмы (перепроверяем —
        # тот же контракт, что в L2.6; список L2 — вход, не догма).
        if any(
            contains_form(norm_text(pair.translation_text), norm_text(form))
            for form in entry.expected_forms()
        ):
            continue
        # Условие 3: подтверждённая парадигма (unverified — только отчёт).
        # `==` (не `is`): после roundtrip через хранилище строка — новый
        # объект (D63).
        if entry.paradigm_status != PARADIGM_VERIFIED:
            continue
        key = (pair.segment_id, entry.term)
        if key in seen:
            continue
        seen.add(key)
        fixable.append(
            FixableSegment(
                segment_id=pair.segment_id,
                doc=pair.doc,
                term=entry.term,
                source_xpath=pair.source_xpath,
                translation_xpath=pair.translation_xpath,
                source_text=pair.source_text,
                translation_text=pair.translation_text,
            )
        )
    unverified = tuple(
        UnverifiedTerm(
            term=term,
            translation_expected=entry.translation if entry is not None else "",
            occurrences=occurrences,
            consistent=consistent,
        )
        for term, (occurrences, consistent) in sorted(term_stats.items())
        if (entry := glossary.term(term)) is not None
        and entry.paradigm_status == PARADIGM_UNVERIFIED
        and occurrences > 0
    )
    return FixSelection(
        total_segments=total_segments,
        fixable=tuple(sorted(fixable, key=lambda f: (f.doc, f.translation_xpath, f.term))),
        unverified_terms=unverified,
    )


class GlossaryFixer:
    """Один вызов analysis-клиента на сегмент + 5-пунктная валидация (L3.3/L3.4).

    Повторы: клиент повторяет только НЕВАЛИДНЫЙ по JSON-схеме ответ в рамках
    retry_times профиля (E4.4). Валидный по схеме, но не прошедший детер-
    минированную валидацию ответ — отказ БЕЗ повтора (T-L3-14: «повторной
    попытки сверх лимита нет»).
    """

    def __init__(
        self,
        settings: Settings,
        repo: JobRepository,
        job_id: str,
        *,
        source_lang: str,
        target_lang: str,
        spec: TaskSpec,
        sentence_delta: int,
        seed: int,
        min_confidence: float,
        aliases: dict[str, str],
    ) -> None:
        self._settings = settings
        self._client = AnalysisClient(settings, repo, job_id)
        self._source_lang = source_lang
        self._target_lang = target_lang
        self._spec = spec
        self._sentence_delta = sentence_delta
        self._seed = seed
        self._min_confidence = min_confidence
        self._aliases = aliases
        self._model = settings.llm.profile("analysis").model

    def fix_segment(
        self,
        *,
        segment_id: str,
        doc: str,
        entry: GlossaryTerm,
        source_xml: bytes,
        translation_xml: bytes,
        style_note: str,
    ) -> FixOutcome:
        """Попытка правки одного сегмента: вызов + валидация (без применения)."""
        old_text = normalize_text(full_text(lxml.etree.fromstring(translation_xml)))
        payload: dict[str, Any] = {
            "segment_id": segment_id,
            "source_xml": source_xml.decode("utf-8"),
            "translation_xml": translation_xml.decode("utf-8"),
            "term": entry.term,
            "canonical_translation": entry.translation,
            "paradigm_forms": list(entry.expected_forms()),
            "style": style_note or None,
        }
        try:
            result = self._client.run_task(
                self._spec, self._source_lang, self._target_lang,
                json.dumps(payload, ensure_ascii=False),
            )
        except AppError as exc:
            # Сбой клиента (повторы исчерпаны, E4-4): правка невозможна.
            return FixOutcome(
                segment_id=segment_id, doc=doc, term=entry.term, applied=False,
                validation_points=(0,), reason=f"задача glossary_fix не выполнена: {exc.message}",
                old_text=old_text,
            )
        items = result.payload.get("items") or []
        if len(items) != 1 or items[0].get("segment_id") != segment_id:
            return FixOutcome(
                segment_id=segment_id, doc=doc, term=entry.term, applied=False,
                validation_points=(3,),
                reason="ответ не содержит ровно один блок с тем же segment_id",
                old_text=old_text,
            )
        new_html = str(items[0].get("html") or "")
        failures = validate_fix(
            new_html=new_html,
            translation_xml=translation_xml,
            source_text=normalize_text(full_text(lxml.etree.fromstring(source_xml))),
            expected_forms=entry.expected_forms(),
            sentence_delta=self._sentence_delta,
            source_lang=self._source_lang,
            seed=self._seed,
            min_confidence=self._min_confidence,
            aliases=self._aliases,
        )
        if failures:
            points = tuple(point for point, _ in failures)
            return FixOutcome(
                segment_id=segment_id, doc=doc, term=entry.term, applied=False,
                validation_points=points,
                reason="; ".join(reason for _, reason in failures),
                old_text=old_text, new_text="",
            )
        new_text = normalize_text(full_text(lxml.etree.fromstring(new_html)))
        return FixOutcome(
            segment_id=segment_id, doc=doc, term=entry.term, applied=True,
            old_text=old_text, new_text=new_text, new_html=new_html,
            prompt_file=result.prompt.file,
            prompt_version=str(result.prompt.version),
            model=self._model,
        )


def validate_fix(
    *,
    new_html: str,
    translation_xml: bytes,
    source_text: str,
    expected_forms: tuple[str, ...],
    sentence_delta: int,
    source_lang: str,
    seed: int,
    min_confidence: float,
    aliases: dict[str, str],
) -> list[tuple[int, str]]:
    """5 пунктов валидации (L3.4). Пустой список — все пункты пройдены.

    Возвращает (пункт, причина) по каждому НЕ пройденному пункту."""
    failures: list[tuple[int, str]] = []
    try:
        new_root = lxml.etree.fromstring(new_html.encode("utf-8"))
    except (lxml.etree.XMLSyntaxError, ValueError) as exc:
        return [(3, f"ответ не разбирается как XHTML-блок: {exc}")]
    old_root = lxml.etree.fromstring(translation_xml)
    new_text = normalize_text(full_text(new_root))

    # П.1: форма парадигмы термина в новой версии.
    if not any(contains_form(norm_text(new_text), norm_text(form)) for form in expected_forms):
        failures.append((1, "новая версия не содержит форму парадигмы термина"))
    # П.2: число предложений в пределах порога.
    old_count = sentence_count(normalize_text(full_text(old_root)))
    new_count = sentence_count(new_text)
    if abs(new_count - old_count) > sentence_delta:
        failures.append(
            (2, f"число предложений изменилось: {old_count} -> {new_count} "
                f"(допуск ±{sentence_delta})")
        )
    # П.3: внутренняя разметка (теги + атрибуты) сохранена.
    if markup_signature(new_root) != markup_signature(old_root):
        failures.append((3, "внутренняя разметка блока изменена (теги/атрибуты)"))
    # П.4: язык — не исходный (уверенная детекция; D63).
    detection = detect_text(new_text, seed)
    if (
        detection.language is not None
        and detection.confidence is not None
        and detection.confidence >= min_confidence
        and normalize_code(detection.language, aliases) == normalize_code(source_lang, aliases)
    ):
        failures.append(
            (4, f"язык новой версии — исходный ({detection.language}, "
                f"p={detection.confidence:.2f})")
        )
    # П.5: не пуста и не идентична исходному тексту сегмента.
    if not new_text:
        failures.append((5, "новая версия пуста"))
    elif norm_text(new_text) == norm_text(source_text):
        failures.append((5, "новая версия идентична исходному тексту сегмента"))
    return failures
