# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""S2/S3 (ТЗ_03): идиомы, устойчивые сочетания, языкоспецифичные MWE (S2) и
грамматические явления (S3) — в терминах таксономии языка-источника
(механизм двухступенчатый, как S1).

Детерминированный предотбор (S2.3/S3.5): морфосинтаксические шаблоны spaCy —
данные (`patterns` в phenomena.yaml; типы: `sequence` — смежные POS-
последовательности, `wrap` — два маркера в одном предложении,
`separable_units` — механизм L1 `lexical_units`, `token` — один токен по
морфологическим признакам/словоформам, Rule 8) + повторяемость (вхождения на
всю книгу; жёсткий фильтр — только у широких POS-шаблонов, `min_occurrences`,
данные). LLM (задачи `mwe_note`/`grammar_note`) кандидатов не выдвигает —
только уточняет и объясняет: тип (id таксономии), канонический вид/пояснение
(ТЗ_03 S2.3: порядок стадий обратным запрещён).

Скоп подстадии — поле `stages` таксономии (for_stage): движок агностичен к
стадиям (Rule 8: один механизм для S2 и S3).

Запись (S2.4): поверхностная форма в тексте, части с позициями, канонический
вид, id типа из таксономии языка, значение, перевод, пример из книги,
segment_id. Разорванные конструкции (отделяемые единицы) — ОБЕ части с
позициями обязательны: запись без второй части невалидна (S2.5).

Валидация (S2.6): id — в таксономии языка-источника; все части конструкции
присутствуют в указанном сегменте (механизм `contains_form`, Rule 8);
канонический вид непуст; поверхностная форма — среди детерминированных
кандидатов («подброшенное» отклоняется — механизм S1.5).

Отбор — по тексту выровненного подмножества E6 (исходный текст — как S1);
окно предложения — данные pack.yaml (`sentence_split`).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from spacy.language import Language
from spacy.tokens import Doc, Token

from app.core.errors import ConfigurationError
from app.domain.book import Book
from app.epub import segmenter
from app.epub.aligner import AlignedPair
from app.glossary.consistency import contains_form
from app.languages.frequency import normalize_text
from app.languages.nlp import lexical_units
from app.languages.phenomena import (
    Pattern,
    PatternMarker,
    PhenomenaTaxonomy,
    Phenomenon,
    TokenCriterion,
)
from app.languages.registry import PackSpec


@dataclass(frozen=True)
class MwePart:
    """Часть конструкции: словоформа + индекс токена в предложении."""

    text: str
    index: int


@dataclass(frozen=True)
class MweHit:
    """Один срабатывание шаблона в одном предложении (до агрегации)."""

    sentence: str
    segment_id: str
    phenomenon_id: str
    surface: str
    parts: tuple[MwePart, ...]
    unit_lemma: str | None


@dataclass(frozen=True)
class MweCandidate:
    """Детерминированный кандидат MWE (агрегирован на всю книгу)."""

    phenomenon_id: str
    surface: str
    parts: tuple[MwePart, ...]
    #: Отделяемая единица: unit_lemma (de «aufstehen», nl «opstaan»);
    #: у прочих типов — None.
    unit_lemma: str | None
    first_segment_id: str
    chapter_id: str
    segment_ids: tuple[str, ...]
    occurrences: int
    example_sentence: str
    source_text_hash: str


@dataclass(frozen=True)
class MweExpectation:
    """Ожидание golden: id типа (+ для отделяемых — ожидаемая unit_lemma)."""

    id: str
    unit: str | None = None


@dataclass(frozen=True)
class MweGoldenEntry:
    """Golden-предложение S2: текст + ожидаемые (id, unit) пары."""

    text: str
    expected: tuple[MweExpectation, ...]


