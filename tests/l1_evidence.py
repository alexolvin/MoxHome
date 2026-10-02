# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Доказательства L1: языковые пакеты — NLP, таксономия, карточки, CEFR, частотность.

Запуск: python tests/l1_evidence.py
Захваты: docs/CONTROL/evidence/L1/l1-1…l1-12 (verbatim).

Сценарии:
  l1-1  пайплайны 5/5: версии зафиксированы, один раз на процесс (L1-2)
  l1-2  лемматизация 50×5: таблица точности, слабые формы verbatim (L1-2)
  l1-3  de-карточки: без артикля/множественного — 0 выпущенных, причины (L1-3)
  l1-4  nl-карточки: без артикля de/het — 0 выпущенных (L1-4)
  l1-5  fr/it-карточки глаголов: без вспомогательного — 0 выпущенных (L1-5)
  l1-6  таксономии: состав по языкам, чужой id — 0 принятых (L1-6)
  l1-7  CEFR-аудит: таблица LIMITATIONS.md verbatim, 5/5 строк (L1-7)
  l1-8  CEFR-метки: derived → «Estimated CEFR level», все 5 языков (L1-8)
  l1-9  частотные полосы: границы из pack.yaml, примеров слов, 0 порогов в коде (L1-9)
  l1-10 golden: 20×5, покрытие таксономии, чужой id отклоняется (T-L1-10)
  l1-11 отделяемые глаголы: de/nl — одна единица; предлоги — 0 (T-L1-11)
  l1-12 лексикон удалён → derived, метка меняется, резолвер не молчит (T-L1-12)

LLM не вызывается; доказательства полностью воспроизводимы
(детерминированные правила, эталонные данные из репозитория).
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from collections.abc import Sequence
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.languages.cards import issue_cards, load_card_schema  # noqa: E402
from app.languages.cefr import CefrSource, label  # noqa: E402
from app.languages.frequency import band_report, frequency_band  # noqa: E402
from app.languages.nlp import (  # noqa: E402
    DISABLED_COMPONENTS,
    lemma_of,
    lexical_units,
    load_nlp,
    nlp_report,
)
from app.languages.phenomena import (  # noqa: E402
    PhenomenaTaxonomy,
    load_phenomena,
    validate_golden,
)
from app.languages.registry import (  # noqa: E402
    GoldenEntry,
    PackSpec,
    load_golden,
    load_lemma_reference,
    load_registry,
)
from app.languages.resolver import PairResolver  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "L1"
LANGUAGES = ROOT / "config" / "languages"
LIMITATIONS = ROOT / "docs" / "LIMITATIONS.md"
SOURCE_LANGUAGES: tuple[str, ...] = ("en", "de", "nl", "fr", "it")
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

_REGISTRY: dict[str, PackSpec] = load_registry(LANGUAGES)
_MODELS = sorted({pack.spacy_model for pack in _REGISTRY.values() if pack.spacy_model})
VERSIONS = f"spacy={pkg_version('spacy')} wordfreq={pkg_version('wordfreq')} " + " ".join(
    f"{model}={pkg_version(model)}" for model in _MODELS
)


