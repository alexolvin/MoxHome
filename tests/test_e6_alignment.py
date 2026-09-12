"""T-E6: выравнивание оригинал ↔ перевод (CONTROL E6-1…E6-7, T-E6-8).

Механизм — измеренная разметка движка (E1, `docs/ENGINE_PROBE.md` §3 P3):
APPEND_BLOCK — оригинал сохранён, перевод — следующий sibling текстового
корня (контейнер: blockquote с одним <p> — ВНУТРЬ); у блока с id маркер
`id__translated`, у блока без id — позиция + копия тега. REPLACE — элемент
изменён на месте, исходного текста в выходе нет (источник материалов —
исходный файл, T-E6-8).

Тесты:
- T-E6-1/2 — доля выравнивания ≥ 99.5% на каждой из 5 фикстур; пар с
  несовпадающим segment_id = 0.
- T-E6-3 — невыровненные перечислены 100% с причиной (фиксированный набор).
- T-E6-4 — перестановка блоков: каждый вариант, ломающий пары, обнаружен
  (маркер/отсутствует/xpath/лишний блок); перенос нетронутой пары —
  неверных пар нет.
- T-E6-5 — повторный запуск → побитово идентичный отчёт.
- T-E6-6 — охват: jobs.alignment_coverage == quality == отчёт (разница 0).
- T-E6-7 — карта хранит XPath перевода, пригодный для патча (100%
  разрешаются); книга с таблицами и сносками — вложенные не ломаются.
- T-E6-8 — REPLACE: исходный текст пары — из исходного файла, не из выхода.
- Ниже порога — E6-предупреждение из run_validating_output (переход в
  COMPLETED_WITH_WARNINGS — логика воркера, доказана в E5).
- Белый список — «идентичен по числам» не нарушение, если текст подходит
  под настраиваемый шаблон; некорректный шаблон — ConfigurationError.

Прогон в процессе (Pipeline) — по образцу E5. PASS на mock (D3, rot3) не
является PASS реальных прогонов (BLOCKER-1) — честно.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lxml.etree
import pytest

from app.core.config import Settings, load_settings
from app.core.errors import ConfigurationError
from app.domain.book import Book, Segment
from app.domain.job import Job
from app.epub import aligner, segmenter
from app.epub.reader import read_epub
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

Z_ALPHA = "OEBPS/z_alpha.xhtml"
M_MID = "OEBPS/m_mid.xhtml"
A_LAST = "OEBPS/a_last.xhtml"


# ------------------------------------------------------------------ фикстуры


@dataclass
class BookRun:
    """Результат сквозного in-process прогона книги (validating → … → QC+align)."""

    name: str
    mode: str
    job: Job
    job_dir: Path
    quality: dict[str, Any]
    warnings: list[str]
    report: dict[str, Any]


@dataclass
class E6Env:
    """Модульное окружение: mock + одна БД + кэш прогонов по книгам/режимам."""

    settings: Settings
    repo: JobRepository
    server: MockLLMServer
    runs: dict[str, BookRun] = field(default_factory=dict)

    def run_book(self, name: str, mode: str = "bilingual") -> BookRun:
        key = f"{name}:{mode}"
        if key not in self.runs:
            path, target = BOOKS[name]
            job = create_job(
                self.repo,
                self.settings,
                source_path=path,
                target_language=target,
                mode=mode,
            )
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
            report: dict[str, Any] = json.loads(
                (job_dir / "alignment_report.json").read_text(encoding="utf-8")
            )
            assert (job_dir / "target.epub").is_file(), f"нет результата: {name}"
            self.runs[key] = BookRun(name, mode, job, job_dir, quality, warnings, report)
        return self.runs[key]


@pytest.fixture(scope="module")
def e6_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[E6Env]:
    base = tmp_path_factory.mktemp("e6_module")
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
        yield E6Env(settings=settings, repo=JobRepository(db), server=server)
    finally:
        db.close()
        server.stop()


# ------------------------------------------------------------------ служебное


def _default_tags() -> frozenset[str]:
    """Блочные теги по умолчанию (совпадает с config.example.yaml)."""
    return frozenset(
        ["p", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt", "td"]
    )


def _doc_of(epub: Path, doc: str) -> lxml.etree._Element:
    with zipfile.ZipFile(epub) as z:
        return lxml.etree.fromstring(z.read(doc))


def _el_by_id(root: lxml.etree._Element, elem_id: str) -> lxml.etree._Element:
    for el in root.iter():
        if isinstance(el.tag, str) and el.get("id") == elem_id:
            return el
    raise AssertionError(f"не найден элемент с id={elem_id!r}")


def _body(root: lxml.etree._Element) -> lxml.etree._Element:
    body = segmenter.find_body(root)
    assert body is not None
    return body


def _source_book(e6_env: E6Env, name: str, work: Path) -> Book:
    path, _ = BOOKS[name]
    return read_epub(path, work / name, e6_env.settings)


def _segments_for(book: Book, doc: str) -> tuple[Segment, ...]:
    return next(c for c in book.chapters if c.document_path == doc).segments


def _seg_by_xpath(
    e6_env: E6Env, book: Book, name: str, doc: str, xpath: str
) -> Segment:
    """Сегмент по позиционному XPath исходного элемента (порядок = E2)."""
    blocks = aligner.blocks(
        _doc_of(BOOKS[name][0], doc), frozenset(e6_env.settings.segmentation.block_tags)
    )
    for el, seg in zip(blocks, _segments_for(book, doc), strict=True):
        if segmenter.positional_xpath(el) == xpath:
            return seg
    raise AssertionError(f"нет сегмента с xpath={xpath} в {doc}")


def _fresh_run(e6_env: E6Env, name: str = "complex") -> tuple[Job, Pipeline]:
    """Свежее задание (свой job_dir): validating → planning → translating.

    Повторный прогон той же книги (T-E6-5) и повреждённый выход
    (ниже порога) — через новое задание: стадийный кэш (E3.7) прогоняет
    повторный validating_output того же задания как «уже выполнено».
    """
    path, target = BOOKS[name]
    job = create_job(
        e6_env.repo, e6_env.settings, source_path=path, target_language=target
    )
    pipeline = Pipeline(e6_env.settings)
    j = e6_env.repo.require(job.id)
    pipeline.run_validating(e6_env.repo, j)
    pipeline.run_planning(e6_env.repo, j)
    pipeline.run_translating(e6_env.repo, j, lambda: None)
    return job, pipeline


def _report(
    e6_env: E6Env,
    book: Book,
    source_path: Path,
    target_path: Path,
    submit_mode: str = "APPEND_BLOCK",
) -> aligner.AlignmentReport:
    s = e6_env.settings
    return aligner.build_alignment_report(
        book=book,
        source_path=source_path,
        target_path=target_path,
        submit_mode=submit_mode,
        block_tags=frozenset(s.segmentation.block_tags),
        min_coverage=s.alignment.min_coverage,
        identical_whitelist=s.alignment.identical_whitelist,
    )


def _tampered(
    target_epub: Path,
    doc: str,
    mutate: Callable[[lxml.etree._Element], None],
    out_path: Path,
) -> Path:
    """Копия `target_epub`, где к документу `doc` применён `mutate(root)`.

    Порядок записей исходного архива сохраняется (mimetype первой записью
    без сжатия — E5.4), чтобы EPUBCheck на копии вёл себя как на выходе.
    """
    with zipfile.ZipFile(target_epub) as zin:
        items: list[tuple[str, bytes]] = [
            (info.filename, zin.read(info.filename)) for info in zin.infolist()
        ]
    for i, (n, data) in enumerate(items):
        if n == doc:
            root = lxml.etree.fromstring(data)
            mutate(root)
            items[i] = (
                n,
                lxml.etree.tostring(root, encoding="UTF-8", xml_declaration=True),
            )
    with zipfile.ZipFile(out_path, "w") as zout:
        for n, data in items:
            if n == "mimetype":
                zout.writestr(zipfile.ZipInfo(n), data, compress_type=zipfile.ZIP_STORED)
            else:
                zout.writestr(n, data)
    return out_path


# ------------------------------------------------------------------ T-E6-4: мутации


def _mut_swap_idd_translations(root: lxml.etree._Element) -> None:
    """Вариант a: межамена переводов двух оригиналов с id."""
    body = _body(root)
    p1, p2 = _el_by_id(root, "c1-p1"), _el_by_id(root, "c1-p2")
    p1t, p2t = _el_by_id(root, "c1-p1__translated"), _el_by_id(root, "c1-p2__translated")
    body.remove(p1t)
    body.remove(p2t)
    p1.addnext(p2t)
    p2.addnext(p1t)


def _mut_displace_translation(root: lxml.etree._Element) -> None:
    """Вариант b: сдвиг перевода c1-p1 в конец документа."""
    body = _body(root)
    p1t = _el_by_id(root, "c1-p1__translated")
    body.remove(p1t)
    body.append(p1t)


def _mut_delete_translation(root: lxml.etree._Element) -> None:
    """Вариант c: удаление перевода c1-p1."""
    body = _body(root)
    body.remove(_el_by_id(root, "c1-p1__translated"))


def _mut_delete_original(root: lxml.etree._Element) -> None:
    """Вариант d: удаление оригинала c1-p1 (перевод остаётся сиротой)."""
    body = _body(root)
    body.remove(_el_by_id(root, "c1-p1"))


def _mut_move_intact_pair(root: lxml.etree._Element) -> None:
    """Вариант e: перенос НЕТРОнутой пары (c1-p3 + перевод) в конец."""
    body = _body(root)
    p3, p3t = _el_by_id(root, "c1-p3"), _el_by_id(root, "c1-p3__translated")
    body.remove(p3t)
    body.remove(p3)
    body.append(p3)
    p3.addnext(p3t)


# ------------------------------------------------------------------ T-E6-1/2


def test_e6_1_2_coverage_and_identity(e6_env: E6Env) -> None:
    """E6-1: доля ≥ min_coverage на каждой из 5 фикстур; E6-2: identity = 0."""
    for name in BOOKS:
        run = e6_env.run_book(name)
        rep = run.report
        assert rep["total"] > 0, name
        assert rep["aligned"] + rep["unaligned"] == rep["total"], name
        assert rep["coverage"] >= e6_env.settings.alignment.min_coverage, (
            f"{name}: {rep['coverage']} < {e6_env.settings.alignment.min_coverage}"
        )
        assert rep["identity_mismatches"] == 0, (
            f"{name}: {rep['identity_mismatches']} пар с несовпадающим segment_id"
        )
        # Чистый прогон: E6-предупреждений нет (порог не нарушен):
        assert not [w for w in run.warnings if w.startswith("E6:")], run.warnings


# ------------------------------------------------------------------ T-E6-3


def test_e6_3_unaligned_enumerated_with_reasons(
    e6_env: E6Env, tmp_path: Path
) -> None:
    """Удалённый перевод → невыровненный поимённо с причиной (100% с причиной)."""
    run = e6_env.run_book("complex")
    book = _source_book(e6_env, "complex", tmp_path)
    tampered = _tampered(
        run.job_dir / "target.epub", Z_ALPHA, _mut_delete_translation,
        tmp_path / "e6-3.epub",
    )
    rep = _report(e6_env, book, _COMPLEX, tampered)
    assert rep.unaligned_count == 1, [
        (u.doc, u.source_xpath, u.reason) for d in rep.docs for u in d.unaligned
    ]
    z = next(d for d in rep.docs if d.doc == Z_ALPHA)
    p1 = _seg_by_xpath(e6_env, book, "complex", Z_ALPHA, "/htmlbody[1]p[1]")
    u = next(x for x in z.unaligned if x.segment_id == p1.segment_id)
    assert u.reason == aligner.REASON_MISSING_TRANSLATION
    assert u.detail
    # 100% невыровненных — с причиной из фиксированного набора:
    for d in rep.docs:
        assert len(d.aligned) + len(d.unaligned) == len(_segments_for(book, d.doc))
        for x in d.unaligned:
            assert x.reason in aligner.REASONS
            assert x.segment_id and x.source_xpath and x.detail


# ------------------------------------------------------------------ T-E6-4


def _e64_rep(
    *,
    e6_env: E6Env,
    run: BookRun,
    book: Book,
    tmp_path: Path,
    tag: str,
    mutate: Callable[[lxml.etree._Element], None],
) -> aligner.AlignmentReport:
    tampered = _tampered(
        run.job_dir / "target.epub", Z_ALPHA, mutate, tmp_path / f"e6-4-{tag}.epub"
    )
    return _report(e6_env, book, _COMPLEX, tampered)


def _e64_zdoc(rep: aligner.AlignmentReport) -> aligner.DocAlignment:
    return next(d for d in rep.docs if d.doc == Z_ALPHA)


def _e64_variant_a(e6_env: E6Env, run: BookRun, book: Book, tmp_path: Path) -> None:
    """a: межамена переводов двух оригиналов с id → маркер не совпадает ×2."""
    p1 = _seg_by_xpath(e6_env, book, "complex", Z_ALPHA, "/htmlbody[1]p[1]")
    p2 = _seg_by_xpath(e6_env, book, "complex", Z_ALPHA, "/htmlbody[1]p[2]")
    rep = _e64_rep(
        e6_env=e6_env, run=run, book=book, tmp_path=tmp_path,
        tag="a_swap", mutate=_mut_swap_idd_translations,
    )
    z = _e64_zdoc(rep)
    got = {u.segment_id: u for u in z.unaligned}
    assert set(got) == {p1.segment_id, p2.segment_id}, [
        (u.segment_id, u.reason, u.detail) for u in z.unaligned
    ]
    assert all(
        u.reason == aligner.REASON_MISSING_TRANSLATION and "маркер" in u.detail
        for u in got.values()
    )
    assert rep.unaligned_count == 2


def _e64_variant_b(e6_env: E6Env, run: BookRun, book: Book, tmp_path: Path) -> None:
    """b: сдвиг перевода в конец документа → не найден + лишний блок;
    контейнер (blockquote) остаётся выровненным по СВОЕМУ внутреннему
    переводу (сдвинутый чужой блок не стал «уверенной неверной парой»)."""
    p1 = _seg_by_xpath(e6_env, book, "complex", Z_ALPHA, "/htmlbody[1]p[1]")
    z_segs = _segments_for(book, Z_ALPHA)
    rep = _e64_rep(
        e6_env=e6_env, run=run, book=book, tmp_path=tmp_path,
        tag="b_displace", mutate=_mut_displace_translation,
    )
    z = _e64_zdoc(rep)
    got = {u.segment_id: u for u in z.unaligned}
    assert set(got) == {p1.segment_id}, [
        (u.segment_id, u.reason, u.detail) for u in z.unaligned
    ]
    assert got[p1.segment_id].reason == aligner.REASON_MISSING_TRANSLATION
    assert rep.extra_count == 1
    assert len(z.aligned) + len(z.unaligned) == len(z_segs)
    bq_pair = next(
        (a for a in z.aligned if a.source_xpath == "/htmlbody[1]blockquote[1]"), None
    )
    assert bq_pair is not None and bq_pair.how == "inside"


def _e64_variant_c(e6_env: E6Env, run: BookRun, book: Book, tmp_path: Path) -> None:
    """c: удалённый перевод → missing_translation, лишних блоков нет."""
    p1 = _seg_by_xpath(e6_env, book, "complex", Z_ALPHA, "/htmlbody[1]p[1]")
    rep = _e64_rep(
        e6_env=e6_env, run=run, book=book, tmp_path=tmp_path,
        tag="c_delete_trans", mutate=_mut_delete_translation,
    )
    z = _e64_zdoc(rep)
    got = {u.segment_id: u for u in z.unaligned}
    assert set(got) == {p1.segment_id}
    assert got[p1.segment_id].reason == aligner.REASON_MISSING_TRANSLATION
    assert rep.extra_count == 0


def _e64_variant_d(e6_env: E6Env, run: BookRun, book: Book, tmp_path: Path) -> None:
    """d: удалённый оригинал → xpath_mismatch + сиротский перевод = extra."""
    p1 = _seg_by_xpath(e6_env, book, "complex", Z_ALPHA, "/htmlbody[1]p[1]")
    rep = _e64_rep(
        e6_env=e6_env, run=run, book=book, tmp_path=tmp_path,
        tag="d_delete_orig", mutate=_mut_delete_original,
    )
    z = _e64_zdoc(rep)
    got = {u.segment_id: u for u in z.unaligned}
    assert set(got) == {p1.segment_id}
    assert got[p1.segment_id].reason == aligner.REASON_XPATH_MISMATCH
    assert len(z.extra_blocks) == 1
    # Сирота — именно c1-p1__translated (сверка по тексту перевода):
    out_root = _doc_of(run.job_dir / "target.epub", Z_ALPHA)
    p1t_text = segmenter.normalize_text(
        segmenter.full_text(_el_by_id(out_root, "c1-p1__translated"))
    )
    assert z.extra_blocks[0].text == p1t_text


def _e64_variant_e(e6_env: E6Env, run: BookRun, book: Book, tmp_path: Path) -> None:
    """e: перенос нетронутой пары (c1-p3 + перевод) в конец — порядок
    документа нарушен: рассогласование ОБНАРУЖЕНО (сегменты после p3 не
    сопоставляются монотонно — xpath_mismatch), и НЕТ «уверенных неверных
    пар»: c1-p3 спарена только со своим переводом, остальные — невыровнены,
    а не неправильно спарены (инвариант движка: порядок оригиналов в
    выходе = порядку документа, E1 §3)."""
    p3 = _seg_by_xpath(e6_env, book, "complex", Z_ALPHA, "/htmlbody[1]p[3]")
    z_segs = _segments_for(book, Z_ALPHA)
    rep = _e64_rep(
        e6_env=e6_env, run=run, book=book, tmp_path=tmp_path,
        tag="e_intact", mutate=_mut_move_intact_pair,
    )
    z = _e64_zdoc(rep)
    got_un = {u.segment_id: u for u in z.unaligned}
    later_ids = {s.segment_id for s in z_segs[4:]}  # li1, li2, bq, внутренний p
    assert later_ids <= set(got_un), [(u.segment_id, u.reason) for u in z.unaligned]
    assert all(u.reason == aligner.REASON_XPATH_MISMATCH for u in got_un.values())
    # До p3 (включительно) — корректно выровнены; неверных пар нет:
    assert {a.segment_id for a in z.aligned} == {s.segment_id for s in z_segs[:4]}
    p3_pair = next(a for a in z.aligned if a.segment_id == p3.segment_id)
    assert p3_pair.how == "sibling"
    moved = _el_by_id(_doc_of(run.job_dir / "target.epub", Z_ALPHA), "c1-p3__translated")
    # Сохранённый XPath — на НОВОЕ место блока перевода (конец документа):
    assert segmenter.positional_xpath(moved) == p3_pair.translation_xpath
    assert p3_pair.translation_text != p3_pair.source_text
    # Переводы невыровненных оригиналов — осиротевшие вставки (extra):
    # li1t, li2t и внутренний p-перевод (общий для blockquote и <p>).
    assert rep.extra_count == 3


def test_e6_4_shuffle_variants(e6_env: E6Env, tmp_path: Path) -> None:
    """Каждый вариант, ломающий пары, обнаружен; неверных пар нет (T-E6-4)."""
    run = e6_env.run_book("complex")
    book = _source_book(e6_env, "complex", tmp_path)
    for variant in (
        _e64_variant_a,
        _e64_variant_b,
        _e64_variant_c,
        _e64_variant_d,
        _e64_variant_e,
    ):
        variant(e6_env, run, book, tmp_path)


# ------------------------------------------------------------------ T-E6-5


def test_e6_5_deterministic_report(e6_env: E6Env) -> None:
    """Повторный прогон (свежее задание, тот же вход) → побитово идентичный
    alignment_report.json (E6-5)."""
    first_run = e6_env.run_book("complex")
    first = (first_run.job_dir / "alignment_report.json").read_bytes()
    job, pipeline = _fresh_run(e6_env, "complex")
    pipeline.run_validating_output(e6_env.repo, e6_env.repo.require(job.id))
    second = (pipeline.job_dir(job.id) / "alignment_report.json").read_bytes()
    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


# ------------------------------------------------------------------ T-E6-6


def test_e6_6_coverage_single_source(e6_env: E6Env) -> None:
    """Охват: jobs.alignment_coverage == quality == отчёт (разница 0, 5 книг)."""
    for name in BOOKS:
        run = e6_env.run_book(name)
        job_now = e6_env.repo.require(run.job.id)
        assert job_now.alignment_coverage is not None, name
        assert job_now.alignment_coverage == run.quality["alignment"]["coverage"], name
        assert job_now.alignment_coverage == run.report["coverage"], name


# ------------------------------------------------------------------ T-E6-7


def test_e6_7_patchable_xpath(e6_env: E6Env, tmp_path: Path) -> None:
    """100% пар: XPath перевода разрешается на элемент в выходном файле.

    T-E6-7: книга с таблицами и сносками (complex) — вложенные структуры
    не ломаются: пары ячеек (deformed table) и вложенных (blockquote)
    присутствуют.
    """
    tags = frozenset(e6_env.settings.segmentation.block_tags)
    checked = 0
    for name in BOOKS:
        run = e6_env.run_book(name)
        for d in run.report["docs"]:
            out_root = _doc_of(run.job_dir / "target.epub", d["doc"])
            for p in d["aligned"]:
                el = aligner.resolve_positional_xpath(out_root, p["translation_xpath"])
                assert el is not None, (name, d["doc"], p["translation_xpath"])
                assert segmenter.localname(el) in tags, p["translation_xpath"]
                assert (
                    segmenter.normalize_text(segmenter.full_text(el))
                    == p["translation_text"]
                )
                assert p["how"] in ("sibling", "inside", "inplace")
                checked += 1
    assert checked > 0

    # complex — фикстура T-E6-7: таблицы и сноски присутствуют в источнике:
    book = _source_book(e6_env, "complex", tmp_path)
    assert book.stats.table_count > 0
    assert book.stats.footnote_count > 0

    run = e6_env.run_book("complex")
    m_segs = _segments_for(book, M_MID)
    m_blocks = aligner.blocks(_doc_of(_COMPLEX, M_MID), tags)
    src_tag_by_id = {
        seg.segment_id: segmenter.localname(el)
        for el, seg in zip(m_blocks, m_segs, strict=True)
    }
    m = next(d for d in run.report["docs"] if d["doc"] == M_MID)
    # Пары ячеек таблицы: все 4 <td> выровнены (deformed table, E1 §3 п.6):
    td_ids = [sid for sid, tag in src_tag_by_id.items() if tag == "td"]
    assert len(td_ids) == 4
    td_pairs = [a for a in m["aligned"] if a["segment_id"] in td_ids]
    assert len(td_pairs) == 4, [a["segment_id"] for a in td_pairs]
    assert all(a["how"] == "sibling" for a in td_pairs)
    # Сночка (c2-fn1) выровнена, не потеряна:
    fn = next(seg for seg in m_segs if "Footnote" in seg.plain_text)
    assert any(a["segment_id"] == fn.segment_id for a in m["aligned"])
    # Вложенный blockquote (a_last) — how=inside:
    a_last = next(d for d in run.report["docs"] if d["doc"] == A_LAST)
    bq = next(
        (a for a in a_last["aligned"] if a["source_xpath"] == "/htmlbody[1]blockquote[1]"),
        None,
    )
    assert bq is not None and bq["how"] == "inside"


# ------------------------------------------------------------------ T-E6-8


def test_e6_8_replace_source_from_original(e6_env: E6Env, tmp_path: Path) -> None:
    """REPLACE: исходный текст пары — из ИСХОДНОГО файла, не из выхода."""
    run = e6_env.run_book("complex", mode="translation")
    assert run.report["submit_mode"] == "REPLACE"
    assert "T-E6-8" in run.report["source_text_source"]

    src_root = _doc_of(_COMPLEX, Z_ALPHA)
    out_root = _doc_of(run.job_dir / "target.epub", Z_ALPHA)
    z = next(d for d in run.report["docs"] if d["doc"] == Z_ALPHA)

    for p in z["aligned"]:
        assert p["how"] == "inplace", p
        assert p["translation_xpath"] == p["source_xpath"]  # адрес сохранён
        el = aligner.resolve_positional_xpath(out_root, p["translation_xpath"])
        assert el is not None
        assert (
            segmenter.normalize_text(segmenter.full_text(el)) == p["translation_text"]
        )

    p1 = next(p for p in z["aligned"] if p["source_xpath"] == "/htmlbody[1]p[1]")
    src_p1 = next(
        el
        for el in src_root.iter()
        if isinstance(el.tag, str)
        and segmenter.positional_xpath(el) == "/htmlbody[1]p[1]"
    )
    assert p1["source_text"] == segmenter.normalize_text(
        segmenter.full_text(src_p1)
    )
    assert p1["source_text"] != p1["translation_text"]
    # Исходного текста в выходном документе нет (заменён на месте):
    out_body_text = "".join(_body(out_root).itertext())
    assert "The quick brown fox" not in out_body_text
    assert p1["translation_text"] in segmenter.normalize_text(out_body_text)


# ------------------------------------------------------------- ниже порога


def test_alignment_below_threshold_warns(e6_env: E6Env, tmp_path: Path) -> None:
    """Повреждённый выход: ниже min_coverage → E6-предупреждение + охват в БД.

    Сквозной путь на свежем задании (стадийный кэш E3.7 не прогоняет
    validating_output как «выполненное»): прогон до translating → повреждение
    выхода (удалён один перевод) → run_validating_output. Переход в
    COMPLETED_WITH_WARNINGS по предупреждениям — логика воркера, доказана в E5.
    """
    job, pipeline = _fresh_run(e6_env, "complex")
    job_dir = pipeline.job_dir(job.id)
    tampered = _tampered(
        job_dir / "target.epub",
        Z_ALPHA,
        _mut_delete_translation,
        tmp_path / "below_threshold.epub",
    )
    shutil.copyfile(tampered, job_dir / "target.epub")
    warnings = pipeline.run_validating_output(e6_env.repo, e6_env.repo.require(job.id))
    e6_warnings = [w for w in warnings if w.startswith("E6: выравнивание ниже порога")]
    assert e6_warnings, warnings
    job_now = e6_env.repo.require(job.id)
    assert job_now.alignment_coverage is not None
    assert job_now.alignment_coverage < e6_env.settings.alignment.min_coverage
    quality: dict[str, Any] = json.loads(
        (job_dir / "quality.json").read_text(encoding="utf-8")
    )
    assert quality["alignment"]["coverage"] == job_now.alignment_coverage
    # quality.json хранит ВСЕ предупреждения (согласованность, дефект E5):
    assert quality["warnings"] == warnings


# ------------------------------------------------------------- белый список


def _synth_segments(doc: str, root: lxml.etree._Element) -> tuple[Segment, ...]:
    segs: list[Segment] = []
    for i, el in enumerate(aligner.blocks(root, _default_tags())):
        text = segmenter.normalize_text(segmenter.full_text(el))
        segs.append(
            Segment(
                segment_id=segmenter.make_segment_id(
                    doc, segmenter.positional_xpath(el), text
                ),
                chapter_id="ch1",
                index_in_chapter=i,
                index_global=i,
                plain_text=text,
                marked_text=text,
                char_count=len(text),
                word_count=len(text.split()),
            )
        )
    return tuple(segs)


def test_whitelist_identical_not_violation() -> None:
    """«Идентичен» — нарушение, только если текст НЕ подходит под шаблон."""
    src = lxml.etree.fromstring(
        b'<html><body><p id="a">123</p><p id="b">Hello world</p></body></html>'
    )
    out = lxml.etree.fromstring(
        b'<html><body><p id="a">123</p><p id="a__translated">123</p>'
        b'<p id="b">Hello world</p><p id="b__translated">Fkdswhu wrlgh</p>'
        b"</body></html>"
    )
    segs = _synth_segments("doc.html", src)
    tags = _default_tags()

    default_wl = re.compile(r"^[\d\s.,;:()\-\u2013\u2014/%$€]+$")
    rep = aligner.align_document(
        doc="doc.html",
        src_root=src,
        out_root=out,
        segments=segs,
        submit_mode="APPEND_BLOCK",
        tags=tags,
        whitelist=default_wl,
    )
    assert rep.unaligned == (), [
        (u.segment_id, u.reason, u.detail) for u in rep.unaligned
    ]  # «123» — в белом списке
    assert len(rep.aligned) == 2

    rep_none = aligner.align_document(
        doc="doc.html",
        src_root=src,
        out_root=out,
        segments=segs,
        submit_mode="APPEND_BLOCK",
        tags=tags,
        whitelist=None,
    )
    assert {u.segment_id for u in rep_none.unaligned} == {segs[0].segment_id}
    assert all(u.reason == aligner.REASON_IDENTICAL for u in rep_none.unaligned)


def test_alignment_bad_regex_rejected(
    tmp_path: Path,
    make_env: Callable[..., Path],
    make_yaml: Callable[..., Path],
) -> None:
    """Некорректный шаблон белого списка — ConfigurationError (fail closed)."""
    cfg = make_yaml(
        str(tmp_path / "storage"),
        'alignment:\n  identical_whitelist_pattern: "["\n',
    )
    with pytest.raises(ConfigurationError):
        load_settings(cfg, make_env(None))
