# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Конфигурация приложения — ЕДИНСТВЕННЫЙ слой чтения .env и YAML (Rule 1).

Разделение: секреты (API-ключи) и адрес бинда — в .env; всё остальное
(включая адреса/модели LLM, E4.1) — в YAML. Прямое чтение os.environ вне
этого модуля запрещено.

Обязательные параметры без значений по умолчанию (E0.5, E4.1):
server.bind_address (.env), llm.profiles.*.base_url и llm.profiles.*.model (YAML),
storage.root (YAML). E4.1: адрес и модель LLM задаются ТОЛЬКО в YAML-профилях —
второго места не существует; в .env остаются только секреты (API-ключи).
Бинд на 0.0.0.0 — только при явном ALLOW_WILDCARD_BIND=true (E0.6).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.core.errors import ConfigurationError

#: Все переменные окружения, которые знает приложение (E4.1: только секреты
#: и адрес бинда; адреса/модели LLM — в YAML-профилях, не здесь).
ALL_ENV_KEYS: tuple[str, ...] = (
    "SERVER_BIND_ADDRESS",
    "SERVER_AUTH_SECRET",
    "ALLOW_WILDCARD_BIND",
    "LLM_API_KEY",
    "LLM_ANALYSIS_API_KEY",
    "LLM_MT_API_KEY",
    "LLM_POSTEDIT_API_KEY",
)
#: Обязательные переменные окружения (без них — отказ старта).
REQUIRED_ENV_KEYS: tuple[str, ...] = ("SERVER_BIND_ADDRESS",)
#: Имена чат-профилей LLM (E4, тракт T): движок, классический R8-экран,
#: cost/describe/health. Не язык/модель, а архитектура (Rule 1).
PROFILE_NAMES: tuple[str, ...] = ("translation", "fill", "analysis")
#: Профильные профили тракта M (MTPE TZ §4.1) — mt_pe-конвейер. Классический
#: R8-экран их НЕ управляет (экран M2), но они присутствуют в схеме.
MT_PE_PROFILES: tuple[str, ...] = ("mt", "postedit")
#: Все профили LLM в схеме (3 чат-профиля + 2 профильных) — строит load_settings.
ALL_PROFILE_NAMES: tuple[str, ...] = PROFILE_NAMES + MT_PE_PROFILES
#: Значение, требующее явного разрешения (E0.6). Константа спецификации ТЗ.
WILDCARD_ADDRESS = "0.0.0.0"


class LLMProfile(BaseModel):
    """Настройки одного LLM-профиля. ЕДИНСТВЕННЫЙ источник адреса и модели (E4.1).

    `base_url` и `model` задаются здесь (YAML) и нигде больше не задаются
    (Rule 1, E4.1; «второго места не существует»). Секреты (API-ключи) — в .env.
    Опциональные цены (E4.7): без них стоимость показывается как `Cost: unknown`.

    Пустые `base_url`/`model` допустимы (профиль «ещё не заполнен»); их
    заполненность для профиля, обязательного при текущем `translation.pipeline`,
    проверяет `validate_llm_settings` (TZ §5.3), а не `load_settings` (сервер
    стартует с полосой «Настройки LLM неполны», TZ §5.3 п.2).
    """

    model_config = ConfigDict(frozen=True)

    base_url: str = ""
    model: str = ""
    token_encoding: str = Field(default="cl100k_base", min_length=1)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=300.0, gt=0.0)
    retry_times: int = Field(default=3, ge=0)
    max_retries: int = Field(default=5, ge=1)
    concurrency: int = Field(default=1, ge=1)
    max_group_tokens: int = Field(default=2600, gt=0)
    input_price_per_mtok: float | None = Field(default=None, ge=0.0)
    output_price_per_mtok: float | None = Field(default=None, ge=0.0)
    #: Дополнительные поля тела запроса chat/completions (OpenAI-совместимый
    #: эндпоинт, расширенные параметры владения: vLLM, Ollama, ...). Пример:
    #: `chat_template_kwargs: {enable_thinking: false}` для thinking-моделей,
    #: чей reasoning-поток иначе попадает в контент ответа (D80).
    extra_body: dict[str, Any] = Field(default_factory=dict)
    #: Протокол запроса (MTPE TZ §5.1): `chat` (chat/completions) или
    #: `completions` (текстовый completions). Для существующих профилей — `chat`.
    api: str = "chat"
    #: Задача промпта в `llm.prompts_root` (mt: `mt_draft`; postedit: `postedit`).
    prompt_task: str | None = None
    #: Таймаут прогревочного запроса перед LLM-стадией/фазой (TZ §4.6).
    warmup_timeout_s: float = Field(default=900.0, gt=0.0)
    # --- Поля профиля mt (completions-черновик, TZ §5.1) ---
    # Числовые поля без range-ограничений в схеме: их границы — единая точка
    # validate_llm_settings V8 (TZ §5.3 «иных проверок этих полей в коде нет»).
    top_k: int | None = None
    max_tokens_ratio: float | None = None
    max_segment_tokens: int | None = None
    stop: list[str] = Field(default_factory=list)
    #: Транспортные повторы (mt/postedit, TZ §5.1); чат-профили — `retry_times`.
    retries: int | None = None
    timeout_s: float | None = None

    @property
    def cost_defined(self) -> bool:
        """Цена задана полностью (вход и выход) — иначе `Cost: unknown` (E4-6)."""
        return self.input_price_per_mtok is not None and self.output_price_per_mtok is not None


class LLMSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: Секрет профилей translation/fill (LLM_API_KEY в .env).
    api_key: str = ""
    #: Секрет профиля analysis (LLM_ANALYSIS_API_KEY в .env); пусто → api_key.
    analysis_api_key: str = ""
    #: Секрет профиля mt (LLM_MT_API_KEY в .env); пусто → api_key (TZ §5.2).
    mt_api_key: str = ""
    #: Секрет профиля postedit (LLM_POSTEDIT_API_KEY в .env); пусто → api_key.
    postedit_api_key: str = ""
    profiles: dict[str, LLMProfile]
    #: Корень внешних промптов (E4.5), относительно storage.root:
    #: prompts/<task>/<source>/<target>/vN.yaml.
    prompts_root: str = Field(default="prompts", min_length=1)
    #: Таймаут кнопки «Проверить» на экране «LLM и модели», с (TZ §5.1).
    probe_timeout_s: float = Field(default=300.0, gt=0.0)

    def profile(self, name: str) -> LLMProfile:
        try:
            return self.profiles[name]
        except KeyError as exc:
            raise ConfigurationError(f"LLM-профиль не найден: {name}") from exc

    def api_key_for(self, name: str) -> str:
        """API-ключ профиля: analysis/mt/postedit могут иметь собственные."""
        if name == "analysis":
            return self.analysis_api_key or self.api_key
        if name == "mt":
            return self.mt_api_key or self.api_key
        if name == "postedit":
            return self.postedit_api_key or self.api_key
        return self.api_key

    def endpoint(self, name: str) -> tuple[str, str, str]:
        """(base_url, model, api_key) профиля — адрес и модель только из YAML (E4.1)."""
        p = self.profile(name)
        return p.base_url, p.model, self.api_key_for(name)


class ServerSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    bind_address: str = Field(min_length=1)
    port: int = Field(default=8321, ge=1, le=65535)
    allow_wildcard_bind: bool = False
    auth_secret: str = ""
    heartbeat_seconds: float = Field(default=15.0, gt=0.0)
    #: ≤ 2× heartbeat: осиротевшее задание обнаруживается за ≤ 2 периодов heartbeat (E3-1).
    lease_seconds: float = Field(default=30.0, gt=0.0)


class StorageDirs(BaseModel):
    model_config = ConfigDict(frozen=True)

    input: str = "input"
    working: str = "working"
    output: str = "output"
    jobs: str = "jobs"
    cache: str = "cache"
    logs: str = "logs"
    backups: str = "backups"
    books: str = "books"
    data: str = "data"


class StorageSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: Пустое значение root отклоняется в load_settings (обязательный параметр).
    root: Path
    busy_timeout_ms: int = Field(default=5000, ge=0)
    dirs: StorageDirs = Field(default_factory=StorageDirs)


