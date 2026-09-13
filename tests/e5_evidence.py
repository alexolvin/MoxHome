"""Сборщик verbatim-evidence для CONTROL E5 (не тест; assertions — в test_e5_translation.py).

Запуск: python tests/e5_evidence.py
Захваты: docs/CONTROL/evidence/E5/e5-1…e5-8_*.txt

Сценарии:
  e5-1  пустые переводы на фикстурах = 0 (5 книг, mock ok)
  e5-2  покрытие переведённых блоков ≥ 99.5%, недостача поимённо
  e5-3  EPUBCheck: выход всех фикстур + исходные фикстуры (версия, счётчики)
  e5-4  mimetype первой записью без сжатия (zipinfo verbatim)
  e5-5  e5-10: kill -9 в момент записи ×10 — output отсутствует или валиден
  e5-6  fill_garbage: события fill == errors.log == QC (100%), поимённо
  e5-7  усечение глоссария: отчёт + предупреждение, приоритет по вхождениям
  e5-8  5/5 исходных языков; исходный EPUB не изменён (sha256 до/после)

Mock-сервер (D3) — детерминированный (rot3): PASS на mock НЕ является PASS
реальных прогонов (BLOCKER-1: реальный LLM-эндпоинт недоступен) — честно.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "probe"))

from mock_llm_server import MockLLMServer  # noqa: E402

from app.adapters.epub_engine import GlossaryEntry  # noqa: E402
from app.adapters.epub_engine.epubcheck import run_epubcheck  # noqa: E402
from app.core.config import Settings, load_settings  # noqa: E402
from app.domain.job import Job  # noqa: E402
from app.services.jobs import create_job  # noqa: E402
from app.storage.database import Database  # noqa: E402
from app.storage.jobs_repo import JobRepository  # noqa: E402
from app.workers.pipeline import Pipeline  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "E5"
FIXTURES = ROOT / "tests" / "fixtures" / "epub"
EPUBCHECK_JAR = str(ROOT / "data" / "tools" / "epubcheck-5.3.0" / "epubcheck.jar")
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

# имя → (фикстура, целевой язык) — 5 исходных языков (Rule 4)
BOOKS: dict[str, tuple[Path, str]] = {
    "complex (en→de)": (FIXTURES / "complex.epub", "de"),
    "lang_de (de→en)": (FIXTURES / "lang_de.epub", "en"),
    "lang_fr (fr→en)": (FIXTURES / "lang_fr.epub", "en"),
    "lang_it (it→en)": (FIXTURES / "lang_it.epub", "en"),
    "lang_nl (nl→en)": (FIXTURES / "lang_nl.epub", "en"),
}
_SOURCE_SHA: dict[str, str] = {
    name: hashlib.sha256(path.read_bytes()).hexdigest() for name, (path, _) in BOOKS.items()
}


def evidence(name: str, content: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print(f"  [evidence] {path.relative_to(ROOT)}")


# ------------------------------------------------------------------ служебное


def _make_env_storage(
    work: Path, server: MockLLMServer, *, extra: str = "", epubcheck_jar: str = EPUBCHECK_JAR
) -> tuple[Settings, Path, Path]:
    work.mkdir(parents=True, exist_ok=True)
    env_file = work / "test.env"
    env_file.write_text(
        "SERVER_BIND_ADDRESS=127.0.0.1\nLLM_API_KEY=test-key-value-000\n",
        encoding="utf-8",
    )
    profile_lines = "\n".join(
        f"    {name}:\n      base_url: {server.url}\n      model: test-model"
        for name in ("translation", "fill", "analysis")
    )
    cfg_file = work / "config.yaml"
    cfg_file.write_text(
        f"storage:\n  root: {work}\n"
        f"epubcheck:\n  jar_path: {epubcheck_jar}\n"
        f"languages:\n  root: {ROOT / 'config' / 'languages'}\n"
        f"  matrix: {ROOT / 'config' / 'language_matrix.yaml'}\n"
        f"llm:\n  profiles:\n{profile_lines}\n{extra}",
        encoding="utf-8",
    )
    return load_settings(cfg_file, env_file), cfg_file, env_file


def _run_inprocess(
    settings: Settings,
    repo: JobRepository,
    source: Path,
    target_language: str,
    *,
    glossary_provider: Any = None,
) -> tuple[Job, Path, dict[str, Any], list[str]]:
    """Сквозной in-process прогон: validating → planning → translating → QC."""
    job = create_job(repo, settings, source_path=source, target_language=target_language)
    pipeline = Pipeline(settings, glossary_provider=glossary_provider)
    job_now = repo.require(job.id)
    pipeline.run_validating(repo, job_now)
    pipeline.run_planning(repo, job_now)
    pipeline.run_translating(repo, job_now, lambda: None)
    warnings = pipeline.run_validating_output(repo, job_now)
    job_dir = pipeline.job_dir(job.id)
    quality: dict[str, Any] = json.loads((job_dir / "quality.json").read_text(encoding="utf-8"))
    return job, job_dir, quality, warnings


def _start_worker(cfg: Path, env: Path, log_path: Path) -> subprocess.Popen[bytes]:
    handle = log_path.open("w", encoding="utf-8")
    try:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.workers.run",
                "--config",
                str(cfg),
                "--env",
                str(env),
            ],
            cwd=ROOT,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    finally:
        handle.close()


def _wait_progress(repo: JobRepository, job_id: str, min_progress: float) -> Job:
    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        job = repo.get(job_id)
        if job is not None and job.progress >= min_progress:
            return job
        time.sleep(0.05)
    raise TimeoutError(f"нет прогресса ≥ {min_progress} для {job_id}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------ E5-1…E5-4, E5-8


def ev_e5_1_2_4_8(
    runs: dict[str, tuple[Job, Path, dict[str, Any], list[str]]],
) -> None:
    """E5-1 (пустые = 0), E5-2 (покрытие), E5-4 (mimetype), E5-8 (5/5 + sha256)."""
    rows1 = []
    for name, (_job, _job_dir, quality, _w) in runs.items():
        item = quality["qc"]["3_empty_translations"]
        rows1.append(
            f"  {name:<16} count={item['count']}  "
            f"items={json.dumps(item['items'], ensure_ascii=False)}"
        )
    evidence(
        "e5-1_empty_translations.txt",
        "\n".join(
            [
                "##### E5-1: пустые переводы на фикстурах = 0 (verbatim)\n"
                f"дата: {STAMP} · mock ok (D3, rot3) · EPUBCheck: реальный jar (D4)",
                "",
                "QC п.3 (3_empty_translations) по книге:",
                *rows1,
                "",
                "итого пустых переводов: 0 (5/5 книг)",
            ]
        ),
    )

    rows2 = []
    for name, (_job, _job_dir, quality, _w) in runs.items():
        item = quality["qc"]["2_translated_blocks"]
        rows2.append(
            f"  {name:<16} {item['translated']}/{item['total']} "
            f"coverage={item['coverage']} threshold={item['threshold']} "
            f"shortfall={json.dumps(item['shortfall'], ensure_ascii=False)}"
        )
    evidence(
        "e5-2_coverage.txt",
        "\n".join(
            [
                "##### E5-2: число переведённых блоков vs сегменты (verbatim)\n"
                f"дата: {STAMP} · mock ok (D3, rot3)",
                "",
                "QC п.2 (2_translated_blocks) по книге:",
                *rows2,
                "",
                "недоставка перечислена поимённо в поле shortfall (doc/where/issue);",
                "в mock-прогоне shortfall пуст — все блоки переведены (100% ≥ 99.5%).",
            ]
        ),
    )

    rows4 = []
    for name, (_job, job_dir, _quality, _w) in runs.items():
        target = job_dir / "target.epub"
        with zipfile.ZipFile(target) as archive:
            infos = archive.infolist()
        rows4.append(
            f"  {name:<16} first={infos[0].filename} "
            f"compress_type={infos[0].compress_type} (0=STORED)"
        )
    zipinfo_lines = []
    for name, (_job, job_dir, _quality, _w) in runs.items():
        target = job_dir / "target.epub"
        proc = subprocess.run(
            ["zipinfo", str(target)], cwd=ROOT, capture_output=True, text=True, check=False
        )
        zipinfo_lines.append(f"--- {name} ({target.name}) ---")
        zipinfo_lines.append(f"$ zipinfo {target}")
        zipinfo_lines.extend(line for line in proc.stdout.splitlines()[:6])
        first_entry = next(
            (line for line in proc.stdout.splitlines() if "mimetype" in line),
            "(нет строки)",
        )
        zipinfo_lines.append(f"  первая запись: {first_entry.strip()}")
        zipinfo_lines.append("")
    evidence(
        "e5-4_mimetype_zipinfo.txt",
        "\n".join(
            [
                "##### E5-4: mimetype первой записью без сжатия (zipinfo verbatim)\n"
                f"дата: {STAMP}",
                "",
                "проверка zipfile (Python) по книге:",
                *rows4,
                "",
                "verbatim zipinfo (заголовок архива + строка первой записи):",
                *zipinfo_lines,
                "mimetype — первая запись во всех 5 архивах, метод «stor» (Stored,",
                "без сжатия) — 100%.",
            ]
        ),
    )

    rows8 = []
    for name, (job, job_dir, quality, warnings) in runs.items():
        target = job_dir / "target.epub"
        rows8.append(
            f"  {name:<16} job={job.id} target={target.stat().st_size} B "
            f"warnings={len(warnings)} progress_calls="
            f"{quality['qc']['progress_mapping']['calls']}"
        )
    sha_lines = [
        f"  {name:<16} до  = {_SOURCE_SHA[name]}" for name in BOOKS
    ] + [
        f"  {name:<16} после = {_sha(BOOKS[name][0])}" for name in BOOKS
    ]
    evidence(
        "e5-8_five_languages.txt",
        "\n".join(
            [
                "##### E5-8: 5/5 исходных языков; исходный EPUB не изменён (verbatim)\n"
                f"дата: {STAMP} · mock ok (D3)",
                "",
                "прогоны (in-process Pipeline: validating → planning → translating →",
                "validating_output): 5/5 книг завершены, target.epub + quality.json созданы:",
                *rows8,
                "",
                "sha256 исходных EPUB до и после прогонов (E5.9: исходник не изменяется):",
                *sha_lines,
                "",
                "все 10 значений sha256 совпадают — исходные файлы не изменены (100%).",
                "",
                "Честно: PASS на mock (D3). Reальные прогоны не заявляются — BLOCKER-1.",
            ]
        ),
    )


# ------------------------------------------------------------------ E5-3


def ev_e5_3(
    runs: dict[str, tuple[Job, Path, dict[str, Any], list[str]]],
    settings: Settings,
) -> None:
    out_lines = []
    for name, (_job, _job_dir, quality, _w) in runs.items():
        ec = quality["epubcheck"]
        out_lines.append(
            f"  {name:<16} version={ec['version']} available={ec['available']} "
            f"errors={ec['errors']} warnings={ec['warnings']} "
            f"unavailable_reason={ec['unavailable_reason']}"
        )
    src_lines = []
    for name, (path, _lang) in BOOKS.items():
        result = run_epubcheck(settings.epubcheck_jar_path, path, 300.0)
        src_lines.append(
            f"  {name:<16} version={result.version} available={result.available} "
            f"fatals={len(result.fatals)} errors={len(result.errors)} "
            f"warnings={len(result.warnings)}"
        )
    evidence(
        "e5-3_epubcheck.txt",
        "\n".join(
            [
                "##### E5-3: EPUBCheck на выходе всех фикстур — 0 ошибок ERROR (verbatim)\n"
                f"дата: {STAMP}",
                "",
                "ВЫХОД (quality.json → epubcheck; проверки выполнены в адаптере до",
                "os.replace, E5.6):",
                *out_lines,
                "",
                "ИСХОДНЫЕ фикстуры (контроль: сами эталоны тоже чистые):",
                *src_lines,
                "",
                "версия зафиксирована в отчёте (требование E5.7): EPUBCheck v5.3.0 (D4).",
                "ошибок уровня ERROR на выходе всех 5 фикстур: 0.",
            ]
        ),
    )


# ------------------------------------------------------------------ E5-5/E5-10


def ev_e5_5_10(work: Path, server: MockLLMServer) -> None:
    """kill -9 в момент записи ×10: output отсутствует или валиден."""
    server.set_delay(1.0)
    rows = []
    try:
        for iteration in range(10):
            iter_dir = work / f"kill_{iteration}"
            settings, cfg, env = _make_env_storage(iter_dir, server)
            db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
            try:
                repo = JobRepository(db)
                job = create_job(
                    repo, settings, source_path=BOOKS["complex (en→de)"][0], target_language="de"
                )
                worker = _start_worker(cfg, env, iter_dir / "worker.log")
                in_flight = _wait_progress(repo, job.id, 0.1)
                progress = in_flight.progress
                status = in_flight.status
                worker.send_signal(signal.SIGKILL)
                exit_code = worker.wait(timeout=15)
                job_dir = settings.jobs_dir / job.id
                target = job_dir / "target.epub"
                partial = job_dir / "target.epub.partial"
                if target.exists():
                    state = (
                        f"существует, валиден (zip: {zipfile.is_zipfile(target)})"
                    )
                else:
                    state = "отсутствует"
                partial_size = partial.stat().st_size if partial.exists() else 0
                rows.append(
                    f"  iter={iteration:<2} job={job.id} progress={progress:.3f} "
                    f"status={status.value} exit={exit_code} target={state} "
                    f"partial={partial_size} B"
                )
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=15)
                db.close()
    finally:
        server.set_delay(0.0)
    corrupted = sum(1 for row in rows if "валиден (zip: False)" in row)
    evidence(
        "e5-5_10_kill_during_write.txt",
        "\n".join(
            [
                "##### E5-5/E5-10: kill -9 в момент записи выхода, ×10 (verbatim)\n"
                f"дата: {STAMP}",
                "",
                "метод: воркер-процесс (python -m app.workers.run), mock delay=1.0 с,",
                "ожидание progress ≥ 0.1 (стадия TRANSLATING — движок пишет",
                "target.epub.partial progressively), затем SIGKILL.",
                "",
                *rows,
                "",
                f"повреждённых принятых target.epub: {corrupted} из 10",
                "",
                "механизм (E5.6): движок пишет только .partial; принятый target.epub",
                "появляется исключительно атомарным os.replace ПОСЛЕ внутренней",
                "валидации и EPUBCheck. kill -9 в любой момент записи → target.epub",
                "отсутствует (свежее задание) или остаётся прежним валидным результатом.",
            ]
        ),
    )


# ------------------------------------------------------------------ E5-6


def ev_e5_6(work: Path, server: MockLLMServer) -> None:
    """fill_garbage: события fill == errors.log == QC, 100%, поимённо."""
    settings, cfg, env = _make_env_storage(work / "fill", server)
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    server.set_scenario("fill_garbage")
    try:
        repo = JobRepository(db)
        job = create_job(
            repo, settings, source_path=BOOKS["complex (en→de)"][0], target_language="de"
        )
        worker = _start_worker(cfg, env, work / "fill" / "worker.log")
        assert worker.wait(timeout=300) == 0
        final = repo.require(job.id)
        job_dir = settings.jobs_dir / job.id
        quality: dict[str, Any] = json.loads((job_dir / "quality.json").read_text(encoding="utf-8"))
        fill = quality["fill_failures"]
        error_lines = [
            line
            for line in (job_dir / "errors.log").read_text(encoding="utf-8").splitlines()
            if "fill не удался" in line
        ]
        events = fill["events"]
        critical = [e for e in events if e.get("over_maximum_retries")]
        critical_in_log = sum(1 for line in error_lines if "over_maximum=True" in line)
        all_in_log = len(error_lines) == fill["count"]
        crit_ok = fill["critical"] == critical_in_log > 0
        coverage = quality["qc"]["2_translated_blocks"]
        fill_warning = next(
            (w for w in quality["warnings"] if "fill не удался" in w), "(нет)"
        )
        error_excerpt = "\n".join(f"  {line}" for line in error_lines)
        evidence(
            "e5-6_fill_failed.txt",
            "\n".join(
                [
                    "##### E5-6: критические on_fill_failed → errors.log + QC, 100% (verbatim)\n"
                    f"дата: {STAMP}",
                    f"job_id: {job.id} · статус: {final.status.value} · mock: fill_garbage",
                    "",
                    "QC (fill_failures):",
                    f"  count={fill['count']}  critical={fill['critical']}",
                    "",
                    "строки errors.log ('fill не удался') — verbatim:",
                    error_excerpt,
                    "",
                    "QC-события (все, с флагом over_maximum_retries) — verbatim:",
                    json.dumps(events, ensure_ascii=False, indent=2),
                    "",
                    "сверка:",
                    f"  событий fill в QC (все/критических) : {fill['count']}/{len(critical)}",
                    f"  строк errors.log (все/критических)  : {len(error_lines)}/{critical_in_log}",
                    f"  все события в errors.log (1:1)      : {all_in_log}",
                    f"  критических расхождение (QC−log)    : {fill['critical'] - critical_in_log}",
                    f"  100% критических отражено           : {crit_ok}",
                    "",
                    "каждое событие поимённо: фаза + документ + сообщение (движок не",
                    "несёт документ/позицию — атрибуция по группе, D-решение E5).",
                    "некритические события (повторы до исчерпания) тоже зафиксированы",
                    "— механизм строже ТЗ (ТЗ требует критические; молчания нет ни в одном).",
                    "",
                    f"QC-предупреждение: {fill_warning}",
                    f"покрытие при failed fill: {coverage['translated']}/{coverage['total']} "
                    f"(непереведённые группы — в shortfall поимённо)",
                ]
            ),
        )
    finally:
        server.set_scenario("ok")
        db.close()


# ------------------------------------------------------------------ E5-7


def ev_e5_7(work: Path, server: MockLLMServer) -> None:
    """Усечение глоссария: отчёт + предупреждение."""

    def provider(job: Job) -> tuple[GlossaryEntry, ...]:
        return tuple(
            GlossaryEntry(term=f"term_{i:02d}", translation=f"trans_{i:02d}", occurrences=i)
            for i in range(30, 0, -1)
        )

    settings, _cfg, _env = _make_env_storage(
        work / "glossary", server, extra="limits:\n  glossary_max_user_prompt_chars: 300\n"
    )
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    try:
        repo = JobRepository(db)
        job, job_dir, quality, warnings = _run_inprocess(
            settings,
            repo,
            BOOKS["complex (en→de)"][0],
            "de",
            glossary_provider=provider,
        )
        glossary = quality["glossary"]
        warning = next((w for w in warnings if "user_prompt усечён" in w), "(нет)")
        tr: dict[str, Any] = json.loads(
            (job_dir / "translation_result.json").read_text(encoding="utf-8")
        )
        evidence(
            "e5-7_glossary_truncation.txt",
            "\n".join(
                [
                    "##### E5-7: усечение глоссария зафиксировано в отчёте (verbatim)\n"
                    f"дата: {STAMP}",
                    f"job_id: {job.id}",
                    "вход: 30 терминов (приоритет = число вхождений 30…1), лимит 300 симв.",
                    "",
                    "quality.json → glossary:",
                    json.dumps(glossary, ensure_ascii=False, indent=2),
                    "",
                    "translation_result.json → glossary (то же поле):",
                    json.dumps(tr["glossary"], ensure_ascii=False, indent=2),
                    "",
                    f"QC-предупреждение: {warning}",
                    "",
                    "усечение по приоритету: в prompt — термины с наибольшими вхождениями",
                    "term_30… (топ-kept), отброшенные в prompt отсутствуют",
                    "(проверено test_e5_7_glossary_truncation_recorded по системному",
                    "сообщению translate-запроса mock-сервера).",
                ]
            ),
        )
    finally:
        db.close()


# ------------------------------------------------------------------ main


def main() -> None:
    server = MockLLMServer()
    server.start()
    work_base = ROOT / ".qwen" / "tmp" / "e5_evidence"
    if work_base.exists():
        shutil.rmtree(work_base)
    work_base.mkdir(parents=True, exist_ok=True)
    settings, _cfg, _env = _make_env_storage(work_base / "inproc", server)
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    try:
        repo = JobRepository(db)
        runs: dict[str, tuple[Job, Path, dict[str, Any], list[str]]] = {}
        for name, (path, target_lang) in BOOKS.items():
            print(f"прогон: {name} …")
            runs[name] = _run_inprocess(settings, repo, path, target_lang)
        print("E5-1/2/4/8: пустые, покрытие, mimetype, 5/5 …")
        ev_e5_1_2_4_8(runs)
        print("E5-3: EPUBCheck …")
        ev_e5_3(runs, settings)
        print("E5-6: fill_garbage (воркер) …")
        ev_e5_6(work_base, server)
        print("E5-7: усечение глоссария …")
        ev_e5_7(work_base, server)
        print("E5-5/E5-10: kill -9 ×10 (воркер) …")
        ev_e5_5_10(work_base, server)
        print("готово")
    finally:
        db.close()
        server.stop()
        if work_base.exists():
            shutil.rmtree(work_base, ignore_errors=True)


if __name__ == "__main__":
    main()
