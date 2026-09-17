"""U2 (ТЗ_04 §U2): verbatim-захваты критериев CONTROL U2.

Запуск: python tests/u2_evidence.py
Захваты: docs/CONTROL/evidence/U2/u2-1…u2-3 + t-u2-4 (verbatim).

Сценарии (mock-сервер D3 для LLM; реальные spaCy/EPUBCheck/SQLite/FastAPI;
CLI и воркер — реальные процессы, ``python -m app.cli`` / ``--loop``):
  u2-1     дублирования бизнес-логики CLI/API = 0: таблица команда → сервис,
        полный список импортов cli.py, паритет вывода (CLI == API)
  u2-2     8 сценариев кодов выхода (rc + вывод verbatim) — 100%
  u2-3     задание CLI → UI и UI → CLI (строки verbatim) — 100%
  t-u2-4   validate повреждённого EPUB: код + внятное сообщение;
           валидная книга — 0 + версия EPUBCheck
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "probe"))

from mock_llm_server import MockLLMServer  # noqa: E402
from test_l3_glossary_fix import _kill_all  # noqa: E402
from test_u1_web import (  # noqa: E402
    AUTH_SECRET,
    U1Profile,
    _auth_header,
    _create_job,
    _make_u1_storage,
    _synth_u1_slow_book,
    _synth_u1_tiny_book,
    _upload_book,
)

from app.storage.jobs_repo import JobRepository  # noqa: E402
from app.web.app import create_app  # noqa: E402
from app.web.auth import csrf_token  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "U2"
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
VERSIONS = f"python={sys.version.split()[0]} spacy={pkg_version('spacy')}"

_WORKERS: list[subprocess.Popen[bytes]] = []
_BOOKS: dict[str, Path] = {}


def _profile(base: Path, name: str, url: str, *, secret: bool = False) -> U1Profile:
    """Профиль evidence. ``secret=True`` — U3 3.9 (fail closed): у стенда
    есть SERVER_AUTH_SECRET, клиент шлёт Basic + X-U3-CSRF по умолчанию."""
    env_extra = f"SERVER_AUTH_SECRET={AUTH_SECRET}\n" if secret else ""
    settings, db, cfg, env_file = _make_u1_storage(
        base, f"u2ev_{name}", url, env_extra=env_extra
    )
    app = create_app(settings, db=db)
    client_headers = None
    if secret:
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


def _start_worker_loop(cfg: Path, env_file: Path, log_path: Path) -> subprocess.Popen[bytes]:
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


def _cli(
    profile: U1Profile, *args: str, cfg: Path | None = None
) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        "-m",
        "app.cli",
        "--config",
        str(cfg or profile.cfg),
        "--env",
        str(profile.env_file),
        *args,
    ]
    return subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=600, check=False
    )


def _cli_cmdline(profile: U1Profile, *args: str, cfg: Path | None = None) -> str:
    return (
        "python -m app.cli --config <cfg> --env <env> "
        + " ".join(f'"{a}"' if " " in a else a for a in args)
    )


def _cli_translate_wait(
    profile: U1Profile,
    book: Path,
    log_path: Path,
    *,
    wait_seconds: float,
    extra_args: tuple[str, ...] = (),
) -> tuple[int, str, str | None]:
    """CLI ``translate … --wait``; NEEDS_CONFIRMATION — подтверждение
    через API (человек в контуре — шаг UI). ``-u`` — без буферизации
    stdout («задание: <id>» видно live)."""
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


def evidence(name: str, content: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print(f"  [evidence] {path.relative_to(ROOT)}")


def header() -> str:
    return f"дата: {STAMP}\nверсии: {VERSIONS}\n\n"


# ------------------------------------------------------------------ u2-1


def ev_u2_1(main: U1Profile) -> str:
    lines = [
        header(),
        "K: U2-1 — дублирование бизнес-логики CLI/API = 0 (проверка по сервисам)",
        "",
        "1. Таблица «команда → сервис» (docstring app/cli.py, verbatim):",
        "",
    ]
    src = (ROOT / "app" / "cli.py").read_text(encoding="utf-8")
    doc = src.split('"""')[1]
    lines += [f"  {line}" for line in doc.strip().splitlines()]
    lines.append("")
    lines.append("2. Все импорты app.* в app/cli.py (verbatim; только сервисный слой):")
    for line in src.splitlines():
        if re.match(r"^(?:from|import)\s+app", line):
            lines.append(f"  {line}")
    lines.append("")
    lines.append("3. Паритет «CLI analyze» и «POST /api/books» (одна книга, один scan_book):")

    name = _upload_book(main.client, _BOOKS["tiny"])
    meta = next(b for b in main.client.get("/api/books").json() if b["name"] == name)
    res = _cli(main, "analyze", str(_BOOKS["tiny"]))
    cmd = _cli_cmdline(main, "analyze", _BOOKS["tiny"].name)
    lines.append(f"  $ {cmd}  →  rc={res.returncode}")
    lines += [f"  | {line}" for line in res.stdout.splitlines()]
    lines.append("  API /api/books (та же книга, verbatim ключевые поля):")
    lines.append(
        "  | "
        + json.dumps(
            {
                "title": meta["title"],
                "dc_language": meta["dc_language"],
                "detected": meta["detected"],
                "confidence": meta["confidence"],
                "decision": meta["decision"],
                "probabilities": meta["probabilities"],
            },
            ensure_ascii=False,
        )
    )
    parity = [
        (f"заголовок: {meta['title']}" in res.stdout, "заголовок"),
        (f"определён: {meta['detected']}" in res.stdout, "определён (detected)"),
        (f"решение L0.5: {meta['decision']}" in res.stdout, "решение L0.5"),
        (
            all(f"{k} {v}" in res.stdout for k, v in (meta["probabilities"] or {}).items()),
            "вероятности (top-3)",
        ),
    ]
    lines.append("  сверка (строка CLI == значение API):")
    for ok, what in parity:
        lines.append(f"    {what}: {'OK' if ok else 'РАСХОЖДЕНИЕ'}")

    lines.append("")
    lines.append("4. Паритет «CLI languages» и «GET /api/languages» (languages_view):")
    api = main.client.get("/api/languages").json()
    res = _cli(main, "languages")
    n_cli = res.stdout.count("spacy:")
    n_pairs_cli = res.stdout.count("→")
    lines.append(
        f"  языков: CLI {n_cli} / API {len(api['languages'])}; "
        f"пар: CLI {n_pairs_cli} / API {len(api['pairs'])}"
    )
    codes_ok = all(
        re.search(rf"^\s+{re.escape(item['code'])}\s", res.stdout, re.M)
        for item in api["languages"]
    )
    lines.append(
        "  все коды языков API присутствуют в CLI: "
        f"{'OK' if codes_ok else 'РАСХОЖДЕНИЕ'}"
    )

    all_ok = (
        res.returncode == 0
        and all(ok for ok, _ in parity)
        and n_cli == len(api["languages"])
        and n_pairs_cli == len(api["pairs"])
        and codes_ok
    )
    verdict = "PASS" if all_ok else "FAIL"
    lines.append(f"\nВЕРДИКТ: U2-1 (0 дублирования, паритет вывода) — {verdict}")
    return "\n".join(lines)