class LimitsSection(BaseModel):
    """Пороги и лимиты — только конфигурация, не литералы (Rule 1)."""

    model_config = ConfigDict(frozen=True)

    vocabulary_max_cards_per_1000_words: float = Field(default=15.0, gt=0.0)
    grammar_max_notes_per_chapter: int = Field(default=10, ge=1)
    glossary_max_user_prompt_chars: int = Field(default=8000, gt=0)
    #: S1 (ТЗ_03): размер батча LLM-вызова заполнения карточек (глава →
    #: батчи; та же роль, что glossary.annotation_batch_size, для стадии S1).
    vocabulary_llm_batch: int = Field(default=25, gt=0)
    #: S2 (ТЗ_03): размер батча LLM-вызова объяснения MWE-кандидатов
    #: (глава → батчи; та же роль, что vocabulary_llm_batch, для стадии S2).
    mwe_llm_batch: int = Field(default=25, gt=0)
    #: S3 (ТЗ_03): размер батча LLM-вызова объяснения грамматических
    #: кандидатов (глава → батчи; та же роль, что vocabulary_llm_batch,
    #: для стадии S3).
    grammar_llm_batch: int = Field(default=25, gt=0)
    #: S3 (ТЗ_03): лимит длины пояснения грамматической заметки (текст на
    #: целевом языке); превышение — отклонение заметки, НЕ тихая усечка
    #: (S3.1/T-S3-10).
    grammar_note_max_chars: int = Field(default=300, gt=0)
    #: R6 (TZ-R §8): максимум размера загружаемого файла, МБ — жёсткий
    #: отказ импорта «файл больше limits.upload_max_mb».
    upload_max_mb: int = Field(default=50, ge=1)
    #: S1 (ТЗ_03): «вне CEFR-диапазона пользователя» — дистанция уровней
    #: (A1..C2) >= этого значения. Диапазон — не абсолютный фильтр: материал
    #: допускается с меткой cefr_out_of_range (ТЗ_03 общее требование 3).
    cefr_out_of_range_distance: int = Field(default=2, ge=1)
    #: S4 (ТЗ_03 S4.3): состав типов упражнений главы — конфигурация; тип
    #: вне списка отклоняется детерминированной валидацией.
    exercises_types: tuple[str, ...] = (
        "mc", "tf", "gap", "translation", "vocab", "grammar", "free"
    )

    @field_validator("exercises_types")
    @classmethod
    def _check_exercises_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        allowed = ("mc", "tf", "gap", "translation", "vocab", "grammar", "free")
        unknown = [t for t in value if t not in allowed]
        if unknown:
            raise ValueError(
                f"limits.exercises_types: неизвестные типы {unknown}; "
                f"допустимы {list(allowed)}"
            )
        if not value:
            raise ValueError("limits.exercises_types: пустой список недопустим")
        return value


