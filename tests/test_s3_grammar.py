# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Тесты S3: грамматические заметки (ТЗ_03 §S3, CONTROL S3-1…S3-7).

Сквозной in-process прогон (Pipeline) до LEARNING — по образцу S2 (S1Env
переиспользуется); mock (D3) — детерминированный. Кандидаты —
детерминированный предотбор по морфологическим признакам (шаблоны
таксономии в скопе `stages: [s3]`, app/learning/mwe.py); LLM (grammar_note) —
только объяснение НАЙДЕННОГО, не выдвижение (S3.5). Явление объясняется
ОДИН раз развёрнуто, далее — детерминированная краткая ссылка (S3.4);
≤K заметок на главу (S3.3). Валидация (S3.6): id ∈ таксономии; заметка не
«подброшена»; цитата ∈ сегменте; длина ≤ лимита (превышение — отклонение,
не усечка); язык пояснения = целевой.

- T-S3-1 — id явления вне таксономии: 0 принятых, отклонение с причиной
  (S3-1).
- T-S3-2 — «подброшенная» заметка (surface вне кандидатов) отклонена;
  цитата вне сегмента отклонена (unit) (S3-2).
- T-S3-3 — ≤K заметок на главу; фактический счёт в отчёте; не отобранные —
  записаны (S3-3).
- T-S3-4 — повторные развёрнутые объяснения одного явления: 0; остальные
  кандидаты явления — ссылки на первую заметку (S3-4).
- T-S3-5 — язык каждого выпущенного пояснения = целевой (S3-5).
- T-S3-6 — покрытие golden (20/язык) ≥ 75% на ПРЕДОТБОРЕ (S3-6).
- T-S3-7 — 5/5 исходных языков + гейт уровней (Minimal/Balanced — нет S3;
  Learning/Intensive — да).
- T-S3-8 — de фикстура с придаточным: DE-NEBENSATZ-VERBLETZT обнаружен и
  объяснён.
- T-S3-9 — it фикстура: IT-CONGIUNTIVO; fr фикстура: FR-SUBJONCTIF.
- T-S3-10 — заметка выше лимита ОТКЛОНЯЕТСЯ, а не усекается (S3.1).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.domain.job import JobStatus
from app.glossary.consistency import contains_form
from app.languages.detect import detect_text, normalize_code
from app.languages.frequency import normalize_text
from app.languages.nlp import load_nlp
from app.languages.phenomena import PhenomenaTaxonomy, for_stage, load_phenomena
from app.languages.registry import load_registry
from app.learning.grammar import (
    check_grammar_notes,
    load_grammar_golden,
    reference_explanation,
)
from app.learning.mwe import (
    MweCandidate,
    MwePart,
    golden_metrics,
    select_mwe_candidates,
)

_TESTS_DIR = str(Path(__file__).resolve().parent)
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from mock_llm_server import _GRAMMAR_EXPLANATION, MockLLMServer  # noqa: E402
from test_l2_glossary import _make_storage, _synth_book  # noqa: E402
from test_s1_vocabulary import S1Env, S1Run  # noqa: E402
from test_s2_mwe import _golden_book  # noqa: E402

# ------------------------------------------------------------------ содержимое

#: S3-книга (en): 12 шаблонов × 4 повторения = 48 сегментов. 16
#: детерминированных кандидатов > лимита K=10 на главу (T-S3-3); явления с
#: несколькими кандидатами (EN-COND-2, EN-GERUND-VS-INF, EN-MODAL-PERFECT) —
#: механизм «объяснить один раз» + ссылки (T-S3-4).
_S3_EN_SENTS: tuple[str, ...] = (
    "She has finished her work.",
    "She has finished the job.",
    "The bridge was built in 1900.",
    "The house was built by workers.",
    "They have been waiting for hours.",
    "She must have forgotten the key.",
    "If I were you, I would leave.",
    "I enjoy swimming every day.",
    "I want to swim in the lake.",
    "He should have called his sister.",
    "If she had known, she would have come.",
    "If they had asked, they would have come.",
)
_S3_EN_PARAS: tuple[str, ...] = tuple(s for _ in range(4) for s in _S3_EN_SENTS)

