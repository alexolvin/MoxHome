# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Тесты L3: правка расхождений перевода по глоссарию (ТЗ_02 §L3, CONTROL L3-1…L3-10).

Сквозной in-process прогон (Pipeline) ходит по тем же переходам, что воркер
(пайплайн сам статус задания не меняет); mock-сервер (D3) — детерминированный
(rot3 + инъекции неверных форм). T-L3-8 — реальные процессы воркера:
kill -9 ×10 в окне пересборки, аренда 1.0 с / heartbeat 0.5 с.

- T-L3-1 — всё правимое множество исправлено (100%), в выходе — только формы
  парадигмы; доля правимого множества 0.0 → 1.0.
- T-L3-2 — 0 правок без записи: по одной записи quality.log на правку.
- T-L3-3 — 0 применённых правок, не прошедших ВСЕ 5 пунктов валидации.
- T-L3-4 — 0 потери разметки: сигнатура (тег, атрибуты) правимого блока сохранена.
- T-L3-5 — повтор стадии по исправленному файлу: 0 расхождений, 0 правок,
  файл побайтово тот же.
- T-L3-6/T-L3-13 — доля 60% > порога 50%: остановка с отчётом, 0 правок,
  книга не переписана, LLM не вызывался.
- T-L3-7 — EPUBCheck пересобранного архива: 0 ERROR; mimetype первой записью,
  без сжатия.
- T-L3-8 — kill -9 ×10 в окне пересборки: 0 повреждённых target (либо доправки
  состояние, либо финальное), бэкап l3bak цел; после истечения аренды задание
  завершено с тем же результатом.
- T-L3-9 — unverified-термин: 0 автоправок, 100% сообщено отдельным перечнем
  с собственной долей.
- T-L3-10 — 5 исходных языков (en/de/fr/it/nl → ru): 5/5 завершены, стадия
  присутствует; правка выключена — no-op, обнаружение при этом не выключено.
- T-L3-11 — имя, переведённое тремя разными способами в 20 вхождениях: после
  L3 все вхождения — каноническая форма; quality.log ≤20 записей (пропущенных
  нет — 0 отклонений).
- T-L3-12 — гиперссылка и сноска внутри правимого предложения: правка
  сохраняет обе (валидация пройдена), разметка в итоговом блоке.
- T-L3-14 — модель вернула версию без требуемого термина: правка отклонена
  (пункт 1), расхождение неустранено, повторной попытки нет (1 запрос на сегмент).
- T-L3-15 — сравнение резервной копии и результата: ровно тот список изменений
  (блоки), что записан в quality.log, ничего лишнего.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lxml.etree
import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import attach_job_logs, detach_job_logs
from app.domain.job import Job, JobStatus
from app.epub.reader import read_epub
from app.epub.rebuild import block_bytes
from app.epub.segmenter import full_text, normalize_text
from app.glossary.fix import markup_signature
from app.services.jobs import confirm_job, create_job
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.workers.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]
_TESTS_DIR = str(Path(__file__).resolve().parent)
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from mock_llm_server import MockLLMServer  # noqa: E402
from test_l2_glossary import (  # noqa: E402
    _WORDS20,
    FIXTURES,
    WEXLEY_PARAS,
    _make_storage,
    _write_epub,
)

#: Запись главы в синтетических книгах и в их целевых EPUB.
L3_CHAPTER = "OEBPS/chapter1.xhtml"
_XH = "{http://www.w3.org/1999/xhtml}"

# ------------------------------------------------------------------ содержимое

# T-L3-1/2/3/5/7/15: 3 из 7 сегментов с Wexley (доля 3/7 ≈ 0.43 ≤ 0.5).
L3_FIX_PARAS: tuple[str, ...] = (
    *WEXLEY_PARAS,
    "The morning was quiet.",
    "The river kept its course.",
    "Stone walls held the wind.",
)
# T-L3-11: имя в 20 сегментах (20/21 ≈ 0.95 — проходит только при пороге 1.0).
L3_20_PARAS: tuple[str, ...] = tuple(f"The {w} around Wexley." for w in _WORDS20)
# T-L3-6/T-L3-13: 6 из 11 сегментов с Wexley (6/11 ≈ 0.55 > 0.5 → остановка).
L3_60_PARAS: tuple[str, ...] = (
    "The village of Wexley stood before dawn.",
    "Every morning Wexley woke to the sound of water.",
    "Far from the city, Wexley kept its silence.",
    "The well of Wexley never ran dry.",
    "The Wexley bells rang at noon.",
    "Old Wexley gates opened slowly.",
    "The morning was quiet.",
    "The river kept its course.",
    "Stone walls held the wind.",
    "Lanterns glowed in the fog.",
)
# T-L3-4/T-L3-12: 2 из 5 сегментов с Wexley; p4 — гиперссылка + сноска (epub:type).
L3_MARKUP_PARAS: tuple[str, ...] = (
    "The morning was quiet.",
    "The keeper of Wexley lit the lamp before dawn.",
    "Lanterns glowed in the fog.",
    'Wexley is <a href="https://example.org/wexley">described</a> in the '
    '<em>old atlas</em> (see <a epub:type="notefoot" href="#fn1">note</a>). '
    '<span id="fn1" epub:type="notefoot">Old atlas, plate 7.</span>',
)
# T-L3-9: unverified-сентинел (mock: 12 идентичных форм → парадигма отклоняется).
L3_UNVERIF_PARAS: tuple[str, ...] = (
    "The Badparadigm reached dawn.",
    "And Badparadigm returned before river.",
    "The morning was quiet.",
    "Lanterns glowed in the fog.",
)

