# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""V2 — готовность продукта: удобство интерфейса (T-V2-1..T-V2-5).

- T-V2-1: сигнал «воркер жив» (D87): живой воркер ``--loop`` пишет heartbeat
  → ``/api/health``: ``worker: ok (pid N)``; kill процесса → ``error`` (оффлайн).
  Единицы измерения: реальное время ожидания, никаких фиктивных статусов.
- T-V2-2: именованные артефакты (target.epub / learning.epub / quality.json /
  cards.*) скачиваются по имени, байт-в-байт; ``main`` — learning.epub при его
  наличии; 404 — файла нет; 422 — имя вне allow-list (path traversal);
  409 — результат не готов.
- T-V2-3: человекочитаемые метки времени (создано/обновлено) в facts, API и
  HTML; сырой epoch в HTML страницы задания отсутствует.
- T-V2-4: таймлайн стадий: ``job_facts.stages`` == переходы из БД (единый
  источник, Rule 8); HTML содержит ту же таблицу.
- T-V2-5: статус воркера на странице Jobs (HTML): error без heartbeat,
  ``ok (pid N)`` после ``write_heartbeat``.

Сквозные прогоны не дублируются (E3/U1/L3/S5 покрывают конвейер); здесь —
только слой представления и сигнал воркера. LLM — мёртвый порт (connection
refused мгновенно: UI-слой LLM не трогает).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.worker_signal import (
    HEARTBEAT_FILE,
    heartbeat_path,
    worker_alive,
    write_heartbeat,
)
from app.domain.job import JobStatus
from app.services.jobs import create_job
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.web import views
from app.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from test_u1_web import _make_u1_storage, _synth_u1_tiny_book  # noqa: E402

_HUMAN_TS = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

#: Полный «нормальный» путь конвейера (та же цепочка, что в реальных прогонах):
#: 9 переходов CREATED → … → COMPLETED_WITH_WARNINGS.
_CHAIN: tuple[tuple[JobStatus, str], ...] = (
    (JobStatus.VALIDATING, "валидация"),
    (JobStatus.PLANNING, "план"),
    (JobStatus.TRANSLATING, "перевод"),
    (JobStatus.ANALYZING, "анализ"),
    (JobStatus.GENERATING, "генерация"),
    (JobStatus.VALIDATING_OUTPUT, "проверка результата"),
    (JobStatus.GLOSSARY_FIX, "глоссарий"),
    (JobStatus.LEARNING, "learning"),
    (JobStatus.COMPLETED_WITH_WARNINGS, "завершено с предупреждениями"),
)


@dataclass
class V2Env:
    """Изолированное окружение: два профиля (API-тесты + воркер-процесс)."""

    base: Path
    settings: Settings
    db: Database
    repo: JobRepository
    app: FastAPI
    client: TestClient
    cfg: Path
    env_file: Path
    loop_settings: Settings
    loop_db: Database
    loop_cfg: Path
    loop_env_file: Path
    workers: list[subprocess.Popen[bytes]] = field(default_factory=list)
    #: Задание с цепочкой до CWW и артефактами (общее для T-V2-2..T-V2-5).
    job_id: str = ""

    def close(self) -> None:
        _kill_all(self.workers)
        self.db.close()
        self.loop_db.close()


