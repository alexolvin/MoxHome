# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""S3: доказательства CONTROL (verbatim).

Запуск: python tests/s3_evidence.py
Захваты: docs/CONTROL/evidence/S3/s3-1…s3-7 (verbatim).

Сценарии (mock-сервер D3, детерминированный; реальные spaCy-модели):
  s3-1  id явления вне таксономии — 0 принятых, отклонение с причиной (S3-1)
  s3-2  «подброшенная» заметка (surface вне кандидатов) + цитата вне
        сегмента (unit) — отклонены (S3-2)
  s3-3  ≤K заметок на главу; не отобранные — записаны (S3-3)
  s3-4  явление — одно развёрнутое объяснение, остальные — ссылки (S3-4)
  s3-5  язык каждого выпущенного пояснения = целевой (ru) (S3-5)
  s3-6  покрытие golden (20/язык) на ПРЕДОТБОРЕ ≥ 75% (S3-6)
  s3-7  5 исходных языков сквозным прогоном до LEARNING, метрики; гейт
        уровней (Minimal/Balanced — S3 нет) (S3-7)

Реальные LLM-прогоны не проводились (BLOCKER-1): LLM-объяснение — mock
(детерминированный эхо кандидатов + фиксированное русское пояснение);
детерминированный предотбор (морф-шаблоны таксономии), валидация и метрики —
реальные механизмы.
"""

from __future__ import annotations

import sys
import tempfile
import time
from importlib.metadata import version as pkg_version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "probe"))

from mock_llm_server import _GRAMMAR_EXPLANATION, MockLLMServer  # noqa: E402
from test_l2_glossary import _make_storage, _synth_book  # noqa: E402
from test_s1_vocabulary import S1Env, S1Run  # noqa: E402
from test_s3_grammar import (  # noqa: E402
    _S3_DE_PARAS,
    _S3_EN_PARAS,
    _S3_FR_PARAS,
    _S3_IT_PARAS,
    _S3_NL_PARAS,
    _grammar_golden,
    _grammar_report,
)

from app.languages.detect import detect_text, normalize_code  # noqa: E402
from app.languages.frequency import normalize_text  # noqa: E402
from app.languages.phenomena import PhenomenaTaxonomy, load_phenomena  # noqa: E402
from app.languages.registry import load_registry  # noqa: E402
from app.learning.grammar import check_grammar_notes, reference_explanation  # noqa: E402
from app.learning.mwe import MweCandidate, MwePart  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "S3"
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
VERSIONS = f"python={sys.version.split()[0]} spacy={pkg_version('spacy')}"

_FIXTURE_LANGS = (
    ("en", "s3_en.epub"),
    ("de", "s3_de.epub"),
    ("fr", "s3_fr.epub"),
    ("it", "s3_it.epub"),
    ("nl", "s3_nl.epub"),
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


# ------------------------------------------------------------------ S3-1…S3-7


def _ev_s3_1(run: S1Run) -> None:
    rep = _grammar_report(run)
    bad = [
        r for r in rep["rejected"] if any("таксономии" in reason for reason in r["reasons"])
    ]
    fake = [n for n in rep["notes"] if n["phenomenon_id"] == "ZZ-S3-FAKE"]
    rows = "".join(
        f"  surface={r['surface']!r} id={r['phenomenon_id']!r} reasons={r['reasons']!r}\n"
        for r in bad
    )
    verdict = "PASS" if (bad and not fake) else "FAIL"
    content = (
        _header()
        + "s3_en.epub, сценарий grammar_bad_id (mock: у первого элемента батча "
        "phenomenon_id = ZZ-S3-FAKE — вне таксономии en)\n\n"
        + f"кандидатов отобрано: {rep['candidates_total']}\n"
        + f"заметок выпущено: {rep['notes_issued']}; с id ZZ-S3-FAKE: {len(fake)}\n\n"
        + "отклонения с причиной «вне таксономии» (verbatim):\n" + rows
        + f"\nS3-1: {verdict} — 0 принятых с id вне таксономии\n"
    )
    evidence("s3-1-bad-id-rejected.txt", content)


def _ev_s3_2(run: S1Run, env: S1Env) -> None:
    rep = _grammar_report(run)
    ghost = [r for r in rep["rejected"] if r["surface"] == "Qwzxw"]
    ghost_ok = bool(ghost) and any("кандидатов" in reason for reason in ghost[0]["reasons"])
    ghost_not_issued = all(n["surface"] != "Qwzxw" for n in rep["notes"])
    rows = "".join(
        f"  surface={r['surface']!r} id={r['phenomenon_id']!r} reasons={r['reasons']!r}\n"
        for r in ghost
    )
    # unit: кандидат, чья поверхность НЕ в тексте его сегмента
    settings = env.settings_of("main")
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
    _validated, rejected = check_grammar_notes(
        items=[item],
        candidates={_normalize("was built"): candidate},
        taxonomy=taxonomy,
        seg_texts={"seg-0": "The road was constructed in 1900."},
        normalize=_normalize,
        max_chars=settings.limits.grammar_note_max_chars,
        target_language="ru",
        seed=settings.language_detection.seed,
    )
    unit_ok = bool(rejected) and any(
        "вне сегмента" in reason for reason in rejected[0]["reasons"]
    )
    rows2 = "".join(f"  {r['surface']!r} reasons={r['reasons']!r}\n" for r in rejected)
    verdict = "PASS" if (ghost_ok and ghost_not_issued and unit_ok) else "FAIL"
    content = (
        _header()
        + "s3_en.epub, сценарий grammar_bad_quote (mock: у первого элемента батча "
        "surface = «Qwzxw» — не среди детерминированных кандидатов):\n" + rows
        + f"выпущенных заметок с surface «Qwzxw»: "
        f"{sum(1 for n in rep['notes'] if n['surface'] == 'Qwzxw')}\n\n"
        + "unit-проверка (check_grammar_notes): кандидат «was built» для "
        "сегмента «The road was constructed in 1900.» (фразы в сегменте нет):\n"
        + rows2
        + f"\nS3-2: {verdict} — подброшенная заметка и цитата вне сегмента "
        "отклонены\n"
    )
    evidence("s3-2-injected-rejected.txt", content)


def _ev_s3_3(run: S1Run, env: S1Env) -> None:
    rep = _grammar_report(run)
    max_k = env.settings_of("main").limits.grammar_max_notes_per_chapter
    per_chapter = rep["per_chapter"]
    under_cap = all(count <= max_k for count in per_chapter.values())
    accounting = rep["notes_not_selected"] == rep["candidates_total"] - max_k
    rows = "".join(
        f"  {c['phenomenon_id']} {c['surface']!r} occ={c['occurrences']}\n"
        for c in rep["candidates"]
    )
    rows_ns = "".join(
        f"  {n['surface']!r} ({n['phenomenon_id']})\n" for n in rep["not_selected"]
    )
    verdict = "PASS" if (
        rep["max_notes_per_chapter"] == max_k
        and under_cap
        and accounting
        and rep["per_chapter"]["ch1"] == rep["notes_issued"]
    ) else "FAIL"
    content = (
        _header()
        + f"s3_en.epub (Learning, mock ok): кандидатов {rep['candidates_total']} > "
        f"лимит главы K={max_k}\n\n"
        + "отобранные кандидаты (отчёт, verbatim):\n" + rows
        + f"\nзаметок выпущено: {rep['notes_issued']}; не отобранных: "
        f"{rep['notes_not_selected']} (записаны, verbatim):\n" + rows_ns
        + "\nзаметок по главам (лимит "
        + str(max_k)
        + "):\n"
        + "".join(f"  {ch}: {count}\n" for ch, count in sorted(per_chapter.items()))
        + f"\nS3-3: {verdict} — ≤K заметок на главу; не отобранные — записаны\n"
    )
    evidence("s3-3-per-chapter-cap.txt", content)


def _ev_s3_4(run: S1Run) -> None:
    rep = _grammar_report(run)
    detailed = [n for n in rep["notes"] if n["note_type"] == "detailed"]
    reference = [n for n in rep["notes"] if n["note_type"] == "reference"]
    pids = [n["phenomenon_id"] for n in detailed]
    unique_pids = len(pids) == len(set(pids))
    first_by_pid = {n["phenomenon_id"]: n for n in detailed}
    refs_ok = bool(reference) and all(
        n["reference_to"] == first_by_pid[n["phenomenon_id"]]["segment_id"]
        and n["explanation"] == reference_explanation()
        and n["model"] == ""
        for n in reference
    )
    rows_d = "".join(
        f"  {n['phenomenon_id']} {n['surface']!r} seg={n['segment_id']} "
        f"len={len(n['explanation'])}\n"
        for n in detailed
    )
    rows_r = "".join(
        f"  {n['phenomenon_id']} {n['surface']!r} → seg={n['reference_to']}: "
        f"{n['explanation']!r} (model={n['model']!r})\n"
        for n in reference
    )
    verdict = "PASS" if (unique_pids and refs_ok) else "FAIL"
    content = (
        _header()
        + "s3_en.epub (Learning, mock ok): явление объясняется ОДИН раз "
        f"развёрнуто; развёрнутых: {rep['detailed_notes']}, ссылок: "
        f"{rep['reference_notes']}\n\n"
        + "развёрнутые заметки (verbatim):\n" + rows_d
        + "\nзаметки-ссылки (verbatim; LLM не участвует — model пусто):\n"
        + rows_r
        + f"\nS3-4: {verdict} — повторных развёрнутых объяснений одного "
        "явления: 0; остальные — ссылки на первую заметку\n"
    )
    evidence("s3-4-explain-once.txt", content)


def _ev_s3_5(run: S1Run, env: S1Env) -> None:
    rep = _grammar_report(run)
    seed = env.settings_of("main").language_detection.seed
    rows: list[str] = []
    ru_count = 0
    for note in rep["notes"]:
        detected = detect_text(note["explanation"], seed)
        code = normalize_code(detected.language)
        if code == "ru":
            ru_count += 1
        conf = f"{detected.confidence:.2f}" if detected.confidence is not None else "—"
        rows.append(
            f"  {note['surface']!r} ({note['note_type']}): {code} conf={conf}\n"
        )
    verdict = "PASS" if (rep["notes"] and ru_count == len(rows)) else "FAIL"
    content = (
        _header()
        + "s3_en.epub: язык каждого выпущенного пояснения "
        f"(langdetect, seed={seed}; целевой — ru):\n\n" + "".join(rows)
        + f"ru/всего: {ru_count}/{len(rows)}\n\n"
        + f"S3-5: {verdict} — язык каждого пояснения = целевой\n"
    )
    evidence("s3-5-language.txt", content)


def _ev_s3_6(env: S1Env) -> None:
    rows: list[str] = []
    ok = True
    for code in ("de", "nl", "en", "fr", "it"):
        expected, matched, produced = _grammar_golden(env, code)
        recall = matched / expected
        precision = matched / produced if produced else 0.0
        ok = ok and recall >= 0.75
        rows.append(
            f"  {code}: golden={expected} matched={matched} produced={produced} "
            f"recall={recall:.3f} (порог 0.75); precision (справочно)="
            f"{precision:.3f}\n"
        )
    verdict = "PASS" if ok else "FAIL"
    content = (
        _header()
        + "покрытие (S3-6): детерминированный предотбор по grammar-golden "
        "наборам (config/languages/*/grammar_golden.yaml, 20 "
        "предложений/язык), сопоставление пар (предложение, id явления). "
        "Гейтится RECALL (precision — не гейтится: дополнительные кандидаты "
        "допустимы, например EN-PERFECT ⊂ modal-perfect/cond-3):\n\n"
        + "".join(rows)
        + f"\nS3-6: {verdict} — recall ≥ 0.75 на всех 5 языках\n"
    )
    evidence("s3-6-golden-recall.txt", content)


def _ev_s3_7(lang_runs: list[S1Run], minimal: S1Run, balanced: S1Run) -> None:
    rows: list[str] = []
    ok = 0
    for run in lang_runs:
        _grammar_report(run)  # ассерт: отчёт S3 существует
        q = run.quality["s3"]
        good = run.error is None and q["enabled"] is True
        ok += 1 if good else 0
        rows.append(
            f"  {run.name:14s} book_words={q['book_words']:<6d} "
            f"candidates={q['candidates']:<4d} issued={q['issued']:<3d} "
            f"detailed={q['detailed']:<3d} reference={q['reference']:<3d} "
            f"not_selected={q['not_selected']:<4d} "
            f"status={run.job.status.value}\n"
        )
    min_ok = (
        minimal.error is None
        and "s3" not in minimal.quality
        and not (minimal.job_dir / "grammar_notes.json").is_file()
    )
    bal_ok = (
        balanced.error is None
        and "s1" in balanced.quality
        and "s3" not in balanced.quality
        and not (balanced.job_dir / "grammar_notes.json").is_file()
    )
    verdict = "PASS" if (ok == 5 and min_ok and bal_ok) else "FAIL"
    content = (
        _header()
        + "5 исходных языков → ru (learning_level=Learning, learning_cefr=A1, "
        "mock ok):\n\n" + "".join(rows)
        + f"\nдошли до LEARNING с метриками S3: {ok}/5\n\n"
        + "гейт уровней (таблица ТЗ_03): S3 — только Learning/Intensive:\n"
        + f"  Minimal: s3 в quality={'s3' in minimal.quality}; "
        f"grammar_notes.json={(minimal.job_dir / 'grammar_notes.json').is_file()}\n"
        + f"  Balanced: s1 в quality={'s1' in balanced.quality}; "
        f"s3 в quality={'s3' in balanced.quality}; "
        f"grammar_notes.json={(balanced.job_dir / 'grammar_notes.json').is_file()}\n\n"
        + f"S3-7: {verdict} — 5/5 исходных языков + гейт уровней\n"
    )
    evidence("s3-7-five-languages.txt", content)


# ------------------------------------------------------------------ main


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="s3_evidence_"))
    books_dir = work / "books"
    books_dir.mkdir()
    books: dict[str, Path] = {}
    for name, title, lang, uid, paras in (
        ("s3_en.epub", "S3 Probe EN", "en", "s3-ev-en-0001", _S3_EN_PARAS),
        ("s3_de.epub", "S3 Probe DE", "de", "s3-ev-de-0002", _S3_DE_PARAS),
        ("s3_it.epub", "S3 Probe IT", "it", "s3-ev-it-0003", _S3_IT_PARAS),
        ("s3_fr.epub", "S3 Probe FR", "fr", "s3-ev-fr-0004", _S3_FR_PARAS),
        ("s3_nl.epub", "S3 Probe NL", "nl", "s3-ev-nl-0005", _S3_NL_PARAS),
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
        print("S3: сквозные прогоны …")
        bad = env.run(
            "ev_bad", "s3_en.epub", lang="en",
            learning_level="Learning", scenario="grammar_bad_id",
        )
        quote = env.run(
            "ev_quote", "s3_en.epub", lang="en",
            learning_level="Learning", scenario="grammar_bad_quote",
        )
        en = env.run("ev_en", "s3_en.epub", lang="en", learning_level="Learning")
        minimal = env.run("ev_min", "s3_en.epub", lang="en", learning_level="Minimal")
        balanced = env.run("ev_bal", "s3_en.epub", lang="en", learning_level="Balanced")
        lang_runs = [
            env.run(f"ev_lang_{lang}", book, lang=lang, learning_level="Learning")
            for lang, book in _FIXTURE_LANGS
        ]

        _ev_s3_1(bad)
        _ev_s3_2(quote, env)
        _ev_s3_3(en, env)
        _ev_s3_4(en)
        _ev_s3_5(en, env)
        _ev_s3_6(env)
        _ev_s3_7(lang_runs, minimal, balanced)
    finally:
        env.close()
        server.stop()
    print("s3 evidence done")


if __name__ == "__main__":
    main()