# ------------------------------------------------------------------ u2-2


@dataclass
class U2EvCtx:
    main: U1Profile
    dead: U1Profile
    base: Path
    server: MockLLMServer
    lines: list[str] = field(default_factory=list)
    results: list[tuple[str, bool]] = field(default_factory=list)

    def scen(
        self,
        no: str,
        title: str,
        ok: bool,
        cmd: str,
        res: subprocess.CompletedProcess[str],
    ) -> None:
        self.results.append((f"{no} {title}", ok))
        self.lines.append(f"--- сценарий {no}: {title}")
        self.lines.append(f"  $ {cmd}")
        self.lines.append(f"  rc = {res.returncode}")
        out = (res.stderr or res.stdout).strip().splitlines()
        for out_line in out[:4]:
            self.lines.append(f"  | {out_line}")
        self.lines.append("")


def _ev_scen_5(ctx: U2EvCtx) -> None:
    res = _cli(
        ctx.main,
        "translate",
        str(_BOOKS["tiny"]),
        "--source", "en", "--target", "ru", "--mode", "bilingual",
        cfg=ctx.base / "u2ev_no_such_config.yaml",
    )
    ctx.scen(
        "5", "translate со сломанным конфигом → 2",
        res.returncode == 2 and "конфигурации" in res.stderr,
        _cli_cmdline(
            ctx.main, "translate", "u2_tiny.epub", "--source", "en",
            "--target", "ru", "--mode", "bilingual",
        ) + "  [cfg не существует]",
        res,
    )


