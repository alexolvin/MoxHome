"""S1: доказательства CONTROL (verbatim).

Запуск: python tests/s1_evidence.py
Захваты: docs/CONTROL/evidence/S1/s1-1…s1-7 (verbatim).

Сценарии (mock-сервер D3, детерминированный):
  s1-1  словоформа каждой выпущенной карточки — в тексте сегмента (S1-1)
  s1-2  дедупликация по лемме: 0 дублей; POS-поля по языкам: de article
        ∈ {der,die,das}, nl article ∈ {de,het} (S1-2)
  s1-3  плотность: max_cards = floor(N·W/1000); issued ≤ cap (S1-3)
  s1-4  плейсхолдер → отклонение по card_schema.json; выпущенные проходят
        схему языка 100% (S1-4)
  s1-5  воспроизводимость: два запуска → идентичные кандидаты; «призрак»
        (лемма не среди кандидатов) отклонён (S1-5)
  s1-6  примеры из книги ≥ 95%; сгенерированный — только с пометкой (S1-6)
  s1-7  5 исходных языков 5/5 до LEARNING, метрики в отчёте (S1-7)

Реальные LLM-прогоны не проводились (BLOCKER-1): LLM-заполнение — mock
(детерминированный rot3 + POS-поля по лемме); детерминированный отбор
(S1.1), валидация (S1.5) и плотность (S1-3) — реальные механизмы.
"""

from __future__ import annotations

import math
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
from test_s1_vocabulary import (  # noqa: E402
    S1Env,
    S1Run,
    _de_paras,
    _en_paras,
    _nl_paras,
)

from app.glossary.consistency import contains_form  # noqa: E402
from app.languages.cards import load_card_schema, validate_card  # noqa: E402
from app.languages.registry import load_registry  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "S1"
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


# ------------------------------------------------------------------ S1-1…S1-7


def _ev_s1_1(run: S1Run) -> None:
    cards: list[dict[str, Any]] = run.s1_report["cards"]
    rows: list[str] = []
    ok = 0
    for card in cards:
        in_seg = contains_form(run.seg_texts[card["segment_id"]], card["word_form"])
        ok += 1 if in_seg else 0
        rows.append(
            f"  {card['lemma']:<10s} word_form={card['word_form']!r:<12s} "
            f"seg={card['segment_id'][:12]}… в_сегменте={in_seg}\n"
        )
    verdict = "PASS" if ok == len(cards) else "FAIL"
    content = (
        _header()
        + "s1_en.epub (130 предложений; 24 кандидата: 18 noun / 3 verb / 3 adj)\n\n"
        + f"книга: book_words={run.s1_report['book_words']}  "
        f"cards_issued={len(cards)}\n\n"
        + "словоформа в тексте сегмента (verbatim, все выпущенные):\n"
        + "".join(rows)
        + f"\nS1-1: {verdict} — {ok}/{len(cards)} словоформ в сегментах\n"
    )
    evidence("s1-1-word-form-in-segment.txt", content)


def _ev_s1_2(en_run: S1Run, de_run: S1Run, nl_run: S1Run) -> None:
    en_cards: list[dict[str, Any]] = en_run.s1_report["cards"]
    de_nouns = [c for c in de_run.s1_report["cards"] if c["pos"] == "noun"]
    nl_nouns = [c for c in nl_run.s1_report["cards"] if c["pos"] == "noun"]
    en_lemmas = [c["lemma"] for c in en_cards]
    de_ok = sum(1 for c in de_nouns if c.get("article") in ("der", "die", "das"))
    nl_ok = sum(1 for c in nl_nouns if c.get("article") in ("de", "het"))
    dedup_ok = len(en_lemmas) == len(set(en_lemmas))
    verdict = (
        "PASS"
        if dedup_ok and de_ok == len(de_nouns) and nl_ok == len(nl_nouns)
        else "FAIL"
    )
    content = (
        _header()
        + "s1_en.epub — дедупликация по лемме на всю книгу:\n"
        + f"  issued={len(en_cards)}  уникальных лемм={len(set(en_lemmas))}  "
        f"дублей={len(en_cards) - len(set(en_lemmas))}\n\n"
        + f"s1_de.epub — существительные (issued={len(de_nouns)}), артикли "
        "(verbatim, выборка 20):\n"
        + "".join(
            f"  {c['lemma']:<12s} article={c.get('article')!r:<6s} "
            f"plural={c.get('plural')!r}\n"
            for c in de_nouns[:20]
        )
        + f"  article ∈ {{der,die,das}}: {de_ok}/{len(de_nouns)}\n\n"
        + f"s1_nl.epub — существительные (issued={len(nl_nouns)}), артикли "
        "(verbatim, выборка 20):\n"
        + "".join(
            f"  {c['lemma']:<12s} article={c.get('article')!r:<6s} "
            f"plural={c.get('plural')!r} diminutive={c.get('diminutive')!r}\n"
            for c in nl_nouns[:20]
        )
        + f"  article ∈ {{de,het}}: {nl_ok}/{len(nl_nouns)}\n\n"
        + f"S1-2: {verdict} — 0 дублей леммы; POS-поля (артикль) — "
        "100% в enum языка\n"
    )
    evidence("s1-2-dedup-pos-fields.txt", content)


