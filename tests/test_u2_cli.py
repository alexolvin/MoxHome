"""U2 (ТЗ_04 §U2): CLI ``epubtranslator`` — тесты T-U2-1..T-U2-4.

Принципы (тот же стандарт, что у предыдущих стадий):
- CLI — реальный subprocess (``python -m app.cli --config … --env …``),
  воркер — реальный subprocess ``--loop`` (паттерн D69: --once выходит
  после NEEDS_CONFIRMATION), LLM — MockLLMServer;
- U2-1: собственной бизнес-логики в CLI нет — проверка по вызываемым
  сервисам (статически: точный импорт-allowlist + вызовы; поведенчески:
  CLI-вывод == API-данные по тем же сущностям);
- U2-2: 8 сценариев кодов выхода — каждый замерен реальным rc процесса;
- U2-3: задание из CLI видно в UI (API) и наоборот — по тому же БД;
- T-U2-4: validate повреждённого EPUB — ненулевой код + внятное сообщение,
  валидная книга — 0 + версия EPUBCheck.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.web.auth import csrf_token

ROOT = Path(__file__).resolve().parents[1]
_TESTS_DIR = str(Path(__file__).resolve().parent)
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from mock_llm_server import MockLLMServer  # noqa: E402
from test_l3_glossary_fix import _kill_all  # noqa: E402
from test_u1_web import (  # noqa: E402
    AUTH_SECRET,
    U1Env,
    U1Profile,
    _auth_header,
    _create_job,
    _make_u1_storage,
    _synth_u1_slow_book,
    _synth_u1_tiny_book,
    _upload_book,
)

#: Точный allowlist импортов app в CLI (U2-1: собственной бизнес-логики нет —
#: CLI ходит только в сервисный слой и представления). Любое новое
#: ``from app.… import …`` вне списка — падение теста.
ALLOWED_APP_IMPORTS: dict[str, frozenset[str]] = {
    "app.adapters.epub_engine.epubcheck": frozenset({"run_epubcheck"}),
    "app.core.config": frozenset({"Settings", "load_settings"}),
    "app.core.errors": frozenset(
        {"AppError", "ConfigurationError", "InputValidationError", "JobStateError"}
    ),
    "app.domain.job": frozenset({"TERMINAL_STATES", "Job", "JobStatus"}),
    "app.epub.reader": frozenset({"read_epub"}),
    "app.languages.resolver": frozenset({"PairResolver", "PairStatus"}),
    "app.learning": frozenset({"card_export"}),
    "app.services.analyze": frozenset({"scan_book"}),
    "app.services.jobs": frozenset({"create_job"}),
    "app.storage.database": frozenset({"Database"}),
    "app.storage.jobs_repo": frozenset({"JobRepository"}),
    "app.web": frozenset({"views"}),
}


@pytest.fixture(scope="module")
def u2_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[U1Env]:
    """Окружение U2: изолированный storage, mock LLM, две книги (паттерн U1)."""
    base = tmp_path_factory.mktemp("u2_module")
    books_dir = base / "books"
    books_dir.mkdir()
    _synth_u1_slow_book(books_dir / "u2_slow.epub")
    _synth_u1_tiny_book(books_dir / "u2_tiny.epub")
    server = MockLLMServer()
    server.start()
    env = U1Env(
        server=server,
        base=base,
        books={"slow": books_dir / "u2_slow.epub", "tiny": books_dir / "u2_tiny.epub"},
    )
    try:
        # main: U3 3.9 (fail closed) — у профиля есть SECRET, клиент шлёт
        # Basic + X-U3-CSRF по умолчанию (мутации через HTTP — только так).
        main_settings, main_db, main_cfg, main_env = _make_u1_storage(
            base,
            "u2_main",
            server.url,
            env_extra=f"SERVER_AUTH_SECRET={AUTH_SECRET}\n",
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
        # dead: тот же секрет — confirm (POST) в NEEDS_CONFIRMATION проходит;
        # LLM-down поведение от секрета не зависит (web-auth vs LLM — разное).
        dead_settings, dead_db, dead_cfg, dead_env = _make_u1_storage(
            base,
            "u2_dead",
            "http://127.0.0.1:1/v1",
            env_extra=f"SERVER_AUTH_SECRET={AUTH_SECRET}\n",
        )
        env.add_profile(
            "dead",
            dead_settings,
            dead_db,
            dead_cfg,
            dead_env,
            client_headers={
                **_auth_header(),
                "X-U3-CSRF": csrf_token(dead_settings, "any-user"),
            },
        )
        yield env
    finally:
        env.close()
        server.stop()


def _cli(cfg: Path, env_file: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Один вызов CLI как отдельного процесса (то, что делает пользователь)."""
    cmd = [
        sys.executable,
        "-m",
        "app.cli",
        "--config",
        str(cfg),
        "--env",
        str(env_file),
        *args,
    ]
    return subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=600, check=False
    )