#: name → (заголовок, uuid, абзацы). UID — валидные hex (EPUBCheck исходника).
L3_BOOKS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "wexley_fix.epub": ("L3 Probe Fix", "3b000001-0000-0000-0000-000000000001", L3_FIX_PARAS),
    "wexley20.epub": ("L3 Probe 20", "3b000002-0000-0000-0000-000000000002", L3_20_PARAS),
    "wexley60.epub": ("L3 Probe 60", "3b000003-0000-0000-0000-000000000003", L3_60_PARAS),
    "wexley_markup.epub": (
        "L3 Probe Markup", "3b000004-0000-0000-0000-000000000004", L3_MARKUP_PARAS,
    ),
    "wexley_no_term.epub": (
        "L3 Probe NoTerm", "3b000005-0000-0000-0000-000000000005", L3_FIX_PARAS,
    ),
    "unverif.epub": ("L3 Probe Unverif", "3b000006-0000-0000-0000-000000000006", L3_UNVERIF_PARAS),
}


def _synth_book_l3(out_path: Path, title: str, uid: str, paragraphs: tuple[str, ...]) -> None:
    """Синтетический EPUB; в корне главы объявлен xmlns:epub (T-L3-12:
    epub:type в исходнике; для остальных книг безвредно)."""
    body = "\n".join(f'  <p id="p{i}">{p}</p>' for i, p in enumerate(paragraphs, start=1))
    chapter = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" '
        'lang="en" xml:lang="en">\n'
        f"<head>\n  <title>{title}</title>\n</head>\n"
        f'<body>\n  <h1 id="h1">{title}</h1>\n{body}</body>\n</html>\n'
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="l3-id">\n'
        "  <metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
        f"\n    <dc:identifier id=\"l3-id\">urn:uuid:{uid}</dc:identifier>"
        f"\n    <dc:title>{title}</dc:title>\n    <dc:creator>L3 Probe</dc:creator>"
        "\n    <dc:language>en</dc:language>\n    <dc:date>2026-01-01</dc:date>\n"
        '    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>\n'
        "  </metadata>\n  <manifest>"
        '\n    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>'
        '\n    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
        'properties="nav"/>\n  </manifest>\n  <spine>\n    <itemref idref="ch1"/>\n'
        "  </spine>\n</package>\n"
    )
    nav = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" '
        'lang="en" xml:lang="en">\n<head>\n  <title>Contents</title>\n</head>\n'
        "<body>\n  <nav epub:type=\"toc\" id=\"toc\">\n    <h1>Contents</h1>\n    <ol>\n"
        f'      <li><a href="chapter1.xhtml">{title}</a></li>\n    </ol>\n  </nav>\n'
        "</body>\n</html>\n"
    )
    _write_epub(
        out_path,
        {
            "META-INF/container.xml": (
                '<?xml version="1.0"?>\n<container version="1.0" '
                'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n  <rootfiles>\n'
                '    <rootfile full-path="OEBPS/content.opf" '
                'media-type="application/oebps-package+xml"/>\n  </rootfiles>\n</container>\n'
            ),
            "OEBPS/content.opf": opf,
            "OEBPS/chapter1.xhtml": chapter,
            "OEBPS/nav.xhtml": nav,
        },
    )


def _build_l3_books(root: Path) -> dict[str, Path]:
    books: dict[str, Path] = {}
    for name, (title, uid, paras) in L3_BOOKS.items():
        path = root / name
        _synth_book_l3(path, title, uid, paras)
        books[name] = path
    return books


# ------------------------------------------------------------------ служебное


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _chapter_of(epub_path: Path) -> bytes:
    with zipfile.ZipFile(epub_path) as archive:
        return archive.read(L3_CHAPTER)


def _read_l3_report(job_dir: Path) -> dict[str, Any] | None:
    path = job_dir / "l3_fix_report.json"
    if not path.is_file():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _fix_log_lines(quality_log: str) -> list[dict[str, Any]]:
    """Записи quality.log вида glossary_fix (L3.7: по одной на правку)."""
    lines: list[dict[str, Any]] = []
    for raw in quality_log.splitlines():
        if not raw.strip():
            continue
        record = json.loads(raw)
        msg = str(record.get("msg", ""))
        if not msg.startswith("glossary_fix: "):
            continue
        payload = json.loads(msg[len("glossary_fix: ") :])
        if payload.get("kind") == "glossary_fix":
            lines.append(payload)
    return lines


