"""Конфигурация приложения — ЕДИНСТВЕННЫЙ слой чтения .env и YAML (Rule 1).

Разделение (E0.3): секреты и адреса — в .env; всё структурное — в YAML.
Прямое чтение os.environ вне этого модуля запрещено.

Обязательные параметры без значений по умолчанию (E0.5):
server.bind_address, llm.base_url, llm.model, storage.root.
Бинд на 0.0.0.0 — только при явном ALLOW_WILDCARD_BIND=true (E0.6).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.core.errors import ConfigurationError

#: Все переменные окружения, которые знает приложение (секреты и адреса).
ALL_ENV_KEYS: tuple[str, ...] = (
    "SERVER_BIND_ADDRESS",
    "SERVER_AUTH_SECRET",
    "ALLOW_WILDCARD_BIND",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_ANALYSIS_BASE_URL",
    "LLM_ANALYSIS_MODEL",
    "LLM_ANALYSIS_API_KEY",
)
#: Обязательные переменные окружения (без них — отказ старта).
REQUIRED_ENV_KEYS: tuple[str, ...] = ("SERVER_BIND_ADDRESS", "LLM_BASE_URL", "LLM_MODEL")
#: Имена профилей LLM (E4): фиксированный набор, не язык/модель, а архитектура.
PROFILE_NAMES: tuple[str, ...] = ("translation", "fill", "analysis")
#: Значение, требующее явного разрешения (E0.6). Константа спецификации ТЗ.
WILDCARD_ADDRESS = "0.0.0.0"


class LLMProfile(BaseModel):
    """Настройки одного LLM-профиля (структурная часть, YAML)."""

    model_config = ConfigDict(frozen=True)

    token_encoding: str = Field(default="cl100k_base", min_length=1)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=300.0, gt=0.0)
    retry_times: int = Field(default=3, ge=0)
    max_retries: int = Field(default=5, ge=1)
    concurrency: int = Field(default=1, ge=1)
    max_group_tokens: int = Field(default=2600, gt=0)


class LLMSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key: str = ""
    #: Опциональные переопределения для профиля analysis (T-E4-8).
    analysis_base_url: str = ""
    analysis_model: str = ""
    analysis_api_key: str = ""
    profiles: dict[str, LLMProfile]

    def profile(self, name: str) -> LLMProfile:
        try:
            return self.profiles[name]
        except KeyError as exc:
            raise ConfigurationError(f"LLM-профиль не найден: {name}") from exc

    def endpoint(self, name: str) -> tuple[str, str, str]:
        """(base_url, model, api_key) профиля с учётом переопределений analysis."""
        if name == "analysis" and self.analysis_base_url and self.analysis_model:
            return (
                self.analysis_base_url,
                self.analysis_model,
                self.analysis_api_key or self.api_key,
            )
        return self.base_url, self.model, self.api_key


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


class AlignmentSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_coverage: float = Field(default=0.995, gt=0.0, le=1.0)


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
    model_config = ConfigDict(frozen=True)

    sample_segments: int = Field(default=200, gt=0)
    min_confidence: float = Field(default=0.6, gt=0.0, le=1.0)


class EpubcheckSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    jar_path: str = Field(default="data/tools/epubcheck.jar", min_length=1)


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    server: ServerSection
    llm: LLMSection
    storage: StorageSection
    limits: LimitsSection
    alignment: AlignmentSection
    segmentation: SegmentationSection
    language_detection: LanguageDetectionSection
    epubcheck: EpubcheckSection

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
    if not env_file.is_file():
        problems.append(
            f"не найден файл .env: {env_file} (секреты и адреса: LLM_BASE_URL, LLM_MODEL, "
            "SERVER_BIND_ADDRESS)"
        )
    else:
        load_dotenv(env_file, override=False)

    data = _read_yaml(config_yaml, problems)
    if problems:
        raise ConfigurationError(
            "источники конфигурации недоступны", details={"problems": problems}
        )

    env = {key: os.environ.get(key, "").strip() for key in ALL_ENV_KEYS}

    missing: list[str] = [key for key in REQUIRED_ENV_KEYS if not env[key]]
    storage_sub = _sub(data, "storage")
    root_raw = storage_sub.get("root")
    if not isinstance(root_raw, str) or not root_raw.strip():
        missing.append("storage.root (YAML-конфиг)")
    if missing:
        raise ConfigurationError(
            "не заданы обязательные параметры конфигурации (молчаливых значений по умолчанию нет)",
            details={"missing": missing},
        )

    root_path = Path(str(root_raw)).expanduser().resolve()

    try:
        llm_sub = _sub(data, "llm")
        profiles_sub = _sub(llm_sub, "profiles")
        llm_section = LLMSection(
            base_url=env["LLM_BASE_URL"],
            model=env["LLM_MODEL"],
            api_key=env["LLM_API_KEY"],
            analysis_base_url=env["LLM_ANALYSIS_BASE_URL"],
            analysis_model=env["LLM_ANALYSIS_MODEL"],
            analysis_api_key=env["LLM_ANALYSIS_API_KEY"],
            profiles={
                name: LLMProfile.model_validate(_sub(profiles_sub, name)) for name in PROFILE_NAMES
            },
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
        alignment_section = AlignmentSection.model_validate(_sub(data, "alignment"))
        segmentation_section = SegmentationSection.model_validate(_sub(data, "segmentation"))
        detection_section = LanguageDetectionSection.model_validate(
            _sub(data, "language_detection")
        )
        epubcheck_section = EpubcheckSection.model_validate(_sub(data, "epubcheck"))
    except ValidationError as exc:
        raise ConfigurationError(
            "конфигурация не прошла валидацию", details={"errors": _format_validation_errors(exc)}
        ) from exc

    settings = Settings(
        server=server_section,
        llm=llm_section,
        storage=storage_section,
        limits=limits_section,
        alignment=alignment_section,
        segmentation=segmentation_section,
        language_detection=detection_section,
        epubcheck=epubcheck_section,
    )

    if settings.server.bind_address == WILDCARD_ADDRESS and not settings.server.allow_wildcard_bind:
        raise ConfigurationError(
            "отказ: бинд на 0.0.0.0 запрещён без явного разрешения. Задайте "
            "ALLOW_WILDCARD_BIND=true в .env, только если открытый доступ осознанно нужен",
            details={"bind_address": settings.server.bind_address},
        )

    for directory in settings.all_data_dirs():
        directory.mkdir(parents=True, exist_ok=True)

    return settings