def _start_loop_worker(
    cfg: Path, env_file: Path, log_path: Path
) -> subprocess.Popen[bytes]:
    """Воркер --loop (D69): переживает NEEDS_CONFIRMATION."""
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
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=30)


def _cli_translate_wait(
    env: U1Env,
    profile_name: str,
    book: Path,
    log_path: Path,
    *,
    wait_seconds: float,
    extra_args: tuple[str, ...] = (),
) -> tuple[int, str, str | None]:
    """CLI ``translate … --wait`` в subprocess; NEEDS_CONFIRMATION
    подтверждается через API (человек в контуре — это шаг UI, не CLI).

    Возврат: (rc, лог CLI, id задания). ``-u``: stdout без буферизации —
    «задание: <id>» видно в логе до завершения (подтверждение идёт live).
    """
    profile = env.profiles[profile_name]
    cmd = [
        sys.executable,
        "-u",
        "-m",
        "app.cli",
        "--config",
        str(profile.cfg),
        "--env",
        str(profile.env_file),
        "translate",
        str(book),
        "--source",
        "en",
        "--target",
        "ru",
        "--mode",
        "bilingual",
        *extra_args,
        "--wait",
        str(wait_seconds),
    ]
    handle = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
    handle.close()
    job_id: str | None = None
    confirmed = False
    deadline = time.monotonic() + wait_seconds + 120
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        text = log_path.read_text(encoding="utf-8")
        match = re.search(r"задание: (\S+)", text)
        if match is not None:
            job_id = match.group(1)
            if not confirmed:
                facts = profile.client.get(f"/api/jobs/{job_id}").json()
                if facts.get("status") == "NEEDS_CONFIRMATION":
                    detected = (facts.get("language_detection") or {}).get("detected")
                    profile.client.post(
                        f"/api/jobs/{job_id}/confirm",
                        json={"source_language": detected or "en"},
                    )
                    confirmed = True
        time.sleep(0.3)
    code = proc.wait(timeout=120)
    log = log_path.read_text(encoding="utf-8")
    if job_id is None:  # процесс завершился раньше первого чтения лога
        final_match = re.search(r"задание: (\S+)", log)
        job_id = final_match.group(1) if final_match is not None else None
    return code, log, job_id


# ------------------------------------------------------------------ T-U2-1


