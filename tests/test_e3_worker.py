"""T-E3: воркер, машина состояний, аренда, идемпотентность (CONTROL E3-1…E3-6).

Тесты:
- T-E3-1 — цикл: старт → ~30% сегментов → kill -9 → старт → resume → завершение
  (+ замер повторного перевода, E3-1, E3-4).
- T-E3-2 — таблица переходов: полный перебор всех пар 14×14 (E3-2).
- T-E3-3 — двойной запуск одного задания (E3-6).
- T-E3-4 — симуляция перезагрузки хоста → задание восстановимо (E3-1).
- Дополнительно: E3-3 (идемпотентность повторного запуска этапа),
  E3-5 (кооперативная отмена с пометкой частичного артефакта),
  поток NEEDS_CONFIRMATION → confirm → возобновление.

Воркер запускается как отдельный процесс (python -m app.workers.run) —
единственный писатель рабочих состояний (E3.1); тест читает БД и отдаёт
управляющие команды (разрешённые writes API-стороны).
"""

from __future__ import annotations

import hashlib
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.core.config import Settings, load_settings
from app.core.errors import JobStateError
from app.domain.job import TRANSITIONS, WORKING_STATES, Job, JobStatus
from app.services.jobs import confirm_job, create_job
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository, TransitionRecord
from app.workers.pipeline import Pipeline

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "epub"
ROOT = Path(__file__).resolve().parents[1]
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from mock_llm_server import MockLLMServer  # noqa: E402

#: Тестовые параметры аренды: lease = 2×heartbeat (верхняя граница E3-1).
HEARTBEAT = 0.5
LEASE = 1.0
SERVER_EXTRA = f"server:\n  heartbeat_seconds: {HEARTBEAT}\n  lease_seconds: {LEASE}\n"
#: Задержка ответа mock-сервера для детерминированного kill внутри перевода.
#: 1.5 с → фаза перевода ~6 с, kill гарантированно позже истечения исходной
#: аренды (1.0 с): без продления heartbeat арена на момент kill была бы истекшей.
KILL_DELAY = 1.5
#: Адрес по умолчанию для YAML-профилей, когда mock-сервер не нужен
#: (совпадает с константой conftest — тестовая фикстура, не production-значение).
DEFAULT_LLM_BASE_URL = "http://127.0.0.1:9999/v1"

_COMPLEX = FIXTURES / "complex.epub"
_SUSPICIOUS = FIXTURES / "lang_de.epub"


@pytest.fixture()
def mock_server() -> Iterator[MockLLMServer]:
    server = MockLLMServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


# ------------------------------------------------------------------ служебное


def _settings_for(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
    server: MockLLMServer | None = None,
) -> tuple[Settings, Database, JobRepository]:
    """Настройки + БД + репозиторий на изолированном storage.root.

    E4.1: адрес LLM задаётся в YAML-профилях (base_url в make_yaml), в .env —
    только секреты и адрес бинда.
    """
    cfg = make_yaml(
        str(tmp_path / "storage"),
        SERVER_EXTRA,
        base_url=server.url if server is not None else DEFAULT_LLM_BASE_URL,
    )
    env = make_env(None)
    settings = load_settings(cfg, env)
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    return settings, db, JobRepository(db)


