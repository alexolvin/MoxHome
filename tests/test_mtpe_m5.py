# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Тесты M5 (CONTROL): тракт M — писатель сегментов (MTPE TZ §4.9, §7).

- M5-1 — EPUBCheck на пяти книгах тестового корпуса (mock): 0 ошибок;
  предупреждений не больше, чем у выхода тракта T той же книги.
- M5-2 — Скелет выхода M5 против выхода тракта T той же книги и того же
  SubmitKind: 0 различий (деревья XHTML без текстовых узлов, OPF, навигация).
- M5-3 — Множество переведённых блоков M5 = множеству тракта T (сверка XPath).
- M5-4 — Карта E6 = write_map.json (100 % сегментов).
- M5-5 — Текст модели с `<script>`, `&`, `<b>`: экранирован; новых элементов
  вне карты — 0 (T-M5-5).
- M5-6 — Изменения кода E6, L2, L3, S1–S5 в PR серии M: 0 строк (git diff).
- M5-7 — kill -9 во время записи: частичного выходного файла нет (атомарность E5).

Писатель (D131) дублирует сборку движка: текст модели — только lxml-текстовые
узлы, элементы — только из markup_map, вставка по SubmitKind, id-дедупликация
`__translated`, OPF/навигация — побайто, атомарная запись = вызовы E5
(`rebuild_epub` + `atomic_finalize`).
"""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from lxml import etree

from app.adapters.epub_engine.engine import EpubTranslatorEngine
from app.adapters.epub_engine.epubcheck import run_epubcheck
from app.adapters.epub_engine.models import SubmitMode, TranslationRequest
from app.core.config import Settings, load_settings
from app.epub import segmenter
from app.epub.aligner import build_alignment_report
from app.epub.reader import read_epub
from app.mtpe.draft import DraftPhase
from app.mtpe.markup import encode
from app.mtpe.postedit import PostEditPhase
from app.mtpe.writer import WriterPhase
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.workers.qc import _diff_trees

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "epub"
#: Пятикнижный тестовый корпус серии (тот же, что в M3).
_BOOKS = [
    "complex.epub",
    "lang_de.epub",
    "lang_fr.epub",
    "lang_it.epub",
    "lang_nl.epub",
]
_COMPLEX = FIXTURES / "complex.epub"
JAR = str(ROOT / "data" / "tools" / "epubcheck-5.3.0" / "epubcheck.jar")
_LANG_ROOT = str(ROOT / "config" / "languages")
_MATRIX = str(ROOT / "config" / "language_matrix.yaml")
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from mock_llm_server import MockLLMServer  # noqa: E402

#: Пара: источник en, цель ru (есть spaCy en_core_web_sm + mt_name Russian).
_SRC, _TGT = "en", "ru"


@pytest.fixture()
def mock_server() -> Iterator[MockLLMServer]:
    server = MockLLMServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


# ------------------------------------------------------------------ служебное


def _m5_settings(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    server: MockLLMServer,
) -> tuple[Settings, Database, JobRepository]:
    """Настройки + БД + репозиторий: 5 профилей (mt/postedit → mock), реестр,
    промпты, таксономии."""
    root = tmp_path / "storage"
    root.mkdir(parents=True, exist_ok=True)
    prompts = root / "prompts"
    if not prompts.exists():
        shutil.copytree(ROOT / "prompts", prompts)
    tax_dir = root / "config" / "taxonomies"
    if not tax_dir.exists():
        shutil.copytree(ROOT / "config" / "taxonomies", tax_dir)

    base = server.url
    mt = {
        "base_url": base,
        "model": "mt-model",
        "api": "completions",
        "prompt_task": "mt_draft",
        "temperature": 0,
        "top_k": 1,
        "max_tokens_ratio": 3.0,
        "max_segment_tokens": 400,
        "retries": 3,
        "timeout_s": 120,
        "warmup_timeout_s": 900,
    }

    def prof(name: str, fields: Mapping[str, object]) -> str:
        lines = [f"    {name}:"]
        for key, value in fields.items():
            lines.append(f"      {key}: {json.dumps(value, ensure_ascii=False)}")
        return "\n".join(lines)

    profiles = "\n".join(
        [
            prof("translation", {"base_url": base, "model": "t-model"}),
            prof("fill", {"base_url": base, "model": "f-model"}),
            prof("analysis", {"base_url": base, "model": "a-model"}),
            prof("mt", mt),
            prof(
                "postedit",
                {"base_url": base, "model": "pe-model", "api": "chat",
                 "prompt_task": "postedit", "temperature": 0, "retries": 3,
                 "timeout_s": 300, "warmup_timeout_s": 900},
            ),
        ]
    )
    cfg = (
        f"storage:\n  root: {json.dumps(str(root), ensure_ascii=False)}\n"
        "translation:\n  pipeline: mt_pe\n"
        f"epubcheck:\n  jar_path: {json.dumps(JAR, ensure_ascii=False)}\n"
        f"languages:\n  root: {json.dumps(_LANG_ROOT, ensure_ascii=False)}\n"
        f"  matrix: {json.dumps(_MATRIX, ensure_ascii=False)}\n"
        f"llm:\n  prompts_root: prompts\n  profiles:\n{profiles}\n"
    )
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg, encoding="utf-8")
    settings = load_settings(cfg_path, make_env(None))
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    return settings, db, JobRepository(db)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _spine_docs(book: Any) -> list[str]:
    """document_path всех spine-глав (порядок spine)."""
    return [chapter.document_path for chapter in book.chapters]


def _identity_m_dir(settings: Settings, epub_path: Path, tmp: Path) -> Path:
    """Синтезирует m/segments.jsonl + m/final.jsonl с идентичным переводом
    (final.text = src_encoded, markup_map — реальный кодек): писатель получает
    валидный ввод без LLM. Используется для изолированных проверок писателя."""
    m_dir = tmp / "m"
    m_dir.mkdir(parents=True, exist_ok=True)
    book = read_epub(epub_path, tmp / "work", settings)
    seg_rows: list[dict[str, Any]] = []
    fin_rows: list[dict[str, Any]] = []
    for chapter in book.chapters:
        for segment in chapter.segments:
            enc = encode(segment.marked_text)
            seg_rows.append(
                {
                    "segment_id": segment.segment_id,
                    "src_encoded": enc.src_encoded,
                    "markup_map": [list(pair) for pair in enc.markup_map],
                }
            )
            fin_rows.append({"segment_id": segment.segment_id, "text": enc.src_encoded})
    (m_dir / "segments.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in seg_rows),
        encoding="utf-8",
    )
    (m_dir / "final.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in fin_rows),
        encoding="utf-8",
    )
    return m_dir


def _run_full_tract(
    settings: Settings,
    repo: JobRepository,
    job_id: str,
    epub_path: Path,
    server: MockLLMServer,
    *,
    scenario: str = "ok",
) -> Path:
    """Полный тракт M под mock: черновик (M3) → постредактура (M4) на реальном
    EPUB. Возвращает m_dir с segments/drafts/final.jsonl (реальные segment_id)."""
    server.set_scenario(scenario)
    DraftPhase(settings, repo, job_id).run(
        str(epub_path), _SRC, _TGT, work_dir=str(settings.job_dir(job_id) / "work")
    )
    PostEditPhase(settings, repo, job_id).run(_SRC, _TGT)
    return settings.job_dir(job_id) / "m"


def _epubcheck(settings: Settings, path: Path) -> Any:
    return run_epubcheck(settings.epubcheck_jar_path, path, settings.epubcheck.timeout_seconds)


def _read_doc_bytes(epub_path: Path, doc: str) -> bytes:
    with zipfile.ZipFile(epub_path) as archive:
        return archive.read(doc)


def _run_tract_t(settings: Settings, epub_path: Path, out: Path, tmp: Path) -> Path:
    """Выход тракта T (движок `epub-translator` под mock) для эталонного
    сравнения M5-2/M5-3. Тот же SubmitKind (APPEND_BLOCK), что у писателя M5."""
    job_dir = tmp / "tract-t"
    job_dir.mkdir(parents=True, exist_ok=True)
    engine = EpubTranslatorEngine(settings, job_dir)
    request = TranslationRequest(
        source_path=epub_path,
        target_path=out,
        target_language=_TGT,
        mode=SubmitMode.APPEND_BLOCK,
    )
    return engine.translate_book(request).target_path


def _strip_lang(root: Any) -> None:
    """Убрать атрибуты языка (lang + xml:lang) со всего дерева: движок
    «схлопывает»/удаляет корневой lang при ресериализации (измерено, E1 §3) —
    это сериализационный артефакт, не структура перевода; скелет сверяем без них."""
    for element in [root, *root.iter()]:
        if not isinstance(element.tag, str):
            continue
        element.attrib.pop("lang", None)
        element.attrib.pop("{http://www.w3.org/XML/1998/namespace}lang", None)


def _opf_nav_docs(epub_path: Path) -> list[str]:
    """Пути OPF и nav-документа (для сверки скелета M5-2)."""
    docs: list[str] = []
    with zipfile.ZipFile(epub_path) as archive:
        names = set(archive.namelist())
        if "META-INF/container.xml" in names:
            container = etree.fromstring(archive.read("META-INF/container.xml"))
            for rootfile in container.iter():
                if segmenter.localname(rootfile) == "rootfile" and "full-path" in rootfile.attrib:
                    opf = rootfile.attrib["full-path"]
                    if opf in names:
                        docs.append(opf)
        for doc in docs:
            opf_root = etree.fromstring(archive.read(doc))
            for item in opf_root.iter():
                if (
                    segmenter.localname(item) == "item"
                    and item.get("properties") == "nav"
                    and item.get("href") in names
                ):
                    nav = item.get("href")
                    if not nav.startswith("/"):
                        base = doc.rsplit("/", 1)[0] + "/" if "/" in doc else ""
                        nav = base + nav
                    if nav in names:
                        docs.append(nav)
    return docs


def _structural_docs(book: Any, epub_path: Path) -> list[str]:
    """Spine-документы + OPF + nav: все структурные документы для скелета."""
    docs = [chapter.document_path for chapter in book.chapters]
    docs.extend(_opf_nav_docs(epub_path))
    return docs


# ------------------------------------------------------------------ M5-1


@pytest.mark.parametrize("name", _BOOKS)
def test_m5_1_epubcheck_zero_errors(
    name: str,
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M5-1: полный тракт M (mock) → писатель → EPUBCheck: 0 ошибок (и 0 fatal).
    settings, db, repo = _m5_settings(tmp_path, make_env, mock_server)
    epub_path = FIXTURES / name
    out = tmp_path / f"out-{name}"
    try:
        m_dir = _run_full_tract(settings, repo, f"j-m5-1-{name}", epub_path,
                                mock_server)
        WriterPhase(settings).run(epub_path, out, m_dir, "APPEND_BLOCK")
        assert out.is_file(), "выходной EPUB не создан"
        outcome = _epubcheck(settings, out)
        assert outcome.available, "EPUBCheck недоступен (нет java/jar)"
        assert not outcome.fatals, f"fatal: {outcome.fatals}"
        assert not outcome.errors, f"ошибки EPUBCheck: {outcome.errors}"
    finally:
        mock_server.set_scenario("ok")
        db.close()


# ------------------------------------------------------------------ M5-5


def test_m5_5_model_text_escaped_no_new_elements(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M5-5 (T-M5-5): литеральные `<script>`/`&`/`<b>` в тексте модели экранируются;
    # новых элементов вне карты разметки — 0 (разбор ответа как XHTML не делается).
    settings, db, _ = _m5_settings(tmp_path, make_env, mock_server)
    try:
        book = read_epub(_COMPLEX, tmp_path / "work", settings)
        m_dir = tmp_path / "m"
        m_dir.mkdir(parents=True, exist_ok=True)
        hostile = "A <script>alert(1)</script> & <b>bold</b> <i>x</i> tail"
        seg_rows: list[dict[str, Any]] = []
        fin_rows: list[dict[str, Any]] = []
        for chapter in book.chapters:
            for segment in chapter.segments:
                seg_rows.append(
                    {"segment_id": segment.segment_id, "src_encoded": "",
                     "markup_map": []}  # карта пуста — литералы не маркеры
                )
                fin_rows.append(
                    {"segment_id": segment.segment_id, "text": hostile}
                )
        (m_dir / "segments.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in seg_rows),
            encoding="utf-8",
        )
        (m_dir / "final.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in fin_rows),
            encoding="utf-8",
        )

        out = tmp_path / "out-m5-5.epub"
        WriterPhase(settings).run(_COMPLEX, out, m_dir, "APPEND_BLOCK")

        out_b = b"".join(_read_doc_bytes(out, d) for d in _spine_docs(book))
        # Литералы как текст: сериализация экранирует < > &.
        assert b"alert(1)" in out_b, "текст <script> сохранён как узел текста"
        assert b"<script" not in out_b, "литеральный <script> стал элементом"
        assert b"&amp;" in out_b, "литеральная & экранирована"
        # Новых элементов вне карты (markup_map пуста) — 0: счётчик каждого тега
        # по всем spine-документам в выходе == в источнике (только оригинальные).
        for tag in (b"<b", b"<i", b"<script", b"<strong", b"<code", b"<a "):
            src_count = sum(_read_doc_bytes(_COMPLEX, d).count(tag) for d in _spine_docs(book))
            out_count = sum(_read_doc_bytes(out, d).count(tag) for d in _spine_docs(book))
            assert out_count == src_count, (
                f"новый элемент вне карты: {tag!r} {src_count}->{out_count}"
            )
    finally:
        db.close()


# ------------------------------------------------------------------ M5-7


def test_m5_7_kill9_no_partial_output(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # M5-7: «kill -9» в момент публикации (os.replace) → частичного ВЫХОДНОГО файла
    # нет: либо отсутствует target, либо прежний валидный; `.partial` — не результат.
    settings, db, _ = _m5_settings(tmp_path, make_env, mock_server)
    try:
        m_dir = _identity_m_dir(settings, _COMPLEX, tmp_path)
        out = tmp_path / "out-m5-7.epub"
        partial = out.with_name(out.name + ".partial")

        def _kill9(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError("simulated kill -9 at os.replace")

        monkeypatch.setattr("os.replace", _kill9)
        with pytest.raises(OSError, match="kill -9"):
            WriterPhase(settings).run(_COMPLEX, out, m_dir, "APPEND_BLOCK")
        # Сразу после «убийства»: выходного файла нет, `.partial` (рабочий) — есть.
        assert not out.exists(), "target.epub появился при прерванной записи"
        assert partial.exists(), ".partial (рабочий файл) должен остаться"

        # Повторный прогон (без kill) доводит до конца: target валиден, .partial ушёл.
        monkeypatch.undo()
        WriterPhase(settings).run(_COMPLEX, out, m_dir, "APPEND_BLOCK")
        assert out.is_file() and not partial.exists(), "после повтора: target, без .partial"
        outcome = _epubcheck(settings, out)
        assert not outcome.errors and not outcome.fatals
    finally:
        db.close()


# ------------------------------------------------------------------ M5-2


def test_m5_2_skeleton_matches_tract_t(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M5-2: скелет выхода M5 == скелет выхода тракта T (та же книга, тот же
    # SubmitKind): 0 различий в деревьях XHTML без текстовых узлов + OPF + nav
    # (атрибуты языка нормализованы: движок их «схлопывает» при ресериализации).
    settings, db, repo = _m5_settings(tmp_path, make_env, mock_server)
    try:
        m_dir = _run_full_tract(settings, repo, "j-m5-2", _COMPLEX, mock_server)
        m5_out = tmp_path / "m5-2.epub"
        WriterPhase(settings).run(_COMPLEX, m5_out, m_dir, "APPEND_BLOCK")
        t_out = _run_tract_t(settings, _COMPLEX, tmp_path / "t-2.epub", tmp_path)
        assert t_out.is_file(), "выход тракта T не создан"

        book = read_epub(_COMPLEX, tmp_path / "work-skel", settings)
        docs = _structural_docs(book, _COMPLEX)
        assert any(d.endswith(".opf") for d in docs), "OPF в списке структурных документов"
        assert len(docs) > 1, "сверка должна охватывать более одного документа"
        findings: list[dict[str, Any]] = []
        for doc in docs:
            m_root = etree.fromstring(_read_doc_bytes(m5_out, doc))
            t_root = etree.fromstring(_read_doc_bytes(t_out, doc))
            _strip_lang(m_root)
            _strip_lang(t_root)
            findings.extend(_diff_trees(m_root, t_root, "/", doc, ignore_text=True))
        assert findings == [], f"M5-2: различия скелета: {findings}"

        # Порог M5-1 на книге с эталоном: 0 ошибок + предупреждений M5 ≤ у тракта T.
        m5_ec = _epubcheck(settings, m5_out)
        t_ec = _epubcheck(settings, t_out)
        assert not m5_ec.errors and not m5_ec.fatals, "M5-1: ошибки в выходе M5"
        assert len(m5_ec.warnings) <= len(t_ec.warnings), (
            f"M5-1: предупреждений M5 ({len(m5_ec.warnings)}) > "
            f"тракта T ({len(t_ec.warnings)})"
        )
    finally:
        mock_server.set_scenario("ok")
        db.close()


# ------------------------------------------------------------------ M5-3


def _aligned_xpaths(report: Any) -> set[tuple[str, str, str]]:
    """Множество переведённых блоков: {(doc, translation_xpath, how)} по парам E6."""
    return {
        (d.doc, p.translation_xpath, p.how) for d in report.docs for p in d.aligned
    }


def test_m5_3_translated_block_set_equals_tract_t(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M5-3: множество переведённых блоков M5 = множеству тракта T (сверка XPath):
    # те же блоки, те же позиции вставки, то же расположение (sibling/inside).
    settings, db, repo = _m5_settings(tmp_path, make_env, mock_server)
    try:
        m_dir = _run_full_tract(settings, repo, "j-m5-3", _COMPLEX, mock_server)
        m5_out = tmp_path / "m5-3.epub"
        WriterPhase(settings).run(_COMPLEX, m5_out, m_dir, "APPEND_BLOCK")
        t_out = _run_tract_t(settings, _COMPLEX, tmp_path / "t-3.epub", tmp_path)

        book = read_epub(_COMPLEX, tmp_path / "work-set", settings)
        tags = frozenset(settings.segmentation.block_tags)
        common = {
            "book": book,
            "source_path": _COMPLEX,
            "submit_mode": "APPEND_BLOCK",
            "block_tags": tags,
            "min_coverage": settings.alignment.min_coverage,
            "identical_whitelist": settings.alignment.identical_whitelist,
        }
        m5_set = _aligned_xpaths(build_alignment_report(target_path=m5_out, **common))
        t_set = _aligned_xpaths(build_alignment_report(target_path=t_out, **common))
        assert m5_set, "M5-3: нет ни одного переведённого блока (M5)"
        assert m5_set == t_set, (
            f"M5-3: множества переведённых блоков различаются\n"
            f"только в M5: {sorted(m5_set - t_set)}\n"
            f"только в T : {sorted(t_set - m5_set)}"
        )
    finally:
        mock_server.set_scenario("ok")
        db.close()


# ------------------------------------------------------------------ M5-4


def test_m5_4_e6_map_equals_write_map(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M5-4: карта E6 = write_map.json — 100 % сегментов: для каждого сегмента
    # XPath блока перевода из E6 совпадает с записью write_map.json.
    settings, db, repo = _m5_settings(tmp_path, make_env, mock_server)
    try:
        m_dir = _run_full_tract(settings, repo, "j-m5-4", _COMPLEX, mock_server)
        m5_out = tmp_path / "m5-4.epub"
        result = WriterPhase(settings).run(_COMPLEX, m5_out, m_dir, "APPEND_BLOCK")
        write_map = json.loads((m_dir / "write_map.json").read_text(encoding="utf-8"))

        # 100 % сегментов: write_map покрывает все сегменты (== результат прогона).
        assert len(write_map) == result.segment_count == len(result.write_map)
        assert set(write_map) == set(result.write_map)

        book = read_epub(_COMPLEX, tmp_path / "work-e6", settings)
        report = build_alignment_report(
            book=book,
            source_path=_COMPLEX,
            target_path=m5_out,
            submit_mode="APPEND_BLOCK",
            block_tags=frozenset(settings.segmentation.block_tags),
            min_coverage=settings.alignment.min_coverage,
            identical_whitelist=settings.alignment.identical_whitelist,
        )
        e6_map = {
            p.segment_id: p.translation_xpath
            for d in report.docs
            for p in d.aligned
        }
        # Каждый сегмент из write_map найден E6, и XPath совпадает.
        for seg_id, xpath in write_map.items():
            assert seg_id in e6_map, f"M5-4: сегмент {seg_id} не выровнен E6"
            assert e6_map[seg_id] == xpath, (
                f"M5-4: расхождение E6/write_map для {seg_id}: "
                f"E6={e6_map[seg_id]} write_map={xpath}"
            )
    finally:
        mock_server.set_scenario("ok")
        db.close()