def test_u2_1_cli_reuses_api_services(u2_env: U1Env) -> None:
    """U2-1: дублирование бизнес-логики CLI/API = 0 — проверка по
    вызываемым сервисам: (1) статика cli.py, (2) вызовы тех же сервисов,
    что API, (3) поведенческий паритет вывода на тех же сущностях."""
    src = (ROOT / "app" / "cli.py").read_text(encoding="utf-8")
    # Однострочный вид многострочных `from app.… import (…)`:
    src = re.sub(
        r"(from\s+(?:app(?:\.\w+)*))\s+import\s*\(([^)]*)\)",
        lambda m: f"{m.group(1)} import " + " ".join(m.group(2).split()),
        src,
        flags=re.S,
    )

    # Точная сверка всех импортов app: (модуль, имена) — только allowlist.
    violations: list[str] = []
    for line in src.splitlines():
        from_match = re.match(r"^from\s+((?:app(?:\.\w+)*))\s+import\s+(.+)$", line)
        if from_match is not None:
            module, raw_names = from_match.group(1), from_match.group(2)
            names = frozenset(
                n.strip() for n in raw_names.strip("()").split(",") if n.strip()
            )
            if ALLOWED_APP_IMPORTS.get(module) != names:
                violations.append(f"{module} {sorted(names)}")
            continue
        bare_match = re.match(r"^import\s+app(?:\.\w+)*", line)
        if bare_match is not None:
            violations.append(bare_match.group(0))
    assert not violations, f"CLI импортирует вне сервисного слоя: {violations}"
    for forbidden in ("sqlite3", "httpx", "fastapi", "uvicorn"):
        assert not re.search(
            rf"^(?:from|import)\s+{forbidden}\b", src, re.M
        ), f"CLI обращается напрямую к {forbidden}"

    # Те же сервисы, что API (U2-1 — по вызовам).
    for call in (
        "scan_book(",  # == POST /api/books (app.services.analyze)
        "create_job(",  # == POST /api/jobs (app.services.jobs)
        "views.job_facts(",  # == GET /api/jobs/{id}
        "views.progress_facts(",  # == GET /api/jobs (list)
        "views.languages_view(",  # == GET /api/languages
        "repo.request_resume(",  # == POST /api/jobs/{id}/resume
        "repo.request_cancel(",  # == POST /api/jobs/{id}/cancel
        "read_epub(",  # == стадия validating (E2.5)
        "run_epubcheck(",  # == стадия validating_output (E5.7)
    ):
        assert call in src, f"CLI не вызывает сервис: {call}"

    # Поведенческий паритет: CLI analyze == мета API загрузки (scan_book).
    profile = u2_env.profiles["main"]
    client = profile.client
    book = u2_env.books["tiny"]
    name = _upload_book(client, book)
    meta = next(b for b in client.get("/api/books").json() if b["name"] == name)
    res = _cli(profile.cfg, profile.env_file, "analyze", str(book))
    assert res.returncode == 0, res.stderr
    assert f"заголовок: {meta['title']}" in res.stdout
    assert f"dc:language: {meta['dc_language'] or '—'}" in res.stdout
    assert f"определён: {meta['detected']}" in res.stdout
    for code, prob in (meta.get("probabilities") or {}).items():
        assert f"{code} {prob}" in res.stdout, (code, prob)
    assert f"решение L0.5: {meta['decision']}" in res.stdout

    # Поведенческий паритет: CLI languages == GET /api/languages (languages_view).
    api = client.get("/api/languages").json()
    res = _cli(profile.cfg, profile.env_file, "languages")
    assert res.returncode == 0, res.stderr
    assert res.stdout.count("spacy:") == len(api["languages"])
    for lang in api["languages"]:
        assert re.search(
            rf"^\s+{re.escape(lang['code'])}\s+{re.escape(lang['name'])}",
            res.stdout,
            re.M,
        ), lang["code"]
    assert res.stdout.count("→") == len(api["pairs"])


# ------------------------------------------------------------------ T-U2-2


@dataclass
class U2Ctx:
    env: U1Env
    workers: list[subprocess.Popen[bytes]] = field(default_factory=list)
    results: list[tuple[str, bool, str]] = field(default_factory=list)
    not_zip: Path | None = None
    done_job_id: str | None = None

    @property
    def main(self) -> U1Profile:
        return self.env.profiles["main"]

    @property
    def dead(self) -> U1Profile:
        return self.env.profiles["dead"]

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.results.append((name, ok, detail))


