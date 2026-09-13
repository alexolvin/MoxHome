"""T-L1-1…T-L1-12: языковые пакеты (L1) — NLP, таксономия, карточки, CEFR, частотность.

TZ_EpubTranslator_03_PACKAGES.md, раздел L1:
- T-L1-1 — пайплайны 5/5 загружаются, версии зафиксированы, один раз на процесс;
- T-L1-2 — лемматизация ≥90% на эталонном наборе 50 словоформ на язык (L1-2);
- T-L1-3 — de-карточка существительного без артикля → 0 выпущенных (L1-3);
- T-L1-4 — nl-карточка существительного без de/het → 0 выпущенных (L1-4);
- T-L1-5 — fr/it-карточка глагола без вспомогательного → 0 выпущенных (L1-5);
- T-L1-6 — идентификатор явления вне таксономии → 0 принятых (L1-6);
- T-L1-7 — LIMITATIONS.md: таблица CEFR-аудита, 5/5 строк (L1-7);
- T-L1-8 — `derived` → обязательная метка `Estimated CEFR level` (L1-8);
- T-L1-9 — в коде полос частотности числовых порогов нет (L1-9);
- T-L1-10 — golden: 20 предложений на язык, все id ⊆ таксономии, покрытие
  таксономии, внешний id валидатором отклоняется;
- T-L1-11 — отделяемые глаголы de «Er steht früh auf» / nl «Hij staat vroeg op»
  — ОДНА лексическая единица;
- T-L1-12 — файл лексикона удалён → переход в derived, метка меняется,
  исключений нет, резолвер перечень не молчит.

Реестр и данные — из репозитория (по умолчанию, как в L0): конфиги языков
живут в `config/languages/`, и тесты проверяют именно поставленные данные.
spaCy-модели загружаются реально (кэш на процесс — один раз на прогон pytest).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import spacy

from app.languages.cards import issue_cards, load_card_schema
from app.languages.cefr import CefrSource, label
from app.languages.nlp import (
    DISABLED_COMPONENTS,
    lemma_of,
    lexical_units,
    load_nlp,
    nlp_report,
)
from app.languages.phenomena import PhenomenaTaxonomy, load_phenomena, validate_golden
from app.languages.registry import (
    GoldenEntry,
    PackSpec,
    load_golden,
    load_lemma_reference,
    load_registry,
)
from app.languages.resolver import PairResolver

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ROOT / "config" / "languages"
LIMITATIONS = ROOT / "docs" / "LIMITATIONS.md"
#: Языки-источники L1 (пакеты с NLP; `ru` — только целевой, тесты L1 не касаются).
SOURCE_LANGUAGES: tuple[str, ...] = ("en", "de", "nl", "fr", "it")


def _registry() -> dict[str, PackSpec]:
    return load_registry(LANGUAGES)


# ------------------------------------------------------------------ T-L1-1


def test_l1_1_pipelines_five_of_five_versions_once_per_process() -> None:
    """T-L1-1 (L1-2): 5/5 пайплайнов загружаются, версии зафиксированы,
    один пайплайн на модель на процесс."""
    registry = _registry()
    for code in SOURCE_LANGUAGES:
        pack = registry[code]
        nlp = load_nlp(pack)
        names = [name for name, _ in nlp.pipeline]
        assert "sentencizer" in names, f"{code}: нет сегментатора (E030 без него)"
        # L2: NER включён (кандидаты в глоссарий, ТЗ L2.1/D59); parser остаётся
        # отключённым ради скорости (D59). Замечание L1 («включаются в L2»)
        # исполнено на этапе L2.
        assert "parser" not in names, f"{code}: parser должен быть отключён (D59)"
        assert "ner" in names, f"{code}: NER включён с L2 (ТЗ L2.1, D59)"
        report = nlp_report(pack)
        assert report["model"] == pack.spacy_model
        # L1-2: версии фиксированы — изменение версии модели ломает тест (осознанно).
        assert report["model_version"] == "3.8.0", (
            f"{code}: версия модели изменилась: {report['model_version']}"
        )
        assert report["spacy_version"] == spacy.__version__
        assert report["disabled_components"] == list(DISABLED_COMPONENTS)
    for code in SOURCE_LANGUAGES:
        pack = registry[code]
        assert load_nlp(pack) is load_nlp(pack), f"{code}: один пайплайн на процесс"


# ------------------------------------------------------------------ T-L1-2


@pytest.mark.parametrize("code", SOURCE_LANGUAGES)
def test_l1_2_lemmatization_at_least_90_per_language(code: str) -> None:
    """T-L1-2 (L1-2): точность лемматизации ≥90% на эталонном наборе 50 словоформ."""
    registry = _registry()
    pack = registry[code]
    nlp = load_nlp(pack)
    forms = load_lemma_reference(LANGUAGES / code)
    assert len(forms) == 50, f"{code}: эталонный набор обязан содержать 50 словоформ"
    failures = [
        f"{form.form}→{lemma_of(nlp, form.form)} (ожидалось {form.lemma})"
        for form in forms
        if lemma_of(nlp, form.form) != form.lemma
    ]
    correct = len(forms) - len(failures)
    assert correct / len(forms) >= 0.90, f"{code}: {correct}/50; {failures}"


# ------------------------------------------------------------------ T-L1-3..5


def _de_noun_card() -> dict[str, Any]:
    """Полная de-карточка существительного (positive control для L1-3)."""
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
    """Полная nl-карточка существительного (positive control для L1-4)."""
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
    """Полная fr-карточка глагола (positive control для L1-5)."""
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
    """Полная it-карточка глагола (positive control для L1-5)."""
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


def test_l1_3_de_noun_card_without_article_not_issued() -> None:
    """T-L1-3 (L1-3): de-существительное без артикля (der/die/das) — 0 выпущенных;
    без множественного — 0; плейсхолдер «—» вместо значения (Rule 5) — 0."""
    registry = _registry()
    schema = load_card_schema(LANGUAGES / "de", registry["de"])
    card = _de_noun_card()
    issued, rejected = issue_cards([card], schema)
    assert len(issued) == 1 and not rejected, "полная карточка обязана выпускаться"

    for field in ("article", "plural"):
        incomplete = dict(card)
        del incomplete[field]
        issued, rejected = issue_cards([incomplete], schema)
        assert issued == [], f"de-noun без {field}: выпущены {issued}"
        assert len(rejected) == 1
        assert any(field in reason for reason in rejected[0][1]), (
            f"причина отклонения должна называть поле {field}: {rejected[0][1]}"
        )

    placeholder = dict(card)
    placeholder["plural"] = "—"
    issued, rejected = issue_cards([placeholder], schema)
    assert issued == [], "плейсхолдер «—» не проходит (Rule 5)"
    assert any("плейсхолдер" in reason for reason in rejected[0][1])


def test_l1_4_nl_noun_card_without_article_not_issued() -> None:
    """T-L1-4 (L1-4): nl-существительное без артикля de/het — 0 выпущенных;
    чужой артикль (der) — 0; `diminutive: null` — легитимно (не плейсхолдер)."""
    registry = _registry()
    schema = load_card_schema(LANGUAGES / "nl", registry["nl"])
    card = _nl_noun_card()
    issued, rejected = issue_cards([card], schema)
    assert len(issued) == 1 and not rejected, "полная карточка обязана выпускаться"

    without_article = dict(card)
    del without_article["article"]
    issued, rejected = issue_cards([without_article], schema)
    assert issued == [], "nl-noun без артикля: выпущены " + repr(issued)
    assert any("article" in reason for reason in rejected[0][1])

    foreign_article = dict(card)
    foreign_article["article"] = "der"
    issued, _ = issue_cards([foreign_article], schema)
    assert issued == [], "чужой артикль (der) не проходит enum de/het"

    no_diminutive = dict(card)
    no_diminutive["diminutive"] = None
    issued, rejected = issue_cards([no_diminutive], schema)
    assert len(issued) == 1 and not rejected, (
        "diminutive: null — легитимное значение nullable-поля, не плейсхолдер"
    )


def test_l1_5_fr_it_verb_card_without_auxiliary_not_issued() -> None:
    """T-L1-5 (L1-5): fr/it-глагол без вспомогательного (avoir/etre, avere/essere)
    — 0 выпущенных; группа спряжения тоже обязательна."""
    registry = _registry()
    for code, card in (("fr", _fr_verb_card()), ("it", _it_verb_card())):
        schema = load_card_schema(LANGUAGES / code, registry[code])
        issued, rejected = issue_cards([card], schema)
        assert len(issued) == 1 and not rejected, f"{code}: полная карточка не выпущена"

        without_aux = dict(card)
        del without_aux["auxiliary"]
        issued, rejected = issue_cards([without_aux], schema)
        assert issued == [], f"{code}-verb без auxiliary: выпущены {issued}"
        assert any("auxiliary" in reason for reason in rejected[0][1])

        without_group = dict(card)
        del without_group["conjugation_group"]
        issued, rejected = issue_cards([without_group], schema)
        assert issued == [], f"{code}-verb без conjugation_group: выпущены {issued}"
        assert any("conjugation_group" in reason for reason in rejected[0][1])


# ------------------------------------------------------------------ T-L1-6


def test_l1_6_foreign_phenomenon_id_zero_accepted() -> None:
    """T-L1-6 (L1-6): идентификатор вне таксономии языка — 0 принятых;
    валидный id принимается, чужой (en→de, nl→de) — отклоняется."""
    taxonomy = PhenomenaTaxonomy(load_phenomena(LANGUAGES / "de"))
    assert taxonomy.ids, "таксономия de не должна быть пустой"

    valid, invalid = taxonomy.validate(("DE-TRENNBAR", "EN-PHRASAL-VERB", "NL-SCHEIDBAAR"))
    assert valid == ("DE-TRENNBAR",)
    assert invalid == ("EN-PHRASAL-VERB", "NL-SCHEIDBAAR")

    foreign_only, _ = taxonomy.validate(("EN-PHRASAL-VERB",))
    assert foreign_only == (), "чужой id принят (L1-6: 0 принятых)"


# ------------------------------------------------------------------ T-L1-7


def _cefr_section_rows(text: str) -> list[str]:
    """Данные строки таблицы CEFR-аудита в LIMITATIONS.md (после заголовка L1-7)."""
    assert "### CEFR-лексиконы" in text, "раздел CEFR-аудита отсутствует"
    section = text.split("### CEFR-лексиконы", 1)[1].split("\n## ", 1)[0]
    return [line for line in section.splitlines() if re.match(r"^\|\s*(en|de|fr|nl|it)\s*\|", line)]


def test_l1_7_limitations_cefr_table_five_rows() -> None:
    """T-L1-7 (L1-7): таблица аудита 5/5 строк; каждая строка — лексикон НЕ
    включён в поставку и фактический cefr_source = derived (честный статус)."""
    rows = _cefr_section_rows(LIMITATIONS.read_text(encoding="utf-8"))
    assert len(rows) == 5, f"таблица CEFR-аудита: строк {len(rows)}, ожидается 5"
    codes: set[str] = set()
    for line in rows:
        match = re.match(r"^\|\s*(\w+)\s*\|", line)
        assert match is not None
        codes.add(match.group(1))
        assert "нет" in line, f"строка без статуса поставки: {line}"
        assert "derived" in line, f"строка без фактического cefr_source: {line}"
    assert codes == set(SOURCE_LANGUAGES)


# ------------------------------------------------------------------ T-L1-8


@pytest.mark.parametrize("code", SOURCE_LANGUAGES)
def test_l1_8_derived_source_estimated_label(code: str) -> None:
    """T-L1-8 (L1-8): все пять языков — derived (аудит L1-7), уровень
    вычисляется — обязательная метка `Estimated CEFR level` (единая точка
    `cefr.label`; UI в U1 читает только её)."""
    registry = _registry()
    pack = registry[code]
    assert pack.cefr_source == "derived", (
        f"{code}: cefr_source изменился после аудита L1-7 — перепроверить лицензию"
    )
    source = CefrSource(pack, LANGUAGES / code)
    assert source.effective_source == "derived"
    result = source.level_for("слово")
    assert result.estimated is True
    assert result.level is None, "derived: уровень не подан как истина (L1.4)"
    assert label(result) == "Estimated CEFR level"


# ------------------------------------------------------------------ T-L1-9


def test_l1_9_no_numeric_band_thresholds_in_code() -> None:
    """T-L1-9 (L1-9): числовых порогов полос в коде нет — границы только
    в данных (pack.yaml: frequency_bands)."""
    registry = _registry()
    bounds: set[str] = set()
    for code in SOURCE_LANGUAGES:
        for band in registry[code].frequency_bands:
            if band.zipf_max is not None:
                bounds.add(f"{band.zipf_max}")
    assert bounds, "данные: полосы без числовых границ — проверка не осмысленна"
    for path in (ROOT / "app" / "languages").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for bound in bounds:
            assert bound not in source, (
                f"L1-9: числовой порог {bound} найден в {path.name} (должен быть в данных)"
            )


# ------------------------------------------------------------------ T-L1-10


@pytest.mark.parametrize("code", SOURCE_LANGUAGES)
def test_l1_10_golden_twenty_labeled_full_coverage(code: str) -> None:
    """T-L1-10: golden — 20 предложений на язык; все id ⊆ таксономии языка;
    таксономия покрыта golden полностью (каждый id встречается)."""
    entries = load_golden(LANGUAGES / code)
    assert len(entries) == 20, f"{code}: golden обязанных 20 предложений, найдено {len(entries)}"
    taxonomy = PhenomenaTaxonomy(load_phenomena(LANGUAGES / code))
    assert taxonomy.ids, f"{code}: таксономия пуста"
    errors = validate_golden(entries, taxonomy)
    assert errors == (), f"{code}: golden с id вне таксономии: {errors}"
    labeled = {phenomenon_id for entry in entries for phenomenon_id in entry.phenomena}
    uncovered = taxonomy.ids - labeled
    assert not uncovered, f"{code}: таксономия не покрыта golden: {sorted(uncovered)}"


def test_l1_10_validator_rejects_foreign_id() -> None:
    """T-L1-10: чужой id в golden (EN-PHRASAL-VERB у de) — валидатор возвращает
    ошибку; пустая таксономия — ошибка, а не молчаливый пропуск (Rule 6)."""
    taxonomy = PhenomenaTaxonomy(load_phenomena(LANGUAGES / "de"))
    foreign = (
        GoldenEntry(
            text="Er steht früh auf.",
            expected_language="de",
            phenomena=("EN-PHRASAL-VERB",),
        ),
    )
    errors = validate_golden(foreign, taxonomy)
    assert len(errors) == 1, f"ожидалась одна ошибка, получено: {errors}"
    assert "EN-PHRASAL-VERB" in errors[0]

    empty = PhenomenaTaxonomy(())
    assert validate_golden((), empty) == ("таксономия языка пуста — сверка невозможна",)


# ------------------------------------------------------------------ T-L1-11


@pytest.mark.parametrize(
    ("code", "sentence", "verb", "particle", "unit_lemma"),
    [
        ("de", "Er steht früh auf, bevor die Arbeit beginnt.", "steht", "auf", "aufstehen"),
        ("nl", "Hij staat vroeg op om te joggen.", "staat", "op", "opstaan"),
    ],
    ids=["de-aufstehen", "nl-opstaan"],
)
def test_l1_11_separable_verb_is_one_lexical_unit(
    code: str, sentence: str, verb: str, particle: str, unit_lemma: str
) -> None:
    """T-L1-11: «Er steht früh auf» / «Hij staat vroeg op» — ОДНА лексическая
    единица (глагол + приставка из данных pack.yaml), а не два слова."""
    registry = _registry()
    pack = registry[code]
    nlp = load_nlp(pack)
    units = lexical_units(
        nlp(sentence),
        pack.separable_prefixes,
        pack.sentence_split.min_chars,
        pack.sentence_split.max_chars,
    )
    assert len(units) == 1, f"{code}: ожидалась одна единица, получено: {units!r}"
    unit = units[0]
    assert unit.verb_form == verb
    assert unit.particle == particle
    assert unit.unit_lemma == unit_lemma


def test_l1_11_preposition_with_noun_complement_is_not_a_unit() -> None:
    """T-L1-11 (отрицательный контроль): «steht auf der Bank» / «gaat naar huis»
    — предлог с именным дополнением, не отделяемая приставка: 0 единиц."""
    registry = _registry()
    for code, sentence in (
        ("de", "Das steht auf der Bank, und die Sonne scheint."),
        ("nl", "Hij gaat naar huis om te slapen."),
    ):
        pack = registry[code]
        nlp = load_nlp(pack)
        units = lexical_units(
            nlp(sentence),
            pack.separable_prefixes,
            pack.sentence_split.min_chars,
            pack.sentence_split.max_chars,
        )
        assert units == (), f"{code}: ложная единица для предлога: {units!r}"


# ------------------------------------------------------------------ T-L1-12


def test_l1_12_lexicon_removed_derived_transition_no_crash(tmp_path: Path) -> None:
    """T-L1-12: декларированный лексикон удалён — язык переходит в derived:
    метка меняется на `Estimated CEFR level`, исключений нет, резолвер
    (L0.3) перечисляет отсутствующий файл — переход не молчаливый."""
    languages = tmp_path / "languages"
    (languages / "xx" / "cefr").mkdir(parents=True)
    (languages / "yy").mkdir(parents=True)
    (languages / "xx" / "pack.yaml").write_text(
        'code: xx\nname: "Тестовый XX"\ncefr_source: lexicon:testlex\n',
        encoding="utf-8",
    )
    (languages / "yy" / "pack.yaml").write_text('code: yy\nname: "Тестовый YY"\n', encoding="utf-8")
    lexicon = languages / "xx" / "cefr" / "testlex.json"
    lexicon.write_text('{"cat": "A1", "dog": "A2"}\n', encoding="utf-8")

    registry = load_registry(languages)
    pack = registry["xx"]

    source = CefrSource(pack, languages / "xx")
    assert source.lexicon_available is True
    hit = source.level_for("cat")
    assert hit.level == "A1" and hit.estimated is False
    assert label(hit) == "CEFR level (lexicon:testlex): A1"
    miss = source.level_for("неизвестное")
    assert miss.level is None and miss.estimated is False
    assert "не установлен" in label(miss)

    # Файл удалён — переход в derived без исключения.
    lexicon.unlink()
    source = CefrSource(pack, languages / "xx")
    assert source.lexicon_available is False
    assert source.effective_source == "derived"
    result = source.level_for("cat")
    assert result.level is None and result.estimated is True
    assert label(result) == "Estimated CEFR level"

    # Резолвер не молчит: отсутствующий лексикон в перечне (L0.3).
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    resolver = PairResolver(registry_root=languages, prompts_root=prompts)
    report = resolver.resolve_pair("xx", "yy")
    assert any("xx/cefr/testlex.json" in item for item in report.missing), (
        f"резолвер не перечислил отсутствующий лексикон: {report.missing}"
    )
