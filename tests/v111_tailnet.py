"""V1.11: проверка доступа по Tailscale с внешней машины (TZ_04 §V1.11).

Все четыре операции выполняются на внешней машине ``vzu5-claw``
(100.95.195.40) через SSH поверх tailnet. Проверка ``curl localhost``
достаточной проверкой не считается (TZ_04): сервер привязан к Tailscale-
адресу этой машины, все запросы идут с внешней машины по сети.

Операции (4/4):
1. открыть Web UI (HTTP 200, HTML-страница);
2. загрузить книгу (``POST /api/books``);
3. запустить задание (``POST /api/jobs``);
4. скачать результат (``GET /api/jobs/{id}/result``).

Mock LLM привязан к 0.0.0.0 (класс по умолчанию — 127.0.0.1): воркер
зовёт его по тому же tailnet-адресу (``http://<ts-ip>:<port>/v1``) —
LLM-канал тоже идёт по частной сети.

Доступ: ``SERVER_AUTH_SECRET`` задан (код U3, D91: при пустом секрете все
мутирующие маршруты отвечают 403, 4/4 без секрета недостижимо), все
запросы — с Basic (пароль = секрет), мутирующие (загрузка книги, задание,
подтверждение) — с CSRF-токеном, извлечённым из meta ``u3-csrf`` открытой
страницы (тот же путь, что у браузерного пользователя). Предмет проверки —
канал доступа, а не аутентификация (секрет покрыт U1-7). Книга — tiny 2×8
(английская), режим bilingual; при NEEDS_CONFIRMATION внешняя машина
подтверждает язык через тот же API.

Захват: по умолчанию docs/CONTROL/evidence/V1/v1-11-tailnet.txt;
переопределяется переменными окружения V111_EVIDENCE_DIR /
V111_EVIDENCE_FILE (V1F-прогон пишет в evidence/V1F/).
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "probe"))
sys.path.insert(0, str(ROOT / "tests"))

import mock_llm_server as mls  # noqa: E402
from test_u1_web import _synth_u1_tiny_book  # noqa: E402
from v1_evidence import _make_v1_storage, _stop_worker, _worker  # noqa: E402

from app.core.config import ALL_ENV_KEYS, load_settings  # noqa: E402
from app.storage.jobs_repo import JobRepository  # noqa: E402

EVIDENCE_DIR = Path(
    os.environ.get(
        "V111_EVIDENCE_DIR", str(ROOT / "docs" / "CONTROL" / "evidence" / "V1")
    )
)
EVIDENCE_FILE = Path(os.environ.get("V111_EVIDENCE_FILE", "v1-11-tailnet.txt"))
if not EVIDENCE_FILE.is_absolute():
    EVIDENCE_FILE = EVIDENCE_DIR / EVIDENCE_FILE
REMOTE_IP = "100.95.195.40"  # vzu5-claw (tailnet); MagicDNS-имена отключены
#: U3 (D91): без секрета мутирующие маршруты отвечают 403 — 4/4 только с ним.
#: Секрет одноразовый (временное хранилище harness'а), не боевой.
AUTH_USER = "v111"
AUTH_SECRET = "v1f11-tailnet-secret"
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
WEB_PORT = 8321  # server.port по умолчанию; экземпляр единственный
_JSON = "python3 -c 'import json,sys; print(json.load(sys.stdin)[\"{k}\"])'"


def _tailscale_ip() -> str:
    out = subprocess.run(
        ["tailscale", "ip", "-4"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if not out.startswith("100."):
        raise SystemExit(f"tailscale ip -4: не tailnet-адрес: {out!r}")
    return out


def _mock_on_any_addr() -> tuple[int, mls.MockLLMServer]:
    """Mock, привязанный к 0.0.0.0 (класс hardcode'ит 127.0.0.1)."""
    real = mls.ThreadingHTTPServer

    def _bind_any(bind: tuple[str, int], handler: Any) -> ThreadingHTTPServer:
        return real(("0.0.0.0", bind[1]), handler)

    mls.ThreadingHTTPServer = _bind_any  # type: ignore[misc,assignment]
    server = mls.MockLLMServer()
    server.start()
    return server._port, server


def _clean_env() -> dict[str, str]:
    """Окружение subprocess'ов: без app-переменных из внешней среды.

    load_settings читает os.environ ВПЕРЕД .env-файлом (D71); фоновый
    экспорт SERVER_BIND_ADDRESS=127.0.0.1 в окружении сессии маскировал
    bind к tailnet-адресу. Env-файл задания — единственный источник."""
    env = dict(os.environ)
    for key in ALL_ENV_KEYS:
        env.pop(key, None)
    return env


def _port_in_use(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_http(
    url: str, timeout: float, basic: tuple[str, str] | None = None
) -> bool:
    """GET с ожиданием HTTP 200; U3: при заданном секрете — с Basic-заголовком."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(url)
            if basic is not None:
                token = base64.b64encode(
                    f"{basic[0]}:{basic[1]}".encode()
                ).decode()
                req.add_header("Authorization", f"Basic {token}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(1.0)
    return False


def _remote_script(ts_ip: str) -> str:
    base = f"http://{ts_ip}:{WEB_PORT}"
    script = [
        "set -u",
        f"B={base}",
        f"U={AUTH_USER}",
        f"P={AUTH_SECRET}",
        "echo '### OP1: открытие Web UI (внешняя машина vzu5-claw)'",
        "curl -s -L -u \"$U:$P\" -o /tmp/v111_ui.html "
        "-w 'UI HTTP %{http_code}\\n' $B/",
        "echo '--- первые 300 байт страницы:'",
        "head -c 300 /tmp/v111_ui.html",
        "echo",
        "echo \"строк u3-strings на странице (вёрстка U3): "
        "$(grep -c 'u3-strings' /tmp/v111_ui.html)\"",
        'TOK=$(grep -o \'name="u3-csrf" content="[a-f0-9]*"\' '
        "/tmp/v111_ui.html | grep -oE '[a-f0-9]{32}' | head -n 1)",
        "echo \"CSRF-токен (meta страницы): ${TOK:-отсутствует}\"",
        "echo '### OP2: загрузка книги (POST /api/books, Basic + X-U3-CSRF)'",
        "UP=$(curl -s -w '\\nUPLOAD HTTP %{http_code}' "
        "-u \"$U:$P\" -H \"X-U3-CSRF: $TOK\" "
        "-F 'file=@/tmp/v111_book.epub' $B/api/books)",
        'echo "$UP"',
        f'NAME=$(printf \'%s\\n\' "$UP" | head -n -1 | {_JSON.format(k="name")})',
        'echo "name: $NAME"',
        "echo '### OP3: запуск задания (POST /api/jobs, Basic + JSON csrf)'",
        "JDATA=$(printf '{\"book\":\"%s\",\"target_language\":\"ru\","
        "\"mode\":\"bilingual\",\"csrf\":\"%s\"}' \"$NAME\" \"$TOK\")",
        "echo \"JDATA: $JDATA\"",
        "JOB=$(curl -s -w '\\nJOB HTTP %{http_code}' "
        "-u \"$U:$P\" -H 'Content-Type: application/json' "
        '-d "$JDATA" $B/api/jobs 2>/tmp/v111_curl_err.txt); '
        "RC3=$?",
        "echo \"curl_rc: $RC3; curl_stderr: $(cat /tmp/v111_curl_err.txt)\"",
        'echo "$JOB"',
        f'JID=$(printf \'%s\\n\' "$JOB" | head -n -1 | {_JSON.format(k="id")})',
        'echo "job: $JID"',
        "echo '### ожидание завершения (с внешней машины, опрос API)'",
        "for i in $(seq 1 120); do",
        f'  ST=$(curl -s -u "$U:$P" $B/api/jobs/$JID | {_JSON.format(k="status")})',
        '  echo "poll $i: $ST"',
        "  case \"$ST\" in",
        "    NEEDS_CONFIRMATION)",
        '      curl -s -X POST -u "$U:$P" -H \'Content-Type: application/json\' '
        '-d \'{"source_language":"en","csrf":"\'$TOK\'"}\' '
        "$B/api/jobs/$JID/confirm",
        "      echo",
        "      ;;",
        "    COMPLETED|COMPLETED_WITH_WARNINGS|FAILED|CANCELLED) break ;;",
        "  esac",
        "  sleep 5",
        "done",
        "echo '### OP4: скачивание результата (GET /api/jobs/$JID/result)'",
        "curl -s -u \"$U:$P\" -o /tmp/v111_result.epub "
        "-w 'RESULT HTTP %{http_code} SIZE %{size_download}\\n' "
        "$B/api/jobs/$JID/result",
        "echo -n 'zip-магия (PK = 50 4b 03 04): '",
        "head -c 4 /tmp/v111_result.epub | od -An -tx1",
        "echo '### факты задания (с внешней машины):'",
        "curl -s -u \"$U:$P\" $B/api/jobs/$JID | python3 -m json.tool "
        "| head -n 25",
        "echo REMOTE_DONE",
    ]
    return "\n".join(script) + "\n"


def _run_remote(ts_ip: str, book: Path, lines: list[str]) -> tuple[bool, str]:
    """scp книги + 4 операции с внешней машины; verbatim-вывод в evidence."""
    scp = subprocess.run(
        ["scp", "-o", "BatchMode=yes", str(book), f"{REMOTE_IP}:/tmp/v111_book.epub"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    lines.append(f"scp книга: rc={scp.returncode} {scp.stderr.strip() or 'ок'}")
    if scp.returncode != 0:
        return False, ""
    ssh = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", REMOTE_IP, "bash -s"],
        input=_remote_script(ts_ip),
        capture_output=True, text=True, timeout=900, check=False,
    )
    out = ssh.stdout
    lines.append("")
    lines.append("=== вывод с внешней машины vzu5-claw (verbatim) ===")
    lines.extend(out.splitlines())
    if ssh.returncode != 0:
        lines.append(f"--- ssh rc={ssh.returncode}, stderr: {ssh.stderr.strip()}")
    return ssh.returncode == 0, out


def _append_web_tail(base: Path, lines: list[str]) -> None:
    """web.log (серверная сторона) в evidence: какие запросы дошли до сервера."""
    wlog = base / "web.log"
    if not wlog.is_file():
        return
    lines.append("")
    lines.append("--- web.log (серверная сторона, последние 40 строк):")
    wlines = wlog.read_text(encoding="utf-8", errors="replace").splitlines()
    lines += [f"  | {wline}" for wline in wlines[-40:]]


def _setup(base: Path, ts_ip: str) -> dict[str, Any]:
    """Книга, mock (0.0.0.0), хранилище, env-файл: сервер — на tailnet-адресе
    этой машины (не 127.0.0.1, не wildcard)."""
    book = base / "book.epub"
    _synth_u1_tiny_book(book)
    mock_port, mock = _mock_on_any_addr()
    settings, db, cfg, env_file = _make_v1_storage(
        base, "v111", f"http://{ts_ip}:{mock_port}/v1"
    )
    env_file.write_text(
        f"SERVER_BIND_ADDRESS={ts_ip}\nLLM_API_KEY=test-key\n"
        f"SERVER_AUTH_SECRET={AUTH_SECRET}\n", encoding="utf-8"
    )
    settings = load_settings(cfg, env_file)
    if _port_in_use(WEB_PORT):
        raise SystemExit(f"порт {WEB_PORT} занят — честный BLOCKER, не ослабляем")
    return {
        "book": book, "mock": mock, "mock_port": mock_port,
        "settings": settings, "db": db, "cfg": cfg, "env_file": env_file,
    }


def _teardown(worker: Any, web_proc: subprocess.Popen[bytes],
              web_log: Any, mock: mls.MockLLMServer, db: Any) -> None:
    _stop_worker(worker)
    web_proc.kill()
    web_proc.wait(timeout=30)
    web_log.close()
    mock.stop()
    db.close()


def _verdict(lines: list[str], remote_out: str, repo: JobRepository,
             job_dir: Any) -> bool:
    checks: dict[str, bool] = {
        "OP1 UI": "UI HTTP 200" in remote_out and "<html" in remote_out.lower(),
        "OP2 upload": "UPLOAD HTTP 200" in remote_out,
        "OP3 job": "JOB HTTP 200" in remote_out,
        "OP4 result": "RESULT HTTP 200" in remote_out and "50 4b 03 04" in remote_out,
    }
    jid_match = re.search(r"^job: (\S+)$", remote_out, flags=re.M)
    job = repo.get(jid_match.group(1)) if jid_match else None
    final_ok = job is not None and job.status.value in (
        "COMPLETED", "COMPLETED_WITH_WARNINGS",
    )
    if job is not None:
        lines.append("")
        lines.append(
            f"задание (БД): {job.id}  {job.status.value}  "
            f"токенов: {job.tokens_total}  ал.: {job.alignment_coverage}"
        )
        size_match = re.search(r"RESULT HTTP 200 SIZE (\d+)", remote_out)
        local_target = job_dir(job.id) / "target.epub"
        if size_match and local_target.is_file():
            same = int(size_match.group(1)) == local_target.stat().st_size
            lines.append(
                f"размер скачанного ({size_match.group(1)}) == локальный "
                f"target.epub ({local_target.stat().st_size}): {same}"
            )
            checks["OP4 result"] = checks["OP4 result"] and same
    lines.append(f"итоговый статус COMPLETED*: {final_ok}")
    for name, passed in checks.items():
        lines.append(f"  {name}: {'PASS' if passed else 'FAIL'}")
    ok = final_ok and all(checks.values())
    lines.append("")
    lines.append(f"ВЕРДИКТ V1.11: {'PASS 4/4' if ok else 'FAIL'}")
    return ok


def main() -> int:
    ts_ip = _tailscale_ip()
    lines: list[str] = []
    lines.append(f"V1.11 — доступ по Tailscale с внешней машины  ({STAMP})")
    lines.append(f"эта машина: {ts_ip}; внешняя: vzu5-claw ({REMOTE_IP}); порт {WEB_PORT}")
    lines.append(
        "сервер привязан к tailnet-адресу этой машины; mock LLM — к 0.0.0.0; "
        "LLM-адрес в конфиге = http://<ts-ip>:<mock-port>/v1"
    )
    lines.append(
        f"U3 (код стадии V1F): SERVER_AUTH_SECRET задан ({AUTH_USER}:<секрет>), "
        "все запросы — Basic, мутирующие — с CSRF из meta страницы "
        "(D91: без секрета мутирующие маршруты отвечают 403)"
    )
    lines.append(f"python={sys.version.split()[0]}")
    lines.append("")

    base = Path(tempfile.mkdtemp(prefix="v111_"))
    st = _setup(base, ts_ip)
    book, mock = st["book"], st["mock"]
    lines.append(f"книга: tiny 2×8, {book.stat().st_size} байт (scp → {REMOTE_IP}:/tmp)")
    lines.append("")
    lines.append(f"mock LLM: 0.0.0.0:{st['mock_port']} (scenario=ok)")
    lines.append("")
    settings, db, cfg, env_file = (
        st["settings"], st["db"], st["cfg"], st["env_file"]
    )

    clean = _clean_env()
    web_log = (base / "web.log").open("w", encoding="utf-8")
    web_proc = subprocess.Popen(
        [sys.executable, "-m", "app", "--config", str(cfg), "--env", str(env_file)],
        cwd=ROOT, stdout=web_log, stderr=subprocess.STDOUT, env=clean,
    )
    worker = _worker(cfg, env_file, base / "worker.log", env=clean)
    ok_ready = _wait_http(
        f"http://{ts_ip}:{WEB_PORT}/api/health", timeout=90,
        basic=(AUTH_USER, AUTH_SECRET),
    )
    lines.append(f"готовность сервера (опрос по {ts_ip}): {'ок' if ok_ready else 'НЕ ГОТОВ'}")
    if not ok_ready:
        lines.append("--- хвост web.log:")
        lines.append((base / "web.log").read_text(encoding="utf-8")[-2000:])

    ok = ok_ready
    remote_out = ""
    if ok_ready:
        ok, remote_out = _run_remote(ts_ip, book, lines)

    repo = JobRepository(db)
    ok = ok and _verdict(lines, remote_out, repo, settings.job_dir)

    _teardown(worker, web_proc, web_log, mock, db)
    # Серверная сторона: какие запросы дошли до сервера (диагностика OP)
    _append_web_tail(base, lines)

    EVIDENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE_FILE
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[v111] evidence: {out}")
    print(f"[v111] вердикт: {'PASS 4/4' if ok else 'FAIL'}")

    if ok:
        shutil.rmtree(base, ignore_errors=True)
    else:
        # При FAIL рабочая директория (web.log, worker.log, БД, артефакты)
        # сохраняется для диагностики — не удаляем безосновательно.
        keep = Path(tempfile.gettempdir()) / f"v111_debug_{int(time.time())}"
        shutil.move(str(base), str(keep))
        print(f"[v111] рабочая директория сохранена: {keep}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
