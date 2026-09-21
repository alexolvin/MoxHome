"""U1 (ТЗ_04 §U1): Web UI + API — тесты T-U1-1..T-U1-10.

Принципы (тот же стандарт, что у предыдущих стадий):
- реальные процессы: воркер — subprocess (как E3/L3/S5), LLM — MockLLMServer;
- UI/API проверяются через TestClient + один раз — через реальный uvicorn
  (access log, T-U1-8);
- честность прогресса (U1-1/U1-2): отображаемое — только функция БД;
- каждый /api-endpoint имеет потребителя (U1-6) — проверяется двусторонне.
"""

from __future__ import annotations

import base64
import html
import json
import math
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, load_settings
from app.domain.job import Job
from app.languages.registry import load_registry
from app.languages.resolver import PairResolver
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.web import views
from app.web.app import create_app
from app.web.auth import csrf_token

ROOT = Path(__file__).resolve().parents[1]
_TESTS_DIR = str(Path(__file__).resolve().parent)
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from mock_llm_server import MockLLMServer  # noqa: E402
from test_l3_glossary_fix import _kill_all, _start_worker  # noqa: E402
from test_s4_study import _S4_EN_POOLS, _synth_book_multi, _synth_s4_en_book  # noqa: E402

#: Секреты с уникальными значениями (T-U1-8: 0 вхождений в HTML/access log).
AUTH_SECRET = "u1-secret-auth-7f3a"
LLM_KEY = "u1-secret-llm-91c4"


# ------------------------------------------------------------------ книги


def _u1_paras(pool: tuple[str, ...]) -> tuple[str, ...]:
    """8 предложений: все 5 слов пула в каждом (детерминированный материал)."""
    a, b, c, d, e = pool
    return (
        f"The {a} near the {b} kept the {c} steady while the {d} rose over the {e}.",
        f"Every {a} passed the {b} and the {c} guided the {d} toward the {e}.",
        f"Workers at the {a} mended the {d} before the {c} closed the {b}.",
        f"The {e} drifted past the {a}, past the {b}, and the {c} rang out.",
        f"Storms bent the {a}, the {b} widened, and the {d} held against the {e}.",
        f"Old maps marked the {c} beside the {a}, a quiet {b} above the {e}.",
        f"Traders counted the {d} at the {a} while the {c} watched the {b}.",
        f"The {a} echoed at dusk, the {b} glistened, and the {e} lay far away.",
    )


def _synth_u1_slow_book(out_path: Path) -> None:
    """6 глав × 8 <p> (T-U1-1/T-U1-10: медленный под задержкой mock)."""
    bodies = tuple(
        "\n".join(
            f'    <p id="p{i}">{p}</p>' for i, p in enumerate(_u1_paras(pool), start=1)
        )
        for pool in _S4_EN_POOLS[:6]
    )
    _synth_book_multi(out_path, "U1 Probe EN", "en", "u1-en-uid-0001", bodies)


def _synth_u1_tiny_book(out_path: Path) -> None:
    """2 главы × 8 <p> (T-U1-2: быстрый завершённый прогон)."""
    bodies = tuple(
        "\n".join(
            f'    <p id="p{i}">{p}</p>' for i, p in enumerate(_u1_paras(pool), start=1)
        )
        for pool in _S4_EN_POOLS[:2]
    )
    _synth_book_multi(out_path, "U1 Tiny EN", "en", "u1-tiny-uid-0002", bodies)


# ------------------------------------------------------------------ окружение