def _start_worker(cfg: Path, env: Path, log_path: Path) -> subprocess.Popen[bytes]:
    """Воркер как отдельный процесс (stdout+stderr → log_path)."""
    cmd = [
        sys.executable,
        "-m",
        "app.workers.run",
        "--config",
        str(cfg),
        "--env",
        str(env),
    ]
    handle = log_path.open("w", encoding="utf-8")
    try:
        return subprocess.Popen(cmd, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
    finally:
        handle.close()


def _wait_job(
    repo: JobRepository, job_id: str, predicate: Callable[[Job], bool], timeout: float, what: str
) -> Job:
    deadline = time.monotonic() + timeout
    last: Job | None = None
    while time.monotonic() < deadline:
        job = repo.get(job_id)
        if job is not None:
            last = job
            if predicate(job):
                return job
        time.sleep(0.05)
    status = None if last is None else last.status.value
    progress = None if last is None else last.progress
    raise TimeoutError(f"{what}: последнее состояние status={status} progress={progress}")


def _kill_all(workers: list[subprocess.Popen[bytes]]) -> None:
    for proc in workers:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def _force_status(db: Database, job_id: str, status: JobStatus) -> None:
    """Тестовая фиксация статуса (для полного перебора таблицы переходов)."""
    interrupted_from = JobStatus.TRANSLATING.value if status is JobStatus.INTERRUPTED else None
    paused_from = JobStatus.ANALYZING.value if status is JobStatus.PAUSED else None
    db.conn.execute(
        "UPDATE jobs SET status = ?, interrupted_from = ?, paused_from = ?, "
        "resume_requested = 0, cancel_requested = 0, pause_requested = 0 WHERE id = ?",
        (status.value, interrupted_from, paused_from, job_id),
    )
    db.conn.commit()


def _expected_allowed(from_status: JobStatus, to_status: JobStatus) -> bool:
    """Ожидаемый результат перехода (таблица + resume-правило)."""
    if to_status not in TRANSITIONS[from_status]:
        return False
    if from_status is JobStatus.INTERRUPTED and to_status in WORKING_STATES:
        return to_status is JobStatus.TRANSLATING  # зафиксированное interrupted_from
    if from_status is JobStatus.PAUSED and to_status in WORKING_STATES:
        return to_status is JobStatus.ANALYZING  # зафиксированное paused_from
    return True


# ------------------------------------------------------------------ T-E3-1


@dataclass(frozen=True)
class _KillResumeResult:
    """Измерения сценария kill -9 → resume (T-E3-1)."""

    job_id: str
    x_tokens: int
    y_tokens: int
    lease_at_kill: float
    lease_delay: float
    final_status: JobStatus
    final_progress: float
    recovery: list[TransitionRecord]
    resume: list[TransitionRecord]
    job_dir: Path


def _run_reference_job(
    settings: Settings, repo: JobRepository, cfg: Path, env: Path, tmp_path: Path
) -> int:
    """Эталонный полный прогон без прерываний. Возвращает Z (total_tokens)."""
    job_ref = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
    w_ref = _start_worker(cfg, env, tmp_path / "w_ref.log")
    try:
        assert w_ref.wait(timeout=180) == 0
    finally:
        _kill_all([w_ref])
    final_ref = repo.require(job_ref.id)
    assert final_ref.status is JobStatus.COMPLETED
    z = final_ref.tokens_translation + final_ref.tokens_fill
    assert z > 0
    return z


def _kill_and_resume(
    settings: Settings,
    repo: JobRepository,
    *,
    cfg: Path,
    env: Path,
    tmp_path: Path,
    mock_server: MockLLMServer,
) -> _KillResumeResult:
    """Kill -9 на ~30% сегментов, ожидание истечения аренды, возобновление."""
    mock_server.set_delay(KILL_DELAY)
    job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
    w1 = _start_worker(cfg, env, tmp_path / "w1.log")
    try:
        # Точка kill: движок пишет on_progress(0.4/0.7/1.0) пачкой в конце
        # фазы (замеры E1/E3: для complex.epub это milliseconds), поэтому
        # progress ненадёжен. Надёжный сигнал — токены в БД: они flush'ятся
        # после первой завершённой группы (TOC+META), окно до конца фазы ~3 с.
        _wait_job(
            repo,
            job.id,
            lambda j: (
                j.status is JobStatus.TRANSLATING
                and j.tokens_translation + j.tokens_fill > 0
            ),
            120.0,
            "ожидание завершённой группы в TRANSLATING (токены в БД)",
        )
        t_kill = time.time()
        state_at_kill = repo.require(job.id)
        assert state_at_kill.status is JobStatus.TRANSLATING
        assert state_at_kill.interrupted_from is None
        lease_at_kill = state_at_kill.lease_expires_at
        assert lease_at_kill is not None
        # Регрессия heartbeat-дефекта: kill позже истечения исходной аренды
        # (KILL_DELAY > lease), значит арена действенна только если продлевается.
        assert lease_at_kill >= t_kill, (
            "арена истекла при живом воркере — heartbeat не продлевает аренду"
        )
        x = state_at_kill.tokens_translation + state_at_kill.tokens_fill
        assert x > 0
        os.kill(w1.pid, signal.SIGKILL)
        assert w1.wait(timeout=20) == -9
        # истечение аренды — детерминированная граница осиротения
        time.sleep(max(0.0, lease_at_kill - time.time()) + 0.1)
    finally:
        _kill_all([w1])
    w2 = _start_worker(cfg, env, tmp_path / "w2.log")
    try:
        assert w2.wait(timeout=180) == 0
    finally:
        _kill_all([w2])
    final = repo.require(job.id)
    transitions = repo.list_transitions(job.id)
    recovery = [
        t for t in transitions if t.to_status is JobStatus.INTERRUPTED and t.actor == "recovery"
    ]
    resume = [
        t
        for t in transitions
        if t.from_status is JobStatus.INTERRUPTED and t.to_status is JobStatus.TRANSLATING
    ]
    y = (final.tokens_translation + final.tokens_fill) - x
    assert y >= 0
    return _KillResumeResult(
        job_id=job.id,
        x_tokens=x,
        y_tokens=y,
        lease_at_kill=lease_at_kill,
        lease_delay=lease_at_kill - t_kill,
        final_status=final.status,
        final_progress=final.progress,
        recovery=recovery,
        resume=resume,
        job_dir=settings.jobs_dir / job.id,
    )


def test_e3_1_kill9_resume_no_retranslation(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[[str, str], Path],
    mock_server: MockLLMServer,
) -> None:
    """T-E3-1 (E3-1, E3-4): kill -9 на ~30% сегментов → восстановление → завершение.

    E3-1: задание в INTERRUPTED 100%, окно осиротения ≤ 2×heartbeat.
    E3-4: доля повторного перевода (по приросту total_tokens) ≤ 5%.
    """
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, mock_server)
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / "test.env"
    try:
        z = _run_reference_job(settings, repo, cfg_path, env_path, tmp_path)
        res = _kill_and_resume(
            settings, repo, cfg=cfg_path, env=env_path, tmp_path=tmp_path, mock_server=mock_server
        )

        assert res.final_status is JobStatus.COMPLETED
        assert res.final_progress == pytest.approx(1.0)

        # E3-1: INTERRUPTED зафиксирован (100%); окно осиротения ≤ 2×heartbeat
        assert len(res.recovery) == 1
        assert "арена истекла" in res.recovery[0].reason
        assert res.recovery[0].at >= res.lease_at_kill - 0.05, (
            "восстановление до истечения аренды"
        )
        assert res.lease_delay <= 2 * HEARTBEAT + 0.05, (
            f"окно осиротения {res.lease_delay:.3f} > 2×heartbeat {2 * HEARTBEAT}"
        )
        # resume — только в сохранённое состояние (TRANSLATING)
        assert len(res.resume) == 1

        # E3-4: доля повторного перевода по приросту total_tokens: max(0, X+Y-Z)/Z ≤ 5%
        share = max(0, res.x_tokens + res.y_tokens - z) / z
        assert share <= 0.05, f"доля повторного перевода {share:.3%} > 5%"

        # артефакты и логи задания (E3.9)
        assert (res.job_dir / "target.epub").is_file()
        for name in ("job.log", "errors.log", "quality.log"):
            assert (res.job_dir / name).is_file(), f"нет лога задания: {name}"
    finally:
        db.close()


