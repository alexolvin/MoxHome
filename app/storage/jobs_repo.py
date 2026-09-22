"""Репозиторий заданий (E3): состояния, переходы, аренда, результаты этапов.

- Переход вне явной таблицы — JobStateError + запись в лог (E3.2).
- Переход фиксируется в БД до побочного эффекта (E3.3): вызывается воркером
  перед началом работы состояния.
- Аренда (lease) — единственный механизм обнаружения осиротевших заданий (E3.4):
  recover_expired_leases() переводит задания с истёкшей арендой в INTERRUPTED.
- Занятие (claim) — атомарный UPDATE: два воркера не могут занять одно задание (E3.6).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass

from app.core.errors import JobStateError
from app.domain.job import (
    TERMINAL_STATES,
    TRANSITIONS,
    WORKING_STATES,
    Job,
    JobStatus,
)
from app.storage.database import Database, transaction

logger = logging.getLogger("app.jobs")

_RESUME_SOURCES: frozenset[JobStatus] = frozenset(
    {JobStatus.INTERRUPTED, JobStatus.PAUSED}
)
_LEASE_CLEARED_ON: frozenset[JobStatus] = frozenset(
    {
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.COMPLETED,
        JobStatus.COMPLETED_WITH_WARNINGS,
        JobStatus.INTERRUPTED,
        JobStatus.PAUSED,
    }
)


@dataclass(frozen=True)
class StageResult:
    stage: str
    input_hash: str
    artifact_path: str | None
    tokens: int
    created_at: float


@dataclass(frozen=True)
class TransitionRecord:
    job_id: str
    from_status: JobStatus
    to_status: JobStatus
    at: float
    reason: str
    actor: str


@dataclass(frozen=True)
class ProfileTokens:
    """Токены профиля по заданию (разбивка, E4.7). input_cache ⊆ input."""

    profile: str
    input_tokens: int
    input_cache_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ResolvedPrompt:
    """Фактически применённый промпт задачи (E4-2: файл + версия, 100% задач)."""

    job_id: str
    task: str
    file: str
    version: int
    level: str  # pair | source | generic
    created_at: float


def _now() -> float:
    return time.time()


class JobRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------- создание/чтение

    def create(self, job: Job) -> None:
        conn = self._db.conn
        with transaction(conn):
            conn.execute(
                """
                INSERT INTO jobs (
                    id, status, source_path, target_language, mode, priority, queue_rank,
                    progress, created_at, updated_at, input_sha256, config_sha256,
                    prompt_version, learning_level, learning_cefr, param_chips
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.status.value,
                    job.source_path,
                    job.target_language,
                    job.mode,
                    job.priority,
                    job.queue_rank,
                    job.progress,
                    job.created_at,
                    job.updated_at,
                    job.input_sha256,
                    job.config_sha256,
                    job.prompt_version,
                    job.learning_level,
                    job.learning_cefr,
                    job.param_chips,
                ),
            )

    def get(self, job_id: str) -> Job | None:
        row = self._db.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row is not None else None

    def list_jobs(
        self,
        limit: int | None = None,
        offset: int = 0,
        *,
        statuses: tuple[str, ...] | None = None,
        book: str | None = None,
        source_language: str | None = None,
        target_language: str | None = None,
        created_from: float | None = None,
        created_to: float | None = None,
        updated_before: float | None = None,
        updated_after: float | None = None,
        sort: str = "created_at",
        direction: str = "desc",
    ) -> list[Job]:
        """Задания с фильтрами/сортировкой/пагинацией — ЕДИНСТВЕННЫЙ источник
        списка (U3 3.4.16: ``list_jobs(limit, offset, …)``). Без параметров —
        все задания, новые первыми (поведение U1 сохранено).

        Фильтрация — в SQL (не в шаблоне). Только чтение — API/UI не пишут
        рабочие состояния (E3.1). ``updated_before`` — для mass-purge U3:
        «в терминальном статусе не младше N дней» (updated_at терминального
        перехода). ``updated_after`` — серия R §10: дельта списка
        (``GET /api/jobs?since=<rev>``)."""
        where, params = self._jobs_filter_clause(
            statuses=statuses,
            book=book,
            source_language=source_language,
            target_language=target_language,
            created_from=created_from,
            created_to=created_to,
            updated_before=updated_before,
            updated_after=updated_after,
        )
        sort_col, direction_norm = self._jobs_sort_clause(sort, direction)
        limit_sql = " LIMIT ? OFFSET ?" if limit is not None else ""
        sql = f"SELECT * FROM jobs{where} ORDER BY {sort_col} {direction_norm}, id DESC{limit_sql}"
        query_params = [*params, *((limit, max(offset, 0)) if limit is not None else ())]
        rows = self._db.conn.execute(sql, query_params).fetchall()
        jobs: list[Job] = []
        for row in rows:
            job = self._row_to_job(row)
            if job is not None:
                jobs.append(job)
        return jobs

    def count_jobs(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        book: str | None = None,
        source_language: str | None = None,
        target_language: str | None = None,
        created_from: float | None = None,
        created_to: float | None = None,
        updated_before: float | None = None,
        updated_after: float | None = None,
    ) -> int:
        """Число заданий по тем же фильтрам, что ``list_jobs`` (для пагинации)."""
        where, params = self._jobs_filter_clause(
            statuses=statuses,
            book=book,
            source_language=source_language,
            target_language=target_language,
            created_from=created_from,
            created_to=created_to,
            updated_before=updated_before,
            updated_after=updated_after,
        )
        row = self._db.conn.execute(
            f"SELECT COUNT(*) AS n FROM jobs{where}", params
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    def list_job_ids_by_book(self, filename: str) -> list[str]:
        """Задания, ссылающиеся на книгу по имени файла (U3: fail closed
        при удалении книги). Сопоставление: сохранённый ``source_path``
        равен имени либо оканчивается на ``/<имя>``."""
        rows = self._db.conn.execute(
            "SELECT id FROM jobs WHERE source_path = ? OR source_path LIKE ? "
            "ORDER BY id",
            (filename, "%/" + filename),
        ).fetchall()
        return [str(row["id"]) for row in rows]

    def delete_job(self, job_id: str) -> int:
        """U3 3.5.22: удаление строк задания — ОДНА транзакция по всем
        таблицам (jobs, transitions, job_tokens, stage_results,
        resolved_prompts). Каталог с артефактами — не дело БД: его удаляет
        вызывающий (API) после commit. Возврат — число удалённых строк
        (строка аудита, 3.5.27)."""
        total = 0
        with transaction(self._db.conn) as conn:
            for statement in (
                "DELETE FROM transitions WHERE job_id = ?",
                "DELETE FROM job_tokens WHERE job_id = ?",
                "DELETE FROM stage_results WHERE job_id = ?",
                "DELETE FROM resolved_prompts WHERE job_id = ?",
                "DELETE FROM jobs WHERE id = ?",
            ):
                total += conn.execute(statement, (job_id,)).rowcount
        return total

    @staticmethod
    def _jobs_filter_clause(
        *,
        statuses: tuple[str, ...] | None,
        book: str | None,
        source_language: str | None,
        target_language: str | None,
        created_from: float | None,
        created_to: float | None,
        updated_before: float | None = None,
        updated_after: float | None = None,
    ) -> tuple[str, list[object]]:
        where: list[str] = []
        params: list[object] = []
        if statuses:
            where.append(f"status IN ({', '.join('?' for _ in statuses)})")
            params.extend(statuses)
        if book:
            where.append("(source_path = ? OR source_path LIKE ?)")
            params.extend([book, "%/" + book])
        if source_language:
            where.append("source_language = ?")
            params.append(source_language)
        if target_language:
            where.append("target_language = ?")
            params.append(target_language)
        if created_from is not None:
            where.append("created_at >= ?")
            params.append(created_from)
        if created_to is not None:
            where.append("created_at <= ?")
            params.append(created_to)
        if updated_before is not None:
            where.append("updated_at < ?")
            params.append(updated_before)
        if updated_after is not None:
            where.append("updated_at > ?")
            params.append(updated_after)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        return where_sql, params

    @staticmethod
    def _jobs_sort_clause(sort: str, direction: str) -> tuple[str, str]:
        if sort not in ("created_at", "updated_at"):
            raise ValueError(
                f"сортировка: допустимы created_at | updated_at (получено {sort!r})"
            )
        if direction not in ("asc", "desc"):
            raise ValueError(
                f"направление сортировки: допустимы asc | desc (получено {direction!r})"
            )
        return sort, direction.upper()

    def ping(self) -> None:
        """Жив ли БД (U1 /api/health): ``SELECT 1``; сбой — исключение."""
        self._db.conn.execute("SELECT 1").fetchone()

    def list_transitions(self, job_id: str) -> list[TransitionRecord]:
        rows = self._db.conn.execute(
            "SELECT * FROM transitions WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
        return [
            TransitionRecord(
                job_id=row["job_id"],
                from_status=JobStatus(row["from_status"]),
                to_status=JobStatus(row["to_status"]),
                at=row["at"],
                reason=row["reason"],
                actor=row["actor"],
            )
            for row in rows
        ]

    def list_transitions_bulk(self, job_ids: list[str]) -> dict[str, list[TransitionRecord]]:
        """Переходы нескольких заданий одним запросом (серия R §10: время
        работы строк списка — без N+1). Порядок внутри — id (как в
        ``list_transitions``)."""
        ids = list(dict.fromkeys(job_ids))
        if not ids:
            return {}
        marks = ", ".join("?" for _ in ids)
        rows = self._db.conn.execute(
            f"SELECT * FROM transitions WHERE job_id IN ({marks}) ORDER BY id",
            tuple(ids),
        ).fetchall()
        grouped: dict[str, list[TransitionRecord]] = {}
        for row in rows:
            grouped.setdefault(str(row["job_id"]), []).append(
                TransitionRecord(
                    job_id=row["job_id"],
                    from_status=JobStatus(row["from_status"]),
                    to_status=JobStatus(row["to_status"]),
                    at=row["at"],
                    reason=row["reason"],
                    actor=row["actor"],
                )
            )
        return grouped

    # ------------------------------------------------------------------- переходы

    def transition(
        self,
        job_id: str,
        to_status: JobStatus,
        reason: str,
        *,
        actor: str = "worker",
        error_message: str | None = None,
    ) -> Job:
        """Переход по явной таблице; фиксирует строку в transitions и возвращает снимок.

        Resume-правило: из INTERRUPTED/PAUSED в рабочее состояние разрешён только
        сохранённый *_from-статус (T-E3-2 перебирает и недопустимые пары).

        ``error_message`` (опционально) пишется в той же транзакции —
        опрашивающий читатель (CLI --wait, UI) не видит терминальный
        статус без причины (гонка двух отдельных записей).
        """
        conn = self._db.conn
        with transaction(conn):
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobStateError(f"задание не найдено: {job_id}")
            current = JobStatus(row["status"])
            if to_status not in TRANSITIONS[current]:
                self._log_rejection(job_id, current, to_status, reason)
                raise JobStateError(
                    f"недопустимый переход: {current.value} -> {to_status.value} "
                    f"(задание {job_id})"
                )
            if current in _RESUME_SOURCES and to_status in WORKING_STATES:
                if current is JobStatus.INTERRUPTED:
                    stored = row["interrupted_from"]
                else:
                    stored = row["paused_from"]
                if stored is None or JobStatus(stored) is not to_status:
                    self._log_rejection(job_id, current, to_status, reason)
                    raise JobStateError(
                        f"resume только в сохранённое состояние "
                        f"({stored or 'нет'}, запрошено {to_status.value}); задание {job_id}"
                    )

            now = _now()
            updates = self._build_updates(current, to_status, now)
            if error_message is not None:
                updates["error_message"] = error_message

            sets = ", ".join(f"{key} = ?" for key in updates)
            cur = conn.execute(
                f"UPDATE jobs SET {sets} WHERE id = ? AND status = ?",
                (*updates.values(), job_id, current.value),
            )
            if cur.rowcount != 1:
                raise JobStateError(
                    f"конкурентное изменение статуса задания {job_id} "
                    f"(ожидалось {current.value})"
                )
            conn.execute(
                "INSERT INTO transitions (job_id, from_status, to_status, at, reason, actor) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, current.value, to_status.value, now, reason, actor),
            )
        return self.require(job_id)

    @staticmethod
    def _build_updates(current: JobStatus, to_status: JobStatus, now: float) -> dict[str, object]:
        """Поля jobs, изменяемые при переходе (арена, флаги, *_from, confirmed_at)."""
        updates: dict[str, object] = {"status": to_status.value, "updated_at": now}
        if to_status is JobStatus.INTERRUPTED:
            updates["interrupted_from"] = current.value
        elif to_status is JobStatus.PAUSED:
            updates["paused_from"] = current.value
        if current in _RESUME_SOURCES:
            updates["interrupted_from"] = None
            updates["paused_from"] = None
            updates["resume_requested"] = 0
            updates["pause_requested"] = 0
        if to_status is JobStatus.PAUSED:
            updates["pause_requested"] = 0
        if to_status is JobStatus.CANCELLED:
            updates["cancel_requested"] = 0
        if to_status in _LEASE_CLEARED_ON:
            updates["worker_pid"] = None
            updates["lease_expires_at"] = None
        if to_status is JobStatus.PLANNING and current is JobStatus.NEEDS_CONFIRMATION:
            updates["confirmed_at"] = now
        return updates

    def require(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise JobStateError(f"задание не найдено: {job_id}")
        return job

    @staticmethod
    def _log_rejection(job_id: str, current: JobStatus, to_status: JobStatus, reason: str) -> None:
        logger.warning(
            "недопустимый переход отклонён: задание=%s %s -> %s (причина запроса: %s)",
            job_id,
            current.value,
            to_status.value,
            reason,
        )

    # ------------------------------------------------------------------ аренда (E3.4)

    def claim(self, job_id: str, worker_pid: int, lease_seconds: float) -> bool:
        """Атомарное занятие (E3.5/E3.6).

        Занять можно: CREATED, INTERRUPTED, PLANNING (после подтверждения —
        аренды нет), PAUSED по команде resume. Занятая с действующей арендой
        запись занять нельзя (глобальная проверка аренды в WHERE).
        """
        now = _now()
        cur = self._db.conn.execute(
            """
            UPDATE jobs
            SET worker_pid = ?, lease_expires_at = ?, updated_at = ?
            WHERE id = ?
              AND (
                    status IN ('CREATED', 'INTERRUPTED', 'PLANNING')
                    OR (status = 'PAUSED' AND resume_requested = 1)
              )
              AND (worker_pid IS NULL OR lease_expires_at < ?)
            """,
            (worker_pid, now + lease_seconds, now, job_id, now),
        )
        self._db.conn.commit()
        return cur.rowcount == 1

    def heartbeat(self, job_id: str, worker_pid: int, lease_seconds: float) -> bool:
        """Продление аренды. False — аренда потеряна (занято другим), воркер обязан остановиться."""
        now = _now()
        cur = self._db.conn.execute(
            "UPDATE jobs SET lease_expires_at = ?, updated_at = ? "
            "WHERE id = ? AND worker_pid = ?",
            (now + lease_seconds, now, job_id, worker_pid),
        )
        self._db.conn.commit()
        return cur.rowcount == 1

    def release(self, job_id: str, worker_pid: int) -> None:
        cur = self._db.conn.execute(
            "UPDATE jobs SET worker_pid = NULL, lease_expires_at = NULL, updated_at = ? "
            "WHERE id = ? AND worker_pid = ?",
            (_now(), job_id, worker_pid),
        )
        self._db.conn.commit()
        if cur.rowcount != 1:
            logger.warning("снять аренду не удалось (занято другим воркером): задание=%s", job_id)

    def recover_expired_leases(self) -> list[str]:
        """Единственный механизм обнаружения осиротевших заданий (E3.4).

        Рабочие состояния с истёкшей арендой → INTERRUPTED (+ строка перехода);
        CREATED/PAUSED с истёкшей арендой (заняты, но не запущены) — только
        снятие аренды, статус не меняется.
        """
        now = _now()
        rows = self._db.conn.execute(
            """
            SELECT id, status, worker_pid FROM jobs
            WHERE worker_pid IS NOT NULL
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < ?
            """,
            (now,),
        ).fetchall()
        recovered: list[str] = []
        for row in rows:
            job_id = str(row["id"])
            status = JobStatus(row["status"])
            if status in WORKING_STATES:
                self.transition(
                    job_id,
                    JobStatus.INTERRUPTED,
                    reason=(
                        f"арена истекла (worker_pid={row['worker_pid']}); "
                        "восстановление при старте воркера"
                    ),
                    actor="recovery",
                )
            else:
                self._db.conn.execute(
                    "UPDATE jobs SET worker_pid = NULL, lease_expires_at = NULL, updated_at = ? "
                    "WHERE id = ?",
                    (now, job_id),
                )
                self._db.conn.commit()
                logger.info(
                    "снята истёкшая аренда незапущенного задания (%s): %s", status.value, job_id
                )
            recovered.append(job_id)
        return recovered

    # ------------------------------------------------------------------ очередь (E3.5)

    def next_claimable(self) -> Job | None:
        """Одно активное задание (E3.5): FIFO с ручной очередью (серия R §15:
        queue_rank выше — раньше; иначе по времени создания).

        PLANNING — после подтверждения подозрительной книги (арены нет);
        запись с действующей арендой не выбирается (занять её нельзя, E3.6).
        """
        now = _now()
        row = self._db.conn.execute(
            """
            SELECT * FROM jobs
            WHERE (
                    status IN ('CREATED', 'INTERRUPTED', 'PLANNING')
                    OR (status = 'PAUSED' AND resume_requested = 1)
              )
              AND (worker_pid IS NULL OR lease_expires_at < ?)
            ORDER BY queue_rank DESC, created_at ASC, id ASC
            LIMIT 1
            """,
            (now,),
        ).fetchone()
        return self._row_to_job(row) if row is not None else None

    def set_queue_rank(self, job_id: str, queue_rank: int) -> Job:
        """Серия R §15: единственное изменяемое поле задания из UI —
        очередь («Вперёд очереди»). Терминальные задания в очередь не
        встают — бессмысленно (JobStateError, как управляющие команды)."""
        job = self.require(job_id)
        if job.status in TERMINAL_STATES:
            raise JobStateError(
                f"queue_rank в терминальном состоянии {job.status.value}"
            )
        self._db.conn.execute(
            "UPDATE jobs SET queue_rank = ?, updated_at = ? WHERE id = ?",
            (queue_rank, _now(), job_id),
        )
        self._db.conn.commit()
        return self.require(job_id)

    def count_active(self) -> int:
        row = self._db.conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE worker_pid IS NOT NULL"
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    # ------------------------------------------------------- управляющие команды (E3.1)

    def request_cancel(self, job_id: str) -> Job:
        job = self.require(job_id)
        if job.status is JobStatus.PAUSED:
            # PAUSED-задание не исполняется: воркер занимает PAUSED только по
            # resume, поэтому флаг cancel_requested не применялся бы никогда —
            # отмена выполняется немедленным переходом (ребро PAUSED → CANCELLED).
            return self.transition(
                job_id,
                JobStatus.CANCELLED,
                reason="отмена из PAUSED (кооперативная, E3.8)",
                actor="api",
            )
        return self._flag(job_id, "cancel_requested")

    def request_pause(self, job_id: str) -> Job:
        return self._flag(job_id, "pause_requested")

    def request_resume(self, job_id: str) -> Job:
        job = self.require(job_id)
        if job.status is not JobStatus.PAUSED:
            raise JobStateError(
                f"resume доступно только из PAUSED (статус: {job.status.value})"
            )
        self._db.conn.execute(
            "UPDATE jobs SET resume_requested = 1, updated_at = ? WHERE id = ?",
            (_now(), job_id),
        )
        self._db.conn.commit()
        return self.require(job_id)

    def _flag(self, job_id: str, column: str) -> Job:
        job = self.require(job_id)
        if job.status in TERMINAL_STATES:
            raise JobStateError(
                f"команда {column} в терминальном состоянии {job.status.value}"
            )
        self._db.conn.execute(
            f"UPDATE jobs SET {column} = 1, updated_at = ? WHERE id = ?", (_now(), job_id)
        )
        self._db.conn.commit()
        return self.require(job_id)

    # ------------------------------------------------------------- прогресс и токены

    def set_progress(self, job_id: str, progress: float) -> None:
        self._db.conn.execute(
            "UPDATE jobs SET progress = MAX(progress, ?), updated_at = ? WHERE id = ?",
            (progress, _now(), job_id),
        )
        self._db.conn.commit()

    def set_alignment_coverage(self, job_id: str, coverage: float) -> None:
        """Доля выравнивания в статусе задания (E6.5). Единственный источник —
        aligner (alignment_report.json); учебные этапы читают это же значение."""
        self._db.conn.execute(
            "UPDATE jobs SET alignment_coverage = ?, updated_at = ? WHERE id = ?",
            (coverage, _now(), job_id),
        )
        self._db.conn.commit()

    def set_source_language(self, job_id: str, language: str) -> None:
        """Подтверждённый исходный язык (L0): ставится на стадии validating —
        решение детектора + dc:language (L0.5). Единственный писатель — воркер."""
        self._db.conn.execute(
            "UPDATE jobs SET source_language = ?, updated_at = ? WHERE id = ?",
            (language, _now(), job_id),
        )
        self._db.conn.commit()

    def add_tokens(self, job_id: str, profile: str, delta: int) -> None:
        if delta <= 0:
            return
        column = {
            "translation": "tokens_translation",
            "fill": "tokens_fill",
            "analysis": "tokens_analysis",
        }.get(profile)
        if column is None:
            raise JobStateError(f"неизвестный профиль токенов: {profile}")
        self._db.conn.execute(
            f"UPDATE jobs SET {column} = {column} + ?, updated_at = ? WHERE id = ?",
            (delta, _now(), job_id),
        )
        # Инвариант E4-5: tokens_total всегда равен сумме по профилям. Отдельное
        # UPDATE: в SQLite правые части SET вычисляются от исходных значений строки,
        # поэтому пересчёт в том же выражении недоучёл бы прирост.
        self._db.conn.execute(
            "UPDATE jobs SET tokens_total = tokens_translation + tokens_fill + tokens_analysis "
            "WHERE id = ?",
            (job_id,),
        )
        self._db.conn.commit()

    def add_profile_tokens(
        self,
        job_id: str,
        profile: str,
        input_delta: int,
        input_cache_delta: int,
        output_delta: int,
    ) -> None:
        """Разбивка токенов профиля (E4.7): накопление по job_tokens."""
        if input_delta <= 0 and input_cache_delta <= 0 and output_delta <= 0:
            return
        if profile not in ("translation", "fill", "analysis"):
            raise JobStateError(f"неизвестный профиль токенов: {profile}")
        self._db.conn.execute(
            """
            INSERT INTO job_tokens
                (job_id, profile, input_tokens, input_cache_tokens, output_tokens)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(job_id, profile) DO UPDATE SET
                input_tokens = input_tokens + excluded.input_tokens,
                input_cache_tokens = input_cache_tokens + excluded.input_cache_tokens,
                output_tokens = output_tokens + excluded.output_tokens
            """,
            (job_id, profile, input_delta, input_cache_delta, output_delta),
        )
        self._db.conn.commit()

    def profile_tokens(self, job_id: str) -> dict[str, ProfileTokens]:
        """Суммы токенов по профилям задания (разбивка, E4.7)."""
        rows = self._db.conn.execute(
            "SELECT * FROM job_tokens WHERE job_id = ?", (job_id,)
        ).fetchall()
        return {
            row["profile"]: ProfileTokens(
                profile=row["profile"],
                input_tokens=row["input_tokens"],
                input_cache_tokens=row["input_cache_tokens"],
                output_tokens=row["output_tokens"],
            )
            for row in rows
        }

    # ------------------------------------------------------------------ промпты (E4-2)

    def record_resolved_prompt(
        self, job_id: str, task: str, file: str, version: int, level: str
    ) -> None:
        """Фактически применённый промпт задачи (E4-2: записывается в задание)."""
        self._db.conn.execute(
            "INSERT INTO resolved_prompts (job_id, task, file, version, level, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, task, file, version, level, _now()),
        )
        self._db.conn.commit()

    def list_resolved_prompts(self, job_id: str) -> list[ResolvedPrompt]:
        rows = self._db.conn.execute(
            "SELECT * FROM resolved_prompts WHERE job_id = ? ORDER BY created_at, task",
            (job_id,),
        ).fetchall()
        return [
            ResolvedPrompt(
                job_id=row["job_id"],
                task=row["task"],
                file=row["file"],
                version=row["version"],
                level=row["level"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def set_error(self, job_id: str, message: str) -> None:
        self._db.conn.execute(
            "UPDATE jobs SET error_message = ?, updated_at = ? WHERE id = ?",
            (message, _now(), job_id),
        )
        self._db.conn.commit()

    # ------------------------------------------------------------------- этапы (E3.6)

    def record_stage_result(
        self,
        job_id: str,
        stage: str,
        input_hash: str,
        artifact_path: str | None,
        tokens: int = 0,
    ) -> None:
        self._db.conn.execute(
            """
            INSERT OR REPLACE INTO stage_results
                (job_id, stage, input_hash, artifact_path, tokens, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, stage, input_hash, artifact_path, tokens, _now()),
        )
        self._db.conn.commit()

    def get_stage_result(self, job_id: str, stage: str) -> StageResult | None:
        row = self._db.conn.execute(
            "SELECT * FROM stage_results WHERE job_id = ? AND stage = ?", (job_id, stage)
        ).fetchone()
        if row is None:
            return None
        return StageResult(
            stage=row["stage"],
            input_hash=row["input_hash"],
            artifact_path=row["artifact_path"] if row["artifact_path"] is not None else None,
            tokens=row["tokens"],
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------- служебное

    @staticmethod
    def _row_to_job(row: sqlite3.Row | None) -> Job | None:
        if row is None:
            return None
        interrupted_from = row["interrupted_from"]
        paused_from = row["paused_from"]
        return Job(
            id=row["id"],
            status=JobStatus(row["status"]),
            source_path=row["source_path"],
            target_language=row["target_language"],
            mode=row["mode"],
            priority=row["priority"],
            queue_rank=row["queue_rank"],
            progress=row["progress"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            confirmed_at=row["confirmed_at"],
            interrupted_from=(
                JobStatus(interrupted_from) if interrupted_from is not None else None
            ),
            paused_from=JobStatus(paused_from) if paused_from is not None else None,
            resume_requested=bool(row["resume_requested"]),
            cancel_requested=bool(row["cancel_requested"]),
            pause_requested=bool(row["pause_requested"]),
            worker_pid=row["worker_pid"],
            lease_expires_at=row["lease_expires_at"],
            input_sha256=row["input_sha256"],
            config_sha256=row["config_sha256"],
            prompt_version=row["prompt_version"],
            tokens_translation=row["tokens_translation"],
            tokens_fill=row["tokens_fill"],
            tokens_analysis=row["tokens_analysis"],
            tokens_total=row["tokens_total"],
            alignment_coverage=row["alignment_coverage"],
            source_language=row["source_language"],
            learning_level=row["learning_level"],
            learning_cefr=row["learning_cefr"],
            param_chips=row["param_chips"],
            error_message=row["error_message"],
        )