def _density_ok(rep: dict[str, Any], cap: int, n: float) -> bool:
    return rep["cards_issued"] <= cap and 0 < rep["density_actual"] <= n + 1e-9


def _ev_s1_3(run: S1Run, n: float) -> None:
    rep = run.s1_report
    words = rep["book_words"]
    cap = rep["max_cards"]
    content = (
        _header()
        + "s1_en.epub — плотность (S1-3: ≤ N на 1000 слов исходного языка):\n\n"
        + f"  N (limits.vocabulary_max_cards_per_1000_words) = {n}\n"
        + f"  book_words = {words}\n"
        + f"  max_cards = floor({n} × {words} / 1000) = {cap} "
        f"(расчёт: {math.floor(n * words / 1000.0)})\n"
        + f"  candidates_total = {rep['candidates_total']}\n"
        + f"  cards_issued = {rep['cards_issued']} (≤ cap: {rep['cards_issued'] <= cap})\n"
        + f"  density_actual = {rep['density_actual']} "
        f"(≤ N: {rep['density_actual'] <= n + 1e-9})\n"
        + f"  cards_not_selected = {rep['cards_not_selected']}  "
        f"cards_rejected = {rep['cards_rejected']}  "
        f"cards_missing = {rep['cards_missing']}\n\n"
        + f"S1-3: {'PASS' if _density_ok(rep, cap, n) else 'FAIL'}\n"
    )
    evidence("s1-3-density-cap.txt", content)


def _ev_s1_4(run: S1Run, settings: Any) -> None:
    rep = run.s1_report
    registry = load_registry(settings.languages_root)
    schema = load_card_schema(settings.languages_root / "en", registry["en"])
    rejected = rep["rejected"]
    placeholder_rows = "".join(
        f"  {r['lemma']:<10s} reasons={r['reasons']!r}\n" for r in rejected
    )
    ok = 0
    for card in rep["cards"]:
        projection = {k: v for k, v in card.items() if k in schema["properties"]}
        valid, _ = validate_card(projection, schema)
        ok += 1 if valid else 0
    issued_count = len(rep["cards"])
    verdict = "PASS" if (rejected and ok == issued_count) else "FAIL"
    content = (
        _header()
        + "s1_en.epub, сценарий vocab_schema_bad (mock: translation='—' у "
        "первой карточки батча)\n\n"
        + f"отклонённые (verbatim, {len(rejected)}):\n{placeholder_rows}\n"
        + f"выпущенных = {issued_count}; проходят card_schema.json (en): {ok}\n\n"
        + f"S1-4: {verdict} — плейсхолдер отклонён; {ok}/{issued_count} "
        "выпущенных валидны по схеме языка\n"
    )
    evidence("s1-4-placeholder-schema.txt", content)


def _ev_s1_5(first: S1Run, second: S1Run, ghost: S1Run) -> None:
    same = first.s1_report["candidates"] == second.s1_report["candidates"]
    ghosts = [r for r in ghost.s1_report["rejected"] if r["lemma"] == "Ghostlemma"]
    ghost_rows = "".join(
        f"  {r['lemma']!r} reasons={r['reasons']!r}\n" for r in ghosts
    )
    verdict = "PASS" if (same and ghosts) else "FAIL"
    content = (
        _header()
        + "два независимых запуска s1_en.epub (два задания, два job-каталога):\n"
        + f"  candidates запуска 1: {len(first.s1_report['candidates'])} шт.\n"
        + f"  candidates запуска 2: {len(second.s1_report['candidates'])} шт.\n"
        + f"  списки идентичны (сравнение структур): {same}\n\n"
        + "сценарий vocab_ghost — подброшенная моделью карточка "
        "(лемма не среди кандидатов), отклонения (verbatim):\n"
        + ghost_rows
        + f"  «Ghostlemma» среди выпущенных: "
        f"{any(c['lemma'] == 'Ghostlemma' for c in ghost.s1_report['cards'])}\n\n"
        + f"S1-5: {verdict} — воспроизводимость 100%; призраки отклонены "
        "и не выпущены\n"
    )
    evidence("s1-5-determinism-ghost.txt", content)


