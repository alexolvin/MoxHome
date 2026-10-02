# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""S4: доказательства CONTROL (verbatim).

Запуск: python tests/s4_evidence.py
Захваты: docs/CONTROL/evidence/S4/s4-1…s4-9 (verbatim).

Сценарии (mock-сервер D3, детерминированный; реальные spaCy-модели):
  s4-1  глава без текстовых сегментов: summary НЕ создаётся, пропуск
        зафиксирован с причиной (S4-1)
  s4-2  gap-слово не в исходном предложении: вопрос отклонён (S4-2)
  s4-3  mc с двумя правильными / с дублем варианта: отклонён (S4-3)
  s4-4  вопрос с сущностью, отсутствующей в главе: отклонён (S4-4)
  s4-5  ответы отсутствуют в теле главы (questions/exercises.json),
        полностью в answers.json (S4-5)
  s4-6  100% vocab-упражнений из выпущенных S1-карточек; «призрачная»
        карточка отклонена (S4-6)
  s4-7  5 исходных языков + гейт уровней (S4-7)
  s4-8  глава-изображение: summary не создаётся, пропуск зафиксирован
        (T-S4-8)
  s4-9  100 mc-вопросов: правильный не систематически самый длинный,
        позиции распределены (T-S4-9)

Реальные LLM-прогоны не проводились (BLOCKER-1): LLM-суммаризация/вопросы/
упражнения — mock (детерминированная генерация по фактическим предложениям
главы); детерминированная валидация (S4-2/S4-3/S4-4/S4-6), отсечение
ответов (S4-5), гейт уровней и метрики — реальные механизмы.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import zipfile
from collections import Counter
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
from test_s4_study import (  # noqa: E402
    _S4_DE_PARAS,
    _S4_FR_PARAS,
    _S4_IT_PARAS,
    _S4_NL_PARAS,
    _synth_s4_en1_book,
    _synth_s4_en_book,
    _synth_s4_sparse_book,
)

from app.learning.study import blank_first_occurrence, split_words  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "S4"
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
VERSIONS = f"python={sys.version.split()[0]} spacy={pkg_version('spacy')}"