def _start_worker(cfg: Path, env_file: Path, log_path: Path) -> subprocess.Popen[bytes]:
    """Воркер как отдельный процесс (patтерn E3; --once: одно задание и выход)."""
    cmd = [
        sys.executable,
        "-m",
        "app.workers.run",
        "--config",
        str(cfg),
        "--env",
        str(env_file),
    ]
    handle = log_path.open("w", encoding="utf-8")
    try:
        return subprocess.Popen(cmd, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
    finally:
        handle.close()


def _wait_status(
    repo: JobRepository, job_id: str, want: JobStatus, timeout: float
) -> Job:
    deadline = time.monotonic() + timeout
    last: Job | None = None
    while time.monotonic() < deadline:
        job = repo.get(job_id)
        if job is not None:
            last = job
            if job.status is want:
                return job
        time.sleep(0.02)
    status = None if last is None else last.status.value
    raise TimeoutError(f"ожидание {want.value}: последнее состояние {status}")


def _kill_all(workers: list[subprocess.Popen[bytes]]) -> None:
    for proc in workers:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


# ------------------------------------------------------------------ окружение


@dataclass
class L3Run:
    """Сквозной in-process прогон до стадии GLOSSARY_FIX (включая её)."""

    name: str
    profile: str
    job: Job
    job_dir: Path
    quality: dict[str, Any]
    warnings: list[str]
    l3_report: dict[str, Any] | None
    #: target.epub ДО стадии (байты) — эталон «книга не переписана».
    target_before: bytes
    quality_log: str
    error: AppError | None = None
    #: Число LLM-запросов стадии (только glossary_fix — других задач нет).
    fix_requests: int = 0


@dataclass
class L3Env:
    server: MockLLMServer
    base: Path
    books: dict[str, Path]
    _settings: dict[str, Settings] = field(default_factory=dict)
    _repos: dict[str, JobRepository] = field(default_factory=dict)
    _dbs: dict[str, Database] = field(default_factory=dict)
    runs: dict[str, L3Run] = field(default_factory=dict)

    def add_profile(self, name: str, settings: Settings, db: Database) -> None:
        self._settings[name] = settings
        self._dbs[name] = db
        self._repos[name] = JobRepository(db)

    def run(
        self,
        name: str,
        book: str,
        *,
        profile: str = "main",
        scenario: str = "glossary_inconsistent",
        lang: str = "en",
    ) -> L3Run:
        """Ленивый кэшируемый прогон: ходит по переходам, как воркер
        (пайплайн сам статус не меняет), стадия GLOSSARY_FIX — в процессе."""
        if name in self.runs:
            return self.runs[name]
        settings = self._settings[profile]
        repo = self._repos[profile]
        source = self.books[book] if book in self.books else FIXTURES / book
        self.server.set_scenario(scenario)
        try:
            job = create_job(repo, settings, source_path=source, target_language="ru")
            pipeline = Pipeline(settings)
            repo.transition(job.id, JobStatus.VALIDATING, reason="начало этапа (T-L3)")
            needs_confirm = pipeline.run_validating(repo, repo.require(job.id))
            if needs_confirm:
                repo.transition(
                    job.id, JobStatus.NEEDS_CONFIRMATION, reason="L0.5 (T-L3)"
                )
                confirm_job(repo, job.id, source_language=lang)
            else:
                repo.transition(job.id, JobStatus.PLANNING, reason="язык принят (T-L3)")
            pipeline.run_planning(repo, repo.require(job.id))
            repo.transition(job.id, JobStatus.TRANSLATING, reason="начало этапа (T-L3)")
            pipeline.run_translating(repo, repo.require(job.id), lambda: None)
            repo.transition(job.id, JobStatus.ANALYZING, reason="начало этапа (T-L3)")
            pipeline.run_analyzing(repo, repo.require(job.id))
            repo.transition(job.id, JobStatus.GENERATING, reason="начало этапа (T-L3)")
            pipeline.run_generating(repo, repo.require(job.id))
            repo.transition(
                job.id, JobStatus.VALIDATING_OUTPUT, reason="начало этапа (T-L3)"
            )
            pipeline.run_validating_output(repo, repo.require(job.id))
            repo.transition(job.id, JobStatus.GLOSSARY_FIX, reason="начало этапа (T-L3)")

            job_dir = settings.job_dir(job.id)
            target_before = (job_dir / "target.epub").read_bytes()
            reqs_before = len(self.server.requests_log())
            error: AppError | None = None
            warnings: list[str] = []
            handlers = attach_job_logs(job_dir, settings.secrets())
            try:
                try:
                    warnings = pipeline.run_glossary_fix(repo, repo.require(job.id))
                except AppError as exc:
                    error = exc
            finally:
                detach_job_logs(handlers)
            run = L3Run(
                name=name,
                profile=profile,
                job=repo.require(job.id),
                job_dir=job_dir,
                quality=json.loads((job_dir / "quality.json").read_text(encoding="utf-8")),
                warnings=warnings,
                l3_report=_read_l3_report(job_dir),
                target_before=target_before,
                quality_log=(job_dir / "quality.log").read_text(encoding="utf-8"),
                error=error,
                fix_requests=len(self.server.requests_log()) - reqs_before,
            )
        finally:
            self.server.set_scenario("ok")
        self.runs[name] = run
        return run

    def rerun_l3(self, name: str) -> L3Run:
        """T-L3-5: повтор стадии по тому же заданию (терминальный переход
        не делается — его делает воркер)."""
        run = self.runs[name]
        settings = self._settings[run.profile]
        repo = self._repos[run.profile]
        pipeline = Pipeline(settings)
        reqs_before = len(self.server.requests_log())
        error: AppError | None = None
        warnings: list[str] = []
        handlers = attach_job_logs(run.job_dir, settings.secrets())
        try:
            try:
                warnings = pipeline.run_glossary_fix(repo, repo.require(run.job.id))
            except AppError as exc:
                error = exc
        finally:
            detach_job_logs(handlers)
        return L3Run(
            name=f"{name}:rerun",
            profile=run.profile,
            job=repo.require(run.job.id),
            job_dir=run.job_dir,
            quality=json.loads(
                (run.job_dir / "quality.json").read_text(encoding="utf-8")
            ),
            warnings=warnings,
            l3_report=_read_l3_report(run.job_dir),
            target_before=run.target_before,
            quality_log=(run.job_dir / "quality.log").read_text(encoding="utf-8"),
            error=error,
            fix_requests=len(self.server.requests_log()) - reqs_before,
        )

    def close(self) -> None:
        for db in self._dbs.values():
            db.close()


@pytest.fixture(scope="module")
def l3_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[L3Env]:
    base = tmp_path_factory.mktemp("l3_module")
    books_dir = base / "books"
    books_dir.mkdir()
    server = MockLLMServer()
    server.start()
    env = L3Env(server=server, base=base, books=_build_l3_books(books_dir))
    main_settings, main_db = _make_storage(
        base, "storage_main", server, extra="  fix_enabled: true\n"
    )
    env.add_profile("main", main_settings, main_db)
    wide_settings, wide_db = _make_storage(
        base,
        "storage_wide",
        server,
        extra="  fix_enabled: true\n  fix_max_segment_share: 1.0\n",
    )
    env.add_profile("wide", wide_settings, wide_db)
    nofix_settings, nofix_db = _make_storage(
        base, "storage_nofix", server, extra="  fix_enabled: false\n"
    )
    env.add_profile("nofix", nofix_settings, nofix_db)
    kill_settings, kill_db = _make_storage(
        base,
        "storage_kill",
        server,
        extra=(
            "  fix_enabled: true\nserver:\n"
            "  heartbeat_seconds: 0.5\n  lease_seconds: 1.0\n"
        ),
    )
    env.add_profile("kill", kill_settings, kill_db)
    try:
        yield env
    finally:
        env.close()
        server.stop()


# ------------------------------------------------------------------ T-L3-1…T-L3-7


def test_l3_1_full_fixable_set(l3_env: L3Env) -> None:
    """L3-1: 100% правимого множества исправлено; в выходе — только формы
    парадигмы; доля правимого множества 0.0 → 1.0."""
    run = l3_env.run("fix", "wexley_fix.epub")
    assert run.error is None, str(run.error)
    report = run.l3_report
    assert report is not None and report["halted"] is False
    assert report["mismatches_input"] == 3, "вход — список расхождений L2"
    assert report["total_segments"] == 7
    assert report["fixable_segment_share"] == pytest.approx(3 / 7, abs=1e-6)
    assert report["attempted"] == 3
    assert report["applied"] == 3, "всё правимое множество исправлено"
    assert report["rejected"] == 0
    assert report["editable_share_before"] == 0.0
    assert report["editable_share_after"] == 1.0
    chapter = _chapter_of(run.job_dir / "target.epub").decode("utf-8")
    assert chapter.count("Zhaohb") == 3, "каноническая форма — во всех вхождениях"
    assert chapter.count("Xrongy") == 0, "внедрённая неверная форма устранена"
    assert run.warnings == [], "предупреждений нет — статус был бы COMPLETED"


def test_l3_2_every_fix_logged(l3_env: L3Env) -> None:
    """L3-2: 0 правок без записи — по одной записи quality.log на правку,
    segment_id совпадают, поля записи заполнены."""
    run = l3_env.run("fix", "wexley_fix.epub")
    report = run.l3_report
    assert report is not None, "нет L3-отчёта"
    lines = _fix_log_lines(run.quality_log)
    assert len(lines) == report["applied"] == 3
    applied_ids = sorted(f["segment_id"] for f in report["fixes"] if f["applied"])
    logged_ids = sorted(line["segment_id"] for line in lines)
    assert applied_ids == logged_ids, "записи логa — ровно применённые правки"
    for line in lines:
        assert line["kind"] == "glossary_fix"
        for key in (
            "segment_id", "doc", "term", "old_variant", "new_variant",
            "prompt_file", "prompt_version", "model", "timestamp",
        ):
            assert str(line.get(key, "")).strip(), f"поле записи не заполнено: {key}"


def test_l3_3_no_unvalidated_applied(l3_env: L3Env) -> None:
    """L3-3: 0 применённых правок, не прошедших ВСЕ 5 пунктов валидации."""
    run = l3_env.run("fix", "wexley_fix.epub")
    assert run.l3_report is not None, "нет L3-отчёта"
    applied = [f for f in run.l3_report["fixes"] if f["applied"]]
    assert applied, "правок нет — тест не о чём"
    assert all(f["validation_points"] == [] for f in applied), (
        "применена правка с непройденными пунктами валидации: "
        + "; ".join(f"{f['segment_id']}: {f['validation_points']}" for f in applied)
    )


def test_l3_4_markup_preserved(l3_env: L3Env) -> None:
    """L3-4: 0 потери разметки — сигнатура (теги, атрибуты) каждого правого
    блока сохранена (сравнение l3bak и результата)."""
    run = l3_env.run("markup", "wexley_markup.epub")
    assert run.error is None, str(run.error)
    report = run.l3_report
    assert report is not None, "нет L3-отчёта"
    applied = [f for f in report["fixes"] if f["applied"]]
    assert len(applied) == 2, "оба сегмента с Wexley (обычный и с разметкой) правятся"
    job_dir = run.job_dir
    with zipfile.ZipFile(job_dir / "target.epub.l3bak") as archive:
        old_chapter = archive.read(L3_CHAPTER)
    new_chapter = _chapter_of(job_dir / "target.epub")
    xpaths = {f["segment_id"]: f for f in report["fixable"]}
    for fix in applied:
        seg = xpaths[fix["segment_id"]]
        old_el = lxml.etree.fromstring(block_bytes(old_chapter, seg["translation_xpath"]))
        new_el = lxml.etree.fromstring(block_bytes(new_chapter, seg["translation_xpath"]))
        assert markup_signature(old_el) == markup_signature(new_el), (
            f"разметка потеряна в {fix['segment_id']}: "
            f"{markup_signature(old_el)} != {markup_signature(new_el)}"
        )


def test_l3_5_rerun_idempotent(l3_env: L3Env) -> None:
    """L3-8: повтор стадии по исправленному файлу — 0 расхождений, 0 правок,
    файл побайтово тот же, новых записей логa нет."""
    first = l3_env.run("fix", "wexley_fix.epub")
    target = first.job_dir / "target.epub"
    after_first = target.read_bytes()
    lines_before = len(_fix_log_lines(first.quality_log))
    second = l3_env.rerun_l3("fix")
    assert second.error is None, str(second.error)
    report = second.l3_report
    assert report is not None
    assert report["mismatches_input"] == 0, "расхождения переопределены измерением"
    assert report["attempted"] == 0
    assert report["applied"] == 0
    assert report["rejected"] == 0
    assert second.fix_requests == 0, "повтор не вызывает LLM"
    assert _sha256(target.read_bytes()) == _sha256(after_first), "файл побайтово тот же"
    assert len(_fix_log_lines(second.quality_log)) == lines_before
    assert second.warnings == []


def test_l3_6_halt_on_share_exceed(l3_env: L3Env) -> None:
    """L3-6 + T-L3-13: 60% сегментов > порога 50% — остановка с отчётом,
    книга не переписана, LLM не вызывался (порог — ДО любых вызовов)."""
    run = l3_env.run("halt", "wexley60.epub")
    assert run.error is not None, "превышение порога — остановка (AppError → FAILED)"
    assert "L3-6" in run.error.message
    report = run.l3_report
    assert report is not None
    assert report["halted"] is True
    assert report["fixable_segment_share"] == pytest.approx(6 / 11, abs=1e-6)
    assert report["fixable_segment_share"] > report["max_segment_share"]
    assert "fixable" in report and len(report["fixable"]) == 6
    target = run.job_dir / "target.epub"
    assert _sha256(target.read_bytes()) == _sha256(run.target_before), "книга не переписана"
    assert run.fix_requests == 0, "порог объёма — ДО любого LLM-вызова"
    assert not (run.job_dir / "target.epub.l3bak").exists(), "без правок — без бэкапа"
    assert not (run.job_dir / "target.epub.partial").exists()


def test_l3_7_rebuild_epubcheck(l3_env: L3Env) -> None:
    """L3-7: пересобранный архив — EPUBCheck 0 ERROR (FATAL отклоняет
    публикацию до os.replace); mimetype — первой записью, без сжатия."""
    run = l3_env.run("fix", "wexley_fix.epub")
    assert run.l3_report is not None, "нет L3-отчёта"
    rebuild = run.l3_report["rebuild"]
    assert rebuild is not None and rebuild["available"] is True
    assert rebuild["errors"] == 0, f"EPUBCheck: {rebuild}"
    assert rebuild["version"], "версия EPUBCheck записана"
    target = run.job_dir / "target.epub"
    assert zipfile.is_zipfile(target)
    with zipfile.ZipFile(target) as archive:
        first = archive.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"


# ------------------------------------------------------------------ T-L3-8


def _kill_iteration(
    settings: Settings,
    repo: JobRepository,
    *,
    cfg: Path,
    env_file: Path,
    log_dir: Path,
    book: Path,
    index: int,
    pre_chapter: bytes,
    post_chapter: bytes,
    workers: list[subprocess.Popen[bytes]],
) -> dict[str, Any]:
    """Одна итерация kill -9: W1 (до подтверждения) → W2 (весь конвейер) →
    SIGKILL в окне пересборки (`.partial` существует) → проверка target и
    l3bak → после истечения аренды W3 доводит задание до конца."""
    job = create_job(repo, settings, source_path=book, target_language="ru")
    job_dir = settings.jobs_dir / job.id
    target_file = job_dir / "target.epub"
    l3bak = job_dir / "target.epub.l3bak"
    partial = job_dir / "target.epub.partial"

    w1 = _start_worker(cfg, env_file, log_dir / f"kill_{index}_w1.log")
    workers.append(w1)
    _wait_status(repo, job.id, JobStatus.NEEDS_CONFIRMATION, 180)
    assert w1.wait(timeout=60) == 0
    confirm_job(repo, job.id, source_language="en")

    w2 = _start_worker(cfg, env_file, log_dir / f"kill_{index}_w2.log")
    workers.append(w2)
    _wait_status(repo, job.id, JobStatus.GLOSSARY_FIX, 300)
    # На входе в стадию файл — доправки (стадия не трогает target до rebuild).
    assert _chapter_of(target_file) == pre_chapter
    deadline = time.monotonic() + 120
    while not partial.exists():
        state = repo.get(job.id)
        if state is not None and state.status is JobStatus.COMPLETED:
            break
        if time.monotonic() > deadline:
            break
        time.sleep(0.02)
    state = repo.get(job.id)
    assert state is not None
    if not (partial.exists() and state.status is JobStatus.GLOSSARY_FIX):
        # Воркер успел завершиться раньше окна — результат полный и валидный.
        _wait_status(repo, job.id, JobStatus.COMPLETED, 300)
        assert w2.wait(timeout=60) == 0
        assert _chapter_of(target_file) == post_chapter
        return {"killed": False}

    lease_at_kill = state.lease_expires_at
    assert lease_at_kill is not None
    assert lease_at_kill >= time.time(), "арена действенна (heartbeat продлевает)"
    os.kill(w2.pid, signal.SIGKILL)
    assert w2.wait(timeout=20) == -9

    # Сразу после kill: target — либо доправки состояние, либо финальное;
    # в обоих случаях — валидный EPUB. l3bak (записан до .partial) — цел.
    after_chapter = _chapter_of(target_file)
    assert after_chapter in (pre_chapter, post_chapter), (
        "target повреждён: не доправки и не финальное состояние"
    )
    assert zipfile.is_zipfile(target_file)
    read_epub(target_file, log_dir / f"kill_{index}_check", settings)
    assert l3bak.is_file(), "бэкап l3bak должен существовать (записан до .partial)"
    assert zipfile.is_zipfile(l3bak)
    assert _chapter_of(l3bak) == pre_chapter, "бэкап = доправки состояние"

    # Задание осиротело: после истечения аренды новый воркер доводит до конца.
    time.sleep(max(0.0, lease_at_kill - time.time()) + 0.1)
    w3 = _start_worker(cfg, env_file, log_dir / f"kill_{index}_w3.log")
    workers.append(w3)
    _wait_status(repo, job.id, JobStatus.COMPLETED, 300)
    assert w3.wait(timeout=60) == 0
    assert _chapter_of(target_file) == post_chapter, "результат после resume — полный"
    read_epub(target_file, log_dir / f"kill_{index}_final", settings)
    return {"killed": True, "state_at_kill": "pre" if after_chapter == pre_chapter else "post"}


def test_l3_8_kill9_no_corruption(l3_env: L3Env, tmp_path: Path) -> None:
    """T-L3-8: kill -9 ×10 в окне пересборки — 0 повреждённых target (0 из 10):
    файл либо доправки, либо финальный; l3bak цел; после истечения аренды
    задание завершено с тем же результатом, что и эталонный прогон."""
    settings = l3_env._settings["kill"]
    repo = l3_env._repos["kill"]
    storage = l3_env.base / "storage_kill"
    cfg = storage / "config.yaml"
    env_file = storage / "test.env"
    book = l3_env.books["wexley_fix.epub"]
    workers: list[subprocess.Popen[bytes]] = []
    log_dir = tmp_path / "kill"
    log_dir.mkdir()
    # Сценарий глобальный (mock-сервер): расхождения обязаны быть, иначе
    # правок нет → пересборки нет → kill-окна (`.partial`) не существует.
    l3_env.server.set_scenario("glossary_inconsistent")
    results: list[dict[str, Any]] = []
    try:
        # Эталонный прогон (без kill): pre — на входе в GLOSSARY_FIX,
        # post — после COMPLETED. Контент главы детерминирован (rot3-mock).
        job_ref = create_job(repo, settings, source_path=book, target_language="ru")
        job_ref_dir = settings.jobs_dir / job_ref.id
        target_ref = job_ref_dir / "target.epub"
        w1 = _start_worker(cfg, env_file, log_dir / "ref_w1.log")
        workers.append(w1)
        _wait_status(repo, job_ref.id, JobStatus.NEEDS_CONFIRMATION, 180)
        assert w1.wait(timeout=60) == 0
        confirm_job(repo, job_ref.id, source_language="en")
        w2 = _start_worker(cfg, env_file, log_dir / "ref_w2.log")
        workers.append(w2)
        _wait_status(repo, job_ref.id, JobStatus.GLOSSARY_FIX, 300)
        pre_chapter = _chapter_of(target_ref)
        _wait_status(repo, job_ref.id, JobStatus.COMPLETED, 300)
        assert w2.wait(timeout=60) == 0
        post_chapter = _chapter_of(target_ref)
        assert pre_chapter != post_chapter, "в эталоне правки действительно применяются"

        for index in range(10):
            results.append(
                _kill_iteration(
                    settings,
                    repo,
                    cfg=cfg,
                    env_file=env_file,
                    log_dir=log_dir,
                    book=book,
                    index=index,
                    pre_chapter=pre_chapter,
                    post_chapter=post_chapter,
                    workers=workers,
                )
            )
    finally:
        _kill_all(workers)
        l3_env.server.set_scenario("ok")

    assert len(results) == 10
    killed = [r for r in results if r["killed"]]
    assert len(killed) >= 1, "окно пересборки (EPUBCheck JVM) не было задето ни разу"
    # CONTROL L3-8: 0 повреждённых target (все проверки — внутри итераций),
    # бэкап цел в каждом задетом случае.
    for result in results:
        assert result["killed"] in (True, False)
        if result["killed"]:
            assert result["state_at_kill"] in ("pre", "post")


# ------------------------------------------------------------------ T-L3-9…T-L3-15


def test_l3_9_unverified_only_reported(l3_env: L3Env) -> None:
    """L3-9: unverified-термин — 0 автоправок; 100% сообщено отдельным
    перечнем с собственной долей согласованности."""
    run = l3_env.run("unverif", "unverif.epub")
    assert run.error is None, str(run.error)
    report = run.l3_report
    assert report is not None and report["halted"] is False
    assert report["mismatches_input"] == 2, "расхождения обнаружены (L3.10)"
    assert report["attempted"] == 0, "автоправка unverified запрещена"
    assert report["applied"] == 0
    assert run.fix_requests == 0, "LLM не вызывался"
    unverified = {u["term"]: u for u in report["unverified_terms"]}
    assert "Badparadigm" in unverified
    assert unverified["Badparadigm"]["occurrences"] == 2
    assert unverified["Badparadigm"]["consistent"] == 0
    assert report["unverified_share"] == 0.0, "собственная доля перечня"
    assert any("Badparadigm" in w for w in run.warnings), "термин назван в предупреждении"
    target = run.job_dir / "target.epub"
    assert _sha256(target.read_bytes()) == _sha256(run.target_before), "книга не переписана"
    assert run.quality["glossary_consistency"]["mismatches"] == 2, "расхождения в отчёте"


def test_l3_10_five_languages_and_noop(l3_env: L3Env) -> None:
    """L3-10: 5 исходных языков (en/de/fr/it/nl → ru) — 5/5 завершены, стадия
    L3 присутствует с метриками; правка выключена — no-op, но обнаружение
    расхождений при этом не выключено."""
    books = (
        ("complex.epub", "en"),
        ("lang_de.epub", "de"),
        ("lang_fr.epub", "fr"),
        ("lang_it.epub", "it"),
        ("lang_nl.epub", "nl"),
    )
    done = 0
    for file_name, lang in books:
        run = l3_env.run(f"lang5_{lang}", file_name, lang=lang, scenario="ok")
        assert run.error is None, f"{file_name}: {run.error}"
        report = run.l3_report
        assert report is not None and report["enabled"] is True
        assert "halted" in report, f"{file_name}: полный отчёт (глоссарий собран)"
        l3 = run.quality.get("l3")
        assert l3 is not None, f"{file_name}: секция L3 в quality.json"
        for key in ("fixable_segment_share", "attempted", "applied", "editable_share_after"):
            assert key in l3, f"{file_name}: метрика {key}"
        assert report["applied"] == 0, f"{file_name}: расхождений нет (сценарий ok)"
        done += 1
    assert done == 5, "5/5 исходных языков"

    # Правка выключена — no-op: отчёт есть, правок нет, а расхождения
    # обнаруживаются и сообщаются (L3.10).
    nofix = l3_env.run("nofix_run", "wexley_fix.epub", profile="nofix")
    assert nofix.error is None, str(nofix.error)
    report = nofix.l3_report
    assert report is not None
    assert report["enabled"] is False
    assert report["reason"] == "правка L3 выключена (L3.10)"
    assert nofix.fix_requests == 0
    target = nofix.job_dir / "target.epub"
    assert _sha256(target.read_bytes()) == _sha256(nofix.target_before)
    assert nofix.quality["glossary_consistency"]["mismatches"] == 3, (
        "обнаружение расхождений не выключено (L3.10)"
    )
    assert nofix.warnings, "расхождения сообщены предупреждением"


def test_l3_11_three_variants_20_occurrences(l3_env: L3Env) -> None:
    """T-L3-11: имя, переведённое ТРЁМЯ разными способами в 20 вхождениях —
    после L3 все вхождения — каноническая форма; ≤20 записей в quality.log,
    пропущенных нет (0 отклонений — объяснить нечего)."""
    run = l3_env.run("triple20", "wexley20.epub", profile="wide",
                     scenario="glossary_inconsistent_triple")
    assert run.error is None, str(run.error)
    report = run.l3_report
    assert report is not None, "нет L3-отчёта"
    wrong = ("Xrongy", "Yrrqjz", "Zssabw")
    pre_chapter = _chapter_of(run.job_dir / "target.epub.l3bak").decode("utf-8")
    present = {v for v in wrong if v in pre_chapter}
    assert len(present) == 3, f"до правки — три разные неверные формы, got {present}"
    assert report["fixable_segment_share"] == pytest.approx(20 / 21, abs=1e-6)
    assert report["attempted"] == 20
    assert report["applied"] == 20, "все 20 вхождений исправлены"
    assert report["rejected"] == 0, "пропущенных нет — перечень объяснений пуст"
    # M6 §4.6: +1 прогрев analysis перед L3 (не запрос на вхождение, не повтор).
    assert run.fix_requests == 21, "20 на вхождения + 1 прогрев, повторов нет"
    lines = _fix_log_lines(run.quality_log)
    assert len(lines) == 20, "quality.log: ровно 20 записей (≤20)"
    final = _chapter_of(run.job_dir / "target.epub").decode("utf-8")
    assert final.count("Zhaohb") == 20, "все вхождения — каноническая форма парадигмы"
    assert all(final.count(v) == 0 for v in wrong), "неверные формы устранены"
    assert report["editable_share_after"] == 1.0


def test_l3_12_hyperlink_footnote_preserved(l3_env: L3Env) -> None:
    """T-L3-12: гиперссылка и сноска внутри правимого предложения — правка
    сохраняет обе (иначе отклонялась бы пунктом 3); разметка в итоговом блоке."""
    run = l3_env.run("markup", "wexley_markup.epub")
    assert run.error is None, str(run.error)
    report = run.l3_report
    assert report is not None, "нет L3-отчёта"
    applied = [f for f in report["fixes"] if f["applied"]]
    assert len(applied) == 2
    markup_fixes = [f for f in applied if f.get("new_html") and "notefoot" in f["new_html"]]
    assert len(markup_fixes) == 1, "правка разметочного сегмента применена"
    fix = markup_fixes[0]
    assert fix["validation_points"] == [], "валидация пройдена (разметка сохранена)"
    assert 'href="https://example.org/wexley"' in fix["new_html"], "гиперссылка сохранена"
    assert 'epub:type="notefoot"' in fix["new_html"], "сноска сохранена"
    final = _chapter_of(run.job_dir / "target.epub").decode("utf-8")
    assert 'href="https://example.org/wexley"' in final
    assert final.count('epub:type="notefoot"') >= 2
    assert run.warnings == []


def test_l3_14_rejected_without_term(l3_env: L3Env) -> None:
    """T-L3-14: модель вернула версию без требуемого термина — правка
    отклонена (пункт 1), расхождение неустранено, повторной попытки сверх
    лимита нет (ровно 1 запрос на сегмент)."""
    run = l3_env.run("noterm", "wexley_no_term.epub", scenario="glossary_fix_no_term")
    assert run.error is None, str(run.error)
    report = run.l3_report
    assert report is not None and report["halted"] is False
    assert report["attempted"] == 3
    assert report["applied"] == 0
    assert report["rejected"] == 3
    assert all(f["validation_points"] == [1] for f in report["fixes"]), (
        "отказ — по пункту 1 (форма парадигмы отсутствует)"
    )
    # M6 §4.6: +1 прогрев analysis перед L3 (не повторной попытки, не запрос).
    assert run.fix_requests == 4, "3 на сегменты + 1 прогрев; повторной попытки нет"
    assert run.quality["glossary_consistency"]["mismatches"] == 3, (
        "расхождения неустранены — в финальном отчёте"
    )
    target = run.job_dir / "target.epub"
    assert _sha256(target.read_bytes()) == _sha256(run.target_before), "книга не переписана"
    assert any("не прошли валидацию" in w for w in run.warnings), (
        "отклонённые правки в финальных предупреждениях (статус CW)"
    )


def test_l3_15_backup_diff_equals_log(l3_env: L3Env) -> None:
    """T-L3-15: сравнение резервной копии и результата — ровно тот список
    изменений (блоки главы), что записан в quality.log; остальные файлы
    архива побайтово не изменились."""
    run = l3_env.run("fix", "wexley_fix.epub")
    report = run.l3_report
    assert report is not None, "нет L3-отчёта"
    job_dir = run.job_dir
    l3bak = job_dir / "target.epub.l3bak"
    target = job_dir / "target.epub"
    with zipfile.ZipFile(l3bak) as zbak, zipfile.ZipFile(target) as ztarget:
        assert zbak.namelist() == ztarget.namelist()
        for name in zbak.namelist():
            if name != L3_CHAPTER:
                assert zbak.read(name) == ztarget.read(name), f"лишнее изменение: {name}"
        old_root = lxml.etree.fromstring(zbak.read(L3_CHAPTER))
        new_root = lxml.etree.fromstring(ztarget.read(L3_CHAPTER))
    old_body = old_root.find(_XH + "body")
    new_body = new_root.find(_XH + "body")
    assert old_body is not None and new_body is not None, "нет <body>"
    old_blocks = list(old_body)
    new_blocks = list(new_body)
    assert len(old_blocks) == len(new_blocks)
    changed: list[int] = []
    for i in range(len(old_blocks)):
        old_b = lxml.etree.tostring(old_blocks[i], encoding="unicode")
        new_b = lxml.etree.tostring(new_blocks[i], encoding="unicode")
        if old_b != new_b:
            changed.append(i)
    fixes = [f for f in report["fixes"] if f["applied"]]
    explained: set[int] = set()
    for index in changed:
        old_text = normalize_text(full_text(old_blocks[index]))
        new_text = normalize_text(full_text(new_blocks[index]))
        match = next(
            (f for f in fixes if f["old_text"] == old_text and f["new_text"] == new_text),
            None,
        )
        assert match is not None, (
            f"необъяснённое изменение блока {index}: {old_text!r} -> {new_text!r}"
        )
        explained.add(index)
    assert len(changed) == len(fixes) == 3, (
        f"изменено блоков: {len(changed)}, правок в логe: {len(_fix_log_lines(run.quality_log))}"
    )
    assert len(_fix_log_lines(run.quality_log)) == len(changed)
    assert explained == set(changed)
