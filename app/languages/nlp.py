"""spaCy-пайплайны (L1.2): один раз на процесс, только реально нужные компоненты.

- Модель — из `pack.yaml` (`spacy_model`); версии фиксируются в docs/HANDOFF.md
  и в отчёте каждого задания (`nlp_report` — секция `nlp` в validation/plan.json).
- Компоненты: токенизация + `sentencizer` (разбиение на предложения — параметры
  из pack.yaml) + `tagger` (POS) + `attribute_ruler` (морфология) + `lemmatizer`
  (лемматизация) + `ner` (L2: именованные сущности — кандидаты в глоссарий;
  включён на этапе L2, как заявлено в L1); `parser` остаётся отключённым ради
  скорости (без какого-либо сегментатора `doc.sents` не работает, E030 —
  измерено; sentencizer покрывает разбиение).
- Отсутствие модели — явный отказ на уровне резолвера (L0), а не исключение
  в середине задания (L1.2): `load_nlp` вызывается только для разрешённой пары.
- `lexical_units` — отделяемые/разделяемые глаголы (T-L1-11): приставка — данные
  pack.yaml (`separable_prefixes`), не код.
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import spacy
from spacy.language import Language

from app.core.errors import AppError
from app.languages.registry import PackSpec

#: Компоненты, отключённые ради скорости. L2: `ner` включён (кандидаты в
#: глоссарий — ТЗ L2.1); `parser` остаётся отключённым (D59).
DISABLED_COMPONENTS: tuple[str, ...] = ("parser",)

#: POS-состав между глаголом и приставкой, при котором единица НЕ создаётся:
#: именные constituents (NP) и границы предложений/реплик. Измерено: nl-модель
#: размечает наречие «vroeg» как VERB (lemma «vragen») — поэтому запрещаем
# поимённо, а не «всё, кроме ADV/ADJ/ADP/PART» (иначе T-L1-11 для nl ломался).
_FORBIDDEN_BETWEEN: frozenset[str] = frozenset(
    {"NOUN", "PRON", "DET", "NUM", "PUNCT", "SCONJ", "CCONJ"}
)
#: Приставка как предлог с именным дополнением — НЕ отделяемая приставка
#: («Das steht auf der Bank» / «Hij staat op de bank»).
_FORBIDDEN_AFTER: frozenset[str] = frozenset({"NOUN", "DET", "PRON", "NUM"})


class _NlpHolder:
    """Один пайплайн на модель на процесс (L1.2: «загружается один раз и
    переиспользуется»). Класс-держатель, без `global` (D41)."""

    _pipelines: ClassVar[dict[str, Language]] = {}

    @classmethod
    def get(cls, pack: PackSpec) -> Language:
        model = pack.spacy_model
        if model is None:
            raise AppError(
                f"для языка {pack.code} не объявлена spaCy-модель (pack.yaml: spacy_model)"
            )
        pipeline = cls._pipelines.get(model)
        if pipeline is None:
            pipeline = spacy.load(model, disable=list(DISABLED_COMPONENTS))
            if not any(name in ("sentencizer", "senter") for name, _ in pipeline.pipeline):
                # Встроенный sentencizer (пунктуация, детерминированно);
                # параметры pack.yaml (min_chars/max_chars) — окно обработки
                # предложений механизмами L1 (см. lexical_units).
                pipeline.add_pipe("sentencizer")
            cls._pipelines[model] = pipeline
        return pipeline

    @classmethod
    def known_models(cls) -> tuple[str, ...]:
        return tuple(sorted(cls._pipelines))


def load_nlp(pack: PackSpec) -> Language:
    """Пайплайн языка (одна загрузка на процесс). Отсутствие модели не доходит
    сюда: резолвер (L0) уже перевёл пару в unsupported."""
    return _NlpHolder.get(pack)


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def nlp_report(pack: PackSpec) -> dict[str, Any]:
    """Секция `nlp` отчёта задания: версии пайплайна источника зафиксированы
    (L1.2). Пайплайн не загружается — версии берутся из метаданных пакета;
    фактическая загрузка — лениво, в точке использования (`load_nlp`)."""
    return {
        "model": pack.spacy_model,
        "model_version": _package_version(pack.spacy_model) if pack.spacy_model else None,
        "spacy_version": _package_version("spacy"),
        "disabled_components": list(DISABLED_COMPONENTS),
        "sentence_split": {
            "min_chars": pack.sentence_split.min_chars,
            "max_chars": pack.sentence_split.max_chars,
        },
    }


def lemma_of(nlp: Language, form: str) -> str:
    """Лемма словоформы (один токен). Механизм измерения L1-2 (T-L1-2)."""
    return nlp(form + ".")[0].lemma_


@dataclass(frozen=True)
class LexicalUnit:
    """Единая лексическая единица: глагол + отделяемая приставка (T-L1-11).

    «Er steht früh auf» — ОДНА единица, а не два слова: `verb_form` и
    `particle` описывают её части, `unit_lemma` = приставка + лемма глагола
    (детерминированная сборка: «aufstehen» / «opstaan»).
    """

    verb_form: str
    particle: str
    verb_index: int
    particle_index: int
    lemma: str
    unit_lemma: str


def _sentence_ok(sent_text: str, min_chars: int, max_chars: int | None) -> bool:
    """Окно обработки предложения — данные pack.yaml (sentence_split)."""
    length = len(sent_text.strip())
    if length < min_chars:
        return False
    return max_chars is None or length <= max_chars


def lexical_units(
    doc: spacy.tokens.Doc,
    prefixes: Sequence[str],
    min_chars: int = 10,
    max_chars: int | None = None,
) -> tuple[LexicalUnit, ...]:
    """Отделяемые глаголы (de/nl, T-L1-11): глагол + приставка из данных
    pack.yaml как единая лексическая единица.

    Детерминированные правила (измерены по тегам моделей, 2026-09-13):
    - глагол — `pos_ == VERB` (вспомогательные AUX исключаются: «ist auf» нет);
      финитность: ранее в предложении есть другой глагол без границы клизмы
      (CCONJ/SCONJ/PUNCT) — текущий не финитный (nl-модель размечает наречие
      «vroeg» как VERB «vragen» — без этого правила ложная единица «opvragen»);
    - приставка — текст (без регистра) ∈ `separable_prefixes`, pos ADP/ADV;
      финальная приставка тагуется PTKVZ/VZ (ADP), предлог — APPR/VZ (ADP),
      поэтому disambiguation — по контексту, не по POS;
    - между глаголом и приставкой запрещены именные constituents и границы
      предложений (`_FORBIDDEN_BETWEEN`) — наречия (в т.ч. неверно размеченные
      nl-моделью как VERB) допускаются;
    - приставка-предлог с именным дополнением (след. токен NOUN/DET/PRON/NUM)
      — не единица (`_FORBIDDEN_AFTER`);
    - на глагол — одна единица (первая подходящая приставка);
    - предложения вне окна [min_chars, max_chars] (pack.yaml) не обрабатываются;
    - только главная конструкция (приставка справа от глагола); придаточные
      и другие позиции — не детектируются (ограничение заявлено в CONTROL).
    """
    prefix_set = frozenset(prefix.lower() for prefix in prefixes)
    if not prefix_set:
        return ()
    units: list[LexicalUnit] = []
    for sent in doc.sents:
        if not _sentence_ok(sent.text, min_chars, max_chars):
            continue
        tokens = list(sent)
        for i, verb in enumerate(tokens):
            if verb.pos_ != "VERB" or verb.is_punct:
                continue
            # Голова — финитный глагол клизмы: ранее в предложении есть другой
            # глагол БЕЗ границы клизмы (CCONJ/SCONJ/PUNCT) — текущий не
            # финитный. Иначе nl-модель, размечающая наречие «vroeg» как VERB
            # (lemma «vragen»), давала бы ложную единицу «opvragen» рядом с
            # истинной «opstaan» (измерено смоук-прогоном, 2026-09-13).
            if any(
                earlier.pos_ == "VERB"
                and not earlier.is_punct
                and not any(
                    barrier.pos_ in ("CCONJ", "SCONJ", "PUNCT") for barrier in tokens[k + 1 : i]
                )
                for k, earlier in enumerate(tokens[:i])
            ):
                continue
            for j in range(i + 1, len(tokens)):
                particle = tokens[j]
                if particle.pos_ not in ("ADP", "ADV"):
                    continue
                if particle.text.lower() not in prefix_set:
                    continue
                blocked = any(between.pos_ in _FORBIDDEN_BETWEEN for between in tokens[i + 1 : j])
                if blocked:
                    continue
                following = tokens[j + 1] if j + 1 < len(tokens) else None
                if following is not None and following.pos_ in _FORBIDDEN_AFTER:
                    continue
                units.append(
                    LexicalUnit(
                        verb_form=verb.text,
                        particle=particle.text.lower(),
                        verb_index=i,
                        particle_index=j,
                        lemma=verb.lemma_,
                        unit_lemma=particle.text.lower() + verb.lemma_,
                    )
                )
                break
    return tuple(units)


def iter_lemmas(nlp: Language, forms: Sequence[str]) -> Iterator[tuple[str, str]]:
    """(словоформа, лемма) по эталонному набору — механизм L1-2."""
    for form in forms:
        yield form, lemma_of(nlp, form)
