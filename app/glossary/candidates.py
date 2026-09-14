"""Детерминированный отбор кандидатов в глоссарий (ТЗ L2.1, L2-1).

Пять правил — все без LLM (LLM только переводит/поясняет отобранных, L2.2):
1. `ner` — именованные сущности spaCy (NER включён на L2, D59);
2. `multiword` — повторяющиеся многословные последовательности (n-граммы,
   максимальные: подфразы уже отобранных более длинных не дублируются);
3. `abbreviation` — аббревиатуры (акронимы и сокращения с точкой: «U.N.», «St.»);
4. `unit` — единицы измерения (список — данные конфигурации, Rule 1);
5. `capitalized` — слова с заглавной вне начала предложения; для `de`
   правило выключено в pack.yaml (capitalized_candidates: false) — в
   немецком все существительные с заглавной (ТЗ L2.1).

Воспроизводимость (L2-1): порядок правил фиксирован (RULE_PRIORITY),
итерация — по порядку глав/сегментов, дедупликация — по нормализованной
поверхности (NFC + casefold) с приоритетом более раннего правила. Два
запуска по той же книге дают ОДИНАКОВЫЙ список (L2-1: 100%). Пороги
(min_occurrences, n-граммы, длина, единицы) — из конфигурации
(glossary.*), не из кода (Rule 1).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from spacy.language import Language
from spacy.tokens import Doc

from app.core.config import Settings
from app.domain.book import Book
from app.languages.registry import PackSpec

#: Приоритет правил при дедупликации (кандидат остаётся за первым правилом).
RULE_PRIORITY: tuple[str, ...] = ("ner", "multiword", "abbreviation", "unit", "capitalized")

#: Акроним: 2–12 заглавных без внутренних символов («UNESCO», «NASA»).
_ACRONYM_RE = re.compile(r"^[A-Z]{2,12}$")
#: Сокращение с точкой: заглавная + буквы + точка («St.», «Dr.», «Corp.»)
#: либо цепочка инициалов («U.N.», «U.S.A.»).
_DOTTED_ABBR_RE = re.compile(r"^[A-Z][a-zA-Z]{1,10}\.$|^[A-Z]\.(?:[A-Z]\.)+$")


def _norm(text: str) -> str:
    """Нормализация сравнения: NFC + casefold (детерминированная)."""
    return unicodedata.normalize("NFC", text).casefold()


def _is_contiguous_subseq(needle: tuple[str, ...], haystack: tuple[str, ...]) -> bool:
    """`needle` — ли это непрерывный кусок `haystack` (n-грамма ⊂ n-граммы)."""
    if len(needle) > len(haystack):
        return False
    n = len(needle)
    return any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


@dataclass(frozen=True)
class Candidate:
    """Кандидат в глоссарий (до LLM-аннотации; L2.1/L2.2).

    `surface` — каноническая поверхностная форма (наиболее частая; при равном
    числе — первая встреченная); `source_forms` — все поверхности, порядок
    первой встречи. `first_segment_id` — контекст: сегмент ПЕРВОГО вхождения
    (L2.3). `rule` — правило отбора (аудит); `pos_hint` — подсказка POS для
    LLM (окончательный POS определяет модель).
    """

    surface: str
    source_forms: tuple[str, ...]
    occurrences: int
    first_segment_id: str
    rule: str
    pos_hint: str


@dataclass
class _Accumulator:
    """Накопитель по нормализованной поверхности (до дедупликации правил)."""

    surfaces: dict[str, int]  # поверхностная форма (NFC) → число вхождений
    order: list[str]  # поверхности, порядок первой встречи
    first_segment_id: str
    rules: set[str]  # правила, выбравшие кандидата

    @property
    def total(self) -> int:
        return sum(self.surfaces.values())


def _add(
    acc: dict[str, _Accumulator],
    surface: str,
    segment_id: str,
    rule: str,
    count: int = 1,
) -> None:
    if not surface.strip() or count <= 0:
        return
    # Внутренние пробельные (переносы/отступы исходного XHTML) — в один
    # пробел: один термин с разным отбелом (NER-сущность «Charles\n      »
    # в реальной книге) давал бы отдельного кандидата, а аннотация
    # отклонялась бы как «дублирующая» (поймано V1.13 на v1_en.epub).
    surface = re.sub(r"\s+", " ", unicodedata.normalize("NFC", surface)).strip()
    key = _norm(surface)
    entry = acc.get(key)
    if entry is None:
        entry = _Accumulator(
            surfaces={}, order=[], first_segment_id=segment_id, rules=set()
        )
        acc[key] = entry
    entry.rules.add(rule)
    if surface not in entry.surfaces:
        entry.surfaces[surface] = 0
        entry.order.append(surface)
    entry.surfaces[surface] += count


def _collect_ner(doc: Doc, segment_id: str, acc: dict[str, _Accumulator]) -> None:
    """NER-сущности (ТЗ L2.1). Каждое вхождение сущности — вхождение кандидата.

    Поверхности одной сущности с разными регистрами дедуплицируются
    casefold-ключом; лемматическая группировка поверхностных форм не
    требуется: исходные языки (en/de/nl/fr/it) не склоняют имена, а
    целевое словоизменение покрывает парадигма (L2.4, T-L2-10)."""
    for ent in doc.ents:
        if not ent.text.strip():
            continue
        _add(acc, ent.text, segment_id, "ner")


def _collect_multiword(
    doc: Doc,
    segment_id: str,
    settings: Settings,
    acc: dict[str, _Accumulator],
) -> None:
    """Повторяющиеся многословные последовательности (ТЗ L2.1).

    Слова — токены с буквенным началом (без пунктуации/цифр); n-грамма не
    переходит границу сегмента (сегмент — единица перевода). Подсчёт — по
    нормализованной n-грамме, поверхность — оригинальная (первая встреча).
    Максимальность: отбор от длинных к коротким, подпоследовательность
    уже отобранной n-граммы с ≥ тем же числом вхождений — не дублируется.
    """
    g = settings.glossary
    words = [
        t.text for t in doc if t.text.strip() and not t.is_punct and t.text[0].isalpha()
    ]
    if len(words) < g.ngram_min:
        return
    counts: dict[tuple[str, ...], int] = {}
    first_surface: dict[tuple[str, ...], str] = {}
    for n in range(g.ngram_min, g.ngram_max + 1):
        if len(words) < n:
            continue
        for i in range(len(words) - n + 1):
            gram_norm = tuple(_norm(w) for w in words[i : i + n])
            counts[gram_norm] = counts.get(gram_norm, 0) + 1
            if gram_norm not in first_surface:
                first_surface[gram_norm] = " ".join(words[i : i + n])
    kept: list[tuple[tuple[str, ...], int]] = []
    for gram in sorted(counts, key=lambda x: (-len(x), x)):
        if counts[gram] < g.min_occurrences:
            continue
        if any(
            _is_contiguous_subseq(gram, longer) and counts[gram] <= n
            for longer, n in kept
        ):
            continue
        kept.append((gram, counts[gram]))
    for gram, count in kept:
        _add(acc, first_surface[gram], segment_id, "multiword", count)


def _collect_abbreviations(doc: Doc, segment_id: str, acc: dict[str, _Accumulator]) -> None:
    """Аббревиатуры по токенам (детерминированно, без построчных regex-сдвигов)."""
    for token in doc:
        text = token.text
        if _ACRONYM_RE.match(text) or _DOTTED_ABBR_RE.match(text):
            _add(acc, text, segment_id, "abbreviation")


def _collect_units(
    doc: Doc,
    segment_id: str,
    settings: Settings,
    acc: dict[str, _Accumulator],
) -> None:
    """Число + единица из списка конфигурации (Rule 1: список — данные).
    Кандидат — САМА ЕДИНИЦА («kg», «km/h»); число в термин не входит.
    Граница после единицы — не буква (иначе «5 km/hour» попадёт в «km/h»)."""
    for unit in settings.glossary.units:
        pattern = re.compile(rf"\b\d+(?:[.,]\d+)?\s*{re.escape(unit)}(?![A-Za-z])")
        count = sum(1 for _ in pattern.finditer(doc.text))
        if count:
            _add(acc, unit, segment_id, "unit", count)


def _collect_capitalized(doc: Doc, segment_id: str, acc: dict[str, _Accumulator]) -> None:
    """Слова с заглавной ВНЕ начала предложения (ТЗ L2.1).

    Начало предложения — по doc.sents (sentencizer, L1); NER-токены не
    дублируются (правило ner уже отобрало их — приоритет RULE_PRIORITY).
    Для `de` функция не вызывается (capitalized_candidates: false, pack.yaml).
    """
    for sent in doc.sents:
        tokens = list(sent)
        for token in tokens[1:]:
            text = token.text
            if not text or not text[0].isupper() or not text[0].isalpha():
                continue
            if token.is_punct:
                continue
            if any(token in ent for ent in doc.ents):
                continue
            _add(acc, text, segment_id, "capitalized")


def extract_candidates(
    book: Book,
    pack: PackSpec,
    nlp: Language,
    settings: Settings,
) -> tuple[Candidate, ...]:
    """Список кандидатов книги (ТЗ L2.1). Детерминированно (L2-1: 100%)."""
    g = settings.glossary
    acc: dict[str, _Accumulator] = {}
    for chapter in book.chapters:
        for segment in chapter.segments:
            if not segment.plain_text.strip():
                continue
            doc = nlp(segment.plain_text)
            _collect_ner(doc, segment.segment_id, acc)
            _collect_multiword(doc, segment.segment_id, settings, acc)
            _collect_abbreviations(doc, segment.segment_id, acc)
            _collect_units(doc, segment.segment_id, settings, acc)
            if pack.capitalized_candidates:
                _collect_capitalized(doc, segment.segment_id, acc)

    candidates: list[Candidate] = []
    for key in sorted(acc):
        entry = acc[key]
        if entry.total < g.min_occurrences:
            continue
        rule = next(r for r in RULE_PRIORITY if r in entry.rules)
        # Каноническая поверхность: наиболее частая; при равенстве — первая.
        best = max(entry.order, key=lambda s: (entry.surfaces[s], -entry.order.index(s)))
        if len(best) > g.max_term_chars:
            continue
        pos_hint = "proper_noun" if rule in ("ner", "capitalized") else "other"
        candidates.append(
            Candidate(
                surface=best,
                source_forms=tuple(entry.order),
                occurrences=entry.total,
                first_segment_id=entry.first_segment_id,
                rule=rule,
                pos_hint=pos_hint,
            )
        )
    return tuple(candidates)
