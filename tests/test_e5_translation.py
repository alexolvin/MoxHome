"""T-E5: тракт T (перевод): адаптер, атомарный выход, валидация, QC (CONTROL E5-1…E5-8).

Тесты:
- T-E5-1 — пустые переводы на фикстурах = 0 (5 книг, mock ok).
- T-E5-2 — покрытие переведённых блоков ≥ 99.5%, недостача перечислена поимённо.
- T-E5-3 — EPUBCheck на выходе всех фикстур: 0 ошибок ERROR (версия зафиксирована).
- T-E5-4 — mimetype первой записью и без сжатия (zipinfo verbatim — в evidence).
- T-E5-5/T-E5-10 — kill -9 во время записи выхода (×10): target.epub отсутствует
  или валиден; повреждённый принятый файл — 0 случаев.
- T-E5-6 — критические fill-события == записи errors.log и QC-отчёта, 100%
  (воркер, сценарий fill_garbage) + прогресс в реальных единицах в job.log (E5.3).
- T-E5-7 — усечение глоссария зафиксировано в отчёте + предупреждение.
- T-E5-8 — 5/5 книг завершены; исходный EPUB не изменён (sha256 до/после).
- T-E5-9 — недоступный LLM (воркер): FAILED, выходного файла нет, причина в сообщении.
- T-E5-11 — без EPUBCheck (воркер): COMPLETED_WITH_WARNINGS с VALIDATION_UNAVAILABLE,
  результат выдаётся.
- Единицы: режим задания → движковый submit (plan.json, E5.2);
  `build_glossary_prompt` (приоритет, лимит, детерминизм);
  `find_broken_links` (синтетический архив); бэкап перед перезаписью (E5.9);
  п.7 QC — измеренные факты известной ресериализации движка.

Прогон в процессе (Pipeline) — по образцу E4: адаптер — единственный шов над
движком (D17). Воркер (отдельный процесс `python -m app.workers.run`) — там, где
важна семантика воркера (статусы, job.log/errors.log, kill -9).
"""

from __future__ import annotations

import hashlib
import io
import json
import signal
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.adapters.epub_engine import (
    EpubTranslatorEngine,
    GlossaryEntry,
    SubmitMode,
    TranslationRequest,
)
from app.adapters.epub_engine.glossary import build_glossary_prompt
from app.adapters.epub_engine.validation import find_broken_links
from app.core.config import Settings, load_settings
from app.core.errors import LLMUnavailableError
from app.domain.job import Job, JobStatus
from app.services.jobs import create_job
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.workers.pipeline import Pipeline

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "epub"
ROOT = Path(__file__).resolve().parents[1]
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from mock_llm_server import MockLLMServer  # noqa: E402

#: Совпадает с константой conftest — тестовая фикстура, не production-значение.
DEFAULT_LLM_BASE_URL = "http://127.0.0.1:9999/v1"
#: Реальный jar EPUBCheck 5.3.0 (D4); без него прогон — COMPLETED_WITH_WARNINGS
#: (E5.7), поэтому основной прогон указывает на него; T-E5-11 — на несуществующий.
DEFAULT_EPUBCHECK_JAR = str(
    ROOT / "data" / "tools" / "epubcheck-5.3.0" / "epubcheck.jar"
)
_COMPLEX = FIXTURES / "complex.epub"

#: 5 исходных языков (Rule 4): имя → (фикстура, целевой язык).
BOOKS: dict[str, tuple[Path, str]] = {
    "complex": (_COMPLEX, "de"),
    "lang_de": (FIXTURES / "lang_de.epub", "en"),
    "lang_fr": (FIXTURES / "lang_fr.epub", "en"),
    "lang_it": (FIXTURES / "lang_it.epub", "en"),
    "lang_nl": (FIXTURES / "lang_nl.epub", "en"),
}
#: sha256 исходных EPUB на момент импорта модуля (до любого прогона, E5.9/T-E5-8).
SOURCE_SHA256: dict[str, str] = {
    name: hashlib.sha256(path.read_bytes()).hexdigest() for name, (path, _) in BOOKS.items()
}


