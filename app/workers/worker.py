"""Воркер заданий (E3): восстановление, аренда, одно активное задание, кооперативный контроль.

- При старте: задания с истёкшей арендой → INTERRUPTED — единственный механизм
  обнаружения осиротевших заданий (E3.4). «Задание висит в рабочем состоянии»
  без аренды недопустимо.
- Переход состояния фиксируется в БД до побочного эффекта (E3.3): переход в
  рабочее состояние записывается до начала работы этого состояния.
- Одновременно активное задание — одно (E3.5): занятие — атомарный UPDATE;
  второй воркер не захватывает занятое задание (E3.6).
- Отмена/пауза — кооперативные (E3.8): флаг проверяется на границах групп
  (on_progress движка) и между этапами.
- Логи задания: jobs/<job_id>/job.log, errors.log, quality.log (E3.9).
"""

from __future__ import annotations

import logging
import os
import threading
import time

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import attach_job_logs, detach_job_logs, job_context
from app.core.worker_signal import write_heartbeat
from app.domain.job import NEXT_STAGE, WORKING_STATES, Job, JobStatus
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.workers.controls import LeaseLost, WorkerCancelled, WorkerPaused
from app.workers.pipeline import Pipeline

logger = logging.getLogger("app.worker")


class Worker:
    def __init__(self, settings: Settings, repo: JobRepository) -> None:
        self._settings = settings
        self._repo = repo
        self._pipeline = Pipeline(settings)
        self._pid = os.getpid()
        self._stop_event = threading.Event()
        self._lease_lost = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._active_job_id: str | None = None

    # ------------------------------------------------------------------ публичное

    def startup(self) -> list[str]:
        """Восстановление осиротевших заданий при старте (E3.4). Возвращает job_id."""
        recovered = self._repo.recover_expired_leases()
        for job_id in recovered:
            logger.info("восстановлено осиротевшее задание: %s", job_id)
        return recovered

    def run_once(self) -> Job | None:
        """Взять одно задание (если есть) и выполнить до терминального/стоп-состояния."""
        self.startup()
        job = self._repo.next_claimable()
        if job is None:
            return None
        return self._execute(job)

    def run_forever(self, poll_seconds: float = 1.0) -> None:
        """Непрерывный цикл: одно активное задание, затем следующее из очереди."""
        self.startup()
        while True:
            job = self._repo.next_claimable()
            if job is None:
                time.sleep(poll_seconds)
                self.write_alive()
                self.startup()
                continue
            self._execute(job)

    # ------------------------------------------------------------------ внутреннее

    def _execute(self, job: Job) -> Job:
        lease = self._settings.server.lease_seconds
        if not self._repo.claim(job.id, self._pid, lease):
            logger.info("задание не занято (арена занята другим): %s", job.id)
            return job
        handlers = attach_job_logs(self._settings.job_dir(job.id), self._settings.secrets())
        try:
            with job_context(job.id):
                return self._run(job)
        finally:
            detach_job_logs(handlers)

    def _run(self, job: Job) -> Job:
        try:
            self._check_control(job.id)
            self._resume_if_needed(job.id)
            self._active_job_id = job.id
            self._start_heartbeat()
            job_now = self._repo.require(job.id)
            state = job_now.status if job_now.status in WORKING_STATES else JobStatus.VALIDATING
            while state in WORKING_STATES:
                self._check_control(job.id)
                next_state = self._run_stage(job.id, state)
                if next_state is None:
                    break
                state = next_state
        except WorkerCancelled:
            self._finalize_cancelled(job.id)
        except WorkerPaused:
            self._finalize_paused(job.id)
        except LeaseLost:
            logger.error(
                "арена потеряна — останавливаюсь, состояние не меняю: задание=%s", job.id
            )
        except AppError as exc:
            self._finalize_failed(job.id, exc)
        except Exception as exc:  # непредвиденное: fail closed (Rule 6)
            self._finalize_failed(job.id, AppError(f"непредвиденная ошибка: {exc!r}"))
        finally:
            self._stop_heartbeat()
        return self._repo.require(job.id)

    def _resume_if_needed(self, job_id: str) -> None:
        job_now = self._repo.require(job_id)
        if job_now.status is JobStatus.INTERRUPTED and job_now.interrupted_from is not None:
            self._repo.transition(
                job_id,
                job_now.interrupted_from,
                reason="resume: перезапуск воркера, арена истекла",
            )
        elif job_now.status is JobStatus.PAUSED and job_now.paused_from is not None:
            self._repo.transition(
                job_id, job_now.paused_from, reason="resume: управляющая команда"
            )

    def _run_stage(self, job_id: str, state: JobStatus) -> JobStatus | None:
        """Переход в `state` (до побочного эффекта, E3.3) → исполнение → следующее состояние.

        Возвращает None, если конвейер останавливается (NEEDS_CONFIRMATION или
        терминальное состояние после LEARNING — финального рабочего этапа,
        S1–S5, ТЗ_03).
        """
        current = self._repo.require(job_id).status
        if current is not state:
            self._repo.transition(job_id, state, reason="начало этапа")
        job = self._repo.require(job_id)

        if state is JobStatus.VALIDATING:
            if self._pipeline.run_validating(self._repo, job):
                self._check_control(job_id)
                self._repo.transition(
                    job_id,
                    JobStatus.NEEDS_CONFIRMATION,
                    reason=self._confirmation_reason(job_id),
                )
                self._repo.release(job_id, self._pid)
                return None
            self._check_control(job_id)
        elif state is JobStatus.PLANNING:
            self._pipeline.run_planning(self._repo, job)
            self._check_control(job_id)
        elif state is JobStatus.TRANSLATING:
            self._pipeline.run_translating(
                self._repo, job, lambda: self._check_control(job_id)
            )
            self._check_control(job_id)
        elif state is JobStatus.ANALYZING:
            self._pipeline.run_analyzing(self._repo, job)
            self._check_control(job_id)
        elif state is JobStatus.GENERATING:
            self._pipeline.run_generating(self._repo, job)
            self._check_control(job_id)
        elif state is JobStatus.VALIDATING_OUTPUT:
            # Предупреждения записаны в quality.json стадией (единый источник);
            # финальный статус — по итогам LEARNING (L3/S могут добавить свои).
            self._pipeline.run_validating_output(self._repo, job)
            self._check_control(job_id)
        elif state is JobStatus.GLOSSARY_FIX:
            # L3: после правки — LEARNING (S1–S5, ТЗ_03); halt (L3.6) —
            # AppError → FAILED (D63).
            self._pipeline.run_glossary_fix(self._repo, job)
            self._check_control(job_id)
        else:  # LEARNING — финальный рабочий этап: терминальный статус по итогам
            warnings = self._pipeline.run_learning(self._repo, job)
            final = JobStatus.COMPLETED_WITH_WARNINGS if warnings else JobStatus.COMPLETED
            self._repo.transition(
                job_id, final, reason="результат валидирован (вкл. L3, S)"
            )
            return None
        return NEXT_STAGE[state]

    def _confirmation_reason(self, job_id: str) -> str:
        """Причина NEEDS_CONFIRMATION (E2.7 + L0.5): подозрительная книга и/или
        язык. Строится из validation.json — единый источник с пайплайном (Rule 8)."""
        try:
            payload = self._pipeline.validation(job_id)
        except (OSError, ValueError):
            return "подозрительная книга (требует подтверждения, E2.7)"
        parts: list[str] = []
        if payload.get("suspicious"):
            parts.append("подозрительная книга (требует подтверждения, E2.7)")
        detection = payload.get("language_detection") or {}
        for reason in detection.get("reasons", ()):
            parts.append(f"язык: {reason}")
        return "; ".join(parts) or "требует подтверждения"

    # ------------------------------------------------------------- контроль (E3.8)

    def _check_control(self, job_id: str) -> None:
        if self._lease_lost.is_set():
            raise LeaseLost()
        job_now = self._repo.get(job_id)
        if job_now is None:
            return
        if job_now.cancel_requested:
            raise WorkerCancelled()
        if job_now.pause_requested:
            raise WorkerPaused()

    def _finalize_cancelled(self, job_id: str) -> None:
        job_now = self._repo.require(job_id)
        if job_now.status in WORKING_STATES or job_now.status is JobStatus.CREATED:
            self._repo.transition(
                job_id, JobStatus.CANCELLED, reason="отмена (кооперативная, E3.8)"
            )
        # E5.6: движок пишет результат только через `.partial` → `os.replace`,
        # поэтому в момент отмены незавершённый файл уже помечен (`.partial`),
        # а существующий target.epub — предыдущий ВАЛИДНЫЙ результат: не трогаем.
        job_dir = self._settings.job_dir(job_id)
        partial = job_dir / "target.epub.partial"
        target = job_dir / "target.epub"
        if partial.exists():
            logger.warning(
                "незавершённый артефакт оставлен без изменений: %s (не является результатом, E5.6)",
                partial.name,
            )
        if target.exists():
            logger.info("существующий валидный результат сохранён: %s", target.name)
        logger.info("задание отменено: %s", job_id)

    def _finalize_paused(self, job_id: str) -> None:
        job_now = self._repo.require(job_id)
        if job_now.status in WORKING_STATES or job_now.status is JobStatus.CREATED:
            self._repo.transition(job_id, JobStatus.PAUSED, reason="пауза (кооперативная)")
        logger.info("задание на паузе: %s", job_id)

    def _finalize_failed(self, job_id: str, exc: AppError) -> None:
        job_now = self._repo.require(job_id)
        if job_now.status in WORKING_STATES or job_now.status is JobStatus.CREATED:
            self._repo.transition(job_id, JobStatus.FAILED, reason=f"сбой этапа: {exc.message}")
        else:
            logger.error("сбой в не-рабочем состоянии %s: %s", job_now.status.value, exc.message)
        self._repo.set_error(job_id, exc.message)
        logger.error("задание завершено ошибкой: %s — %s", job_id, exc.message)

    # ------------------------------------------------------------------ heartbeat

    def write_alive(self) -> None:
        """Сигнал «воркер жив» для UI/health (D87). Ошибка записи не
        прерывает цикл: статус деградирует до «оффлайн», не ломая воркер."""
        try:
            write_heartbeat(self._settings, self._pid)
        except OSError as exc:
            logger.warning("heartbeat воркера не записан: %s", exc)

    def _start_heartbeat(self) -> None:
        self._stop_event.clear()
        self._lease_lost.clear()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        """Продление аренды с собственным thread-local подключением.

        sqlite3-соединение `self._repo` создано в главной нити
        (check_same_thread=True): использование его из heartbeat-нити —
        ProgrammingError, и арена никогда не продлевалась (дефект, найден
        в логах: долгое задание ошибочно осиротевало).
        """
        db = Database(self._settings.jobs_db_path, self._settings.storage.busy_timeout_ms)
        hb_repo = JobRepository(db)
        try:
            period = self._settings.server.heartbeat_seconds
            lease = self._settings.server.lease_seconds
            while not self._stop_event.wait(period):
                job_id = self._active_job_id
                if job_id is None:
                    return
                # Глобальный «воркер жив» — и во время активного задания
                # (главный цикл заблокирован в _execute, D87).
                self.write_alive()
                if not hb_repo.heartbeat(job_id, self._pid, lease):
                    logger.error("арена продлена не была (занято другим): %s", job_id)
                    self._lease_lost.set()
                    return
        finally:
            db.close()

    def _stop_heartbeat(self) -> None:
        self._stop_event.set()
        thread = self._heartbeat_thread
        if thread is not None:
            thread.join(timeout=self._settings.server.lease_seconds + 1.0)
            self._heartbeat_thread = None
        self._active_job_id = None
        self._lease_lost.clear()