def _ev_s1_6(run: S1Run) -> None:
    rep = run.s1_report
    cards: list[dict[str, Any]] = rep["cards"]
    generated = [c for c in cards if c["example_generated"]]
    share = rep["book_example_share"]
    gen_rows = "".join(
        f"  {c['lemma']!r}: example={c['example']!r} "
        f"example_generated={c['example_generated']}\n"
        for c in generated
    )
    book_examples = [c for c in cards if not c["example_generated"]]
    verdict = "PASS" if share >= 0.95 else "FAIL"
    content = (
        _header()
        + "s1_en.epub, сценарий vocab_generated_example (mock: у первой "
        "карточки выдуманный пример с example_generated=true)\n\n"
        + f"cards_issued = {len(cards)}  cards_rejected = {rep['cards_rejected']}\n"
        + f"example_generated = {len(generated)} (verbatim):\n{gen_rows}\n"
        + "примеры из книги (выборка 5, verbatim):\n"
        + "".join(
            f"  {c['lemma']!r}: {c['example']!r}\n" for c in book_examples[:5]
        )
        + f"\nbook_example_share = {share} (порог 0.95)\n"
        + f"cefr_out_of_range (user_cefr=A1, дистанция ≥ 2): "
        f"{rep['cefr_out_of_range']}\n\n"
        + f"S1-6: {verdict} — {share:.4f} ≥ 0.95; сгенерированный пример "
        "допущен только с пометкой\n"
    )
    evidence("s1-6-example-from-book.txt", content)


def _ev_s1_7(lang_runs: list[S1Run]) -> None:
    rows: list[str] = []
    ok = 0
    for run in lang_runs:
        rep = run.s1_report
        good = (
            run.error is None
            and rep is not None
            and run.quality["s1"]["enabled"] is True
            and run.job.status.value == "LEARNING"
        )
        ok += 1 if good else 0
        rows.append(
            f"  {run.name:12s} book_words={rep['book_words']:<6d} "
            f"candidates={rep['candidates_total']:<4d} "
            f"issued={rep['cards_issued']:<3d} "
            f"density={rep['density_actual']:<8} "
            f"status={run.job.status.value}\n"
        )
    verdict = "PASS" if ok == 5 else "FAIL"
    content = (
        _header()
        + "5 исходных языков → ru (learning_level=Balanced, learning_cefr=A1, "
        "mock ok):\n\n"
        + "".join(rows)
        + f"\nдошли до LEARNING с метриками: {ok}/5\n\n"
        + f"S1-7: {verdict} — {ok}/5 исходных языков, метрики в отчёте и "
        "quality.json\n"
    )
    evidence("s1-7-five-languages.txt", content)


# ------------------------------------------------------------------ main


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="s1_evidence_"))
    books_dir = work / "books"
    books_dir.mkdir()
    books: dict[str, Path] = {}
    for name, title, lang, uid, paras in (
        ("s1_en.epub", "S1 Probe EN", "en", "s1-ev-en-0001", _en_paras()),
        ("s1_de.epub", "S1 Probe DE", "de", "s1-ev-de-0002", _de_paras()),
        ("s1_nl.epub", "S1 Probe NL", "nl", "s1-ev-nl-0003", _nl_paras()),
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
        print("S1: сквозные прогоны …")
        en = env.run("ev_en", "s1_en.epub")
        en2 = env.run("ev_en2", "s1_en.epub")
        de = env.run("ev_de", "s1_de.epub", lang="de")
        nl = env.run("ev_nl", "s1_nl.epub", lang="nl")
        schema = env.run("ev_schema", "s1_en.epub", scenario="vocab_schema_bad")
        example = env.run(
            "ev_example", "s1_en.epub", scenario="vocab_generated_example"
        )
        ghost = env.run("ev_ghost", "s1_en.epub", scenario="vocab_ghost")
        lang_runs = [
            env.run(f"ev_lang_{lang}", book, lang=lang) for lang, book in _FIXTURE_LANGS
        ]

        _ev_s1_1(en)
        _ev_s1_2(en, de, nl)
        _ev_s1_3(en, main_settings.limits.vocabulary_max_cards_per_1000_words)
        _ev_s1_4(schema, main_settings)
        _ev_s1_5(en, en2, ghost)
        _ev_s1_6(example)
        _ev_s1_7(lang_runs)
    finally:
        env.close()
        server.stop()
    print("s1 evidence done")


if __name__ == "__main__":
    main()