# ------------------------------------------------------------------ фикстуры


@pytest.fixture()
def mock_server() -> Iterator[MockLLMServer]:
    server = MockLLMServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


@dataclass
class BookRun:
    """Результат сквозного in-process прогона книги (validating → … → QC)."""

    name: str
    job: Job
    job_dir: Path
    quality: dict[str, Any]
    warnings: list[str]


@dataclass
class E5Env:
    """Модульное окружение: mock + одна БД + кэш прогонов по книгам."""

    settings: Settings
    repo: JobRepository
    server: MockLLMServer
    runs: dict[str, BookRun] = field(default_factory=dict)

    def run_book(self, name: str) -> BookRun:
        if name not in self.runs:
            path, target = BOOKS[name]
            job = create_job(self.repo, self.settings, source_path=path, target_language=target)
            pipeline = Pipeline(self.settings)
            job_now = self.repo.require(job.id)
            pipeline.run_validating(self.repo, job_now)
            pipeline.run_planning(self.repo, job_now)
            pipeline.run_translating(self.repo, job_now, lambda: None)
            warnings = pipeline.run_validating_output(self.repo, job_now)
            job_dir = pipeline.job_dir(job.id)
            quality: dict[str, Any] = json.loads(
                (job_dir / "quality.json").read_text(encoding="utf-8")
            )
            assert (job_dir / "target.epub").is_file(), f"нет результата: {name}"
            self.runs[name] = BookRun(name, job, job_dir, quality, warnings)
        return self.runs[name]


@pytest.fixture(scope="module")
def e5_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[E5Env]:
    base = tmp_path_factory.mktemp("e5_module")
    work = base / "storage"
    work.mkdir(parents=True)
    server = MockLLMServer()
    server.start()
    (work / "test.env").write_text(
        "SERVER_BIND_ADDRESS=127.0.0.1\nLLM_API_KEY=test-key-value-000\n",
        encoding="utf-8",
    )
    (work / "config.yaml").write_text(
        f"storage:\n  root: {work}\n"
        f"epubcheck:\n  jar_path: {DEFAULT_EPUBCHECK_JAR}\n"
        "llm:\n  profiles:\n"
        + "".join(
            f"    {name}:\n      base_url: {server.url}\n      model: test-model\n"
            for name in ("translation", "fill", "analysis")
        ),
        encoding="utf-8",
    )
    settings = load_settings(work / "config.yaml", work / "test.env")
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    try:
        yield E5Env(settings=settings, repo=JobRepository(db), server=server)
    finally:
        db.close()
        server.stop()


# ------------------------------------------------------------------ служебное


def _settings_for(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
    server: MockLLMServer | None,
    *,
    extra: str = "",
    base_url: str | None = None,
    profile_overrides: Mapping[str, Mapping[str, object]] | None = None,
    epubcheck_jar: str = DEFAULT_EPUBCHECK_JAR,
) -> tuple[Settings, Database, JobRepository]:
    """Настройки + БД + репозиторий на изолированном storage.root (паттерн E3/E4)."""
    cfg = make_yaml(
        str(tmp_path / "storage"),
        extra,
        base_url=base_url or (server.url if server is not None else DEFAULT_LLM_BASE_URL),
        profile_overrides=profile_overrides,
        epubcheck_jar=epubcheck_jar,
    )
    env = make_env(None)
    settings = load_settings(cfg, env)
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    return settings, db, JobRepository(db)


def _start_worker(cfg: Path, env: Path, log_path: Path) -> subprocess.Popen[bytes]:
    """Воркер как отдельный процесс (stdout+stderr → log_path)."""
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
    raise TimeoutError(f"таймаут ожидания: {what} (статус: {status})")


# ------------------------------------------------------------------ T-E5-1…4, 8


