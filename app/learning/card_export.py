"""S5 (ТЗ_03): экспорт карточек — CSV, формат для Anki, JSON.

Собственный формат не изобретается (S5.6): CSV — RFC 4180 (модуль `csv`);
Anki — стандартный текстовый импорт Anki (tab-separated, служебные строки
`#separator:Tab`/`#html:false`/`#columns:…`, C-escaping: `\\`, `\\n`,
`\\t`); JSON — полный массив объектов карточек.

Поля экспорта соответствуют `card_schema.json` языка (Rule 1): все
языковые поля (артикль de/nl, род fr/it, вспомогательные глаголы, формы)
имеют СВОИ колонки (столбцы — полный фиксированный набор на язык),
поэтому не теряются при выравнивании колонок (S5-6). Вложенные объекты
(например, de `main_forms`) раскладываются в подстолбцы `имя_поле`.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

#: Общие столбцы экспорта (присутствуют в карточке любого языка).
COMMON_COLUMNS: tuple[str, ...] = (
    "lemma",
    "word_form",
    "pos",
    "translation",
    "meaning",
    "example",
    "frequency_band",
    "cefr_level",
    "chapter_id",
    "segment_id",
)


def extra_columns(schema: Mapping[str, Any]) -> tuple[str, ...]:
    """Языковые столбцы — свойства `card_schema.json` (порядок схемы),
    без общих; вложенные объекты — подстолбцы `имя_поле` (порядок схемы)."""
    cols: list[str] = []
    for name, prop in schema.get("properties", {}).items():
        if name in COMMON_COLUMNS:
            continue
        if prop.get("type") == "object" and "properties" in prop:
            cols.extend(f"{name}_{sub}" for sub in prop["properties"])
        else:
            cols.append(name)
    return tuple(cols)


def export_columns(schema: Mapping[str, Any]) -> tuple[str, ...]:
    """Полный фиксированный набор столбцов: общие + языковые."""
    return COMMON_COLUMNS + extra_columns(schema)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v) for v in value)
    return str(value)


def card_to_row(card: Mapping[str, Any], schema: Mapping[str, Any]) -> dict[str, str]:
    """Карточка → строка экспорта (все столбцы набора; отсутствующее — "")."""
    row = {c: _cell(card.get(c)) for c in COMMON_COLUMNS}
    for name in schema.get("properties", {}):
        if name in COMMON_COLUMNS:
            continue
        value = card.get(name)
        if isinstance(value, Mapping):
            for sub, sub_value in value.items():
                row[f"{name}_{sub}"] = _cell(sub_value)
        else:
            row[name] = _cell(value)
    return row


def required_fields(schema: Mapping[str, Any], pos: str) -> set[str]:
    """Обязательные поля карточки для части речи `pos` (базовый `required`
    + `allOf` if/then по pos) — критерий S5-6."""
    required = set(schema.get("required", ()))
    for clause in schema.get("allOf", ()):
        condition = clause.get("if", {}).get("properties", {}).get("pos", {})
        if condition.get("const") == pos:
            required.update(clause.get("then", {}).get("required", ()))
    return required


def required_cells(schema: Mapping[str, Any], pos: str) -> set[str]:
    """Обязательные колонки экспорта для части речи `pos` (критерий S5-6):
    скалярные поля — по имени, обязательные объекты — подстолбцами
    `имя_поле` (кроме допускающих `null`)."""
    cells: set[str] = set()
    for name in required_fields(schema, pos):
        prop = schema.get("properties", {}).get(name, {})
        types = prop.get("type")
        if isinstance(types, list) and "null" in types:
            continue
        if prop.get("type") == "object" and "properties" in prop:
            for sub, sub_prop in prop["properties"].items():
                sub_types = sub_prop.get("type")
                if isinstance(sub_types, list) and "null" in sub_types:
                    continue
                cells.add(f"{name}_{sub}")
        else:
            cells.add(name)
    return cells


def nullable_fields(schema: Mapping[str, Any]) -> set[str]:
    """Поля, допускающие JSON `null` (type: [.., "null"]) — в экспорте
    могут быть пустыми, но КОЛОНКА/КЛЮЧ обязаны присутствовать."""
    out: set[str] = set()
    for name, prop in schema.get("properties", {}).items():
        types = prop.get("type")
        if isinstance(types, list) and "null" in types:
            out.add(name)
    return out


def _anki_escape(text: str) -> str:
    """Anki текстовый импорт: C-escaping (обратный слэш, перенос строки,
    таб). Порядок — сначала `\\`, иначе двойное экранирование."""
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")


def export_cards_csv(
    path: Path, cards: Sequence[Mapping[str, Any]], schema: Mapping[str, Any]
) -> int:
    """CSV (RFC 4180): заголовок — полный набор столбцов языка."""
    cols = export_columns(schema)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(cols)
        for card in cards:
            row = card_to_row(card, schema)
            writer.writerow([row.get(c, "") for c in cols])
    return len(cards)


def export_cards_anki(
    path: Path, cards: Sequence[Mapping[str, Any]], schema: Mapping[str, Any]
) -> int:
    """Формат для Anki (текстовый импорт): Front/Back + языковые столбцы
    (свои колонки — S5-6) + теги. Строки — tab-separated."""
    extra = extra_columns(schema)
    columns = ("Front", "Back", *extra, "Tags")
    lines = ["#separator:Tab", "#html:false", "#columns:" + "\t".join(columns)]
    for card in cards:
        row = card_to_row(card, schema)
        tags = (
            "epubtranslator pos=" + row["pos"]
            + " cefr=" + row["cefr_level"]
            + " band=" + row["frequency_band"]
            + " chapter=" + row["chapter_id"]
        )
        values = [row["word_form"], row["translation"], *(row.get(c, "") for c in extra), tags]
        lines.append("\t".join(_anki_escape(v) for v in values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(cards)


def export_cards_json(path: Path, cards: Sequence[Mapping[str, Any]]) -> int:
    """JSON: полный массив объектов карточек (все поля, без потерь)."""
    path.write_text(
        json.dumps(list(cards), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return len(cards)
