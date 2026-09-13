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
_PATTERN_TYPES: frozenset[str] = frozenset({"sequence", "wrap", "separable_units"})


@dataclass(frozen=True)
class PatternMarker:
    """Маркер wrap-шаблона (S2): точные словоформы (без регистра) и/или
    универсальный POS; задан хотя бы один критерий."""

    text: tuple[str, ...] = ()
    pos: str | None = None


@dataclass(frozen=True)
class Pattern:
    """Шаблон детерминированного отбора MWE (S2, ТЗ_03) — данные, не код.

    - ``sequence``: смежная POS-последовательность (2–3 позиции); ``texts`` —
      карта «позиция → разрешённые словоформы (без регистра)» (позиция без
      записи — любая словоформа; nl-er: «er» на позиции 0 перед глаголом);
    - ``wrap``: два маркера в пределах одного предложения (``start`` раньше
      ``end``); ``end_final`` — маркер ``end`` обязан быть последним
      содержательным токеном предложения;
    - ``separable_units``: механизм L1 `lexical_units` (отделяемые приставки —
      данные pack.yaml; одна единица на глагол).

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


@dataclass(frozen=True)
class Phenomenon:
    """Позиция таксономии: id — данные, не код (имя и описание — тоже данные).

    ``patterns`` — необязательные шаблоны S2 для детерминированного отбора;
    явление без шаблонов не отбирается детерминированно (честный предел
    механизма — в CONTROL)."""

    id: str
    kind: str
    name: str
    description: str
    patterns: tuple[Pattern, ...] = ()


def _parse_marker(data: Any, phenomenon_id: str, path: Path) -> PatternMarker:
    """Маркер wrap-шаблона: `text` (строка или список) и/или `pos`."""
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: маркер шаблона должен быть mapping"
        )
    unknown = set(data) - {"text", "pos"}
    if unknown:
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: неизвестные поля маркера: {sorted(unknown)}"
        )
    raw_text = data.get("text", ())
    if isinstance(raw_text, str):
        raw_text = (raw_text,)
    if not isinstance(raw_text, list) or not all(
        isinstance(word, str) and word.strip() for word in raw_text
    ):
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: маркер.text — строка или "
            f"список непустых строк"
        )
    pos = data.get("pos")
    if pos is not None and (not isinstance(pos, str) or pos not in _POS_UNIVERSAL):
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: маркер.pos — универсальный POS "
            f"(spaCy), а не {pos!r}"
        )
    if not raw_text and pos is None:
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: маркер без text и pos"
        )
    return PatternMarker(tuple(word.casefold() for word in raw_text), pos)


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
    }[ptype]
    unknown = set(data) - known
    if unknown:
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: шаблон {ptype} с неизвестными "
            f"полями: {sorted(unknown)}"
        )
    if ptype == "separable_units":
        return Pattern(type=ptype)
    if ptype == "sequence":
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
        parsed_texts: list[tuple[int, tuple[str, ...]]] = []
        for position, words in sorted(raw_texts.items()):
            if (
                isinstance(position, bool)
                or not isinstance(position, int)
                or not (0 <= position < len(pos))
            ):
                raise ConfigurationError(
                    f"таксономия {path}: {phenomenon_id}: sequence.texts — "
                    f"позиция вне диапазона 0..{len(pos) - 1}: {position!r}"
                )
            if not isinstance(words, list) or not all(
                isinstance(word, str) and word.strip() for word in words
            ):
                raise ConfigurationError(
                    f"таксономия {path}: {phenomenon_id}: sequence.texts[{position}] "
                    f"— список непустых строк"
                )
            parsed_texts.append((position, tuple(word.casefold() for word in words)))
        min_occurrences = data.get("min_occurrences", 0)
        if (
            isinstance(min_occurrences, bool)
            or not isinstance(min_occurrences, int)
            or min_occurrences < 0
        ):
            raise ConfigurationError(
                f"таксономия {path}: {phenomenon_id}: sequence.min_occurrences — "
                f"целое ≥ 0, а не {min_occurrences!r}"
            )
        return Pattern(
            type=ptype,
            pos=tuple(pos),
            texts=tuple(parsed_texts),
            min_occurrences=min_occurrences,
        )
    # wrap
    end_final = data.get("end_final", False)
    if not isinstance(end_final, bool):
        raise ConfigurationError(
            f"таксономия {path}: {phenomenon_id}: wrap.end_final — bool, "
            f"а не {end_final!r}"
        )
    return Pattern(
        type=ptype,
        start=_parse_marker(data.get("start"), phenomenon_id, path),
        end=_parse_marker(data.get("end"), phenomenon_id, path),
        end_final=end_final,
    )


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
        phenomenon_id = entry.get("id")
        kind = entry.get("kind")
        name = entry.get("name")
        description = entry.get("description")
        if not isinstance(phenomenon_id, str) or not _ID_RE.match(phenomenon_id):
            raise ConfigurationError(
                f"таксономия {path}: недопустимый идентификатор: {phenomenon_id!r}"
            )
        if phenomenon_id in seen:
            raise ConfigurationError(
                f"таксономия {path}: дублирующийся идентификатор: {phenomenon_id}"
            )
        if kind not in _KINDS:
            raise ConfigurationError(f"таксономия {path}: недопустимый kind: {kind!r}")
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError(f"таксономия {path}: пустое name у {phenomenon_id}")
        if not isinstance(description, str) or not description.strip():
            raise ConfigurationError(f"таксономия {path}: пустое description у {phenomenon_id}")
        raw_patterns = entry.get("patterns", [])
        if raw_patterns is None:
            raw_patterns = []
        if not isinstance(raw_patterns, list):
            raise ConfigurationError(
                f"таксономия {path}: {phenomenon_id}: patterns — список шаблонов S2"
            )
        patterns = tuple(_parse_pattern(p, phenomenon_id, path) for p in raw_patterns)
        seen.add(phenomenon_id)
        phenomena.append(Phenomenon(phenomenon_id, kind, name, description, patterns))
    return tuple(phenomena)


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