def _ev_scen_6(ctx: U2EvCtx) -> None:
    res = _cli(
        ctx.main, "translate", str(ctx.base / "no_such_book.epub"),
        "--source", "en", "--target", "ru", "--mode", "bilingual",
    )
    ctx.scen(
        "6", "translate несуществующего файла → 3",
        res.returncode == 3 and "не найден" in res.stderr,
        _cli_cmdline(
            ctx.main, "translate", "no_such_book.epub", "--source", "en",
            "--target", "ru", "--mode", "bilingual",
        ),
        res,
    )


def _ev_scen_7(ctx: U2EvCtx) -> None:
    not_zip = ctx.base / "u2ev_not_zip.epub"
    not_zip.write_text("это не EPUB и не ZIP", encoding="utf-8")
    res = _cli(
        ctx.main, "translate", str(not_zip),
        "--source", "en", "--target", "ru", "--mode", "bilingual",
    )
    ctx.scen(
        "7", "translate не-ZIP → 3",
        res.returncode == 3 and "ZIP" in res.stderr,
        _cli_cmdline(
            ctx.main, "translate", "u2ev_not_zip.epub", "--source", "en",
            "--target", "ru", "--mode", "bilingual",
        ),
        res,
    )


def _ev_scen_8(ctx: U2EvCtx) -> None:
    res = _cli(ctx.main, "validate", str(ctx.base / "u2ev_not_zip.epub"))
    ctx.scen(
        "8", "validate повреждённого EPUB → 3",
        res.returncode == 3 and "ZIP" in res.stderr,
        _cli_cmdline(ctx.main, "validate", "u2ev_not_zip.epub"),
        res,
    )


def _ev_scen_1(ctx: U2EvCtx) -> None:
    res = _cli(
        ctx.dead, "translate", str(_BOOKS["tiny"]),
        "--source", "en", "--target", "ru", "--mode", "bilingual",
    )
    created = re.search(r"задание: (\S+)", res.stdout)
    ctx.scen(
        "1", "translate валидное → 0",
        res.returncode == 0 and created is not None,
        _cli_cmdline(
            ctx.dead, "translate", "u2_tiny.epub", "--source", "en",
            "--target", "ru", "--mode", "bilingual",
        ),
        res,
    )


def _ev_scen_3(ctx: U2EvCtx) -> None:
    worker = _start_worker_loop(ctx.dead.cfg, ctx.dead.env_file, ctx.base / "u2ev_w3.log")
    _WORKERS.append(worker)
    try:
        code, log, _job_id = _cli_translate_wait(
            ctx.dead, _BOOKS["tiny"], ctx.base / "u2ev_c3.log", wait_seconds=180
        )
        ctx.scen(
            "3", "translate --wait при LLM down → 1 (FAILED, причина as-is)",
            code == 1 and "FAILED" in log and "причина:" in log,
            _cli_cmdline(
                ctx.dead, "translate", "u2_tiny.epub", "--source", "en",
                "--target", "ru", "--mode", "bilingual", "--wait", "180",
            ) + "  [профиль: LLM base_url = 127.0.0.1:1 (dead)]",
            subprocess.CompletedProcess([], code, stdout=log, stderr=""),
        )
    finally:
        _stop_worker(worker)


def _ev_scen_2(ctx: U2EvCtx) -> None:
    worker = _start_worker_loop(ctx.main.cfg, ctx.main.env_file, ctx.base / "u2ev_w2.log")
    _WORKERS.append(worker)
    try:
        code, log, done_id = _cli_translate_wait(
            ctx.main, _BOOKS["tiny"], ctx.base / "u2ev_c2.log",
            wait_seconds=300, extra_args=("--level", "Learning"),
        )
        ctx.scen(
            "2", "translate --wait → COMPLETED → 0",
            code == 0
            and ("исход: COMPLETED" in log or "исход: COMPLETED_WITH_WARNINGS" in log),
            _cli_cmdline(
                ctx.main, "translate", "u2_tiny.epub", "--source", "en",
                "--target", "ru", "--mode", "bilingual", "--level", "Learning",
                "--wait", "300",
            ),
            subprocess.CompletedProcess([], code, stdout=log, stderr=""),
        )
        assert done_id is not None
        res = _cli(ctx.main, "job", "status", done_id)
        ctx.scen(
            "2b", "job status COMPLETED → 0",
            res.returncode == 0 and "статус: COMPLETED" in res.stdout,
            _cli_cmdline(ctx.main, "job", "status", done_id),
            res,
        )
    finally:
        _stop_worker(worker)


