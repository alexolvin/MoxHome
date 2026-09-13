"""Таксономия грамматических явлений и типов MWE (L1.5): данные, не код.

Каждый язык объявляет собственный набор идентификаторов в
`config/languages/<code>/phenomena.yaml`. Жёсткое правило ТЗ L1 п.5: LLM
возвращает ТОЛЬКО идентификаторы из таксономии соответствующего языка;
идентификатор вне таксономии — ошибка валидации, артефакт отклоняется
(L1-6: 0 принятых). Свободная формулировка явления — только как пояснительный
текст при обязательном валидном идентификаторе (S1).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.errors import ConfigurationError

#: Идентификатор — стабильная строка: код языка (2–3 заглавные) + позиция.
_ID_RE = re.compile(r"^[A-Z]{2,3}(-[A-Z0-9]+)+$")
_KINDS: frozenset[str] = frozenset({"grammar", "mwe", "morphology"})
#: Универсальные (spaCy) классы части речи — допустимые значения шаблонов S2.
_POS_UNIVERSAL: frozenset[str] = frozenset(
    {
        "ADJ", "ADP", "ADV", "AUX", "CCONJ", "CONJ", "DET", "INTJ",
        "NOUN", "NUM", "PART", "PRON", "PUNCT", "SCONJ", "VERB", "X",
    }
)
_PATTERN_TYPES: frozenset[str] = frozenset({"sequence", "wrap", "separable_units", "token"})
#: Подстадии ТЗ_03, в которых явление участвует в детерминированном отборе
#: (значения поля `stages` — данные; без stage шаблон никуда не идёт —
#: Rule 6: опечатка в данных не должна молча включать/выключать шаблон).
STAGES: frozenset[str] = frozenset({"s2", "s3"})


@dataclass(frozen=True)
class PatternMarker:
    """Маркер wrap-шаблона (S2/S3): точные словоформы (без регистра) и/или
    универсальный POS (один или несколько) и/или универсальные морфологические
    признаки spaCy (Mood/Tense/Aspect, значения — данные); задан хотя бы
    один критерий."""

    text: tuple[str, ...] = ()
    pos: tuple[str, ...] = ()
    mood: tuple[str, ...] = ()
    tense: tuple[str, ...] = ()
    aspect: tuple[str, ...] = ()


@dataclass(frozen=True)
class TokenCriterion:
    """Критерий одного токена (шаблон `token`): И заданных полей, ИЛИ между
    критериями; задано хотя бы одно поле.

    - ``pos`` — универсальный POS (один или несколько, spaCy);
    - ``text`` — словоформа (без регистра);
    - ``mood``/``tense``/``aspect`` — универсальные морфологические признаки
      spaCy (Mood/Tense/Aspect; значения — данные, Rule 1: наклонение, время,
      вид — как просит ТЗ_03 S3.5);
    - ``position`` — индекс токена в предложении (считать с пунктуацией).
    """

    pos: tuple[str, ...] = ()
    text: tuple[str, ...] = ()
    mood: tuple[str, ...] = ()
    tense: tuple[str, ...] = ()
    aspect: tuple[str, ...] = ()
    position: int | None = None


@dataclass(frozen=True)
class Pattern:
    """Шаблон детерминированного отбора (S2/S3, ТЗ_03) — данные, не код.

    - ``sequence``: смежная POS-последовательность (2–3 позиции); ``texts`` —
      карта «позиция → разрешённые словоформы (без регистра)» (позиция без
      записи — любая словоформа; nl-er: «er» на позиции 0 перед глаголом);
    - ``wrap``: два маркера в пределах одного предложения (``start`` раньше
      ``end``); ``end_final`` — маркер ``end`` обязан быть последним
      содержательным токеном предложения;
    - ``separable_units``: механизм L1 `lexical_units` (отделяемые приставки —
      данные pack.yaml; одна единица на глагол);
    - ``token``: один токен по критериям (`TokenCriterion`, список — ИЛИ);
      одно срабатывание на токен, поверхность — сам токен (S3: наклонение,
      модальные частицы, фиксированные словоформы).

    ``min_occurrences`` — минимальная повторяемость кандидата на всю книгу
    (0 — без фильтра; применяется к широким POS-последовательностям).
    """

    type: str
    pos: tuple[str, ...] = ()
    #: sequence: позиция (0..len(pos)-1) → разрешённые словоформы (casefold).
    texts: tuple[tuple[int, tuple[str, ...]], ...] = ()
    start: PatternMarker | None = None
    end: PatternMarker | None = None
    end_final: bool = False
    min_occurrences: int = 0
    #: token: альтернативные критерии (ИЛИ между ними).
    criteria: tuple[TokenCriterion, ...] = ()


@dataclass(frozen=True)
class Phenomenon:
    """Позиция таксономии: id — данные, не код (имя и описание — тоже данные).

    ``patterns`` — необязательные шаблоны для детерминированного отбора;
    явление без шаблонов не отбирается детерминированно (честный предел
    механизма — в CONTROL). ``stages`` — подстадии (s2/s3), в которых
    явление отбирается: обязательное ВМЕСТЕ с ``patterns`` (оба поля или
    ни одного) — иначе новый шаблон молча попадёт в чужую подстадию."""

    id: str
    kind: str
    name: str
    description: str
    patterns: tuple[Pattern, ...] = ()
    stages: tuple[str, ...] = ()


def _parse_pos_list(data: Any, field: str, phenomenon_id: str, path: Path) -> tuple[str, ...]:
    """Универсальные POS (spaCy): строка или список строк из _POS_UNIVERSAL."""
    if isinstance(data, str):
        data = [data]
    if not isinstance(data, list) or not data or not all(
        isinstance(tag, str) and tag in _POS_UNIVERSAL for tag in data
    ):
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: {field} — один или несколько "
            f"универсальных POS (spaCy), а не {data!r}"
        )
    return tuple(data)


def _parse_morph_list(data: Any, field: str, phenomenon_id: str, path: Path) -> tuple[str, ...]:
    """Универсальное морфологическое значение (Mood/Tense/Aspect): строка
    или список непустых строк (значения spaCy — данные, Rule 1)."""
    if isinstance(data, str):
        data = [data]
    if not isinstance(data, list) or not data or not all(
        isinstance(value, str) and value.strip() for value in data
    ):
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: {field} — строка или "
            f"список непустых строк, а не {data!r}"
        )
    return tuple(data)


def _parse_word_list(data: Any, field: str, phenomenon_id: str, path: Path) -> tuple[str, ...]:
    """Словоформы (без регистра): строка или список непустых строк."""
    if isinstance(data, str):
        data = [data]
    if not isinstance(data, list) or not data or not all(
        isinstance(word, str) and word.strip() for word in data
    ):
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: {field} — строка или "
            f"список непустых строк, а не {data!r}"
        )
    return tuple(word.casefold() for word in data)


def _parse_nonneg_int(value: Any, field: str, phenomenon_id: str, path: Path) -> int:
    """Целое ≥ 0 (`position`, `min_occurrences`); bool не допускается
    (YAML 1.1 читает голые on/off как bool — Rule 6)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: {field} — целое ≥ 0, "
            f"а не {value!r}"
        )
    return value