def _scen_5_broken_config(ctx: U2Ctx) -> None:
    res = _cli(
        ctx.env.base / "u2_no_such_config.yaml",
        ctx.main.env_file,
        "translate",
        str(ctx.env.books["tiny"]),
        "--source",
        "en",
        "--target",
        "ru",
        "--mode",
        "bilingual",
    )
    ctx.record(
        "5 translate broken config -> 2",
        res.returncode == 2 and "конфигурации" in res.stderr,
        f"rc={res.returncode} stderr={res.stderr.strip()!r}",
    )


def _scen_6_missing_file(ctx: U2Ctx) -> None:
    res = _cli(
        ctx.main.cfg,
        ctx.main.env_file,
        "translate",
        str(ctx.env.base / "no_such_book.epub"),
        "--source",
        "en",
        "--target",
        "ru",
        "--mode",
        "bilingual",
    )
    ctx.record(
        "6 translate missing file -> 3",
        res.returncode == 3 and "не найден" in res.stderr,
        f"rc={res.returncode} stderr={res.stderr.strip()!r}",
    )


def _scen_7_non_zip(ctx: U2Ctx) -> None:
    not_zip = ctx.env.base / "u2_not_zip.epub"
    not_zip.write_text("это не EPUB и не ZIP", encoding="utf-8")
    ctx.not_zip = not_zip
    res = _cli(
        ctx.main.cfg,
        ctx.main.env_file,
        "translate",
        str(not_zip),
        "--source",
        "en",
        "--target",
        "ru",
        "--mode",
        "bilingual",
    )
    ctx.record(
        "7 translate non-ZIP -> 3",
        res.returncode == 3 and "ZIP" in res.stderr,
        f"rc={res.returncode} stderr={res.stderr.strip()!r}",
    )


def _scen_8_validate_broken(ctx: U2Ctx) -> None:
    res = _cli(ctx.main.cfg, ctx.main.env_file, "validate", str(ctx.not_zip))
    ctx.record(
        "8 validate corrupted EPUB -> 3",
        res.returncode == 3 and "ZIP" in res.stderr,
        f"rc={res.returncode} stderr={res.stderr.strip()!r}",
    )


def _scen_1_translate_valid(ctx: U2Ctx) -> None:
    # Профиль dead: LLM для создания задания не нужен — изоляция от (3).
    res = _cli(
        ctx.dead.cfg,
        ctx.dead.env_file,
        "translate",
        str(ctx.env.books["tiny"]),
        "--source",
        "en",
        "--target",
        "ru",
        "--mode",
        "bilingual",
    )
    created = re.search(r"задание: (\S+)", res.stdout)
    ctx.record(
        "1 translate valid -> 0",
        res.returncode == 0 and created is not None,
        f"rc={res.returncode} stdout={res.stdout.strip()!r}",
    )


def _scen_3_dead_wait(ctx: U2Ctx) -> None:
    worker = _start_loop_worker(ctx.dead.cfg, ctx.dead.env_file, ctx.env.base / "w3.log")
    ctx.workers.append(worker)
    try:
        code, log, job_id = _cli_translate_wait(
            ctx.env, "dead", ctx.env.books["tiny"], ctx.env.base / "c3.log",
            wait_seconds=180,
        )
        ctx.record(
            "3 translate --wait dead LLM -> 1",
            code == 1 and "FAILED" in log and "причина:" in log and job_id is not None,
            f"rc={code} tail={log.strip().splitlines()[-2:]}",
        )
    finally:
        _stop_worker(worker)