def _kill_all(workers: list[subprocess.Popen[bytes]]) -> None:
    for proc in workers:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def _start_worker_loop(cfg: Path, env_file: Path, log_path: Path) -> subprocess.Popen[bytes]:
    """Воркер-процесс в режиме ``--loop`` (D87: heartbeat пишется только тут)."""
    cmd = [
        sys.executable,
        "-m",
        "app.workers.run",
        "--config",
        str(cfg),
        "--env",
        str(env_file),
        "--loop",
    ]
    handle = log_path.open("w", encoding="utf-8")
    try:
        return subprocess.Popen(cmd, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
    finally:
        handle.close()


@pytest.fixture(scope="module")
def v2_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[V2Env]:
    base = tmp_path_factory.mktemp("v2_module")
    book_dir = base / "books"
    book_dir.mkdir()
    book_path = book_dir / "v2_probe.epub"
    _synth_u1_tiny_book(book_path)

    # Профиль API-тестов: LLM — мёртвый порт (connection refused мгновенно).
    settings, db, cfg, env_file = _make_u1_storage(
        base, "storage_main", "http://127.0.0.1:1/v1"
    )
    # Профиль воркера: быстрый heartbeat (1 c; свежесть 3 c) — kill виден <10 c.
    loop_settings, loop_db, loop_cfg, loop_env_file = _make_u1_storage(
        base,
        "storage_loop",
        "http://127.0.0.1:1/v1",
        yaml_extra="server:\n  heartbeat_seconds: 1.0\n  lease_seconds: 2.0\n",
    )
    app = create_app(settings, db=db)
    repo = JobRepository(db)
    env = V2Env(
        base=base,
        settings=settings,
        db=db,
        repo=repo,
        app=app,
        client=TestClient(app),
        cfg=cfg,
        env_file=env_file,
        loop_settings=loop_settings,
        loop_db=loop_db,
        loop_cfg=loop_cfg,
        loop_env_file=loop_env_file,
    )
    env.job_id = _build_finished_job(env, book_path)
    try:
        yield env
    finally:
        env.close()


def _build_finished_job(env: V2Env, book_path: Path) -> str:
    """Задание в COMPLETED_WITH_WARNINGS с артефактами в каталоге задания."""
    repo, settings = env.repo, env.settings
    job = create_job(repo, settings, source_path=book_path, target_language="ru")
    for to_status, reason in _CHAIN:
        repo.transition(job.id, to_status, reason)
    jdir = settings.job_dir(job.id)
    jdir.mkdir(parents=True, exist_ok=True)
    # Минимальные валидные ZIP (пустой архив) — разные байты, чтобы
    # «байт-в-байт» было проверяемо: target != learning.
    (jdir / "target.epub").write_bytes(b"PK\x05\x06" + b"\x00" * 18 + b"T")
    (jdir / "learning.epub").write_bytes(b"PK\x05\x06" + b"\x00" * 18 + b"L")
    (jdir / "quality.json").write_text(
        json.dumps({"warnings": ["v2-test"]}, ensure_ascii=False), encoding="utf-8"
    )
    return job.id


def _health(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/health")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def _wait_until(predicate: Callable[[], bool], timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    pytest.fail(f"{what}: не наступило за {timeout:.0f} c")


# ------------------------------------------------------------------ T-V2-1


def test_v2_1_worker_heartbeat_in_health(v2_env: V2Env) -> None:
    """D87: живой воркер --loop → health worker: ok (pid N); kill → error.

    Единая точка worker_alive() для health и страницы Jobs (Rule 8)."""
    settings = v2_env.loop_settings
    client = TestClient(create_app(settings, db=v2_env.loop_db))

    # Файла нет — честный «не запущен» (не «ok» по умолчанию).
    status, detail = worker_alive(settings)
    assert status == "error"
    assert "heartbeat не найден" in detail

    # Повреждённый/протухший heartbeat — error, не «ok» (fail closed).
    hb = heartbeat_path(settings)
    hb.parent.mkdir(parents=True, exist_ok=True)
    hb.write_text("12345\n", encoding="utf-8")
    assert worker_alive(settings)[0] == "error"
    hb.write_text(f"12345\n{time.time() - 3600.0}\n", encoding="utf-8")
    assert "оффлайн" in worker_alive(settings)[1]
    hb.unlink()

    log_path = v2_env.base / "worker_v2_1.log"
    proc = _start_worker_loop(v2_env.loop_cfg, v2_env.loop_env_file, log_path)
    v2_env.workers.append(proc)
    try:
        # Живой воркер: heartbeat появляется, health — ok (pid = PID процесса).
        _wait_until(
            lambda: _health(client).get("worker", "").startswith("ok (pid"),
            timeout=30.0,
            what="ожидание ok (pid …) после запуска воркера --loop",
        )
        report = _health(client)
        assert report["worker"] == f"ok (pid {proc.pid})", report
        assert "worker" not in report.get("detail", {})

        # Kill процесса: heartbeat протухает (3 × 1 c) → error «оффлайн».
        proc.kill()
        proc.wait(timeout=10)
        _wait_until(
            lambda: str(_health(client).get("worker", "")).startswith("error"),
            timeout=15.0,
            what="ожидание error после kill воркера",
        )
        report = _health(client)
        assert report["worker"] == "error"
        assert "оффлайн" in str(report.get("detail", {}).get("worker", ""))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


# ------------------------------------------------------------------ T-V2-2


def test_v2_2_named_result_artifacts(v2_env: V2Env) -> None:
    """Именованные артефакты: байт-в-байт; main — learning.epub; 404/422/409."""
    client, settings = v2_env.client, v2_env.settings
    job_id = v2_env.job_id
    jdir = settings.job_dir(job_id)
    target_bytes = (jdir / "target.epub").read_bytes()
    learning_bytes = (jdir / "learning.epub").read_bytes()
    quality_bytes = (jdir / "quality.json").read_bytes()
    assert target_bytes != learning_bytes  # различимость проверок

    for artifact, expected in (
        ("target.epub", target_bytes),
        ("learning.epub", learning_bytes),
        ("quality.json", quality_bytes),
    ):
        response = client.get(f"/api/jobs/{job_id}/result", params={"artifact": artifact})
        assert response.status_code == 200, response.text
        assert response.content == expected
        assert response.headers["content-disposition"].endswith(f'"{artifact}"')

    # main — learning.epub, если он есть (Learning), иначе target.epub.
    response = client.get(f"/api/jobs/{job_id}/result")
    assert response.status_code == 200
    assert response.content == learning_bytes

    # Файла нет в каталоге задания — честный 404 (не пустой ответ).
    response = client.get(f"/api/jobs/{job_id}/result", params={"artifact": "cards.csv"})
    assert response.status_code == 404

    # Имя вне allow-list (path traversal) — 422, файл не отдаётся.
    response = client.get(
        f"/api/jobs/{job_id}/result", params={"artifact": "../jobs.db"}
    )
    assert response.status_code == 422

    # Незавершённое задание — 409 «результат не готов».
    other = _fresh_created_job(v2_env)
    response = client.get(f"/api/jobs/{other}/result")
    assert response.status_code == 409

    # Ссылки в HTML страницы задания — только на существующие файлы.
    page = client.get(f"/jobs/{job_id}").text
    assert f"/api/jobs/{job_id}/result?artifact=target.epub" in page
    assert f"/api/jobs/{job_id}/result?artifact=learning.epub" in page
    assert f"/api/jobs/{job_id}/result?artifact=quality.json" in page
    assert "artifact=cards.csv" not in page  # файла нет → ссылки нет


def _fresh_created_job(env: V2Env) -> str:
    """Новое задание в CREATED (для проверки 409 «результат не готов»)."""
    book = env.base / "books" / "v2_probe.epub"
    job = create_job(env.repo, env.settings, source_path=book, target_language="ru")
    return job.id


# ------------------------------------------------------------------ T-V2-3


def test_v2_3_human_timestamps_no_raw_epoch(v2_env: V2Env) -> None:
    """Создано/обновлено — человекочитаемо в facts, API и HTML экрана задания;
    epoch не виден. R2: список — строки §10, без колонки «создано»."""
    client = v2_env.client
    job_id = v2_env.job_id
    job = v2_env.repo.get(job_id)
    assert job is not None

    facts = client.get(f"/api/jobs/{job_id}").json()
    assert _HUMAN_TS.match(facts["created_at_human"]), facts["created_at_human"]
    assert _HUMAN_TS.match(facts["updated_at_human"]), facts["updated_at_human"]
    assert facts["created_at_human"] == views.human_ts(job.created_at)

    rows = client.get("/api/jobs").json()
    row = next(r for r in rows if r["id"] == job_id)
    assert row["created_at_human"] == views.human_ts(job.created_at)

    # Сырой epoch (как в до-V2 UI: ``created_at | round(0)``) в HTML отсутствует.
    page = client.get(f"/jobs/{job_id}").text
    assert str(int(job.created_at)) not in page
    assert str(int(job.updated_at)) not in page
    assert facts["created_at_human"] in page
    assert facts["updated_at_human"] in page

    # R2: список — строки §10, без колонки «создано» (время создания —
    # на экране задания). data-rev строки несёт epoch updated_at для дельты
    # (атрибут, не видимый текст) — сырая проверка по всему HTML списка
    # неприменима; проверяем, что строка задания отрендерена.
    jobs_page = client.get("/jobs").text
    assert f'data-id="{job_id}"' in jobs_page


# ------------------------------------------------------------------ T-V2-4


def test_v2_4_stage_timeline_from_db(v2_env: V2Env) -> None:
    """Таймлайн стадий == переходы БД (единый источник); HTML содержит таблицу."""
    client = v2_env.client
    job_id = v2_env.job_id
    transitions = v2_env.repo.list_transitions(job_id)
    assert len(transitions) == len(_CHAIN)

    facts = client.get(f"/api/jobs/{job_id}").json()
    expected = [
        {
            "from": record.from_status.value,
            "to": record.to_status.value,
            "at_human": views.human_ts(record.at),
            "reason": record.reason,
        }
        for record in transitions
    ]
    assert facts["stages"] == expected

    page = client.get(f"/jobs/{job_id}").text
    # U3: подпись из strings.py (job_stages).
    assert f"история переходов ({len(transitions)})" in page
    for record in transitions:
        assert f"{record.from_status.value} → {record.to_status.value}" in page


# ------------------------------------------------------------------ T-V2-5


def test_v2_5_worker_status_on_jobs_page(v2_env: V2Env) -> None:
    """Страница Jobs: без heartbeat — баннер «Воркер не отвечает…», после —
    баннера нет (реворк 2026-09-26 п.8: строка сокращена, деталь «ok (pid N)»
    убрана — видна только проверка воркера, а не её подпись на странице)."""
    client, settings = v2_env.client, v2_env.settings
    hb = heartbeat_path(settings)
    hb.unlink(missing_ok=True)

    page = client.get("/jobs").text
    assert "Воркер не отвечает или не запущен" in page

    write_heartbeat(settings, os.getpid())
    assert worker_alive(settings)[0] == f"ok (pid {os.getpid()})"
    page = client.get("/jobs").text
    assert "Воркер не отвечает или не запущен" not in page

    # Имя файла — единая константа (тот же путь, что у воркера, Rule 8).
    assert hb.name == HEARTBEAT_FILE
    hb.unlink(missing_ok=True)
