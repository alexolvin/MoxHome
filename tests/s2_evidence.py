# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""S2: доказательства CONTROL (verbatim).

Запуск: python tests/s2_evidence.py
Захваты: docs/CONTROL/evidence/S2/s2-1…s2-6 (verbatim).

Сценарии (mock-сервер D3, детерминированный; реальные spaCy-модели):
  s2-1  id типа вне таксономии — 0 принятых, отклонение с причиной (S2-1)
  s2-2  de/nl разорванные: полнота на golden (30/язык) ≥ 85%; 0 записей
        без второй части; unit_lemma — справочно (конвенция L1-11) (S2-2)
  s2-3  части каждой выпущенной записи — в сегменте; синтетический
        кандидат с частью вне сегмента отклонён (S2-3)
  s2-4  точность на golden ≥ 80% для 5 языков (S2-4)
  s2-5  воспроизводимость предотбора: 2 прогона → идентичные кандидаты
        (S2-5)
  s2-6  5 исходных языков сквозным прогоном до LEARNING, метрики (S2-6)

Реальные LLM-прогоны не проводились (BLOCKER-1): LLM-объяснение — mock
(детерминированный эхо + rot3); детерминированный предотбор (шаблоны
таксономии + повторяемость), валидация и метрики — реальные механизмы.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "probe"))

from mock_llm_server import MockLLMServer  # noqa: E402
from test_l2_glossary import _make_storage, _synth_book  # noqa: E402
from test_s1_vocabulary import S1Env, S1Run  # noqa: E402
from test_s2_mwe import (  # noqa: E402
    _S2_DE_PARAS,
    _S2_EN_PARAS,
    _S2_NL_PARAS,
    _engine_golden,
    _golden_book,
)

from app.glossary.consistency import contains_form  # noqa: E402
from app.languages.frequency import normalize_text  # noqa: E402
from app.languages.nlp import load_nlp  # noqa: E402
from app.languages.phenomena import PhenomenaTaxonomy, load_phenomena  # noqa: E402
from app.languages.registry import load_registry  # noqa: E402
from app.learning.mwe import (  # noqa: E402
    MweCandidate,
    MwePart,
    check_mwe_records,
    golden_unit_accuracy,
    load_mwe_golden,
    select_mwe_candidates,
)

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "S2"
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
VERSIONS = f"python={sys.version.split()[0]} spacy={pkg_version('spacy')}"

_FIXTURE_LANGS = (
    ("en", "complex.epub"),
    ("de", "lang_de.epub"),
    ("fr", "lang_fr.epub"),
    ("it", "lang_it.epub"),
    ("nl", "lang_nl.epub"),
)


