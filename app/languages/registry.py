"""Реестр языков (L0.1): `config/languages/<code>/pack.yaml` — язык это данные.

Реестр обнаруживается ПО КАТАЛОГУ: списка языков в коде Python нет (Rule 1).
Код — BCP-47 (`en`, `de`, `fr`, `nl`, `it`, `ru`). Пакет описывает ресурсы
языка (spaCy-модель, источник частотности, CEFR, фикстура); golden-набор —
отдельный файл `golden.yaml` в каталоге пакета.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.errors import ConfigurationError

#: BCP-47: основной тег 2–3 буквы, опционально скрипт (4) и регион (2–4).
_CODE_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z]{4})?(?:-[a-z]{2,4})?$")
#: CEFR-источник (L1.4): машиночитаемый лексикон либо вычисление из частотности.
_CEFR_SOURCE_RE = re.compile(r"^(lexicon:[a-z0-9_]+|derived)$")


class PackSpec(BaseModel):
    """Языковой пакет — данные о языке и его ресурсах (L0.1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    name: str = Field(min_length=1)
    #: Альтернативные коды (ISO 639-2 и пр.): eng→en, deu→de, … — данные, не код.
    aliases: tuple[str, ...] = ()
    #: Имя spaCy-пайплайна (L1). None — пайплайн не объявлен (целевой язык).
    spacy_model: str | None = None
    #: Источник частотности (L1: wordfreq, Zipf-шкала).
    frequency_source: str = Field(default="wordfreq", min_length=1)
    #: CEFR: `lexicon:<имя>` либо `derived` (L1.4; L1 проверяет лицензию).
    cefr_source: str = Field(default="derived")
    #: Фикстура исходного языка, относительно корня проекта (L0-6/L1).
    fixture: str | None = None

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str) -> str:
        if not _CODE_RE.match(value):
            raise ValueError(f"недопустимый BCP-47-код: {value!r}")
        return value

    @field_validator("aliases")
    @classmethod
    def _check_aliases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for alias in value:
            if not _CODE_RE.match(alias):
                raise ValueError(f"недопустимый BCP-47-алиас: {alias!r}")
        return value

    @field_validator("cefr_source")
    @classmethod
    def _check_cefr(cls, value: str) -> str:
        if not _CEFR_SOURCE_RE.match(value):
            raise ValueError(
                f"cefr_source: допустимо 'derived' или 'lexicon:<имя>': {value!r}"
            )
        return value


class GoldenEntry(BaseModel):
    """Предложение golden-набора (L0: семя; L1/T-L1-10: + размеченные явления)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    expected_language: str
    source: str = ""


def load_pack(path: Path) -> PackSpec:
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"не удалось разобрать языковой пакет {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"корень pack.yaml должен быть mapping: {path}")
    try:
        return PackSpec.model_validate(data)
    except Exception as exc:
        raise ConfigurationError(f"языковой пакет не прошёл валидацию: {path}: {exc}") from exc


def load_registry(root: Path) -> dict[str, PackSpec]:
    """Реестр: `config/languages/<code>/pack.yaml`. Каталог обнаруживается —
    списка языков в коде нет (Rule 1). Корень не найден/пуст — ошибка (Rule 6)."""
    if not root.is_dir():
        raise ConfigurationError(
            f"реестр языков не найден: {root} (config/languages/<code>/pack.yaml)",
            details={"root": str(root)},
        )
    registry: dict[str, PackSpec] = {}
    for code_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        pack_file = code_dir / "pack.yaml"
        if not pack_file.is_file():
            continue
        pack = load_pack(pack_file)
        if pack.code != code_dir.name:
            raise ConfigurationError(
                f"код пакета не совпадает с именем каталога: {pack.code} != {code_dir.name}",
                details={"pack": str(pack_file)},
            )
        if pack.code in registry:
            raise ConfigurationError(
                f"дублирующийся язык в реестре: {pack.code}", details={"path": str(pack_file)}
            )
        registry[pack.code] = pack
    if not registry:
        raise ConfigurationError(
            f"реестр языков пуст: {root} (нет ни одного pack.yaml)", details={"root": str(root)}
        )
    return registry


def load_golden(pack_dir: Path) -> tuple[GoldenEntry, ...]:
    """Golden-набор языка (`golden.yaml` рядом с pack.yaml).

    Файл отсутствует/пуст — пустой набор: это НЕ ошибка загрузки, а отсутствующий
    ресурс, который резолвер (L0.3) перечислит в недостающих.
    """
    path = pack_dir / "golden.yaml"
    if not path.is_file():
        return ()
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"не удалось разобрать golden-набор {path}: {exc}") from exc
    if not isinstance(data, list) or not data:
        return ()
    try:
        return tuple(GoldenEntry.model_validate(entry) for entry in data)
    except Exception as exc:
        raise ConfigurationError(f"golden-набор не прошёл валидацию: {path}: {exc}") from exc


def aliases_map(registry: dict[str, PackSpec]) -> dict[str, str]:
    """Канонический код для каждого кода/алиаса реестра (eng→en, deu→de, …)."""
    mapping: dict[str, str] = {}
    for code, pack in registry.items():
        mapping[code] = code
        for alias in pack.aliases:
            mapping.setdefault(alias.lower(), code)
    return mapping