def _make_u1_storage(
    base: Path,
    name: str,
    base_url: str,
    *,
    env_extra: str = "",
    yaml_extra: str = "",
) -> tuple[Settings, Database, Path, Path]:
    """Изолированный storage (паттерн L2) + пути cfg/env для воркера-процесса."""
    work = base / name
    work.mkdir(parents=True, exist_ok=True)
    prompts_dir = base / "prompts"
    if not prompts_dir.exists():
        shutil.copytree(ROOT / "prompts", prompts_dir)
    (work / "test.env").write_text(
        f"SERVER_BIND_ADDRESS=127.0.0.1\nLLM_API_KEY={LLM_KEY}\n{env_extra}",
        encoding="utf-8",
    )
    profiles = "".join(
        f"    {n}:\n      base_url: {base_url}\n      model: test-model\n"
        for n in ("translation", "fill", "analysis")
    )
    (work / "config.yaml").write_text(
        f"storage:\n  root: {work}\n"
        f"epubcheck:\n  jar_path: {ROOT / 'data/tools/epubcheck.jar'}\n"
        f"languages:\n  root: {ROOT / 'config' / 'languages'}\n"
        f"  matrix: {ROOT / 'config' / 'language_matrix.yaml'}\n"
        f"llm:\n  prompts_root: {prompts_dir}\n  profiles:\n{profiles}"
        f"glossary:\n  enabled: true\n  fix_enabled: true\n{yaml_extra}",
        encoding="utf-8",
    )
    cfg = work / "config.yaml"
    env_file = work / "test.env"
    settings = load_settings(cfg, env_file)
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    return settings, db, cfg, env_file


@dataclass
class U1Profile:
    settings: Settings
    db: Database
    repo: JobRepository
    app: FastAPI
    client: TestClient
    cfg: Path
    env_file: Path


@dataclass
class U1Env:
    server: MockLLMServer
    base: Path
    books: dict[str, Path]
    profiles: dict[str, U1Profile] = field(default_factory=dict)
    workers: list[subprocess.Popen[bytes]] = field(default_factory=list)

    def add_profile(
        self,
        name: str,
        settings: Settings,
        db: Database,
        cfg: Path,
        env_file: Path,
        *,
        client_headers: dict[str, str] | None = None,
    ) -> None:
        app = create_app(settings, db=db)
        self.profiles[name] = U1Profile(
            settings=settings,
            db=db,
            repo=JobRepository(db),
            app=app,
            # U3 3.9: у main включён SECRET — все его запросы несут Basic +
            # CSRF по умолчанию (мутации fail closed без аутентификации).
            client=TestClient(app, headers=client_headers or {}),
            cfg=cfg,
            env_file=env_file,
        )

    def close(self) -> None:
        _kill_all(self.workers)
        for profile in self.profiles.values():
            profile.db.close()


@pytest.fixture(scope="module")
def u1_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[U1Env]:
    base = tmp_path_factory.mktemp("u1_module")
    books_dir = base / "books"
    books_dir.mkdir()
    _synth_u1_slow_book(books_dir / "u1_slow.epub")
    _synth_u1_tiny_book(books_dir / "u1_tiny.epub")
    _synth_s4_en_book(books_dir / "u1_full.epub")  # 20 глав × 8 <p> (T-U1-9)
    server = MockLLMServer()
    server.start()
    env = U1Env(
        server=server,
        base=base,
        books={
            "slow": books_dir / "u1_slow.epub",
            "tiny": books_dir / "u1_tiny.epub",
            "full": books_dir / "u1_full.epub",
        },
    )
    try:
        # main: U3 3.9 (fail closed) — мутирующие маршруты требуют
        # SERVER_AUTH_SECRET + CSRF, поэтому у профиля есть секрет, а его
        # клиент шлёт Basic + X-U3-CSRF по умолчанию (чтение с заголовками
        # открыто; U1-критерии на main не зависят от режима аутентификации).
        main_settings, main_db, main_cfg, main_env = _make_u1_storage(
            base, "storage_main", server.url, env_extra=f"SERVER_AUTH_SECRET={AUTH_SECRET}\n"
        )
        env.add_profile(
            "main",
            main_settings,
            main_db,
            main_cfg,
            main_env,
            client_headers={
                **_auth_header(),
                "X-U3-CSRF": csrf_token(main_settings, "any-user"),
            },
        )
        for name, url, kwargs in (
            # мёртвый порт: connection refused мгновенно (T-U1-3)
            ("dead", "http://127.0.0.1:1/v1", {}),
            ("auth", server.url, {"env_extra": f"SERVER_AUTH_SECRET={AUTH_SECRET}\n"}),
        ):
            settings, db, cfg, env_file = _make_u1_storage(base, f"storage_{name}", url, **kwargs)
            env.add_profile(name, settings, db, cfg, env_file)
        yield env
    finally:
        env.close()
        server.stop()


