# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Строковое редактирование файлов конфигурации (R8, ТЗ §13).

`config.yaml` — единственный источник LLM-настроек; интерфейс правит
именно его **с сохранением комментариев** (ТЗ §13). ruamel.yaml не
вводится: правка — на уровне строк. В блоке `llm.profiles.<name>`
значения заменяются на месте (inline-комментарий строки сохраняется),
`extra_body` — замена целого вложенного блока, отсутствующее поле —
вставка в конец блока профиля. Остальной файл — побайтово нетронут.

`.env` — тот же принцип: строка `KEY=value` заменяется либо
дописывается в конец; остальные строки (комментарии, другие ключи)
не трогаются.

Оба редактора НЕ валидируют результат: сервис (`app.services.
llm_config`) прогоняет новый текст через `load_settings` до записи
(fail-closed, Р8-решение 5).
"""

from __future__ import annotations

import re
from typing import Any

import yaml

#: Правимые скалярные поля профиля (Р8 ТЗ §13 + MTPE M2 §6.2). `extra_body`
#: (блок) и `stop` (список — сериализуется как YAML flow) — отдельно.
_SCALAR_FIELDS = (
    "base_url",
    "model",
    "temperature",
    "retry_times",
    "concurrency",
    "timeout_seconds",
    "warmup_timeout_s",
    "top_k",
    "max_tokens_ratio",
    "max_segment_tokens",
)
#: Правимые списковые поля профиля (MTPE §6.2: `stop` у mt) — одна строка,
#: YAML flow-стиль.
_LIST_FIELDS = ("stop",)
#: Индентация в config.yaml (фиксирована схемой файла, Р8).
_LLM_KEY_RE = re.compile(r"^llm:\s*(?:#.*)?$")
_PROFILES_KEY_RE = re.compile(r"^  profiles:\s*(?:#.*)?$")
_PROFILE_KEY_RE = re.compile(r"^    (?P<name>[A-Za-z0-9_]+):\s*(?:#.*)?$")
_FIELD_LINE_RE = re.compile(r"^      (?P<field>[A-Za-z0-9_]+):\s*(?P<rest>.*)$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _find_profile_block(lines: list[str], name: str) -> tuple[int, int]:
    """Индексы (start, end) блока профиля: start — строка `    name:`,
    end — первая ПУСТАЯ/короткая строка после блока (не включается).

    Возврат ValueError — профиль не найден (fail-closed)."""
    start: int | None = None
    for idx, line in enumerate(lines):
        if start is None:
            match = _PROFILE_KEY_RE.match(line)
            if match is not None and match["name"] == name:
                start = idx
        else:
            stripped = line.strip()
            if stripped and _indent(line) <= 4:
                return start, idx
    if start is None:
        raise ValueError(f"профиль не найден в конфиге: {name}")
    return start, len(lines)


def _serialize_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return yaml.safe_dump(value, default_flow_style=True, allow_unicode=True).strip()
    return str(value)


def _replace_field_line(lines: list[str], idx: int, field: str, value: Any) -> None:
    """Строка `      field: <старое>[ # комментарий]` → новое значение,
    inline-комментарий сохраняется."""
    rest = lines[idx].split(":", 1)[1]
    # inline-комментарий: всё после пробельного `#` вне кавык (значения
    # правимых полей не котируются — URL/числа/имена моделей).
    hash_pos = rest.find(" #")
    comment = rest[hash_pos:] if hash_pos != -1 else ""
    lines[idx] = f"      {field}: {_serialize_value(value)}{comment}"


def _extra_body_block(value: dict[str, Any]) -> list[str]:
    """YAML-блок значения extra_body (индентация 8: под `extra_body:`)."""
    dumped = yaml.safe_dump(
        value, default_flow_style=False, sort_keys=True, allow_unicode=True
    ).splitlines()
    return [line + " " if line == "" else "        " + line for line in dumped]


def apply_llm_profiles(text: str, changes: dict[str, dict[str, Any]]) -> str:
    """Применить изменения профилей к тексту config.yaml.

    ``changes`` — ``{профиль: {поле: значение | None}}``; ``None`` — поле
    не менять. Ключ ``extra_body`` принимает ``dict`` (заменить блок) или
    ``None`` (не менять). Возврат — новый текст. ValueError — структура
    файла не распознана (fail-closed: запись не выполняется)."""
    lines = text.split("\n")
    # Порядок: с конца файла — индексы не сдвигаются при вставках.
    for name in sorted(changes, reverse=True):
        fields = changes[name]
        all_fields = _SCALAR_FIELDS + _LIST_FIELDS
        scalar = {f: fields[f] for f in all_fields if fields.get(f) is not None}
        extra_body = fields.get("extra_body")
        if not scalar and extra_body is None:
            continue
        start, end = _find_profile_block(lines, name)
        # extra_body — ЦЕЛЫЙ блок (строка + вложенные строки с indent > 6).
        if extra_body is not None:
            new_lines = _replace_extra_body(lines, start, end, extra_body)
            lines = new_lines
            start, end = _find_profile_block(lines, name)
        for field in reversed(all_fields):
            if field not in scalar:
                continue
            found: int | None = None
            for idx in range(start + 1, end):
                match = _FIELD_LINE_RE.match(lines[idx])
                if match is not None and match["field"] == field:
                    found = idx
                    break
            if found is not None:
                _replace_field_line(lines, found, field, scalar[field])
            else:
                lines.insert(end, f"      {field}: {_serialize_value(scalar[field])}")
    return "\n".join(lines)


def apply_translation_pipeline(text: str, pipeline: str) -> str:
    """Поставить/обновить `translation.pipeline` (MTPE §5.1/§6.1.3).

    Индентация секции — 2 пробела. Отсутствующее поле — вставка после
    строки `translation:`. ValueError — секция не найдена."""
    lines = text.split("\n")
    trans_start: int | None = None
    for idx, line in enumerate(lines):
        if line.startswith("translation:"):
            trans_start = idx
            break
    if trans_start is None:
        raise ValueError("секция translation не найдена в конфиге")
    for idx in range(trans_start + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped and _indent(lines[idx]) < 2:
            break  # конец секции translation
        if re.match(r"^  pipeline:", lines[idx]):
            lines[idx] = f"  pipeline: {pipeline}"
            return "\n".join(lines)
    lines.insert(trans_start + 1, f"  pipeline: {pipeline}")
    return "\n".join(lines)


def apply_ui_main_language(text: str, code: str) -> str:
    """Поставить/обновить `ui.main_language` (основной язык пользователя).

    Индентация секции — 2 пробела. Значение — код языка (простое, без
    кавык; пусто — `""`). Отсутствующее поле — вставка после строки
    `ui:`. ValueError — секция ui не найдена (fail-closed)."""
    lines = text.split("\n")
    ui_start: int | None = None
    for idx, line in enumerate(lines):
        if line.startswith("ui:"):
            ui_start = idx
            break
    if ui_start is None:
        raise ValueError("секция ui не найдена в конфиге")
    value = code if code else '""'
    for idx in range(ui_start + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped and _indent(lines[idx]) < 2:
            break  # конец секции ui
        if re.match(r"^  main_language:", lines[idx]):
            lines[idx] = f"  main_language: {value}"
            return "\n".join(lines)
    lines.insert(ui_start + 1, f"  main_language: {value}")
    return "\n".join(lines)


def _replace_extra_body(
    lines: list[str], start: int, end: int, value: dict[str, Any]
) -> list[str]:
    """Замена блока extra_body (строка `extra_body:` + вложенные)."""
    eb: int | None = None
    for idx in range(start + 1, end):
        match = _FIELD_LINE_RE.match(lines[idx])
        if match is not None and match["field"] == "extra_body":
            eb = idx
            break
    if eb is None:
        lines.insert(end, "      extra_body:")
        lines[end + 1 : end + 1] = _extra_body_block(value)
        return lines
    # Конец блока: первая строка с indent <= 6 (или end).
    stop = end
    for idx in range(eb + 1, end):
        stripped = lines[idx].strip()
        if stripped and _indent(lines[idx]) <= 6:
            stop = idx
            break
    lines[eb:stop] = ["      extra_body:", *_extra_body_block(value)]
    return lines


def apply_env_keys(text: str, keys: dict[str, str | None]) -> str:
    """Заменить/добавить строки `KEY=value` в тексте .env.

    ``keys`` — ``{ключ: значение | None}``; ``None`` — не менять.
    Значение пишется как есть (без кавык — формат существующего .env).
    Возврат — новый текст (конечный переводчик строки гарантирован)."""
    lines = text.split("\n")
    for key, value in keys.items():
        if value is None:
            continue
        line = f"{key}={value}"
        for idx, existing in enumerate(lines):
            if existing.startswith(f"{key}="):
                lines[idx] = line
                break
        else:
            if lines and lines[-1] == "":
                lines[-1] = line
            else:
                lines.append(line)
    return "\n".join(lines)
