"""Тесты S5: сборка учебного EPUB и экспорт карточек (ТЗ_03 §S5, CONTROL S5-1…S5-9).

Сквозной in-process прогон (Pipeline) — по образцу S1Env; mock-сервер (D3) —
детерминированный. Сборка — ДОПОЛНЕНИЕ target.epub (S5.1): секции в конец
глав, OPF/nav правка, финальный документ «Ответы и охват» в конец spine,
атомарная выдача (Rule 8).

- T-S5-1 — EPUBCheck 0 ERROR на learning EPUB, 5 языковых фикстур (S5-1).
- T-S5-2 — mimetype первой записью без сжатия, остальные сжаты, zipinfo
  verbatim в отчёте (S5-2).
- T-S5-3 — сохранность обложки/изображений/стилей/ссылок: записи target,
  кроме структурных, побайтово; оригинальные блоки глав — префикс (S5-3).
- T-S5-4 — записи оглавления для всех добавленных секций, 100% (S5-4).
- T-S5-5 — «Ответ:» в телах глав 0; ответы только в финальном документе
  (S5-5).
- T-S5-6 — обязательные поля карточек в экспорте (артикль de/nl, род fr/it)
  — 100% строк CSV/Anki/JSON (S5-6).
- T-S5-7 — структура Anki-экспорта (BLOCKER: Anki в окружении отсутствует —
  фактический импорт не проверяется, S5-7).
- T-S5-8 — охват в книге = E6 (jobs.alignment_coverage), расхождение 0
  (S5-8).
- T-S5-9 — kill -9 ×10 на записи learning EPUB: 0 повреждённых принятых
  файлов (S5-9).
- T-S5-10 — Minimal: в книге нет секций лексики/грамматики/вопросов.
- T-S5-11 — Intensive: все существующие секции присутствуют (эквивалентность
  «артефакт ⇔ секция»), ответы только в финальном документе.
- T-S5-12 — искусственный охват 90%: отражён в книге, пропущенные главы
  перечислены, расхождение с E6 = 0.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import posixpath
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from lxml import etree

from app.adapters.epub_engine.epubcheck import run_epubcheck
from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import attach_job_logs, detach_job_logs
from app.domain.job import Job, JobStatus
from app.epub import segmenter
from app.epub.reader import read_epub
from app.languages.cards import load_card_schema
from app.languages.registry import load_registry
from app.learning import card_export, study_book
from app.services.jobs import confirm_job, create_job
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.workers.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "epub"
_TESTS_DIR = str(Path(__file__).resolve().parent)
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from mock_llm_server import MockLLMServer  # noqa: E402
from test_l2_glossary import _make_storage, _write_epub  # noqa: E402
from test_l3_glossary_fix import _kill_all, _start_worker, _wait_status  # noqa: E402
from test_s4_study import (  # noqa: E402
    _S4_DE_PARAS,
    _S4_EN_POOLS,
    _S4_FR_PARAS,
    _S4_IT_PARAS,
    _S4_NL_PARAS,
    _synth_book_multi,
    _synth_s4_en1_book,
    _synth_s4_en_book,
)

# ------------------------------------------------------------------ книги


def _s5_10_paras(pool: tuple[str, ...]) -> tuple[str, ...]:
    """10 предложений: каждое слово пула — в каждом предложении."""
    a, b, c, d, e = pool
    words = (a, b, c, d, e)
    return tuple(
        f"The {words[k % 5]} near the {words[(k + 1) % 5]} was seen by the "
        f"{words[(k + 2) % 5]} as the {words[(k + 3) % 5]} rose over the "
        f"{words[(k + 4) % 5]}."
        for k in range(10)
    )


def _synth_s5_10_book(out_path: Path) -> None:
    """10 глав × 10 <p> (T-S5-12: 100 сегментов, 1 глава → 90%)."""
    bodies = tuple(
        "\n".join(
            f'    <p id="p{i}">{p}</p>' for i, p in enumerate(_s5_10_paras(pool), start=1)
        )
        for pool in _S4_EN_POOLS[:10]
    )
    _synth_book_multi(out_path, "S5 Ten", "en", "s5-10-uid-0003", bodies)


_S5_LANG_PARAS: dict[str, tuple[str, ...]] = {
    "de": _S4_DE_PARAS,
    "fr": _S4_FR_PARAS,
    "it": _S4_IT_PARAS,
    "nl": _S4_NL_PARAS,
}


def _synth_s5_lang_book(out_path: Path, lang: str, uid: str) -> None:
    """20 глав из языкового пула (T-S5-6: число слов выше плотности S1,
    иначе `max_cards = floor(15 × слов / 1000)` = 0 и карточек нет)."""
    paras = _S5_LANG_PARAS[lang]
    bodies = tuple(
        "\n".join(
            f'    <p id="p{i}">{p}</p>' for i, p in enumerate(paras, start=1)
        )
        for _ in range(20)
    )
    _synth_book_multi(out_path, f"S5 Lang {lang.upper()}", lang, uid, bodies)


#: EPUB 2.0 / XHTML 1.1 — паттерн гутенберговских книг (V1R.5 v1_en.epub).
#: T-S5-13 (D83/D84): движок ломает OPF-атрибуты (ремонт — D83), S5 в XHTML 1.1
#: вставляет секции (D84: div, не section) — на таком источнике оба дефекта
#: реально проходят через конвейер, в отличие от HTML5-фикстур.
_S5_EPUB2_UID = "http://example.org/s5-epub2"
_S5_EPUB2_DOCTYPE = (
    '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
    '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
)


def _synth_s5_epub2_book(out_path: Path) -> None:
    """EPUB 2.0.1: XHTML 1.1 главы, NCX вместо nav, OPF 2.0 с opf:-атрибутами.

    Источник обязан быть EPUBCheck-чистым (проверяется в T-S5-13), чтобы
    ошибки в артефактах были произведением движка/S5, а не фикстуры.
    """
    chapters = []
    for i, pool in enumerate(_S4_EN_POOLS[:3], start=1):
        body = "\n".join(
            f'    <p id="p{i}-{n}">{p}</p>'
            for n, p in enumerate(_s5_10_paras(pool), start=1)
        )
        chapters.append(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            + _S5_EPUB2_DOCTYPE
            + f'\n<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">\n'
            f"  <head><title>Chapter {i}</title></head>\n"
            f"  <body>\n    <h1 id=\"h{i}\">Chapter {i}</h1>\n{body}\n  </body>\n"
            "</html>\n"
        )
    items = "\n".join(
        f'    <item id="ch{n}" href="ch{n}.xhtml" media-type="application/xhtml+xml"/>'
        for n in range(1, 4)
    )
    itemrefs = "\n".join(f'    <itemref idref="ch{n}"/>' for n in range(1, 4))
    navpoints = "\n".join(
        '    <navPoint id="navpoint-'
        f'{n}" playOrder="{n}">\n'
        f"      <navLabel><text>Chapter {n}</text></navLabel>\n"
        f'      <content src="ch{n}.xhtml"/>\n'
        "    </navPoint>"
        for n in range(1, 4)
    )
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
        'unique-identifier="s5epub2id"\n'
        '  xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
        '  xmlns:opf="http://www.idpf.org/2007/opf"\n'
        '  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        "  <metadata>\n"
        f'    <dc:identifier id="s5epub2id" opf:scheme="URI">{_S5_EPUB2_UID}</dc:identifier>\n'
        '    <dc:creator opf:file-as="Author, S5" opf:role="aut">S5 Author</dc:creator>\n'
        "    <dc:title>S5 EPUB 2.0 Book</dc:title>\n"
        '    <dc:language xsi:type="dcterms:RFC4646">en</dc:language>\n'
        '    <dc:date opf:event="publication">2001-10-01</dc:date>\n'
        "  </metadata>\n"
        "  <manifest>\n"
        f"{items}\n"
        '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>\n'
        "  </manifest>\n"
        '  <spine toc="ncx">\n'
        f"{itemrefs}\n"
        "  </spine>\n"
        "  <guide>\n"
        '    <reference type="toc" title="Contents" href="ch1.xhtml"/>\n'
        "  </guide>\n"
        "</package>\n"
    )
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        "  <head>\n"
        f'    <meta name="dtb:uid" content="{_S5_EPUB2_UID}"/>\n'
        '    <meta name="dtb:depth" content="1"/>\n'
        '    <meta name="dtb:totalPageCount" content="0"/>\n'
        '    <meta name="dtb:maxPageNumber" content="0"/>\n'
        "  </head>\n"
        "  <docTitle><text>S5 EPUB 2.0 Book</text></docTitle>\n"
        "  <navMap>\n"
        f"{navpoints}\n"
        "  </navMap>\n"
        "</ncx>\n"
    )
    files: dict[str, str | bytes] = {
        "META-INF/container.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            "  <rootfiles>\n"
            '    <rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/>\n'
            "  </rootfiles>\n"
            "</container>\n"
        ),
        "OEBPS/content.opf": opf,
        "OEBPS/toc.ncx": ncx,
    }
    for n, chapter in enumerate(chapters, start=1):
        files[f"OEBPS/ch{n}.xhtml"] = chapter
    _write_epub(out_path, files)


# ------------------------------------------------------------------ окружение


@dataclass
class S5Run:
    """Сквозной in-process прогон до стадии LEARNING (включая её)."""

    name: str
    profile: str
    job: Job
    job_dir: Path
    quality: dict[str, Any]
    warnings: list[str]
    s5_report: dict[str, Any] | None
    error: AppError | None = None


@dataclass
class S5Env:
    server: MockLLMServer
    base: Path
    books: dict[str, Path]
    _settings: dict[str, Settings] = field(default_factory=dict)
    _repos: dict[str, JobRepository] = field(default_factory=dict)
    _dbs: dict[str, Database] = field(default_factory=dict)
    runs: dict[str, S5Run] = field(default_factory=dict)

    def add_profile(self, name: str, settings: Settings, db: Database) -> None:
        self._settings[name] = settings
        self._dbs[name] = db
        self._repos[name] = JobRepository(db)

    def _run_common(
        self,
        name: str,
        book: str,
        *,
        profile: str,
        scenario: str,
        lang: str,
        learning_level: str,
        learning_cefr: str,
    ) -> tuple[Job, Pipeline, JobRepository]:
        """Прогон до GLOSSARY_FIX включительно (для T-S5-12 — точка white-box
        инъекции после run_validating_output)."""
        settings = self._settings[profile]
        repo = self._repos[profile]
        source = self.books[book] if book in self.books else FIXTURES / book
        self.server.set_scenario(scenario)
        job = create_job(
            repo,
            settings,
            source_path=source,
            target_language="ru",
            learning_level=learning_level,
            learning_cefr=learning_cefr,
        )
        pipeline = Pipeline(settings)
        repo.transition(job.id, JobStatus.VALIDATING, reason="начало этапа (T-S5)")
        needs_confirm = pipeline.run_validating(repo, repo.require(job.id))
        if needs_confirm:
            repo.transition(job.id, JobStatus.NEEDS_CONFIRMATION, reason="L0.5 (T-S5)")
            confirm_job(repo, job.id, source_language=lang)
        else:
            repo.transition(job.id, JobStatus.PLANNING, reason="язык принят (T-S5)")
        pipeline.run_planning(repo, repo.require(job.id))
        repo.transition(job.id, JobStatus.TRANSLATING, reason="начало этапа (T-S5)")
        pipeline.run_translating(repo, repo.require(job.id), lambda: None)
        repo.transition(job.id, JobStatus.ANALYZING, reason="начало этапа (T-S5)")
        pipeline.run_analyzing(repo, repo.require(job.id))
        repo.transition(job.id, JobStatus.GENERATING, reason="начало этапа (T-S5)")
        pipeline.run_generating(repo, repo.require(job.id))
        repo.transition(job.id, JobStatus.VALIDATING_OUTPUT, reason="начало этапа (T-S5)")
        pipeline.run_validating_output(repo, repo.require(job.id))
        repo.transition(job.id, JobStatus.GLOSSARY_FIX, reason="начало этапа (T-S5)")
        return job, pipeline, repo

    def run(
        self,
        name: str,
        book: str,
        *,
        profile: str = "main",
        scenario: str = "ok",
        lang: str = "en",
        learning_level: str = "Balanced",
        learning_cefr: str = "A1",
    ) -> S5Run:
        """Ленивый кэшируемый прогон до LEARNING (терминальный переход —
        воркер, как в S1-тестах)."""
        if name in self.runs:
            return self.runs[name]
        settings = self._settings[profile]
        repo = self._repos[profile]
        job, pipeline, _repo = self._run_common(
            name,
            book,
            profile=profile,
            scenario=scenario,
            lang=lang,
            learning_level=learning_level,
            learning_cefr=learning_cefr,
        )
        job_dir = settings.job_dir(job.id)
        error: AppError | None = None
        warnings: list[str] = []
        handlers = attach_job_logs(job_dir, settings.secrets())
        try:
            try:
                pipeline.run_glossary_fix(repo, repo.require(job.id))
                repo.transition(job.id, JobStatus.LEARNING, reason="начало этапа (T-S5)")
                warnings = pipeline.run_learning(repo, repo.require(job.id))
            except AppError as exc:
                error = exc
        finally:
            detach_job_logs(handlers)
        s5_path = job_dir / "s5_report.json"
        report: dict[str, Any] | None = (
            json.loads(s5_path.read_text(encoding="utf-8"))
            if s5_path.is_file()
            else None
        )
        run = S5Run(
            name=name,
            profile=profile,
            job=repo.require(job.id),
            job_dir=job_dir,
            quality=json.loads((job_dir / "quality.json").read_text(encoding="utf-8")),
            warnings=warnings,
            s5_report=report,
            error=error,
        )
        self.runs[name] = run
        return run

    def run_coverage90(self, name: str, book: str) -> S5Run:
        """T-S5-12: прогон с БЕЛОЯЩИЧНЫМ 90% (ТЗ: «искусственно 90%»): после
        run_validating_output — патч alignment_report.json (1 из 10 глав
        невыровнена) + jobs.alignment_coverage = 0.9, затем GLOSSARY_FIX →
        LEARNING."""
        if name in self.runs:
            return self.runs[name]
        settings = self._settings["main"]
        repo = self._repos["main"]
        job, pipeline, _repo = self._run_common(
            name,
            book,
            profile="main",
            scenario="ok",
            lang="en",
            learning_level="Intensive",
            learning_cefr="A1",
        )
        job_dir = settings.job_dir(job.id)
        _patch_alignment(job_dir, "chapter3.xhtml", coverage=0.9)
        repo.set_alignment_coverage(job.id, 0.9)
        error: AppError | None = None
        warnings: list[str] = []
        handlers = attach_job_logs(job_dir, settings.secrets())
        try:
            try:
                pipeline.run_glossary_fix(repo, repo.require(job.id))
                repo.transition(job.id, JobStatus.LEARNING, reason="начало этапа (T-S5)")
                warnings = pipeline.run_learning(repo, repo.require(job.id))
            except AppError as exc:
                error = exc
        finally:
            detach_job_logs(handlers)
        s5_path = job_dir / "s5_report.json"
        report: dict[str, Any] | None = (
            json.loads(s5_path.read_text(encoding="utf-8"))
            if s5_path.is_file()
            else None
        )
        run = S5Run(
            name=name,
            profile="main",
            job=repo.require(job.id),
            job_dir=job_dir,
            quality=json.loads((job_dir / "quality.json").read_text(encoding="utf-8")),
            warnings=warnings,
            s5_report=report,
            error=error,
        )
        self.runs[name] = run
        return run

    def close(self) -> None:
        for db in self._dbs.values():
            db.close()


def _patch_alignment(job_dir: Path, doc_suffix: str, coverage: float) -> None:
    """White-box (T-S5-12): глава → unaligned, coverage пересчитан."""
    path = job_dir / "alignment_report.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    doc = next(d for d in data["docs"] if d["doc"].endswith(doc_suffix))
    moved = [
        {
            "segment_id": p["segment_id"],
            "source_xpath": p["source_xpath"],
            "source_text": p["source_text"],
            "reason": "xpath_mismatch",
            "detail": "T-S5-12: искусственное невыравнивание",
        }
        for p in doc["aligned"]
    ]
    doc["aligned"] = []
    doc["unaligned"] = moved
    data["aligned"] = data["aligned"] - len(moved)
    data["unaligned"] = data["unaligned"] + len(moved)
    data["coverage"] = round(data["aligned"] / data["total"], 6)
    assert abs(data["coverage"] - coverage) < 1e-9, (
        f"искусственный охват {data['coverage']} != {coverage}"
    )
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture(scope="module")
def s5_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[S5Env]:
    base = tmp_path_factory.mktemp("s5_module")
    books_dir = base / "books"
    books_dir.mkdir()
    _synth_s5_epub2_book(books_dir / "s5_epub2.epub")
    _synth_s4_en_book(books_dir / "s5_en.epub")
    _synth_s4_en1_book(books_dir / "s5_en1.epub")
    _synth_s5_10_book(books_dir / "s5_10.epub")
    for lang, uid in (
        ("de", "s5-de-uid-0010"),
        ("fr", "s5-fr-uid-0011"),
        ("it", "s5-it-uid-0012"),
        ("nl", "s5-nl-uid-0013"),
    ):
        _synth_s5_lang_book(books_dir / f"s5_{lang}.epub", lang, uid)
    server = MockLLMServer()
    server.start()
    env = S5Env(
        server=server,
        base=base,
        books={
            "s5_epub2.epub": books_dir / "s5_epub2.epub",
            "s5_en.epub": books_dir / "s5_en.epub",
            "s5_en1.epub": books_dir / "s5_en1.epub",
            "s5_10.epub": books_dir / "s5_10.epub",
            "s5_de.epub": books_dir / "s5_de.epub",
            "s5_fr.epub": books_dir / "s5_fr.epub",
            "s5_it.epub": books_dir / "s5_it.epub",
            "s5_nl.epub": books_dir / "s5_nl.epub",
        },
    )
    main_settings, main_db = _make_storage(base, "storage_main", server)
    env.add_profile("main", main_settings, main_db)
    kill_settings, kill_db = _make_storage(
        base,
        "storage_kill",
        server,
        extra="server:\n  heartbeat_seconds: 0.5\n  lease_seconds: 1.0\n",
    )
    env.add_profile("kill", kill_settings, kill_db)
    try:
        yield env
    finally:
        env.close()
        server.stop()


# ------------------------------------------------------------------ служебное


def _s5_report(run: S5Run) -> dict[str, Any]:
    assert run.error is None, f"стадия S5 завершилась ошибкой: {run.error}"
    assert run.s5_report is not None, "нет s5_report.json"
    return run.s5_report


def _answers_doc(learning_epub: Path) -> str:
    with zipfile.ZipFile(learning_epub) as archive:
        return archive.read(_answers_path(archive)).decode("utf-8")


def _answers_path(archive: zipfile.ZipFile) -> str:
    _opf_path, opf_dir, _nav, _ncx = study_book.locate_structure(archive)
    return study_book.join_path(opf_dir, study_book.ANSWERS_HREF)


def _nav_hrefs(learning_epub: Path) -> set[str]:
    with zipfile.ZipFile(learning_epub) as archive:
        nav_root = etree.fromstring(archive.read("OEBPS/nav.xhtml"))
    return {
        a.get("href") or ""
        for a in nav_root.iter()
        if segmenter.localname(a) == "a"
    }


def _artifact_chapters(job_dir: Path) -> dict[str, set[str]]:
    """Типы секций, которые ОБЯЗАНЫ быть у главы (наличие артефакта)."""
    present: dict[str, set[str]] = {}

    def _add(fname: str, fieldname: str, section_type: str) -> None:
        path = job_dir / fname
        if not path.is_file():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        for record in data.get(fieldname, ()):
            if not isinstance(record, dict):
                continue
            if section_type == "summary" and record.get("status", "issued") != "issued":
                continue
            present.setdefault(str(record.get("chapter_id", "")), set()).add(section_type)

    _add("summaries.json", "chapters", "summary")
    _add("vocabulary_cards.json", "cards", "vocab")
    _add("mwe_records.json", "records", "expressions")
    _add("grammar_notes.json", "notes", "grammar")
    _add("questions.json", "questions", "questions")
    _add("exercises.json", "exercises", "exercises")
    return present


def _epub_valid(epub: Path, settings: Settings, unpack: Path) -> None:
    assert zipfile.is_zipfile(epub), f"{epub.name} — не zip"
    read_epub(epub, unpack, settings)


def _s5_key(pipeline: Pipeline, job: Job) -> str:
    glossary_hash = pipeline._learning_glossary_hash(job)
    return hashlib.sha256(
        "|".join(
            (
                pipeline.input_hash(job),
                glossary_hash,
                job.learning_level or "",
                job.learning_cefr or "",
            )
        ).encode()
    ).hexdigest()


def _rerun_s5_assembly(settings: Settings, repo: JobRepository, job_id: str) -> None:
    """In-process повтор S5-сборки (детерминизм: тот же target + артефакты)."""
    pipeline = Pipeline(settings)
    job = repo.require(job_id)
    job_dir = settings.job_dir(job.id)
    handlers = attach_job_logs(job_dir, settings.secrets())
    try:
        pipeline._s5_assembly(
            repo,
            job,
            job_dir,
            _s5_key(pipeline, job),
            pipeline._learning_glossary_hash(job),
        )
    finally:
        detach_job_logs(handlers)


# ------------------------------------------------------------------ T-S5-1


def test_s5_1_epubcheck_five_languages(s5_env: S5Env) -> None:
    # S5-1: EPUBCheck 0 ERROR на learning EPUB для 5 языковых фикстур.
    cases = (
        ("intensive_complex", "complex.epub", "en"),
        ("intensive_de", "lang_de.epub", "de"),
        ("intensive_fr", "lang_fr.epub", "fr"),
        ("intensive_it", "lang_it.epub", "it"),
        ("intensive_nl", "lang_nl.epub", "nl"),
    )
    for name, book, lang in cases:
        run = s5_env.run(name, book, lang=lang, learning_level="Intensive")
        report = _s5_report(run)
        epubcheck = report["epub"]["epubcheck"]
        assert epubcheck["available"] is True, f"{lang}: EPUBCheck недоступен"
        assert epubcheck["errors"] == 0, f"{lang}: {epubcheck}"
        learning = run.job_dir / "learning.epub"
        _epub_valid(learning, s5_env._settings[run.profile],
                    run.job_dir / f"check_{lang}")
        with zipfile.ZipFile(learning) as archive:
            assert archive.namelist()[0] == "mimetype"


# ------------------------------------------------------------------ T-S5-2


def test_s5_2_mimetype_first_zipinfo(s5_env: S5Env) -> None:
    # S5-2: mimetype — первой записью, без сжатия; остальные сжаты;
    # zipinfo verbatim в отчёте.
    run = s5_env.run("intensive_en20", "s5_en.epub", learning_level="Intensive")
    report = _s5_report(run)
    learning = run.job_dir / "learning.epub"
    with zipfile.ZipFile(learning) as archive:
        infos = archive.infolist()
        assert infos[0].filename == "mimetype"
        assert infos[0].compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        assert all(
            info.compress_type == zipfile.ZIP_DEFLATED for info in infos[1:]
        ), "остальные записи — сжаты"
    zipinfo = report["zipinfo"]
    assert zipinfo.startswith("Archive:"), "zipinfo verbatim отсутствует"
    lines = zipinfo.splitlines()
    entries = [line for line in lines[2:] if line[:1] in ("?", "-")]
    assert entries, "zipinfo: список записей пуст"
    first = entries[0]
    assert first.rstrip().endswith("mimetype"), (
        f"mimetype не первой строкой списка записей: {first!r}"
    )
    assert " stor " in first, f"mimetype сжат (ожидается stor): {first!r}"


# ------------------------------------------------------------------ T-S5-3


def test_s5_3_preservation(s5_env: S5Env) -> None:
    # S5-3: записи target, кроме структурных, — побайтово; оригинальные
    # блоки главы — точный префикс новой.
    run = s5_env.run("intensive_complex", "complex.epub", learning_level="Intensive")
    report = _s5_report(run)
    settings = s5_env._settings[run.profile]
    target = run.job_dir / "target.epub"
    learning = run.job_dir / "learning.epub"
    book = read_epub(target, run.job_dir / "pre3", settings)
    doc_by_id = {ch.chapter_id: ch.document_path for ch in book.chapters}
    with zipfile.ZipFile(target) as tzip, zipfile.ZipFile(learning) as lzip:
        t_names = tzip.namelist()
        patched = {doc_by_id[cid] for cid in report["sections"]}
        answers = _answers_path(lzip)
        structural = {
            "OEBPS/content.opf",
            "OEBPS/nav.xhtml",
            answers,
            *patched,
        }
        for name in t_names:
            if name in structural:
                continue
            assert name in lzip.namelist(), f"{name} отсутствует в сборке"
            assert lzip.read(name) == tzip.read(name), f"{name} изменена"
        # оригинальные блоки каждой главы — префикс
        tags = frozenset(settings.segmentation.block_tags)
        for chapter in book.chapters:
            old_body = segmenter.find_body(
                etree.fromstring(tzip.read(chapter.document_path))
            )
            new_body = segmenter.find_body(
                etree.fromstring(lzip.read(chapter.document_path))
            )
            old_blocks = [
                segmenter.full_text(e)
                for e in segmenter.find_block_elements(old_body, tags)
            ]
            new_blocks = [
                segmenter.full_text(e)
                for e in segmenter.find_block_elements(new_body, tags)
            ]
            assert new_blocks[: len(old_blocks)] == old_blocks, (
                f"{chapter.document_path}: оригинальные блоки изменены"
            )
    # обложка/стили/изображения complex.epub — побайтово (явно)
    with zipfile.ZipFile(target) as tzip, zipfile.ZipFile(learning) as lzip:
        for name in ("OEBPS/style.css", "OEBPS/img/dot.png", "META-INF/container.xml"):
            assert lzip.read(name) == tzip.read(name), f"{name} не сохранён"
    assert report["epub"]["entries_total"] == (
        len(zipfile.ZipFile(target).namelist()) + 1
    )


# ------------------------------------------------------------------ T-S5-4


def test_s5_4_toc_entries(s5_env: S5Env) -> None:
    # S5-4: запись оглавления для КАЖДОЙ добавленной секции (100%).
    run = s5_env.run("intensive_en20", "s5_en.epub", learning_level="Intensive")
    report = _s5_report(run)
    settings = s5_env._settings[run.profile]
    learning = run.job_dir / "learning.epub"
    book = read_epub(
        run.job_dir / "target.epub", run.job_dir / "toc_book", settings
    )
    doc_by_id = {ch.chapter_id: ch.document_path for ch in book.chapters}
    nav_hrefs = _nav_hrefs(learning)
    expected = 0
    for chapter_id, section_types in report["sections"].items():
        doc = doc_by_id[chapter_id]
        rel = posixpath.relpath(doc, posixpath.dirname("OEBPS/nav.xhtml"))
        for section_type in section_types:
            href = f"{rel}#s5-{section_type}-{chapter_id}"
            assert href in nav_hrefs, f"нет записи оглавления: {href}"
            expected += 1
    assert expected > 0, "секции должны быть (Intensive)"
    assert "study-answers.xhtml" in nav_hrefs, "нет записи финального документа"


# ------------------------------------------------------------------ T-S5-5


def test_s5_5_answers_not_in_chapters(s5_env: S5Env) -> None:
    # S5-5: «Ответ:» в телах глав — 0 вхождений; ответы — только в
    # финальном документе.
    run = s5_env.run("intensive_en20", "s5_en.epub", learning_level="Intensive")
    report = _s5_report(run)
    assert report["sections"], "Intensive: секции обязаны быть"
    learning = run.job_dir / "learning.epub"
    patched_docs = set()
    settings = s5_env._settings[run.profile]
    book = read_epub(run.job_dir / "target.epub", run.job_dir / "ch5", settings)
    with zipfile.ZipFile(learning) as archive:
        for chapter in book.chapters:
            if chapter.chapter_id in report["sections"]:
                patched_docs.add(chapter.document_path)
        for doc in patched_docs:
            text = archive.read(doc).decode("utf-8")
            assert "Ответ:" not in text, f"{doc}: «Ответ:» в теле главы"
    answers = _answers_doc(learning)
    assert "Ответ:" in answers, "ответы должны быть в финальном документе"


# ------------------------------------------------------------------ T-S5-6


def test_s5_6_export_required_fields(s5_env: S5Env) -> None:
    # S5-6: обязательные поля карточек (артикль de/nl, род fr/it) — в
    # экспорте, 100% строк; JSON — полный. Книги — 20-главные (число слов
    # выше плотности S1: на 1-главных фикстурах max_cards = 0).
    settings = s5_env._settings["main"]
    registry = load_registry(settings.languages_root)
    cases = (
        ("cards_de", "de", {"noun": ("article", ("der", "die", "das"))}),
        ("cards_nl", "nl", {"noun": ("article", ("de", "het"))}),
        ("cards_fr", "fr", {"noun": ("gender", ("masc", "fem"))}),
        ("cards_it", "it", {"noun": ("gender", ("masc", "fem"))}),
    )
    for name, lang, noun_checks in cases:
        run = s5_env.run(name, f"s5_{lang}.epub", lang=lang, learning_level="Intensive")
        _s5_report(run)
        cards = json.loads(
            (run.job_dir / "vocabulary_cards.json").read_text(encoding="utf-8")
        )["cards"]
        assert cards, f"{lang}: карточки должны быть (Intensive)"
        schema = load_card_schema(
            settings.languages_root / lang, registry[lang]
        )
        # CSV: полный набор столбцов; обязательные колонки — не пусты
        # (100% строк; объекты — по подстолбцам).
        with (run.job_dir / "cards.csv").open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            assert reader.fieldnames == list(card_export.export_columns(schema))
            rows = list(reader)
        assert len(rows) == len(cards)
        for card, row in zip(cards, rows, strict=True):
            for cell in sorted(card_export.required_cells(schema, card["pos"])):
                assert row.get(cell, "").strip(), (
                    f"{lang}: пустая обязательная колонка {cell} "
                    f"({card['lemma']}, pos={card['pos']})"
                )
            for pos, (fieldname, allowed) in noun_checks.items():
                if card["pos"] == pos:
                    assert row.get(fieldname) in allowed, (
                        f"{lang}: {fieldname}={row.get(fieldname)!r} вне enum"
                    )
        # Anki TSV: служебные строки, число полей, колонка языкового поля
        lines = (run.job_dir / "cards.anki.tsv").read_text(encoding="utf-8").splitlines()
        assert lines[0] == "#separator:Tab"
        assert lines[1] == "#html:false"
        columns = lines[2].removeprefix("#columns:").split("\t")
        assert "Front" in columns and "Back" in columns and "Tags" in columns
        for _pos, (fieldname, _allowed) in noun_checks.items():
            assert fieldname in columns, f"{lang}: колонка {fieldname} потеряна"
        data_lines = lines[3:]
        assert len(data_lines) == len(cards)
        for line in data_lines:
            assert len(line.split("\t")) == len(columns)
        # JSON: полный массив объектов
        exported = json.loads((run.job_dir / "cards.json").read_text(encoding="utf-8"))
        assert exported == cards


# ------------------------------------------------------------------ T-S5-7


def test_s5_7_anki_structure_blocker(s5_env: S5Env) -> None:
    # S5-7: импорт в Anki — BLOCKER (Anki в окружении отсутствует);
    # проверена структура экспорта (формат Anki text import), verbatim.
    run = s5_env.run("cards_de", "s5_de.epub", lang="de", learning_level="Intensive")
    _s5_report(run)
    anki_bin = next((shutil.which(name) for name in ("anki", "pyanki")), None)
    assert anki_bin is None, "Anki обнаружен — фактический импорт должен быть проведён"
    lines = (run.job_dir / "cards.anki.tsv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "#separator:Tab"
    assert lines[1] == "#html:false"
    assert lines[2].startswith("#columns:")
    columns = lines[2].removeprefix("#columns:").split("\t")
    for line in lines[3:]:
        fields = line.split("\t")
        assert len(fields) == len(columns)
        assert all("\t" not in f and "\n" not in f for f in fields)
        assert fields[-1].startswith("epubtranslator ")


# ------------------------------------------------------------------ T-S5-8


def test_s5_8_coverage_matches_e6(s5_env: S5Env) -> None:
    # S5-8: охват в книге = E6 (jobs.alignment_coverage), расхождение 0.
    run = s5_env.run("intensive_en20", "s5_en.epub", learning_level="Intensive")
    report = _s5_report(run)
    e6 = json.loads(
        (run.job_dir / "alignment_report.json").read_text(encoding="utf-8")
    )
    assert abs(e6["coverage"] - run.job.alignment_coverage) < 1e-9
    assert abs(report["coverage"] - run.job.alignment_coverage) < 1e-9
    answers = _answers_doc(run.job_dir / "learning.epub")
    marker = (
        f'title="s5-coverage={run.job.alignment_coverage:.6f} '
        f"s5-aligned={e6['aligned']} s5-total={e6['total']}\""
    )
    assert marker in answers, f"метка охвата (D86) в книге != E6: {marker}"


# ------------------------------------------------------------------ T-S5-9


def _wait_terminal(
    repo: JobRepository, job_id: str, timeout: float = 300
) -> Job:
    """Терминальное состояние: COMPLETED или COMPLETED_WITH_WARNINGS
    (малая kill-книга даёт информационные warning S2/S3 — «кандидаты не
    отобраны (0)» — и воркер честно ставит COMPLETED_WITH_WARNINGS)."""
    deadline = time.monotonic() + timeout
    last: Job | None = None
    while time.monotonic() < deadline:
        job = repo.get(job_id)
        if job is not None:
            last = job
            if job.status in (JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_WARNINGS):
                return job
        time.sleep(0.02)
    status = None if last is None else last.status.value
    raise TimeoutError(f"ожидание терминального состояния: последнее {status}")


def _s5_kill_iteration(
    settings: Settings,
    repo: JobRepository,
    *,
    cfg: Path,
    env_file: Path,
    log_dir: Path,
    book: Path,
    index: int,
    workers: list[subprocess.Popen[bytes]],
) -> dict[str, Any]:
    """Одна итерация kill -9: W1 → confirm → W2 → SIGKILL в окне записи
    learning.epub.partial → проверка принятых файлов → W3 доводит до конца."""
    job = create_job(
        repo,
        settings,
        source_path=book,
        target_language="ru",
        learning_level="Intensive",
        learning_cefr="A1",
    )
    job_dir = settings.jobs_dir / job.id
    target_file = job_dir / "target.epub"
    learning_final = job_dir / "learning.epub"
    partial = job_dir / "learning.epub.partial"

    w1 = _start_worker(cfg, env_file, log_dir / f"kill_{index}_w1.log")
    workers.append(w1)
    _wait_status(repo, job.id, JobStatus.NEEDS_CONFIRMATION, 180)
    assert w1.wait(timeout=60) == 0
    confirm_job(repo, job.id, source_language="en")

    w2 = _start_worker(cfg, env_file, log_dir / f"kill_{index}_w2.log")
    workers.append(w2)
    _wait_status(repo, job.id, JobStatus.LEARNING, 300)
    deadline = time.monotonic() + 240
    while not partial.exists():
        state = repo.get(job.id)
        if state is not None and state.status in (
            JobStatus.COMPLETED,
            JobStatus.COMPLETED_WITH_WARNINGS,
        ):
            break
        if time.monotonic() > deadline:
            break
        time.sleep(0.02)
    state = repo.get(job.id)
    assert state is not None
    if not (partial.exists() and state.status is JobStatus.LEARNING):
        # Воркер успел завершиться раньше окна — результат полный и валидный.
        _wait_terminal(repo, job.id)
        assert w2.wait(timeout=60) == 0
        assert learning_final.is_file()
        _epub_valid(learning_final, settings, log_dir / f"kill_{index}_early")
        return {"killed": False}

    lease_at_kill = state.lease_expires_at
    assert lease_at_kill is not None
    assert lease_at_kill >= time.time(), "арена действенна (heartbeat продлевает)"
    os.kill(w2.pid, signal.SIGKILL)
    assert w2.wait(timeout=20) == -9

    # Сразу после kill: принятый learning.epub — либо отсутствует, либо
    # валидный; target.epub — всегда цел (LEARNING его не трогает).
    if learning_final.exists():
        _epub_valid(learning_final, settings, log_dir / f"kill_{index}_at_kill")
    assert zipfile.is_zipfile(target_file)
    _epub_valid(target_file, settings, log_dir / f"kill_{index}_target")

    # Задание осиротело: после истечения аренды W3 доводит до конца.
    time.sleep(max(0.0, lease_at_kill - time.time()) + 0.1)
    w3 = _start_worker(cfg, env_file, log_dir / f"kill_{index}_w3.log")
    workers.append(w3)
    _wait_terminal(repo, job.id)
    assert w3.wait(timeout=60) == 0
    assert learning_final.is_file()
    _epub_valid(learning_final, settings, log_dir / f"kill_{index}_final")
    # Детерминизм S5-сборки: in-process повтор — побайтово тот же файл.
    final_bytes = learning_final.read_bytes()
    _rerun_s5_assembly(settings, repo, job.id)
    assert learning_final.read_bytes() == final_bytes, "S5-сборка недетерминирована"
    return {"killed": True}


def test_s5_9_kill9_no_corruption(s5_env: S5Env, tmp_path: Path) -> None:
    # S5-9: kill -9 ×10 на записи learning EPUB — 0 повреждённых принятых
    # файлов; после resume задание доходит до терминального состояния
    # (COMPLETED / COMPLETED_WITH_WARNINGS) с валидной книгой.
    settings = s5_env._settings["kill"]
    repo = s5_env._repos["kill"]
    storage = s5_env.base / "storage_kill"
    cfg = storage / "config.yaml"
    env_file = storage / "test.env"
    book = s5_env.books["s5_en1.epub"]
    workers: list[subprocess.Popen[bytes]] = []
    log_dir = tmp_path / "kill"
    log_dir.mkdir()
    results: list[dict[str, Any]] = []
    try:
        for index in range(10):
            results.append(
                _s5_kill_iteration(
                    settings,
                    repo,
                    cfg=cfg,
                    env_file=env_file,
                    log_dir=log_dir,
                    book=book,
                    index=index,
                    workers=workers,
                )
            )
    finally:
        _kill_all(workers)
    killed = sum(1 for r in results if r["killed"])
    assert len(results) == 10
    # Окно kill реально использовалось (не все итерации «дождались конца»).
    assert killed >= 1, f"ни одна итерация не попала в окно kill: {results}"


# ------------------------------------------------------------------ T-S5-10


def test_s5_10_minimal_no_sections(s5_env: S5Env) -> None:
    # T-S5-10: Minimal — в книге отсутствуют секции лексики, грамматики,
    # вопросов (и любые другие); книга собрана (охват + финальный документ).
    run = s5_env.run("minimal_en1", "s5_en1.epub", learning_level="Minimal")
    report = _s5_report(run)
    assert report["chapters_with_sections"] == 0
    assert report["sections"] == {}
    assert report["epub"]["epubcheck"]["errors"] == 0
    target = run.job_dir / "target.epub"
    learning = run.job_dir / "learning.epub"
    with zipfile.ZipFile(target) as tzip, zipfile.ZipFile(learning) as lzip:
        for name in tzip.namelist():
            if name in ("OEBPS/content.opf", "OEBPS/nav.xhtml"):
                continue
            assert lzip.read(name) == tzip.read(name), (
                f"Minimal: {name} изменён (секции не должны вставляться)"
            )
    nav_hrefs = _nav_hrefs(learning)
    s5_hrefs = [href for href in nav_hrefs if "#s5-" in href]
    assert s5_hrefs == [], f"Minimal: записи секций в оглавлении: {s5_hrefs}"
    answers = _answers_doc(learning)
    assert "Ответов нет" in answers
    assert "s5-coverage=" in answers


# ------------------------------------------------------------------ T-S5-11


def test_s5_11_intensive_all_sections(s5_env: S5Env) -> None:
    # T-S5-11: Intensive — все секции (эквивалентность «артефакт ⇔ секция»
    # по каждой главе), ответы только в финальном документе.
    run = s5_env.run("intensive_en20", "s5_en.epub", learning_level="Intensive")
    report = _s5_report(run)
    expected = _artifact_chapters(run.job_dir)
    assert expected, "Intensive: артефакты обязаны быть"
    union: set[str] = set()
    for chapter_types in expected.values():
        union |= chapter_types
    # Сводка/лексика/вопросы/упражнения на этой книге гарантированы (S4).
    assert {"summary", "vocab", "questions", "exercises"} <= union, f"отсутствуют: {union}"
    for chapter_id, section_types in report["sections"].items():
        assert set(section_types) == expected.get(chapter_id, set()), (
            f"{chapter_id}: секции {section_types} != артефакты "
            f"{sorted(expected.get(chapter_id, set()))}"
        )
    assert set(report["sections"]) == set(expected), "набор глав с секциями не совпал"
    # Ответы — только в финальном документе.
    learning = run.job_dir / "learning.epub"
    with zipfile.ZipFile(learning) as archive:
        for name in archive.namelist():
            if name.endswith((".xhtml",)) and study_book.ANSWERS_HREF not in name:
                assert "Ответ:" not in archive.read(name).decode("utf-8"), name
    assert "Ответ:" in _answers_doc(learning)


# ------------------------------------------------------------------ T-S5-12


def test_s5_12_partial_coverage_90(s5_env: S5Env) -> None:
    # T-S5-12: искусственный охват 90% — отражён в книге, пропущенные главы
    # перечислены, расхождение с E6 = 0.
    run = s5_env.run_coverage90("cover90", "s5_10.epub")
    report = _s5_report(run)
    assert abs(report["coverage"] - 0.9) < 1e-9
    assert abs(run.job.alignment_coverage - 0.9) < 1e-9
    e6 = json.loads((run.job_dir / "alignment_report.json").read_text(encoding="utf-8"))
    assert abs(e6["coverage"] - 0.9) < 1e-9
    assert e6["unaligned"] == 10 and e6["aligned"] == 90
    # Пропущенная глава — перечислена; в книге — без секций.
    skipped = {s["chapter_id"] for s in report["skipped"]}
    assert "ch3" in skipped and len(skipped) == 1
    answers = _answers_doc(run.job_dir / "learning.epub")
    assert "s5-coverage=0.900000" in answers
    assert "S5 Ten — 3" in answers, "пропущенная глава не перечислена в книге"
    assert "90.0%" in answers
    target = run.job_dir / "target.epub"
    learning = run.job_dir / "learning.epub"
    with zipfile.ZipFile(target) as tzip, zipfile.ZipFile(learning) as lzip:
        assert lzip.read("OEBPS/chapter3.xhtml") == tzip.read("OEBPS/chapter3.xhtml"), (
            "пропущенная глава должна быть без секций"
        )
        chapter1 = lzip.read("OEBPS/chapter1.xhtml").decode("utf-8")
        assert 'class="s5-section"' in chapter1, "остальные главы — с секциями"
    assert "ch3" not in report["sections"]
    # Предупреждения: частичный охват + пропущенная глава.
    assert any("0.9" in w or "90" in w for w in run.warnings), run.warnings
    assert any("S5" in w and "глав" in w for w in run.warnings), run.warnings


# ------------------------------------------------------------------ T-S5-13

_EPUBCHECK_JAR = ROOT / "data" / "tools" / "epubcheck-5.3.0" / "epubcheck.jar"


@pytest.mark.skipif(
    shutil.which("java") is None or not _EPUBCHECK_JAR.is_file(),
    reason="EPUBCheck (java + jar) недоступен в окружении",
)
def test_s5_13_epub2_xhtml11_learning_epubcheck(s5_env: S5Env) -> None:
    # T-S5-13 (D83/D84): источник EPUB 2.0 / XHTML 1.1 (паттерн гутенберга,
    # как v1_en.epub из V1R.5) — чист по EPUBCheck; движок ломает атрибуты
    # OPF (ремонт — D83 в atomic_finalize), S5 вставляет секции в XHTML 1.1
    # (D84: <div>, не <section>). Оба артефакта обязаны пройти EPUBCheck
    # с 0 ошибок — на HTML5-фикстурах (T-S5-1) оба дефекта не проявляются.
    book = s5_env.books["s5_epub2.epub"]
    src = run_epubcheck(_EPUBCHECK_JAR, book, 300)
    assert src.available is True, f"исходник: {src.unavailable_reason}"
    assert src.fatals == () and src.errors == (), f"исходник не чист: {src.errors}"

    run = s5_env.run("epub2_xhtml11", "s5_epub2.epub", learning_level="Balanced")
    report = _s5_report(run)
    # E5: target.epub — 0 ошибок (D83: ремонт OPF-атрибутов перед валидацией).
    e5 = run.quality["epubcheck"]
    assert e5["available"] is True, f"E5 EPUBCheck: {e5}"
    assert e5["errors"] == 0, f"target.epub: {e5}"
    # D83 явно: атрибуты метаданных — в OPF-нс, а не без нс/в XSI-нс.
    with zipfile.ZipFile(run.job_dir / "target.epub") as tzip:
        opf_root = etree.fromstring(tzip.read("OEBPS/content.opf"))
    opf_ns = "http://www.idpf.org/2007/opf"
    dc_ns = "http://purl.org/dc/elements/1.1/"
    identifier = opf_root.find(f"{{{opf_ns}}}metadata/{{{dc_ns}}}identifier")
    assert identifier is not None and identifier.get(f"{{{opf_ns}}}scheme") == "URI", (
        "D83: dc:identifier@scheme не в OPF-нс — ремонт не сработал"
    )
    # S5: learning.epub — 0 ошибок (D83 + D84).
    s5check = report["epub"]["epubcheck"]
    assert s5check["available"] is True, f"S5 EPUBCheck: {s5check}"
    assert s5check["errors"] == 0, f"learning.epub: {s5check}"
    # D84: секции — <div class="s5-section">, <section> отсутствует.
    learning = run.job_dir / "learning.epub"
    div_sections = 0
    with zipfile.ZipFile(learning) as archive:
        for name in archive.namelist():
            if not name.endswith(".xhtml") or study_book.ANSWERS_HREF in name:
                continue
            root = etree.fromstring(archive.read(name))
            bad = [
                el
                for el in root.iter(f"{{{study_book.XHTML}}}section")
                if el.get("class") == "s5-section"
            ]
            assert bad == [], f"{name}: <section class='s5-section'> (D84)"
            div_sections += sum(
                1
                for el in root.iter(f"{{{study_book.XHTML}}}div")
                if el.get("class") == "s5-section"
            )
    assert div_sections > 0, "секции в книге отсутствуют"
