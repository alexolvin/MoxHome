"""Тесты S2: устойчивые выражения и MWE (ТЗ_03 §S2, CONTROL S2-1…S2-6).

Сквозной in-process прогон (Pipeline) до LEARNING — по образцу S1
(S1Env переиспользуется); mock (D3) — детерминированный. Кандидаты —
детерминированный предотбор (шаблоны таксономии + повторяемость,
app/learning/mwe.py); LLM (mwe_note) — только объяснение, не выдвижение
(S2.3). Полнота/точность — на golden-наборах (config/languages/*/mwe_golden.yaml)
по ПРЕДОТБОРУ: сопоставление пар (предложение, id типа) — конвенция L1-11
(качество unit_lemma — справочно, не влияет на recall).

- T-S2-1 — id типа вне таксономии: 0 принятых, отклонение с причиной (S2-1).
- T-S2-2 — de/nl разорванные: полнота на golden (30/язык) ≥ 85% + 0
  выпущенных записей разорванной конструкции без второй части (S2-2).
- T-S2-3 — все части выпущенной записи — в её сегменте; синтетический
  кандидат с частью вне сегмента отклонён (S2-3).
- T-S2-4 — точность на golden ≥ 80% для 5 языков (S2-4).
- T-S2-5 — воспроизводимость детерминированного предотбора: 2 прогона →
  идентичный набор кандидатов (S2-5).
- T-S2-6 — 5 исходных языков сквозным прогоном до LEARNING, метрики в
  отчёте и quality (S2-6).
- T-S2-7 — гейт уровней: Minimal — ни S1, ни S2; Balanced — только S1;
  Learning — S1 + S2 (таблица ТЗ_03).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.domain.book import Book, BookStats, Chapter, Segment
from app.domain.job import JobStatus
from app.epub.aligner import AlignedPair
from app.glossary.consistency import contains_form
from app.languages.frequency import normalize_text
from app.languages.nlp import load_nlp
from app.languages.phenomena import PhenomenaTaxonomy, for_stage, load_phenomena
from app.languages.registry import load_registry
from app.learning.mwe import (
    MweCandidate,
    MwePart,
    check_mwe_records,
    golden_metrics,
    load_mwe_golden,
    select_mwe_candidates,
)

_TESTS_DIR = str(Path(__file__).resolve().parent)
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from mock_llm_server import MockLLMServer  # noqa: E402
from test_l2_glossary import _make_storage, _synth_book  # noqa: E402
from test_s1_vocabulary import S1Env, S1Run  # noqa: E402

# ------------------------------------------------------------------ содержимое

#: S2-книги (en): повторяющиеся конструкции-кандидаты (min_occurrences=2 —
#: данные шаблонов; одна и та же нормализованная поверхность ≥ 2 раз).
_S2_EN_PARAS: tuple[str, ...] = tuple(
    "She gave up the game. He gave up the plan. "
    "We must take a decision. They must take a decision too. "
    "He drinks strong coffee every day."
    for _ in range(8)
)

#: S2-книга (de): отделяемые глаголы (механизм L1 lexical_units).
_S2_DE_PARAS: tuple[str, ...] = tuple(
    "Ich stehe früh auf. Ich gehe langsam aus." for _ in range(8)
)

#: S2-книга (nl): отделяемые глаголы (проверено прогоном модели).
_S2_NL_PARAS: tuple[str, ...] = tuple(
    "Hij belt telkens op. Hij schakelt langzaam uit." for _ in range(8)
)


# ------------------------------------------------------------------ окружение


@pytest.fixture(scope="module")
def s2_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[S1Env]:
    """Модульное окружение S2: S1Env (сквозной прогон до LEARNING) +
    S2-книги. Фикстуры 5 языков — из tests/fixtures/epub (как T-S1-7)."""
    base = tmp_path_factory.mktemp("s2_module")
    books_dir = base / "books"
    books_dir.mkdir()
    books: dict[str, Path] = {}
    for name, title, lang, uid, paras in (
        ("s2_en.epub", "S2 Probe EN", "en", "s2-en-uid-0001", _S2_EN_PARAS),
        ("s2_de.epub", "S2 Probe DE", "de", "s2-de-uid-0002", _S2_DE_PARAS),
        ("s2_nl.epub", "S2 Probe NL", "nl", "s2-nl-uid-0003", _S2_NL_PARAS),
    ):
        path = books_dir / name
        _synth_book(path, title, lang, uid, paras)
        books[name] = path
    server = MockLLMServer()
    server.start()
    env = S1Env(server=server, base=base, books=books)
    main_settings, main_db = _make_storage(base, "storage_main", server)
    env.add_profile("main", main_settings, main_db)
    try:
        yield env
    finally:
        env.close()
        server.stop()


# ------------------------------------------------------------------ служебное


def _mwe_report(run: S1Run) -> dict[str, Any]:
    path = run.job_dir / "mwe_records.json"
    assert path.is_file(), f"нет mwe_records.json: {run.error}"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _golden_book(
    code: str, texts: tuple[str, ...]
) -> tuple[Book, tuple[AlignedPair, ...]]:
    """Книга-«прогон» для engine-тестов: одно предложение = один сегмент
    ``golden-{i}`` (соответствует segment_id_prefix метрик)."""
    segments = tuple(
        Segment(
            segment_id=f"golden-{i}",
            chapter_id="ch1",
            index_in_chapter=i,
            index_global=i,
            plain_text=text,
            marked_text=text,
            char_count=len(text),
            word_count=len(text.split()),
        )
        for i, text in enumerate(texts)
    )
    book = Book(
        title=f"golden {code}",
        language=code,
        chapters=(
            Chapter(
                chapter_id="ch1",
                title=None,
                document_path="a.xhtml",
                spine_index=0,
                segments=segments,
            ),
        ),
        stats=BookStats(
            chapter_count=1,
            spine_document_count=1,
            segment_count=len(segments),
            char_count=sum(s.char_count for s in segments),
            word_count=sum(s.word_count for s in segments),
            image_count=0,
            table_count=0,
            footnote_count=0,
            has_drm=False,
            token_estimate=0,
        ),
        suspicious=False,
        suspicious_reasons=(),
    )
    pairs = tuple(
        AlignedPair(
            segment_id=seg.segment_id,
            doc="a.xhtml",
            source_xpath=f"//p[{i + 1}]",
            source_text=seg.plain_text,
            translation_xpath=f"//p[{i + 1}]",
            translation_text=seg.plain_text,
            how="sibling",
        )
        for i, seg in enumerate(segments)
    )
    return book, pairs


def _engine_golden(
    s2_env: S1Env, code: str
) -> tuple[int, int, int, tuple[MweCandidate, ...]]:
    """Прогон детерминированного предотбора по golden-набору языка:
    (expected, matched, produced, candidates)."""
    settings = s2_env.settings_of("main")
    pack = load_registry(settings.languages_root)[code]
    nlp = load_nlp(pack)
    phenomena = for_stage(load_phenomena(settings.languages_root / code), "s2")
    golden = load_mwe_golden(settings.languages_root / code)
    assert golden, f"{code}: golden-набор S2 пуст"
    book, pairs = _golden_book(code, tuple(entry.text for entry in golden))
    candidates = select_mwe_candidates(
        book=book, pairs=pairs, pack=pack, nlp=nlp, phenomena=phenomena
    )
    expected, matched, produced = golden_metrics(
        candidates=candidates, golden=golden
    )
    return expected, matched, produced, candidates


# ------------------------------------------------------------------ T-S2-1


def test_s2_1_bad_id_rejected(s2_env: S1Env) -> None:
    # S2-1: id типа вне таксономии — 0 принятых записей, отклонение с причиной.
    run = s2_env.run(
        "t1", "s2_en.epub", lang="en", learning_level="Learning", scenario="mwe_bad_id"
    )
    assert run.error is None
    report = _mwe_report(run)
    assert report["candidates_total"] > 0, "кандидаты отобраны — есть что валидировать"
    assert all(r["phenomenon_id"] != "ZZ-S2-FAKE" for r in report["records"])
    bad = [
        r
        for r in report["rejected"]
        if any("таксономии" in reason for reason in r["reasons"])
    ]
    assert bad, "запись с id вне таксономии отклонена с причиной"
    bad_surfaces = {r["surface"] for r in bad}
    issued_surfaces = {r["surface"] for r in report["records"]}
    assert not (bad_surfaces & issued_surfaces), "отклонённая запись выпущена"


# ------------------------------------------------------------------ T-S2-2


def test_s2_2_golden_recall_de_nl(s2_env: S1Env) -> None:
    # S2-2 (полнота): de/nl разорванные приставки на golden (30/язык) ≥ 85%;
    # кандидаты отделяемых конструкций — всегда с обеими частями.
    for code in ("de", "nl"):
        expected, matched, _produced, candidates = _engine_golden(s2_env, code)
        assert expected == 30, f"{code}: в golden {expected} ≠ 30"
        recall = matched / expected
        assert recall >= 0.85, f"{code}: recall {recall:.3f} < 0.85"
        separable = [c for c in candidates if c.unit_lemma is not None]
        assert separable, f"{code}: отделяемые кандидаты не отобраны"
        for cand in separable:
            assert len(cand.parts) == 2, (
                f"{code}: кандидат {cand.surface!r} без второй части"
            )


def test_s2_2_issued_separable_have_both_parts(s2_env: S1Env) -> None:
    # S2-2 (записи): 0 выпущенных записей разорванной конструкции без
    # второй части (сквозно, de + nl).
    for lang, book in (("de", "s2_de.epub"), ("nl", "s2_nl.epub")):
        run = s2_env.run(f"t2_{lang}", book, lang=lang, learning_level="Learning")
        assert run.error is None
        report = _mwe_report(run)
        separable = [r for r in report["records"] if r["unit_lemma"]]
        assert separable, f"{lang}: разорванные записи не выпущены"
        for record in separable:
            assert len(record["parts"]) >= 2, (
                f"{lang}: запись {record['surface']!r} без второй части"
            )


# ------------------------------------------------------------------ T-S2-3


def test_s2_3_parts_in_segment(s2_env: S1Env) -> None:
    # S2-3: части КАЖДОЙ выпущенной записи присутствуют в её сегменте.
    run = s2_env.run("t3", "s2_en.epub", lang="en", learning_level="Learning")
    assert run.error is None
    report = _mwe_report(run)
    assert report["records"], "записи выпущены"
    for record in report["records"]:
        seg_text = run.seg_texts[record["segment_id"]]
        for part, _index in record["parts"]:
            assert contains_form(seg_text, part), (
                f"часть {part!r} записи {record['surface']!r} вне сегмента "
                f"{record['segment_id']}"
            )


def test_s2_3_fake_part_rejected(s2_env: S1Env) -> None:
    # S2-3 (unit): кандидат с частью вне сегмента — отклонён; валидный —
    # принят. check_mwe_records — чистая функция валидации.
    settings = s2_env.settings_of("main")
    pack = load_registry(settings.languages_root)["en"]
    taxonomy = PhenomenaTaxonomy(load_phenomena(settings.languages_root / "en"))

    def _normalize(text: str) -> str:
        return normalize_text(text, pack.normalization)

    item = {
        "surface": "strong coffee",
        "phenomenon_id": "EN-COLLOCATION",
        "canonical": "strong coffee",
        "meaning": "м",
        "translation": "т",
    }
    seg_text = "He drinks strong coffee every day."
    fake = MweCandidate(
        phenomenon_id="EN-COLLOCATION",
        surface="strong coffee",
        parts=(MwePart("strong", 3), MwePart("ghostword", 4)),
        unit_lemma=None,
        first_segment_id="seg-0",
        chapter_id="ch1",
        segment_ids=("seg-0",),
        occurrences=2,
        example_sentence=seg_text,
        source_text_hash="x" * 64,
    )
    validated, rejected = check_mwe_records(
        items=[item],
        candidates={_normalize("strong coffee"): fake},
        taxonomy=taxonomy,
        seg_texts={"seg-0": seg_text},
        normalize=_normalize,
    )
    assert validated == []
    assert rejected and any("вне сегмента" in r for r in rejected[0]["reasons"])
    good = MweCandidate(
        phenomenon_id="EN-COLLOCATION",
        surface="strong coffee",
        parts=(MwePart("strong", 3), MwePart("coffee", 4)),
        unit_lemma=None,
        first_segment_id="seg-0",
        chapter_id="ch1",
        segment_ids=("seg-0",),
        occurrences=2,
        example_sentence=seg_text,
        source_text_hash="x" * 64,
    )
    validated, rejected = check_mwe_records(
        items=[item],
        candidates={_normalize("strong coffee"): good},
        taxonomy=taxonomy,
        seg_texts={"seg-0": seg_text},
        normalize=_normalize,
    )
    assert len(validated) == 1 and rejected == []


# ------------------------------------------------------------------ T-S2-4


@pytest.mark.parametrize("code", ["de", "nl", "en", "fr", "it"])
def test_s2_4_golden_precision(s2_env: S1Env, code: str) -> None:
    # S2-4: точность на golden ≥ 80% для каждого языка.
    _expected, matched, produced, _candidates = _engine_golden(s2_env, code)
    assert produced > 0, f"{code}: предотбор не выдал кандидатов"
    precision = matched / produced
    assert precision >= 0.80, f"{code}: precision {precision:.3f} < 0.80"


# ------------------------------------------------------------------ T-S2-5


def test_s2_5_reproducible_selection(s2_env: S1Env) -> None:
    # S2-5: воспроизводимость детерминированного предотбора — два прогона
    # дают идентичный набор кандидатов (и сквозно: идентичный отчёт).
    settings = s2_env.settings_of("main")
    pack = load_registry(settings.languages_root)["de"]
    nlp = load_nlp(pack)
    phenomena = for_stage(load_phenomena(settings.languages_root / "de"), "s2")
    golden = load_mwe_golden(settings.languages_root / "de")
    book, pairs = _golden_book("de", tuple(entry.text for entry in golden))
    first = select_mwe_candidates(
        book=book, pairs=pairs, pack=pack, nlp=nlp, phenomena=phenomena
    )
    second = select_mwe_candidates(
        book=book, pairs=pairs, pack=pack, nlp=nlp, phenomena=phenomena
    )
    assert first == second, "предотбор не воспроизводится"
    run_a = s2_env.run("t5a", "s2_en.epub", lang="en", learning_level="Learning")
    run_b = s2_env.run("t5b", "s2_en.epub", lang="en", learning_level="Learning")
    assert _mwe_report(run_a)["candidates"] == _mwe_report(run_b)["candidates"]


# ------------------------------------------------------------------ T-S2-6


@pytest.mark.parametrize(
    ("lang", "book"),
    [
        ("en", "complex.epub"),
        ("de", "lang_de.epub"),
        ("fr", "lang_fr.epub"),
        ("it", "lang_it.epub"),
        ("nl", "lang_nl.epub"),
    ],
)
def test_s2_6_five_source_languages(s2_env: S1Env, lang: str, book: str) -> None:
    # S2-6: 5 исходных языков — прогон до LEARNING, метрики S2 в отчёте и
    # quality (малые фикстуры могут дать 0 кандидатов — семантика warning).
    run = s2_env.run(f"t6_{lang}", book, lang=lang, learning_level="Learning")
    assert run.error is None, f"{lang}: {run.error}"
    assert run.job.status is JobStatus.LEARNING
    assert run.quality["s2"]["enabled"] is True
    for key in ("book_words", "candidates", "issued", "rejected", "separable", "report"):
        assert key in run.quality["s2"], f"{lang}: нет метрики {key}"
    report = _mwe_report(run)
    assert report["records_issued"] == len(report["records"])
    assert report["records_rejected"] == len(report["rejected"])
    for record in report["records"]:
        assert record["canonical"], f"{lang}: пустой канонический вид"
        assert record["segment_id"] in run.seg_texts


# ------------------------------------------------------------------ T-S2-7


def test_s2_7_level_gating(s2_env: S1Env) -> None:
    # Таблица уровней ТЗ_03: Minimal — только оригинал+перевод (learning-
    # материалов нет); Balanced — +S1; Learning — +S2.
    minimal = s2_env.run("t7_min", "s2_en.epub", lang="en", learning_level="Minimal")
    assert minimal.error is None
    assert minimal.job.status is JobStatus.LEARNING
    assert "s1" not in minimal.quality, "Minimal: S1 не должен исполняться"
    assert "s2" not in minimal.quality, "Minimal: S2 не должен исполняться"
    assert not (minimal.job_dir / "vocabulary_cards.json").is_file()
    assert not (minimal.job_dir / "mwe_records.json").is_file()

    balanced = s2_env.run("t7_bal", "s2_en.epub", lang="en", learning_level="Balanced")
    assert "s1" in balanced.quality, "Balanced: S1 исполняется"
    assert "s2" not in balanced.quality, "Balanced: S2 не входит в уровень"
    assert not (balanced.job_dir / "mwe_records.json").is_file()

    learning = s2_env.run("t7_learn", "s2_en.epub", lang="en", learning_level="Learning")
    assert "s1" in learning.quality and "s2" in learning.quality
    assert (learning.job_dir / "mwe_records.json").is_file()
