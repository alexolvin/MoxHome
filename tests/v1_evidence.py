"""V1 (ТЗ_04 §V1.5–V1.9): verbatim-захваты программы приёмки.

Запуск: python tests/v1_evidence.py [v5 v6 v7 v8 v8a v9]   (без аргументов — все)

LLM — детерминированный mock (D3): реальный LLM — BLOCKER (ключей нет),
не утверждается. Остальное — реальные компоненты (spaCy, EPUBCheck, SQLite,
движок epub-translator; CLI и воркер — отдельные процессы ``python -m …``).

Захваты: docs/CONTROL/evidence/V1/v1-5-smoke.txt, v1-6-inspection.txt,
v1-7-bilingual.txt, v1-8-scenarios.txt, v1-8a-terminology.txt,
v1-9-restart.txt + v1-evidence-summary.txt.

V1.1–V1.4 (статические проверки) и V1.10–V1.13 — отдельные процедуры,
см. docs/CONTROL/V1.md (V1.13 — tests/v113_runs.py).
V1.11 (Tailscale с другой машины) — честный BLOCKER: второй машины нет.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "probe"))
sys.path.insert(0, str(ROOT / "tests"))

from mock_llm_server import MockLLMServer  # noqa: E402
from test_l2_glossary import _WORDS20, WEXLEY_PARAS  # noqa: E402
from test_l3_glossary_fix import _kill_all  # noqa: E402
from test_s4_study import _synth_book_multi  # noqa: E402
from test_u1_web import _synth_u1_slow_book, _synth_u1_tiny_book  # noqa: E402

from app.core.config import Settings, load_settings  # noqa: E402
from app.epub.reader import read_epub  # noqa: E402
from app.languages.cefr import language_cefr_label  # noqa: E402
from app.languages.registry import load_registry  # noqa: E402
from app.storage.database import Database  # noqa: E402
from app.storage.jobs_repo import JobRepository  # noqa: E402
from app.web.app import create_app  # noqa: E402
from app.workers.pipeline import Pipeline  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "V1"
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
VERSIONS = f"python={sys.version.split()[0]} spacy={pkg_version('spacy')}"
EPUBCHECK_JAR = ROOT / "data" / "tools" / "epubcheck.jar"

_WORKERS: list[subprocess.Popen[bytes]] = []


# ------------------------------------------------------------------ служебное


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_lines(payload: Any, prefix: str = "  | ") -> list[str]:
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    return [f"{prefix}{line}" for line in raw.splitlines()]


def _subset(payload: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: payload.get(key) for key in keys if key in payload}


def _worker(
    cfg: Path,
    env_file: Path,
    log_path: Path,
    *,
    block_kb: int | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    cmd = [
        sys.executable, "-m", "app.workers.run",
        "--config", str(cfg), "--env", str(env_file), "--loop",
    ]
    if block_kb is not None:
        # ENOSPC-симуляция без root: ограничение размера файла (RLIMIT_FSIZE) —
        # запись крупного файла даёт честную OSError [Errno 28].
        cmd = ["bash", "-c", f"ulimit -f {block_kb}; exec " + " ".join(f'"{p}"' for p in cmd)]
    handle = log_path.open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, env=env
        )
    finally:
        handle.close()
    _WORKERS.append(proc)
    return proc


def _stop_worker(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=30)


def _cli(cfg: Path, env_file: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "app.cli", "--config", str(cfg), "--env", str(env_file), *args]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=900, check=False)


def _make_v1_storage(
    base: Path,
    name: str,
    url: str,
    *,
    yaml_extra: str = "",
    profile_overrides: dict[str, dict[str, Any]] | None = None,
    jar: Path | None = None,
    languages_root: Path | None = None,
    matrix: Path | None = None,
) -> tuple[Settings, Database, Path, Path]:
    """Изолированное хранилище (паттерн U1) + настраиваемые профили/jar/языки."""
    work = base / name
    work.mkdir(parents=True, exist_ok=True)
    prompts_dir = base / "prompts"
    if not prompts_dir.exists():
        shutil.copytree(ROOT / "prompts", prompts_dir)
    (work / "test.env").write_text(
        "SERVER_BIND_ADDRESS=127.0.0.1\nLLM_API_KEY=test-key\n", encoding="utf-8"
    )
    profiles = ""
    for prof in ("translation", "fill", "analysis"):
        lines = [f"    {prof}:", f"      base_url: {url}", "      model: test-model"]
        for key, val in (profile_overrides or {}).get(prof, {}).items():
            lines.append(f"      {key}: {val}")
        profiles += "\n".join(lines) + "\n"
    (work / "config.yaml").write_text(
        f"storage:\n  root: {work}\n"
        f"epubcheck:\n  jar_path: {jar or EPUBCHECK_JAR}\n"
        f"languages:\n  root: {languages_root or ROOT / 'config' / 'languages'}\n"
        f"  matrix: {matrix or ROOT / 'config' / 'language_matrix.yaml'}\n"
        f"llm:\n  prompts_root: {prompts_dir}\n  profiles:\n{profiles}"
        f"glossary:\n  enabled: true\n  fix_enabled: true\n{yaml_extra}",
        encoding="utf-8",
    )
    cfg = work / "config.yaml"
    env_file = work / "test.env"
    settings = load_settings(cfg, env_file)
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    return settings, db, cfg, env_file


def _profile(
    base: Path, name: str, url: str, **kwargs: Any
) -> tuple[Settings, Database, JobRepository, TestClient, Path, Path]:
    """Хранилище + app/TestClient + репозиторий (для подтверждения L0.5)."""
    settings, db, cfg, env_file = _make_v1_storage(base, name, url, **kwargs)
    app = create_app(settings, db=db)
    return settings, db, JobRepository(db), TestClient(app), cfg, env_file


def _create_job_cli(
    cfg: Path, env_file: Path, book: Path, lang: str, extra: tuple[str, ...]
) -> str | None:
    """CLI translate (без --wait); возвращает job_id из «задание: <id>»."""
    res = _cli(
        cfg, env_file, "translate", str(book),
        "--source", lang, "--target", "ru", "--mode", "bilingual", *extra,
    )
    match = re.search(r"задание: (\S+)", res.stdout)
    return match.group(1) if match else None


def _confirm_needs(client: TestClient, repo: Any, job_id: str, lang: str,
                   timeout: float = 900.0) -> None:
    """L0.5 (человек в контуре): дождаться NEEDS_CONFIRMATION, подтвердить
    через API, дождаться выхода из состояния. Терминальный статус до гейта
    (DRM/ENOSPC/не-ZIP) — подтверждение не нужно, возврат сразу."""
    terminal = {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED",
                "INTERRUPTED", "PAUSED"}
    deadline = time.monotonic() + timeout
    confirmed = False
    while time.monotonic() < deadline:
        job = repo.get(job_id)
        if job is None:
            return
        status = job.status.value
        if status in terminal:
            return
        if status == "NEEDS_CONFIRMATION":
            if not confirmed:
                facts = client.get(f"/api/jobs/{job_id}").json()
                detected = (facts.get("language_detection") or {}).get("detected")
                client.post(f"/api/jobs/{job_id}/confirm",
                            json={"source_language": detected or lang})
                confirmed = True
        elif confirmed:
            return  # гейт пройден
        time.sleep(0.5)
    if not confirmed:
        raise TimeoutError(f"NEEDS_CONFIRMATION: задание {job_id} не дошло до гейта")


def _await_live_status(client: TestClient, repo: Any, job_id: str, lang: str,
                       statuses: set[str], *, timeout: float = 1800.0) -> Any:
    """Подтверждение L0.5 (если возник гейт) + ожидание ЖИВОГО статуса.

    В отличие от ``_confirm_needs`` — который для книги с уверенным
    определением языка (гейт не возникает) дожидается ТЕРМИНАЛЬНОГО
    статуса — возврат при первом же наблюдении статуса из ``statuses``.
    Для сценариев «в полёте» (kill/cancel внутри окна стадии, V1.8
    s5/s9/s16). Поллинг 0,2 c."""
    deadline = time.monotonic() + timeout
    confirmed = False
    last = None
    while time.monotonic() < deadline:
        last = repo.get(job_id)
        if last is None:
            time.sleep(0.2)
            continue
        if last.status.value in statuses:
            return last
        if last.status.value == "NEEDS_CONFIRMATION" and not confirmed:
            facts = client.get(f"/api/jobs/{job_id}").json()
            detected = (facts.get("language_detection") or {}).get("detected")
            client.post(f"/api/jobs/{job_id}/confirm",
                        json={"source_language": detected or lang})
            confirmed = True
        time.sleep(0.2)
    return last


def _wait_status(repo: Any, job_id: str, statuses: set[str], timeout: float = 1800.0) -> Any:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = repo.get(job_id)
        if last is not None and last.status.value in statuses:
            return last
        time.sleep(0.5)
    return last


def _wait_interrupted_transition(repo: Any, job_id: str, timeout: float = 180.0) -> bool:
    """INTERRUPTED в истории переходов БД (источник — не живой статус).

    worker ``--loop`` после пометки задания INTERRUPTED (единственный
    механизм, E3-1) **немедленно** авто-резюмирует его
    (``worker._resume_if_needed``): живое окно INTERRUPTED субсекундное,
    поллинг 0,5 c его не видит. Таблица ``transitions`` фиксирует
    переход — проверку читаем оттуда."""
    deadline = time.monotonic() + timeout
    while True:
        if any(t.to_status.value == "INTERRUPTED"
               for t in repo.list_transitions(job_id)):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def _translate_wait(
    client: TestClient,
    repo: Any,
    cfg: Path,
    env_file: Path,
    *,
    book: Path,
    lang: str,
    log_path: Path,
    wait_seconds: float = 1200.0,
    extra: tuple[str, ...] = (),
) -> tuple[int, str, str | None]:
    """CLI ``translate … --wait`` + подтверждение L0.5 через API."""
    cmd = [
        sys.executable, "-u", "-m", "app.cli",
        "--config", str(cfg), "--env", str(env_file),
        "translate", str(book),
        "--source", lang, "--target", "ru", "--mode", "bilingual",
        *extra, "--wait", str(wait_seconds),
    ]
    handle = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
    handle.close()
    job_id: str | None = None
    confirmed = False
    deadline = time.monotonic() + wait_seconds + 300
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        text = log_path.read_text(encoding="utf-8")
        match = re.search(r"задание: (\S+)", text)
        if match is not None:
            job_id = match.group(1)
            if not confirmed:
                facts = client.get(f"/api/jobs/{job_id}").json()
                if facts.get("status") == "NEEDS_CONFIRMATION":
                    detected = (facts.get("language_detection") or {}).get("detected")
                    client.post(
                        f"/api/jobs/{job_id}/confirm",
                        json={"source_language": detected or lang},
                    )
                    confirmed = True
        time.sleep(0.5)
    code = proc.wait(timeout=300)
    log = log_path.read_text(encoding="utf-8")
    if job_id is None:
        final_match = re.search(r"задание: (\S+)", log)
        job_id = final_match.group(1) if final_match is not None else None
    return code, log, job_id


def _full_run(
    ctx: Ctx,
    name: str,
    book: Path,
    lang: str,
    *,
    extra: tuple[str, ...] = (),
    **storage_kwargs: Any,
) -> dict[str, Any]:
    """Сквозной прогон: хранилище + воркер + CLI --wait Learning. Возврат:
    dict(settings, db, repo, client, cfg, env_file, code, log, job_id, job, jdir)."""
    settings, db, repo, client, cfg, env_file = _profile(
        ctx.base, name, ctx.server.url, **storage_kwargs
    )
    worker = _worker(cfg, env_file, ctx.base / f"{name}_worker.log")
    try:
        code, log, job_id = _translate_wait(
            client, repo, cfg, env_file, book=book, lang=lang,
            log_path=ctx.base / f"{name}_cli.log", extra=extra,
        )
        job = repo.require(job_id) if job_id else None
        jdir = settings.job_dir(job.id) if job else None
        return {
            "settings": settings, "db": db, "repo": repo, "client": client,
            "cfg": cfg, "env_file": env_file, "code": code, "log": log,
            "job_id": job_id, "job": job, "jdir": jdir, "worker": worker,
        }
    except Exception:
        _stop_worker(worker)
        raise


def _zipinfo(path: Path, max_lines: int = 60) -> list[str]:
    res = subprocess.run(
        ["zipinfo", str(path)], capture_output=True, text=True, timeout=60, check=False
    )
    lines = res.stdout.splitlines()
    if len(lines) > max_lines:
        rest = len(lines) - max_lines + 1
        return [*lines[: max_lines - 1], f"  … (ещё {rest} строк)"]
    return lines


def _unzip(path: Path, dest: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        archive.extractall(dest)


def _changed_paragraphs(before_epub: Path, after_epub: Path, work: Path) -> int:
    """Сколько <p>-блоков изменилось между двумя EPUB (позиционная сверка).

    Regex-подход (без ET): независим от namespaces выходной разметки."""

    def paras(root: Path) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for xhtml in sorted(root.rglob("*.xhtml")):
            text = xhtml.read_text(encoding="utf-8")
            blocks = re.findall(r"<p\b[^>]*>(.*?)</p>", text, flags=re.S)
            plain = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", b)).strip() for b in blocks]
            out[xhtml.name] = plain
        return out

    dest_b, dest_a = work / "before", work / "after"
    _unzip(before_epub, dest_b)
    _unzip(after_epub, dest_a)
    before, after = paras(dest_b), paras(dest_a)
    changed = 0
    for name, texts_b in before.items():
        texts_a = after.get(name, [])
        for i, b_text in enumerate(texts_b):
            a_text = texts_a[i] if i < len(texts_a) else ""
            if b_text != a_text:
                changed += 1
    return changed


def evidence(name: str, content: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print(f"  [evidence] {path.relative_to(ROOT)}")


def header() -> str:
    return f"дата: {STAMP}\nверсии: {VERSIONS}\n\n"


@dataclass
class Ctx:
    base: Path
    server: MockLLMServer
    results: dict[str, tuple[bool, str]] = field(default_factory=dict)


# ------------------------------------------------------------------ книги


def _synth_wexley8(out_path: Path) -> None:
    """8 глав × 12 <p>: 3× Wexley (повторяющееся собственное имя) + 9
    разнообразных предложений (30 имён × 20 слов → 30+ терминов глоссария
    по ТЗ V1.8a). Индексация idx = ch*9+i (0..79) прогоняет ВСЕ имена:
    каждое встречается несколько раз (правило «capitalized» + NER)."""
    names = (
        "Marlow", "Ostrava", "Halden", "Ingrid", "Garrick", "Brno", "Fiona",
        "Douglas", "Catherine", "Brenda", "Albert", "Oliver", "Rutger",
        "Selma", "Viktor", "Anouk", "Wexford", "Yarrow", "Zephyr",
        "Quentin", "Beatrix", "Cornelius", "Daphne", "Emeric", "Fenwick",
        "Giselle", "Hortense", "Isidore", "Jovita", "Kasimir",
    )
    words = tuple(_WORDS20)
    bodies: list[str] = []
    for ch in range(8):
        paras: list[str] = []
        for i in range(3):
            paras.append(WEXLEY_PARAS[(ch + i) % len(WEXLEY_PARAS)])
        for i in range(9):
            idx = ch * 9 + i
            w = words[idx % len(words)]
            nm1 = names[idx % len(names)]
            nm2 = names[(idx + 11) % len(names)]
            paras.append(f"The {w} near {nm1} kept the {nm2} quiet through the night.")
        bodies.append(
            "\n".join(f'    <p id="p{i + 1}">{p}</p>' for i, p in enumerate(paras))
        )
    _synth_book_multi(out_path, "V1 Terminology EN", "en", "v1-8a-uid-0001", tuple(bodies))


def _synth_big_book(out_path: Path, paragraphs: int = 12000) -> None:
    """Одна глава ~1.2 МБ (ENOSPC-сценарий: файл крупнее ulimit -f)."""
    body = "\n".join(
        f'    <p id="p{i}">The {w} stood still while the wind moved the grass.</p>'
        for i, w in enumerate((word for word in (_WORDS20 * (paragraphs // len(_WORDS20) + 1))), 1)
    )
    _synth_book_multi(out_path, "V1 Big EN", "en", "v1-8-uid-big", (body,))


def _synth_drm_book(out_path: Path) -> None:
    """Книга с META-INF/encryption.xml (DRM-симуляция: reader отказывает)."""
    body = (
        '    <p id="p1">The village stood before dawn.</p>\n'
        '    <p id="p2">It kept its silence.</p>'
    )
    _synth_book_multi(
        out_path,
        "V1 DRM EN",
        "en",
        "v1-8-uid-drm",
        (body,),
        extra={
            "META-INF/encryption.xml": (
                '<?xml version="1.0"?>\n<encryption xmlns="http://www.idpf.org/2007/ops">\n'
                "  <encryptedData><cipherData>opaque</cipherData></encryptedData>\n"
                "</encryption>\n"
            )
        },
    )


def _books(ctx: Ctx) -> dict[str, Path]:
    books_dir = ctx.base / "books"
    books_dir.mkdir(parents=True, exist_ok=True)
    tiny = books_dir / "v1_tiny.epub"
    slow = books_dir / "v1_slow.epub"
    wexley8 = books_dir / "v1_wexley8.epub"
    big = books_dir / "v1_big.epub"
    drm = books_dir / "v1_drm.epub"
    _synth_u1_tiny_book(tiny)
    _synth_u1_slow_book(slow)
    _synth_wexley8(wexley8)
    _synth_big_book(big)
    _synth_drm_book(drm)
    return {"tiny": tiny, "slow": slow, "wexley8": wexley8, "big": big, "drm": drm}


# ------------------------------------------------------------------ V1.5


def part_v5(ctx: Ctx, books: dict[str, Path]) -> bool:
    run = _full_run(ctx, "v5", books["tiny"], "en", extra=("--level", "Learning"))
    settings, job, jdir = run["settings"], run["job"], run["jdir"]
    lines = [
        header(),
        "K: V1-5 — дымовой прогон: 2 главы, 10–20 абзацев, полный конвейер (Learning)",
        "",
        "$ python -m app.cli … translate v1_tiny.epub --source en --target ru "
        "--mode bilingual --level Learning --wait 1200",
        f"rc = {run['code']}",
        *[f"  | {line}" for line in run["log"].splitlines()[:4]],
        "",
    ]
    try:
        if run["code"] != 0 or job is None:
            lines.append("прогон не завершился: " + repr(run["log"].splitlines()[-3:]))
            evidence("v1-5-smoke.txt", "\n".join(lines))
            return False
        status = job.status.value
        lines.append(f"статус: {status}")

        # ФИЗИЧЕСКАЯ проверка EPUB (не код возврата)
        target = jdir / "target.epub"
        learning = jdir / "learning.epub"
        checks: list[tuple[str, bool]] = []
        for name, path in (("target.epub", target), ("learning.epub", learning)):
            is_zip = zipfile.is_zipfile(path)
            book = read_epub(path, ctx.base / f"v5_read_{name}", settings) if is_zip else None
            seg_count = sum(len(ch.segments) for ch in book.chapters) if book else 0
            words = (
                sum(len(seg.plain_text.split()) for ch in book.chapters for seg in ch.segments)
                if book else 0
            )
            checks.append((f"{name}: ZIP + читается ридером (E2.5)", is_zip and book is not None))
            ok_readable = book is not None and seg_count > 0
            checks.append((
                f"{name}: глав {len(book.chapters) if book else 0}, "
                f"сегментов {seg_count}, слов {words}",
                ok_readable,
            ))
            lines.append(f"{name}: {path.stat().st_size} байт; "
                         f"глав {len(book.chapters) if book else 0}; сегментов {seg_count}; "
                         f"слов {words}")

        status_res = _cli(run["cfg"], run["env_file"], "job", "status", job.id)
        lines.append("")
        lines.append("CLI job status (verbatim):")
        lines += [f"  | {line}" for line in status_res.stdout.splitlines()]
        val_res = _cli(run["cfg"], run["env_file"], "validate", str(target))
        lines.append("")
        lines.append("CLI validate target.epub (verbatim, EPUBCheck):")
        lines += [f"  | {line}" for line in val_res.stdout.splitlines()]
        checks.append(("EPUBCheck target.epub: 0 FATAL/ERROR",
                       val_res.returncode == 0 and "0 FATAL, 0 ERROR" in val_res.stdout))
        quality = _read_json(jdir / "quality.json") or {}
        lines.append("")
        lines += ["quality.json (warnings/epubcheck/alignment):"]
        lines += _dump_lines(_subset(quality, ("warnings", "epubcheck", "alignment", "glossary")))
        s5 = _read_json(jdir / "s5_report.json") or {}
        checks.append(("S5: learning.epub собран", (jdir / "learning.epub").is_file()))
        checks.append(("S5: coverage > 0", (s5.get("coverage") or 0) > 0))
        ok = run["code"] == 0 and status in ("COMPLETED", "COMPLETED_WITH_WARNINGS") \
            and all(ok for _, ok in checks)
        lines.append("")
        lines.append("Пороговая проверка:")
        for what, okc in checks:
            lines.append(f"  {'OK' if okc else 'НЕ СОВПАЛ'}: {what}")
        lines.append(f"\nВЕРДИКТ: V1-5 (дымовой прогон, физический EPUB) — "
                     f"{'PASS' if ok else 'FAIL'}")
        evidence("v1-5-smoke.txt", "\n".join(lines))
        return ok
    finally:
        _stop_worker(run["worker"])
        run["db"].close()


# ------------------------------------------------------------------ V1.6


def _container_opf(
    dest: Path, lines: list[str]
) -> tuple[Path | None, dict[str, Any]]:
    """container.xml (verbatim) + OPF manifest/spine. Нет OPF — (None, {})."""
    OPF_NS = "http://www.idpf.org/2007/opf"
    container = dest / "META-INF" / "container.xml"
    if container.is_file():
        lines.append("container.xml (verbatim):")
        lines += [f"  | {line}" for line in container.read_text(encoding="utf-8").splitlines()]
    else:
        lines.append("container.xml: ОТСУТСТВУЕТ (дефект)")
    opf_path: Path | None = None
    if container.is_file():
        rootfile = ET.parse(container).getroot().find(
            ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
        )
        if rootfile is not None:
            full_path = rootfile.get("full-path")
            if full_path is not None:
                opf_path = dest / full_path
    if opf_path is None or not opf_path.is_file():
        lines.append("OPF: не найден (дефект)")
        return None, {}
    opf = ET.parse(opf_path).getroot()
    manifest: dict[str, Any] = {
        item.get("id") or "": item
        for item in opf.findall(f"{{{OPF_NS}}}manifest/{{{OPF_NS}}}item")
    }
    spine = [
        i.get("idref") or ""
        for i in opf.findall(f"{{{OPF_NS}}}spine/{{{OPF_NS}}}itemref")
    ]
    lines.append(f"OPF ({opf_path.relative_to(dest)}): "
                 f"manifest {len(manifest)} items, spine {len(spine)} itemref")
    lines += [
        f"  | {item.get('id')}: {item.get('href')} "
        f"[{item.get('media-type')}] {item.get('properties') or ''}"
        for item in manifest.values()
    ]
    lines.append("spine: " + ", ".join(spine))
    return opf_path, manifest


def _toc_links(dest: Path, opf_path: Path, manifest: dict[str, Any],
               lines: list[str], broken: list[str]) -> None:
    """href манифеста + nav + NCX (битые ссылки — в broken)."""
    NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
    for item in manifest.values():
        href = item.get("href")
        if href and not (opf_path.parent / href).exists():
            broken.append(f"manifest: {href}")
    nav_path = None
    for item in manifest.values():
        if item.get("properties") == "nav":
            nav_path = opf_path.parent / item.get("href")
    if nav_path is not None and nav_path.is_file():
        nav_hrefs = re.findall(r'<a\s[^>]*href="([^"]+)"',
                               nav_path.read_text(encoding="utf-8"))
        for href in nav_hrefs:
            path_part = href.split("#", 1)[0]
            if path_part and not (nav_path.parent / path_part).exists():
                broken.append(f"nav: {href}")
        lines.append(f"nav ({nav_path.name}): {len(nav_hrefs)} ссылок")
    ncx = None
    for item in manifest.values():
        if item.get("media-type") == "application/x-dtbncx+xml":
            ncx = opf_path.parent / item.get("href")
    if ncx is not None and ncx.is_file():
        ncount = len(ET.parse(ncx).getroot().findall(f".//{{{NCX_NS}}}navPoint"))
        lines.append(f"NCX ({ncx.name}): {ncount} navPoint")
    else:
        lines.append("NCX: отсутствует (EPUB3: навигация — nav.xhtml; допустимо)")


def _media_links(dest: Path, files: list[str], lines: list[str],
                 broken: list[str]) -> None:
    """CSS + ссылки <img> (битые ссылки — в broken)."""
    css = [name for name in files if name.endswith(".css")]
    lines.append(f"CSS: {len(css)} файлов" + (f" ({', '.join(css)})" if css else " — не найдено"))
    img_refs = 0
    for xhtml in dest.rglob("*.xhtml"):
        for href in re.findall(r'<img\s[^>]*src="([^"]+)"',
                               xhtml.read_text(encoding="utf-8")):
            img_refs += 1
            path_part = href.split("#", 1)[0]
            if path_part and not (xhtml.parent / path_part).exists():
                broken.append(f"img ({xhtml.name}): {href}")
    lines.append(f"изображения (<img>): {img_refs} ссылок")


def _inspect_epub(path: Path, work: Path) -> list[str]:
    """Структурный осмотр EPUB: container/OPF/nav/NCX/XHTML/CSS/картинки/ссылки."""
    dest = work / path.name
    _unzip(path, dest)
    files = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    lines: list[str] = [f"--- {path.name}: {len(files)} файлов"]
    lines += [f"  | {name}" for name in files]
    opf_path, manifest = _container_opf(dest, lines)
    if opf_path is not None and manifest:
        broken: list[str] = []
        _toc_links(dest, opf_path, manifest, lines, broken)
        _media_links(dest, files, lines, broken)
        lines.append(f"битые ссылки: {len(broken)}" + (f" — {broken}" if broken else ""))
        lines.append("zipinfo (verbatim):")
        lines += [f"  | {line}" for line in _zipinfo(path)]
    return lines


def part_v6(ctx: Ctx, books: dict[str, Path]) -> bool:
    run = _full_run(ctx, "v6", books["tiny"], "en", extra=("--level", "Learning"))
    try:
        job, jdir = run["job"], run["jdir"]
        if run["code"] != 0 or job is None or job.status.value not in (
            "COMPLETED", "COMPLETED_WITH_WARNINGS"
        ):
            evidence(
                "v1-6-inspection.txt",
                header() + "K: V1-6 — осмотр EPUB\nпрогон не завершился (см. v1-5)\n",
            )
            return False
        lines = [
            header(),
            "K: V1-6 — осмотр сгенерированного EPUB (container/OPF/nav/NCX/XHTML/CSS/",
            "картинки/ссылки + zipinfo verbatim; «стандартный ридер» в headless-",
            "окружении заменён EPUBCheck 5.3.0 + ридером приложения E2.5 — честно)",
            "",
        ]
        lines += _inspect_epub(jdir / "target.epub", ctx.base / "v6_target")
        lines.append("")
        lines += _inspect_epub(jdir / "learning.epub", ctx.base / "v6_learning")
        lines.append("")
        lines.append("ВЕРДИКТ: V1-6 (осмотр EPUB) — PASS"
                     if "дефект" not in "\n".join(lines) and "битые ссылки: 0" in "\n".join(lines)
                     else "ВЕРДИКТ: V1-6 (осмотр EPUB) — FAIL")
        ok = "дефект" not in "\n".join(lines) and "битые ссылки: 0" in "\n".join(lines)
        evidence("v1-6-inspection.txt", "\n".join(lines))
        return ok
    finally:
        _stop_worker(run["worker"])
        run["db"].close()


# ------------------------------------------------------------------ V1.7


def part_v7(ctx: Ctx, books: dict[str, Path]) -> bool:
    run = _full_run(ctx, "v7", books["tiny"], "en")
    lines = [header(), "K: V1-7 — bilingual: сверка исходных сегментов с переводами", ""]
    try:
        job, jdir, settings = run["job"], run["jdir"], run["settings"]
        if run["code"] != 0 or job is None:
            lines.append("прогон не завершился")
            evidence("v1-7-bilingual.txt", "\n".join(lines))
            return False

        src_book = read_epub(books["tiny"], ctx.base / "v7_src", settings)
        src_texts = [
            seg.plain_text for ch in src_book.chapters for seg in ch.segments
        ]
        alignment = _read_json(jdir / "alignment_report.json") or {}
        quality = _read_json(jdir / "quality.json") or {}
        docs = alignment.get("docs") or []
        pairs = [
            pair
            for doc in docs
            for pair in (doc.get("aligned") or [])
        ]
        unaligned = [
            item
            for doc in docs
            for item in (doc.get("unaligned") or [])
        ]
        aligned = alignment.get("aligned") or 0
        total = alignment.get("total") or 0
        share = (aligned / total) if total else 0.0

        empty_blocks = [
            str(pair.get("segment_id"))
            for pair in pairs
            if not re.sub(r"\s+", " ", str(pair.get("translation_text") or "")).strip()
        ]
        trans_texts: list[str] = []
        src_seen: dict[str, int] = {}
        dup_src = 0
        for pair in pairs:
            src_norm = re.sub(r"\s+", " ", str(pair.get("source_text") or "")).strip()
            dst_norm = re.sub(r"\s+", " ", str(pair.get("translation_text") or "")).strip()
            if src_norm in src_seen:
                dup_src += 1
            src_seen[src_norm] = src_seen.get(src_norm, 0) + 1
            trans_texts.append(dst_norm)
        dup_trans = len(trans_texts) - len(set(trans_texts))
        coverage_ok = (quality.get("alignment") or {}).get("coverage")

        checks = [
            (f"исходных сегментов: {len(src_texts)}; пар в выравнивании: {aligned}/{total} "
             f"({share:.4f})", True),
            (f"охват (quality.alignment.coverage): {coverage_ok} ≥ 0.995 (E5-2)",
             coverage_ok is not None and coverage_ok >= 0.995),
            (f"сегментов без выравнивания (unaligned, с причиной): {len(unaligned)}",
             not unaligned),
            (f"пустых переводных блоков: {len(empty_blocks)}", not empty_blocks),
            (f"дублирующиеся переводы (разные исходные → один перевод): {dup_trans}",
             dup_trans == 0),
            (f"дублирующиеся исходники: {dup_src} (в синтетической книге абзацы "
             "уникальны — ожидаемо 0)", dup_src == 0),
        ]
        lines.append("Метрики:")
        lines += _dump_lines({
            "segments_total": total, "aligned": aligned, "share": share,
            "coverage_quality": coverage_ok,
            "unaligned": [
                {"segment_id": u.get("segment_id"), "reason": u.get("reason")}
                for u in unaligned
            ],
            "empty_blocks": empty_blocks,
            "duplicate_translations": dup_trans,
            "duplicate_sources": dup_src,
            "identity_mismatches": alignment.get("identity_mismatches"),
        })
        lines.append("")
        lines.append("Пороговая проверка:")
        for what, okc in checks:
            lines.append(f"  {'OK' if okc else 'НЕ СОВПАЛ'}: {what}")
        ok = all(okc for _, okc in checks)
        lines.append(f"\nВЕРДИКТ: V1-7 (bilingual-сверка) — {'PASS' if ok else 'FAIL'}")
        evidence("v1-7-bilingual.txt", "\n".join(lines))
        return ok
    finally:
        _stop_worker(run["worker"])
        run["db"].close()


# ------------------------------------------------------------------ V1.8


def _scen(out_lines: list[str], no: str, title: str, ok: bool, detail: list[str], *,
          res: subprocess.CompletedProcess[str] | None = None) -> None:
    block = [f"--- сценарий {no}: {title}"]
    if res is not None:
        block.append(f"  rc = {res.returncode}")
        block += [f"  | {line}" for line in (res.stderr or res.stdout).strip().splitlines()[:4]]
    block += [f"  {line}" for line in detail]
    block.append(f"  → {'ОК' if ok else 'НЕ СОВПАЛ'}")
    out_lines.extend(block)
    print(f"[v1-8] {no} {title}: {'OK' if ok else 'FAIL'}")


def _run_to_status(
    ctx: Ctx, name: str, book: Path, lang: str, statuses: set[str],
    *, scenario: str | None = None, worker_block_kb: int | None = None,
    wait: float = 1800.0, extra: tuple[str, ...] = ("--level", "Learning"),
    live: bool = False, **storage_kwargs: Any,
) -> dict[str, Any]:
    """Хранилище + воркер + CLI (без --wait) + ожидание статуса. Возврат dict.

    ``live=True`` — живой статус (задание ещё работает, сценарий действует
    внутри окна стадии): подтверждение L0.5 + возврат при первом
    наблюдении статуса из ``statuses`` (``_await_live_status``). Иначе —
    ``_confirm_needs`` + ``_wait_status`` (цель — терминальный статус)."""
    settings, db, repo, client, cfg, env_file = _profile(
        ctx.base, name, ctx.server.url, **storage_kwargs
    )
    if scenario is not None:
        ctx.server.set_scenario(scenario)
    worker = _worker(cfg, env_file, ctx.base / f"{name}_worker.log",
                     block_kb=worker_block_kb)
    job_id = _create_job_cli(cfg, env_file, book, lang, extra)
    if job_id:
        if live:
            job = _await_live_status(client, repo, job_id, lang, statuses,
                                     timeout=wait)
        else:
            _confirm_needs(client, repo, job_id, lang)
            job = _wait_status(repo, job_id, statuses, timeout=wait)
    else:
        job = None
    return {
        "settings": settings, "db": db, "repo": repo, "client": client,
        "cfg": cfg, "env_file": env_file, "job_id": job_id, "job": job,
        "jdir": settings.job_dir(job_id) if job_id else None, "worker": worker,
    }


def _v8_01(ctx: Ctx, books: dict[str, Path], lines: list[str]) -> bool:
    # --- 1: таймаут LLM
    r = _run_to_status(
        ctx, "v8_s1", books["tiny"], "en", {"FAILED"}, scenario="timeout",
        profile_overrides={"translation": {"timeout_seconds": 1.0, "retry_times": 1}},
    )
    job = r["job"]
    reason = (job.error_message or "") if job else ""
    # httpx пишет "Request timed out" — прежняя проверка искала только
    # "timeout" (статус и причина были верны, а сценарий помечался
    # «НЕ СОВПАЛ»)
    timeout_in_reason = "timeout" in reason.lower() or "timed out" in reason.lower()
    ok = (job is not None and job.status.value == "FAILED" and timeout_in_reason)
    _scen(lines, "1", "таймаут LLM (mock 30 c сна, клиент 1 c, 1 попытка) → FAILED",
          ok,
          [f"причина (verbatim): {reason}",
           f"target.epub создан: {(r['jdir'] / 'target.epub').is_file()}",
           f"токены T: {job.tokens_translation if job else 0}"]
          )
    _stop_worker(r["worker"])
    r["db"].close()
    return ok


def _v8_02(ctx: Ctx, books: dict[str, Path], lines: list[str]) -> bool:
    # --- 2: HTTP 500
    r = _run_to_status(
        ctx, "v8_s2", books["tiny"], "en", {"FAILED"}, scenario="http500",
        profile_overrides={"translation": {"retry_times": 1}},
    )
    job = r["job"]
    reason = (job.error_message or "") if job else ""
    ok = job is not None and job.status.value == "FAILED" and "500" in reason
    _scen(lines, "2", "HTTP 500 (mock) → FAILED с причиной as-is", ok,
          [f"причина (verbatim): {reason}"])
    _stop_worker(r["worker"])
    r["db"].close()
    return ok


def _v8_03(ctx: Ctx, books: dict[str, Path], lines: list[str]) -> bool:
    # --- 3: пустой ответ модели
    # Финальный текст в mock-мире создаётся FILL-путём (ответ translate
    # структурно невалидный XML), поэтому сценарий translate_empty не
    # создавал условие ТЗ «пустой артефакт не создан»: книга целиком
    # заполнялась fill. Сценарий `empty`: модель отвечает пустой строкой
    # на ANALYSIS-задачу — валидация по JSON-схеме невозможна → ошибка
    # задачи после повторных попыток, артефакт стадии не создаётся (E4-4).
    r = _run_to_status(
        ctx, "v8_s3", books["tiny"], "en", {"FAILED"}, scenario="empty",
    )
    job = r["job"]
    status = job.status.value if job else "NO_JOB"
    reason = (job.error_message or "") if job else ""
    artifacts3 = sorted(p.name for p in r["jdir"].glob("*.json")) if r["jdir"] else []
    ok = bool(status == "FAILED" and reason
              and "vocabulary_cards.json" not in artifacts3)
    _scen(lines, "3", "пустой ответ модели (mock, analysis) → ошибка задачи, "
          "пустой артефакт не создан",
          ok, [
              f"статус: {status}",
              f"причина (verbatim): {reason}",
              f"json-артефакты задания: {artifacts3 or 'нет'}",
              f"S1-артефакт vocabulary_cards.json создан: "
              f"{'vocabulary_cards.json' in artifacts3}",
          ])
    _stop_worker(r["worker"])
    r["db"].close()
    return ok


def _v8_04(ctx: Ctx, books: dict[str, Path], lines: list[str]) -> bool:
    # --- 4: битый JSON (analysis)
    r = _run_to_status(
        ctx, "v8_s4", books["tiny"], "en", {"FAILED"}, scenario="bad_json",
    )
    job = r["job"]
    reason = (job.error_message or "") if job else ""
    artifacts4 = sorted(p.name for p in r["jdir"].glob("*.json")) if r["jdir"] else []
    ok = bool(job is not None and job.status.value == "FAILED" and reason
              and "vocabulary_cards.json" not in artifacts4)
    _scen(lines, "4", "невалидный JSON ответа (mock bad_json) → ошибка задачи, "
          "артефакт не создан",
          ok, [
              f"причина (verbatim): {reason}",
              f"json-артефакты задания: {artifacts4 or 'нет'}",
              f"S1-артефакт vocabulary_cards.json создан: "
              f"{'vocabulary_cards.json' in artifacts4}",
          ])
    _stop_worker(r["worker"])
    r["db"].close()
    return ok


def _v8_05(ctx: Ctx, books: dict[str, Path], lines: list[str],
           keep: dict[str, Any]) -> bool:
    # --- 5: перезапуск процесса (kill -9) → INTERRUPTED
    # Задержка 2,0 c растягивает окно TRANSLATING (~20 c для «медленной»
    # книги — мерялось переходами БД). Live-поймать статус (поллинг 0,2 c)
    # ДО kill: _confirm_needs (старая схема) для книги с уверенным языком
    # ждал ТЕРМИНАЛ — kill приходил в уже завершённое задание.
    ctx.server.set_scenario("ok")
    ctx.server.set_delay(2.0)
    r = _run_to_status(ctx, "v8_s5", books["slow"], "en", {"TRANSLATING"},
                       wait=300, live=True)
    job_id = r["job_id"]
    caught = r["job"] is not None and r["job"].status.value == "TRANSLATING"
    # kill на ~5% сегментов: дождаться первого progress >= 0.05 (TOC-фаза
    # перевода), если движок покажет; терминал до этого — условие не создалось
    _terminal5 = {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED",
                  "CANCELLED", "INTERRUPTED"}
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = r["repo"].get(job_id)
        if job is None:
            break
        if job.status.value in _terminal5:
            caught = False
            break
        if job.progress >= 0.05:
            break
        time.sleep(0.2)
    job = r["repo"].get(job_id)
    killed_at = (f"status={job.status.value} progress={job.progress:.2f}"
                 if job else "?")
    os.kill(r["worker"].pid, signal.SIGKILL)
    r["worker"].wait(timeout=30)
    worker2 = _worker(r["cfg"], r["env_file"], ctx.base / "v8_s5_w2.log")
    keep["s5"] = {"r": r, "worker2": worker2}
    # авто-резюме (worker2) идёт без задержки: условие сценария уже
    # выполнено, kill случился в окне TRANSLATING
    ctx.server.set_delay(0.0)
    # INTERRUPTED — из истории переходов: worker --loop пометив задание
    # INTERRUPTED, немедленно авто-резюмирует его (live-окно субсекундное)
    saw_interrupted = caught and _wait_interrupted_transition(
        r["repo"], job_id, timeout=180)
    ok = caught and saw_interrupted
    # drain: worker2 уже авто-резюмировал задание (без задержки) — дождаться
    # терминала ДО возврата: иначе LLM-вызовы resumed-задания попадут в окна
    # следующих сценариев (общий mock, дельта-подсчёт) — ложные s6/s7.
    drained = _wait_status(
        r["repo"], job_id,
        {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED"},
        timeout=600,
    )
    _scen(lines, "5", "kill -9 воркера на ~5% сегментов → новое поколение воркера → "
          "INTERRUPTED",
          ok, [
              f"момент kill: {killed_at}",
              f"INTERRUPTED в истории переходов БД: {saw_interrupted}",
              f"задание в БД: {r['repo'].get(job_id) is not None}",
              f"терминал после авто-резюма worker2: "
              f"{drained.status.value if drained else None}",
          ])
    return ok


def _v8_06(ctx: Ctx, books: dict[str, Path], lines: list[str]) -> bool:
    # --- 6: не-ZIP EPUB
    ctx.server.set_delay(0.0)
    not_zip = ctx.base / "v8_not_zip.epub"
    not_zip.write_text("это не EPUB и не ZIP", encoding="utf-8")
    _settings, db, repo, _client, cfg, env_file = _profile(ctx.base, "v8_s6", ctx.server.url)
    before = len(ctx.server.requests_log())
    res = _cli(cfg, env_file, "translate", str(not_zip),
               "--source", "en", "--target", "ru", "--mode", "bilingual")
    # requests_log() — кумулятивный (все сценарии части); проверяем
    # дельту за сценарий
    llm_calls = len(ctx.server.requests_log()) - before
    ok = res.returncode == 3 and llm_calls == 0
    _scen(lines, "6", "translate не-ZIP → отказ до LLM", ok,
          [f"задач в БД: {len(repo.list_jobs())}",
           f"LLM-запросов mock (дельта): {llm_calls}"],
          res=res)
    db.close()
    return ok


def _v8_07(ctx: Ctx, books: dict[str, Path], lines: list[str]) -> bool:
    # --- 7: DRM EPUB
    _settings, db, repo, client, cfg, env_file = _profile(ctx.base, "v8_s7", ctx.server.url)
    worker = _worker(cfg, env_file, ctx.base / "v8_s7_w.log")
    before = len(ctx.server.requests_log())
    job_id = _create_job_cli(cfg, env_file, books["drm"], "en", ())
    if job_id:
        _confirm_needs(client, repo, job_id, "en")
        job = _wait_status(repo, job_id, {"FAILED", "COMPLETED", "COMPLETED_WITH_WARNINGS"}, 600)
    else:
        job = None
    # requests_log() — кумулятивный (все сценарии части); проверяем
    # дельту за сценарий
    llm_calls = len(ctx.server.requests_log()) - before
    rejected = (
        (job is None)
        or job.status.value == "FAILED"
        or "DRM" in (job.error_message or "")
    )
    ok = rejected and llm_calls == 0
    _scen(lines, "7", "DRM EPUB (META-INF/encryption.xml) → отказ до LLM", ok,
          [
              f"задание: {job_id}",
              f"статус: {job.status.value if job else 'не создано'}",
              f"причина: {job.error_message if job else '—'}",
              f"LLM-запросов mock (дельта): {llm_calls}",
          ])
    _stop_worker(worker)
    db.close()
    return ok


def _v8_08(ctx: Ctx, books: dict[str, Path], lines: list[str]) -> bool:
    # --- 8: ENOSPC (ulimit -f)
    r = _run_to_status(
        ctx, "v8_s8", books["big"], "en", {"FAILED"}, worker_block_kb=1024,
    )
    job = r["job"]
    reason = (job.error_message or "") if job else ""
    # ulimit -f (без root) симулирует «нет места» как EFBIG (errno 27,
    # "File too large"), а не ENOSPC (28) — оба это отказ файловой
    # системы на запись; реальный ENOSPC требует root/tmpfs-монтирования
    # (ограничение симуляции зафиксировано в DECISIONS). Критерий ТЗ:
    # FAILED с причиной as-is, повреждённый output не принят.
    ok = job is not None and job.status.value == "FAILED" and (
        "EFBIG" in reason or "Errno 27" in reason or "File too large" in reason
        or "ENOSPC" in reason or "Errno 28" in reason or "No space left" in reason
    )
    _scen(lines, "8", "нет места на диске (ulimit -f 1MB, глава >1MB; симуляция "
          "даёт EFBIG/27) → FAILED, причина as-is, output не принят",
          ok, [f"причина (verbatim): {reason}"])
    _stop_worker(r["worker"])
    r["db"].close()
    return ok


def _v8_09(ctx: Ctx, books: dict[str, Path], lines: list[str]) -> bool:
    # --- 9: отмена во время TRANSLATING
    # Задержка 2,0 c растягивает окно TRANSLATING (~20 c — мерялось
    # переходами БД). Live-поймать статус (поллинг 0,2 c), затем CLI
    # cancel (1–2 c на запуск) — команда попадает в ту же стадию; воркер
    # сходит кооперативно на границе группы (E3.8).
    ctx.server.set_scenario("ok")
    ctx.server.set_delay(2.0)
    r = _run_to_status(ctx, "v8_s9", books["slow"], "en", {"TRANSLATING"},
                       wait=300, live=True)
    job_id = r["job_id"]
    cancel_res = _cli(r["cfg"], r["env_file"], "job", "cancel", job_id)
    job = _wait_status(r["repo"], job_id, {"CANCELLED"}, timeout=120)
    jdir = r["jdir"]
    partial = sorted(p.name for p in jdir.glob("*.partial")) if jdir else []
    ok = (job is not None and job.status.value == "CANCELLED"
          and not (jdir / "target.epub").is_file())
    _scen(lines, "9", "job cancel во время TRANSLATING → CANCELLED, частичный помечен",
          ok, [
              f"cancel rc: {cancel_res.returncode}",
              f"статус: {job.status.value if job else None}",
              f".partial-файлы: {partial or 'нет'}",
              f"target.epub принят: {(jdir / 'target.epub').is_file() if jdir else False}",
          ])
    _stop_worker(r["worker"])
    r["db"].close()
    return ok


def _v8_10(ctx: Ctx, books: dict[str, Path], lines: list[str],
           keep: dict[str, Any]) -> bool:
    # --- 10: возобновление из INTERRUPTED (продолжение сценария 5)
    ctx.server.set_scenario("ok")
    ctx.server.set_delay(0.0)
    r = keep["s5"]["r"]
    res = _cli(r["cfg"], r["env_file"], "job", "resume", r["job_id"])
    job = _wait_status(r["repo"], r["job_id"],
                       {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED"}, timeout=1800)
    ok = job is not None and job.status.value in ("COMPLETED", "COMPLETED_WITH_WARNINGS")
    _scen(lines, "10", "resume из INTERRUPTED → COMPLETED", ok, [
        f"resume rc: {res.returncode}",
        f"статус: {job.status.value if job else None}",
        f"target.epub: {(r['jdir'] / 'target.epub').is_file()}",
    ])
    return ok


def _v8_11(ctx: Ctx, books: dict[str, Path], lines: list[str]) -> bool:
    # --- 11: нет EPUBCheck
    ctx.server.set_scenario("ok")
    ctx.server.set_delay(0.0)
    r = _run_to_status(
        ctx, "v8_s11", books["tiny"], "en",
        {"COMPLETED", "COMPLETED_WITH_WARNINGS"},
        jar=Path("/nonexistent/epubcheck.jar"),
    )
    job = r["job"]
    quality = _read_json(r["jdir"] / "quality.json") or {}
    warnings = quality.get("warnings") or []
    ok = (job is not None and job.status.value == "COMPLETED_WITH_WARNINGS"
          and any("VALIDATION_UNAVAILABLE" in w for w in warnings))
    _scen(lines, "11", "нет EPUBCheck (битый jar) → COMPLETED_WITH_WARNINGS "
          "VALIDATION_UNAVAILABLE",
          ok, [
              f"статус: {job.status.value if job else None}",
              "warnings (verbatim):",
              *_dump_lines(warnings),
          ])
    _stop_worker(r["worker"])
    r["db"].close()
    return ok


def _v8_12(ctx: Ctx, books: dict[str, Path], lines: list[str]) -> bool:
    # --- 12: нет spaCy-модели (пакет xx)
    ctx.server.set_scenario("ok")
    tmp_lang = ctx.base / "v8_s12_languages"
    shutil.copytree(ROOT / "config" / "languages", tmp_lang)
    xx_src = tmp_lang / "en"
    xx = tmp_lang / "xx"
    shutil.copytree(xx_src, xx)
    pack_text = (xx / "pack.yaml").read_text(encoding="utf-8")
    pack_text = pack_text.replace("code: en", "code: xx", 1)
    pack_text = pack_text.replace("name: English", "name: Testlang", 1)
    pack_text = pack_text.replace("spacy_model: en_core_web_sm",
                                  "spacy_model: xx_core_web_sm", 1)
    pack_text = pack_text.replace("aliases: [eng]", "aliases: []", 1)
    (xx / "pack.yaml").write_text(pack_text, encoding="utf-8")
    matrix = ctx.base / "v8_s12_matrix.yaml"
    matrix.write_text(
        (ROOT / "config" / "language_matrix.yaml")
        .read_text(encoding="utf-8")
        .replace("  - {source: en, target: ja}",
                 "  - {source: en, target: ja}\n  - {source: xx, target: ru}"),
        encoding="utf-8",
    )
    _settings, db, repo, client, cfg, env_file = _profile(
        ctx.base, "v8_s12", ctx.server.url,
        languages_root=tmp_lang, matrix=matrix,
    )
    res_langs = _cli(cfg, env_file, "languages")
    xx_line = next((ln for ln in res_langs.stdout.splitlines() if "xx" in ln), "нет строки xx")
    res_xx = _cli(cfg, env_file, "translate", str(books["tiny"]),
                  "--source", "xx", "--target", "ru", "--mode", "bilingual")
    worker = _worker(cfg, env_file, ctx.base / "v8_s12_w.log")
    job_id = _create_job_cli(cfg, env_file, books["tiny"], "en", ())
    if job_id:
        _confirm_needs(client, repo, job_id, "en")
        job = _wait_status(repo, job_id,
                           {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED"}, 900)
    else:
        job = None
    ok = (res_xx.returncode != 0
          and job is not None and job.status.value in ("COMPLETED", "COMPLETED_WITH_WARNINGS"))
    xx_err = (res_xx.stderr or res_xx.stdout).strip()
    xx_first = xx_err.splitlines()[0] if xx_err else ""
    _scen(lines, "12", "нет spaCy-модели (пакет xx) → пара unsupported, приложение работает",
          ok, [
              f"строка xx в CLI languages (verbatim): {xx_line.strip()}",
              f"translate xx→ru rc: {res_xx.returncode}",
              f"причина (verbatim): {xx_first}",
              f"контроль en→ru: {job.status.value if job else 'не создано'}",
          ])
    _stop_worker(worker)
    db.close()
    return ok


def _v8_13(ctx: Ctx, books: dict[str, Path], lines: list[str]) -> bool:
    # --- 13: CEFR-лексикон отсутствует
    ctx.server.set_scenario("ok")
    reg = load_registry(ROOT / "config" / "languages")
    en_pack = reg["en"]
    real_cefr_dir = ROOT / "config" / "languages" / "en" / "cefr"
    cefr_files = (
        sorted(p.name for p in real_cefr_dir.glob("*.json")) if real_cefr_dir.exists() else []
    )
    no_lex = "нет (D6: лексикон не включён в поставку)"
    label = language_cefr_label(en_pack)
    r = _run_to_status(
        ctx, "v8_s13", books["tiny"], "en",
        {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED"},
        extra=("--level", "Learning", "--cefr", "A1"),
    )
    job = r["job"]
    s1 = _read_json(r["jdir"] / "vocabulary_cards.json") or {}
    cards = s1.get("cards") or []
    derived = sum(1 for c in cards if c.get("cefr_source") == "derived")
    out_of_range = s1.get("cefr_out_of_range")
    ok = (job is not None
          and job.status.value in ("COMPLETED", "COMPLETED_WITH_WARNINGS")
          and (not cards or derived == len(cards)) and "Estimated CEFR level" in label)
    _scen(lines, "13",
          "CEFR-лексикон отсутствует (derived) → Estimated CEFR level, материал с меткой",
          ok, [
              f"pack.yaml: cefr_source: {en_pack.cefr_source}",
              f"файлы cefr/*.json в пакете en: {cefr_files or no_lex}",
              f"языковая подпись (единая точка cefr.label): {label!r}",
              f"карточек: {len(cards)}; cefr_source=derived: {derived}",
              f"cefr_out_of_range (user_cefr=A1): {out_of_range}",
          ])
    _stop_worker(r["worker"])
    r["db"].close()
    return ok


def _v8_14(ctx: Ctx, books: dict[str, Path], lines: list[str]) -> bool:
    # --- 14: нарушение глоссария в L3 (правка без термина)
    r = _run_to_status(
        ctx, "v8_s14", books["wexley8"], "en",
        {"COMPLETED", "COMPLETED_WITH_WARNINGS"}, scenario="glossary_fix_no_term",
        wait=2400,
    )
    job = r["job"]
    l3 = _read_json(r["jdir"] / "l3_fix_report.json") or {}
    l2 = _read_json(r["jdir"] / "glossary_consistency.json") or {}
    q = _read_json(r["jdir"] / "quality.json") or {}
    post = (q.get("glossary_consistency") or {})
    ok = (job is not None
          and job.status.value in ("COMPLETED", "COMPLETED_WITH_WARNINGS")
          and len(l2.get("mismatches") or []) > 0
          and (l3.get("attempted") or 0) >= 1
          and (l3.get("applied") or 0) == 0)
    l3_log_lines = [
        ln for ln in (r["jdir"] / "quality.log").read_text(encoding="utf-8").splitlines()
        if "L3" in ln or "Wexley" in ln
    ][:10]
    _scen(lines, "14", "L3: правка без требуемого термина (mock glossary_fix_no_term) → "
          "отклонена",
          ok, [
              f"статус: {job.status.value if job else None}",
              f"расхождений L2.6 (glossary_consistency.json): "
              f"{len(l2.get('mismatches') or [])}; L3 mismatches_input="
              f"{l3.get('mismatches_input')}",
              f"L3: attempted={l3.get('attempted')} applied={l3.get('applied')} "
              f"rejected={l3.get('rejected')}",
              f"после L3: mismatches={post.get('mismatches')} "
              f"consistent_share={post.get('consistent_share')}",
              "quality.log (строки L3, verbatim):",
              *[f"  | {ln}" for ln in l3_log_lines],
          ])
    _stop_worker(r["worker"])
    r["db"].close()
    return ok


def _v8_15(ctx: Ctx, books: dict[str, Path], lines: list[str]) -> bool:
    # --- 15: превышение доли правок (share cap)
    # ТЗ/L3-6: превышение fix_max_segment_share — задание FAILED (стоп
    # ДО LLM-вызовов), отчёт, target.epub неизменён (D63: AppError L3-6
    # до первой правки — ни l3bak, ни переписывания).
    r = _run_to_status(
        ctx, "v8_s15", books["wexley8"], "en",
        {"FAILED", "COMPLETED", "COMPLETED_WITH_WARNINGS"},
        scenario="glossary_inconsistent",
        yaml_extra="  fix_max_segment_share: 0.1\n", wait=2400,
    )
    job = r["job"]
    l3 = _read_json(r["jdir"] / "l3_fix_report.json") or {}
    l3bak = r["jdir"] / "target.epub.l3bak"
    ok = (job is not None
          and job.status.value == "FAILED"
          and l3.get("halted") is True
          and (l3.get("applied") or 0) == 0
          and not l3bak.is_file())
    _scen(lines, "15",
          "доля правимых > fix_max_segment_share (0.25 > 0.1) → задание FAILED "
          "(L3-6, стоп до LLM), отчёт, target.epub неизменён",
          ok, [
              f"статус: {job.status.value if job else None}",
              f"reason: {job.error_message if job else '—'}",
              f"L3: halted={l3.get('halted')} share={l3.get('fixable_segment_share')} "
              f"cap={l3.get('max_segment_share')} applied={l3.get('applied')}",
              f"l3bak создан: {l3bak.is_file()} (без правок — без бэкапа)",
          ])
    _stop_worker(r["worker"])
    r["db"].close()
    return ok


def _v8_16(ctx: Ctx, books: dict[str, Path], lines: list[str]) -> bool:
    # --- 16: kill -9 в окне L3 (ребилд после правок)
    # Задержка 1.0 c растягивает правки (glossary_fix, по одной на
    # сегмент: 24 расхождения → ~24 c стадии). l3bak пишется сразу
    # перед ребилдом (D63) — окно kill = [создание l3bak, os.replace];
    # статус в это время ещё GLOSSARY_FIX (стадия завершится только
    # после os.replace). GLOSSARY_FIX ловится LIVE (поллинг 0,2 c),
    # затем kill-луп ждёт появления l3bak. Критерий ТЗ: «резервная
    # копия цела, повреждённого принятого файла нет» + восстановление.
    ctx.server.set_delay(1.0)
    r = _run_to_status(
        ctx, "v8_s16", books["wexley8"], "en", {"GLOSSARY_FIX"},
        scenario="glossary_inconsistent", wait=2400, live=True,
    )
    job_id = r["job_id"]
    jdir = r["jdir"]
    terminal = {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED"}
    deadline = time.monotonic() + 300
    killed = False
    job_now = None
    while time.monotonic() < deadline:
        job_now = r["repo"].get(job_id)
        if job_now is None:
            time.sleep(0.2)
            continue
        if job_now.status.value in terminal:
            break  # окно L3 прошло — сценарий условие не создал
        if job_now.status.value == "GLOSSARY_FIX" and (jdir / "target.epub.l3bak").is_file():
            os.kill(r["worker"].pid, signal.SIGKILL)
            r["worker"].wait(timeout=30)
            killed = True
            break
        time.sleep(0.2)
    if not killed:
        _scen(lines, "16", "kill -9 в окне L3 — окно не поймано (честный FAIL)", False,
              [f"статус: {job_now.status.value if job_now else None}"])
        ctx.server.set_delay(0.0)
        _stop_worker(r["worker"])
        r["db"].close()
        return False
    worker2 = _worker(r["cfg"], r["env_file"], ctx.base / "v8_s16_w2.log")
    ctx.server.set_delay(0.0)
    # INTERRUPTED — из истории переходов: live-окно субсекундное
    # (worker2 немедленно авто-резюмирует после пометки)
    saw_interrupted = _wait_interrupted_transition(r["repo"], job_id, timeout=180)
    _cli(r["cfg"], r["env_file"], "job", "resume", job_id)
    job = _wait_status(r["repo"], job_id,
                       {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED"}, 2400)
    l3bak_ok = (jdir / "target.epub.l3bak").is_file() and zipfile.is_zipfile(
        jdir / "target.epub.l3bak"
    )
    target_ok = not (jdir / "target.epub").is_file() or zipfile.is_zipfile(
        jdir / "target.epub"
    )
    l3 = _read_json(jdir / "l3_fix_report.json") or {}
    ok = (saw_interrupted
          and job is not None
          and job.status.value in ("COMPLETED", "COMPLETED_WITH_WARNINGS")
          and l3bak_ok and target_ok)
    _scen(lines, "16", "kill -9 в окне ребилда L3 → INTERRUPTED, восстановление; "
          "резервная копия цела, повреждённого принятого файла нет",
          ok, [
              "момент kill: статус GLOSSARY_FIX, l3bak на месте (ребилд идёт)",
              f"INTERRUPTED в истории переходов БД: {saw_interrupted}",
              f"финал: {job.status.value if job else None}",
              f"l3bak валидный ZIP: {l3bak_ok}",
              f"target.epub не повреждён (валидный ZIP, если есть): {target_ok}",
              f"L3 (отчёт): applied={l3.get('applied')} halted={l3.get('halted')}",
          ])
    _stop_worker(worker2)
    r["db"].close()
    return ok


def part_v8(ctx: Ctx, books: dict[str, Path]) -> bool:
    """V1.8: 16 сценариев отказов — каждый отдельной функцией _v8_NN."""
    lines = [
        header(),
        "K: V1-8 — 16 сценариев отказов (mock-сценарии, kill -9, ulimit ENOSPC,",
        "DRM, битый jar, выключенный spaCy); каждый — с verbatim-фактами",
        "",
    ]
    ctx.server.set_scenario("ok")
    ctx.server.set_delay(0.0)
    keep: dict[str, Any] = {}
    steps: list[tuple[str, Callable[[], bool]]] = [
        ("1 таймаут LLM", lambda: _v8_01(ctx, books, lines)),
        ("2 HTTP 500", lambda: _v8_02(ctx, books, lines)),
        ("3 пустой ответ", lambda: _v8_03(ctx, books, lines)),
        ("4 битый JSON", lambda: _v8_04(ctx, books, lines)),
        ("5 перезапуск", lambda: _v8_05(ctx, books, lines, keep)),
        ("6 не-ZIP", lambda: _v8_06(ctx, books, lines)),
        ("7 DRM", lambda: _v8_07(ctx, books, lines)),
        ("8 ENOSPC", lambda: _v8_08(ctx, books, lines)),
        ("9 отмена", lambda: _v8_09(ctx, books, lines)),
        ("10 возобновление", lambda: _v8_10(ctx, books, lines, keep)),
        ("11 нет EPUBCheck", lambda: _v8_11(ctx, books, lines)),
        ("12 нет spaCy", lambda: _v8_12(ctx, books, lines)),
        ("13 CEFR", lambda: _v8_13(ctx, books, lines)),
        ("14 L3-нарушение", lambda: _v8_14(ctx, books, lines)),
        ("15 share cap", lambda: _v8_15(ctx, books, lines)),
        ("16 kill в L3", lambda: _v8_16(ctx, books, lines)),
    ]
    results: list[tuple[str, bool]] = []
    for name, step in steps:
        results.append((name, step()))
    if "s5" in keep:  # закрыть worker2 сценария 5 (задание уже COMPLETED)
        _stop_worker(keep["s5"]["worker2"])
        keep["s5"]["r"]["db"].close()
    bad = [name for name, ok in results if not ok]
    lines.append("")
    lines.append(f"сценариев: {len(results)}/16; не сошлись: {bad if bad else 'нет (0)'}")
    ok_all = len(bad) == 0
    lines.append(f"\nВЕРДИКТ: V1-8 (16/16 сценариев отказов) — {'PASS' if ok_all else 'FAIL'}")
    evidence("v1-8-scenarios.txt", "\n".join(lines))
    return ok_all


# ------------------------------------------------------------------ V1.8a


def part_v8a(ctx: Ctx, books: dict[str, Path]) -> bool:
    lines = [
        header(),
        "K: V1-8a — терминология: 8 глав, повторяющееся собственное имя (Wexley),",
        "30+ терминов глоссария; L2 до/после L3, quality.log vs diff, повтор L3 = 0",
        "",
    ]
    run = _run_to_status(
        ctx, "v8a", books["wexley8"], "en",
        {"COMPLETED", "COMPLETED_WITH_WARNINGS"}, scenario="glossary_inconsistent",
        wait=2400,
    )
    try:
        job, jdir = run["job"], run["jdir"]
        if job is None or job.status.value not in ("COMPLETED", "COMPLETED_WITH_WARNINGS"):
            lines.append(f"прогон не завершился: {job.status.value if job else 'NO_JOB'}")
            evidence("v1-8a-terminology.txt", "\n".join(lines))
            return False
        l2 = _read_json(jdir / "glossary_consistency.json") or {}
        l3 = _read_json(jdir / "l3_fix_report.json") or {}
        q = _read_json(jdir / "quality.json") or {}
        post = q.get("glossary_consistency") or {}
        tr_glossary = (
            _read_json(jdir / "translation_result.json") or {}
        ).get("glossary") or {}
        quality_log = (jdir / "quality.log").read_text(encoding="utf-8")
        # Фактические правки — строки «glossary_fix». Фильтр «Wexley»
        # ловил ещё строку L2 QC-предупреждения, перечисляющую сегменты
        # Wexley (25 против 24 фактических правок).
        fix_lines = [ln for ln in quality_log.splitlines() if "glossary_fix" in ln]
        changed = _changed_paragraphs(
            jdir / "target.epub.l3bak", jdir / "target.epub", ctx.base / "v8a_diff"
        )
        # повтор L3: та же стадия на исправленной книге → 0 расхождений → 0 правок
        pipeline = Pipeline(run["settings"])
        pipeline.run_glossary_fix(run["repo"], run["repo"].require(job.id))
        l3_rerun = _read_json(jdir / "l3_fix_report.json") or {}

        checks = [
            (f"книга: 8 глав × 12 абзацев; сегментов {l3.get('total_segments')}",
             (l3.get("total_segments") or 0) >= 8 * 12),
            (f"терминов глоссария (total/kept): "
             f"{tr_glossary.get('total_terms')}/{tr_glossary.get('kept_terms')} (ТЗ: ≥ 30)",
             (tr_glossary.get("kept_terms") or 0) >= 30),
            (f"расхождения L2 до L3 (L3 mismatches_input): "
             f"{l3.get('mismatches_input')} (Wexley в 3×8=24 сегментах; "
             f"glossary_consistency.json: {len(l2.get('mismatches') or [])})",
             (l3.get("mismatches_input") or 0) >= 1),
            (f"L3: attempted={l3.get('attempted')} applied={l3.get('applied')} "
             f"rejected={l3.get('rejected')} halted={l3.get('halted')}",
             (l3.get("applied") or 0) >= 1 and l3.get("halted") is False),
            (f"согласованность после L3: consistent_share={post.get('consistent_share')} "
             f"(ТЗ: ≥ 99%, L3-1)",
             (post.get("consistent_share") or 0) >= 0.99),
            (f"расхождений после L3: {post.get('mismatches')}",
             (post.get("mismatches") or 0) == 0),
            (f"quality.log: строк правок Wexley = {len(fix_lines)}; "
             f"фактически изменено <p>-блоков (l3bak→target) = {changed}; diff = 0",
             len(fix_lines) == changed),
            (f"повторный L3 (на исправленной книге): attempted={l3_rerun.get('attempted')} "
             f"applied={l3_rerun.get('applied')} (ТЗ: 0 правок)",
             (l3_rerun.get("applied") or 0) == 0),
        ]
        lines.append("Метрики:")
        lines += _dump_lines(_subset(l2, ("total_terms", "kept_terms", "mismatches")))
        lines.append("")
        lines += ["L3 (первый прогон, verbatim сводка):"]
        lines += _dump_lines(_subset(l3, (
            "halted", "fixable_segment_share", "total_segments", "mismatches_input",
            "attempted", "applied", "rejected", "backup",
        )))
        lines.append("")
        lines += ["quality.log (строки правок, verbatim):"]
        lines += [f"  | {line}" for line in fix_lines]
        lines.append("")
        lines += ["L3 (повторный прогон, verbatim сводка):"]
        lines += _dump_lines(_subset(l3_rerun, (
            "halted", "mismatches_input", "attempted", "applied", "rejected",
        )))
        lines.append("")
        lines.append("Пороговая проверка:")
        for what, okc in checks:
            lines.append(f"  {'OK' if okc else 'НЕ СОВПАЛ'}: {what}")
        ok = all(okc for _, okc in checks)
        lines.append(f"\nВЕРДИКТ: V1-8a (терминология L2/L3) — {'PASS' if ok else 'FAIL'}")
        evidence("v1-8a-terminology.txt", "\n".join(lines))
        return ok
    finally:
        _stop_worker(run["worker"])
        run["db"].close()


# ------------------------------------------------------------------ V1.9


def _v9_wait_kill(
    repo: JobRepository, client: TestClient, job_id: str
) -> tuple[bool, Any]:
    """Опрос 0.2 c: подтверждение L0.5, точка kill (TRANSLATING, progress ≥ 0.3
    + сдвиг 0.5 c в fill-фазу), терминал (kill не выполнен). Возврат
    (kill_point, job).

    on_progress движка бьёт на границах «глав» (TOC 5%, meta 5%, главы
    90% / N): в момент видимого скачка в полёте translation-запрос новой
    главы (mock 0.3 c). Кэш движка коммитится только после успешного
    ответа — повторный слитый запрос — целый групповой перевод (8/24
    сегмента; окно задокументировано в CONTROL V1.md). Сдвиг 0.5 c: kill по
    блоку fill-запроса — гранулярность, которую механизм кэша гарантирует.
    """
    terminal = ("COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED")
    deadline = time.monotonic() + 900
    job = None
    while time.monotonic() < deadline:
        job = repo.get(job_id)
        if job is None:
            time.sleep(0.2)
            continue
        st = job.status.value
        if st == "NEEDS_CONFIRMATION":
            client.post(f"/api/jobs/{job_id}/confirm",
                        json={"source_language": "en"})
        elif st in terminal:
            return False, job
        elif st == "TRANSLATING" and job.progress >= 0.3:
            time.sleep(0.5)
            job = repo.get(job_id)
            if job is not None and job.status.value not in terminal:
                return True, job
            return False, job
        time.sleep(0.2)
    return False, job


def _v9_resends(requests: list[dict[str, Any]], from_idx: int) -> tuple[int, int]:
    """Дубликаты промптов в requests_log с from_idx (пересылка при resume)."""
    seen: set[str] = set()
    resends = 0
    resend_tokens = 0
    for req in requests[from_idx:]:
        key = f"{req.get('kind')}:{req.get('user_sha256')}"
        if key in seen:
            resends += 1
            resend_tokens += (
                int(req.get("prompt_tokens") or 0)
                + int(req.get("completion_tokens") or 0)
            )
        seen.add(key)
    return resends, resend_tokens


def _v9_kill_and_resume(ctx: Ctx, run: dict[str, Any], job: Any) -> dict[str, Any]:
    """kill -9 → worker2 → INTERRUPTED → resume → финал + ground truth
    пересылки. Возврат dict: x, progress_at_kill, saw_interrupted, res,
    job_f, resends, resend_tokens."""
    x = job.tokens_translation + job.tokens_fill
    progress_at_kill = job.progress
    os.kill(run["worker"].pid, signal.SIGKILL)
    run["worker"].wait(timeout=30)
    worker2 = _worker(run["cfg"], run["env_file"], ctx.base / "v9_w2.log")
    # INTERRUPTED — из истории переходов БД: live-окно субсекундное
    # (worker2 немедленно авто-резюмирует после пометки)
    saw_interrupted = _wait_interrupted_transition(run["repo"], run["job_id"],
                                                   timeout=180)
    res = _cli(run["cfg"], run["env_file"], "job", "resume", run["job_id"])
    job_f = _wait_status(
        run["repo"], run["job_id"],
        {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED"}, 2400
    )
    _stop_worker(worker2)
    ctx.server.set_delay(0.0)
    resends, resend_tokens = _v9_resends(
        ctx.server.requests_log(), run["log_at_run_start"]
    )
    return {
        "x": x, "progress_at_kill": progress_at_kill,
        "saw_interrupted": saw_interrupted,
        "res": res, "job_f": job_f, "resends": resends,
        "resend_tokens": resend_tokens,
    }


def _v9_verdict(*, cfg: Path, env_file: Path, jdir: Path, z: int,
                kr: dict[str, Any], lines: list[str]) -> bool:
    """Проверки и метрики V1.9 → v1-9-restart.txt; возвращает вердикт.

    BД-метрика E3-4 (прирост total_tokens) при kill внутри группы ≈ 0:
    токены незавершённой группы неflushed (flush — на границах групп) и
    теряются при SIGKILL, а завершённые запросы — cache-hit при resume.
    Факт пересылки in-flight запроса — отдельная ground-truth метрика."""
    x = kr["x"]
    progress_at_kill = kr["progress_at_kill"]
    saw_interrupted = kr["saw_interrupted"]
    res = kr["res"]
    job_f = kr["job_f"]
    resends = kr["resends"]
    resend_tokens = kr["resend_tokens"]
    final_tokens = (job_f.tokens_translation + job_f.tokens_fill) if job_f else 0
    y = final_tokens - x
    share = max(0, x + y - z) / z
    val_res = _cli(cfg, env_file, "validate", str(jdir / "target.epub")) \
        if (jdir / "target.epub").is_file() else None
    checks = [
        (f"Z (эталон, translation+fill): {z} токенов", z > 0),
        (f"X на момент kill (progress={progress_at_kill:.2f}): {x} токенов", True),
        (f"Y (финал − X): {y} токенов", True),
        (f"доля повторного перевода max(0, X+Y−Z)/Z = {share:.4%} ≤ 5% (E3-4)", share <= 0.05),
        (f"INTERRUPTED зафиксирован (история переходов БД): {saw_interrupted}",
         saw_interrupted),
        (f"resume rc: {res.returncode}; финал: {job_f.status.value if job_f else None}",
         job_f is not None and job_f.status.value in ("COMPLETED", "COMPLETED_WITH_WARNINGS")),
        ("задание не потеряно: строка в БД, стадийные результаты на месте",
         job_f is not None and (jdir / "target.epub").is_file()),
        (
            "финальный EPUB валиден (EPUBCheck 0 FATAL/ERROR)",
            val_res is not None and val_res.returncode == 0
            and "0 FATAL, 0 ERROR" in val_res.stdout,
        ),
    ]
    lines.append("Метрики:")
    lines += _dump_lines({
        "Z": z, "X": x, "progress_at_kill": round(progress_at_kill, 3),
        "Y": y, "final_tokens": final_tokens, "share": round(share, 6),
        "resume_rc": res.returncode,
        "final_status": job_f.status.value if job_f else None,
        "переслано_запросов (mock requests_log, дубликаты промптов)": resends,
        "переслано_токенов (ground truth; БД их не видит при неflushed главе)":
            resend_tokens,
    })
    if val_res is not None:
        lines.append("")
        lines.append("CLI validate target.epub (verbatim):")
        lines += [f"  | {line}" for line in val_res.stdout.splitlines()]
    lines.append("")
    lines.append("Пороговая проверка:")
    for what, okc in checks:
        lines.append(f"  {'OK' if okc else 'НЕ СОВПАЛ'}: {what}")
    ok = all(okc for _, okc in checks)
    lines.append(f"\nВЕРДИКТ: V1-9 (перезапуск, E3-4 ≤ 5%) — {'PASS' if ok else 'FAIL'}")
    evidence("v1-9-restart.txt", "\n".join(lines))
    return ok


def part_v9(ctx: Ctx, books: dict[str, Path]) -> bool:
    lines = [
        header(),
        "K: V1-9 — перезапуск: старт → частичный прогон → kill -9 → старт → resume;",
        "прирост токенов ≤ 5% остатка (E3-4); задание не потеряно; EPUB валиден",
        "",
    ]
    ctx.server.set_scenario("ok")
    ctx.server.set_delay(0.3)
    # эталонный непрерывный прогон → Z
    zrun = _run_to_status(
        ctx, "v9_ref", books["slow"], "en", {"COMPLETED", "COMPLETED_WITH_WARNINGS"},
        wait=2400,
    )
    zjob = zrun["job"]
    z = (zjob.tokens_translation + zjob.tokens_fill) if zjob else 0
    _stop_worker(zrun["worker"])
    zrun["db"].close()
    if z <= 0:
        lines.append("эталонный прогон не дал токенов (зависание) — FAIL")
        evidence("v1-9-restart.txt", "\n".join(lines))
        return False
    # прогон с kill на ~30%. Немедленный опрос (0.2 c): стадия translating
    # длится несколько секунд (mock 0.3 c/запрос), любой начальный wait
    # пропускает всё окно (de-facto: ждём 900 c, задание уже в терминале).
    settings, db, repo, client, cfg, env_file = _profile(ctx.base, "v9", ctx.server.url)
    worker = _worker(cfg, env_file, ctx.base / "v9_w1.log")
    run = {
        "settings": settings, "db": db, "repo": repo, "client": client,
        "cfg": cfg, "env_file": env_file, "worker": worker,
    }
    job_id = _create_job_cli(cfg, env_file, books["slow"], "en",
                             ("--level", "Learning"))
    run["job_id"] = job_id
    run["jdir"] = settings.job_dir(job_id) if job_id else None
    # ground truth пересыла считается от начала прогона (эталонный прогон
    # исключён индексом); БД-токены пересылку не видят, если глава была
    # неflushed на момент kill (flush — на границах глав) — поэтому факт
    # пересыла отдельной метрикой, без порога.
    run["log_at_run_start"] = len(ctx.server.requests_log())
    if job_id is not None:
        kill_point, job = _v9_wait_kill(repo, client, job_id)
    else:
        kill_point, job = False, None
    if job_id is None or job is None:
        lines.append("CLI не создал задание — FAIL")
        evidence("v1-9-restart.txt", "\n".join(lines))
        _stop_worker(worker)
        db.close()
        return False
    if not kill_point:
        lines.append(
            "задание достигло терминального статуса "
            f"({job.status.value}, progress {job.progress}) до точки kill — "
            "стадия translating короче сценария; kill не выполнен — FAIL"
        )
        evidence("v1-9-restart.txt", "\n".join(lines))
        _stop_worker(worker)
        db.close()
        return False
    ok = _v9_verdict(
        cfg=cfg, env_file=env_file, jdir=settings.job_dir(job_id), z=z,
        kr=_v9_kill_and_resume(ctx, run, job), lines=lines,
    )
    db.close()
    return ok


# ------------------------------------------------------------------ main


PARTS = {
    "v5": part_v5,
    "v6": part_v6,
    "v7": part_v7,
    "v8": part_v8,
    "v8a": part_v8a,
    "v9": part_v9,
}


def main() -> int:
    parts = [arg for arg in sys.argv[1:] if arg in PARTS] or list(PARTS)
    base = Path(tempfile.mkdtemp(prefix="v1-evidence-"))
    ctx = Ctx(base=base, server=MockLLMServer())
    ctx.server.start()
    try:
        print(f"[v1-evidence] книги → {base / 'books'}")
        books = _books(ctx)
        for name in parts:
            print(f"[v1-evidence] часть {name} …")
            t0 = time.monotonic()
            try:
                ok = PARTS[name](ctx, books)
                ctx.results[name] = (ok, "")
            except Exception as exc:
                ctx.results[name] = (False, f"{type(exc).__name__}: {exc}")
                print(f"[v1-evidence] {name}: ОШИБКА: {exc}", file=sys.stderr)
            print(f"[v1-evidence] {name}: {'PASS' if ctx.results[name][0] else 'FAIL'} "
                  f"({time.monotonic() - t0:.0f} c)")
        content = "\n".join(
            [
                header(),
                "K: V1 — сводка частей программы приёмки (детали — по файлам)",
                "",
                *[f"{name}: {'PASS' if ok else 'FAIL'}" + (f" — {detail}" if detail else "")
                  for name, (ok, detail) in sorted(ctx.results.items())],
                "",
                f"ВЕРДИКТ: V1 (части) — "
                f"{'PASS' if all(ok for ok, _ in ctx.results.values()) else 'FAIL'}",
            ]
        )
        evidence("v1-evidence-summary.txt", content)
        return 0 if all(ok for ok, _ in ctx.results.values()) else 1
    finally:
        _kill_all(_WORKERS)
        ctx.server.stop()
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