@pytest.mark.parametrize("name", sorted(BOOKS))
def test_e5_1_no_empty_translations(e5_env: E5Env, name: str) -> None:
    """T-E5-1 (E5-1): пустые переводы на фикстурах = 0."""
    run = e5_env.run_book(name)
    item = run.quality["qc"]["3_empty_translations"]
    assert item["count"] == 0, f"пустые переводы в {name}: {item['items']}"


@pytest.mark.parametrize("name", sorted(BOOKS))
def test_e5_2_coverage_at_least_threshold(e5_env: E5Env, name: str) -> None:
    """T-E5-2 (E5-2): переведённых блоков ≥ 99.5% сегментов, недостача поимённо."""
    run = e5_env.run_book(name)
    item = run.quality["qc"]["2_translated_blocks"]
    assert item["coverage"] >= item["threshold"], f"покрытие в {name}: {item}"
    for entry in item["shortfall"]:
        # поимённость: каждая недостача несёт документ и позицию
        assert entry.get("doc") and entry.get("where"), f"недостача без позиции: {entry}"


@pytest.mark.parametrize("name", sorted(BOOKS))
def test_e5_3_epubcheck_zero_errors(e5_env: E5Env, name: str) -> None:
    """T-E5-3 (E5-3): EPUBCheck на выходе всех фикстур — 0 ошибок, версия зафиксирована."""
    run = e5_env.run_book(name)
    ec = run.quality["epubcheck"]
    assert ec["available"] is True, f"EPUBCheck недоступен в {name}: {ec}"
    assert ec["version"], "версия EPUBCheck не зафиксирована"
    assert ec["errors"] == 0, f"ошибки EPUBCheck в {name}: {ec}"


@pytest.mark.parametrize("name", sorted(BOOKS))
def test_e5_4_mimetype_first_entry_uncompressed(e5_env: E5Env, name: str) -> None:
    """T-E5-4 (E5-4): mimetype — первая запись архива, без сжатия (zipinfo — в evidence)."""
    run = e5_env.run_book(name)
    with zipfile.ZipFile(run.job_dir / "target.epub") as archive:
        infos = archive.infolist()
        assert infos, "пустой архив"
        assert infos[0].filename == "mimetype", f"первая запись: {infos[0].filename}"
        assert (
            infos[0].compress_type == zipfile.ZIP_STORED
        ), f"mimetype со сжатием: {infos[0].compress_type}"
        assert archive.read("mimetype").decode("utf-8").strip() == "application/epub+zip"


def test_e5_8_all_five_books_complete_sources_untouched(e5_env: E5Env) -> None:
    """T-E5-8 (E5-8): 5/5 исходных языков завершены; исходные EPUB не изменены (E5.9)."""
    for name in sorted(BOOKS):
        run = e5_env.run_book(name)
        assert (run.job_dir / "target.epub").is_file()
        assert (run.job_dir / "quality.json").is_file()
    actual = {
        name: hashlib.sha256(path.read_bytes()).hexdigest() for name, (path, _) in BOOKS.items()
    }
    assert actual == SOURCE_SHA256, "исходный EPUB изменён прогоном"


# ------------------------------------------------------------------ T-E5-5/10


