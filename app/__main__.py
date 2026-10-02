# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Точка входа приложения.

E0: загрузка и валидация конфигурации (fail closed), инициализация логирования.
Начиная с U1 здесь запускается FastAPI-сервер (Web UI + API). Воркер —
отдельный процесс: ``python -m app.workers.run --loop`` (запуск из UI —
«Not implemented», не притворяемся, что он запускается).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from app.core.config import load_settings
from app.core.errors import ConfigurationError
from app.core.logging import setup_logging
from app.web.app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="moxhome",
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
    # R8: пути конфигов — запись LLM-настроек из UI + релоад после
    # сохранения (ТЗ §13).
    app = create_app(settings, config_path=Path(args.config), env_path=Path(args.env))
    log.info(
        "U1: запуск FastAPI-сервера",
        extra={
            "bind": settings.server.bind_address,
            "port": settings.server.port,
            "auth": "on (общий секрет)" if settings.server.auth_secret else "off",
        },
    )
    uvicorn.run(
        app,
        host=settings.server.bind_address,
        port=settings.server.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