_ListParser = Callable[[Any, str, str, Path], tuple[str, ...]]
_MARKER_PARSERS: dict[str, _ListParser] = {
    "text": _parse_word_list,
    "pos": _parse_pos_list,
    "mood": _parse_morph_list,
    "tense": _parse_morph_list,
    "aspect": _parse_morph_list,
}


def _parse_marker(data: Any, phenomenon_id: str, path: Path) -> PatternMarker:
    """Маркер wrap-шаблона: `text`, `pos` (один или несколько) и/или
    морфологические признаки `mood`/`tense`/`aspect`; задан хотя бы один."""
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: маркер шаблона должен быть mapping"
        )
    unknown = set(data) - set(_MARKER_PARSERS)
    if unknown:
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: неизвестные поля маркера: {sorted(unknown)}"
        )
    values: dict[str, tuple[str, ...]] = {}
    for field, parser in _MARKER_PARSERS.items():
        if field in data:
            values[field] = parser(data[field], f"маркер.{field}", phenomenon_id, path)
    if not values:
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: маркер без text/pos/mood/tense/aspect"
        )
    get = values.get
    return PatternMarker(
        text=get("text", ()),
        pos=get("pos", ()),
        mood=get("mood", ()),
        tense=get("tense", ()),
        aspect=get("aspect", ()),
    )