_FIXTURE_LANGS = (
    ("en", "s4_en1.epub"),
    ("de", "s4_de.epub"),
    ("fr", "s4_fr.epub"),
    ("it", "s4_it.epub"),
    ("nl", "s4_nl.epub"),
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


def _read_json(run: S1Run, name: str) -> dict[str, Any]:
    path = run.job_dir / name
    if not path.is_file():
        return {}
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


# ------------------------------------------------------------------ S4-1/S4-8


def _ev_s4_1(run: S1Run) -> None:
    rep = _read_json(run, "summaries.json")
    skipped = rep.get("skipped", [])
    issued = rep.get("chapters", [])
    reasons_ok = all(
        "нет выровненных текстовых сегментов" in s.get("reason", "") for s in skipped
    )
    warn_ok = any(
        "S4 summary" in w and "нет выровненных" in w
        for w in run.quality.get("warnings", [])
    )
    rows = "".join(
        f"  chapter_id={s['chapter_id']!r} reason={s['reason']!r}\n" for s in skipped
    )
    verdict = "PASS" if (
        rep.get("chapters_total") == 3
        and len(skipped) == 2
        and len(issued) == 1
        and reasons_ok
        and warn_ok
    ) else "FAIL"
    content = (
        _header()
        + "s4_sparse.epub (Intensive, mock ok): 3 главы — без блоков (ch1), "
        "только изображение (ch2), текст (ch3)\n\n"
        + f"chapters_total={rep.get('chapters_total')} "
        f"issued={rep.get('chapters_issued')} "
        f"skipped={rep.get('chapters_skipped')} "
        f"rejected={rep.get('chapters_rejected')}\n\n"
        + "зафиксированные пропуски (summaries.json «skipped», verbatim):\n"
        + rows
        + "warning в quality.json (первый подходящий, verbatim):\n  "
        + next(w for w in run.quality.get("warnings", []) if "S4 summary" in w)
        + f"\nS4-1: {verdict} — нетекстовая глава: summary не создана, "
        "пропуск зафиксирован с причиной\n"
    )
    evidence("s4-1-empty-chapter-no-summary.txt", content)


def _ev_s4_8(run: S1Run) -> None:
    rep = _read_json(run, "summaries.json")
    skipped = rep.get("skipped", [])
    issued = [c["chapter_id"] for c in rep.get("chapters", [])]
    with zipfile.ZipFile(run.job_dir / "target.epub") as archive:
        has_image = "OEBPS/image1.png" in archive.namelist()
    verdict = "PASS" if (len(skipped) == 2 and len(issued) == 1 and has_image) else "FAIL"
    rows = "".join(
        f"  chapter_id={s['chapter_id']!r} reason={s['reason']!r}\n" for s in skipped
    )
    content = (
        _header()
        + "s4_sparse.epub (Intensive): глава-изображение (OEBPS/image1.png, "
        f"сохранена в target.epub: {has_image}) — текстовых сегментов нет\n\n"
        + "пропущенные главы (verbatim):\n" + rows
        + f"суммаризированы: {issued}\n\n"
        + f"S4-8: {verdict} — глава-изображение: summary не создана, пропуск "
        "зафиксирован, книга собрана\n"
    )
    evidence("s4-8-image-chapter.txt", content)


# ------------------------------------------------------------------ S4-2


def _ev_s4_2(run: S1Run) -> None:
    rep = _read_json(run, "questions.json")
    bad = [
        r for r in rep.get("rejected", [])
        if any("отсутствует в исходном предложении (S4.2)" in reason for reason in r["reasons"])
    ]
    issued_gap = [q for q in rep.get("questions", []) if q["type"] == "gap"]
    ghost_issued = any("ZzblqAbsentWord" in str(q) for q in rep.get("questions", []))
    rows = "".join(
        f"  chapter={r['chapter_id']!r} question={r['question']!r} "
        f"reasons={r['reasons']!r}\n"
        for r in bad
    )
    verdict = "PASS" if (bad and not ghost_issued) else "FAIL"
    content = (
        _header()
        + "s4_en1.epub (Learning, сценарий s4_gap_missing_word: у первого "
        "gap-вопроса answer = «ZzblqAbsentWord» — слова нет в предложении)\n\n"
        + f"вопросов выпущено: {rep.get('questions_issued')}; "
        f"отклонено: {rep.get('questions_rejected')}\n\n"
        + "отклонения с причиной S4-2 (verbatim):\n" + rows
        + f"gap-вопросов выпущено: {len(issued_gap)}; «ZzblqAbsentWord» "
        f"среди выпущенных: {ghost_issued}\n\n"
        + f"S4-2: {verdict} — gap с чужим словом отклонён, выпущено 0 таких\n"
    )
    evidence("s4-2-gap-word-missing.txt", content)


# ------------------------------------------------------------------ S4-3


def _ev_s4_3(run_two: S1Run, run_dup: S1Run) -> None:
    rep_two = _read_json(run_two, "questions.json")
    rep_dup = _read_json(run_dup, "questions.json")
    bad_two = [
        r for r in rep_two.get("rejected", [])
        if any("число правильных вариантов" in reason for reason in r["reasons"])
    ]
    bad_dup = [
        r for r in rep_dup.get("rejected", [])
        if any("дублирующиеся варианты" in reason for reason in r["reasons"])
    ]
    rows_two = "".join(
        f"  question={r['question']!r} reasons={r['reasons']!r}\n" for r in bad_two
    )
    rows_dup = "".join(
        f"  question={r['question']!r} reasons={r['reasons']!r}\n" for r in bad_dup
    )
    verdict = "PASS" if (bad_two and bad_dup) else "FAIL"
    content = (
        _header()
        + "s4_en1.epub (Learning), два mock-сценария (у первого mc-вопроса "
        "батчи):\n\n"
        + "сценарий s4_mc_two_correct — два правильных варианта (verbatim):\n"
        + rows_two
        + "сценарий s4_mc_dup_option — дублирующиеся варианты (verbatim):\n"
        + rows_dup
        + f"\nS4-3: {verdict} — mc с числом правильных ≠ 1 / с дублем: "
        "отклонён, выпущено 0\n"
    )
    evidence("s4-3-mc-invalid.txt", content)


# ------------------------------------------------------------------ S4-4


def _ev_s4_4(run: S1Run) -> None:
    rep = _read_json(run, "questions.json")
    bad = [
        r for r in rep.get("rejected", [])
        if any("сущность отсутствует в главе (S4.4)" in reason for reason in r["reasons"])
    ]
    ghost_issued = any(
        "QwzxwGhost" in str(q.get("entities", [])) for q in rep.get("questions", [])
    )
    rows = "".join(
        f"  question={r['question']!r} reasons={r['reasons']!r}\n" for r in bad
    )
    verdict = "PASS" if (bad and not ghost_issued) else "FAIL"
    content = (
        _header()
        + "s4_en1.epub (Learning, сценарий s4_entity_ghost: у первого вопроса "
        "entities = [«QwzxwGhost»] — слова нет в главе)\n\n"
        + f"вопросов выпущено: {rep.get('questions_issued')}; "
        f"отклонено: {rep.get('questions_rejected')}\n\n"
        + "отклонения с причиной S4-4 (verbatim):\n" + rows
        + f"«QwzxwGhost» среди выпущенных сущностей: {ghost_issued}\n\n"
        + f"S4-4: {verdict} — вопрос с отсутствующей сущностью отклонён\n"
    )
    evidence("s4-4-entity-ghost.txt", content)


# ------------------------------------------------------------------ S4-5


def _ev_s4_5(run: S1Run) -> None:
    q_rep = _read_json(run, "questions.json")
    e_rep = _read_json(run, "exercises.json")
    answers = _read_json(run, "answers.json")
    questions = q_rep.get("questions", [])
    exercises = e_rep.get("exercises", [])
    answer_in_body = [q["id"] for q in questions if "answer" in q]
    correct_in_body = [
        q["id"]
        for q in questions
        for opt in q.get("options") or []
        if isinstance(opt, dict)
    ]
    e_answer_in_body = [e["id"] for e in exercises if "answer" in e]
    chapters = answers.get("chapters", {})
    answered_q = {qid for ch in chapters.values() for qid in ch.get("questions", {})}
    answered_e = {eid for ch in chapters.values() for eid in ch.get("exercises", {})}
    all_q = {q["id"] for q in questions}
    all_e = {e["id"] for e in exercises}
    # gap: ответ — слово исходного предложения, blank по конвенции.
    gap_rows: list[str] = []
    gap_ok = True
    for q in questions:
        if q["type"] != "gap":
            continue
        answer = chapters[q["chapter_id"]]["questions"][q["id"]]
        ok = (
            answer.casefold()
            in {w.casefold() for w in split_words(q["original_sentence"])}
            and q["sentence_with_blank"]
            == blank_first_occurrence(q["original_sentence"], answer)
        )
        gap_ok = gap_ok and ok
        if len(gap_rows) < 3:
            gap_rows.append(
                f"  answer={answer!r}\n  original={q['original_sentence']!r}\n"
                f"  blank={q['sentence_with_blank']!r}\n"
            )
    verdict = "PASS" if (
        not answer_in_body
        and not correct_in_body
        and not e_answer_in_body
        and all_q == answered_q
        and all_e == answered_e
        and gap_ok
    ) else "FAIL"
    content = (
        _header()
        + "s4_en.epub (Intensive, mock ok)\n\n"
        + f"вопросов выпущено: {len(questions)}; "
        f"упражнений выпущено: {len(exercises)}\n"
        + f"полей «answer» в записях questions.json: {len(answer_in_body)}\n"
        + f"полей «correct» в вариантах questions.json: {len(correct_in_body)}\n"
        + f"полей «answer» в записях exercises.json: {len(e_answer_in_body)}\n"
        + f"ответов в answers.json: questions={len(answered_q)} "
        f"exercises={len(answered_e)} (1:1 с выпущенными: "
        f"{all_q == answered_q} / {all_e == answered_e})\n\n"
        + "примеры gap (ответ ∈ исходном предложении; blank — конвенция "
        "«____» на первом вхождении, verbatim):\n" + "".join(gap_rows)
        + f"\nS4-5: {verdict} — ответов в теле главы нет; answers.json — "
        "отдельная сущность, полная\n"
    )
    evidence("s4-5-answers-separated.txt", content)


# ------------------------------------------------------------------ S4-6


def _ev_s4_6(run: S1Run, run_ghost: S1Run) -> None:
    cards = _read_json(run, "vocabulary_cards.json")
    e_rep = _read_json(run, "exercises.json")
    cards_by_chapter: dict[str, set[str]] = {}
    for card in cards.get("cards", []):
        cards_by_chapter.setdefault(str(card.get("chapter_id", "")), set()).add(
            str(card.get("lemma", ""))
        )
    vocab = [e for e in e_rep.get("exercises", []) if e["type"] == "vocab"]
    foreign = [
        e for e in vocab
        if (e.get("source_ref") or {}).get("id") not in cards_by_chapter.get(e["chapter_id"], set())
    ]
    sample = "".join(
        f"  chapter={e['chapter_id']} ref={e.get('source_ref')!r} prompt={e['prompt']!r}\n"
        for e in vocab[:5]
    )
    g_rep = _read_json(run_ghost, "exercises.json")
    g_bad = [
        r for r in g_rep.get("rejected", [])
        if any("S4.6" in reason for reason in r["reasons"])
    ]
    g_issued = any(
        (e.get("source_ref") or {}).get("id") == "ghost_lemma_x"
        for e in g_rep.get("exercises", [])
    )
    g_rows = "".join(
        f"  prompt={r['prompt']!r} reasons={r['reasons']!r}\n" for r in g_bad
    )
    verdict = "PASS" if (vocab and not foreign and g_bad and not g_issued) else "FAIL"
    content = (
        _header()
        + "s4_en.epub (Intensive, mock ok) + сценарий s4_vocab_ghost "
        "(source_ref = «ghost_lemma_x» — не среди выпущенных карточек)\n\n"
        + f"карточек S1 выпущено: {len(cards.get('cards', []))}; "
        f"vocab-упражнений: {len(vocab)}; вне выпущенных карточек: "
        f"{len(foreign)}\n"
        + f"vocab_from_cards (exercises.json): {e_rep.get('vocab_from_cards')}\n\n"
        + "примеры vocab-упражнений с source_ref (verbatim):\n" + sample
        + "\nсценарий s4_vocab_ghost — отклонения (verbatim):\n" + g_rows
        + f"«ghost_lemma_x» среди выпущенных: {g_issued}\n\n"
        + f"S4-6: {verdict} — 100% vocab-упражнений из выпущенных карточек; "
        "призрачный источник отклонён\n"
    )
    evidence("s4-6-vocab-from-cards.txt", content)


# ------------------------------------------------------------------ S4-7


def _ev_s4_7(
    lang_runs: list[S1Run], minimal: S1Run, balanced: S1Run, learning: S1Run
) -> None:
    rows: list[str] = []
    ok = 0
    for run in lang_runs:
        s4 = run.quality.get("s4", {})
        q = _read_json(run, "questions.json")
        s = _read_json(run, "summaries.json")
        e = _read_json(run, "exercises.json")
        good = (
            run.error is None
            and s4.get("summary", {}).get("enabled") is True
            and s4.get("questions", {}).get("enabled") is True
            and s4.get("exercises", {}).get("enabled") is True
            and s.get("chapters_issued", 0) >= 1
            and q.get("questions_issued", 0) >= 1
            and e.get("exercises_issued", 0) >= 1
        )
        ok += 1 if good else 0
        rows.append(
            f"  {run.name:16s} summary={s.get('chapters_issued')!s:<3} "
            f"questions={q.get('questions_issued')!s:<4} "
            f"exercises={e.get('exercises_issued')!s:<4} "
            f"status={run.job.status.value}\n"
        )
    min_ok = (
        minimal.error is None
        and "s4" not in minimal.quality
        and not (minimal.job_dir / "questions.json").is_file()
    )
    bal_ok = (
        balanced.error is None
        and "s1" in balanced.quality
        and "s4" not in balanced.quality
    )
    learn_ok = (
        learning.error is None
        and learning.quality.get("s4", {}).get("questions", {}).get("enabled") is True
        and "summary" not in learning.quality.get("s4", {})
        and "exercises" not in learning.quality.get("s4", {})
        and (learning.job_dir / "questions.json").is_file()
        and not (learning.job_dir / "summaries.json").is_file()
    )
    verdict = "PASS" if (ok == 5 and min_ok and bal_ok and learn_ok) else "FAIL"
    content = (
        _header()
        + "5 исходных языков → ru (Intensive, mock ok):\n\n" + "".join(rows)
        + f"\nвсе три подстадии S4 выпущены: {ok}/5\n\n"
        + "гейт уровней (таблица ТЗ_03):\n"
        + f"  Minimal: s4 в quality={'s4' in minimal.quality}; "
        f"questions.json={(minimal.job_dir / 'questions.json').is_file()}\n"
        + f"  Balanced: s1 в quality={'s1' in balanced.quality}; "
        f"s4 в quality={'s4' in balanced.quality}\n"
        + f"  Learning: только вопросы — {learn_ok} (summary/exercises вне "
        "уровня)\n\n"
        + f"S4-7: {verdict} — 5/5 исходных языков + гейт уровней\n"
    )
    evidence("s4-7-five-languages.txt", content)


# ------------------------------------------------------------------ S4-9


def _ev_s4_9(run: S1Run) -> None:
    rep = _read_json(run, "questions.json")
    answers = _read_json(run, "answers.json")
    mc: list[dict[str, Any]] = []
    for chapter_id, chapter in answers.get("chapters", {}).items():
        for qid, answer in chapter.get("questions", {}).items():
            record = next(q for q in rep.get("questions", []) if q["id"] == qid)
            if record["type"] == "mc" and record["chapter_id"] == chapter_id:
                mc.append({"record": record, "answer": int(answer)})
    positions = Counter(m["answer"] for m in mc)
    longest_hits = sum(
        1
        for m in mc
        if len(m["record"]["options"][m["answer"]])
        == max(len(o) for o in m["record"]["options"])
    )
    longest_share = longest_hits / len(mc) if mc else 0.0
    top_share = max(positions.values()) / len(mc) if mc else 0.0
    pos_rows = "".join(f"  позиция {i}: {positions[i]}\n" for i in sorted(positions))
    sample = "".join(
        f"  options={m['record']['options']!r} correct={m['answer']}\n"
        for m in mc[:5]
    )
    verdict = "PASS" if (
        len(mc) >= 100 and len(positions) >= 3 and top_share < 0.6 and longest_share < 0.6
    ) else "FAIL"
    content = (
        _header()
        + "s4_en.epub (Intensive, mock ok): mc-вопросы всех 20 глав\n\n"
        + f"mc-вопросов: {len(mc)}\n"
        + "распределение позиций правильного варианта:\n" + pos_rows
        + f"доля топ-позиции: {top_share:.1%}\n"
        + f"правильный = самый длинный вариант: {longest_hits}/{len(mc)} "
        f"= {longest_share:.1%}\n\n"
        + "примеры (verbatim):\n" + sample
        + f"\nS4-9: {verdict} — ≥100 mc; правильный не систематически "
        "самый длинный, позиции распределены\n"
    )
    evidence("s4-9-mc-distribution.txt", content)


# ------------------------------------------------------------------ main


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="s4_evidence_"))
    books_dir = work / "books"
    books_dir.mkdir()
    books: dict[str, Path] = {}
    _synth_s4_en_book(books_dir / "s4_en.epub")
    books["s4_en.epub"] = books_dir / "s4_en.epub"
    _synth_s4_en1_book(books_dir / "s4_en1.epub")
    books["s4_en1.epub"] = books_dir / "s4_en1.epub"
    _synth_s4_sparse_book(books_dir / "s4_sparse.epub")
    books["s4_sparse.epub"] = books_dir / "s4_sparse.epub"
    for name, title, lang, uid, paras in (
        ("s4_de.epub", "S4 Evidence DE", "de", "s4-ev-de-0004", _S4_DE_PARAS),
        ("s4_fr.epub", "S4 Evidence FR", "fr", "s4-ev-fr-0005", _S4_FR_PARAS),
        ("s4_it.epub", "S4 Evidence IT", "it", "s4-ev-it-0006", _S4_IT_PARAS),
        ("s4_nl.epub", "S4 Evidence NL", "nl", "s4-ev-nl-0007", _S4_NL_PARAS),
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
        print("S4: сквозные прогоны …")
        sparse = env.run("ev_sparse", "s4_sparse.epub", lang="en", learning_level="Intensive")
        gap = env.run(
            "ev_gap", "s4_en1.epub", lang="en", learning_level="Learning",
            scenario="s4_gap_missing_word",
        )
        mc_two = env.run(
            "ev_mc_two", "s4_en1.epub", lang="en", learning_level="Learning",
            scenario="s4_mc_two_correct",
        )
        mc_dup = env.run(
            "ev_mc_dup", "s4_en1.epub", lang="en", learning_level="Learning",
            scenario="s4_mc_dup_option",
        )
        entity = env.run(
            "ev_entity", "s4_en1.epub", lang="en", learning_level="Learning",
            scenario="s4_entity_ghost",
        )
        intensive = env.run("ev_intensive", "s4_en.epub", lang="en", learning_level="Intensive")
        ghost = env.run(
            "ev_ghost", "s4_en.epub", lang="en", learning_level="Intensive",
            scenario="s4_vocab_ghost",
        )
        minimal = env.run("ev_min", "s4_en1.epub", lang="en", learning_level="Minimal")
        balanced = env.run("ev_bal", "s4_en1.epub", lang="en", learning_level="Balanced")
        learning = env.run("ev_learn", "s4_en1.epub", lang="en", learning_level="Learning")
        lang_runs = [
            env.run(f"ev_lang_{lang}", book, lang=lang, learning_level="Intensive")
            for lang, book in _FIXTURE_LANGS
        ]

        _ev_s4_1(sparse)
        _ev_s4_8(sparse)
        _ev_s4_2(gap)
        _ev_s4_3(mc_two, mc_dup)
        _ev_s4_4(entity)
        _ev_s4_5(intensive)
        _ev_s4_6(intensive, ghost)
        _ev_s4_7(lang_runs, minimal, balanced, learning)
        _ev_s4_9(intensive)
    finally:
        env.close()
        server.stop()
    print("s4 evidence done")


if __name__ == "__main__":
    main()