# ------------------------------------------------------------------ T-E3-2


def test_e3_2_transition_table_exhaustive(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[[str, str], Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T-E3-2 (E3-2): полный перебор 14×14 пар; каждое отклонение — ошибка + запись в лог."""
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, None)
    job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
    try:
        with caplog.at_level(logging.WARNING, logger="app.jobs"):
            allowed = 0
            rejected = 0
            for from_status in JobStatus:
                for to_status in JobStatus:
                    _force_status(db, job.id, from_status)
                    if _expected_allowed(from_status, to_status):
                        repo.transition(job.id, to_status, reason="полный перебор")
                        allowed += 1
                    else:
                        with pytest.raises(JobStateError):
                            repo.transition(job.id, to_status, reason="полный перебор")
                        rejected += 1
        assert allowed + rejected == len(JobStatus) ** 2
        # точный перебор: 44 допустимые, 152 отклонённые пары (таблица + resume-правило)
        assert allowed == 44
        assert rejected == 152
        # E3-2: каждое отклонение зафиксировано в логе
        logged = [r for r in caplog.records if "недопустимый переход отклонён" in r.getMessage()]
        assert len(logged) == rejected
        # отклонённые переходы не фиксируются в transitions
        row = db.conn.execute("SELECT COUNT(*) FROM transitions").fetchone()
        assert row is not None
        assert int(row[0]) == allowed
    finally:
        db.close()


# ------------------------------------------------------------------ E3-3


def test_e3_3_stage_rerun_idempotent(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[[str, str], Path],
    mock_server: MockLLMServer,
) -> None:
    """E3-3: повторный запуск завершённого этапа не изменяет артефакт (хэш совпадает)."""
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, mock_server)
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        pipeline = Pipeline(settings)
        job_now = repo.require(job.id)
        assert pipeline.run_validating(repo, job_now) is False
        pipeline.run_planning(repo, job_now)
        pipeline.run_translating(repo, job_now, lambda: None)
        target = settings.jobs_dir / job.id / "target.epub"
        assert target.is_file()
        hash_1 = hashlib.sha256(target.read_bytes()).hexdigest()
        requests_after_first = len(mock_server.requests_log())
        assert requests_after_first >= 2, "ожидается ≥ 2 translate-запросов (2 группы)"

        # повтор того же этапа: ключ идемпотентности совпал → без повторного перевода
        pipeline.run_translating(repo, job_now, lambda: None)
        hash_2 = hashlib.sha256(target.read_bytes()).hexdigest()
        assert hash_2 == hash_1, "повторный запуск этапа изменил артефакт"
        assert len(mock_server.requests_log()) == requests_after_first, (
            "повторный запуск породил новые LLM-запросы"
        )
        result = repo.get_stage_result(job.id, "translating")
        assert result is not None
        assert result.input_hash == pipeline.input_hash(job_now)
        assert result.tokens > 0
    finally:
        db.close()


# ------------------------------------------------------------------ T-E3-3 / E3-6


def test_e3_4_double_worker_single_execution(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[[str, str], Path],
    mock_server: MockLLMServer,
) -> None:
    """T-E3-3 (E3-6): двойной запуск — второе занятие не происходит, 0 коллизий."""
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, mock_server)
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / "test.env"
    job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
    w1 = _start_worker(cfg_path, env_path, tmp_path / "w1.log")
    w2 = _start_worker(cfg_path, env_path, tmp_path / "w2.log")
    try:
        assert w1.wait(timeout=180) == 0
        assert w2.wait(timeout=180) == 0
    finally:
        _kill_all([w1, w2])
        db.close()

    out1 = (tmp_path / "w1.log").read_text(encoding="utf-8")
    out2 = (tmp_path / "w2.log").read_text(encoding="utf-8")
    with Database(settings.jobs_dir / "jobs.db", settings.storage.busy_timeout_ms) as db2:
        repo2 = JobRepository(db2)
        final = repo2.require(job.id)
        assert final.status is JobStatus.COMPLETED

        # ровно один воркер выполнил задание
        winners = [out for out in (out1, out2) if "status=COMPLETED" in out]
        assert len(winners) == 1, "задание выполнено не одним воркером"
        loser = out2 if "status=COMPLETED" in out1 else out1
        assert ("status=CREATED" in loser) or ("очередь пуста" in loser), (
            "второй воркер выполнил работу: " + loser[-500:]
        )

        # 0 коллизий: ровно один переход CREATED→VALIDATING
        transitions = repo2.list_transitions(job.id)
        first_steps = [
            t
            for t in transitions
            if t.from_status is JobStatus.CREATED and t.to_status is JobStatus.VALIDATING
        ]
        assert len(first_steps) == 1
        # ровно один перевод: 2 translate-запроса (2 группы complex.epub)
        translate_requests = [r for r in mock_server.requests_log() if r["kind"] == "translate"]
        assert len(translate_requests) == 2


# ------------------------------------------------------------------ T-E3-4


def test_e3_5_reboot_simulation_recoverable(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[[str, str], Path],
    mock_server: MockLLMServer,
) -> None:
    """T-E3-4 (E3-1): симуляция перезагрузки хоста (kill -9) → БД цела, задание восстановимо."""
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, mock_server)
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / "test.env"
    job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
    workers: list[subprocess.Popen[bytes]] = []
    try:
        mock_server.set_delay(0.3)
        w1 = _start_worker(cfg_path, env_path, tmp_path / "w1.log")
        workers.append(w1)
        _wait_job(
            repo,
            job.id,
            lambda j: j.status is JobStatus.TRANSLATING,
            60.0,
            "ожидание TRANSLATING",
        )
        t_kill = time.time()
        os.kill(w1.pid, signal.SIGKILL)
        assert w1.wait(timeout=20) == -9
        # чтение после смерти воркера: состояние заморожено — это состояние на момент kill;
        # любое рабочее состояние допустимо (kill может попасть в микросекундное окно
        # перехода в следующее состояние) — resume обязан вернуть ровно его
        state_at_kill = repo.require(job.id)
        assert state_at_kill.status in WORKING_STATES

        # «перезагрузка»: целостность БД после аварийного завершения
        assert db.integrity_check() == "ok"
        lease_at_kill = state_at_kill.lease_expires_at
        assert lease_at_kill is not None
        time.sleep(max(0.0, lease_at_kill - time.time()) + 0.1)

        w2 = _start_worker(cfg_path, env_path, tmp_path / "w2.log")
        workers.append(w2)
        assert w2.wait(timeout=180) == 0
        final = repo.require(job.id)
        assert final.status is JobStatus.COMPLETED

        transitions = repo.list_transitions(job.id)
        recovery = [
            t for t in transitions if t.to_status is JobStatus.INTERRUPTED and t.actor == "recovery"
        ]
        assert len(recovery) == 1
        assert recovery[0].at >= lease_at_kill - 0.05
        assert lease_at_kill - t_kill <= 2 * HEARTBEAT + 0.05
        # resume — ровно в состояние, в котором прервались
        resume = [
            t
            for t in transitions
            if t.from_status is JobStatus.INTERRUPTED and t.to_status is state_at_kill.status
        ]
        assert len(resume) == 1
        assert (settings.jobs_dir / job.id / "target.epub").is_file()
    finally:
        _kill_all(workers)
        db.close()


# ------------------------------------------------------------------ E3-5


def test_e3_6_cooperative_cancel_marks_partial(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[[str, str], Path],
    mock_server: MockLLMServer,
) -> None:
    """E3-5: отмена во время перевода — CANCELLED ≤ 30 с, частичный артефакт помечен."""
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, mock_server)
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / "test.env"
    job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
    mock_server.set_delay(0.2)
    workers: list[subprocess.Popen[bytes]] = []
    try:
        w1 = _start_worker(cfg_path, env_path, tmp_path / "w1.log")
        workers.append(w1)
        _wait_job(repo, job.id, lambda j: j.progress >= 0.05, 120.0, "ожидание старта перевода")
        t_flag = time.time()
        repo.request_cancel(job.id)
        assert w1.wait(timeout=60) == 0
        final = repo.require(job.id)
        assert final.status is JobStatus.CANCELLED
        transitions = repo.list_transitions(job.id)
        cancelled = [t for t in transitions if t.to_status is JobStatus.CANCELLED]
        assert len(cancelled) == 1
        assert cancelled[0].at - t_flag <= 30, "отмена не выполнена за 30 с"
        job_dir = settings.jobs_dir / job.id
        assert (job_dir / "target.epub.partial").is_file(), "частичный артефакт не помечен"
        assert not (job_dir / "target.epub").exists(), "непомеченный target.epub остался"
    finally:
        _kill_all(workers)
        db.close()


# ------------------------------------------------------------------ NEEDS_CONFIRMATION


def test_e3_7_needs_confirmation_then_resume(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[[str, str], Path],
    mock_server: MockLLMServer,
) -> None:
    """Подозрительная книга: NEEDS_CONFIRMATION → confirm → возобновление до COMPLETED."""
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, mock_server)
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / "test.env"
    job = create_job(repo, settings, source_path=_SUSPICIOUS, target_language="it")
    workers: list[subprocess.Popen[bytes]] = []
    try:
        w1 = _start_worker(cfg_path, env_path, tmp_path / "w1.log")
        workers.append(w1)
        assert w1.wait(timeout=180) == 0
        stopped = repo.require(job.id)
        assert stopped.status is JobStatus.NEEDS_CONFIRMATION
        assert stopped.worker_pid is None, "арена не снята в NEEDS_CONFIRMATION"
        assert (settings.jobs_dir / job.id / "validation.json").is_file()

        # подтверждение (управляющая команда API)
        confirmed = confirm_job(repo, job.id)
        assert confirmed.status is JobStatus.PLANNING
        assert confirmed.confirmed_at is not None

        # второй запуск занимает подтверждённое задание (PLANNING без аренды)
        w2 = _start_worker(cfg_path, env_path, tmp_path / "w2.log")
        workers.append(w2)
        assert w2.wait(timeout=180) == 0
        final = repo.require(job.id)
        assert final.status is JobStatus.COMPLETED

        transitions = repo.list_transitions(job.id)
        confirms = [
            t
            for t in transitions
            if t.from_status is JobStatus.NEEDS_CONFIRMATION and t.to_status is JobStatus.PLANNING
        ]
        assert len(confirms) == 1
        assert confirms[0].actor == "api"
        planning_starts = [
            t
            for t in transitions
            if t.from_status is JobStatus.PLANNING and t.to_status is JobStatus.TRANSLATING
        ]
        assert len(planning_starts) == 1
    finally:
        _kill_all(workers)
        db.close()