_CRITERION_PARSERS: dict[str, _ListParser] = {
    "pos": _parse_pos_list,
    "text": _parse_word_list,
    "mood": _parse_morph_list,
    "tense": _parse_morph_list,
    "aspect": _parse_morph_list,
}
_CRITERION_FIELDS = frozenset(_CRITERION_PARSERS) | {"position"}


def _parse_criterion(data: Any, phenomenon_id: str, path: Path) -> TokenCriterion:
    """Критерий шаблона `token`: И заданных полей (Rule 6: неизвестное поле —
    ошибка, опечатка не должна молча отключать критерий)."""
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: критерий шаблона должен быть mapping"
        )
    unknown = set(data) - _CRITERION_FIELDS
    if unknown:
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: неизвестные поля критерия: {sorted(unknown)}"
        )
    position: int | None = None
    if "position" in data:
        position = _parse_nonneg_int(
            data["position"], "критерий.position", phenomenon_id, path
        )
    values: dict[str, tuple[str, ...]] = {}
    for field, parser in _CRITERION_PARSERS.items():
        if field in data:
            values[field] = parser(data[field], f"критерий.{field}", phenomenon_id, path)
    if not values and position is None:
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: критерий без условий "
            f"(нужно хотя бы одно поле)"
        )
    get = values.get
    return TokenCriterion(
        pos=get("pos", ()),
        text=get("text", ()),
        mood=get("mood", ()),
        tense=get("tense", ()),
        aspect=get("aspect", ()),
        position=position,
    )


def _parse_pattern(data: Any, phenomenon_id: str, path: Path) -> Pattern:
    """Один шаблон отбора S2. Неизвестный тип/поле — ошибка (Rule 6:
    опечатка в данных не должна молча отключать шаблон)."""
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: шаблон должен быть mapping"
        )
    ptype = data.get("type")
    if ptype not in _PATTERN_TYPES:
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: неизвестный тип шаблона: {ptype!r}"
        )
    known = {
        "sequence": {"type", "pos", "texts", "min_occurrences"},
        "wrap": {"type", "start", "end", "end_final"},
        "separable_units": {"type"},
        "token": {"type", "criteria", "min_occurrences"},
    }[ptype]
    unknown = set(data) - known
    if unknown:
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: шаблон {ptype} с неизвестными "
            f"полями: {sorted(unknown)}"
        )
    if ptype == "separable_units":
        return Pattern(type=ptype)
    if ptype == "token":
        return _parse_token_pattern(data, phenomenon_id, path)
    if ptype == "sequence":
        return _parse_sequence_pattern(data, phenomenon_id, path)
    return _parse_wrap_pattern(data, phenomenon_id, path)


def _parse_token_pattern(data: dict[str, Any], phenomenon_id: str, path: Path) -> Pattern:
    raw_criteria = data.get("criteria")
    if not isinstance(raw_criteria, list) or not raw_criteria:
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: token.criteria — непустой "
            f"список критериев"
        )
    criteria = tuple(_parse_criterion(c, phenomenon_id, path) for c in raw_criteria)
    min_occurrences = _parse_nonneg_int(
        data.get("min_occurrences", 0), "token.min_occurrences", phenomenon_id, path
    )
    return Pattern(type="token", criteria=criteria, min_occurrences=min_occurrences)