# ------------------------------------------------------------------ утилиты


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _upload_book(client: TestClient, path: Path) -> str:
    with path.open("rb") as fh:
        response = client.post(
            "/api/books", files={"file": (path.name, fh, "application/epub+zip")}
        )
    assert response.status_code == 200, response.text
    return str(response.json()["name"])


def _create_job(
    client: TestClient,
    book_name: str,
    *,
    target: str = "ru",
    mode: str = "bilingual",
    level: str | None = None,
    cefr: str | None = None,
) -> str:
    payload: dict[str, Any] = {"book": book_name, "target_language": target, "mode": mode}
    if level is not None:
        payload["learning_level"] = level
    if cefr is not None:
        payload["learning_cefr"] = cefr
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _wait_job_terminal(client: TestClient, job_id: str, timeout: float) -> dict[str, Any]:
    """Терминальное состояние через API; при NEEDS_CONFIRMATION — подтверждение
    детектированным исходным языком (полный цикл — только через API, T-U1-9)."""
    deadline = time.monotonic() + timeout
    confirmed = False
    facts: dict[str, Any] = {}
    while time.monotonic() < deadline:
        facts = client.get(f"/api/jobs/{job_id}").json()
        if facts["status"] == "NEEDS_CONFIRMATION" and not confirmed:
            detected = (facts.get("language_detection") or {}).get("detected") or "en"
            response = client.post(
                f"/api/jobs/{job_id}/confirm", json={"source_language": detected}
            )
            assert response.status_code == 200, response.text
            confirmed = True
        if facts.get("status_terminal"):
            return facts
        time.sleep(0.2)
    pytest.fail(f"не завершилось за {timeout:.0f} c: {facts.get('status')}")