def _wait_cancelled(
    main: U1Profile, cancel_id: str
) -> tuple[str, subprocess.CompletedProcess[str]]:
    """Ожидание TRANSLATING → job cancel → CANCELLED (кооперативная отмена, E3.8)."""
    status: Any = None
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        status = main.client.get(f"/api/jobs/{cancel_id}").json()["status"]
        if status == "TRANSLATING":
            break
        time.sleep(0.2)
    assert status == "TRANSLATING", status
    cancel_res = _cli(main, "job", "cancel", cancel_id)
    assert cancel_res.returncode == 0, cancel_res.stderr
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        status = main.client.get(f"/api/jobs/{cancel_id}").json()["status"]
        if status == "CANCELLED":
            break
        time.sleep(0.2)
    assert status == "CANCELLED", status
    return status, cancel_res


def _ev_scen_4(ctx: U2EvCtx) -> None:
    ctx.server.set_delay(0.3)
    try:
        res = _cli(
            ctx.main, "translate", str(_BOOKS["slow"]),
            "--source", "en", "--target", "ru", "--mode", "bilingual",
        )
        cancel_id = re.search(r"задание: (\S+)", res.stdout).group(1)
        worker = _start_worker_loop(ctx.main.cfg, ctx.main.env_file, ctx.base / "u2ev_w4.log")
        _WORKERS.append(worker)
        try:
            _status, cancel_res = _wait_cancelled(ctx.main, cancel_id)
            res = _cli(ctx.main, "job", "status", cancel_id)
            ctx.scen(
                "4", "job status CANCELLED → 1",
                res.returncode == 1 and "статус: CANCELLED" in res.stdout,
                _cli_cmdline(ctx.main, "job", "status", cancel_id)
                + f"  [job cancel: rc={cancel_res.returncode}]",
                res,
            )
        finally:
            _stop_worker(worker)
    finally:
        ctx.server.set_delay(0.0)


def ev_u2_2(main: U1Profile, dead: U1Profile, base: Path, server: MockLLMServer) -> str:
    ctx = U2EvCtx(main=main, dead=dead, base=base, server=server)
    ctx.lines += [
        header(),
        "K: U2-2 — код выхода соответствует исходу задания (8 сценариев, verbatim)",
        "",
    ]
    _ev_scen_5(ctx)
    _ev_scen_6(ctx)
    _ev_scen_7(ctx)
    _ev_scen_8(ctx)
    _ev_scen_1(ctx)
    _ev_scen_3(ctx)
    _ev_scen_2(ctx)
    _ev_scen_4(ctx)
    bad = [name for name, ok in ctx.results if not ok]
    ctx.lines.append(f"сценариев: {len(ctx.results)}/8 (+2b)")
    ctx.lines.append(f"не сошлись: {bad if bad else 'нет (0)'}")
    ok = not bad and len(ctx.results) >= 8
    verdict = "PASS" if ok else "FAIL"
    ctx.lines.append(f"\nВЕРДИКТ: U2-2 (8/8 сценариев кодов выхода) — {verdict}")
    return "\n".join(ctx.lines)


# ------------------------------------------------------------------ u2-3


