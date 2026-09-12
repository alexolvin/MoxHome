"""SQLite в режиме WAL с миграциями (E3.1).

Единственный писатель — воркер; API пишет только создание задания и управляющие
команды (E3.1). `busy_timeout` — из конфигурации (storage.busy_timeout_ms).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.core.errors import ConfigurationError

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    source_path TEXT NOT NULL,
    target_language TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    progress REAL NOT NULL DEFAULT 0.0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    confirmed_at REAL,
    interrupted_from TEXT,
    paused_from TEXT,
    resume_requested INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    pause_requested INTEGER NOT NULL DEFAULT 0,
    worker_pid INTEGER,
    lease_expires_at REAL,
    input_sha256 TEXT NOT NULL DEFAULT '',
    config_sha256 TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT 'v0',
    tokens_translation INTEGER NOT NULL DEFAULT 0,
    tokens_fill INTEGER NOT NULL DEFAULT 0,
    tokens_analysis INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    at REAL NOT NULL,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'worker'
);

CREATE TABLE IF NOT EXISTS stage_results (
    job_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    artifact_path TEXT,
    tokens INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    PRIMARY KEY (job_id, stage)
);

CREATE INDEX IF NOT EXISTS idx_jobs_claim
    ON jobs (status, priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_transitions_job ON transitions (job_id, id);
"""


class Database:
    """Подключение SQLite (WAL) с выполненными миграциями."""

    def __init__(self, path: Path, busy_timeout_ms: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._conn = sqlite3.connect(str(path), timeout=max(busy_timeout_ms, 1) / 1000.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        mode = self._conn.execute("PRAGMA journal_mode = WAL").fetchone()
        if mode is None or str(mode[0]).lower() != "wal":
            self._conn.close()
            raise ConfigurationError(
                f"не удаётся включить WAL для {path}",
                details={"actual_mode": None if mode is None else str(mode[0])},
            )
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    def migrate(self) -> None:
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    @property
    def path(self) -> Path:
        return self._path

    def integrity_check(self) -> str:
        row = self._conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row is not None else "(нет ответа)"

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Блок транзакции с авто-commit при успехе и откатом при исключении."""
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
