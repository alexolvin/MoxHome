"""Контракт адаптера (E5.1): типы TranslationEngine Protocol и запроса/результата.

Модуль НЕ импортирует `epub_translator` (Rule 8, D2): типы движка существуют
только в `engine.py`. Интерфейс — по ТЗ E5:

    class TranslationEngine(Protocol):
        def translate_book(self, request: TranslationRequest) -> TranslationResult: ...
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class TranslationEngine(Protocol):
    """Интерфейс адаптера (E5.1, ТЗ). Реализация `EpubTranslatorEngine` — единственная.

    Прикладной код не импортирует `epub_translator` напрямую нигде, кроме
    пакета адаптера (Rule 8, D2, D17).
    """

    def translate_book(self, request: TranslationRequest) -> TranslationResult: ...


class SubmitMode(StrEnum):
    """Движковый режим подачи перевода (E5.2).

    translation (режим задания) → REPLACE; bilingual → APPEND_BLOCK по умолчанию,
    APPEND_TEXT — настройка, по умолчанию не используется (ухудшает выравнивание).
    """

    REPLACE = "REPLACE"
    APPEND_BLOCK = "APPEND_BLOCK"
    APPEND_TEXT = "APPEND_TEXT"


class ValidationStatus(StrEnum):
    """Статус внешней валидации (EPUBCheck, E5.7)."""

    OK = "ok"
    #: EPUBCheck/Java отсутствуют — результат выдаётся, но задание не COMPLETED
    #: (COMPLETED_WITH_WARNINGS, причина VALIDATION_UNAVAILABLE, Rule 6).
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class GlossaryEntry:
    """Термин глоссария (E5.5; наполняется этапом L2).

    `occurrences` — число вхождений в исходном тексте: приоритет при усечении
    (TZ 02, L2.5: термины отбираются по числу вхождений).
    """

    term: str
    translation: str
    occurrences: int = 0


@dataclass(frozen=True)
class FillFailedEvent:
    """Событие неудачи fill (колбэк движка, тип адаптера — не движка)."""

    error_message: str
    retried_count: int
    over_maximum_retries: bool


@dataclass(frozen=True)
class EngineUsage:
    """Накопительные счётчики токенов движка (тракт T, E4.7). input_cache ⊆ input."""

    translation_input: int = 0
    translation_input_cache: int = 0
    translation_output: int = 0
    fill_input: int = 0
    fill_input_cache: int = 0
    fill_output: int = 0

    @property
    def translation_total(self) -> int:
        return self.translation_input + self.translation_output

    @property
    def fill_total(self) -> int:
        return self.fill_input + self.fill_output

    @property
    def total(self) -> int:
        return self.translation_total + self.fill_total


@dataclass(frozen=True)
class TranslationRequest:
    """Запрос к движку (E5.1).

    `target_path` — финальный результат: адаптер пишет ТОЛЬКО через
    `.partial` → валидация → `os.replace` (E5.6); файл в состоянии записи не
    считается результатом ни при каких условиях.
    """

    source_path: Path
    target_path: Path
    target_language: str
    mode: SubmitMode
    #: Глоссарий (E5.5): передаётся движку через user_prompt с лимитом из конфига.
    glossary: tuple[GlossaryEntry, ...] = ()
    #: Лимит user_prompt (символы), из limits.glossary_max_user_prompt_chars.
    glossary_limit_chars: int = 8000
    #: Префикс user_prompt (L2.7): стилевой профиль книги; учитывается в
    #: лимите (glossary_max_user_prompt_chars) наравне с глоссарием.
    glossary_prefix: str = ""
    on_progress: Callable[[float], None] | None = None
    on_fill_failed: Callable[[FillFailedEvent], None] | None = None


@dataclass(frozen=True)
class TranslationResult:
    """Результат перевода (E5.1): файл создан атомарно, валидация — факты."""

    target_path: Path
    usage: EngineUsage
    #: Глоссарий (E5-7): полнота передачи и факт усечения — в отчёт, не молча.
    glossary_total_terms: int
    glossary_kept_terms: int
    glossary_dropped_terms: int
    glossary_prompt_chars: int
    #: EPUBCheck (E5.7): версия фиксируется в отчёте.
    epubcheck_version: str | None
    epubcheck_errors: int
    epubcheck_warnings: int
    validation_status: ValidationStatus
    validation_unavailable_reason: str | None
    #: Резервная копия существующего результата (E5.9: бэкап перед перезаписью).
    backup_path: Path | None
    #: Число вызовов on_progress движка (проверка маппинга «реальные единицы», E5.3).
    progress_call_count: int