def test_e5_10_kill9_during_write_never_corrupts_output(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
    mock_server: MockLLMServer,
) -> None:
    """T-E5-10 (E5-5/E5-10): kill -9 в момент записи, ×10 — выход отсутствует или валиден.

    «Момент записи» — стадия TRANSLATING: движок пишет `target.epub.partial`
    progressively на протяжении всего перевода (E5.6).
    """
    mock_server.set_delay(1.0)
    workers: list[subprocess.Popen[bytes]] = []
    for iteration in range(10):
        settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, mock_server)
        cfg_path = tmp_path / "config.yaml"
        env_path = tmp_path / "test.env"
        try:
            job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
            worker = _start_worker(cfg_path, env_path, tmp_path / f"worker_{iteration}.log")
            workers.append(worker)
            _wait_job(
                repo, job.id, lambda j: j.progress >= 0.1, 120.0, "начало перевода (T-E5-10)"
            )
            in_flight = repo.require(job.id)
            assert in_flight.status is JobStatus.TRANSLATING
            worker.send_signal(signal.SIGKILL)
            assert worker.wait(timeout=15) != 0
            target = settings.jobs_dir / job.id / "target.epub"
            if target.exists():
                # принятый файл = прошедший валидацию до os.replace: обязан быть валиден
                assert zipfile.is_zipfile(target), f"повреждённый выход: {target}"
                assert not (settings.jobs_dir / job.id / "target.epub.partial").exists(), (
                    ".partial и target одновременно"
                )
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=15)
            db.close()
    mock_server.set_delay(0.0)


# ------------------------------------------------------------------ T-E5-6


def test_e5_6_fill_failed_events_match_errors_log_and_qc(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
    mock_server: MockLLMServer,
) -> None:
    """T-E5-6 (E5-6): критические fill-события == записи errors.log и QC, 100%.

    Сценарий mock fill_garbage: fill отвечает не-XML → движок исчерпывает повторы
    → on_fill_failed(over_maximum_retries=True) на группу. Проверяется 1:1
    «событие → строка errors.log → запись QC» и поимённость (фаза, документ).
    Здесь же — прогресс в реальных единицах в job.log (E5.3).
    """
    mock_server.set_scenario("fill_garbage")
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, mock_server)
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / "test.env"
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        worker = _start_worker(cfg_path, env_path, tmp_path / "worker.log")
        assert worker.wait(timeout=300) == 0
        final = repo.require(job.id)
        assert final.status is JobStatus.COMPLETED_WITH_WARNINGS
        job_dir = settings.jobs_dir / job.id
        quality: dict[str, Any] = json.loads((job_dir / "quality.json").read_text(encoding="utf-8"))
        fill = quality["fill_failures"]
        events = fill["events"]
        assert fill["count"] > 0, "сценарий fill_garbage не породил fill-событий"
        assert fill["critical"] >= 1, "нет критических fill-событий"
        assert fill["count"] >= fill["critical"]
        # errors.log: по одной строке на СООБЩЕНИЕ (включая некритические повторы —
        # механизм строже ТЗ: фиксирует и промежуточные fill-сбои, не молча)
        error_lines = [
            line
            for line in (job_dir / "errors.log").read_text(encoding="utf-8").splitlines()
            if "fill не удался" in line
        ]
        assert len(error_lines) == fill["count"], (
            f"строки errors.log {len(error_lines)} != событий {fill['count']}"
        )
        # 100% критических событий в QC поимённо: фаза + документ + сообщение
        for event in events:
            assert event.get("phase") and event.get("document"), f"без позиции: {event}"
            assert event.get("error_message")
        assert all(
            e.get("phase") and e.get("document")
            for e in events
            if e.get("over_maximum_retries")
        )
        # QC-предупреждение по fill (не молча)
        assert any("fill не удался" in w for w in quality["warnings"])
        # E5.3: прогресс в реальных единицах (job.log, воркер): главы + сегменты
        job_log = (job_dir / "job.log").read_text(encoding="utf-8")
        for fragment in (
            "прогресс: глава 1 / 3, сегментов 8 / 26 (OEBPS/z_alpha.xhtml)",
            "прогресс: глава 2 / 3, сегментов 21 / 26 (OEBPS/m_mid.xhtml)",
            "прогресс: глава 3 / 3, сегментов 26 / 26 (OEBPS/a_last.xhtml)",
        ):
            assert fragment in job_log, f"нет в job.log: {fragment}"
        # маппинг фаз: число вызовов == TOC + metadata + 3 главы
        progress_mapping = quality["qc"]["progress_mapping"]
        assert progress_mapping["consistent"] is True
        assert progress_mapping["calls"] == progress_mapping["expected"] == 5
    finally:
        db.close()