def load_mwe_golden(pack_dir: Path) -> tuple[MweGoldenEntry, ...]:
    """Golden-набор S2 языка (`mwe_golden.yaml` рядом с pack.yaml).

    Файл отсутствует/пуст — пустой набор (та же семантика, что у golden.yaml,
    L0): отсутствующий ресурс, а не ошибка загрузки."""
    path = pack_dir / "mwe_golden.yaml"
    if not path.is_file():
        return ()
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"не удалось разобрать MWE golden {path}: {exc}") from exc
    entries = data.get("mwe") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        return ()
    golden: list[MweGoldenEntry] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigurationError(f"MWE golden {path}: запись должна быть mapping")
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ConfigurationError(f"MWE golden {path}: пустой text")
        expected_raw = entry.get("expected")
        if not isinstance(expected_raw, list) or not expected_raw:
            raise ConfigurationError(
                f"MWE golden {path}: «{text}»: expected — непустой список"
            )
        expected: list[MweExpectation] = []
        for exp in expected_raw:
            if not isinstance(exp, dict):
                raise ConfigurationError(f"MWE golden {path}: expected — список mapping")
            exp_id = exp.get("id")
            if not isinstance(exp_id, str) or not exp_id.strip():
                raise ConfigurationError(f"MWE golden {path}: expected.id — непустая строка")
            unit = exp.get("unit")
            if unit is not None and (not isinstance(unit, str) or not unit.strip()):
                raise ConfigurationError(
                    f"MWE golden {path}: expected.unit — строка или отсутствует"
                )
            expected.append(MweExpectation(exp_id, unit))
        golden.append(MweGoldenEntry(text.strip(), tuple(expected)))
    return tuple(golden)


# ------------------------------------------------------------------ шаблоны


def _sent_ok(sent_text: str, min_chars: int, max_chars: int | None) -> bool:
    """Окно обработки предложения (pack.yaml: sentence_split)."""
    length = len(sent_text.strip())
    if length < min_chars:
        return False
    return max_chars is None or length <= max_chars


def _span_text(tokens: Sequence[Token]) -> str:
    """Поверхностная форма пролёта токенов — из ИСХОДНОГО текста (позиции
    символов), а не join по словоформам: слипшиеся формы (fr «n'est»,
    «s'appelle») остаются слитными, как в тексте книги."""
    first, last = tokens[0], tokens[-1]
    return first.doc.text[first.idx : last.idx + len(last.text)]


def _sequence_hits(
    tokens: list[Token], pattern: Pattern
) -> tuple[tuple[str, tuple[MwePart, ...]], ...]:
    """Все смежные POS-спаны (с опц. разрешёнными словоформами позиций)."""
    width = len(pattern.pos)
    if len(tokens) < width:
        return ()
    out: list[tuple[str, tuple[MwePart, ...]]] = []
    for start in range(len(tokens) - width + 1):
        span = tokens[start : start + width]
        if any(token.pos_ != tag for token, tag in zip(span, pattern.pos, strict=True)):
            continue
        if any(
            words and span[position].text.casefold() not in words
            for position, words in pattern.texts
        ):
            continue
        parts = tuple(
            MwePart(token.text, start + offset) for offset, token in enumerate(span)
        )
        out.append((_span_text(span), parts))
    return tuple(out)


def _marker_ok(token: Token, marker: PatternMarker) -> bool:
    if marker.text and token.text.casefold() not in marker.text:
        return False
    if marker.pos and token.pos_ not in marker.pos:
        return False
    return _morph_ok(token, marker.mood, marker.tense, marker.aspect)


def _morph_value(token: Token, feature: str) -> tuple[str, ...]:
    """Значение универсальной фичи spaCy (Mood/Tense/Aspect): `Morphl.get`
    отдаёт список значений (измерено: «Mood» в de-модели — ['Sub']);
    фича отсутствует — None/пустой список."""
    value = token.morph.get(feature, None)
    return tuple(value) if value else ()