def _scen_2_completed(ctx: U2Ctx) -> None:
    worker = _start_loop_worker(ctx.main.cfg, ctx.main.env_file, ctx.env.base / "w2.log")
    ctx.workers.append(worker)
    try:
        code, log, done_id = _cli_translate_wait(
            ctx.env, "main", ctx.env.books["tiny"], ctx.env.base / "c2.log",
            wait_seconds=300, extra_args=("--level", "Learning"),
        )
        assert code == 0 and (
            "исход: COMPLETED" in log or "исход: COMPLETED_WITH_WARNINGS" in log
        ), f"сценарий 2: rc={code} tail={log.strip().splitlines()[-2:]}"
        ctx.done_job_id = done_id
        res = _cli(ctx.main.cfg, ctx.main.env_file, "job", "status", done_id)
        ctx.record(
            "2 job status COMPLETED -> 0",
            res.returncode == 0 and "статус: COMPLETED" in res.stdout,
            f"rc={res.returncode} head={res.stdout.strip().splitlines()[:2]}",
        )
        # export cards на том же задании (гейт состояния: _OK_STATES)
        res = _cli(
            ctx.main.cfg, ctx.main.env_file, "export", "cards", done_id, "--format", "csv"
        )
        jdir = ctx.main.settings.job_dir(done_id)
        has_cards = (jdir / "cards.csv").is_file()
        if has_cards:
            ok = res.returncode == 0 and res.stdout.strip() != ""
        else:  # уровень не дал карточек — честный отказ состояния
            ok = res.returncode == 1 and "карточек" in res.stderr
        ctx.record(
            "2b export cards (gate _OK_STATES)",
            ok,
            f"rc={res.returncode} cards.csv={has_cards} "
            f"stderr={res.stderr.strip()[:120]!r}",
        )
    finally:
        _stop_worker(worker)


def _scen_4_cancelled(ctx: U2Ctx) -> None:
    ctx.env.server.set_delay(0.3)
    try:
        res = _cli(
            ctx.main.cfg,
            ctx.main.env_file,
            "translate",
            str(ctx.env.books["slow"]),
            "--source",
            "en",
            "--target",
            "ru",
            "--mode",
            "bilingual",
        )
        cancel_id = re.search(r"задание: (\S+)", res.stdout).group(1)
        worker = _start_loop_worker(
            ctx.main.cfg, ctx.main.env_file, ctx.env.base / "w4.log"
        )
        ctx.workers.append(worker)
        try:
            status: Any = None
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                status = ctx.main.client.get(f"/api/jobs/{cancel_id}").json()["status"]
                if status == "TRANSLATING":
                    break
                time.sleep(0.2)
            assert status == "TRANSLATING", f"не застали TRANSLATING: {status}"
            res = _cli(ctx.main.cfg, ctx.main.env_file, "job", "cancel", cancel_id)
            assert res.returncode == 0, res.stderr
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                status = ctx.main.client.get(f"/api/jobs/{cancel_id}").json()["status"]
                if status == "CANCELLED":
                    break
                time.sleep(0.2)
            assert status == "CANCELLED", f"не дошли до CANCELLED: {status}"
            res = _cli(ctx.main.cfg, ctx.main.env_file, "job", "status", cancel_id)
            ctx.record(
                "4 job status CANCELLED -> 1",
                res.returncode == 1 and "статус: CANCELLED" in res.stdout,
                f"rc={res.returncode} stdout={res.stdout.strip().splitlines()[:2]!r}",
            )
        finally:
            _stop_worker(worker)
    finally:
        ctx.env.server.set_delay(0.0)


def test_u2_2_exit_codes_eight_scenarios(u2_env: U1Env) -> None:
    """U2-2: код выхода соответствует исходу задания — 100% (8/8) сценариев.

    1 translate валидное → 0; 2 job status COMPLETED → 0;
    3 translate --wait при LLM down → 1 (FAILED, причина as-is);
    4 job status CANCELLED → 1; 5 translate сломанный конфиг → 2;
    6 translate несуществующий файл → 3; 7 translate не-ZIP → 3;
    8 validate повреждённый EPUB → 3.
    """
    ctx = U2Ctx(env=u2_env)
    try:
        _scen_5_broken_config(ctx)
        _scen_6_missing_file(ctx)
        _scen_7_non_zip(ctx)
        _scen_8_validate_broken(ctx)
        _scen_1_translate_valid(ctx)
        _scen_3_dead_wait(ctx)
        _scen_2_completed(ctx)
        _scen_4_cancelled(ctx)
    finally:
        _kill_all(ctx.workers)
    bad = [(name, detail) for name, ok, detail in ctx.results if not ok]
    assert len(ctx.results) >= 8, f"мало сценариев: {len(ctx.results)}"
    assert not bad, f"сценарии U2-2 не сошлись: {bad}"


