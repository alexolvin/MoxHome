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


@dataclass(frozen=True)
class Phenomenon:
    """Позиция таксономии: id — данные, не код (имя и описание — тоже данные)."""

    id: str
    kind: str
    name: str
    description: str


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
        seen.add(phenomenon_id)
        phenomena.append(Phenomenon(phenomenon_id, kind, name, description))
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