def _morph_ok(
    token: Token,
    mood: tuple[str, ...],
    tense: tuple[str, ...],
    aspect: tuple[str, ...],
) -> bool:
    """Универсальные морфологические признаки (spaCy): пустой набор — без
    требования (Rule 1: значения — данные таксономии)."""
    if mood and not set(_morph_value(token, "Mood")) & set(mood):
        return False
    if tense and not set(_morph_value(token, "Tense")) & set(tense):
        return False
    return not aspect or bool(set(_morph_value(token, "Aspect")) & set(aspect))


def _criterion_ok(token: Token, criterion: TokenCriterion, index: int) -> bool:
    """Критерий шаблона `token`: И заданных полей; поле не задано — без
    требования. ``index`` — позиция токена в ПРЕДЛОЖЕНИИ (criterion.position
    считается по предложению, не по документу)."""
    if criterion.pos and token.pos_ not in criterion.pos:
        return False
    if criterion.text and token.text.casefold() not in criterion.text:
        return False
    if not _morph_ok(token, criterion.mood, criterion.tense, criterion.aspect):
        return False
    return criterion.position is None or index == criterion.position


def _token_hits(
    tokens: list[Token], pattern: Pattern
) -> tuple[tuple[str, tuple[MwePart, ...]], ...]:
    """Одно срабатывание на подходящий токен (ИЛИ критериев); поверхность —
    сам токен, часть — (текст, индекс в предложении, как у прочих частей)."""
    hits: list[tuple[str, tuple[MwePart, ...]]] = []
    for index, token in enumerate(tokens):
        if any(
            _criterion_ok(token, criterion, index) for criterion in pattern.criteria
        ):
            hits.append((token.text, (MwePart(token.text, index),)))
    return tuple(hits)


def _wrap_hits(
    tokens: list[Token], pattern: Pattern
) -> tuple[tuple[str, tuple[MwePart, ...]], ...]:
    """Два маркера в пределах предложения (start раньше end); один хит
    на предложение (первый start, первый подходящий end после него)."""
    if pattern.start is None or pattern.end is None:
        return ()
    start_index = next(
        (i for i, token in enumerate(tokens) if _marker_ok(token, pattern.start)), None
    )
    if start_index is None:
        return ()
    end_index: int | None = None
    if pattern.end_final:
        last = max(
            (i for i, token in enumerate(tokens) if not token.is_punct), default=None
        )
        if last is not None and last > start_index and _marker_ok(tokens[last], pattern.end):
            end_index = last
    if end_index is None:
        end_index = next(
            (
                i
                for i in range(start_index + 1, len(tokens))
                if _marker_ok(tokens[i], pattern.end)
            ),
            None,
        )
    if end_index is None:
        return ()
    span = tokens[start_index : end_index + 1]
    parts = tuple(
        MwePart(token.text, start_index + offset) for offset, token in enumerate(span)
    )
    return ((_span_text(span), parts),)


def _separable_hits(doc: Doc, pack: PackSpec) -> tuple[MweHit, ...]:
    """Отделяемые единицы (L1 `lexical_units`, Rule 8): части — глагол и
    приставка с индексами; поверхностная форма — полный текст пролёта."""
    units = lexical_units(
        doc,
        pack.separable_prefixes,
        pack.sentence_split.min_chars,
        pack.sentence_split.max_chars,
    )
    if not units:
        return ()
    by_text: dict[str, list[list[Token]]] = {}
    for sent in doc.sents:
        by_text.setdefault(sent.text, []).append(list(sent))
    hits: list[MweHit] = []
    for unit in units:
        queue = by_text.get(unit.sentence, [])
        token_list = queue.pop(0) if queue else None
        if token_list is None or not (
            0 <= unit.verb_index < len(token_list)
            and 0 <= unit.particle_index < len(token_list)
        ):
            # Деградация (разметка/индексы разошлись — не должно случаться):
            # части — из полей единицы, поверхность — явный пропуск.
            surface = f"{unit.verb_form} … {unit.particle}"
        else:
            span = token_list[unit.verb_index : unit.particle_index + 1]
            surface = _span_text(span)
        parts = (
            MwePart(unit.verb_form, unit.verb_index),
            MwePart(unit.particle, unit.particle_index),
        )
        hits.append(
            MweHit(
                sentence=unit.sentence,
                segment_id="",
                phenomenon_id="",
                surface=surface,
                parts=parts,
                unit_lemma=unit.unit_lemma,
            )
        )
    return tuple(hits)