# ------------------------------------------------------------------ T-E5-7


def _glossary_provider(job: Job) -> tuple[GlossaryEntry, ...]:
    # 30 терминов, приоритет (число вхождений) 30…1
    return tuple(
        GlossaryEntry(term=f"term_{i:02d}", translation=f"trans_{i:02d}", occurrences=i)
        for i in range(30, 0, -1)
    )


def test_e5_7_glossary_truncation_recorded(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
    mock_server: MockLLMServer,
) -> None:
    """T-E5-7 (E5-7): усечение глоссария — факт в отчёте + предупреждение, не молча."""
    settings, db, repo = _settings_for(
        tmp_path,
        make_env,
        make_yaml,
        mock_server,
        extra="limits:\n  glossary_max_user_prompt_chars: 300\n",
    )
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        pipeline = Pipeline(settings, glossary_provider=_glossary_provider)
        job_now = repo.require(job.id)
        pipeline.run_validating(repo, job_now)
        pipeline.run_planning(repo, job_now)
        pipeline.run_translating(repo, job_now, lambda: None)
        warnings = pipeline.run_validating_output(repo, job_now)
        job_dir = pipeline.job_dir(job.id)
        quality: dict[str, Any] = json.loads((job_dir / "quality.json").read_text(encoding="utf-8"))
        glossary = quality["glossary"]
        assert glossary["total_terms"] == 30
        assert glossary["kept_terms"] > 0
        assert glossary["dropped_terms"] > 0
        assert glossary["kept_terms"] + glossary["dropped_terms"] == glossary["total_terms"]
        assert glossary["truncated"] is True
        assert glossary["prompt_chars"] <= glossary["limit_chars"] == 300
        # предупреждение с числами
        assert any("user_prompt усечён" in w for w in warnings)
        # приоритет: в translate-запросе (user_prompt движок кладёт в
        # <rules> системного сообщения — измерено) ровно топ-kept по числу
        # вхождений; отброшенных терминов в prompt нет
        kept = glossary["kept_terms"]
        top = set(range(30, 30 - kept, -1))
        dropped = set(range(1, 31 - kept))
        assert len(top) == kept and not (top & dropped)
        translate_requests = [
            r for r in mock_server.requests_log() if r["kind"] == "translate"
        ]
        assert translate_requests, "нет translate-запросов"
        system = str(translate_requests[0]["system"])
        for i in top:
            assert f"term_{i:02d} → trans_{i:02d}" in system, f"не вошёл: term_{i:02d}"
        for i in dropped:
            assert f"term_{i:02d} → trans_{i:02d}" not in system, f"отброшенный: term_{i:02d}"
    finally:
        db.close()


def test_glossary_prompt_priority_limit_deterministic() -> None:
    """Единица: build_glossary_prompt — приоритет, жёсткий лимит, детерминизм."""
    entries = tuple(
        GlossaryEntry(term=f"t{i:02d}", translation="x", occurrences=i) for i in range(10)
    )
    prompt, total, kept, chars = build_glossary_prompt(entries, 60)
    assert total == 10
    assert 0 < kept < 10
    assert prompt is not None and "t09" in prompt and "t00" not in prompt
    assert chars <= 60
    # детерминизм: один вход — один prompt
    assert build_glossary_prompt(entries, 60) == (prompt, total, kept, chars)
    # перемешанный ввод — тот же результат (порядок по приоритету, не по вводу)
    shuffled = tuple(reversed(entries))
    assert build_glossary_prompt(shuffled, 60) == (prompt, total, kept, chars)
    # пустой глоссарий — prompt не передаётся
    assert build_glossary_prompt((), 100) == (None, 0, 0, 0)
    # лимит меньше, чем одна строка — усечение на все термины
    assert build_glossary_prompt(entries, 10) == (None, 10, 0, 0)


# ------------------------------------------------------------------ T-E5-9


