"""Реестр языков (L0.1): `config/languages/<code>/pack.yaml` — язык это данные.

Реестр обнаруживается ПО КАТАЛОГУ: списка языков в коде Python нет (Rule 1).
Код — BCP-47 (`en`, `de`, `fr`, `nl`, `it`, `ru`). Пакет описывает ресурсы
языка (spaCy-модель, источник частотности, CEFR, фикстура); golden-набор —
отдельный файл `golden.yaml` в каталоге пакета.
"""

from __future__ import annotations

import re
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import ConfigurationError
from app.llm.tasks import all_tasks

#: BCP-47: основной тег 2–3 буквы, опционально скрипт (4) и регион (2–4).
_CODE_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z]{4})?(?:-[a-z]{2,4})?$")
#: CEFR-источник (L1.4): машиночитаемый лексикон либо вычисление из частотности.
_CEFR_SOURCE_RE = re.compile(r"^(lexicon:[a-z0-9_]+|derived)$")
#: Unicode-формы нормализации (pack.yaml: normalization.unicode_form).
_UNICODE_FORMS: frozenset[str] = frozenset({"NFC", "NFD", "NFKC", "NFKD"})
#: Универсальные POS (Universal Tags) — допустимое содержание pos_select.
_POS_TAGS: frozenset[str] = frozenset(
    {"NOUN", "VERB", "ADJ", "ADP", "ADV", "PROPN", "PRON", "DET", "AUX", "PART", "NUM", "X"}
)


class NormalizationSpec(BaseModel):
    """Правила нормализации языка (L1.1, pack.yaml) — данные, не код."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unicode_form: str = "NFC"
    casefold: bool = False
    collapse_whitespace: bool = True

    @field_validator("unicode_form")
    @classmethod
    def _check_form(cls, value: str) -> str:
        if value not in _UNICODE_FORMS:
            raise ValueError(f"недопустимая unicode-форма: {value!r}")
        return value


class SentenceSplitSpec(BaseModel):
    """Параметры разбиения на предложения (L1.1, pack.yaml) — данные, не код."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_chars: int = Field(default=10, ge=0)
    max_chars: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check_range(self) -> SentenceSplitSpec:
        if self.max_chars is not None and self.max_chars < self.min_chars:
            raise ValueError(
                f"sentence_split: max_chars ({self.max_chars}) < min_chars ({self.min_chars})"
            )
        return self