def _pattern_hits(
    pattern: Pattern,
    *,
    doc: Doc,
    pair: AlignedPair,
    phenomenon_id: str,
    pack: PackSpec,
) -> list[MweHit]:
    """Хиты одного шаблона в одном документе (dispatch по `type` — данные
    таксономии; `separable_units` — механизм L1, остальные — по предложениям
    в окне pack.yaml)."""
    min_chars = pack.sentence_split.min_chars
    max_chars = pack.sentence_split.max_chars
    if pattern.type == "separable_units":
        return [
            MweHit(
                sentence=hit.sentence,
                segment_id=pair.segment_id,
                phenomenon_id=phenomenon_id,
                surface=hit.surface,
                parts=hit.parts,
                unit_lemma=hit.unit_lemma,
            )
            for hit in _separable_hits(doc, pack)
        ]
    hits: list[MweHit] = []
    for sent in doc.sents:
        if not _sent_ok(sent.text, min_chars, max_chars):
            continue
        tokens = list(sent)
        if pattern.type == "sequence":
            span_hits = _sequence_hits(tokens, pattern)
        elif pattern.type == "token":
            span_hits = _token_hits(tokens, pattern)
        else:  # wrap
            span_hits = _wrap_hits(tokens, pattern)
        for surface, parts in span_hits:
            hits.append(
                MweHit(
                    sentence=sent.text,
                    segment_id=pair.segment_id,
                    phenomenon_id=phenomenon_id,
                    surface=surface,
                    parts=parts,
                    unit_lemma=None,
                )
            )
    return hits


def mwe_hits(
    *,
    pairs: Sequence[AlignedPair],
    pack: PackSpec,
    nlp: Language,
    phenomena: tuple[Phenomenon, ...],
) -> tuple[MweHit, ...]:
    """Детерминированный предотбор по парам выравнивания E6 (ТЗ_03 S2.3).

    Порядок итерации — порядок карты выравнивания (стабилен между запусками —
    воспроизводимость S2-5). `phenomena` — таксономия языка-источника;
    явления без `patterns` детерминированно не отбираются (честный предел —
    CONTROL). segment_id заполняется при агрегации (вызовы с прямыми
    парами, напр. golden, идут с пустым segment_id)."""
    pattern_map: dict[str, tuple[Pattern, ...]] = {
        phenomenon.id: phenomenon.patterns for phenomenon in phenomena if phenomenon.patterns
    }
    if not pattern_map:
        return ()
    hits: list[MweHit] = []
    for pair in pairs:
        if not pair.source_text.strip():
            continue
        doc = nlp(pair.source_text)
        for phenomenon_id, patterns in pattern_map.items():
            for pattern in patterns:
                hits.extend(
                    _pattern_hits(
                        pattern, doc=doc, pair=pair, phenomenon_id=phenomenon_id, pack=pack
                    )
                )
    return tuple(hits)


@dataclass
class _MweAccum:
    """Накопление по кандидату (ключ: id + unit_lemma/нормализованная форма)."""

    first_hit: MweHit
    occurrences: int = 1
    segment_ids: tuple[str, ...] = ()