class GlossarySection(BaseModel):
    """Глоссарий книги (L2): параметры детерминированного отбора кандидатов
    и проверки согласованности. Значения — данные конфигурации (Rule 1);
    включение — опционально (по умолчанию выключено: поведение E-стадий без
    глоссария не меняется, D60)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    #: Минимальное число вхождений для кандидата (все правила, L2.1).
    min_occurrences: int = Field(default=2, ge=1)
    #: Длина многословных последовательностей (n-грамм), кандидата в глоссарий.
    ngram_min: int = Field(default=2, ge=2)
    ngram_max: int = Field(default=4, ge=2)
    #: Кандидат длиннее — отбрасывается (NER-сущности могут быть длинными).
    max_term_chars: int = Field(default=80, ge=1)
    #: Единицы измерения (L2.1): список — данные, не код.
    units: tuple[str, ...] = (
        "kg", "g", "mg", "l", "ml", "m", "km", "cm", "mm",
        "km/h", "m/s", "°C", "%", "w", "kw", "kw·h", "a", "v", "hz",
    )
    #: Объём текстовой пробы для определения стилевого профиля (L2.7).
    style_sample_chars: int = Field(default=5000, ge=100)
    #: Кандидатов на один вызов LLM (glossary_annotation, L2.2).
    annotation_batch_size: int = Field(default=25, ge=1)
    #: Доля согласованных вхождений терминов (L2-4): ниже — предупреждение QC.
    consistency_min_share: float = Field(default=0.95, gt=0.0, le=1.0)
    #: L3: автоматическая правка расхождений в выходном EPUB. По умолчанию
    #: ВЫКЛЮЧЕНА (T-L3-10): расхождения при этом обнаруживаются и сообщаются
    #: (L2.6) — тихого отключения обнаружения нет.
    fix_enabled: bool = False
    #: L3-6: максимальная доля сегментов, правимых за один прогон. Превышение
    #: — системная проблема: задание останавливается с отчётом (fail closed).
    fix_max_segment_share: float = Field(default=0.5, gt=0.0, le=1.0)
    #: L3.4: порог различия числа предложений в переписанном сегменте.
    fix_sentence_delta: int = Field(default=1, ge=0)

    @model_validator(mode="after")
    def _check_ngram_range(self) -> GlossarySection:
        if self.ngram_max < self.ngram_min:
            raise ValueError(
                f"glossary.ngram_max ({self.ngram_max}) < ngram_min ({self.ngram_min})"
            )
        if self.ngram_max > 8:
            raise ValueError(f"glossary.ngram_max слишком велик: {self.ngram_max}")
        return self


class AlignmentSection(BaseModel):
    """Пороги и критерии выравнивания оригинал ↔ перевод (E6)."""

    model_config = ConfigDict(frozen=True)

    #: Ниже порога — задание `COMPLETED_WITH_WARNINGS` (E6.5); учебные этапы
    #: работают только по выровненному подмножеству и указывают охват.
    min_coverage: float = Field(default=0.995, gt=0.0, le=1.0)
    #: Критерий белого списка «идентичен оригиналу» (E6.3): перевод,
    #: совпадающий с оригиналом, нарушением НЕ является, если нормализованный
    #: текст целиком соответствует шаблону (числа, имена собственные без
    #: склонения, декоративная пунктуация разделителей «* * *» и т.п. —
    #: настраиваемый критерий).
    identical_whitelist_pattern: str = Field(
        default=(
            r"^[\d\s.,;:()\-\u2013\u2014/%$€"
            r"\*\u00b7\u2022\u2026\u2020\u2021"
            r"\u201c\u201d\u2018\u2019\u201e\u00ab\u00bb]+$"
        ),
        min_length=1,
    )

    @field_validator("identical_whitelist_pattern")
    @classmethod
    def _check_whitelist_pattern(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(
                "alignment.identical_whitelist_pattern: некорректный регулярный "
                f"шаблон: {exc}"
            ) from exc
        return value

    @property
    def identical_whitelist(self) -> re.Pattern[str]:
        """Скомпилированный шаблон (fullmatch по нормализованному тексту)."""
        return re.compile(self.identical_whitelist_pattern)


class SegmentationSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: Блочные элементы-сегменты (E2.2) — настраиваемое множество.
    block_tags: list[str] = Field(
        default_factory=lambda: [
            "p", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt", "td",
        ]
    )

    @field_validator("block_tags")
    @classmethod
    def _check_tags(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("block_tags не может быть пустым")
        return value


class LanguageDetectionSection(BaseModel):
    """Детерминированное определение исходного языка (L0.4, D7): объём/метод выборки и пороги."""

    model_config = ConfigDict(frozen=True)

    sample_segments: int = Field(default=200, gt=0)
    min_confidence: float = Field(default=0.6, gt=0.0, le=1.0)
    #: Сид детектора (D7: детерминированность — результат воспроизводим).
    seed: int = 42
    #: Многоязычная книга (L0.6): порог доли сегментов с другим языком;
    #: превышение → факт в отчёте и пометки сегментов.
    multilingual_share: float = Field(default=0.2, gt=0.0, le=1.0)
    #: Минимальная длина сегмента (символы) для пометки: короче — детектор ненадёжен.
    segment_min_chars: int = Field(default=40, ge=0)


class LanguagesSection(BaseModel):
    """Языковой слой (L0.1/L0.2): реестр и матрица — данные, а не код (Rule 1)."""

    model_config = ConfigDict(frozen=True)

    #: Корень реестра (L0.1): config/languages/<code>/pack.yaml; относительно storage.root.
    root: str = Field(default="config/languages", min_length=1)
    #: Матрица пар (L0.2); относительно storage.root.
    matrix: str = Field(default="config/language_matrix.yaml", min_length=1)


class EpubcheckSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    jar_path: str = Field(default="data/tools/epubcheck.jar", min_length=1)
    timeout_seconds: float = Field(default=300.0, gt=0.0)


class ConverterSection(BaseModel):
    """Инструмент конвертации (TZ-R §8, R6): Calibre ``ebook-convert`` в
    Podman-контейнере без сети (D110). Пустое ``binary`` — конвертер
    недоступен: импорт только EPUB + явная строка в UI (TZ §8)."""

    model_config = ConfigDict(frozen=True)

    #: Абсолютный путь к обёртке (D110); пустое значение — не задан.
    binary: str = ""
    #: Жёсткий таймаут конвертации при импорте, с (TZ §8).
    timeout_import_seconds: float = Field(default=120.0, gt=0.0)
    #: Жёсткий таймаут конвертации при экспорте, с (R7).
    timeout_export_seconds: float = Field(default=180.0, gt=0.0)
    #: Принимаемые расширения (TZ-R §15, 16 шт.) — данные, не код.
    accept: tuple[str, ...] = (
        "epub", "fb2", "mobi", "azw", "azw3", "docx", "odt", "rtf",
        "txt", "md", "html", "htmlz", "lit", "pdb", "chm", "pdf",
    )

    @field_validator("accept")
    @classmethod
    def _check_accept(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(v.strip().lstrip(".").lower() for v in value)
        if "epub" not in normalized:
            raise ValueError("converter.accept: отсутствует обязательный формат 'epub'")
        if len(set(normalized)) != len(normalized):
            raise ValueError("converter.accept: дубликаты форматов")
        return normalized


class TranslationSection(BaseModel):
    """Режимы перевода задания (E5.2) + способ перевода (MTPE).

    `submit_mode_bilingual` — движковый режим режима bilingual: по умолчанию
    APPEND_BLOCK; APPEND_TEXT доступен настройкой, но не используется по
    умолчанию (ухудшает последующее выравнивание, ТЗ E5 п.2).

    `pipeline` — способ перевода (MTPE TZ §5.1): `single` (движок) или `mt_pe`
    (черновик MT + постредактура). Меняется только экраном «LLM и модели»;
    в теле `POST /api/jobs` поле `pipeline` запрещено (M1-8).
    """

    model_config = ConfigDict(frozen=True)

    submit_mode_bilingual: str = Field(default="APPEND_BLOCK")
    #: Способ перевода задания (TZ §5.1): single | mt_pe. Проверка значения —
    #: validate_llm_settings V1 (единая точка, TZ §5.3).
    pipeline: str = "single"

    @field_validator("submit_mode_bilingual")
    @classmethod
    def _check_submit_mode(cls, value: str) -> str:
        if value not in ("APPEND_BLOCK", "APPEND_TEXT"):
            raise ValueError(
                "translation.submit_mode_bilingual: допустимы APPEND_BLOCK | APPEND_TEXT"
            )
        return value


class MTPESection(BaseModel):
    """Параметры конвейера `mt_pe` (TZ §5.1) — только конфигурация (Rule 1).

    Пороги флага heavy_edit, доли остановок и период системных проверок
    фазы постредактуры. Значения — данные, не код. Числовые поля без
    range-ограничений в схеме: их границы — единая точка validate_llm_settings
    V8 (TZ §5.3 «иных проверок этих полей в коде нет»)."""

    model_config = ConfigDict(frozen=True)

    #: Число предыдущих сегментов главы в контексте постредактуры.
    context_segments: int = 6
    #: Коротче (слов) — язык сегмента не проверяется.
    lang_check_min_words: int = 4
    #: Порог флага heavy_edit (доля заменённых токенов).
    max_edit_ratio: float = 0.40
    #: Доля heavy_edit-сегментов, выше — системная остановка.
    max_heavy_edit_ratio: float = 0.10
    #: Допустимая разница числа предложений между черновиком и итогом.
    max_sentence_delta: int = 1
    #: Доля pe_rejected-сегментов, выше — системная остановка.
    max_rejected_ratio: float = 0.005
    #: Период проверки системных остановок, сегментов.
    check_every_segments: int = 200


class UiSection(BaseModel):
    """Параметры Web-интерфейса (U3, серия R). Значения — только
    конфигурация (Rule 1): веб-код, шаблоны и app.js числовых дефолтов не
    содержат — все читают ``settings.ui.*``. Дефолты живут в этой
    YAML-схеме (единое место); секция ui: в config.yaml задана явно
    (зеркало — config.example.yaml)."""

    model_config = ConfigDict(frozen=True)

    #: Размер страницы списка, записей (ТЗ-R §3: 20).
    page_size: int = Field(default=20, ge=1)
    #: Интервал автообновления, с, активная вкладка (ТЗ-R §3: 3).
    refresh_seconds: float = Field(default=3.0, gt=0.0)
    #: Интервал автообновления, с, вкладка в фоне (ТЗ-R §3: 15;
    #: переключение по visibilitychange).
    refresh_seconds_max: float = Field(default=15.0, gt=0.0)
    #: Число строк tail логов по умолчанию (U3 3.8).
    log_tail_lines: int = Field(default=200, ge=1)
    #: Минимальный возраст задания (дни) для массовой очистки purge (U3 3.5.24).
    purge_min_age_days: float = Field(default=1.0, ge=0.0)
    #: Брейкпоинт «телефон/шире», px (ТЗ-R §3): десктоп — от этой ширины
    #: (≥ 641); телефон — уже.
    mobile_breakpoint_px: int = Field(default=641, ge=1)
    #: Минимальная тап-цель, px (ТЗ-R §3, §14).
    tap_target_px: int = Field(default=44, ge=1)
    #: Минимальный зазор между тап-целями, px (ТЗ-R §3).
    tap_gap_px: int = Field(default=8, ge=0)
    #: Высота строки списка, px (ТЗ-R §3).
    row_height_px: int = Field(default=64, ge=1)
    #: Высота таб-бара, px, плюс env(safe-area-inset-bottom) (ТЗ-R §3).
    tabbar_height_px: int = Field(default=44, ge=1)
    #: Кегль полей ввода, px (ТЗ-R §3: 16 — запрет зума iOS/Android).
    input_font_px: int = Field(default=16, ge=1)
    #: Время жизни экспорт-артефактов, ч (ТЗ-R §9; плановая очистка).
    export_ttl_hours: float = Field(default=48.0, gt=0.0)
    #: Витрина компонентов /dev/kit (ТЗ V §7.9): доступна только при true,
    #: иначе 404. По умолчанию false (не боевая страница).
    dev_kit: bool = Field(default=False)
    #: Основной язык пользователя: целевой язык по умолчанию при импорте
    #: книги. Пусто — не задан (целевой при импорте не предзаполняется).
    main_language: str = ""

    @model_validator(mode="after")
    def _check_refresh_bounds(self) -> UiSection:
        if self.refresh_seconds_max < self.refresh_seconds:
            raise ValueError(
                "ui.refresh_seconds_max должен быть ≥ ui.refresh_seconds"
            )
        return self


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    server: ServerSection
    llm: LLMSection
    storage: StorageSection
    limits: LimitsSection
    glossary: GlossarySection = Field(default_factory=GlossarySection)
    alignment: AlignmentSection
    segmentation: SegmentationSection
    language_detection: LanguageDetectionSection
    languages: LanguagesSection
    epubcheck: EpubcheckSection
    converter: ConverterSection
    translation: TranslationSection
    #: Параметры конвейера mt_pe (MTPE TZ §5.1); опциональная секция YAML.
    mtpe: MTPESection = Field(default_factory=MTPESection)
    #: Параметры Web-интерфейса (U3); опциональная секция YAML (дефолты в схеме).
    ui: UiSection = Field(default_factory=UiSection)

    @model_validator(mode="after")
    def _check_lease_heartbeat(self) -> Settings:
        if self.server.lease_seconds > 2 * self.server.heartbeat_seconds:
            raise ValueError(
                "server.lease_seconds должен быть ≤ 2× server.heartbeat_seconds "
                "(E3-1: осиротевшее задание обнаруживается не позже чем за 2 периода heartbeat)"
            )
        return self

    @property
    def input_dir(self) -> Path:
        return self.storage.root / self.storage.dirs.input

    @property
    def working_dir(self) -> Path:
        return self.storage.root / self.storage.dirs.working

    @property
    def output_dir(self) -> Path:
        return self.storage.root / self.storage.dirs.output

    @property
    def jobs_dir(self) -> Path:
        return self.storage.root / self.storage.dirs.jobs

    def job_dir(self, job_id: str) -> Path:
        """Каталог задания — единый источник формулы (V1.1/V1.3)."""
        return self.jobs_dir / job_id

    @property
    def jobs_db_path(self) -> Path:
        return self.jobs_dir / "jobs.db"

    @property
    def cache_dir(self) -> Path:
        return self.storage.root / self.storage.dirs.cache

    @property
    def logs_dir(self) -> Path:
        return self.storage.root / self.storage.dirs.logs

    @property
    def backups_dir(self) -> Path:
        return self.storage.root / self.storage.dirs.backups

    @property
    def books_dir(self) -> Path:
        return self.storage.root / self.storage.dirs.books

    @property
    def data_dir(self) -> Path:
        return self.storage.root / self.storage.dirs.data

    @property
    def prompts_dir(self) -> Path:
        """Корень внешних промптов (E4.5, D62): <storage.root>/<llm.prompts_root>;
        абсолютный llm.prompts_root — как есть."""
        return self.storage.root / self.llm.prompts_root

    @property
    def languages_root(self) -> Path:
        """Корень реестра языков (L0.1): <storage.root>/<languages.root>, если не абсолютный."""
        path = Path(self.languages.root)
        return path if path.is_absolute() else self.storage.root / path

    @property
    def language_matrix_path(self) -> Path:
        """Матрица пар (L0.2): <storage.root>/<languages.matrix>, если не абсолютный."""
        path = Path(self.languages.matrix)
        return path if path.is_absolute() else self.storage.root / path

    @property
    def epubcheck_jar_path(self) -> Path:
        """Путь к EPUBCheck-jar (E5): относительно storage.root, если не абсолютный."""
        path = Path(self.epubcheck.jar_path)
        return path if path.is_absolute() else self.storage.root / path

    def all_data_dirs(self) -> list[Path]:
        return [
            self.input_dir,
            self.working_dir,
            self.output_dir,
            self.jobs_dir,
            self.cache_dir,
            self.logs_dir,
            self.backups_dir,
            self.books_dir,
            self.data_dir,
        ]

    def secrets(self) -> tuple[str, ...]:
        """Все ненулевые секретные значения — для redaction в логах (E0.7)."""
        values = (self.llm.api_key, self.llm.analysis_api_key, self.server.auth_secret)
        return tuple(v for v in values if v)


def _sub(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigurationError(
            f"Секция конфигурации '{name}' должна быть объектом (mapping)",
            details={"section": name},
        )
    return value


def _read_yaml(config_yaml: Path, problems: list[str]) -> dict[str, Any]:
    if not config_yaml.is_file():
        problems.append(f"не найден YAML-конфиг: {config_yaml}")
        return {}
    try:
        data = yaml.safe_load(config_yaml.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        problems.append(f"не удалось разобрать YAML {config_yaml}: {exc}")
        return {}
    if not isinstance(data, dict):
        problems.append(f"корень YAML {config_yaml} должен быть объектом (mapping)")
        return {}
    return data


def _format_validation_errors(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()]


def load_settings(config_yaml: Path, env_file: Path) -> Settings:
    """Загрузить и валидировать конфигурацию. Ошибка — ConfigurationError (fail closed)."""
    problems: list[str] = []
    file_env: dict[str, str] = {}
    if not env_file.is_file():
        problems.append(
            f"не найден файл .env: {env_file} (секреты: LLM_API_KEY, LLM_ANALYSIS_API_KEY; "
            "адрес бинда: SERVER_BIND_ADDRESS)"
        )
    else:
        # Без мутации os.environ (побочный эффект load_dotenv): при нескольких
        # конфигурациях в одном процессе (тесты, профили) секрет одного .env
        # перетекал бы в другую конфигурацию.
        file_env = {key: (value or "") for key, value in dotenv_values(env_file).items()}

    data = _read_yaml(config_yaml, problems)
    if problems:
        raise ConfigurationError(
            "источники конфигурации недоступны", details={"problems": problems}
        )

    env = {
        key: (os.environ[key] if key in os.environ else file_env.get(key, "")).strip()
        for key in ALL_ENV_KEYS
    }

    missing: list[str] = [key for key in REQUIRED_ENV_KEYS if not env[key]]
    storage_sub = _sub(data, "storage")
    root_raw = storage_sub.get("root")
    if not isinstance(root_raw, str) or not root_raw.strip():
        missing.append("storage.root (YAML-конфиг)")
    # E4.1: адрес и модель — только в YAML-профилях; второго места нет.
    # Заполненность base_url/model — НЕ отказ старта (MTPE TZ §5.3 п.2): сервер
    # стартует с полосой «Настройки LLM неполны»; проверяет validate_llm_settings.
    llm_sub = _sub(data, "llm")
    profiles_sub = _sub(llm_sub, "profiles")
    if missing:
        raise ConfigurationError(
            "не заданы обязательные параметры конфигурации (молчаливых значений по умолчанию нет)",
            details={"missing": missing},
        )

    root_path = Path(str(root_raw)).expanduser().resolve()

    try:
        llm_section = LLMSection(
            api_key=env["LLM_API_KEY"],
            analysis_api_key=env["LLM_ANALYSIS_API_KEY"],
            mt_api_key=env["LLM_MT_API_KEY"],
            postedit_api_key=env["LLM_POSTEDIT_API_KEY"],
            # Все пять профилей схемы (TZ §4.1): отсутствующие в YAML — дефолтный
            # (пустой) профиль; заполненность проверяет validate_llm_settings (TZ §5.3).
            profiles={
                name: LLMProfile.model_validate(_sub(profiles_sub, name))
                for name in ALL_PROFILE_NAMES
            },
            prompts_root=str(llm_sub.get("prompts_root", "prompts")),
            probe_timeout_s=llm_sub.get("probe_timeout_s", 300.0),
        )
        server_section = ServerSection.model_validate(
            {
                **_sub(data, "server"),
                "bind_address": env["SERVER_BIND_ADDRESS"],
                "auth_secret": env["SERVER_AUTH_SECRET"],
                "allow_wildcard_bind": env["ALLOW_WILDCARD_BIND"].strip().lower()
                in {"1", "true", "yes"},
            }
        )
        storage_section = StorageSection.model_validate({**storage_sub, "root": str(root_path)})
        limits_section = LimitsSection.model_validate(_sub(data, "limits"))
        glossary_section = GlossarySection.model_validate(_sub(data, "glossary"))
        alignment_section = AlignmentSection.model_validate(_sub(data, "alignment"))
        segmentation_section = SegmentationSection.model_validate(_sub(data, "segmentation"))
        detection_section = LanguageDetectionSection.model_validate(
            _sub(data, "language_detection")
        )
        languages_section = LanguagesSection.model_validate(_sub(data, "languages"))
        epubcheck_section = EpubcheckSection.model_validate(_sub(data, "epubcheck"))
        converter_section = ConverterSection.model_validate(_sub(data, "converter"))
        translation_section = TranslationSection.model_validate(_sub(data, "translation"))
        mtpe_section = MTPESection.model_validate(_sub(data, "mtpe"))
        ui_section = UiSection.model_validate(_sub(data, "ui"))
    except ValidationError as exc:
        raise ConfigurationError(
            "конфигурация не прошла валидацию", details={"errors": _format_validation_errors(exc)}
        ) from exc

    settings = Settings(
        server=server_section,
        llm=llm_section,
        storage=storage_section,
        limits=limits_section,
        glossary=glossary_section,
        alignment=alignment_section,
        segmentation=segmentation_section,
        language_detection=detection_section,
        languages=languages_section,
        epubcheck=epubcheck_section,
        converter=converter_section,
        translation=translation_section,
        mtpe=mtpe_section,
        ui=ui_section,
    )

    if settings.server.bind_address == WILDCARD_ADDRESS and not settings.server.allow_wildcard_bind:
        raise ConfigurationError(
            "отказ: бинд на 0.0.0.0 запрещён без явного разрешения. Задайте "
            "ALLOW_WILDCARD_BIND=true в .env, только если открытый доступ осознанно нужен",
            details={"bind_address": settings.server.bind_address},
        )

    # D62: явно заданный корень промптов проверяется на старте (fail closed):
    # относительный — от storage.root, абсолютный — как есть. Дефолтный корень —
    # часть storage-раскладки: отсутствие промптов в нём — не ошибка старта,
    # оно обнаруживается пер-задачей явным PromptError/GlossaryError (E4.3/L2).
    if "prompts_root" in llm_sub and not settings.prompts_dir.is_dir():
        raise ConfigurationError(
            f"каталог промптов не существует: {settings.prompts_dir} "
            f"(llm.prompts_root={llm_section.prompts_root!r}) — создайте каталог "
            "или исправьте значение",
            details={"prompts_root": llm_section.prompts_root},
        )

    for directory in settings.all_data_dirs():
        directory.mkdir(parents=True, exist_ok=True)

    return settings