def _parse_sequence_texts(
    raw_texts: dict[Any, Any], width: int, phenomenon_id: str, path: Path
) -> tuple[tuple[int, tuple[str, ...]], ...]:
    """sequence.texts: карта «позиция (0..width-1) → список словоформ»."""
    parsed_texts: list[tuple[int, tuple[str, ...]]] = []
    for position, words in sorted(raw_texts.items()):
        if (
            isinstance(position, bool)
            or not isinstance(position, int)
            or not (0 <= position < width)
        ):
            raise ConfigurationError(
                f"таксономия {path}: {phenomenon_id}: sequence.texts — "
                f"позиция вне диапазона 0..{width - 1}: {position!r}"
            )
        if not isinstance(words, list) or not all(
            isinstance(word, str) and word.strip() for word in words
        ):
            raise ConfigurationError(
                f"таксономия {path}: {phenomenon_id}: sequence.texts[{position}] "
                f"— список непустых строк"
            )
        parsed_texts.append((position, tuple(word.casefold() for word in words)))
    return tuple(parsed_texts)


def _parse_sequence_pattern(data: dict[str, Any], phenomenon_id: str, path: Path) -> Pattern:
    pos = data.get("pos")
    if not isinstance(pos, list) or not (2 <= len(pos) <= 3) or not all(
        isinstance(tag, str) and tag in _POS_UNIVERSAL for tag in pos
    ):
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: sequence.pos — список из 2–3 "
            f"универсальных POS (spaCy), а не {pos!r}"
        )
    raw_texts = data.get("texts", {})
    if not isinstance(raw_texts, dict):
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: sequence.texts — карта "
            f"«позиция → список словоформ»"
        )
    parsed_texts = _parse_sequence_texts(raw_texts, len(pos), phenomenon_id, path)
    min_occurrences = _parse_nonneg_int(
        data.get("min_occurrences", 0), "sequence.min_occurrences", phenomenon_id, path
    )
    return Pattern(
        type="sequence",
        pos=tuple(pos),
        texts=parsed_texts,
        min_occurrences=min_occurrences,
    )


def _parse_wrap_pattern(data: dict[str, Any], phenomenon_id: str, path: Path) -> Pattern:
    end_final = data.get("end_final", False)
    if not isinstance(end_final, bool):
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: wrap.end_final — bool, "
            f"а не {end_final!r}"
        )
    return Pattern(
        type="wrap",
        start=_parse_marker(data.get("start"), phenomenon_id, path),
        end=_parse_marker(data.get("end"), phenomenon_id, path),
        end_final=end_final,
    )


def _validate_phenomenon_fields(
    entry: dict[str, Any], path: Path
) -> tuple[str, str, str, str]:
    """Базовые поля записи (id/kind/name/description) — все проверки."""
    phenomenon_id = entry.get("id")
    kind = entry.get("kind")
    name = entry.get("name")
    description = entry.get("description")
    if not isinstance(phenomenon_id, str) or not _ID_RE.match(phenomenon_id):
        raise ConfigurationError(
            f"таксономия {path}: недопустимый идентификатор: {phenomenon_id!r}"
        )
    if kind not in _KINDS:
        raise ConfigurationError(f"таксономия {path}: недопустимый kind: {kind!r}")
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationError(f"таксономия {path}: пустое name у {phenomenon_id}")
    if not isinstance(description, str) or not description.strip():
        raise ConfigurationError(f"таксономия {path}: пустое description у {phenomenon_id}")
    return phenomenon_id, kind, name, description


def _parse_stages(
    raw_stages: Any, has_patterns: bool, phenomenon_id: str, path: Path
) -> tuple[str, ...]:
    """Поле `stages` (значения — данные из STAGES): список уникальных строк,
    задаётся ВМЕСТЕ с patterns (Rule 6: шаблон без stage молча никуда не
    попадает)."""
    if raw_stages is None:
        raw_stages = []
    if not isinstance(raw_stages, list) or not all(
        isinstance(stage, str) for stage in raw_stages
    ):
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: stages — список строк (s2/s3)"
        )
    stages = tuple(raw_stages)
    if len(set(stages)) != len(stages) or not set(stages) <= STAGES:
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: stages — уникальные значения "
            f"из {sorted(STAGES)}, а не {stages!r}"
        )
    if bool(stages) != has_patterns:
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: stages задаётся вместе с "
            f"patterns (оба поля присутствуют или оба отсутствуют)"
        )
    return stages