#: S3-книга (de): придаточные (T-S3-8) + рекция + модальная частица.
_S3_DE_PARAS: tuple[str, ...] = tuple(
    s
    for _ in range(8)
    for s in (
        "Ich weiß, dass er morgen kommt.",
        "Ich warte auf den Zug.",
        "Ich warte auf dich.",
        "Das ist doch klar.",
    )
)

#: S3-книги (it/fr): фикстуры T-S3-9 — формы, которые модели стабильно
#: размечают (ит. конъюнктив Sub; фр. — список словоформ, теггер fr слаб).
_S3_IT_PARAS: tuple[str, ...] = tuple("Spero che venga presto." for _ in range(8))
_S3_FR_PARAS: tuple[str, ...] = tuple("Je souhaite que tu viennes." for _ in range(8))

#: S3-книга (nl): V2 + глагольная конечная группа + perfectum.
_S3_NL_PARAS: tuple[str, ...] = tuple(
    s for _ in range(8) for s in ("Ik wil hem helpen.", "Hij is gisteren gelopen.")
)


# ------------------------------------------------------------------ окружение


@pytest.fixture(scope="module")
def s3_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[S1Env]:
    """Модульное окружение S3: S1Env (сквозной прогон до LEARNING) +
    S3-книги 5 языков."""
    base = tmp_path_factory.mktemp("s3_module")
    books_dir = base / "books"
    books_dir.mkdir()
    books: dict[str, Path] = {}
    for name, title, lang, uid, paras in (
        ("s3_en.epub", "S3 Probe EN", "en", "s3-en-uid-0001", _S3_EN_PARAS),
        ("s3_de.epub", "S3 Probe DE", "de", "s3-de-uid-0002", _S3_DE_PARAS),
        ("s3_it.epub", "S3 Probe IT", "it", "s3-it-uid-0003", _S3_IT_PARAS),
        ("s3_fr.epub", "S3 Probe FR", "fr", "s3-fr-uid-0004", _S3_FR_PARAS),
        ("s3_nl.epub", "S3 Probe NL", "nl", "s3-nl-uid-0005", _S3_NL_PARAS),
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


def _grammar_report(run: S1Run) -> dict[str, Any]:
    path = run.job_dir / "grammar_notes.json"
    assert path.is_file(), f"нет grammar_notes.json: {run.error}"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _grammar_golden(s3_env: S1Env, code: str) -> tuple[int, int, int]:
    """Прогон детерминированного предотбора по grammar-golden языка:
    (expected, matched, produced)."""
    settings = s3_env.settings_of("main")
    pack = load_registry(settings.languages_root)[code]
    nlp = load_nlp(pack)
    phenomena = for_stage(load_phenomena(settings.languages_root / code), "s3")
    golden = load_grammar_golden(settings.languages_root / code)
    assert golden, f"{code}: grammar golden пуст"
    taxonomy_ids = {p.id for p in phenomena}
    for entry in golden:
        for exp in entry.expected:
            assert exp.id in taxonomy_ids, f"{code}: {exp.id} вне s3-таксономии"
    book, pairs = _golden_book(code, tuple(entry.text for entry in golden))
    candidates = select_mwe_candidates(
        book=book, pairs=pairs, pack=pack, nlp=nlp, phenomena=phenomena
    )
    expected, matched, produced = golden_metrics(candidates=candidates, golden=golden)
    return expected, matched, produced


# ------------------------------------------------------------------ T-S3-1


def test_s3_1_bad_id_rejected(s3_env: S1Env) -> None:
    # S3-1: id явления вне таксономии — 0 принятых, отклонение с причиной.
    run = s3_env.run(
        "t1", "s3_en.epub", lang="en", learning_level="Learning",
        scenario="grammar_bad_id",
    )
    assert run.error is None
    report = _grammar_report(run)
    assert report["candidates_total"] > 0, "кандидаты отобраны — есть что валидировать"
    assert all(n["phenomenon_id"] != "ZZ-S3-FAKE" for n in report["notes"])
    bad = [
        r for r in report["rejected"] if any("таксономии" in reason for reason in r["reasons"])
    ]
    assert bad, "заметка с id вне таксономии отклонена с причиной"
    issued_surfaces = {n["surface"] for n in report["notes"]}
    assert not ({r["surface"] for r in bad} & issued_surfaces), "отклонённая заметка выпущена"


# ------------------------------------------------------------------ T-S3-2


def test_s3_2_injected_note_rejected(s3_env: S1Env) -> None:
    # S3-2: «подброшенная» заметка (surface не среди детерминированных
    # кандидатов — цитата, которой нет в книге) — отклонена, не выпущена.
    run = s3_env.run(
        "t2", "s3_en.epub", lang="en", learning_level="Learning",
        scenario="grammar_bad_quote",
    )
    assert run.error is None
    report = _grammar_report(run)
    ghost = [r for r in report["rejected"] if r["surface"] == "Qwzxw"]
    assert ghost, "подброшенная заметка отклонена"
    assert any("кандидатов" in reason for reason in ghost[0]["reasons"])
    assert all(n["surface"] != "Qwzxw" for n in report["notes"])


def test_s3_2_quote_outside_segment_unit(s3_env: S1Env) -> None:
    # S3-2 (unit): кандидат, чья surface НЕ в тексте его сегмента — отклонён;
    # валидный — принят. check_grammar_notes — чистая функция валидации.
    settings = s3_env.settings_of("main")
    pack = load_registry(settings.languages_root)["en"]
    taxonomy = PhenomenaTaxonomy(load_phenomena(settings.languages_root / "en"))

    def _normalize(text: str) -> str:
        return normalize_text(text, pack.normalization)

    item = {
        "surface": "was built",
        "phenomenon_id": "EN-PASSIVE",
        "explanation": _GRAMMAR_EXPLANATION,
    }
    candidate = MweCandidate(
        phenomenon_id="EN-PASSIVE",
        surface="was built",
        parts=(MwePart("was", 1), MwePart("built", 2)),
        unit_lemma=None,
        first_segment_id="seg-0",
        chapter_id="ch1",
        segment_ids=("seg-0",),
        occurrences=2,
        example_sentence="The bridge was built in 1900.",
        source_text_hash="x" * 64,
    )
    kwargs: dict[str, Any] = {
        "taxonomy": taxonomy,
        "normalize": _normalize,
        "max_chars": settings.limits.grammar_note_max_chars,
        "target_language": "ru",
        "seed": settings.language_detection.seed,
        "candidates": {_normalize("was built"): candidate},
    }
    validated, rejected = check_grammar_notes(
        items=[item], seg_texts={"seg-0": "The road was constructed in 1900."}, **kwargs
    )
    assert validated == []
    assert rejected and any("вне сегмента" in reason for reason in rejected[0]["reasons"])
    validated, rejected = check_grammar_notes(
        items=[item], seg_texts={"seg-0": "The bridge was built in 1900."}, **kwargs
    )
    assert len(validated) == 1 and rejected == []


# ------------------------------------------------------------------ T-S3-3


def test_s3_3_per_chapter_cap(s3_env: S1Env) -> None:
    # S3-3: ≤K заметок на главу (K — конфиг); фактический счёт — в отчёте;
    # кандидаты сверх лимита — записаны как не отобранные.
    run = s3_env.run("t3", "s3_en.epub", lang="en", learning_level="Learning")
    assert run.error is None
    report = _grammar_report(run)
    max_k = s3_env.settings_of("main").limits.grammar_max_notes_per_chapter
    assert report["max_notes_per_chapter"] == max_k
    assert report["candidates_total"] > max_k, (
        "книга задана так, чтобы кандидатов было больше K"
    )
    assert report["notes_not_selected"] == report["candidates_total"] - max_k
    assert all(count <= max_k for count in report["per_chapter"].values())
    assert report["per_chapter"]["ch1"] == report["notes_issued"]


# ------------------------------------------------------------------ T-S3-4


def test_s3_4_explain_once(s3_env: S1Env) -> None:
    # S3-4: развёрнутое объяснение каждого явления — ОДНО (0 повторов);
    # остальные кандидаты явления — краткие ссылки на первую заметку.
    run = s3_env.run("t3", "s3_en.epub", lang="en", learning_level="Learning")
    report = _grammar_report(run)
    detailed = [n for n in report["notes"] if n["note_type"] == "detailed"]
    reference = [n for n in report["notes"] if n["note_type"] == "reference"]
    assert report["detailed_notes"] == len(detailed)
    assert report["reference_notes"] == len(reference)
    # Книга задана: 16 кандидатов, 10 отобранных (6 явлений развёрнуто,
    # 4 — ссылки), 6 не отобранных.
    assert report["candidates_total"] == 16
    assert len(detailed) == 6
    assert len(reference) == 4
    assert report["notes_not_selected"] == 6
    pids = [n["phenomenon_id"] for n in detailed]
    assert len(pids) == len(set(pids)), "развёрнутое объяснение одного явления дважды"
    for note in reference:
        first = next(d for d in detailed if d["phenomenon_id"] == note["phenomenon_id"])
        assert note["reference_to"] == first["segment_id"]
        assert note["explanation"] == reference_explanation()
        assert note["model"] == "", "ссылка — детерминированная, LLM не участвует"


# ------------------------------------------------------------------ T-S3-5


def test_s3_5_explanation_language(s3_env: S1Env) -> None:
    # S3-5: язык КАЖДОГО выпущенного пояснения = целевой (ru) — langdetect
    # с фиксированным сидом (детерминированно).
    run = s3_env.run("t3", "s3_en.epub", lang="en", learning_level="Learning")
    settings = s3_env.settings_of("main")
    seed = settings.language_detection.seed
    report = _grammar_report(run)
    assert report["notes"], "заметки выпущены"
    for note in report["notes"]:
        detected = detect_text(note["explanation"], seed)
        assert normalize_code(detected.language) == "ru", (
            f"язык пояснения {note['surface']!r}: {detected.language!r} != ru"
        )


# ------------------------------------------------------------------ T-S3-6


@pytest.mark.parametrize("code", ["de", "nl", "en", "fr", "it"])
def test_s3_6_golden_recall(s3_env: S1Env, code: str) -> None:
    # S3-6: покрытие golden (20 предложений/язык) на ПРЕДОТБОРЕ ≥ 75%
    # (recall; точность не гейтится — доп. кандидаты допустимы).
    expected, matched, produced = _grammar_golden(s3_env, code)
    assert expected >= 20, f"{code}: в golden меньше 20 ожиданий"
    assert produced > 0, f"{code}: предотбор не выдал кандидатов"
    recall = matched / expected
    assert recall >= 0.75, f"{code}: recall {recall:.3f} < 0.75 (S3-6)"


# ------------------------------------------------------------------ T-S3-7


@pytest.mark.parametrize(
    ("lang", "book"),
    [
        ("en", "s3_en.epub"),
        ("de", "s3_de.epub"),
        ("fr", "s3_fr.epub"),
        ("it", "s3_it.epub"),
        ("nl", "s3_nl.epub"),
    ],
)
def test_s3_7_five_source_languages(s3_env: S1Env, lang: str, book: str) -> None:
    # S3-7: 5 исходных языков — прогон до LEARNING, заметки выпущены,
    # метрики S3 в отчёте и quality.
    run = s3_env.run(f"t7_{lang}", book, lang=lang, learning_level="Learning")
    assert run.error is None, f"{lang}: {run.error}"
    assert run.job.status is JobStatus.LEARNING
    assert run.quality["s3"]["enabled"] is True
    for key in (
        "book_words", "candidates", "issued", "rejected", "detailed",
        "reference", "not_selected", "max_per_chapter", "per_chapter", "report",
    ):
        assert key in run.quality["s3"], f"{lang}: нет метрики {key}"
    report = _grammar_report(run)
    assert report["stage"] == "S3"
    assert report["notes_issued"] == len(report["notes"])
    assert report["notes_rejected"] == len(report["rejected"])
    assert report["notes_issued"] > 0, f"{lang}: заметки выпущены"
    for note in report["notes"]:
        assert note["explanation"], f"{lang}: пустое пояснение"
        assert note["segment_id"] in run.seg_texts


def test_s3_7_level_gating(s3_env: S1Env) -> None:
    # Таблица уровней ТЗ_03: S3 (как S2) — только Learning/Intensive.
    minimal = s3_env.run("t7b_min", "s3_en.epub", lang="en", learning_level="Minimal")
    assert minimal.error is None
    assert minimal.job.status is JobStatus.LEARNING
    assert "s3" not in minimal.quality, "Minimal: S3 не должен исполняться"
    assert not (minimal.job_dir / "grammar_notes.json").is_file()
    balanced = s3_env.run("t7b_bal", "s3_en.epub", lang="en", learning_level="Balanced")
    assert balanced.error is None
    assert "s1" in balanced.quality, "Balanced: S1 исполняется"
    assert "s3" not in balanced.quality, "Balanced: S3 не входит в уровень"
    assert not (balanced.job_dir / "grammar_notes.json").is_file()
    intensive = s3_env.run("t7b_int", "s3_en.epub", lang="en", learning_level="Intensive")
    assert intensive.error is None
    assert "s3" in intensive.quality, "Intensive: S3 исполняется"
    assert (intensive.job_dir / "grammar_notes.json").is_file()


# ------------------------------------------------------------------ T-S3-8


def test_s3_8_de_nebensatz(s3_env: S1Env) -> None:
    # T-S3-8: de фикстура с придаточным (dass + глагол на конце) — явление
    # обнаружено детерминированно и объяснено заметкой.
    run = s3_env.run("t7_de", "s3_de.epub", lang="de", learning_level="Learning")
    assert run.error is None
    notes = [
        n for n in _grammar_report(run)["notes"]
        if n["phenomenon_id"] == "DE-NEBENSATZ-VERBLETZT"
    ]
    assert notes, "DE-NEBENSATZ-VERBLETZT не обнаружен и не объяснён"
    for note in notes:
        assert note["explanation"], "пустое пояснение"
        assert contains_form(run.seg_texts[note["segment_id"]], note["surface"]), (
            f"цитата {note['surface']!r} вне сегмента {note['segment_id']}"
        )


# ------------------------------------------------------------------ T-S3-9


def test_s3_9_it_congiuntivo(s3_env: S1Env) -> None:
    # T-S3-9: it фикстура — конъюнктив (Spero che venga) обнаружен.
    run = s3_env.run("t7_it", "s3_it.epub", lang="it", learning_level="Learning")
    assert run.error is None
    notes = [
        n for n in _grammar_report(run)["notes"] if n["phenomenon_id"] == "IT-CONGIUNTIVO"
    ]
    assert notes, "IT-CONGIUNTIVO не обнаружен"
    assert all(n["explanation"] for n in notes)


def test_s3_9_fr_subjonctif(s3_env: S1Env) -> None:
    # T-S3-9: fr фикстура — субъюнктив (que tu viennes) обнаружен.
    run = s3_env.run("t7_fr", "s3_fr.epub", lang="fr", learning_level="Learning")
    assert run.error is None
    notes = [
        n for n in _grammar_report(run)["notes"] if n["phenomenon_id"] == "FR-SUBJONCTIF"
    ]
    assert notes, "FR-SUBJONCTIF не обнаружен"
    assert all(n["explanation"] for n in notes)


# ------------------------------------------------------------------ T-S3-10


def test_s3_10_too_long_rejected_not_truncated(s3_env: S1Env) -> None:
    # T-S3-10 (S3.1): пояснение длиннее лимита ОТКЛОНЯЕТСЯ с причиной, а не
    # усекается молча: заметка не выпущена ни в каком виде.
    run = s3_env.run(
        "t10", "s3_en.epub", lang="en", learning_level="Learning",
        scenario="grammar_note_too_long",
    )
    assert run.error is None
    report = _grammar_report(run)
    max_chars = s3_env.settings_of("main").limits.grammar_note_max_chars
    bad = [r for r in report["rejected"] if any("лимит" in reason for reason in r["reasons"])]
    assert bad, "заметка выше лимита отклонена с причиной"
    bad_surfaces = {r["surface"] for r in bad}
    assert not (bad_surfaces & {n["surface"] for n in report["notes"]}), (
        "отклонённая (слишком длинная) заметка выпущена"
    )
    for note in report["notes"]:
        assert len(note["explanation"]) <= max_chars, "превышение лимита среди выпущенных"
