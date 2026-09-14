"""Сервисы (сценарии): задание, подтверждение, разбор книги (E3; поверх — U1 HTTP, U2 CLI)."""

from app.services.analyze import scan_book
from app.services.jobs import PROMPT_VERSION, config_hash, confirm_job, create_job

__all__ = ["PROMPT_VERSION", "config_hash", "confirm_job", "create_job", "scan_book"]
