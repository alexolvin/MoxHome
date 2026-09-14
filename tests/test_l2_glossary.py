"""Tесты L2: глоссарий книги (ТЗ L2.1–L2.8, CONTROL L2-1…L2-8).

Сквозной in-process прогон (Pipeline) — по образцу E5; mock-сервер (D3) —
детерминированный (rot3). Синтетические EPUB собираются на лету (детермин.
zip), содержимое фиксировано в этом файле. LLM привлекается ТОЛЬКО для
аннотации/стиля (задачи glossary_annotation/book_style); кандидаты —
детерминированные правила (L2.1).

- T-L2-1 — список кандидатов воспроизводим 100% (два запуска → идентично).
- T-L2-2 — de: правило «с заглавной» выключено в pack.yaml (0 кандидатов;
  контрфакт: при включении правило отбирает Mühle/Fluss).
- T-L2-3 — внедрённый несогласованный перевод (сценарий glossary_inconsistent):
  100% детекция, 0 правок текста (L2 детектирует, правит L3).
- T-L2-4 — согласованность терминов в выходе ≥ 95% (complex en→ru).
- T-L2-5 — усечение глоссария: отчёт + предупреждение, dropped зафиксированы.
- T-L2-6 — доля проверенных парадигм (ru) ≥ 90%, остальные — paradigm_unverified.
- T-L2-7 — golden-30: 0 ложных расхождений с парадигмой (без — 20);
  имя 20× → в глоссарии, один вариант на всех вхождениях.
- T-L2-8 — 5 исходных языков (en/de/fr/it/nl → ru): 5/5 завершены.
- T-L2-9 — файл изменён → прежняя версия superseded, новый глоссарий свежий.
- T-L2-10 — имя во всех 6 падежах ед. ч. → 0 расхождений, варианты = 6 форм.
- T-L2-11 — несклоняемый термин помечен явно (inflected=false, форм нет);
  12 идентичных форм без объяснения → paradigm_unverified.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from spacy.language import Language

from app.core.config import Settings, load_settings
from app.domain.glossary import (
    PARADIGM_NOT_APPLICABLE,
    PARADIGM_UNVERIFIED,
    PARADIGM_VERIFIED,
    Glossary,
    GlossaryTerm,
    Paradigm,
)
from app.domain.job import Job, JobStatus
from app.epub.aligner import AlignedPair, AlignmentReport, DocAlignment
from app.epub.reader import read_epub
from app.glossary.candidates import extract_candidates
from app.glossary.consistency import check_consistency
from app.glossary.store import BookStore, book_key_of
from app.languages.nlp import load_nlp
from app.languages.registry import PackSpec, load_registry
from app.services.jobs import confirm_job, create_job
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.workers.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "epub"
GOLDEN_PATH = ROOT / "config" / "languages" / "ru" / "inflection_golden.yaml"
EPUBCHECK_JAR = str(ROOT / "data" / "tools" / "epubcheck-5.3.0" / "epubcheck.jar")

_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from mock_llm_server import MockLLMServer  # noqa: E402

#: Фиксированная дата записей zip (воспроизводимость синтетических EPUB).
_FIXED_ZIP_DATE: tuple[int, int, int, int, int, int] = (2026, 1, 1, 0, 0, 0)

# ------------------------------------------------------------------ содержимое

# T-L2-3: сентинел несогласованного перевода (mock: сценарий
# glossary_inconsistent заменяет rot3(Wexley) на Xrongy в переводе).
WEXLEY_PARAS: tuple[str, ...] = (
    "The village of Wexley stood before dawn.",
    "Every morning Wexley woke to the sound of water.",
    "Far from the city, Wexley kept its silence.",
)

# T-L2-7: имя «Marlow» ровно 20× (каждое вхождение — посреди предложения,
# окружения уникальны: повторяющихся n-грамм нет, кандидатов кроме имени нет).
_WORDS20: tuple[str, ...] = (
    "dawn", "river", "stone", "bridge", "castle", "forest", "desert", "valley",
    "meadow", "prairie", "canyon", "jungle", "glacier", "tundra", "marsh", "moor",
    "fen", "bog", "lake", "sea",
)

# T-L2-6/T-L2-11: 9 склоняемых имён + сентинелы. Оба вхождения имени —
# посреди предложения (правило capitalized срабатывает независимо от NER).
_NAMES9: tuple[str, ...] = (
    "Albert", "Brenda", "Catherine", "Douglas", "Fiona", "Garrick", "Halden",
    "Ingrid", "Oliver",
)
_SENTINELS: tuple[str, ...] = ("Badparadigm", "Indeclinable")
_W1: tuple[str, ...] = (
    "dawn", "harbor", "meadow", "ridge", "tundra", "canyon", "gulf", "moor",
    "dune", "bog", "fen",
)
_W2: tuple[str, ...] = (
    "river", "forest", "valley", "lake", "glacier", "jungle", "port", "shore",
    "cape", "delta", "isle",
)

# T-L2-9: две версии одной книги (то же имя файла, разное содержимое).
VERSIONED_V1_PARAS: tuple[str, ...] = (
    "The town of Ostrava rose early.",
    "And Ostrava slept past noon.",
)
VERSIONED_V2_PARAS: tuple[str, ...] = (
    "The town of Brno rose early.",
    "And Brno slept past noon.",
)

# T-L2-2: немецкий текст с повторяющимися заглавными существительными
# (Mühle ×3, Fluss ×4): при включённом правиле — кандидаты, при выключенном
# (pack.yaml de) — 0.
DE_REPEAT_PARAS: tuple[str, ...] = (
    "Die Mühle am Fluss war alt, und die Mühle am Fluss war still.",
    "Der Fluss floss an der Mühle vorbei, zum Fluss zurück.",
)

_UIDS: dict[str, str] = {
    "wexley.epub": "urn:uuid:l2000000-0000-0000-0000-000000000001",
    "marlow.epub": "urn:uuid:l2000000-0000-0000-0000-000000000002",
    "parabook.epub": "urn:uuid:l2000000-0000-0000-0000-000000000003",
    "versioned.epub": "urn:uuid:l2000000-0000-0000-0000-000000000004",
    "de_repeat.epub": "urn:uuid:l2000000-0000-0000-0000-000000000005",
}
_TITLES: dict[str, str] = {
    "wexley.epub": "L2 Probe A",
    "marlow.epub": "L2 Probe B",
    "parabook.epub": "L2 Probe C",
    "versioned.epub": "L2 Probe D",
    "de_repeat.epub": "L2 Deutsche Wiederholung",
}
_PARAS: dict[str, tuple[str, ...]] = {
    "wexley.epub": WEXLEY_PARAS,
    "marlow.epub": tuple(f"The {w} around Marlow." for w in _WORDS20),
    "parabook.epub": tuple(
        p
        for name, w1, w2 in zip(_NAMES9 + _SENTINELS, _W1, _W2, strict=True)
        for p in (f"The {name} reached {w1}.", f"And {name} returned before {w2}.")
    ),
    "versioned.epub": VERSIONED_V1_PARAS,
    "de_repeat.epub": DE_REPEAT_PARAS,
}
_LANGS: dict[str, str] = {name: ("de" if name == "de_repeat.epub" else "en") for name in _UIDS}


def _write_epub(out_path: Path, files: dict[str, str | bytes]) -> None:
    with zipfile.ZipFile(out_path, "w") as archive:
        info = zipfile.ZipInfo("mimetype", date_time=_FIXED_ZIP_DATE)
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, "application/epub+zip")
        for name, content in files.items():
            info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)


def _synth_book(
    out_path: Path, title: str, lang: str, uid: str, paragraphs: tuple[str, ...]
) -> None:
    body = "\n".join(f'  <p id="p{i}">{p}</p>' for i, p in enumerate(paragraphs, start=1))
    chapter = (
        f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" lang="{lang}" xml:lang="{lang}">\n'
        "<head>\n  <title>{}</title>\n</head>\n<body>\n  <h1 id=\"h1\">{}</h1>\n"
        "{}</body>\n</html>\n"
    ).format(title, title, body)
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="l2-id">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'\n    <dc:identifier id="l2-id">{uid}</dc:identifier>'
        f'\n    <dc:title>{title}</dc:title>\n    <dc:creator>L2 Probe</dc:creator>'
        f'\n    <dc:language>{lang}</dc:language>\n    <dc:date>2026-01-01</dc:date>'
        '\n    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>\n'
        "  </metadata>\n  <manifest>"
        '\n    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>'
        '\n    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
        'properties="nav"/>\n  </manifest>\n  <spine>\n    <itemref idref="ch1"/>\n'
        "  </spine>\n</package>\n"
    )
    nav = (
        f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        f'lang="{lang}" xml:lang="{lang}">\n<head>\n  <title>Contents</title>\n</head>\n'
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


def _build_books(root: Path) -> dict[str, Path]:
    books: dict[str, Path] = {}
    for name, uid in _UIDS.items():
        path = root / name
        _synth_book(path, _TITLES[name], _LANGS[name], uid, _PARAS[name])
        books[name] = path
    return books


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _chapter_xhtml(job_dir: Path) -> str:
    with zipfile.ZipFile(job_dir / "target.epub") as archive:
        return archive.read("OEBPS/chapter1.xhtml").decode("utf-8")


# ------------------------------------------------------------------ окружение


@dataclass
class Run:
    """Сквозной in-process прогон (validating → … → QC) одной книги."""

    name: str
    job: Job
    job_dir: Path
    quality: dict[str, Any]
    warnings: list[str]
    tr_glossary: dict[str, Any]
    hash_after_translating: str | None = None
    hash_after_validating: str | None = None
    source_sha_before: str = ""
    source_sha_after: str = ""


@dataclass
class L2Env:
    server: MockLLMServer
    registry: dict[str, PackSpec]
    books: dict[str, Path]
    _settings: dict[str, Settings] = field(default_factory=dict)
    _repos: dict[str, JobRepository] = field(default_factory=dict)
    _dbs: dict[str, Database] = field(default_factory=dict)
    _nlp: dict[str, Language] = field(default_factory=dict)
    runs: dict[str, Run] = field(default_factory=dict)
    _v2_written: bool = False

    def nlp_of(self, code: str) -> Language:
        if code not in self._nlp:
            self._nlp[code] = load_nlp(self.registry[code])
        return self._nlp[code]

    def add_profile(self, name: str, settings: Settings, db: Database) -> None:
        self._settings[name] = settings
        self._dbs[name] = db
        self._repos[name] = JobRepository(db)

    def run(
        self,
        name: str,
        book: str,
        target: str,
        *,
        profile: str = "main",
        scenario: str = "ok",
        lang: str = "en",
        capture_target_hashes: bool = False,
    ) -> Run:
        """Ленивый кэшируемый прогон (паттерн E5). Короткие синтетические книги
        могут требовать подтверждения языка (L0.5) — подтверждаем известным
        исходным языком (пользовательский путь, как в воркере)."""
        if name in self.runs:
            return self.runs[name]
        settings = self._settings[profile]
        repo = self._repos[profile]
        source = self.books[book] if book in self.books else FIXTURES / book
        source_sha_before = _sha256(source.read_bytes())
        self.server.set_scenario(scenario)
        try:
            job = create_job(repo, settings, source_path=source, target_language=target)
            pipeline = Pipeline(settings)
            job_now = repo.require(job.id)
            needs_confirm = pipeline.run_validating(repo, job_now)
            if needs_confirm:
                # Точный путь воркера (app/workers/worker.py): VALIDATING →
                # NEEDS_CONFIRMATION → подтверждение → PLANNING.
                repo.transition(job.id, JobStatus.VALIDATING, reason="начало этапа")
                repo.transition(job.id, JobStatus.NEEDS_CONFIRMATION, reason="L0.5 (T-L2)")
                confirm_job(repo, job.id, source_language=lang)
            # Свежее состояние: run_validating ставит source_language (L0.5),
            # confirm_job — перевод в PLANNING; объект job устарел.
            job_now = repo.require(job.id)
            pipeline.run_planning(repo, job_now)
            pipeline.run_translating(repo, job_now, lambda: None)
            target_file = settings.job_dir(job.id) / "target.epub"
            hash_after_translating = (
                _sha256(target_file.read_bytes()) if capture_target_hashes else None
            )
            warnings = pipeline.run_validating_output(repo, job_now)
            hash_after_validating = (
                _sha256(target_file.read_bytes()) if capture_target_hashes else None
            )
            job_dir = settings.job_dir(job.id)
            quality: dict[str, Any] = json.loads(
                (job_dir / "quality.json").read_text(encoding="utf-8")
            )
            tr: dict[str, Any] = json.loads(
                (job_dir / "translation_result.json").read_text(encoding="utf-8")
            )
            run = Run(
                name=name,
                job=job,
                job_dir=job_dir,
                quality=quality,
                warnings=warnings,
                tr_glossary=dict(tr.get("glossary", {})),
                hash_after_translating=hash_after_translating,
                hash_after_validating=hash_after_validating,
                source_sha_before=source_sha_before,
                source_sha_after=_sha256(source.read_bytes()),
            )
        finally:
            self.server.set_scenario("ok")
        self.runs[name] = run
        return run

    def overwrite_versioned_v2(self) -> None:
        """T-L2-9: та же книга (имя файла), новое содержимое — новая версия."""
        if self._v2_written:
            return
        path = self.books["versioned.epub"]
        _synth_book(path, "L2 Probe D v2", "en", _UIDS["versioned.epub"], VERSIONED_V2_PARAS)
        self._v2_written = True

    def close(self) -> None:
        for db in self._dbs.values():
            db.close()


def _make_storage(
    base: Path, name: str, server: MockLLMServer, extra: str = ""
) -> tuple[Settings, Database]:
    """Изолированный storage.root: конфиг (глоссарий включён, D60) + БД +
    промпты (D62: llm.prompts_root → временный каталог с репозиторийными
    промптами; storage-раскладка промптами не засоряется)."""
    work = base / name
    work.mkdir(parents=True, exist_ok=True)
    prompts_dir = base / "prompts"
    if not prompts_dir.exists():
        shutil.copytree(ROOT / "prompts", prompts_dir)
    (work / "test.env").write_text(
        "SERVER_BIND_ADDRESS=127.0.0.1\nLLM_API_KEY=test-key-value-000\n", encoding="utf-8"
    )
    profiles = "".join(
        f"    {n}:\n      base_url: {server.url}\n      model: test-model\n"
        for n in ("translation", "fill", "analysis")
    )
    (work / "config.yaml").write_text(
        f"storage:\n  root: {work}\n"
        f"epubcheck:\n  jar_path: {EPUBCHECK_JAR}\n"
        f"languages:\n  root: {ROOT / 'config' / 'languages'}\n"
        f"  matrix: {ROOT / 'config' / 'language_matrix.yaml'}\n"
        f"llm:\n  prompts_root: {prompts_dir}\n  profiles:\n{profiles}"
        f"glossary:\n  enabled: true\n{extra}",
        encoding="utf-8",
    )
    settings = load_settings(work / "config.yaml", work / "test.env")
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    return settings, db


@pytest.fixture(scope="module")
def l2_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[L2Env]:
    base = tmp_path_factory.mktemp("l2_module")
    books_dir = base / "books"
    books_dir.mkdir()
    server = MockLLMServer()
    server.start()
    env = L2Env(server=server, registry=load_registry(ROOT / "config" / "languages"),
                books=_build_books(books_dir))
    main_settings, main_db = _make_storage(base, "storage_main", server)
    env.add_profile("main", main_settings, main_db)
    small_settings, small_db = _make_storage(
        base, "storage_small", server, extra="limits:\n  glossary_max_user_prompt_chars: 200\n"
    )
    env.add_profile("small", small_settings, small_db)
    try:
        yield env
    finally:
        env.close()
        server.stop()


# ------------------------------------------------------------------ golden (L2-7)

_GOLDEN_CASES_SG: tuple[str, ...] = (
    "nom_sg", "gen_sg", "dat_sg", "acc_sg", "instr_sg", "prep_sg",
)
#: Совпадающие пары форм (им. = вин. у неодушевлённых) — объяснены (L2.4).
_GOLDEN_COINCIDENCES: dict[str, str] = {
    "nom_sg": "им. п. = вин. п. (неодушевлённые)",
    "acc_sg": "вин. п. = им. п. (неодушевлённые)",
    "nom_pl": "им. п. мн. = вин. п. мн. (неодушевлённые)",
    "acc_pl": "вин. п. мн. = им. п. мн. (неодушевлённые)",
}


def _golden_entries() -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8"))
    return data


def _golden_glossary(
    entries: list[dict[str, Any]], names: tuple[str, ...], *, with_paradigm: bool
) -> Glossary:
    """Глоссарий из golden-30: с проверенной парадигмой либо без неё
    (контроль: только каноническая форма)."""
    terms: list[GlossaryTerm] = []
    for entry in entries:
        forms = tuple(str(entry["forms"][name]) for name in names)
        if with_paradigm:
            paradigm = Paradigm(
                inflected=True,
                form_names=tuple(names),
                forms=forms,
                coincidences=tuple(sorted(_GOLDEN_COINCIDENCES.items())),
            )
            status, reason = PARADIGM_VERIFIED, None
        else:
            paradigm, status, reason = None, PARADIGM_NOT_APPLICABLE, None
        terms.append(
            GlossaryTerm(
                term=str(entry["source_term"]),
                source_forms=(str(entry["source_term"]),),
                translation=str(entry["forms"]["nom_sg"]),
                pos="proper_noun",
                first_segment_id="golden",
                occurrences=len(_GOLDEN_CASES_SG),
                paradigm=paradigm,
                paradigm_status=status,
                paradigm_reason=reason,
            )
        )
    return Glossary(source_language="en", target_language="ru", terms=tuple(terms))


def _golden_alignment(entries: list[dict[str, Any]]) -> AlignmentReport:
    """30 выровненных пар: исходный сегмент содержит имя, перевод — golden-
    предложение на падеж (T-L2-7/T-L2-10: уровень чекера, без реального EPUB)."""
    pairs: list[AlignedPair] = []
    for entry in entries:
        name = str(entry["source_term"])
        for case in _GOLDEN_CASES_SG:
            pairs.append(
                AlignedPair(
                    segment_id=f"{name}-{case}",
                    doc="golden.xhtml",
                    source_xpath="/body/p",
                    source_text=f"We visited {name} yesterday.",
                    translation_xpath="/body/p__translated",
                    translation_text=str(entry["sentences"][case]),
                    how="sibling",
                )
            )
    return AlignmentReport(
        submit_mode="APPEND_BLOCK",
        min_coverage=1.0,
        total=len(pairs),
        aligned_count=len(pairs),
        unaligned_count=0,
        extra_count=0,
        coverage=1.0,
        identity_mismatches=0,
        docs=(DocAlignment(doc="golden.xhtml", aligned=tuple(pairs), unaligned=(),
                           extra_blocks=()),),
    )


# ------------------------------------------------------------------ тесты


def test_l2_1_candidates_reproducible(l2_env: L2Env, tmp_path: Path) -> None:
    """L2-1: два запуска отбора по той же книге → идентичный список (100%)."""
    settings = l2_env._settings["main"]
    book = read_epub(FIXTURES / "complex.epub", tmp_path / "unpack", settings)
    pack = l2_env.registry["en"]
    nlp = l2_env.nlp_of("en")
    first = extract_candidates(book, pack, nlp, settings)
    second = extract_candidates(book, pack, nlp, settings)
    assert len(first) > 0, "кандидатов нет — отбор не работает"
    assert first == second, "список кандидатов не воспроизводится (L2-1: 100%)"


def test_l2_2_de_capitalized_rule_off(l2_env: L2Env, tmp_path: Path) -> None:
    """L2-2: для de правило «с заглавной» выключено в pack.yaml (данные, Rule 1).

    Реальная фикстура lang_de: 0 кандидатов по правилу capitalized.
    Синтетический de-текст с повторяющимися заглавными: при pack де-факто — 0,
    контрфакт (rule on) — Mühle/Fluss отбираются (правило именно выключено,
    а не «не было что отбирать»)."""
    settings = l2_env._settings["main"]
    de = l2_env.registry["de"]
    nlp = l2_env.nlp_of("de")
    assert de.capitalized_candidates is False, "pack.yaml de: capitalized_candidates off"

    book_fixture = read_epub(FIXTURES / "lang_de.epub", tmp_path / "unpack_fix", settings)
    fixture_cands = extract_candidates(book_fixture, de, nlp, settings)
    assert sum(1 for c in fixture_cands if c.rule == "capitalized") == 0

    book_repeat = read_epub(l2_env.books["de_repeat.epub"], tmp_path / "unpack_rep", settings)
    repeat_cands = extract_candidates(book_repeat, de, nlp, settings)
    assert sum(1 for c in repeat_cands if c.rule == "capitalized") == 0

    counterfactual = de.model_copy(update={"capitalized_candidates": True})
    cf_cands = extract_candidates(book_repeat, counterfactual, nlp, settings)
    surfaces = {c.surface for c in cf_cands if c.rule == "capitalized"}
    assert {"Mühle", "Fluss"} <= surfaces, (
        f"контрфакт: правило capitalized должно отобрать Mühle/Fluss, got {sorted(surfaces)}"
    )


def test_l2_3_injected_inconsistency(l2_env: L2Env) -> None:
    """L2-3: внедрённый несогласованный перевод → 100% детекция, 0 правок на L2.

    Сценарий mock glossary_inconsistent: Wexley в переводе = «Xrongy» (в
    выходе нет НИ ОДНОЙ формы парадигмы термина; остальные термины — обычный
    rot3, согласованный с глоссарием). L2 фиксирует расхождения и НЕ правит:
    выходной EPUB после стадии L2 побайтово тот же, что после translating."""
    run = l2_env.run(
        "wexley", "wexley.epub", "ru", scenario="glossary_inconsistent",
        capture_target_hashes=True,
    )
    section = run.quality["glossary_consistency"]
    assert section["occurrences_checked"] == 3
    assert section["consistent_occurrences"] == 0
    assert section["mismatches"] == 3, "все внедрённые вхождения должны быть детектированы"
    assert section["consistent_share"] == 0.0

    report = json.loads(
        (run.job_dir / "glossary_consistency.json").read_text(encoding="utf-8")
    )
    mismatches = report["mismatches"]
    assert len(mismatches) == 3
    assert all(m["term"] == "Wexley" for m in mismatches)
    assert len({m["segment_id"] for m in mismatches}) == 3
    assert all(m["translation_text"] for m in mismatches), "расхождения — с текстом пары"

    # Порог согласованности: предупреждение в quality + статус (L2-4: отклонения — в перечне).
    assert any(w.startswith("L2:") for w in run.warnings)

    # L2 НЕ ПРАВИТ ТЕКСТ (ТЗ L2.6): «неправильная» форма осталась в выходе.
    xhtml = _chapter_xhtml(run.job_dir)
    assert xhtml.count("Xrongy") == 3
    assert "Zhaohb" not in xhtml, "канонической формы в (намеренно) несогласованном переводе нет"

    # Файлы не тронуты: источник до/после идентичен; выход — идентичен до/после
    # стадии валидации (L2-шаг читает, а не пишет, target.epub).
    assert run.source_sha_before == run.source_sha_after
    assert run.hash_after_translating is not None
    assert run.hash_after_translating == run.hash_after_validating


def test_l2_4_consistent_share(l2_env: L2Env) -> None:
    """L2-4: согласованность терминов глоссария в выходе ≥ 95% (complex en→ru)."""
    run = l2_env.run("complex", "complex.epub", "ru")
    section = run.quality["glossary_consistency"]
    assert section["terms_checked"] > 0, "глоссарий пуст — нечего проверять"
    assert section["occurrences_checked"] >= section["terms_checked"]
    assert section["consistent_share"] >= 0.95, (
        f"доля согласованных вхождений {section['consistent_share']:.1%} < 95%: "
        + "; ".join(run.warnings)
    )
    assert not any(w.startswith("L2:") for w in run.warnings)

    # Глоссарий версии книги сохранён в books/ (L2.8: привязка к хэшу).
    store = BookStore(l2_env._settings["main"].books_dir)
    glossary = store.load_glossary(book_key_of(run.job.source_path), run.job.input_sha256)
    assert glossary is not None
    assert len(glossary.terms) > 0
    assert glossary.source_hash == run.job.input_sha256
    assert store.is_current(book_key_of(run.job.source_path), run.job.input_sha256)


def test_l2_5_truncation_recorded(l2_env: L2Env) -> None:
    """L2-5: усечение глоссария зафиксировано в отчёте (dropped) + предупреждение."""
    run = l2_env.run("complex_small", "complex.epub", "ru", profile="small")
    glossary = run.tr_glossary
    assert glossary["total_terms"] > 0
    assert glossary["kept_terms"] + glossary["dropped_terms"] == glossary["total_terms"]
    assert glossary["dropped_terms"] > 0, "при лимите 200 термины должны отбрасываться"
    assert glossary["truncated"] is True
    assert glossary["prompt_chars"] <= glossary["limit_chars"] == 200
    assert any("user_prompt усечён" in w for w in run.warnings)


def _book_glossary(l2_env: L2Env, run: Run) -> Glossary:
    store = BookStore(l2_env._settings["main"].books_dir)
    glossary = store.load_glossary(book_key_of(run.job.source_path), run.job.input_sha256)
    assert glossary is not None, f"глоссарий книги {run.job.source_path} не собран"
    return glossary


def test_l2_6_verified_paradigm_share(l2_env: L2Env) -> None:
    """L2-6: доля проверенных парадигм (ru) ≥ 90%; остальные — unverified.

    Контр-кандидат «Badparadigm» (mock: 12 идентичных форм БЕЗ объяснений) —
    paradigm_unverified с причиной; 9 склоняемых имён — verified."""
    run = l2_env.run("parabook", "parabook.epub", "ru")
    glossary = _book_glossary(l2_env, run)
    for name in _NAMES9:
        term = glossary.term(name)
        assert term is not None, f"имя {name!r} не попало в глоссарий"
        assert term.paradigm_status == PARADIGM_VERIFIED, name
        assert term.paradigm is not None and len(term.paradigm.forms) == 12, name

    bad = glossary.term("Badparadigm")
    assert bad is not None
    assert bad.paradigm_status == PARADIGM_UNVERIFIED
    assert bad.paradigm is None, "отклонённая парадигма не хранится как форма"
    assert bad.paradigm_reason and "объясн" in bad.paradigm_reason

    applicable = [
        t for t in glossary.terms
        if t.paradigm_status in (PARADIGM_VERIFIED, PARADIGM_UNVERIFIED)
    ]
    verified = [t for t in applicable if t.paradigm_status == PARADIGM_VERIFIED]
    share = len(verified) / len(applicable)
    assert share >= 0.9, f"доля проверенных парадигм {share:.1%} < 90% (L2-6)"


def test_l2_7_golden30_and_repeated_name(l2_env: L2Env) -> None:
    """L2-7: ложные расхождения от словоизменения = 0 (golden-30, парадигма);
    без парадигмы те же 30 случаев дают ложные расхождения (20). Имя 20× —
    в глоссарии и переведено идентично во всех вхождениях."""
    entries = _golden_entries()
    names = l2_env.registry["ru"].paradigm_forms["proper_noun"]
    assert len(names) == 12, "ru pack.yaml: 12 форм (6 падежей × ед./мн.)"

    alignment = _golden_alignment(entries)
    assert alignment.total == 30

    with_paradigm = _golden_glossary(entries, names, with_paradigm=True)
    report = check_consistency(with_paradigm, alignment)
    assert report.occurrences_checked == 30
    assert len(report.mismatches) == 0, (
        "L2-7: ложных расхождений от падежных форм одного имени должно быть 0: "
        + "; ".join(f"{m.term} ({m.segment_id})" for m in report.mismatches)
    )

    control = _golden_glossary(entries, names, with_paradigm=False)
    control_report = check_consistency(control, alignment)
    # Без парадигмы ожидаема только каноническая форма: им. и вин. совпадают
    # (неодуш.) → 2×5 согласовано, 4×5 = 20 ложных расхождений.
    assert control_report.occurrences_checked == 30
    assert len(control_report.mismatches) == 20, (
        "контроль без парадигмы: падежные формы дают ложные расхождения "
        "(ценность парадигмы, L2.4/L2-7)"
    )

    # Имя 20×: в глоссарии, одно имя (один вариант) на всех вхождениях.
    run = l2_env.run("marlow", "marlow.epub", "ru")
    report_dict = json.loads(
        (run.job_dir / "glossary_consistency.json").read_text(encoding="utf-8")
    )
    term = next(t for t in report_dict["terms"] if t["term"] == "Marlow")
    assert term["occurrences"] == 20
    assert term["consistent"] == 20
    assert len(term["variants"]) == 1, f"один вариант на всех вхождениях, got {term['variants']}"
    assert report_dict["consistent_share"] == 1.0

    glossary = _book_glossary(l2_env, run)
    marlow = glossary.term("Marlow")
    assert marlow is not None and marlow.occurrences == 20


def test_l2_8_five_languages(l2_env: L2Env) -> None:
    """L2-8: 5 исходных языков (en/de/fr/it/nl → ru): 5/5 завершены,
    глоссарий версии собран (пустой допустим — в маленьких фикстурах
    кандидатов нет), секция согласованности в quality.json."""
    books = (
        ("complex", "complex.epub", "en"),
        ("lang_de", "lang_de.epub", "de"),
        ("lang_fr", "lang_fr.epub", "fr"),
        ("lang_it", "lang_it.epub", "it"),
        ("lang_nl", "lang_nl.epub", "nl"),
    )
    done = 0
    for name, file_name, lang in books:
        run = l2_env.run(name, file_name, "ru", lang=lang)
        assert (run.job_dir / "target.epub").is_file(), f"нет результата: {name}"
        assert "glossary_consistency" in run.quality, f"нет секции L2: {name}"
        assert "glossary" in run.quality, f"нет QC-секции глоссария: {name}"
        store = BookStore(l2_env._settings["main"].books_dir)
        assert store.load_glossary(file_name, run.job.input_sha256) is not None, (
            f"глоссарий версии {name} не сохранён"
        )
        done += 1
    assert done == 5, "L2-8: 5/5 исходных языков"


def test_l2_9_version_change(l2_env: L2Env) -> None:
    """L2-8(ТЗ)/T-L2-9: файл изменён (тот же name, новый хэш) → прежняя
    версия superseded, не применяется молча; новой версии — свежий глоссарий."""
    run1 = l2_env.run("versioned_v1", "versioned.epub", "ru")
    hash_a = run1.job.input_sha256
    l2_env.overwrite_versioned_v2()
    run2 = l2_env.run("versioned_v2", "versioned.epub", "ru")
    hash_b = run2.job.input_sha256
    assert hash_a != hash_b, "содержимое файла не изменилось — версия не сменилась"

    store = BookStore(l2_env._settings["main"].books_dir)
    key = "versioned.epub"
    versions = {v.hash: v.status for v in store.versions(key)}
    assert versions.get(hash_a) == "superseded", "прежняя версия должна быть помечена"
    assert versions.get(hash_b) == "current"
    assert store.is_current(key, hash_a) is False
    assert store.is_current(key, hash_b) is True

    glossary_v1 = store.load_glossary(key, hash_a)
    glossary_v2 = store.load_glossary(key, hash_b)
    assert glossary_v1 is not None and glossary_v1.term("Ostrava") is not None
    assert glossary_v2 is not None and glossary_v2.term("Brno") is not None
    assert glossary_v2.term("Ostrava") is None, (
        "глоссарий прежней версии не применяется к новой (T-L2-9)"
    )
    # Секция согласованности v2 — по новому глоссарию.
    assert run2.quality["glossary_consistency"]["terms_checked"] >= 1


def test_l2_10_six_cases_no_mismatch(l2_env: L2Env) -> None:
    """T-L2-10: имя во всех 6 падежах ед. ч. в переводе → все вхождения —
    одно имя (парадигма), 0 расхождений; варианты = все 6 форм."""
    entries = _golden_entries()
    names = l2_env.registry["ru"].paradigm_forms["proper_noun"]
    glossary = _golden_glossary(entries, names, with_paradigm=True)
    report = check_consistency(glossary, _golden_alignment(entries))
    assert len(report.mismatches) == 0
    for entry in entries:
        name = str(entry["source_term"])
        term = next(t for t in report.terms if t.term == name)
        assert term.occurrences == 6
        assert term.consistent == 6, name
        # Варианты хранятся в нормализации чекера (NFC + casefold).
        expected_values = {str(entry["forms"][case]).casefold() for case in _GOLDEN_CASES_SG}
        assert set(term.variants) == expected_values, (
            f"{name}: варианты не покрывают все 6 падежных форм: {term.variants}"
        )


def test_l2_11_indeclinable_marked(l2_env: L2Env) -> None:
    """T-L2-11: несклоняемый термин помечен ЯВНО (inflected=false, форм нет —
    не 6/12 идентичных форм); 12 идентичных форм без объяснений — unverified."""
    run = l2_env.run("parabook", "parabook.epub", "ru")
    glossary = _book_glossary(l2_env, run)

    indeclinable = glossary.term("Indeclinable")
    assert indeclinable is not None
    assert indeclinable.paradigm_status == PARADIGM_NOT_APPLICABLE
    assert indeclinable.paradigm is not None
    assert indeclinable.paradigm.inflected is False, "явная метка несклоняемости"
    assert indeclinable.paradigm.forms == (), "у несклоняемого термина форм нет"

    # Ни один термин не хранит парадигму из 6+ идентичных форм.
    for term in glossary.terms:
        if term.paradigm is not None and term.paradigm.inflected:
            assert len(set(term.paradigm.forms)) > 1, (
                f"{term.term}: парадигма из идентичных форм запрещена (T-L2-11)"
            )

    bad = glossary.term("Badparadigm")
    assert bad is not None
    assert bad.paradigm_status == PARADIGM_UNVERIFIED, (
        "12 идентичных форм без объяснений — парадигма отклоняется (L2.4)"
    )