def load_phenomena(pack_dir: Path) -> tuple[Phenomenon, ...]:
    """`phenomena.yaml` каталога пакета. Файл отсутствует/пуст — пустой набор:
    это отсутствующий ресурс (резолвер L0.3 перечислит), а не ошибка загрузки —
    та же семантика, что у golden.yaml (L0)."""
    path = pack_dir / "phenomena.yaml"
    if not path.is_file():
        return ()
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"не удалось разобрать таксономию {path}: {exc}") from exc
    entries = data.get("phenomena") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        return ()
    seen: set[str] = set()
    phenomena: list[Phenomenon] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigurationError(f"таксономия {path}: запись должна быть mapping")
        phenomenon_id, kind, name, description = _validate_phenomenon_fields(
            entry, path
        )
        if phenomenon_id in seen:
            raise ConfigurationError(
                f"таксономия {path}: дублирующийся идентификатор: {phenomenon_id}"
            )
        raw_patterns = entry.get("patterns", [])
        if raw_patterns is None:
            raw_patterns = []
        if not isinstance(raw_patterns, list):
            raise ConfigurationError(
                f"таксономия {path}: {phenomenon_id}: patterns — список шаблонов S2"
            )
        patterns = tuple(_parse_pattern(p, phenomenon_id, path) for p in raw_patterns)
        stages = _parse_stages(
            entry.get("stages", []), bool(patterns), phenomenon_id, path
        )
        seen.add(phenomenon_id)
        phenomena.append(
            Phenomenon(phenomenon_id, kind, name, description, patterns, stages)
        )
    return tuple(phenomena)


def for_stage(phenomena: tuple[Phenomenon, ...], stage: str) -> tuple[Phenomenon, ...]:
    """Явления в скопе подстадии `stage` (поле `stages` — данные таксономии).

    Скоп — на стороне вызывающего: движок отбора (app/learning/mwe.py)
    агностичен к стадиям (Rule 8: один механизм для S2 и S3); шаблон без
    указания stage никуда не попадает (Rule 6)."""
    return tuple(p for p in phenomena if stage in p.stages)


class PhenomenaTaxonomy:
    """Таксономия одного языка: единственный арбитр допустимых идентификаторов."""

    def __init__(self, phenomena: tuple[Phenomenon, ...]) -> None:
        self._phenomena = phenomena
        self._ids = frozenset(phenomenon.id for phenomenon in phenomena)

    @property
    def phenomena(self) -> tuple[Phenomenon, ...]:
        return self._phenomena

    @property
    def ids(self) -> frozenset[str]:
        return self._ids

    def is_valid(self, phenomenon_id: str) -> bool:
        return phenomenon_id in self._ids

    def validate(
        self, labels: tuple[str, ...] | list[str]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """(валидные, невалидные). Невалидный идентификатор — ошибка валидации:
        артефакт отклоняется целиком (L1-6), а не «с обрезкой»."""
        valid = tuple(label for label in labels if label in self._ids)
        invalid = tuple(label for label in labels if label not in self._ids)
        return valid, invalid


def validate_golden(entries: tuple[Any, ...], taxonomy: PhenomenaTaxonomy) -> tuple[str, ...]:
    """Сверка golden-набора с таксономией (T-L1-10): все ожидаемые
    идентификаторы должны принадлежать таксономии. Возвращает ошибки."""
    errors: list[str] = []
    if not taxonomy.ids:
        return ("таксономия языка пуста — сверка невозможна",)
    for index, entry in enumerate(entries):
        for label in entry.phenomena:
            if not taxonomy.is_valid(label):
                errors.append(
                    f"golden[{index}] «{entry.text}»: идентификатор вне таксономии: {label}"
                )
    return tuple(errors)
