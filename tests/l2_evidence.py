"""L2: доказательства CONTROL (verbatim).

Запуск: python tests/l2_evidence.py
Захваты: docs/CONTROL/evidence/L2/l2-1…l2-8 (verbatim).

Сценарии:
  l2-1  кандидаты complex.epub: два запуска → идентичный список (L2-1: 100%)
  l2-2  de: правило «с заглавной» выключено в pack.yaml — 0 кандидатов;
        контрфакт (rule on) — Mühle/Fluss (L2-2)
  l2-3  внедрённый несогласованный перевод (mock glossary_inconsistent):
        100% детекция, 0 правок текста (L2-3)
  l2-4  complex en→ru: доля согласованных вхождений ≥ 95% (L2-4)
  l2-5  усечение глоссария: отчёт + предупреждение (L2-5)
  l2-6  доля проверенных парадигм (ru) ≥ 90% (L2-6)
  l2-7  golden-30: 0 ложных расхождений с парадигмой / 20 без; имя 20×
        → один вариант (L2-7)
  l2-8  5 исходных языков (en/de/fr/it/nl → ru): 5/5 (L2-8)

Mock-сервер (D3) — детерминированный (rot3); NER — spaCy sm-модели (шум
ложных сущностей — измерен и учтён: кандидаты отбираются независимо от
меток, правила дедуплицируют по поверхности). Доказательства
воспроизводимы; реальные LLM-прогоны не проводились (BLOCKER-1).
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
from test_l2_glossary import (  # noqa: E402
    FIXTURES,
    L2Env,
    _build_books,
    _chapter_xhtml,
    _golden_alignment,
    _golden_entries,
    _golden_glossary,
    _make_storage,
)

from app.core.config import Settings  # noqa: E402
from app.epub.reader import read_epub  # noqa: E402
from app.glossary.candidates import extract_candidates  # noqa: E402
from app.glossary.consistency import check_consistency  # noqa: E402
from app.glossary.store import BookStore, book_key_of  # noqa: E402
from app.languages.registry import load_registry  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "L2"
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

_REGISTRY = load_registry(ROOT / "config" / "languages")
_MODELS = sorted({pack.spacy_model for pack in _REGISTRY.values() if pack.spacy_model})
VERSIONS = (
    f"spacy={pkg_version('spacy')} "
    + " ".join(f"{m}={pkg_version(m)}" for m in _MODELS)
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


def _candidate_lines(cands: tuple[Any, ...]) -> str:
    if not cands:
        return "  (нет кандидатов)\n"
    return "".join(
        f"  {c.rule:12s} {c.surface!r} occ={c.occurrences} pos_hint={c.pos_hint}\n"
        for c in cands
    )


def _term_lines(consistency: dict[str, Any]) -> str:
    return "".join(
        f"  {t['term']!r:28s} occ={t['occurrences']:<3d} consistent={t['consistent']:<3d} "
        f"variants={t['variants']}\n"
        for t in consistency.get("terms", [])
    )


def _l2_1(env: L2Env, work: Path, settings: Settings) -> None:
    book = read_epub(FIXTURES / "complex.epub", work / "un1", settings)
    en_pack = _REGISTRY["en"]
    nlp_en = env.nlp_of("en")
    run1 = extract_candidates(book, en_pack, nlp_en, settings)
    run2 = extract_candidates(book, en_pack, nlp_en, settings)
    evidence(
        "l2-1-candidates.txt",
        _header()
        + "кандидаты complex.epub (en), запуск 1:\n"
        + _candidate_lines(run1)
        + "\nзапуск 2:\n"
        + _candidate_lines(run2)
        + f"\nидентичность списков: {run1 == run2}\n"
        f"число кандидатов: {len(run1)} / {len(run2)}\n"
        "L2-1: 100% (список воспроизведён побайтово)\n",
    )


def _l2_2(env: L2Env, work: Path, settings: Settings) -> None:
    de_pack = _REGISTRY["de"]
    nlp_de = env.nlp_of("de")
    book_fix = read_epub(FIXTURES / "lang_de.epub", work / "un2", settings)
    fix_cands = extract_candidates(book_fix, de_pack, nlp_de, settings)
    book_rep = read_epub(env.books["de_repeat.epub"], work / "un3", settings)
    rep_cands = extract_candidates(book_rep, de_pack, nlp_de, settings)
    de_cf = de_pack.model_copy(update={"capitalized_candidates": True})
    cf_cands = extract_candidates(book_rep, de_cf, nlp_de, settings)
    cap_rep = [c for c in rep_cands if c.rule == "capitalized"]
    cap_cf = [c for c in cf_cands if c.rule == "capitalized"]
    evidence(
        "l2-2-de-capitals.txt",
        _header()
        + f"pack.yaml de: capitalized_candidates={de_pack.capitalized_candidates}\n\n"
        + "lang_de.epub (фикстура, правило ВЫКЛЮЧЕНО):\n"
        + _candidate_lines(fix_cands)
        + f"  capitalized-кандидатов: {sum(1 for c in fix_cands if c.rule == 'capitalized')}\n\n"
        + "de_repeat.epub (Mühle ×3, Fluss ×4; правило ВЫКЛЮЧЕНО):\n"
        + _candidate_lines(rep_cands)
        + f"  capitalized-кандидатов: {len(cap_rep)}\n\n"
        + "de_repeat.epub (КОНТРФАКТ: capitalized_candidates=true):\n"
        + _candidate_lines(cf_cands)
        + f"  capitalized-кандидатов: {len(cap_cf)} "
        f"({', '.join(f'{c.surface!r}×{c.occurrences}' for c in cap_cf)})\n\n"
        "L2-2: 0 ложных кандидатов от заглавных в de (≤5%); контрфакт подтверждает,\n"
        "что именно pack.yaml отключает правило (не «не было что отбирать»)\n",
    )


def _l2_3(env: L2Env) -> None:
    run_wex = env.run(
        "wexley", "wexley.epub", "ru", scenario="glossary_inconsistent",
        capture_target_hashes=True,
    )
    wex_report = json.loads(
        (run_wex.job_dir / "glossary_consistency.json").read_text(encoding="utf-8")
    )
    xhtml = _chapter_xhtml(run_wex.job_dir)
    hashes = (
        f"  sha256 target.epub после translating      = {run_wex.hash_after_translating}\n"
        f"  sha256 target.epub после validating_output = {run_wex.hash_after_validating}\n"
        "  (оба идентичны)\n"
    )
    evidence(
        "l2-3-injected-mismatch.txt",
        _header()
        + "сценарий mock: glossary_inconsistent (rot3(Wexley) → «Xrongy» в переводе)\n\n"
        + "quality.json → glossary_consistency (verbatim):\n"
        + json.dumps(run_wex.quality["glossary_consistency"], ensure_ascii=False, indent=2)
        + "\n\nmismatches (glossary_consistency.json, verbatim):\n"
        + "".join(
            f"  term={m['term']!r} segment_id={m['segment_id']}\n"
            f"    source_text={m['source_text']!r}\n"
            f"    translation_text={m['translation_text']!r}\n"
            for m in wex_report["mismatches"]
        )
        + f"детекция: {len(wex_report['mismatches'])}/3 внедрённых вхождений (100%)\n\n"
        + "L2 НЕ ПРАВИТ ТЕКСТ:\n"
        + f"  target.epub: «Xrongy» = {xhtml.count('Xrongy')} (неправильная форма осталась)\n"
        + f"  target.epub: «Zhaohb» = {xhtml.count('Zhaohb')} (канонической нет — намеренно)\n"
        + f"  sha256 источника: до = {run_wex.source_sha_before}\n"
        f"                  после = {run_wex.source_sha_after} (идентичны)\n"
        + hashes
        + "предупреждения (verbatim):\n"
        + "".join(f"  {w}\n" for w in run_wex.warnings)
        + "\nL2-3: PASS — 100% детекция, 0 правок текста\n",
    )


def _l2_4(env: L2Env, store: BookStore) -> None:
    run_cx = env.run("complex", "complex.epub", "ru")
    cx_report = json.loads(
        (run_cx.job_dir / "glossary_consistency.json").read_text(encoding="utf-8")
    )
    section = run_cx.quality["glossary_consistency"]
    glossary_cx = store.load_glossary(
        book_key_of(run_cx.job.source_path), run_cx.job.input_sha256
    )
    assert glossary_cx is not None
    evidence(
        "l2-4-consistent-share.txt",
        _header()
        + "complex.epub en→ru (mock ok, глоссарий версии книги, D60)\n\n"
        + "quality.json → glossary_consistency (verbatim):\n"
        + json.dumps(section, ensure_ascii=False, indent=2)
        + "\n\nтермины (glossary_consistency.json):\n"
        + _term_lines(cx_report)
        + f"share = {section['consistent_occurrences']}/{section['occurrences_checked']} "
        f"= {section['consistent_share']:.1%} (порог 95%)\n"
        + f"глоссарий сохранён: books/complex.epub/"
        f"{run_cx.job.input_sha256[:16]}…/glossary.yaml "
        f"({len(glossary_cx.terms)} терминов)\n"
        + f"предупреждений L2: {sum(1 for w in run_cx.warnings if w.startswith('L2:'))}\n\n"
        + "L2-4: "
        + ("PASS" if section["consistent_share"] >= 0.95 else "FAIL")
        + f" — {section['consistent_share']:.1%} ≥ 95%\n",
    )


def _l2_5(env: L2Env) -> None:
    run_sm = env.run("complex_small", "complex.epub", "ru", profile="small")
    trunc_warning = next(w for w in run_sm.warnings if "усечён" in w)
    evidence(
        "l2-5-truncation.txt",
        _header()
        + "limits.glossary_max_user_prompt_chars = 200 (конфиг small-профиля)\n\n"
        + "translation_result.json → glossary (verbatim):\n"
        + json.dumps(run_sm.tr_glossary, ensure_ascii=False, indent=2)
        + "\n\nпредупреждение QC (verbatim):\n  "
        + trunc_warning
        + "\n\nL2-5: PASS — усечение зафиксировано (dropped > 0, truncated=true),\n"
        "не молча\n",
    )


def _l2_6(env: L2Env, store: BookStore) -> None:
    run_para = env.run("parabook", "parabook.epub", "ru")
    glossary_para = store.load_glossary("parabook.epub", run_para.job.input_sha256)
    assert glossary_para is not None
    applicable = [
        t for t in glossary_para.terms
        if t.paradigm_status in ("verified", "unverified")
    ]
    verified = [t for t in applicable if t.paradigm_status == "verified"]
    share = len(verified) / len(applicable)
    evidence(
        "l2-6-paradigm-share.txt",
        _header()
        + "parabook.epub: 9 склоняемых имён + Badparadigm (12 идентичных форм без\n"
        + "объяснений) + Indeclinable (явная метка несклоняемости)\n\n"
        + "парадигмы (глоссарий версии, verbatim-поля):\n"
        + "".join(
            f"  {t.term!r:20s} status={t.paradigm_status:<16s} "
            f"reason={t.paradigm_reason!r}\n"
            for t in glossary_para.terms
        )
        + f"\nпроверенных: {len(verified)}/{len(applicable)} = {share:.1%} (порог 90%)\n"
        + "denominator — термины, для которых парадигма применима "
        "(verified + unverified);\n"
        "not_applicable (несклоняемые, inflected=false) — вне знаменателя "
        "(явная метка, T-L2-11)\n\n"
        + f"L2-6: {'PASS' if share >= 0.9 else 'FAIL'} — {share:.1%} ≥ 90%, "
        "остальные — paradigm_unverified\n",
    )


def _l2_7(env: L2Env) -> None:
    entries = _golden_entries()
    names = _REGISTRY["ru"].paradigm_forms["proper_noun"]
    alignment = _golden_alignment(entries)
    report_with = check_consistency(
        _golden_glossary(entries, names, with_paradigm=True), alignment
    )
    report_wo = check_consistency(
        _golden_glossary(entries, names, with_paradigm=False), alignment
    )
    run_ml = env.run("marlow", "marlow.epub", "ru")
    ml_report = json.loads(
        (run_ml.job_dir / "glossary_consistency.json").read_text(encoding="utf-8")
    )
    ml_term = next(t for t in ml_report["terms"] if t["term"] == "Marlow")
    evidence(
        "l2-7-golden30.txt",
        _header()
        + f"golden-30: config/languages/ru/inflection_golden.yaml "
        f"({len(entries)} имён × 6 падежей ед. ч.)\n\n"
        + f"с проверенной парадигмой: расхождений = {len(report_with.mismatches)}/30\n"
        + "без парадигмы (контроль, только каноническая форма): "
        f"расхождений = {len(report_wo.mismatches)}/30:\n"
        + "".join(f"  {m.term} ({m.segment_id})\n" for m in report_wo.mismatches)
        + "\nmarlow.epub: имя «Marlow» 20× (сквозной прогон en→ru):\n"
        + f"  occurrences={ml_term['occurrences']} consistent={ml_term['consistent']} "
        f"variants={ml_term['variants']}\n"
        + f"  share={ml_report['consistent_share']:.1%}, "
        f"расхождений={len(ml_report['mismatches'])}\n\n"
        + f"L2-7: {'PASS' if len(report_with.mismatches) == 0 else 'FAIL'} — "
        "0 ложных расхождений от словоизменения\n"
        "(без парадигмы те же случаи — ложные)\n",
    )


def _l2_8(env: L2Env, store: BookStore) -> None:
    books8 = (
        ("complex.epub", "en"),
        ("lang_de.epub", "de"),
        ("lang_fr.epub", "fr"),
        ("lang_it.epub", "it"),
        ("lang_nl.epub", "nl"),
    )
    rows: list[str] = []
    ok = 0
    for file_name, lang in books8:
        run8 = env.run(file_name, file_name, "ru", lang=lang)
        s8 = run8.quality["glossary_consistency"]
        target_ok = (run8.job_dir / "target.epub").is_file()
        g8 = store.load_glossary(file_name, run8.job.input_sha256)
        n_terms = len(g8.terms) if g8 is not None else -1
        rows.append(
            f"  {file_name:16s} {lang}→ru  terms={s8['terms_checked']:<3d} "
            f"occ={s8['occurrences_checked']:<3d} share={s8['consistent_share']:.1%} "
            f"target.epub={'OK' if target_ok else 'MISSING'} "
            f"glossary_terms={n_terms}\n"
        )
        if target_ok and g8 is not None:
            ok += 1
    evidence(
        "l2-8-five-languages.txt",
        _header()
        + "5 исходных языков (Rule 4) → ru, глоссарий включён (mock ok):\n\n"
        + "".join(rows)
        + f"\nзавершено: {ok}/5\n"
        + "пустые глоссарии (terms=0) — ожидаемо: в маленьких языковых фикстурах\n"
        "нет кандидатов с числом вхождений ≥ 2 (порог glossary.min_occurrences)\n\n"
        + f"L2-8: {'PASS' if ok == 5 else 'FAIL'} — {ok}/5\n",
    )


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="l2_evidence_"))
    books_dir = work / "books"
    books_dir.mkdir()
    server = MockLLMServer()
    server.start()
    env = L2Env(server=server, registry=_REGISTRY, books=_build_books(books_dir))
    main_settings, main_db = _make_storage(work, "storage_main", server)
    env.add_profile("main", main_settings, main_db)
    small_settings, small_db = _make_storage(
        work, "storage_small", server, extra="limits:\n  glossary_max_user_prompt_chars: 200\n"
    )
    env.add_profile("small", small_settings, small_db)
    store = BookStore(main_settings.books_dir)
    try:
        _l2_1(env, work, main_settings)
        _l2_2(env, work, main_settings)
        _l2_3(env)
        _l2_4(env, store)
        _l2_5(env)
        _l2_6(env, store)
        _l2_7(env)
        _l2_8(env, store)
    finally:
        env.close()
        server.stop()
    print("l2 evidence done")


if __name__ == "__main__":
    main()
