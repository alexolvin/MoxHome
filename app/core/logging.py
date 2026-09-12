"""Структурное логирование: JSON-строки, job_id в записях, redaction секретов (E0.7).

Секрет (ключ, токен, cookie) не попадает в лог ни при каком уровне детализации:
форматтер замещает каждое значение из настроек на [REDACTED].
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

_job_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("app_job_id", default=None)

_RESERVED_RECORD_ATTRS: frozenset[str] = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime"}


@contextmanager
def job_context(job_id: str) -> Iterator[None]:
    """Привязать job_id ко всем записям логов в теле контекста."""
    token = _job_id.set(job_id)
    try:
        yield
    finally:
        _job_id.reset(token)


class RedactingJsonFormatter(logging.Formatter):
    def __init__(self, secrets: Sequence[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        current_job = _job_id.get()
        if current_job is not None:
            payload["job_id"] = current_job
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS:
                payload[key] = value
        if record.exc_info is not None:
            payload["exc"] = self.formatException(record.exc_info)
        return self._redact(json.dumps(payload, ensure_ascii=False, default=str))

    def _redact(self, line: str) -> str:
        for secret in self._secrets:
            if secret in line:
                line = line.replace(secret, "[REDACTED]")
        return line


def setup_logging(logs_dir: Path, level: str = "INFO", secrets: Sequence[str] = ()) -> None:
    """Консоль (stderr) + файл logs/app.log. Вызывается один раз при старте."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    formatter = RedactingJsonFormatter(secrets)

    file_handler = logging.FileHandler(logs_dir / "app.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(stream_handler)


#: Отдельный логгер для записей качества (quality.log задания, E3.9).
QUALITY_LOGGER = "app.quality"


def attach_job_logs(job_dir: Path, secrets: Sequence[str] = ()) -> list[logging.Handler]:
    """Логи задания (E3.9): job.log (всё), errors.log (ERROR+), quality.log (только app.quality).

    Файлы создаются сразу, чтобы существовать даже при пустых содержимом.
    Возвращает обработчики для detach_job_logs.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    formatter = RedactingJsonFormatter(secrets)
    handlers: list[logging.Handler] = []

    job_handler = logging.FileHandler(job_dir / "job.log", encoding="utf-8")
    job_handler.setFormatter(formatter)
    job_handler.setLevel(logging.INFO)
    handlers.append(job_handler)

    errors_handler = logging.FileHandler(job_dir / "errors.log", encoding="utf-8")
    errors_handler.setFormatter(formatter)
    errors_handler.setLevel(logging.ERROR)
    handlers.append(errors_handler)

    quality_logger = logging.getLogger(QUALITY_LOGGER)
    quality_handler = logging.FileHandler(job_dir / "quality.log", encoding="utf-8")
    quality_handler.setFormatter(formatter)
    quality_logger.setLevel(logging.INFO)
    quality_logger.handlers.clear()
    quality_logger.addHandler(quality_handler)
    handlers.append(quality_handler)

    root = logging.getLogger()
    root.addHandler(job_handler)
    root.addHandler(errors_handler)
    return handlers


def detach_job_logs(handlers: Sequence[logging.Handler]) -> None:
    root = logging.getLogger()
    quality_logger = logging.getLogger(QUALITY_LOGGER)
    for handler in handlers:
        if handler in root.handlers:
            root.removeHandler(handler)
        elif handler in quality_logger.handlers:
            quality_logger.removeHandler(handler)
        handler.close()
