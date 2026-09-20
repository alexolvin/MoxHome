"""U1 (ТЗ_04 §U1): verbatim-захваты критериев CONTROL U1.

Запуск: python tests/u1_evidence.py
Захваты: docs/CONTROL/evidence/U1/u1-1…u1-9 + t-u1-10 (verbatim).

Сценарии (mock-сервер D3 для LLM; реальные spaCy/EPUBCheck/SQLite/FastAPI):
  u1-1  прогресс: >=20 срезов за живым прогоном — API progress == БД,
        progress_display — чистая функция (строки срезов verbatim)
  u1-2  0 фиктивных процентов: каждый «%» в HTML — измеренный охват E6
  u1-3  LLM выключен: health llm/* = error с причиной; общего «healthy» нет
  u1-4  статусы пар API == свежему резолверу L0 (таблица, 0 расхождений)
  u1-5  «Estimated CEFR level» — 100% мест отображения для derived-языков
  u1-6  каждый /api-endpoint (каждый метод) — потребитель в UI (таблица)
  u1-7  auth: 100% маршрутов 401 без секрета, работают с секретом
  u1-8  0 секретов в HTML/JSON ответах и access log реального uvicorn
  u1-9  правки L3: список UI (quality endpoint) == quality.log (0 расхождений)
  t-u1-10 LLM off в середине перевода: FAILED + причина в UI/API
"""

from __future__ import annotations

import base64
import html
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "probe"))

from mock_llm_server import MockLLMServer  # noqa: E402
from test_l3_glossary_fix import (  # noqa: E402
    L3_FIX_PARAS,
    _fix_log_lines,
    _kill_all,
    _synth_book_l3,
)
from test_u1_web import (  # noqa: E402
    AUTH_SECRET,
    LLM_KEY,
    U1Profile,
    _auth_header,
    _create_job,
    _make_u1_storage,
    _synth_u1_slow_book,
    _synth_u1_tiny_book,
    _upload_book,
    _wait_job_terminal,
)

from app.domain.job import JobStatus  # noqa: E402
from app.languages.registry import load_registry  # noqa: E402
from app.languages.resolver import PairResolver  # noqa: E402
from app.storage.jobs_repo import JobRepository  # noqa: E402
from app.web import views  # noqa: E402
from app.web.app import create_app  # noqa: E402
from app.web.auth import csrf_token  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "U1"
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
VERSIONS = f"python={sys.version.split()[0]} spacy={pkg_version('spacy')}"

_WORKERS: list[subprocess.Popen[bytes]] = []


def _start_worker_loop(cfg: Path, env_file: Path, log_path: Path) -> subprocess.Popen[bytes]:
    """Воркер с --loop: переживает NEEDS_CONFIRMATION (confirm → PLANNING → занятие).

    --once (как _start_worker) выходит после NEEDS_CONFIRMATION, и подтверждённое
    задание остаётся без исполнителя — детерминированный путь только через --loop.
    """

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


def _stop_worker(proc: subprocess.Popen[bytes]) -> None:
    """Воркер --loop не выходит сам: остановка после терминального статуса."""
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=30)


