"""Точка входа приложения.

E0: загрузка и валидация конфигурации (fail closed), инициализация логирования.
Начиная с U1 здесь запускается FastAPI-сервер.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.core.config import load_settings
from app.core.errors import ConfigurationError
from app.core.logging import setup_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="epubtranslator",
        description="Конвейер перевода EPUB и генерации учебных материалов",
    )
    parser.add_argument("--config", default="config/config.yaml", help="путь к YAML-конфигу")
    parser.add_argument("--env", default=".env", help="путь к файлу .env")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(Path(args.config), Path(args.env))
    except ConfigurationError as exc:
        print(f"CONFIGURATION ERROR: {exc.message}", file=sys.stderr)
        for key, value in (exc.details or {}).items():
            print(f"  {key}: {value}", file=sys.stderr)
        return exc.exit_code

    setup_logging(settings.logs_dir, level="INFO", secrets=settings.secrets())
    log = logging.getLogger("app.bootstrap")
    log.info(
        "конфигурация валидирована",
        extra={
            "storage_root": str(settings.storage.root),
            "bind_address": settings.server.bind_address,
            "port": settings.server.port,
        },
    )
    log.info("этап E0: службы ещё не запущены (сервер — этап U1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