def _auth_header() -> dict[str, str]:
    token = base64.b64encode(f"any-user:{AUTH_SECRET}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# ------------------------------------------------------------------ T-U1-1


def test_u1_1_progress_matches_db_over_slices(u1_env: U1Env) -> None:
    """U1-1: на >=20 срезах за живым прогоном progress из API == БД, а
    progress_display — чистая функция (jobs.progress, validation.json);
    расхождений 0. Фиктивного процента в progress_display нет (часть U1-2)."""
    profile = u1_env.profiles["main"]
    client, repo, settings = profile.client, profile.repo, profile.settings
    server = u1_env.server
    book_name = _upload_book(client, u1_env.books["slow"])
    job_id = _create_job(client, book_name)

    server.set_delay(0.3)
    proc = _start_worker(profile.cfg, profile.env_file, u1_env.base / "worker_u1_1.log")
    u1_env.workers.append(proc)
    try:
        slices = 0
        mismatches = 0
        fake_percent = 0
        seen_progress: set[float] = set()
        deadline = time.monotonic() + 240
        terminal: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            matched = False
            for _attempt in range(20):
                facts = client.get(f"/api/jobs/{job_id}").json()
                if facts.get("status_terminal"):
                    terminal = facts
                    matched = True
                    break
                if "%" in str(facts.get("progress_display", "")):
                    fake_percent += 1
                job = repo.get(job_id)
                if job is not None and job.progress == facts["progress"]:
                    validation = views.read_json(
                        settings.job_dir(job_id) / "validation.json"
                    )
                    expected = views.progress_facts(job, validation)["display"]
                    if facts["progress_display"] != expected:
                        mismatches += 1
                    else:
                        slices += 1
                        seen_progress.add(job.progress)
                    matched = True
                    break
                time.sleep(0.05)
            if terminal is not None:
                break
            time.sleep(0.15 if matched else 0.3)
        proc.wait(timeout=180)
    finally:
        server.set_delay(0.0)

    assert terminal is not None, "прогон не завершился за отведённое время"
    assert slices >= 20, f"мало срезов для сравнения: {slices}"
    assert mismatches == 0, f"расхождений progress/API: {mismatches} из {slices}"
    assert fake_percent == 0, "в progress_display попал процент"
    assert len(seen_progress) >= 3, f"прогресс не двигался: {seen_progress}"


# ------------------------------------------------------------------ T-U1-2


def test_u1_2_no_fake_percentages(u1_env: U1Env) -> None:
    """U1-2: проценты — реальные метрики, не фикция.
    Экран задания — измеренный охват выравнивания; список заданий —
    прогресс сегментов (TZ §10: строка 3 и полоса). Проверяется в HTML
    страниц завершённого прогона и в JSON API."""
    profile = u1_env.profiles["main"]
    client, repo = profile.client, profile.repo
    book_name = _upload_book(client, u1_env.books["tiny"])
    job_id = _create_job(client, book_name)
    proc = _start_worker(profile.cfg, profile.env_file, u1_env.base / "worker_u1_2.log")
    u1_env.workers.append(proc)
    try:
        final = _wait_job_terminal(client, job_id, timeout=300)
        proc.wait(timeout=120)
    except Exception:
        proc.kill()
        raise

    assert final["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS"), final["status"]
    job = repo.get(job_id)
    assert job is not None

    # Экран задания: проценты = измеренный охват выравнивания (U1-2).
    allowed_coverage: set[str] = set()
    for other in repo.list_jobs():
        if other.alignment_coverage is not None:
            allowed_coverage.add(f"{other.alignment_coverage * 100:.1f}")
    # Список: проценты = прогресс сегментов (TZ §10: line3 и полоса width) —
    # реальный показатель выполнения, а не охват. Значения — progress_pct API.
    rows = client.get("/api/jobs").json()
    allowed_progress = {
        str(r["progress_pct"]) for r in rows if r.get("progress_pct") is not None
    }

    job_page = client.get(f"/jobs/{job_id}").text
    list_page = client.get("/jobs").text
    found = re.findall(r"(\d+(?:\.\d+)?)%", job_page)
    bad = [value for value in found if value not in allowed_coverage]
    assert not bad, f"job page: проценты вне охвата выравнивания: {bad}"
    if job.alignment_coverage is not None:
        assert re.search(r"(\d+(?:\.\d+)?)%", job_page), "измеренный охват не показан"
    found = re.findall(r"(\d+(?:\.\d+)?)%", list_page)
    bad = [value for value in found if value not in allowed_progress]
    assert not bad, f"jobs list: проценты вне прогресса сегментов: {bad}"

    facts = client.get(f"/api/jobs/{job_id}").json()
    assert "%" not in str(facts["progress_display"]), facts["progress_display"]
    assert "%" not in str(facts.get("phase", ""))
    segments = facts.get("segments")
    if segments is not None:
        assert isinstance(segments["total"], int) and isinstance(segments["processed"], int)


# ------------------------------------------------------------------ T-U1-3


def test_u1_3_health_llm_off_no_global_flag(u1_env: U1Env) -> None:
    """U1-3: LLM недоступен — ``error`` у компонента llm/*/причина;
    общего флага ``healthy`` нет во всём ответе."""
    profile = u1_env.profiles["dead"]
    report = profile.client.get("/api/health").json()
    assert report["app"] == "ok"
    assert report["database"] == "ok"
    assert report["storage"] == "ok"
    assert report["epubcheck"] == "ok"
    for name in ("translation", "fill", "analysis"):
        value = str(report["llm"][name])
        assert value.startswith("error"), f"llm/{name}: {value}"
    assert "healthy" not in json.dumps(report)
    nlp = report["nlp"]
    assert nlp.get("ru") == "n/a (целевой язык)"


# ------------------------------------------------------------------ T-U1-4


def test_u1_4_pair_statuses_match_resolver(u1_env: U1Env) -> None:
    """U1-4: статусы пар из API == свежему резолверу L0 (расхождений 0),
    включая точный перечень недостающих ресурсов."""
    profile = u1_env.profiles["main"]
    data = profile.client.get("/api/languages").json()
    matrix = yaml.safe_load(
        (ROOT / "config/language_matrix.yaml").read_text(encoding="utf-8")
    )
    declared = [(p["source"], p["target"]) for p in matrix["pairs"]]
    api_pairs = {(p["source"], p["target"]): p for p in data["pairs"]}
    assert set(api_pairs) == set(declared), (
        f"набор пар API != матрице: {set(api_pairs) ^ set(declared)}"
    )
    resolver = PairResolver(
        registry_root=profile.settings.languages_root,
        prompts_root=profile.settings.prompts_dir,
    )
    mismatches: list[str] = []
    for source, target in declared:
        fresh = resolver.resolve_pair(source, target)
        row = api_pairs[(source, target)]
        if (
            row["status"] != fresh.status.value
            or list(row["missing"]) != list(fresh.missing)
            or bool(row["creatable"]) != fresh.creatable
        ):
            mismatches.append(f"{source}->{target}: api={row} fresh={fresh}")
    assert not mismatches, "расхождения API/резолвер:\n" + "\n".join(mismatches)


# ------------------------------------------------------------------ T-U1-5


def test_u1_5_estimated_cefr_labels_everywhere(u1_env: U1Env) -> None:
    """U1-5: у каждого языка с `cefr_source: derived` (фактически — все
    исходные) в местах отображения подпись «Estimated CEFR level», 100%."""
    profile = u1_env.profiles["main"]
    data = profile.client.get("/api/languages").json()
    registry = load_registry(profile.settings.languages_root)
    derived = sorted(
        code for code, pack in registry.items() if pack.cefr_source == "derived"
    )
    assert derived, "в реестре нет derived-языков — тест пустой"
    labels = {row["code"]: row["cefr_label"] for row in data["languages"]}
    for code in derived:
        assert labels[code] == "Estimated CEFR level", (code, labels.get(code))
    blob = json.dumps(data, ensure_ascii=False)
    assert blob.count("Estimated CEFR level") == len(derived)


# ------------------------------------------------------------------ T-U1-6


#: Путь endpoint'а → потребители КАЖДОГО метода (файл, литерал в файле).
#: У /api/books и /api/jobs по несколько методов — у каждого свой
#: потребитель (GET — fetch страницы, POST — форма загрузки/создания,
#: DELETE — кнопка удаления U3).
_CONSUMERS: dict[str, tuple[tuple[str, str], ...]] = {
    "/api/health": (("app/web/templates/settings.html", 'data-u1-fetch="/api/health"'),),
    "/api/languages": (
        ("app/web/templates/settings_languages.html", 'data-u1-fetch="/api/languages"'),
    ),
    "/api/books": (
        ("app/web/templates/books.html", 'data-u1-fetch="/api/books"'),  # GET
        ("app/web/static/app.js", 'fetchJSON("/api/books", { method: "POST"'),  # POST
    ),
    "/api/books/{filename}": (
        ("app/web/static/app.js", '"/api/books/" + encodeURIComponent(name)'),  # DELETE
    ),
    "/api/jobs": (
        ("app/web/templates/jobs.html", 'data-u1-fetch="/api/jobs"'),  # GET
        # POST (R3): создание в листе + «Повторить» (повтор из FAILED/CANCELLED)
        ("app/web/static/app.js", 'fetchJSON("/api/jobs", { method: "POST"'),
    ),
    "/api/jobs/{job_id}": (
        ("app/web/static/app.js", '"/api/jobs/" + encodeURIComponent(jobId)'),  # GET
        (
            "app/web/static/app.js",
            '"/api/jobs/" + enc(id), { method: "DELETE"',  # DELETE (R3: лист удаления)
        ),
    ),
    "/api/jobs/purge": (
        ("app/web/static/app.js", 'fetchJSON("/api/jobs/purge", {'),  # POST
    ),
    "/api/jobs/bulk": (
        ("app/web/static/app.js", 'fetchJSON("/api/jobs/bulk", {'),  # POST (R3: выбор/удаление)
    ),
    "/api/jobs/{job_id}/confirm": (("app/web/static/app.js", '"/confirm"'),),
    # R3: pause/resume/cancel — одна точка вызова
    # (fetchJSON("/api/jobs/" + enc(id) + "/" + action)): action — из
    # action-атрибута формы .u3-inline-form (страница задания).
    "/api/jobs/{job_id}/pause": (
        ("app/web/static/app.js", '"/api/jobs/" + enc(id) + "/" + action'),
    ),
    "/api/jobs/{job_id}/resume": (
        ("app/web/static/app.js", '"/api/jobs/" + enc(id) + "/" + action'),
    ),
    "/api/jobs/{job_id}/cancel": (
        ("app/web/static/app.js", '"/api/jobs/" + enc(id) + "/" + action'),
    ),
    "/api/jobs/{job_id}/logs": (
        ("app/web/templates/job.html", "/api/jobs/{{ facts.id }}/logs"),
    ),
    "/api/jobs/{job_id}/logs/clear": (
        ("app/web/static/app.js", '"/logs/clear"'),  # POST
    ),
    "/api/jobs/{job_id}/quality": (
        ("app/web/templates/job.html", "/api/jobs/{{ facts.id }}/quality"),
    ),
    "/api/jobs/{job_id}/result": (
        ("app/web/templates/job.html", "/api/jobs/{{ facts.id }}/result"),
    ),
    "/api/models": (("app/web/templates/settings_llm.html", 'data-u1-fetch="/api/models"'),),
    "/api/config": (("app/web/templates/settings.html", 'data-u1-fetch="/api/config"'),),
    "/api/prompts": (
        ("app/web/static/app.js", 'fetchJSON("/api/prompts", {'),  # POST
    ),
    "/api/prompts/content": (
        ("app/web/static/app.js", '"/api/prompts/content?path="'),  # GET
    ),
    "/api/prompts/version": (
        ("app/web/static/app.js", '"/api/prompts/version?path="'),  # DELETE
    ),
}


def test_u1_6_every_endpoint_has_consumer(u1_env: U1Env) -> None:
    """U1-6: каждый /api-endpoint зарегистрирован И имеет потребителя в UI
    (двусторонне: ни одного без потребителя, ни одного потребителя без
    endpoint'а)."""
    def collect(routes: Any) -> list[str]:
        paths: list[str] = []
        for route in routes:
            path = getattr(route, "path", None)
            if path:
                paths.append(path)
            original = getattr(route, "original_router", None)
            if original is not None:  # FastAPI include_router-обёртка
                paths.extend(collect(original.routes))
        return paths

    api_paths = sorted(
        {p for p in collect(u1_env.profiles["main"].app.routes) if p.startswith("/api")}
    )
    assert api_paths == sorted(_CONSUMERS), (
        f"маршруты API != карте потребителей: {set(api_paths) ^ set(_CONSUMERS)}"
    )
    for path, consumers in _CONSUMERS.items():
        for rel, literal in consumers:
            text = (ROOT / rel).read_text(encoding="utf-8")
            assert literal in text, f"{path}: потребитель {literal!r} не найден в {rel}"


# ------------------------------------------------------------------ T-U1-7


def test_u1_7_auth_denies_without_secret(u1_env: U1Env) -> None:
    """U1-7: при заданном SERVER_AUTH_SECRET ВСЕ запросы (100% маршрутов,
    страницы + API, GET и POST) без секрета — 401; с секретом — работают.
    Неправильный секрет — 401. TЗ UI §7.1: статика /static публична
    (не несёт данных и секретов) — 401/403-страницы в общем оформлении,
    гостю нужен style.css."""
    client = u1_env.profiles["auth"].client
    routes = (
        "/",
        "/books",
        "/jobs",
        "/languages",
        "/models",
        "/prompts",
        "/settings",
        "/api/health",
        "/api/languages",
        "/api/books",
        "/api/jobs",
        "/api/models",
        "/api/config",
        "/jobs/no-such-job",
        "/api/jobs/no-such-job",
    )
    for path in routes:
        response = client.get(path)
        assert response.status_code == 401, (
            f"GET {path} без секрета: {response.status_code} (ожидался 401)"
        )
    # TЗ UI §7.1: статика публична — оформление страницы входа для гостя.
    for path in ("/static/app.js", "/static/style.css", "/static/favicon.ico"):
        response = client.get(path)
        assert response.status_code == 200, (
            f"GET {path} без секрета: {response.status_code} (статика публична)"
        )
    for path in routes:
        response = client.get(path, headers=_auth_header())
        assert response.status_code != 401, f"GET {path} с секретом: {response.status_code}"
    for path in ("/books", "/jobs", "/languages", "/models", "/prompts", "/settings"):
        assert client.get(path, headers=_auth_header()).status_code == 200
    wrong = "Basic " + base64.b64encode(b"user:wrong-secret").decode()
    assert client.get("/jobs", headers={"Authorization": wrong}).status_code == 401
    assert (
        client.post(
            "/api/jobs",
            json={"book": "x.epub", "target_language": "ru"},
        ).status_code
        == 401
    )
    assert client.get("/api/health").headers.get("WWW-Authenticate", "").startswith("Basic")


# ------------------------------------------------------------------ T-U1-8


def test_u1_8_secrets_never_in_html_or_access_log(u1_env: U1Env) -> None:
    """U1-8: секреты (общий секрет доступа + LLM_API_KEY) — 0 вхождений во
    всех HTML/JSON ответах и в access log реального uvicorn-сервера."""
    profile = u1_env.profiles["auth"]
    secrets = (AUTH_SECRET, LLM_KEY)
    for path in (
        "/books",
        "/jobs",
        "/languages",
        "/models",
        "/prompts",
        "/settings",
        "/api/health",
        "/api/languages",
        "/api/books",
        "/api/models",
        "/api/config",
        "/static/app.js",
        "/static/style.css",
    ):
        text = profile.client.get(path, headers=_auth_header()).text
        for secret in secrets:
            assert secret not in text, f"секрет {secret!r} в ответе {path}"

    log_file = u1_env.base / "u1_8_access.log"
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {
            "file": {
                "class": "logging.FileHandler",
                "filename": str(log_file),
                "encoding": "utf-8",
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["file"], "level": "INFO", "propagate": False},
        },
    }
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(profile.app, host="127.0.0.1", port=port, log_config=log_config)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.05)
    assert server.started, "uvicorn не стартовал"
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15) as http:
            for path in ("/jobs", "/api/health", "/static/app.js"):
                response = http.get(path, headers=_auth_header())
                assert response.status_code == 200, (path, response.status_code)
            denied = http.get("/jobs")
            assert denied.status_code == 401
    finally:
        server.should_exit = True
        thread.join(timeout=20)
    content = log_file.read_text(encoding="utf-8")
    assert "GET /jobs" in content, "access log не записал запросы:"
    for secret in secrets:
        assert secret not in content, f"секрет {secret!r} в access log"