def test_e5_9_unavailable_llm_failed_no_output(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
) -> None:
    """T-E5-9: недоступный LLM (мёртвый порт) — FAILED, выхода нет, причина в сообщении."""
    settings, db, repo = _settings_for(
        tmp_path,
        make_env,
        make_yaml,
        None,
        base_url="http://127.0.0.1:1/v1",
        profile_overrides={"translation": {"retry_times": 1}, "fill": {"retry_times": 1}},
    )
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / "test.env"
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        worker = _start_worker(cfg_path, env_path, tmp_path / "worker.log")
        assert worker.wait(timeout=180) == 0
        final = repo.require(job.id)
        assert final.status is JobStatus.FAILED
        assert "тракт T недоступен" in (final.error_message or ""), final.error_message
        assert "test-key-value-000" not in (final.error_message or ""), "ключ API в сообщении"
        assert not (settings.jobs_dir / job.id / "target.epub").exists()
    finally:
        db.close()


def test_e5_9_inprocess_unavailable_llm_raises(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
) -> None:
    """Единица: in-process run_translating на мёртвом порту — LLMUnavailableError,
    target.epub не создаётся (E5.6: файл в состоянии записи не результат)."""
    settings, db, repo = _settings_for(
        tmp_path,
        make_env,
        make_yaml,
        None,
        base_url="http://127.0.0.1:1/v1",
        profile_overrides={"translation": {"retry_times": 1}, "fill": {"retry_times": 1}},
    )
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        pipeline = Pipeline(settings)
        job_now = repo.require(job.id)
        pipeline.run_validating(repo, job_now)
        pipeline.run_planning(repo, job_now)
        with pytest.raises(LLMUnavailableError) as excinfo:
            pipeline.run_translating(repo, job_now, lambda: None)
        message = str(excinfo.value)
        assert "тракт T недоступен" in message
        assert "test-key-value-000" not in message
        assert not (pipeline.job_dir(job.id) / "target.epub").exists()
    finally:
        db.close()


# ------------------------------------------------------------------ T-E5-11


def test_e5_11_without_epubcheck_completed_with_warnings(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
    mock_server: MockLLMServer,
) -> None:
    """T-E5-11 (E5.7): без EPUBCheck — COMPLETED_WITH_WARNINGS + VALIDATION_UNAVAILABLE."""
    settings, db, repo = _settings_for(
        tmp_path,
        make_env,
        make_yaml,
        mock_server,
        epubcheck_jar="/nonexistent/epubcheck-5.3.0/epubcheck.jar",
    )
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / "test.env"
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        worker = _start_worker(cfg_path, env_path, tmp_path / "worker.log")
        assert worker.wait(timeout=300) == 0
        final = repo.require(job.id)
        assert final.status is JobStatus.COMPLETED_WITH_WARNINGS
        job_dir = settings.jobs_dir / job.id
        assert (job_dir / "target.epub").is_file(), "результат не выдан"
        quality: dict[str, Any] = json.loads((job_dir / "quality.json").read_text(encoding="utf-8"))
        assert quality["epubcheck"]["available"] is False
        assert any(
            w.startswith("VALIDATION_UNAVAILABLE") for w in quality["warnings"]
        ), quality["warnings"]
    finally:
        db.close()


# ------------------------------------------------------------------ режимы (E5.2)


def test_e5_2_job_mode_maps_to_engine_submit(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
) -> None:
    """Единица (E5.2): translation → REPLACE; bilingual → APPEND_BLOCK;
    APPEND_TEXT — только явной настройкой submit_mode_bilingual."""
    for extra, mode, expected in (
        ("", "translation", "REPLACE"),
        ("", "bilingual", "APPEND_BLOCK"),
        ("translation:\n  submit_mode_bilingual: APPEND_TEXT\n", "bilingual", "APPEND_TEXT"),
    ):
        settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, None, extra=extra)
        pipeline = Pipeline(settings)
        try:
            job = create_job(repo, settings, source_path=_COMPLEX, target_language="de", mode=mode)
            job_now = repo.require(job.id)
            pipeline.run_validating(repo, job_now)
            pipeline.run_planning(repo, job_now)
            plan: dict[str, Any] = json.loads(
                (pipeline.job_dir(job.id) / "plan.json").read_text(encoding="utf-8")
            )
            assert plan["mode"] == mode
            assert plan["submit_mode"] == expected, (mode, plan["submit_mode"])
        finally:
            db.close()