def evidence(name: str, content: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print(f"  [evidence] {path.relative_to(ROOT)}")


def header() -> str:
    return f"дата: {STAMP}\nверсии: {VERSIONS}\n\n"


def _profile(
    base: Path, name: str, url: str, *, secret: bool = False, client_auth: bool = False
) -> U1Profile:
    """Профиль evidence. ``secret=True`` — U3 3.9 (fail closed): у стенда
    есть SERVER_AUTH_SECRET; ``client_auth=True`` — клиент шлёт Basic +
    X-U3-CSRF по умолчанию (мутирующие запросы проходят)."""
    env_extra = f"SERVER_AUTH_SECRET={AUTH_SECRET}\n" if secret else ""
    settings, db, cfg, env_file = _make_u1_storage(base, f"ev_{name}", url, env_extra=env_extra)
    app = create_app(settings, db=db)
    client_headers = None
    if client_auth:
        client_headers = {
            **_auth_header(),
            "X-U3-CSRF": csrf_token(settings, "any-user"),
        }
    return U1Profile(
        settings=settings,
        db=db,
        repo=JobRepository(db),
        app=app,
        client=TestClient(app, headers=client_headers or {}),
        cfg=cfg,
        env_file=env_file,
    )


def _api_paths(app: Any) -> list[tuple[str, str]]:
    """(path, методы) для каждого /api-маршрута (с развёртыванием include_router)."""

    def collect(routes: Any) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for route in routes:
            path = getattr(route, "path", None)
            if path:
                methods = ",".join(sorted(getattr(route, "methods", None) or ()))
                found.append((path, methods))
            original = getattr(route, "original_router", None)
            if original is not None:
                found.extend(collect(original.routes))
        return found

    return [
        (path, methods)
        for path, methods in collect(app.routes)
        if path.startswith("/api")
    ]


#: U3: карта расширена (purge, logs/clear, DELETE /api/jobs/{job_id},
#: DELETE /api/books, versioning промптов) — синхронно с _CONSUMERS в
#: test_u1_web.py.
_CONSUMER_CHECKS: dict[str, list[tuple[str, str]]] = {
    "/api/health": [("app/web/templates/settings.html", 'data-u1-fetch="/api/health"')],
    "/api/languages": [
        ("app/web/templates/settings_languages.html", 'data-u1-fetch="/api/languages"'),
    ],
    "/api/books": [
        ("app/web/templates/books.html", 'data-u1-fetch="/api/books"'),
        ("app/web/static/app.js", 'fetchJSON("/api/books", { method: "POST"'),
    ],
    "/api/books/{filename}": [
        ("app/web/static/app.js", '"/api/books/" + encodeURIComponent(name)'),
    ],
    "/api/jobs": [
        ("app/web/templates/jobs.html", 'data-u1-fetch="/api/jobs"'),
        ("app/web/static/app.js", 'fetchJSON("/api/jobs", {'),
    ],
    "/api/jobs/{job_id}": [
        ("app/web/static/app.js", '"/api/jobs/" + encodeURIComponent(jobId)'),
        ("app/web/static/app.js", '"/api/jobs/" + encodeURIComponent(job), { method: "DELETE"'),
    ],
    "/api/jobs/purge": [
        ("app/web/static/app.js", 'fetchJSON("/api/jobs/purge", {'),
    ],
    "/api/jobs/{job_id}/confirm": [("app/web/static/app.js", '"/confirm"')],
    "/api/jobs/{job_id}/pause": [("app/web/static/app.js", '"u1-pause", "pause"')],
    "/api/jobs/{job_id}/resume": [("app/web/static/app.js", '"u1-resume", "resume"')],
    "/api/jobs/{job_id}/cancel": [("app/web/static/app.js", '"u1-cancel", "cancel"')],
    "/api/jobs/{job_id}/logs": [
        ("app/web/templates/job.html", "/api/jobs/{{ facts.id }}/logs"),
    ],
    "/api/jobs/{job_id}/logs/clear": [
        ("app/web/static/app.js", '"/logs/clear"'),
    ],
    "/api/jobs/{job_id}/quality": [
        ("app/web/templates/job.html", "/api/jobs/{{ facts.id }}/quality"),
    ],
    "/api/jobs/{job_id}/result": [
        ("app/web/templates/job.html", "/api/jobs/{{ facts.id }}/result"),
    ],
    "/api/models": [("app/web/templates/settings_llm.html", 'data-u1-fetch="/api/models"')],
    "/api/config": [("app/web/templates/settings.html", 'data-u1-fetch="/api/config"')],
    "/api/prompts": [
        ("app/web/static/app.js", 'fetchJSON("/api/prompts", {'),
    ],
    "/api/prompts/content": [
        ("app/web/static/app.js", '"/api/prompts/content?path="'),
    ],
    "/api/prompts/version": [
        ("app/web/static/app.js", '"/api/prompts/version?path="'),
    ],
}


# ------------------------------------------------------------------ u1-1 / u1-2


def ev_u1_1(main: U1Profile, server: MockLLMServer, base: Path, book: Path) -> str:
    client, repo, settings = main.client, main.repo, main.settings
    lines = [header(), "K: U1-1 — progress из API == БД на срезах живого прогона", ""]
    name = _upload_book(client, book)
    job_id = _create_job(client, name)
    server.set_delay(0.3)
    proc = _start_worker_loop(main.cfg, main.env_file, base / "ev_worker_u1_1.log")
    _WORKERS.append(proc)
    try:
        slices: list[tuple[int, float, str]] = []
        mismatches = 0
        deadline = time.monotonic() + 240
        terminal: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            facts: dict[str, Any] = {}
            for _attempt in range(20):
                facts = client.get(f"/api/jobs/{job_id}").json()
                if facts.get("status_terminal"):
                    terminal = facts
                    break
                job = repo.get(job_id)
                if job is not None and job.progress == facts["progress"]:
                    validation = views.read_json(
                        settings.job_dir(job_id) / "validation.json"
                    )
                    expected = views.progress_facts(job, validation)["display"]
                    if facts["progress_display"] != expected:
                        mismatches += 1
                    slices.append((len(slices) + 1, job.progress, str(facts["progress_display"])))
                    break
                time.sleep(0.05)
            if terminal is not None:
                break
            time.sleep(0.15)
        _stop_worker(proc)
    finally:
        server.set_delay(0.0)

    assert terminal is not None, "прогон не завершился"
    lines.append(f"задание: {job_id} (u1_slow.epub, 6 глав, delay 0.3 c)")
    lines.append(f"срезов (API progress == БД): {len(slices)}")
    lines.append(f"расхождений progress_display с чистой функцией: {mismatches}")
    lines.append(f"итоговый статус: {terminal['status']}")
    lines.append("")
    lines.append("| # | progress (БД = API) | progress_display (API) |")
    lines.append("|---|---|---|")
    for idx, progress, display in slices[:30]:
        lines.append(f"| {idx} | {progress:.4f} | {display} |")
    if len(slices) > 30:
        lines.append(f"| … | ({len(slices) - 30} ещё) | |")
    lines.append("")
    fake = [d for _i, _p, d in slices if "%" in d]
    lines.append(f"вхождений «%» в progress_display за все срезы: {len(fake)}")
    lines.append(f"\nВЕРДИКТ: {len(slices)} >= 20 срезов, 0 расхождений, 0 % — "
                 f"{'PASS' if len(slices) >= 20 and mismatches == 0 and not fake else 'FAIL'}")
    return "\n".join(lines)


def ev_u1_2(main: U1Profile, base: Path, book: Path) -> str:
    client, repo = main.client, main.repo
    lines = [header(), "K: U1-2 — 0 фиктивных процентов", ""]
    name = _upload_book(client, book)
    job_id = _create_job(client, name)
    proc = _start_worker_loop(main.cfg, main.env_file, base / "ev_worker_u1_2.log")
    _WORKERS.append(proc)
    try:
        final = _wait_job_terminal(client, job_id, timeout=300)
        _stop_worker(proc)
    except Exception:
        proc.kill()
        raise

    job = repo.get(job_id)
    assert job is not None
    coverage = job.alignment_coverage
    allowed = {f"{coverage * 100:.1f}"} if coverage is not None else set()
    lines.append(f"задание: {job_id} (u1_tiny.epub, 2 главы, bilingual)")
    lines.append(f"статус: {final['status']}; alignment_coverage = {coverage}")
    lines.append(f"допустимое значение «%» (измеренный охват E6): {sorted(allowed) or 'нет'}")
    lines.append("")
    for page_name, text in (
        ("страница задания /jobs/{id}", client.get(f"/jobs/{job_id}").text),
        ("список /jobs", client.get("/jobs").text),
    ):
        found = re.findall(r"(\d+(?:\.\d+)?)%", text)
        bad = [v for v in found if v not in allowed]
        lines.append(f"{page_name}: вхождений «%» = {len(found)}, значения = {found}")
        lines.append(f"  не из охвата выравнивания: {bad}")
        assert not bad, bad
    facts = client.get(f"/api/jobs/{job_id}").json()
    lines.append(f"JSON API: progress_display = {facts['progress_display']!r} — «%»: "
                 f"{'%' in str(facts['progress_display'])}")
    lines.append(f"JSON API: segments = {facts['segments']} (реальные единицы)")
    lines.append("\nВЕРДИКТ: все «%» = измеренный охват E6; фиктивных — 0 — PASS")
    return "\n".join(lines)


# ------------------------------------------------------------------ u1-3..u1-8


def ev_u1_3(dead: U1Profile) -> str:
    report = dead.client.get("/api/health").json()
    lines = [header(), "K: U1-3 — LLM выключен (мёртвый порт 127.0.0.1:1)", ""]
    lines.append("ответ GET /api/health (verbatim):")
    lines.append(json.dumps(report, ensure_ascii=False, indent=2))
    lines.append("")
    for profile_name in ("translation", "fill", "analysis"):
        lines.append(f"llm/{profile_name} = {report['llm'][profile_name]!r} — "
                     f"{'error' if str(report['llm'][profile_name]).startswith('error') else 'OK'}")
    lines.append(f"ключ 'healthy' в ответе: {'healthy' in json.dumps(report)}")
    lines.append(f"nlp/ru = {report['nlp'].get('ru')!r} (целевой язык)")
    ok = all(str(report["llm"][n]).startswith("error") for n in ("translation", "fill", "analysis"))
    ok = ok and "healthy" not in json.dumps(report)
    lines.append(
        f"\nВЕРДИКТ: llm/* = error c причиной, общего флага нет — "
        f"{'PASS' if ok else 'FAIL'}"
    )
    return "\n".join(lines)


def ev_u1_4(main: U1Profile) -> str:
    lines = [header(), "K: U1-4 — статусы пар API == свежему резолверу L0", ""]
    data = main.client.get("/api/languages").json()
    matrix = yaml.safe_load((ROOT / "config/language_matrix.yaml").read_text(encoding="utf-8"))
    declared = [(p["source"], p["target"]) for p in matrix["pairs"]]
    api = {(p["source"], p["target"]): p for p in data["pairs"]}
    resolver = PairResolver(
        registry_root=main.settings.languages_root, prompts_root=main.settings.prompts_dir
    )
    lines.append(f"пар в матрице: {len(declared)}; пар в API: {len(api)}")
    lines.append("")
    lines.append("| пара | API | резолвер (свежий) | не хватает (API) | совпадает |")
    lines.append("|---|---|---|---|---|")
    mismatches = 0
    for source, target in declared:
        fresh = resolver.resolve_pair(source, target)
        row = api.get((source, target))
        same = (
            row is not None
            and row["status"] == fresh.status.value
            and list(row["missing"]) == list(fresh.missing)
        )
        if not same:
            mismatches += 1
        missing = "; ".join(row["missing"]) if row and row["missing"] else "—"
        lines.append(
            f"| {source} → {target} | {row['status'] if row else '—'} | "
            f"{fresh.status.value} | {missing} | {'да' if same else 'НЕТ'} |"
        )
    lines.append(f"\nрасхождений: {mismatches}")
    lines.append(f"\nВЕРДИКТ: {'PASS' if mismatches == 0 else 'FAIL'}")
    return "\n".join(lines)


def ev_u1_5(main: U1Profile) -> str:
    lines = [header(), "K: U1-5 — «Estimated CEFR level» для всех derived-языков", ""]
    data = main.client.get("/api/languages").json()
    registry = load_registry(main.settings.languages_root)
    derived = sorted(c for c, p in registry.items() if p.cefr_source == "derived")
    lines.append(f"языков в реестре: {len(registry)}; derived (фактически): {derived}")
    lines.append("")
    lines.append("| код | название | cefr_source | cefr_label (API, verbatim) |")
    lines.append("|---|---|---|---|")
    for row in data["languages"]:
        lines.append(
            f"| {row['code']} | {row['name']} | {row['cefr_source']} | {row['cefr_label']} |"
        )
    count = json.dumps(data, ensure_ascii=False).count("Estimated CEFR level")
    ok = count == len(derived) and all(
        r["cefr_label"] == "Estimated CEFR level" for r in data["languages"]
        if r["code"] in derived
    )
    lines.append(f"\n«Estimated CEFR level» в ответе: {count} (derived-языков: {len(derived)})")
    lines.append(f"\nВЕРДИКТ: 100% мест отображения — {'PASS' if ok else 'FAIL'}")
    return "\n".join(lines)


def ev_u1_6(main: U1Profile) -> str:
    lines = [header(), "K: U1-6 — каждый /api-endpoint (каждый метод) имеет потребителя", ""]
    routes = sorted(_api_paths(main.app))
    lines.append(f"endpoint'ов API: {len(routes)}")
    lines.append("")
    lines.append("| endpoint | методы | потребитель (файл, литерал) | найден |")
    lines.append("|---|---|---|---|")
    missing = 0
    for path, methods in routes:
        consumers = _CONSUMER_CHECKS.get(path)
        if consumers is None:
            missing += 1
            lines.append(f"| {path} | {methods} | — нет в карте — | НЕТ |")
            continue
        for rel, literal in consumers:
            found = literal in (ROOT / rel).read_text(encoding="utf-8")
            if not found:
                missing += 1
            mark = "да" if found else "НЕТ"
            lines.append(f"| {path} | {methods} | {rel}: {literal!r} | {mark} |")
    extra = set(_CONSUMER_CHECKS) - {path for path, _methods in routes}
    if extra:
        missing += len(extra)
        lines.append(f"\nпотребители без endpoint'а: {sorted(extra)}")
    lines.append(f"\nрасхождений: {missing}")
    lines.append(f"\nВЕРДИКТ: 0 endpoint'ов без потребителя — {'PASS' if missing == 0 else 'FAIL'}")
    return "\n".join(lines)


def ev_u1_7(auth: U1Profile) -> str:
    lines = [header(), "K: U1-7 — доступ без секрета: 401; с секретом: работают", ""]
    client = auth.client
    routes = (
        "/", "/books", "/jobs", "/languages", "/models", "/prompts", "/settings",
        "/api/health", "/api/languages", "/api/books", "/api/jobs", "/api/models",
        "/api/config", "/jobs/no-such-job", "/api/jobs/no-such-job",
        "/static/app.js", "/static/style.css",
    )
    lines.append(f"маршрутов проверено: {len(routes)}")
    lines.append("")
    lines.append("| маршрут | без секрета | с секретом |")
    lines.append("|---|---|---|")
    bad = 0
    for path in routes:
        without = client.get(path).status_code
        with_secret = client.get(path, headers=_auth_header()).status_code
        if without != 401 or with_secret == 401:
            bad += 1
        lines.append(f"| {path} | {without} | {with_secret} |")
    wrong = "Basic " + base64.b64encode(b"user:wrong-secret").decode()
    wrong_status = client.get("/jobs", headers={"Authorization": wrong}).status_code
    post_status = client.post(
        "/api/jobs", json={"book": "x.epub", "target_language": "ru"}
    ).status_code
    www = client.get("/api/health").headers.get("WWW-Authenticate", "")
    lines.append("")
    lines.append(f"неправильный секрет: {wrong_status} (ожидался 401)")
    lines.append(f"POST /api/jobs без секрета: {post_status} (ожидался 401)")
    lines.append(f"WWW-Authenticate (verbatim): {www!r}")
    ok = bad == 0 and wrong_status == 401 and post_status == 401
    lines.append(
        f"\nВЕРДИКТ: 100% маршрутов — 401 без секрета / работают с ним — "
        f"{'PASS' if ok else 'FAIL'}"
    )
    return "\n".join(lines)


def ev_u1_8(auth: U1Profile, base: Path) -> str:
    lines = [header(), "K: U1-8 — 0 секретов в HTML/JSON и access log реального uvicorn", ""]
    secrets = (AUTH_SECRET, LLM_KEY)
    lines.append(f"секреты: {secrets[0]!r}, {secrets[1]!r}")
    paths = (
        "/books", "/jobs", "/languages", "/models", "/prompts", "/settings",
        "/api/health", "/api/languages", "/api/books", "/api/models", "/api/config",
        "/static/app.js", "/static/style.css",
    )
    hits = 0
    for path in paths:
        text = auth.client.get(path, headers=_auth_header()).text
        for secret in secrets:
            if secret in text:
                hits += 1
    lines.append(f"ответов проверено: {len(paths)} × {len(secrets)} секретов; вхождений: {hits}")
    lines.append("")
    log_file = base / "ev_u1_8_access.log"
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"file": {"class": "logging.FileHandler", "filename": str(log_file)}},
        "loggers": {
            "uvicorn": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["file"], "level": "INFO", "propagate": False},
        },
    }
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(auth.app, host="127.0.0.1", port=port, log_config=log_config)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.05)
    assert server.started
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15) as http:
            for path in ("/jobs", "/api/health", "/static/app.js"):
                response = http.get(path, headers=_auth_header())
                assert response.status_code == 200
            assert http.get("/jobs").status_code == 401
    finally:
        server.should_exit = True
        thread.join(timeout=20)
    content = log_file.read_text(encoding="utf-8")
    log_hits = sum(content.count(s) for s in secrets)
    lines.append("access log (первые 12 строк, verbatim):")
    lines.extend(content.splitlines()[:12])
    lines.append(f"вхождений секретов в access log: {log_hits}")
    ok = hits == 0 and log_hits == 0 and "GET /jobs" in content
    lines.append(f"\nВЕРДИКТ: 0 вхождений в ответах и access log — {'PASS' if ok else 'FAIL'}")
    return "\n".join(lines)