# ------------------------------------------------------------------ T-U1-9


def test_u1_9_full_api_cycle(u1_env: U1Env) -> None:
    """T-U1-9: полный цикл только через API/UI: upload → create →
    (confirm при NEEDS_CONFIRMATION) → прогресс → терминал → result (main +
    cards) + quality. Result — побайтово = файл на диске."""
    profile = u1_env.profiles["main"]
    client, repo, settings = profile.client, profile.repo, profile.settings
    book_name = _upload_book(client, u1_env.books["full"])
    upload_meta = None
    for book in client.get("/api/books").json():
        if book["name"] == book_name:
            upload_meta = book
            break
    assert upload_meta is not None and upload_meta["detected"] == "en"

    job_id = _create_job(client, book_name, level="Learning", cefr="B1")
    proc = _start_worker(profile.cfg, profile.env_file, u1_env.base / "worker_u1_9.log")
    u1_env.workers.append(proc)
    try:
        facts = _wait_job_terminal(client, job_id, timeout=420)
        proc.wait(timeout=180)
    except Exception:
        proc.kill()
        raise

    assert facts["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS"), facts["status"]
    assert facts["result_ready"] is True

    jdir = settings.job_dir(job_id)
    learning = jdir / "learning.epub"
    main_path = learning if learning.is_file() else jdir / "target.epub"
    assert main_path.is_file()
    response = client.get(f"/api/jobs/{job_id}/result")
    assert response.status_code == 200
    assert response.content == main_path.read_bytes(), "result != файл на диске (побайтово)"

    if (jdir / "cards.csv").is_file():
        cards = client.get(f"/api/jobs/{job_id}/result?artifact=cards.csv")
        assert cards.status_code == 200
        assert cards.content == (jdir / "cards.csv").read_bytes()
    else:
        assert client.get(f"/api/jobs/{job_id}/result?artifact=cards.csv").status_code == 404

    quality = client.get(f"/api/jobs/{job_id}/quality")
    assert quality.status_code == 200
    q = quality.json()
    for key in ("qc", "warnings", "epubcheck", "alignment", "glossary_consistency", "l3"):
        assert key in q, key
    assert q["alignment"]["total"], "E6 не отмерен в quality-ответе"

    final_job = repo.get(job_id)
    assert final_job is not None and math.isclose(final_job.progress, 1.0, rel_tol=1e-6)
    assert client.get(f"/api/jobs/{job_id}/logs").status_code == 200


