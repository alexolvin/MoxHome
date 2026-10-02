# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""T-E0-3: логгер не пишет значение ключа API; job_id попадает в запись."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.logging import job_context, setup_logging


def _log_file(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "app.log"


def test_secret_not_in_log_file(tmp_path: Path) -> None:
    secret = "sk-supersecret-value-12345"
    setup_logging(tmp_path / "logs", secrets=[secret])
    logger = logging.getLogger("test.e0")
    logger.info("api key is %s", secret)
    try:
        raise ValueError(f"connect with {secret}")
    except ValueError:
        logger.exception("request failed")
    content = _log_file(tmp_path).read_text(encoding="utf-8")
    assert secret not in content
    assert "[REDACTED]" in content


def test_non_secret_not_redacted(tmp_path: Path) -> None:
    setup_logging(tmp_path / "logs", secrets=["sk-only-this-value"])
    logging.getLogger("test.e0").info("plain message abc-123")
    content = _log_file(tmp_path).read_text(encoding="utf-8")
    assert "abc-123" in content
    assert "[REDACTED]" not in content


def test_job_id_in_record(tmp_path: Path) -> None:
    setup_logging(tmp_path / "logs")
    with job_context("j-20260912T000000Z-000000"):
        logging.getLogger("test.e0").info("inside job")
    lines = _log_file(tmp_path).read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[-1])
    assert payload["job_id"] == "j-20260912T000000Z-000000"


def test_structured_json_fields(tmp_path: Path) -> None:
    setup_logging(tmp_path / "logs")
    logging.getLogger("test.e0").info("hello", extra={"chapter": 3})
    lines = _log_file(tmp_path).read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[-1])
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.e0"
    assert payload["msg"] == "hello"
    assert payload["chapter"] == 3
    assert "ts" in payload
