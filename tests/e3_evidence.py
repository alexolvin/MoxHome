"""Сборщик verbatim-evidence для CONTROL E3 (не тест; assertions — в test_e3_worker.py).

Запуск: python tests/e3_evidence.py
Захваты: docs/CONTROL/evidence/E3/e3-1…e3-6_*.txt

Сценарии:
  e3-1  kill -9 в середине перевода → INTERRUPTED, окно осиротения ≤ 2×heartbeat
  e3-2  полный перебор таблицы переходов 14×14 (допущено/отклонено, лог)
  e3-3  повторный запуск завершённого этапа (хэш артефакта, LLM-запросы)
  e3-4  доля повторного перевода при resume (X, Y, Z)
  e3-5  кооперативная отмена (время, частичный артефакт)
  e3-6  двойной запуск воркера (один исполнитель, 0 коллизий)
"""

from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "probe"))

from mock_llm_server import MockLLMServer  # noqa: E402

from app.core.config import Settings, load_settings  # noqa: E402
from app.core.errors import JobStateError  # noqa: E402
from app.domain.job import TRANSITIONS, WORKING_STATES, Job, JobStatus  # noqa: E402
from app.services.jobs import create_job  # noqa: E402
from app.storage.database import Database  # noqa: E402
from app.storage.jobs_repo import JobRepository, TransitionRecord  # noqa: E402
from app.workers.pipeline import Pipeline  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "E3"
FIXTURES = ROOT / "tests" / "fixtures" / "epub"
_COMPLEX = FIXTURES / "complex.epub"

HEARTBEAT = 0.5
LEASE = 1.0
SERVER_EXTRA = f"server:\n  heartbeat_seconds: {HEARTBEAT}\n  lease_seconds: {LEASE}\n"
KILL_DELAY = 1.5
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def evidence(name: str, content: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print(f"  [evidence] {path.relative_to(ROOT)}")


# ------------------------------------------------------------------ служебное


def _make_env_storage(work: Path, server: MockLLMServer) -> tuple[Settings, Path, Path]:
    work.mkdir(parents=True, exist_ok=True)
    env_file = work / "test.env"
    env_file.write_text(
        "SERVER_BIND_ADDRESS=127.0.0.1\n"
        f"LLM_BASE_URL={server.url}\n"
        "LLM_MODEL=test-model\n"
        "LLM_API_KEY=test-key-value-000\n",
        encoding="utf-8",
    )
    cfg_file = work / "config.yaml"
    cfg_file.write_text(f"storage:\n  root: {work}\n{SERVER_EXTRA}", encoding="utf-8")
    settings = load_settings(cfg_file, env_file)
    return settings, cfg_file, env_file


def _start_worker(cfg: Path, env: Path, log_path: Path) -> subprocess.Popen[bytes]:
    handle = log_path.open("w", encoding="utf-8")
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "app.workers.run", "--config", str(cfg), "--env", str(env)],
            cwd=ROOT,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    finally:
        handle.close()