def select_mwe_candidates(
    *,
    book: Book,
    pairs: Sequence[AlignedPair],
    pack: PackSpec,
    nlp: Language,
    phenomena: tuple[Phenomenon, ...],
) -> tuple[MweCandidate, ...]:
    """Детерминированные кандидаты MWE (агрегация + фильтр повторяемости).

    Ключ агрегации: (phenomenon_id, unit_lemma) для отделяемых единиц,
    (phenomenon_id, нормализованная поверхность) для прочих (повторное
    вхождение — в существующего кандидата, как S1.2). `min_occurrences`
    шаблона-явления — жёсткий фильтр на всю книгу (широкие POS-шаблоны).
    Детерминированный тотальный порядок: (-вхождения, id, поверхность)."""
    seg_chapter: dict[str, str] = {}
    seg_text: dict[str, str] = {}
    for chapter in book.chapters:
        for seg in chapter.segments:
            seg_chapter[seg.segment_id] = chapter.chapter_id
            seg_text[seg.segment_id] = seg.plain_text
    phenomenon_map = {phenomenon.id: phenomenon for phenomenon in phenomena}

    accum: dict[tuple[str, str], _MweAccum] = {}
    for hit in mwe_hits(pairs=pairs, pack=pack, nlp=nlp, phenomena=phenomena):
        if hit.segment_id not in seg_chapter:
            continue  # сегмент вне книги — в кандидата не идёт (как S1)
        key = (
            hit.phenomenon_id,
            hit.unit_lemma or normalize_text(hit.surface, pack.normalization),
        )
        acc = accum.get(key)
        if acc is None:
            accum[key] = _MweAccum(
                first_hit=hit, segment_ids=(hit.segment_id,)
            )
        else:
            acc.occurrences += 1
            if hit.segment_id not in acc.segment_ids:
                acc.segment_ids = (*acc.segment_ids, hit.segment_id)

    candidates: list[MweCandidate] = []
    for (phenomenon_id, _key), acc in accum.items():
        phenomenon = phenomenon_map[phenomenon_id]
        min_occurrences = max(
            (p.min_occurrences for p in phenomenon.patterns), default=0
        )
        if acc.occurrences < min_occurrences:
            continue
        first = acc.first_hit
        candidates.append(
            MweCandidate(
                phenomenon_id=first.phenomenon_id,
                surface=first.surface,
                parts=first.parts,
                unit_lemma=first.unit_lemma,
                first_segment_id=first.segment_id,
                chapter_id=seg_chapter[first.segment_id],
                segment_ids=acc.segment_ids,
                occurrences=acc.occurrences,
                example_sentence=first.sentence,
                source_text_hash=hashlib.sha256(
                    segmenter.normalize_text(seg_text[first.segment_id]).encode("utf-8")
                ).hexdigest(),
            )
        )
    candidates.sort(
        key=lambda c: (-c.occurrences, c.phenomenon_id, c.surface.casefold())
    )
    return tuple(candidates)


# ------------------------------------------------------------------ валидация


def check_mwe_records(
    *,
    items: Sequence[Mapping[str, Any]],
    candidates: Mapping[str, MweCandidate],
    taxonomy: PhenomenaTaxonomy,
    seg_texts: Mapping[str, str],
    normalize: Callable[[str], str],
) -> tuple[list[tuple[Mapping[str, Any], MweCandidate]], list[dict[str, Any]]]:
    """Детерминированная валидация записей LLM (ТЗ_03 S2.6 + S2.5/S1.5):

    1) id типа — в таксономии языка (S2-1: 0 принятых вне таксономии);
    2) поверхностная форма — среди детерминированных кандидатов
       («подброшенное» отклоняется — механизм S1.5);
    3) все части конструкции — в указанном сегменте (`contains_form`, S2-3);
    4) отделяемая конструкция — обе части с позициями (S2-2/S2-5);
    5) канонический вид непуст (S2.6).

    `candidates` — карта «нормализованная поверхность → кандидат».
    """
    validated: list[tuple[Mapping[str, Any], MweCandidate]] = []
    rejected: list[dict[str, Any]] = []
    for item in items:
        surface = str(item.get("surface", ""))
        phenomenon_id = str(item.get("phenomenon_id", ""))
        canonical = str(item.get("canonical", ""))
        reasons: list[str] = []
        if not taxonomy.is_valid(phenomenon_id):
            reasons.append(f"id типа вне таксономии языка (S2.6): {phenomenon_id}")
        candidate = candidates.get(normalize(surface))
        if candidate is None:
            reasons.append(
                "подброшенная запись: поверхностная форма не среди "
                "детерминированных кандидатов (S2.6, механизм S1.5)"
            )
        else:
            segment_text = seg_texts.get(candidate.first_segment_id, "")
            for part in candidate.parts:
                if not contains_form(segment_text, part.text):
                    reasons.append(
                        f"часть конструкции вне сегмента (S2.6): {part.text!r}"
                    )
            if candidate.unit_lemma is not None and len(candidate.parts) < 2:
                reasons.append("разорванная конструкция без второй части (S2.5)")
        if not canonical.strip():
            reasons.append("канонический вид пуст (S2.6)")
        # candidate is None — всегда с причиной «подброшенная запись».
        if candidate is None or reasons:
            rejected.append(
                {"surface": surface, "phenomenon_id": phenomenon_id, "reasons": reasons}
            )
        else:
            validated.append((item, candidate))
    return validated, rejected