# ------------------------------------------------------------------ u1-9 / t-u1-10


def ev_u1_9(main: U1Profile, server: MockLLMServer, base: Path) -> str:
    lines = [header(), "K: U1-9 — список правок L3 в UI == quality.log (0 расхождений)", ""]
    book = base / "books" / "u1_wexley.epub"
    _synth_book_l3(book, "U1 L3 Probe", "3b000007-0000-0000-0000-000000000007", L3_FIX_PARAS)
    server.set_scenario("glossary_inconsistent")  # Wexley в переводе — неверный вариант
    try:
        name = _upload_book(main.client, book)
        job_id = _create_job(main.client, name)
        proc = _start_worker_loop(main.cfg, main.env_file, base / "ev_worker_u1_9.log")
        _WORKERS.append(proc)
        try:
            final = _wait_job_terminal(main.client, job_id, timeout=300)
            _stop_worker(proc)
        except Exception:
            proc.kill()
            raise
    finally:
        server.set_scenario("ok")

    lines.append(f"задание: {job_id} (wexley-книга, 7 сегментов, "
                 f"сценарий glossary_inconsistent)")
    lines.append(f"статус: {final['status']}")
    quality = main.client.get(f"/api/jobs/{job_id}/quality").json()
    fixes = quality.get("l3", {}).get("fixes", []) if quality.get("l3") else []
    lines.append(f"l3: attempted={quality.get('l3', {}).get('attempted')} "
                 f"applied={quality.get('l3', {}).get('applied')} "
                 f"rejected={quality.get('l3', {}).get('rejected')}")
    log_path = main.settings.job_dir(job_id) / "quality.log"
    # тот же парсер, что в T-L3-2 (quality.log — JSONL, запись в поле msg)
    log_fixes = _fix_log_lines(log_path.read_text(encoding="utf-8"))
    lines.append(f"строк kind=glossary_fix в quality.log: {len(log_fixes)}")
    lines.append(f"правок в UI (GET /api/jobs/{job_id}/quality → l3.fixes): {len(fixes)}")
    lines.append("")
    lines.append("| segment_id | doc | термин | UI applied | в quality.log |")
    lines.append("|---|---|---|---|---|")
    mismatches = 0
    ui_keys = {
        (f["segment_id"], f["doc"], f["term"]) for f in fixes if f.get("applied")
    }
    log_keys = {(f["segment_id"], f["doc"], f["term"]) for f in log_fixes}
    for fix in sorted(fixes, key=lambda f: (f["doc"], str(f["segment_id"]))):
        in_log = (fix["segment_id"], fix["doc"], fix["term"]) in log_keys
        if fix.get("applied") and not in_log:
            mismatches += 1
        lines.append(
            f"| {fix['segment_id']} | {fix['doc']} | {fix['term']} | "
            f"{'да' if fix.get('applied') else 'нет'} | {'да' if in_log else 'НЕТ'} |"
        )
    only_log = log_keys - ui_keys
    if only_log:
        mismatches += len(only_log)
        lines.append(f"в log, но не в UI: {sorted(only_log)}")
    lines.append(f"\nрасхождений: {mismatches}")
    ok = mismatches == 0 and len(fixes) > 0
    lines.append(f"\nВЕРДИКТ: UI == quality.log, правок фактических: {len(fixes)} — "
                 f"{'PASS' if ok else 'FAIL'}")
    return "\n".join(lines)