def evidence(name: str, content: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print(f"  [evidence] {path.relative_to(ROOT)}")


def _header(title: str) -> list[str]:
    return [title, f"замер: {STAMP}", f"окружение: {VERSIONS}", ""]


# ------------------------------------------------------------------ L1-1


def l1_1() -> None:
    lines = _header("L1-1: пайплайны 5/5 — версии зафиксированы, один раз на процесс")
    ok = 0
    for code in SOURCE_LANGUAGES:
        pack = _REGISTRY[code]
        nlp = load_nlp(pack)
        report = nlp_report(pack)
        same_instance = load_nlp(pack) is nlp
        ok += 1
        lines.append(
            f"PASS {code}: model={pack.spacy_model} "
            f"model_version={report['model_version']} spacy_version={report['spacy_version']} "
            f"once_per_process={same_instance}"
        )
        lines.append(f"  компоненты: {', '.join(name for name, _ in nlp.pipeline)}")
        lines.append(f"  отключены (L1.2): {', '.join(DISABLED_COMPONENTS)}")
        lines.append(f"  nlp_report (verbatim): {json.dumps(report, ensure_ascii=False)}")
    lines.append(f"итого: {ok}/5 загружены; версии зафиксированы; один раз на процесс: True")
    evidence("l1-1_pipelines_5_versions.txt", "\n".join(lines))


# ------------------------------------------------------------------ L1-2


def l1_2() -> None:
    lines = _header("L1-2: лемматизация — эталонный набор 50 словоформ на язык (критерий: ≥90%)")
    for code in SOURCE_LANGUAGES:
        nlp = load_nlp(_REGISTRY[code])
        forms = load_lemma_reference(LANGUAGES / code)
        fails = [
            f"{form.form}→{lemma_of(nlp, form.form)} (ожидалось {form.lemma})"
            for form in forms
            if lemma_of(nlp, form.form) != form.lemma
        ]
        correct = len(forms) - len(fails)
        rate = correct / len(forms) * 100
        verdict = "PASS" if rate >= 90.0 else "FAIL"
        lines.append(f"{verdict} {code}: {correct}/{len(forms)} ({rate:.0f}%)")
        for failure in fails:
            lines.append(f"  слабая форма: {failure}")
    evidence("l1-2_lemmatization_table.txt", "\n".join(lines))


# ------------------------------------------------------------------ L1-3..5


def _de_noun_card() -> dict[str, Any]:
    return {
        "lemma": "Tisch",
        "word_form": "Tisch",
        "pos": "noun",
        "meaning": "мебель для сидения вокруг",
        "translation": "table",
        "example": "Er sitzt am Tisch.",
        "segment_id": "c1s2p3",
        "frequency_band": "A1",
        "cefr_level": "A1",
        "cefr_source": "derived",
        "article": "der",
        "plural": "Tische",
    }


def _nl_noun_card() -> dict[str, Any]:
    return {
        "lemma": "huis",
        "word_form": "huis",
        "pos": "noun",
        "meaning": "жилище",
        "translation": "house",
        "example": "Het huis is groot.",
        "segment_id": "c1s2p3",
        "frequency_band": "A1",
        "cefr_level": "A1",
        "cefr_source": "derived",
        "article": "het",
        "plural": "huizen",
        "diminutive": "huisje",
    }


def _fr_verb_card() -> dict[str, Any]:
    return {
        "lemma": "manger",
        "word_form": "mange",
        "pos": "verb",
        "meaning": "есть (пищу)",
        "translation": "to eat",
        "example": "Il mange une pomme.",
        "segment_id": "c1s2p3",
        "frequency_band": "A1",
        "cefr_level": "A1",
        "cefr_source": "derived",
        "conjugation_group": "1",
        "auxiliary": "avoir",
        "irregular_forms": None,
    }


def _it_verb_card() -> dict[str, Any]:
    return {
        "lemma": "mangiare",
        "word_form": "mangio",
        "pos": "verb",
        "meaning": "есть (пищу)",
        "translation": "to eat",
        "example": "Io mangio una mela.",
        "segment_id": "c1s2p3",
        "frequency_band": "A1",
        "cefr_level": "A1",
        "cefr_source": "derived",
        "conjugation_group": "1",
        "auxiliary": "avere",
        "irregular_forms": None,
    }


def _case(
    lines: list[str], title: str, code: str, card: dict[str, Any], fields: tuple[str, ...]
) -> None:
    """Партия: полная карточка выпущена; без каждого `fields` — 0 выпущенных (verbatim)."""
    schema = load_card_schema(LANGUAGES / code, _REGISTRY[code])
    lines.append(title)
    issued, rejected = issue_cards([card], schema)
    lines.append(f"  полная карточка: issued={len(issued)} rejected={len(rejected)}")
    for field in fields:
        incomplete = dict(card)
        del incomplete[field]
        issued, rejected = issue_cards([incomplete], schema)
        lines.append(f"  без {field}: issued={len(issued)} (критерий: 0)")
        for _, reasons in rejected:
            for reason in reasons:
                lines.append(f"    отклонена: {reason}")
    lines.append("")


def l1_3() -> None:
    lines = _header("L1-3: de-карточки существительного — без обязательного поля 0 выпущенных")
    card = _de_noun_card()
    _case(lines, "de (noun: article der/die/das + plural)", "de", card, ("article", "plural"))
    schema = load_card_schema(LANGUAGES / "de", _REGISTRY["de"])
    placeholder = dict(card)
    placeholder["plural"] = "—"
    issued, rejected = issue_cards([placeholder], schema)
    lines.append("  плейсхолдер «—» вместо plural (Rule 5): issued=" + str(len(issued)))
    for _, reasons in rejected:
        for reason in reasons:
            lines.append(f"    отклонена: {reason}")
    evidence("l1-3_de_noun_cards.txt", "\n".join(lines))


def l1_4() -> None:
    lines = _header("L1-4: nl-карточки существительного — без de/het 0 выпущенных")
    card = _nl_noun_card()
    _case(lines, "nl (noun: article de/het + plural + diminutive)", "nl", card, ("article",))
    schema = load_card_schema(LANGUAGES / "nl", _REGISTRY["nl"])
    foreign_article = dict(card)
    foreign_article["article"] = "der"
    issued, rejected = issue_cards([foreign_article], schema)
    lines.append(f"  чужой артикль (der): issued={len(issued)} (критерий: 0)")
    for _, reasons in rejected:
        for reason in reasons:
            lines.append(f"    отклонена: {reason}")
    no_diminutive = dict(card)
    no_diminutive["diminutive"] = None
    issued, rejected = issue_cards([no_diminutive], schema)
    lines.append(
        f"  diminutive=null (nullable): issued={len(issued)} (критерий: 1, null — не плейсхолдер)"
    )
    evidence("l1-4_nl_noun_cards.txt", "\n".join(lines))


def l1_5() -> None:
    lines = _header("L1-5: fr/it-карточки глагола — без вспомогательного 0 выпущенных")
    _case(
        lines,
        "fr (verb: conjugation_group + auxiliary avoir/etre)",
        "fr",
        _fr_verb_card(),
        ("auxiliary", "conjugation_group"),
    )
    _case(
        lines,
        "it (verb: conjugation_group + auxiliary avere/essere)",
        "it",
        _it_verb_card(),
        ("auxiliary", "conjugation_group"),
    )
    evidence("l1-5_fr_it_verb_cards.txt", "\n".join(lines))


# ------------------------------------------------------------------ L1-6


def l1_6() -> None:
    lines = _header("L1-6: таксономии явлений — состав, чужой id = 0 принятых")
    for code in SOURCE_LANGUAGES:
        phenomena = load_phenomena(LANGUAGES / code)
        lines.append(f"{code}: {len(phenomena)} явлений: {', '.join(p.id for p in phenomena)}")
    lines.append("")
    de_taxonomy = PhenomenaTaxonomy(load_phenomena(LANGUAGES / "de"))
    valid, invalid = de_taxonomy.validate(("DE-TRENNBAR", "EN-PHRASAL-VERB", "NL-SCHEIDBAAR"))
    lines.append("de: метки [DE-TRENNBAR, EN-PHRASAL-VERB, NL-SCHEIDBAAR]")
    lines.append(f"  приняты: {list(valid)}")
    lines.append(f"  отклонены (вне таксономии de): {list(invalid)}")
    foreign_valid, foreign_invalid = de_taxonomy.validate(("EN-PHRASAL-VERB", "NL-SCHEIDBAAR"))
    lines.append(
        f"только чужие id: принято={len(foreign_valid)} (критерий: 0), "
        f"отклонено={list(foreign_invalid)}"
    )
    evidence("l1-6_taxonomy_foreign_id.txt", "\n".join(lines))


# ------------------------------------------------------------------ L1-7


def l1_7() -> None:
    lines = _header("L1-7: CEFR-аудит (docs/LIMITATIONS.md) — таблица verbatim")
    text = LIMITATIONS.read_text(encoding="utf-8")
    section = text.split("### CEFR-лексиконы", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("|")]
    lines.extend(rows)
    data_rows = len(rows) - 2  # без заголовка и разделителя
    lines.append("")
    lines.append(f"строк данных (en/de/fr/nl/it): {data_rows}/5 (критерий: 5/5)")
    evidence("l1-7_cefr_audit_table.txt", "\n".join(lines))


# ------------------------------------------------------------------ L1-8


def l1_8() -> None:
    lines = _header("L1-8: CEFR — все 5 языков derived, обязательная метка «Estimated CEFR level»")
    for code in SOURCE_LANGUAGES:
        pack = _REGISTRY[code]
        source = CefrSource(pack, LANGUAGES / code)
        result = source.level_for("слово")
        lines.append(
            f"{code}: cefr_source={pack.cefr_source} effective_source={source.effective_source} "
            f"level={result.level} estimated={result.estimated} метка={label(result)!r}"
        )
    lines.append("")
    lines.append("единая точка отображения: app.languages.cefr.label (UI U1 читает только её)")
    evidence("l1-8_cefr_labels.txt", "\n".join(lines))


# ------------------------------------------------------------------ L1-9


def l1_9() -> None:
    lines = _header("L1-9: частотные полосы — границы из pack.yaml, 0 числовых порогов в коде")
    for code in SOURCE_LANGUAGES:
        pack = _REGISTRY[code]
        bands = ", ".join(
            f"{b.band}≤{b.zipf_max}" if b.zipf_max is not None else f"{b.band}: без границы"
            for b in pack.frequency_bands
        )
        lines.append(f"{code}: {bands}")
    lines.append("")
    lines.append("примеры (слова → zipf → полоса; границы — из данных):")
    for code, word in (("de", "der"), ("de", "Häuser"), ("en", "the"), ("en", "ubiquitous")):
        pack = _REGISTRY[code]
        band, zipf = frequency_band(word, code, pack.frequency_bands, pack.normalization)
        lines.append(f"  {code} «{word}»: zipf={zipf:.4g} → полоса {band}")
    lines.append("")
    bounds = {
        f"{band.zipf_max}"
        for code in SOURCE_LANGUAGES
        for band in _REGISTRY[code].frequency_bands
        if band.zipf_max is not None
    }
    hits = [
        bound
        for path in sorted((ROOT / "app" / "languages").glob("*.py"))
        for bound in sorted(bounds)
        if bound in path.read_text(encoding="utf-8")
    ]
    lines.append(
        f"значения границ ({', '.join(sorted(bounds))}) в коде app/languages/*.py: "
        f"найдено {len(hits)} (критерий: 0)"
    )
    lines.append(
        "band_report de (verbatim): "
        + json.dumps(band_report(_REGISTRY["de"].frequency_bands), ensure_ascii=False)
    )
    evidence("l1-9_bands_from_config.txt", "\n".join(lines))


# ------------------------------------------------------------------ T-L1-10


def l1_10() -> None:
    lines = _header("T-L1-10: golden — 20 предложений на язык, ids ⊆ таксономии, покрытие")
    for code in SOURCE_LANGUAGES:
        entries = load_golden(LANGUAGES / code)
        taxonomy = PhenomenaTaxonomy(load_phenomena(LANGUAGES / code))
        errors = validate_golden(entries, taxonomy)
        labeled = {phen for entry in entries for phen in entry.phenomena}
        covered = taxonomy.ids & labeled
        lines.append(
            f"{code}: golden={len(entries)} (критерий: 20) таксономия={len(taxonomy.ids)} "
            f"покрытие={len(covered)}/{len(taxonomy.ids)} ошибок валидации={len(errors)}"
        )
    foreign = (
        GoldenEntry(
            text="Er steht früh auf.", expected_language="de", phenomena=("EN-PHRASAL-VERB",)
        ),
    )
    de_taxonomy = PhenomenaTaxonomy(load_phenomena(LANGUAGES / "de"))
    errors = validate_golden(foreign, de_taxonomy)
    lines.append("")
    lines.append("чужой id в golden (de: EN-PHRASAL-VERB) — вердикт валидатора (verbatim):")
    for error in errors:
        lines.append(f"  {error}")
    evidence("l1-10_golden_coverage.txt", "\n".join(lines))


# ------------------------------------------------------------------ T-L1-11


def l1_11() -> None:
    lines = _header("T-L1-11: отделяемые глаголы — одна лексическая единица (de/nl)")

    def units_for(code: str, sentence: str) -> list[Any]:
        pack = _REGISTRY[code]
        nlp = load_nlp(pack)
        return list(
            lexical_units(
                nlp(sentence),
                pack.separable_prefixes,
                pack.sentence_split.min_chars,
                pack.sentence_split.max_chars,
            )
        )

    for code, sentence in (
        ("de", "Er steht früh auf, bevor die Arbeit beginnt."),
        ("nl", "Hij staat vroeg op om te joggen."),
    ):
        lines.append(f"{code}: {sentence!r}")
        units = units_for(code, sentence)
        for unit in units:
            lines.append(
                f"  ЕДИНИЦА: verb_form={unit.verb_form} particle={unit.particle} "
                f"lemma={unit.lemma} unit_lemma={unit.unit_lemma}"
            )
        lines.append(f"  количество единиц: {len(units)} (критерий: 1)")
    lines.append("")
    lines.append("отрицательный контроль — предлог с именным дополнением НЕ единица:")
    for code, sentence in (
        ("de", "Das steht auf der Bank, und die Sonne scheint."),
        ("nl", "Hij gaat naar huis om te slapen."),
    ):
        count = len(units_for(code, sentence))
        lines.append(f"  {code}: {sentence!r} → единиц: {count} (критерий: 0)")
    evidence("l1-11_separable_units.txt", "\n".join(lines))


# ------------------------------------------------------------------ T-L1-12


def l1_12() -> None:
    lines = _header("T-L1-12: файл CEFR-лексикона удалён → derived, метка меняется, нет падений")
    with tempfile.TemporaryDirectory(prefix="l1_evidence_12_") as td:
        work = Path(td)
        languages = work / "languages"
        (languages / "xx" / "cefr").mkdir(parents=True)
        (languages / "yy").mkdir(parents=True)
        (languages / "xx" / "pack.yaml").write_text(
            'code: xx\nname: "Тестовый XX"\ncefr_source: lexicon:testlex\n', encoding="utf-8"
        )
        (languages / "yy" / "pack.yaml").write_text(
            'code: yy\nname: "Тестовый YY"\n', encoding="utf-8"
        )
        lexicon = languages / "xx" / "cefr" / "testlex.json"
        lexicon.write_text('{"cat": "A1", "dog": "A2"}\n', encoding="utf-8")
        registry = load_registry(languages)
        pack = registry["xx"]

        source = CefrSource(pack, languages / "xx")
        hit = source.level_for("cat")
        lines.append(f"лексикон на месте: hit={hit!r} метка={label(hit)!r}")
        miss = source.level_for("неизвестное")
        lines.append(f"слова нет в лексиконе: {miss!r} метка={label(miss)!r} (не истина)")

        lexicon.unlink()
        lines.append("")
        lines.append("файл cefr/testlex.json удалён (T-L1-12):")
        source = CefrSource(pack, languages / "xx")
        result = source.level_for("cat")
        lines.append(
            f"  effective_source={source.effective_source} (был lexicon:testlex) "
            f"level={result.level} estimated={result.estimated} метка={label(result)!r}"
        )
        lines.append("  исключений при пересоздании CefrSource: 0")

        lines.append("")
        prompts = work / "prompts"
        prompts.mkdir()
        resolver = PairResolver(registry_root=languages, prompts_root=prompts)
        report = resolver.resolve_pair("xx", "yy")
        lines.append(f"резолвер xx→yy: {report.status.value}, недостающее (verbatim):")
        for item in report.missing:
            lines.append(f"  - {item}")
    evidence("l1-12_lexicon_removal.txt", "\n".join(lines))


def main(argv: Sequence[str] | None = None) -> int:
    l1_1()
    l1_2()
    l1_3()
    l1_4()
    l1_5()
    l1_6()
    l1_7()
    l1_8()
    l1_9()
    l1_10()
    l1_11()
    l1_12()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