# ------------------------------------------------------------------ T-U1-10


def test_u1_10_llm_off_mid_job_shows_error(u1_env: U1Env) -> None:
    """T-U1-10: LLM убита в середине перевода — задание FAILED с точной
    причиной; UI/API показывают ошибку, а не «застрявший» прогресс."""
    profile = u1_env.profiles["main"]
    client, repo = profile.client, profile.repo
    book_name = _upload_book(client, u1_env.books["slow"])
    job_id = _create_job(client, book_name)

    u1_env.server.set_delay(0.3)
    proc = _start_worker(profile.cfg, profile.env_file, u1_env.base / "worker_u1_10.log")
    u1_env.workers.append(proc)
    try:
        deadline = time.monotonic() + 120
        job: Job | None = None
        while time.monotonic() < deadline:
            job = repo.get(job_id)
            if job is not None and job.status.value == "TRANSLATING":
                break
            time.sleep(0.05)
        job = repo.get(job_id)
        assert job is not None and job.status.value == "TRANSLATING", (
            f"не успели застать TRANSLATING: {None if job is None else job.status}"
        )
        u1_env.server.stop()  # LLM в середине перевода — off
        u1_env.server.set_delay(0.0)

        final: dict[str, Any] | None = None
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            candidate = client.get(f"/api/jobs/{job_id}").json()
            if candidate.get("status_terminal"):
                final = candidate
                break
            time.sleep(0.2)
        proc.wait(timeout=180)
    finally:
        u1_env.server.set_delay(0.0)

    assert final is not None, "задание не дошло до терминального состояния"
    assert final["status"] == "FAILED", final["status"]
    assert final["error"], "нет причины ошибки"
    assert final["status_terminal"] is True

    page = client.get(f"/jobs/{job_id}")
    assert page.status_code == 200
    assert html.escape(final["error"]) in page.text, "причина ошибки не показана в UI"
    assert "FAILED" in page.text
    # Прогресс не выдаётся за «в работе»: статус в UI — терминальный.
    # Серия R §4: класс по группе (единая таблица views.STATUS_GROUP):
    # FAILED -> err.
    assert "status-err" in page.text