def ev_u2_3(main: U1Profile) -> str:
    lines = [
        header(),
        "K: U2-3 — задание из CLI видно в UI (API) и наоборот (100%)",
        "",
    ]
    # CLI → UI
    res = _cli(
        main, "translate", str(_BOOKS["tiny"]),
        "--source", "en", "--target", "ru", "--mode", "bilingual",
    )
    cli_job_id = re.search(r"задание: (\S+)", res.stdout).group(1)
    lines.append("1. Задание создано CLI (verbatim stdout):")
    lines += [f"  | {line}" for line in res.stdout.strip().splitlines()]
    row = next(r for r in main.client.get("/api/jobs").json() if r["id"] == cli_job_id)
    lines.append("")
    lines.append("   тот же id в GET /api/jobs (строка verbatim JSON):")
    lines.append(f"   | {json.dumps(row, ensure_ascii=False)}")
    facts = main.client.get(f"/api/jobs/{cli_job_id}").json()
    lines.append(f"   GET /api/jobs/{cli_job_id}: HTTP 200, status={facts['status']!r}")
    fwd_ok = facts["id"] == cli_job_id and row["target_language"] == "ru"

    # UI → CLI
    name = _upload_book(main.client, _BOOKS["slow"])
    api_job_id = _create_job(main.client, name)
    res = _cli(main, "job", "list")
    line = next(ln for ln in res.stdout.splitlines() if api_job_id in ln)
    lines.append("")
    lines.append(f"2. Задание создано API (id={api_job_id}, u2_slow.epub).")
    lines.append("   то же задание в CLI job list (строка verbatim):")
    lines.append(f"   | {line}")
    res = _cli(main, "job", "status", api_job_id)
    status_line = next(
        ln for ln in res.stdout.splitlines() if ln.startswith("статус:")
    )
    api_status = main.client.get(f"/api/jobs/{api_job_id}").json()["status"]
    lines.append("   CLI job status (rc и строка статуса verbatim):")
    lines.append(f"   | rc={res.returncode} | {status_line}")
    lines.append(f"   API status: {api_status!r}")
    back_ok = api_job_id in res.stdout and f"статус: {api_status}" in res.stdout

    ok = fwd_ok and back_ok
    lines.append(f"\nВЕРДИКТ: U2-3 (CLI→UI и UI→CLI) — {'PASS' if ok else 'FAIL'}")
    return "\n".join(lines)


# ------------------------------------------------------------------ t-u2-4


def ev_t_u2_4(main: U1Profile, base: Path) -> str:
    lines = [
        header(),
        "K: T-U2-4 — validate повреждённого EPUB: ненулевой код + внятное сообщение",
        "",
    ]
    broken = base / "u2ev_broken.epub"
    broken.write_text("это текст, а не EPUB", encoding="utf-8")
    res_broken = _cli(main, "validate", str(broken))
    lines.append("1. Повреждённый EPUB (текстовый файл с расширением .epub):")
    lines.append(f"   $ {_cli_cmdline(main, 'validate', 'u2ev_broken.epub')}")
    lines.append(f"   rc = {res_broken.returncode}")
    lines += [f"   | {line}" for line in res_broken.stderr.strip().splitlines()]
    lines.append("")
    res_valid = _cli(main, "validate", str(_BOOKS["tiny"]))
    lines.append("2. Контроль: валидная книга (u2_tiny.epub):")
    lines.append(f"   $ {_cli_cmdline(main, 'validate', 'u2_tiny.epub')}")
    lines.append(f"   rc = {res_valid.returncode}")
    lines += [f"   | {line}" for line in res_valid.stdout.strip().splitlines()]

    ok = (
        res_broken.returncode == 3
        and "ZIP" in res_broken.stderr
        and res_valid.returncode == 0
        and "EPUBCheck" in res_valid.stdout
        and "0 FATAL, 0 ERROR" in res_valid.stdout
    )
    lines.append(
        f"\nВЕРДИКТ: T-U2-4 (повреждённый → код 3 + сообщение; валидная → 0 + версия) "
        f"— {'PASS' if ok else 'FAIL'}"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ main


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="u2-evidence-"))
    books_dir = base / "books"
    books_dir.mkdir(parents=True, exist_ok=True)
    _synth_u1_slow_book(books_dir / "u2_slow.epub")
    _synth_u1_tiny_book(books_dir / "u2_tiny.epub")
    _BOOKS.update({"slow": books_dir / "u2_slow.epub", "tiny": books_dir / "u2_tiny.epub"})
    server = MockLLMServer()
    server.start()
    profiles: dict[str, U1Profile] = {}
    try:
        profiles["main"] = _profile(base, "main", server.url, secret=True)
        # dead: тот же секрет — confirm (POST) в NEEDS_CONFIRMATION проходит;
        # LLM-down поведение от секрета не зависит (web-auth vs LLM — разное).
        profiles["dead"] = _profile(base, "dead", "http://127.0.0.1:1/v1", secret=True)
        print("[u2-evidence] окружение готово")
        evidence("u2-1-no-duplication.txt", ev_u2_1(profiles["main"]))
        evidence("u2-2-exit-codes.txt", ev_u2_2(profiles["main"], profiles["dead"], base, server))
        evidence("u2-3-cross-visibility.txt", ev_u2_3(profiles["main"]))
        evidence("t-u2-4-validate.txt", ev_t_u2_4(profiles["main"], base))
        print("[u2-evidence] все захваты записаны")
        return 0
    finally:
        _kill_all(_WORKERS)
        server.stop()
        for profile in profiles.values():
            profile.db.close()
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