# ------------------------------------------------------------------ E5.9 бэкап


def test_e5_9_backup_created_before_overwrite(e5_env: E5Env) -> None:
    """Единица (E5.9): повторный перевод поверх существующего результата — бэкап."""
    run = e5_env.run_book("complex")
    job_dir = run.job_dir
    target = job_dir / "target.epub"
    assert target.is_file()
    before = target.read_bytes()
    engine = EpubTranslatorEngine(e5_env.settings, job_dir)
    request = TranslationRequest(
        source_path=_COMPLEX,
        target_path=target,
        target_language="de",
        mode=SubmitMode.APPEND_BLOCK,
    )
    result = engine.translate_book(request)
    assert result.backup_path is not None, "бэкап существующего результата не создан"
    assert result.backup_path.is_file()
    assert result.backup_path.name.startswith("target.epub.bak-")
    assert result.backup_path.read_bytes() == before, "бэкап отличается от прежнего результата"
    assert (job_dir / "target.epub.partial").is_file() is False


# ------------------------------------------------------------------ п.7 QC: измерено


def test_e5_qc_item7_known_engine_reserialization(e5_env: E5Env) -> None:
    """Единица (п.7): известная ресериализация движка — поимённо, без предупреждений.

    Измерено (E1 + E3-прогон 2026-09-12): движок при ресериализации spine-документа
    удаляет корневой атрибут `lang` (`xml:lang` сохраняется) → ровно одна известная
    находка на документ. Непредвиденных изменений структуры в mock-прогоне — 0.
    """
    run = e5_env.run_book("complex")
    item = run.quality["qc"]["7_unexpected_structure_changes"]
    assert item["count"] == 0, f"непредвиденные изменения структуры: {item['items'][:5]}"
    known = item["known_engine_reserialization"]
    assert known["count"] == 3, known["items"]
    for entry in known["items"]:
        assert entry["where"] == "/"
        assert entry["issue"] == "атрибут 'lang' удалён"
    # чистый прогон — без предупреждений
    assert run.warnings == [], run.warnings


# ------------------------------------------------------------------ п.8 QC: синтетика


def test_find_broken_links_detects_missing_file_and_anchor() -> None:
    """Единица (E5.7/п.8): битые ссылки и отсутствующие изображения — поимённо."""
    xhtml = 'xmlns="http://www.w3.org/1999/xhtml"'
    head = "<head><title>t</title></head>"
    doc_a = (
        f"<html {xhtml}>{head}<body>"
        '<p id="s"><a href="b.xhtml">ok</a> <a href="b.xhtml#nope">nope</a> '
        '<a href="gone.xhtml">gone</a></p><img src="img.png"/></body></html>'
    ).encode()
    doc_b = f"<html {xhtml}>{head}<body><div id='ok'>x</div></body></html>".encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("OEBPS/a.xhtml", doc_a)
        archive.writestr("OEBPS/b.xhtml", doc_b)
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        problems = find_broken_links(archive, ["OEBPS/a.xhtml", "OEBPS/b.xhtml"])
    # b.xhtml#nope — нет якоря; gone.xhtml — нет файла; img.png — нет файла.
    # href="b.xhtml" (файл на месте, фрагмента нет) — цел.
    assert len(problems) == 3, problems
    assert any("gone.xhtml" in p for p in problems), problems
    assert any("#nope" in p for p in problems), problems
    assert any("img.png" in p for p in problems), problems
