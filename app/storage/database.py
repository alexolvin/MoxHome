"""SQLite в режиме WAL с миграциями (E3.1).

Единственный писатель — воркер; API пишет только создание задания и управляющие
команды (E3.1). `busy_timeout` — из конфигурации (storage.busy_timeout_ms).

Подключения — по одному на поток (``threading.local``): FastAPI/uvicorn
выполняют синхронные endpoint'ы в пуле рабочих потоков, а соединение SQLite
привязано к создавшему потоку. Конкурентность между соединениями
сериализует сам SQLite (WAL + busy_timeout); общего соединения нет —
гонки на одном объекте connection исключены по построению (U1).
"""

from __future__ import annotations

import sqlite3
import threading
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
    mode TEXT NOT NULL DEFAULT 'bilingual',
    priority INTEGER NOT NULL DEFAULT 0,
    queue_rank INTEGER NOT NULL DEFAULT 0,
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
    tokens_total INTEGER NOT NULL DEFAULT 0,
    alignment_coverage REAL,
    source_language TEXT,
    learning_level TEXT,
    learning_cefr TEXT,
    param_chips TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS resolved_prompts (
    job_id TEXT NOT NULL,
    task TEXT NOT NULL,
    file TEXT NOT NULL,
    version INTEGER NOT NULL,
    level TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS job_tokens (
    job_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    input_cache_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, profile)
);

CREATE INDEX IF NOT EXISTS idx_transitions_job ON transitions (job_id, id);
CREATE INDEX IF NOT EXISTS idx_resolved_prompts_job ON resolved_prompts (job_id, task);
"""

#: Колонки jobs, добавляемые миграциями (имя → DDL ALTER TABLE).
_MIGRATED_JOB_COLUMNS: dict[str, str] = {
    "tokens_total": "INTEGER NOT NULL DEFAULT 0",
    # E3-базы: все задания исполнялись в APPEND_BLOCK = режим "bilingual" (E5).
    "mode": "TEXT NOT NULL DEFAULT 'bilingual'",
    # E5-базы: выравнивание (E6) до этапа не измерялось — NULL.
    "alignment_coverage": "REAL",
    # L0-базы: исходный язык до L0 не хранился — NULL (подтверждается на validating).
    "source_language": "TEXT",
    # S1-базы (ТЗ_03): learning не хранился — NULL = learning не включён (no-op).
    "learning_level": "TEXT",
    "learning_cefr": "TEXT",
    # Серия R §15: ручная очередь («Вперёд очереди»); 0 = по времени создания.
    "queue_rank": "INTEGER NOT NULL DEFAULT 0",
    # Серия R §12: чипы параметров, отличающихся от умолчаний (JSON; '' — нет).
    "param_chips": "TEXT NOT NULL DEFAULT ''",
}


class Database:
    """Подключения SQLite (WAL) — по одному на поток — с миграциями."""

    def __init__(self, path: Path, busy_timeout_ms: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._busy_timeout_ms = int(busy_timeout_ms)
        self._local = threading.local()
        # Соединение создающего потока + миграции (fail closed, как раньше).
        self.migrate()

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._path), timeout=max(self._busy_timeout_ms, 1) / 1000.0
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        """Соединение ТЕКУЩЕГО потока (создаётся лениво)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open_connection()
            mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()
            if mode is None or str(mode[0]).lower() != "wal":
                conn.close()
                raise ConfigurationError(
                    f"не удаётся включить WAL для {self._path}",
                    details={"actual_mode": None if mode is None else str(mode[0])},
                )
            self._local.conn = conn
        return conn

    def migrate(self) -> None:
        """DDL новых баз + миграции существующих (E3 → E4: колонка tokens_total).

        CREATE TABLE IF NOT EXISTS не меняет уже существующую таблицу, поэтому
        для старых баз (E3) недостающие колонки добавляются ALTER TABLE с
        бекфиллом инварианта tokens_total = translation + fill + analysis.
        """
        conn = self.conn
        conn.executescript(SCHEMA)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        for column, ddl in _MIGRATED_JOB_COLUMNS.items():
            if column in existing:
                continue
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {ddl}")
            if column == "tokens_total":
                conn.execute(
                    "UPDATE jobs SET tokens_total = "
                    "tokens_translation + tokens_fill + tokens_analysis"
                )
        # Серия R §15: индекс очереди — ПОСЛЕ миграции колонок (на старых
        # БД executescript предшествует ALTER TABLE); старому индексу
        # (priority) замена — в выборке queue_rank, priority не участвует.
        conn.execute("DROP INDEX IF EXISTS idx_jobs_claim")
        conn.execute(
            "CREATE INDEX idx_jobs_claim "
            "ON jobs (status, queue_rank DESC, created_at ASC)"
        )
        conn.commit()

    @property
    def path(self) -> Path:
        return self._path

    def integrity_check(self) -> str:
        row = self.conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row is not None else "(нет ответа)"

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

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