# ------------------------------------------------------------------ T-U2-3


def test_u2_3_cross_visibility_cli_api(u2_env: U1Env) -> None:
    """U2-3: задание из CLI видно в UI (API) и наоборот — 100%.

    Одно БД-хранилище: CLI пишет (create_job/JobRepository), API читает
    (GET /api/jobs, GET /api/jobs/{id}); обратное — API пишет, CLI читает
    (job list/job status)."""
    profile = u2_env.profiles["main"]
    client = profile.client

    # CLI → API: translate из CLI, видимость в GET /api/jobs и /api/jobs/{id}
    res = _cli(
        profile.cfg,
        profile.env_file,
        "translate",
        str(u2_env.books["tiny"]),
        "--source",
        "en",
        "--target",
        "ru",
        "--mode",
        "bilingual",
    )
    assert res.returncode == 0, res.stderr
    cli_job_id = re.search(r"задание: (\S+)", res.stdout).group(1)
    rows = client.get("/api/jobs").json()
    row = next((r for r in rows if r["id"] == cli_job_id), None)
    assert row is not None, f"задание из CLI не в GET /api/jobs: {cli_job_id}"
    assert row["target_language"] == "ru" and row["mode"] == "bilingual", row
    facts = client.get(f"/api/jobs/{cli_job_id}").json()
    assert facts["id"] == cli_job_id
    assert facts["status"] == row["status"]

    # API → CLI: задание через API, видимость в job list/job status
    name = _upload_book(client, u2_env.books["slow"])
    api_job_id = _create_job(client, name)
    res = _cli(profile.cfg, profile.env_file, "job", "list")
    assert res.returncode == 0, res.stderr
    assert api_job_id in res.stdout, "задание из API не в CLI job list"
    res = _cli(profile.cfg, profile.env_file, "job", "status", api_job_id)
    assert res.returncode == 0, res.stderr
    api_status = client.get(f"/api/jobs/{api_job_id}").json()["status"]
    assert f"статус: {api_status}" in res.stdout, (
        f"статус CLI != API: {res.stdout!r} vs {api_status}"
    )

    # Бонус (гейт export cards): задание из CLI ещё CREATED — экспорт отказан
    res = _cli(
        profile.cfg, profile.env_file, "export", "cards", cli_job_id, "--format", "csv"
    )
    assert res.returncode == 1, res.stdout
    assert "карточки не готовы" in res.stderr, res.stderr


# ------------------------------------------------------------------ T-U2-4


def test_u2_4_validate_corrupted_epub(u2_env: U1Env, tmp_path: Path) -> None:
    """T-U2-4: validate повреждённого EPUB — ненулевой код выхода +
    внятное сообщение; валидная книга — 0 + версия EPUBCheck."""
    profile = u2_env.profiles["main"]
    broken = tmp_path / "broken.epub"
    broken.write_text("это текст, а не EPUB", encoding="utf-8")

    res = _cli(profile.cfg, profile.env_file, "validate", str(broken))
    assert res.returncode == 3, (
        f"ожидался код 3 (валидация входа), получено {res.returncode}: "
        f"{res.stderr!r}"
    )
    assert "ZIP" in res.stderr, f"нет внятного сообщения: {res.stderr!r}"

    res = _cli(profile.cfg, profile.env_file, "validate", str(u2_env.books["tiny"]))
    assert res.returncode == 0, res.stderr
    assert "EPUBCheck" in res.stdout and "0 FATAL, 0 ERROR" in res.stdout