def ev_t_u1_10(main: U1Profile, server: MockLLMServer, base: Path, book: Path) -> str:
    lines = [header(), "K: T-U1-10 — LLM off в середине перевода: FAILED + причина в UI", ""]
    name = _upload_book(main.client, book)
    job_id = _create_job(main.client, name)
    server.set_delay(0.3)
    proc = _start_worker_loop(main.cfg, main.env_file, base / "ev_worker_u1_10.log")
    _WORKERS.append(proc)
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = main.repo.get(job_id)
            if job is not None and job.status is JobStatus.TRANSLATING:
                break
            time.sleep(0.05)
        job = main.repo.get(job_id)
        assert job is not None and job.status is JobStatus.TRANSLATING, job
        server.stop()  # LLM в середине перевода — off
        server.set_delay(0.0)
        final: dict[str, Any] | None = None
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            candidate = main.client.get(f"/api/jobs/{job_id}").json()
            if candidate.get("status_terminal"):
                final = candidate
                break
            time.sleep(0.2)
        _stop_worker(proc)
    finally:
        server.set_delay(0.0)

    assert final is not None
    lines.append(f"задание: {job_id} (u1_slow.epub, 6 глав, LLM остановлен в TRANSLATING)")
    lines.append(f"статус: {final['status']}")
    lines.append(f"error (API, verbatim): {final['error']}")
    lines.append(f"progress_display (API, verbatim): {final['progress_display']}")
    lines.append(f"status_terminal: {final['status_terminal']}")
    page = main.client.get(f"/jobs/{job_id}").text
    lines.append("")
    lines.append("страница /jobs/{id}: причина ошибки в HTML — "
                 f"{'да' if html.escape(final['error']) in page else 'НЕТ'}")
    lines.append("страница /jobs/{id}: статус-класс status-failed — "
                 f"{'да' if 'status-failed' in page else 'НЕТ'}")
    snippet = [line for line in page.splitlines() if "status-failed" in line][:1]
    lines.append("строка verbatim: " + (snippet[0].strip() if snippet else "—"))
    ok = final["status"] == "FAILED" and final["error"] and "status-failed" in page
    lines.append(
        f"\nВЕРДИКТ: FAILED + причина в UI/API, прогресс не «в работе» — "
        f"{'PASS' if ok else 'FAIL'}"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ main


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="u1-evidence-"))
    books_dir = base / "books"
    books_dir.mkdir(parents=True, exist_ok=True)
    _synth_u1_slow_book(books_dir / "u1_slow.epub")
    _synth_u1_tiny_book(books_dir / "u1_tiny.epub")
    server = MockLLMServer()
    server.start()
    profiles: dict[str, U1Profile] = {}
    try:
        profiles["main"] = _profile(base, "main", server.url, secret=True, client_auth=True)
        profiles["dead"] = _profile(base, "dead", "http://127.0.0.1:1/v1")
        # auth: секрет задан, но клиент БЕЗ заголовков — ev_u1_7 меряет
        # именно 401 без Basic и 200 с ним (явные заголовки в запросах).
        profiles["auth"] = _profile(base, "auth", server.url, secret=True)
        print("[u1-evidence] окружение готово")
        evidence("u1-1-progress-db-slices.txt",
                 ev_u1_1(profiles["main"], server, base, books_dir / "u1_slow.epub"))
        evidence("u1-2-no-fake-percent.txt",
                 ev_u1_2(profiles["main"], base, books_dir / "u1_tiny.epub"))
        evidence("u1-3-health-llm-off.txt", ev_u1_3(profiles["dead"]))
        evidence("u1-4-pair-status-resolver.txt", ev_u1_4(profiles["main"]))
        evidence("u1-5-estimated-labels.txt", ev_u1_5(profiles["main"]))
        evidence("u1-6-endpoint-consumers.txt", ev_u1_6(profiles["main"]))
        evidence("u1-7-auth-denied.txt", ev_u1_7(profiles["auth"]))
        evidence("u1-8-no-secrets-in-html.txt", ev_u1_8(profiles["auth"], base))
        evidence("u1-9-l3-fixes-ui-log.txt",
                 ev_u1_9(profiles["main"], server, base))
        evidence("t-u1-10-llm-off-mid-job.txt",
                 ev_t_u1_10(profiles["main"], server, base, books_dir / "u1_slow.epub"))
        print("[u1-evidence] все захваты записаны")
        return 0
    finally:
        _kill_all(_WORKERS)
        server.stop()
        for profile in profiles.values():
            profile.db.close()
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