# ------------------------------------------------------------------ golden


def golden_metrics(
    *,
    candidates: Sequence[MweCandidate],
    golden: Sequence[MweGoldenEntry],
    segment_id_prefix: str = "golden-",
) -> tuple[int, int, int]:
    """Метрики S2-2/S2-4 на уровне пар (предложение, id) по ПРЕДОТБОРУ
    (кандидаты после фильтра повторяемости — повторяемость часть
    детерминированного предотбора, ТЗ_03 S2.3):

    - ``expected`` = {(i, exp.id)} по golden;
    - ``produced`` = {(i, candidate.phenomenon_id)}: кандидат считается
      «в предложении i», если сегмент ``f"{segment_id_prefix}{i}"`` среди
      его segment_ids и его поверхностная форма присутствует в тексте
      golden[i] (`contains_form`);
    - ``matched`` — пары, общие для обоих множеств. Сопоставление — по id
      типа, НЕ по unit_lemma: полнота S2-2 = обнаружение разорванной
      конструкции (обе части), а качество лемматизации — отдельный
      справочный показатель (`golden_unit_accuracy`, конвенция L1-11:
      ожидаемая lemma — по стандартной грамматике, не по выводу модели).

    Возвращает (len(expected), matched, len(produced)): recall =
    matched/expected, precision = matched/produced.
    """
    expected: set[tuple[int, str]] = set()
    for index, entry in enumerate(golden):
        for exp in entry.expected:
            expected.add((index, exp.id))
    produced: set[tuple[int, str]] = set()
    for candidate in candidates:
        for index, entry in enumerate(golden):
            if f"{segment_id_prefix}{index}" not in candidate.segment_ids:
                continue
            if not contains_form(entry.text, candidate.surface):
                continue
            produced.add((index, candidate.phenomenon_id))
    matched = len(expected & produced)
    return len(expected), matched, len(produced)


def golden_unit_accuracy(
    *,
    candidates: Sequence[MweCandidate],
    golden: Sequence[MweGoldenEntry],
    segment_id_prefix: str = "golden-",
) -> tuple[int, int]:
    """Справочное качество unit_lemma (конвенция L1-11): по найденным в
    golden-предложении кандидатам с ожидаемым unit — доля, где unit_lemma
    совпал с ожидаемым (по стандартной грамматике). Возвращает
    (checked, correct); checked = 0 — unit не ожидается нигде."""
    checked = 0
    correct = 0
    for index, entry in enumerate(golden):
        for exp in entry.expected:
            if exp.unit is None:
                continue
            for candidate in candidates:
                if (
                    candidate.phenomenon_id != exp.id
                    or f"{segment_id_prefix}{index}" not in candidate.segment_ids
                    or not contains_form(entry.text, candidate.surface)
                    or candidate.unit_lemma is None
                ):
                    continue
                checked += 1
                if candidate.unit_lemma == exp.unit:
                    correct += 1
    return checked, correct