class BandSpec(BaseModel):
    """Частотная полоса (L1.3, pack.yaml): имя + верхняя граница Zipf-шкалы
    (включительно); `null` — без верхней границы (запасная последняя полоса)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    band: str = Field(min_length=1)
    zipf_max: float | None = Field(default=None, ge=0)


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
    #: JSON-схема словарной карточки (L1.6), относительно каталога пакета;
    #: обязательные поля по частям речи заданы в ней (единый источник, Rule 8).
    card_schema: str = "card_schema.json"
    #: Правила нормализации (L1.1).
    normalization: NormalizationSpec = Field(default_factory=NormalizationSpec)
    #: Параметры разбиения на предложения (L1.1).
    sentence_split: SentenceSplitSpec = Field(default_factory=SentenceSplitSpec)
    #: Пространство имён промптов: задачи анализа, для которых у языка должны
    #: резолвиться промпты (L1.1; резолвер пары итерует ИМЕННО их).
    prompt_tasks: tuple[str, ...] = ()
    #: POS, участвующие в отборе лексики (L1.1; S1).
    pos_select: tuple[str, ...] = ()
    #: Частотные полосы через Zipf-шкалу (L1.3): границы — данные, не код (L1-9).
    frequency_bands: tuple[BandSpec, ...] = ()
    #: Отделяемые/разделяемые приставки (L1.5; пусто — их в языке нет).
    separable_prefixes: tuple[str, ...] = ()
    #: Л2: правило кандидатов «слова с заглавной вне начала предложения»
    #: включено для языка ИСХОДНОГО текста. Для `de` выключено: в немецком все
    #: существительные пишутся с заглавной (ТЗ L2.1) — данные, не код (Rule 1).
    capitalized_candidates: bool = True
    #: Л2 (T): язык ЦЕЛЕВОЙ — флективный (парадигма нужна) или нет.
    #: Для `ru` — true (6 падежей × ед./мн. для существительных и имён
    #: собственных); для `en` — false, парадигма пустая (ТЗ L2.4).
    inflected: bool = False
    #: Л2 (T): список имён форм парадигмы по частям речи (порядок = порядок
    #: значений, которые возвращает LLM). Данные, не код (Rule 1).
    paradigm_forms: dict[str, tuple[str, ...]] = Field(default_factory=dict)

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
            raise ValueError(f"cefr_source: допустимо 'derived' или 'lexicon:<имя>': {value!r}")
        return value

    @field_validator("card_schema")
    @classmethod
    def _check_card_schema(cls, value: str) -> str:
        if not value or value.startswith("/") or ".." in value.split("/"):
            raise ValueError(f"card_schema: путь должен быть относительным: {value!r}")
        return value

    @field_validator("prompt_tasks")
    @classmethod
    def _check_prompt_tasks(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        known = {task.name for task in all_tasks()}
        for name in value:
            if name not in known:
                raise ValueError(f"prompt_tasks: неизвестная задача анализа: {name!r}")
        if len(set(value)) != len(value):
            raise ValueError(f"prompt_tasks: дублирующиеся задачи: {value!r}")
        return value

    @field_validator("pos_select")
    @classmethod
    def _check_pos_select(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for pos in value:
            if pos not in _POS_TAGS:
                raise ValueError(f"pos_select: недопустимый Universal POS: {pos!r}")
        if len(set(value)) != len(value):
            raise ValueError(f"pos_select: дублирующиеся POS: {value!r}")
        return value

    @field_validator("frequency_bands", mode="before")
    @classmethod
    def _bands_from_mapping(cls, value: Any) -> Any:
        """YAML-маппинг {имя полосы: граница Zipf, …, последняя: null} → список
        {band, zipf_max}. Границы — только в данных (pack.yaml), не в коде (L1-9)."""
        if isinstance(value, dict):
            return [{"band": band, "zipf_max": limit} for band, limit in value.items()]
        return value

    @field_validator("frequency_bands")
    @classmethod
    def _check_bands(cls, value: tuple[BandSpec, ...]) -> tuple[BandSpec, ...]:
        if len(value) < 2:
            raise ValueError("frequency_bands: нужно не менее двух полос")
        nulls = [band for band in value if band.zipf_max is None]
        if len(nulls) > 1:
            raise ValueError("frequency_bands: без верхней границы — не более одной полосы")
        if nulls and nulls[0] is not value[-1]:
            raise ValueError("frequency_bands: полоса без верхней границы — только последняя")
        bounds = [band.zipf_max for band in value if band.zipf_max is not None]
        if any(b2 <= b1 for b1, b2 in pairwise(bounds)):
            raise ValueError("frequency_bands: границы Zipf должны строго расти")
        return value

    @field_validator("separable_prefixes")
    @classmethod
    def _check_prefixes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        lowered = []
        for prefix in value:
            if not prefix or not prefix.strip().isalpha():
                raise ValueError(f"separable_prefixes: недопустимая приставка: {prefix!r}")
            lowered.append(prefix.strip().lower())
        if len(set(lowered)) != len(lowered):
            raise ValueError(f"separable_prefixes: дублирующиеся приставки: {value!r}")
        return tuple(lowered)

    @model_validator(mode="after")
    def _check_paradigm(self) -> PackSpec:
        """Согласованность inflected и paradigm_forms (Л2.4): флективный язык
        объявляет формы по частям речи, нефлективный — не должен (en)."""
        if not self.inflected and self.paradigm_forms:
            raise ValueError(
                "paradigm_forms не пуст, но inflected=false "
                f"(язык {self.code}: парадигма у нефлективного языка не объявляется)"
            )
        for pos, names in self.paradigm_forms.items():
            if not names:
                raise ValueError(f"paradigm_forms: пустой список форм для POS {pos!r}")
            if len(set(names)) != len(names):
                raise ValueError(
                    f"paradigm_forms: дублирующиеся имена форм для POS {pos!r}: {names!r}"
                )
        return self


class GoldenEntry(BaseModel):
    """Предложение golden-набора (L0: семя; L1/T-L1-10: + размеченные явления)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    expected_language: str
    source: str = ""
    #: Ожидаемые явления (L1/T-L1-10): идентификаторы из phenomena.yaml языка.
    phenomena: tuple[str, ...] = ()


class LemmaEntry(BaseModel):
    """Словоформа эталонного набора лемматизации (L1-2, T-L1-2).

    `lemma` — ожидаемый лемма по стандартной грамматике (НЕ вывод модели):
    набор — эталон, механизм измерения — пайплайн языка (nlp.lemma_of)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    form: str = Field(min_length=1)
    lemma: str = Field(min_length=1)


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


def load_lemma_reference(pack_dir: Path) -> tuple[LemmaEntry, ...]:
    """Эталонный набор словоформ (`lemma_reference.yaml` рядом с pack.yaml).

    Файл отсутствует/пуст — пустой набор: отсутствующий ресурс, который
    резолвер (L0.3) перечислит в недостающих (та же семантика, что golden).
    """
    path = pack_dir / "lemma_reference.yaml"
    if not path.is_file():
        return ()
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"не удалось разобрать эталонный набор {path}: {exc}") from exc
    if not isinstance(data, list) or not data:
        return ()
    try:
        return tuple(LemmaEntry.model_validate(entry) for entry in data)
    except Exception as exc:
        raise ConfigurationError(
            f"эталонный набор словоформ не прошёл валидацию: {path}: {exc}"
        ) from exc


def aliases_map(registry: dict[str, PackSpec]) -> dict[str, str]:
    """Канонический код для каждого кода/алиаса реестра (eng→en, deu→de, …)."""
    mapping: dict[str, str] = {}
    for code, pack in registry.items():
        mapping[code] = code
        for alias in pack.aliases:
            mapping.setdefault(alias.lower(), code)
    return mapping