def evidence(name: str, content: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print(f"  [evidence] {path.relative_to(ROOT)}")


def _header() -> str:
    return f"дата: {STAMP}\nверсии: {VERSIONS}\n\n"


def _mwe_report(run: S1Run) -> dict[str, Any]:
    path = run.job_dir / "mwe_records.json"
    assert path.is_file(), f"нет mwe_records.json: {run.error}"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


# ------------------------------------------------------------------ S2-1…S2-6


def _ev_s2_1(run: S1Run) -> None:
    rep = _mwe_report(run)
    bad = [
        r for r in rep["rejected"] if any("таксономии" in reason for reason in r["reasons"])
    ]
    fake_accepted = [r for r in rep["records"] if r["phenomenon_id"] == "ZZ-S2-FAKE"]
    rows = "".join(
        f"  surface={r['surface']!r} id={r['phenomenon_id']!r} reasons={r['reasons']!r}\n"
        for r in bad
    )
    verdict = "PASS" if (bad and not fake_accepted) else "FAIL"
    content = (
        _header()
        + "s2_en.epub, сценарий mwe_bad_id (mock: у первой записи батча "
        "phenomenon_id = ZZ-S2-FAKE — вне таксономии en)\n\n"
        + f"кандидатов отобрано: {rep['candidates_total']}\n"
        + f"принято записей: {rep['records_issued']}; с id ZZ-S2-FAKE: "
        f"{len(fake_accepted)}\n\n"
        + "отклонения с причиной «вне таксономии» (verbatim):\n" + rows
        + f"\nS2-1: {verdict} — 0 принятых с id вне таксономии\n"
    )
    evidence("s2-1-bad-id-rejected.txt", content)


def _ev_s2_2(env: S1Env, de_run: S1Run, nl_run: S1Run) -> None:
    rows: list[str] = []
    ok = True
    for code in ("de", "nl"):
        expected, matched, produced, candidates = _engine_golden(env, code)
        checked, correct = golden_unit_accuracy(
            candidates=candidates,
            golden=load_mwe_golden(env.settings_of("main").languages_root / code),
        )
        recall = matched / expected
        ok = ok and recall >= 0.85
        separable = [c for c in candidates if c.unit_lemma is not None]
        both_parts = sum(1 for c in separable if len(c.parts) == 2)
        rows.append(
            f"  {code}: golden={expected} matched={matched} produced={produced} "
            f"recall={recall:.3f} (порог 0.85)\n"
            f"         отделяемых кандидатов={len(separable)}; с обеими частями: "
            f"{both_parts}/{len(separable)}\n"
            f"         unit_lemma (справочно, конвенция L1-11): {correct}/{checked}\n"
        )
    end_rows = ""
    for run in (de_run, nl_run):
        rep = _mwe_report(run)
        separable_records = [r for r in rep["records"] if r["unit_lemma"]]
        both = sum(1 for r in separable_records if len(r["parts"]) >= 2)
        ok = ok and bool(separable_records) and both == len(separable_records)
        end_rows += (
            f"  {rep['source_language']}: записей={rep['records_issued']} "
            f"разорванных={len(separable_records)}; без второй части: "
            f"{len(separable_records) - both}\n"
        )
    verdict = "PASS" if ok else "FAIL"
    content = (
        _header()
        + "полнота (S2-2): детерминированный предотбор по golden-наборам "
        "(config/languages/*/mwe_golden.yaml, 30 предложений/язык), "
        "сопоставление пар (предложение, id типа):\n\n"
        + "".join(rows)
        + "\nвыпущенные записи сквозным прогоном (learning_level=Learning, "
        "mock ok) — разорванные конструкции:\n" + end_rows
        + f"\nS2-2: {verdict} — recall ≥ 0.85 на обоих языках; 0 записей "
        "без второй части\n"
    )
    evidence("s2-2-separable-recall.txt", content)


def _ev_s2_3(run: S1Run, env: S1Env) -> None:
    rep = _mwe_report(run)
    rows: list[str] = []
    ok = True
    for record in rep["records"]:
        seg_text = run.seg_texts[record["segment_id"]]
        for part, _index in record["parts"]:
            in_seg = contains_form(seg_text, part)
            ok = ok and in_seg
            rows.append(
                f"  {record['surface']!r} часть={part!r} seg={record['segment_id']} "
                f"в_сегменте={in_seg}\n"
            )
    settings = env.settings_of("main")
    pack = load_registry(settings.languages_root)["en"]
    taxonomy = PhenomenaTaxonomy(load_phenomena(settings.languages_root / "en"))
    seg_text = "He drinks strong coffee every day."
    item = {
        "surface": "strong coffee",
        "phenomenon_id": "EN-COLLOCATION",
        "canonical": "strong coffee",
        "meaning": "м",
        "translation": "т",
    }
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

    def _norm(text: str) -> str:
        return normalize_text(text, pack.normalization)

    _validated, rejected = check_mwe_records(
        items=[item],
        candidates={_norm("strong coffee"): fake},
        taxonomy=taxonomy,
        seg_texts={"seg-0": seg_text},
        normalize=_norm,
    )
    unit_rejected = bool(rejected)
    rows2 = "".join(f"  {r['surface']!r} reasons={r['reasons']!r}\n" for r in rejected)
    verdict = "PASS" if (ok and unit_rejected) else "FAIL"
    content = (
        _header()
        + "s2_en.epub (Learning, mock ok) — все части выпущенных записей "
        "verbatim:\n"
        + "".join(rows)
        + "\nunit-проверка (check_mwe_records): синтетический кандидат с "
        "частью «ghostword» вне сегмента «" + seg_text + "»:\n"
        + rows2
        + f"\nS2-3: {verdict} — 0 частей вне сегмента; подброшенная часть "
        "отклонена\n"
    )
    evidence("s2-3-parts-in-segment.txt", content)


def _ev_s2_4(env: S1Env) -> None:
    rows: list[str] = []
    ok = True
    for code in ("de", "nl", "en", "fr", "it"):
        expected, matched, produced, _candidates = _engine_golden(env, code)
        precision = matched / produced if produced else 0.0
        ok = ok and precision >= 0.80
        rows.append(
            f"  {code}: golden={expected} matched={matched} produced={produced} "
            f"precision={precision:.3f} (порог 0.80)\n"
        )
    verdict = "PASS" if ok else "FAIL"
    content = (
        _header()
        + "точность (S2-4): доля отобранных пар (предложение, id), которые "
        "ожидались golden, для 5 языков:\n\n" + "".join(rows)
        + f"\nS2-4: {verdict} — precision ≥ 0.80 для всех 5 языков\n"
    )
    evidence("s2-4-precision-golden.txt", content)


def _ev_s2_5(env: S1Env, first: S1Run, second: S1Run) -> None:
    settings = env.settings_of("main")
    pack = load_registry(settings.languages_root)["de"]
    nlp = load_nlp(pack)
    phenomena = load_phenomena(settings.languages_root / "de")
    golden = load_mwe_golden(settings.languages_root / "de")
    book, pairs = _golden_book("de", tuple(e.text for e in golden))
    c1 = select_mwe_candidates(
        book=book, pairs=pairs, pack=pack, nlp=nlp, phenomena=phenomena
    )
    c2 = select_mwe_candidates(
        book=book, pairs=pairs, pack=pack, nlp=nlp, phenomena=phenomena
    )
    engine_same = c1 == c2
    end_same = _mwe_report(first)["candidates"] == _mwe_report(second)["candidates"]
    rows = "".join(
        f"  {c.phenomenon_id} {c.surface!r} occ={c.occurrences} "
        f"segs={c.segment_ids}\n"
        for c in c1
    )
    verdict = "PASS" if (engine_same and end_same) else "FAIL"
    content = (
        _header()
        + "детерминированный предотбор de golden (30 предложений), два "
        "независимых прогона:\n\n"
        + f"кандидатов прогон 1 = {len(c1)}, прогон 2 = {len(c2)}; "
        f"списки идентичны: {engine_same}\n\n"
        + "кандидаты прогона 1 (verbatim):\n" + rows
        + "\nсквозные прогоны s2_en.epub (два задания):\n"
        + f"  candidates запуска 1: {len(_mwe_report(first)['candidates'])}\n"
        + f"  candidates запуска 2: {len(_mwe_report(second)['candidates'])}\n"
        + f"  списки идентичны: {end_same}\n\n"
        + f"S2-5: {verdict} — воспроизводимость 100% (движок и сквозной)\n"
    )
    evidence("s2-5-reproducibility.txt", content)


def _ev_s2_6(lang_runs: list[S1Run]) -> None:
    rows: list[str] = []
    ok = 0
    for run in lang_runs:
        _mwe_report(run)  # ассерт: отчёт S2 существует
        q = run.quality["s2"]
        good = run.error is None and q["enabled"] is True
        ok += 1 if good else 0
        rows.append(
            f"  {run.name:14s} book_words={q['book_words']:<6d} "
            f"candidates={q['candidates']:<4d} issued={q['issued']:<3d} "
            f"rejected={q['rejected']:<3d} separable={q['separable']:<3d} "
            f"status={run.job.status.value}\n"
        )
    verdict = "PASS" if ok == 5 else "FAIL"
    content = (
        _header()
        + "5 исходных языков → ru (learning_level=Learning, learning_cefr=A1, "
        "mock ok):\n\n"
        + "".join(rows)
        + f"\nдошли до LEARNING с метриками S2: {ok}/5 "
        "(малые фикстуры могут дать 0 кандидатов — семантика warning, не ошибка)\n\n"
        + f"S2-6: {verdict} — 5/5 исходных языков\n"
    )
    evidence("s2-6-five-languages.txt", content)


# ------------------------------------------------------------------ main


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="s2_evidence_"))
    books_dir = work / "books"
    books_dir.mkdir()
    books: dict[str, Path] = {}
    for name, title, lang, uid, paras in (
        ("s2_en.epub", "S2 Probe EN", "en", "s2-ev-en-0001", _S2_EN_PARAS),
        ("s2_de.epub", "S2 Probe DE", "de", "s2-ev-de-0002", _S2_DE_PARAS),
        ("s2_nl.epub", "S2 Probe NL", "nl", "s2-ev-nl-0003", _S2_NL_PARAS),
    ):
        path = books_dir / name
        _synth_book(path, title, lang, uid, paras)
        books[name] = path
    server = MockLLMServer()
    server.start()
    env = S1Env(server=server, base=work, books=books)
    main_settings, main_db = _make_storage(work, "storage_main", server)
    env.add_profile("main", main_settings, main_db)
    try:
        print("S2: сквозные прогоны …")
        bad = env.run(
            "ev_bad", "s2_en.epub", lang="en",
            learning_level="Learning", scenario="mwe_bad_id",
        )
        en = env.run("ev_en", "s2_en.epub", lang="en", learning_level="Learning")
        en2 = env.run("ev_en2", "s2_en.epub", lang="en", learning_level="Learning")
        de = env.run("ev_de", "s2_de.epub", lang="de", learning_level="Learning")
        nl = env.run("ev_nl", "s2_nl.epub", lang="nl", learning_level="Learning")
        lang_runs = [
            env.run(f"ev_lang_{lang}", book, lang=lang, learning_level="Learning")
            for lang, book in _FIXTURE_LANGS
        ]

        _ev_s2_1(bad)
        _ev_s2_2(env, de, nl)
        _ev_s2_3(en, env)
        _ev_s2_4(env)
        _ev_s2_5(env, en, en2)
        _ev_s2_6(lang_runs)
    finally:
        env.close()
        server.stop()
    print("s2 evidence done")


if __name__ == "__main__":
    main()
