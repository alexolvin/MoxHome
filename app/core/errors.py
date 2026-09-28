"""Ошибки приложения и классы кодов выхода.

Коды выхода (U2): 0 — успех; 2 — конфигурация; 3 — валидация входа;
4 — недоступность LLM; 5 — отказ валидации выхода; 1 — прочие.
"""

from __future__ import annotations


class AppError(Exception):
    """Базовая ошибка приложения с классом кода выхода."""

    exit_code: int = 1

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class ConfigurationError(AppError):
    """Ошибка конфигурации: пропущенный/невалидный параметр, отказ старта."""

    exit_code = 2


class InputValidationError(AppError):
    """Входной файл/аргумент не прошли детерминированную валидацию."""

    exit_code = 3


class SettingsIncompleteError(AppError):
    """Настройки LLM неполны для способа задания (MTPE TZ §5.3): создание задания
    отклонено — API возвращает 422, CLI/мастер — отказ (exit_code 3). Не входной
    файл, а конфигурация профилей, обязательных при текущем pipeline."""

    exit_code = 3


class LLMUnavailableError(AppError):
    """LLM-эндпоинт или профиль недоступны (fail closed, Rule 6)."""

    exit_code = 4


class OutputValidationFailed(AppError):
    """Выходной артефакт не прошёл валидацию."""

    exit_code = 5


class JobStateError(AppError):
    """Недопустимый переход состояния задания (E3)."""

    exit_code = 1