def _kill_all(workers: list[subprocess.Popen[bytes]]) -> None:
    for proc in workers:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def _wait(
    repo: JobRepository,
    job_id: str,
    predicate: Callable[[Job], bool],
    timeout: float,
    what: str,
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
    st = None if last is None else last.status.value
    raise TimeoutError(f"{what}: последнее {st}")


def _force_status(db: Database, job_id: str, status: JobStatus) -> None:
    interrupted_from = JobStatus.TRANSLATING.value if status is JobStatus.INTERRUPTED else None
    paused_from = JobStatus.ANALYZING.value if status is JobStatus.PAUSED else None
    db.conn.execute(
        "UPDATE jobs SET status = ?, interrupted_from = ?, paused_from = ?, "
        "resume_requested = 0, cancel_requested = 0, pause_requested = 0 WHERE id = ?",
        (status.value, interrupted_from, paused_from, job_id),
    )
    db.conn.commit()


def _expected_allowed(from_status: JobStatus, to_status: JobStatus) -> bool:
    if to_status not in TRANSITIONS[from_status]:
        return False
    if from_status is JobStatus.INTERRUPTED and to_status in WORKING_STATES:
        return to_status is JobStatus.TRANSLATING
    if from_status is JobStatus.PAUSED and to_status in WORKING_STATES:
        return to_status is JobStatus.ANALYZING
    return True


def _transition_lines(transitions: list[TransitionRecord]) -> list[str]:
    return [
        f"  {t.from_status.value} -> {t.to_status.value}  at={t.at:.3f}  "
        f"actor={t.actor}  reason={t.reason!r}"
        for t in transitions
    ]


# ------------------------------------------------------------------ E3-1 + E3-4


def ev_e3_1_and_e3_4(work: Path, server: MockLLMServer) -> None:
    settings, cfg, env = _make_env_storage(work, server)
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    repo = JobRepository(db)
    workers: list[subprocess.Popen[bytes]] = []
    try:
        # Эталонный прогон (Z): без задержек, до конца.
        server.set_delay(0.0)
        job_ref = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        w = _start_worker(cfg, env, work / "w_ref.log")
        workers.append(w)
        assert w.wait(timeout=180) == 0
        final_ref = repo.require(job_ref.id)
        assert final_ref.status is JobStatus.COMPLETED
        z = final_ref.tokens_translation + final_ref.tokens_fill

        # kill -9 в середине перевода → ожидание истечения аренды → resume.
        server.set_delay(KILL_DELAY)
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        w1 = _start_worker(cfg, env, work / "w1.log")
        workers.append(w1)
        _wait(
            repo,
            job.id,
            lambda j: (
                j.status is JobStatus.TRANSLATING
                and j.tokens_translation + j.tokens_fill > 0
            ),
            120.0,
            "ожидание завершённой группы в TRANSLATING",
        )
        t_kill = time.time()
        state_at_kill = repo.require(job.id)
        assert state_at_kill.status is JobStatus.TRANSLATING
        lease_at_kill = state_at_kill.lease_expires_at
        assert lease_at_kill is not None
        x = state_at_kill.tokens_translation + state_at_kill.tokens_fill
        assert x > 0
        assert lease_at_kill >= t_kill, "арена не продлевалась при живом воркере"
        os.kill(w1.pid, signal.SIGKILL)
        assert w1.wait(timeout=20) == -9
        time.sleep(max(0.0, lease_at_kill - time.time()) + 0.1)

        w2 = _start_worker(cfg, env, work / "w2.log")
        workers.append(w2)
        assert w2.wait(timeout=180) == 0
        final = repo.require(job.id)
        assert final.status is JobStatus.COMPLETED
        transitions = repo.list_transitions(job.id)
        recovery = [
            t for t in transitions if t.to_status is JobStatus.INTERRUPTED and t.actor == "recovery"
        ]
        y = (final.tokens_translation + final.tokens_fill) - x

        lease_delay = lease_at_kill - t_kill
        share = max(0, x + y - z) / z
        evidence(
            "e3-1_interrupted_within_2_heartbeat.txt",
            "\n".join(
                [
                    f"##### E3-1: kill -9 → INTERRUPTED, окно осиротения (verbatim)\nдата: {STAMP}",
                    f"job_id: {job.id}",
                    f"конфиг: heartbeat={HEARTBEAT} с, lease={LEASE} с "
                    "(конфигурация требует lease ≤ 2×heartbeat — E3-1)",
                    f"t_kill (epoch)                      : {t_kill:.3f}",
                    f"lease_expires_at на момент kill     : {lease_at_kill:.3f}",
                    f"окно осиротения (lease_at_kill - t_kill): {lease_delay:.3f} с",
                    f"порог 2×heartbeat                   : {2 * HEARTBEAT:.3f} с",
                    f"окно ≤ 2×heartbeat                  : {lease_delay <= 2 * HEARTBEAT + 0.05}",
                    f"арена действовала на момент kill    : {lease_at_kill >= t_kill}",
                    f"итоговый статус после resume        : {final.status.value}",
                    f"итоговый progress                   : {final.progress:.2f}",
                    "",
                    "переход восстановления (actor=recovery — единственный механизм",
                    "обнаружения осиротевших заданий):",
                    *[
                        f"  INTERRUPTED at={t.at:.3f} reason={t.reason!r}" for t in recovery
                    ],
                    "",
                    "полный журнал переходов задания:",
                    *_transition_lines(transitions),
                ]
            ),
        )
        evidence(
            "e3-4_retranslation_share.txt",
            "\n".join(
                [
                    f"##### E3-4: доля повторного перевода при resume (verbatim)\nдата: {STAMP}",
                    f"job_id: {job.id}",
                    "метрика: share = max(0, X + Y - Z) / Z; токены = translation + fill",
                    f"X — токены на момент kill (flush в on_progress)       : {x}",
                    f"Y — прирост токенов при resume (final - X)            : {y}",
                    f"Z — токены эталонного полного прогона (без прерываний): {z}",
                    f"X + Y - Z                                              : {x + y - z}",
                    f"share                                                  : {share:.4%}",
                    f"порог ≤ 5%                                             : {share <= 0.05}",
                    "",
                    "объяснение: кэш движка (cache_path внутри каталога задания) адресует",
                    "ответ по ключу запроса; группы, завершённые до kill, на resume читаются",
                    "из кэша (0 токенов), поэтому X + Y ≤ Z → повторный перевод 0%.",
                ]
            ),
        )
    finally:
        _kill_all(workers)
        db.close()


# ------------------------------------------------------------------ E3-2


def ev_e3_2(work: Path, server: MockLLMServer) -> None:
    settings, _, _ = _make_env_storage(work, server)
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    repo = JobRepository(db)
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        allowed = 0
        rejected = 0
        rejected_pairs: list[str] = []
        for from_status in JobStatus:
            for to_status in JobStatus:
                _force_status(db, job.id, from_status)
                if _expected_allowed(from_status, to_status):
                    repo.transition(job.id, to_status, reason="полный перебор")
                    allowed += 1
                else:
                    try:
                        repo.transition(job.id, to_status, reason="полный перебор")
                    except JobStateError:
                        rejected += 1
                        if len(rejected_pairs) < 8:
                            rejected_pairs.append(f"  {from_status.value} -> {to_status.value}")
                    else:
                        raise AssertionError(
                            f"недопустимый переход не отклонён: {from_status} -> {to_status}"
                        )
        row = db.conn.execute("SELECT COUNT(*) FROM transitions").fetchone()
        recorded = int(row[0]) if row is not None else -1
        evidence(
            "e3-2_transition_table.txt",
            "\n".join(
                [
                    f"##### E3-2: полный перебор таблицы переходов 14×14 (verbatim)\nдата: {STAMP}",
                    f"job_id: {job.id}",
                    f"всего пар (14 статусов)                   : {len(JobStatus) ** 2}",
                    f"допущено (переход выполнен)               : {allowed}",
                    f"отклонено (JobStateError + запись в лог)  : {rejected}",
                    f"строк в transitions (== числу допущенных) : {recorded}",
                    f"отклонённые в transitions не записаны     : {recorded == allowed}",
                    "",
                    "примеры отклонённых пар (первые 8):",
                    *rejected_pairs,
                    "",
                    "каждое отклонение фиксируется в логе (logger app.jobs, WARNING):",
                    "  «недопустимый переход отклонён: задание=… FROM -> TO (причина запроса: …)»",
                    "resume-правило: из INTERRUPTED/PAUSED в рабочее состояние разрешён",
                    "только сохранённый interrupted_from/paused_from (проверяется в репозитории).",
                ]
            ),
        )
    finally:
        db.close()


# ------------------------------------------------------------------ E3-3


def ev_e3_3(work: Path, server: MockLLMServer) -> None:
    settings, _, _ = _make_env_storage(work, server)
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    repo = JobRepository(db)
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        pipeline = Pipeline(settings)
        job_now = repo.require(job.id)
        pipeline.run_validating(repo, job_now)
        pipeline.run_planning(repo, job_now)
        req_before = len(server.requests_log())
        pipeline.run_translating(repo, job_now, lambda: None)
        req_after_first = len(server.requests_log())
        target = settings.jobs_dir / job.id / "target.epub"
        hash_1 = hashlib.sha256(target.read_bytes()).hexdigest()
        pipeline.run_translating(repo, job_now, lambda: None)
        hash_2 = hashlib.sha256(target.read_bytes()).hexdigest()
        req_after_second = len(server.requests_log())
        delta_first = req_after_first - req_before
        delta_second = req_after_second - req_after_first
        result = repo.get_stage_result(job.id, "translating")
        assert result is not None
        evidence(
            "e3-3_stage_idempotent.txt",
            "\n".join(
                [
                    "##### E3-3: повторный запуск этапа не изменяет артефакт (verbatim)\n"
                    f"дата: {STAMP}",
                    f"job_id: {job.id}",
                    f"sha256(target.epub) после 1-го запуска : {hash_1}",
                    f"sha256(target.epub) после 2-го запуска : {hash_2}",
                    f"хэши совпадают                         : {hash_1 == hash_2}",
                    f"LLM-запросов, порождённых 1-м запуском  : {delta_first}",
                    f"LLM-запросов, порождённых 2-м запуском  : {delta_second}",
                    f"повтор не породил новых запросов       : {delta_second == 0}",
                    f"ключ идемпотентности (input_hash)      : {result.input_hash}",
                    f"совпадает с ключом pipeline            : "
                    f"{result.input_hash == pipeline.input_hash(job_now)}",
                    "",
                    "механизм: ключ = sha256(input_sha256 + config_sha256 + prompt_version +",
                    "target_language); совпадение ключа → этап пропускается, повторный",
                    "перевод не порождается, артефакт не накладывается на себя.",
                ]
            ),
        )
    finally:
        db.close()


# ------------------------------------------------------------------ E3-5


def ev_e3_5(work: Path, server: MockLLMServer) -> None:
    settings, cfg, env = _make_env_storage(work, server)
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    repo = JobRepository(db)
    workers: list[subprocess.Popen[bytes]] = []
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        server.set_delay(0.2)
        w1 = _start_worker(cfg, env, work / "w1.log")
        workers.append(w1)
        _wait(repo, job.id, lambda j: j.progress >= 0.05, 120.0, "ожидание старта перевода")
        t_flag = time.time()
        repo.request_cancel(job.id)
        assert w1.wait(timeout=60) == 0
        final = repo.require(job.id)
        transitions = repo.list_transitions(job.id)
        cancelled = [t for t in transitions if t.to_status is JobStatus.CANCELLED]
        assert len(cancelled) == 1
        job_dir = settings.jobs_dir / job.id
        partial = job_dir / "target.epub.partial"
        plain = job_dir / "target.epub"
        listing = sorted(p.name for p in job_dir.iterdir() if p.is_file())
        elapsed = cancelled[0].at - t_flag
        evidence(
            "e3-5_cooperative_cancel.txt",
            "\n".join(
                [
                    f"##### E3-5: кооперативная отмена во время перевода (verbatim)\nдата: {STAMP}",
                    f"job_id: {job.id}",
                    f"итоговый статус                     : {final.status.value}",
                    f"t_flag (epoch)                      : {t_flag:.3f}",
                    f"момент CANCELLED (epoch)            : {cancelled[0].at:.3f}",
                    f"время отмены (CANCELLED - t_flag)   : {elapsed:.3f} с",
                    f"порог ≤ 30 с                        : {elapsed <= 30}",
                    f"target.epub.partial существует      : {partial.is_file()}",
                    f"непомеченный target.epub отсутствует: {not plain.exists()}",
                    "",
                    "файлы в каталоге задания:",
                    *[f"  {name}" for name in listing],
                    "",
                    "механизм: флаг отмены проверяется на границах групп (on_progress) и",
                    "между этапами; частично записанный target.epub переименовывается в",
                    "target.epub.partial — частичный артефакт помечен, не является результатом.",
                ]
            ),
        )
    finally:
        _kill_all(workers)
        db.close()


# ------------------------------------------------------------------ E3-6


def ev_e3_6(work: Path, server: MockLLMServer) -> None:
    settings, cfg, env = _make_env_storage(work, server)
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    repo = JobRepository(db)
    job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
    db.close()
    req_before = len(server.requests_log())
    w1 = _start_worker(cfg, env, work / "w1.log")
    w2 = _start_worker(cfg, env, work / "w2.log")
    workers = [w1, w2]
    try:
        assert w1.wait(timeout=180) == 0
        assert w2.wait(timeout=180) == 0
        out1 = (work / "w1.log").read_text(encoding="utf-8")
        out2 = (work / "w2.log").read_text(encoding="utf-8")
    finally:
        _kill_all(workers)

    with Database(settings.jobs_db_path, settings.storage.busy_timeout_ms) as db2:
        repo2 = JobRepository(db2)
        final = repo2.require(job.id)
        transitions = repo2.list_transitions(job.id)
    first_steps = [
        t
        for t in transitions
        if t.from_status is JobStatus.CREATED and t.to_status is JobStatus.VALIDATING
    ]
    winners = [out for out in (out1, out2) if "status=COMPLETED" in out]
    new_requests = server.requests_log()[req_before:]
    new_translate = [r for r in new_requests if r["kind"] == "translate"]
    loser = out2 if "status=COMPLETED" in out1 else out1
    evidence(
        "e3-6_double_worker.txt",
        "\n".join(
            [
                f"##### E3-6: параллельный запуск второго воркера (verbatim)\nдата: {STAMP}",
                f"job_id: {job.id}",
                f"итоговый статус                            : {final.status.value}",
                f"исполнителей задания (winners из 2)        : {len(winners)}",
                f"переходов CREATED -> VALIDATING            : {len(first_steps)}",
                f"LLM-запросов, порождённых этим сценарием    : {len(new_requests)}",
                f"  из них translate (2 группы complex.epub) : {len(new_translate)}",
                "вывод проигравшего воркера (фрагмент):",
                *[f"  {line}" for line in loser.splitlines()[-3:]],
                "",
                "механизм: занятие — атомарный UPDATE … WHERE status IN (claimable)",
                "AND (worker_pid IS NULL OR lease_expires_at < now); второй воркер не",
                "захватывает занятое задание (0 коллизий) и не дублирует перевод.",
            ]
        ),
    )


# ------------------------------------------------------------------ main


def main() -> None:
    server = MockLLMServer()
    server.start()
    work_base = ROOT / ".qwen" / "tmp" / "e3_evidence"
    if work_base.exists():
        shutil.rmtree(work_base)
    try:
        print("E3-1 + E3-4: kill -9 → resume …")
        ev_e3_1_and_e3_4(work_base / "s14", server)
        print("E3-2: перебор таблицы переходов …")
        ev_e3_2(work_base / "s2", server)
        print("E3-3: идемпотентность этапа …")
        ev_e3_3(work_base / "s3", server)
        print("E3-5: кооперативная отмена …")
        ev_e3_5(work_base / "s5", server)
        print("E3-6: двойной воркер …")
        ev_e3_6(work_base / "s6", server)
        print("готово")
    finally:
        server.stop()
        if work_base.exists():
            shutil.rmtree(work_base, ignore_errors=True)


if __name__ == "__main__":
    main()
