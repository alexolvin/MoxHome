"""Словарные карточки (L1.6): JSON-схема на язык (`card_schema.json`).

Состав обязательных полей зависит от языка и части речи (ТЗ L1 п.6, таблица):
de — артикль der/die/das + множественное (сущ.), вспомогательный haben/sein +
основные формы + отделяемость (гл.); nl — de/het + множественное +
уменьшительная (сущ.), hebben/zijn + причастие + отделяемость (гл.); fr/it —
род/группа спряжения/вспомогательный/неправильные формы; en — неправильные
формы глагола при наличии.

Карточка без обязательного поля НЕ выпускается: отклоняется и попадает в
QC-отчёт со счётчиком (`issue_cards` возвращает отклонённые с причинами).
Заполнение поля прочерком или пустой строкой ради прохождения схемы —
нарушение Rule 5: отдельная проверка плейсхолдеров (схемы для этого
недостаточно — `—` проходит `minLength: 1`).
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import jsonschema

from app.core.errors import ConfigurationError
from app.languages.registry import PackSpec

#: Значения-плейсхолдеры (Rule 5): заполнение «ради прохождения схемы».
#: null (JSON) — легитимное значение для nullable-полей (нет уменьшительной,
#: глагол регулярный) и плейсхолдером НЕ является.
_PLACEHOLDERS: frozenset[str] = frozenset(
    {"", "—", "-", "–", "n/a", "n.a.", "none", "null", "tbd", "?"}
)


def load_card_schema(pack_dir: Path, pack: PackSpec) -> dict[str, Any]:
    """JSON-схема карточки языка (путь — `pack.yaml: card_schema`). Нечитаемый/
    невалидный JSON — ConfigurationError (Rule 6): схема обязана работать."""
    path = pack_dir / pack.card_schema
    if not path.is_file():
        raise ConfigurationError(
            f"схема карточки языка не найдена: {path} (pack.yaml: card_schema)",
            details={"path": str(path)},
        )
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"схема карточки не является JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"схема карточки должна быть JSON-объектом: {path}")
    jsonschema.Draft202012Validator.check_schema(data)
    return data


def _iter_string_leaves(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """(путь, строка) по всем строковым листам карточки (рекурсивно)."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _iter_string_leaves(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            yield from _iter_string_leaves(item, f"{path}[{index}]")


def validate_card(
    card: Mapping[str, Any], schema: Mapping[str, Any]
) -> tuple[bool, tuple[str, ...]]:
    """Валидация карточки: JSON-схема (draft 2020-12) + правило плейсхолдеров
    (Rule 5). Возвращает (пройдена, причины)."""
    errors: list[str] = []
    validator = jsonschema.Draft202012Validator(schema)
    for error in sorted(validator.iter_errors(card), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"поле {where}: {error.message}")
    for where, value in _iter_string_leaves(card):
        if value.strip().lower() in _PLACEHOLDERS:
            errors.append(f"поле {where or '<root>'}: плейсхолдер вместо значения (Rule 5)")
    return (not errors), tuple(errors)


def issue_cards(
    cards: Sequence[Mapping[str, Any]], schema: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], tuple[str, ...]]]]:
    """Выпуск карточек: выпущенные + отклонённые (карточка, причины) — счётчик
    QC. Карточка без обязательного поля в «выпущенные» не попадает (L1-3..5)."""
    issued: list[dict[str, Any]] = []
    rejected: list[tuple[dict[str, Any], tuple[str, ...]]] = []
    for card in cards:
        ok, errors = validate_card(card, schema)
        if ok:
            issued.append(dict(card))
        else:
            rejected.append((dict(card), errors))
    return issued, rejected
