"""S3 (ТЗ_03): грамматические заметки — контекстные короткие пояснения
грамматических явлений в терминах таксономии языка-источника.

Механизм двухступенчатый (Rule 8: один двигатель отбора — как S2,
app/learning/mwe.py): детерминированный предотбор по морфологическим
признакам spaCy (шаблоны ``patterns`` таксономии в скопе ``stages: [s3]``;
наклонение, время, порядок слов — ТЗ_03 S3.5) → LLM (задача `grammar_note`)
объясняет НАЙДЕННОЕ, а не искала. Повторяющееся явление объясняется один
раз развёрнуто (LLM), далее — краткая детерминированная ссылка на первую
заметку (ТЗ_03 S3.4).

Валидация (ТЗ_03 S3.6, проверка `check_grammar_notes`): id — в таксономии
языка (S3-1); заметка не «подброшена» — поверхность среди детерминированных
кандидатов (механизм S1.5/S2.6); цитируемый фрагмент (surface) — в
сегменте (S3-2); длина пояснения ≤ лимита (конфиг) — превышение
ОТКЛОНЯЕТСЯ, а не усекается молча (S3.1, T-S3-10); язык пояснения =
целевой (langdetect с фиксированным сидом — детерминированно, S3-5).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from app.core.errors import ConfigurationError
from app.glossary.consistency import contains_form
from app.languages.detect import detect_text, normalize_code
from app.languages.phenomena import PhenomenaTaxonomy
from app.learning.mwe import MweCandidate, MweExpectation, MweGoldenEntry


def load_grammar_golden(pack_dir: Path) -> tuple[MweGoldenEntry, ...]:
    """Golden-набор S3 языка (`grammar_golden.yaml` рядом с pack.yaml, ключ
    "grammar": text + expected ids). Файл отсутствует/пуст — пустой набор
    (та же семантика, что у mwe_golden.yaml, S2): отсутствующий ресурс, а
    не ошибка загрузки. Запись переиспользует MweGoldenEntry (unit в S3
    не ожидается — поле остаётся None)."""
    path = pack_dir / "grammar_golden.yaml"
    if not path.is_file():
        return ()
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"не удалось разобрать grammar golden {path}: {exc}") from exc
    entries = data.get("grammar") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        return ()
    golden: list[MweGoldenEntry] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigurationError(f"grammar golden {path}: запись должна быть mapping")
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ConfigurationError(f"grammar golden {path}: пустой text")
        expected_raw = entry.get("expected")
        if not isinstance(expected_raw, list) or not expected_raw:
            raise ConfigurationError(
                f"grammar golden {path}: «{text}»: expected — непустой список"
            )
        expected: list[MweExpectation] = []
        for exp in expected_raw:
            if not isinstance(exp, dict):
                raise ConfigurationError(f"grammar golden {path}: expected — список mapping")
            exp_id = exp.get("id")
            if not isinstance(exp_id, str) or not exp_id.strip():
                raise ConfigurationError(
                    f"grammar golden {path}: expected.id — непустая строка"
                )
            unit = exp.get("unit")
            if unit is not None:
                raise ConfigurationError(
                    f"grammar golden {path}: expected.unit не используется в S3"
                )
            expected.append(MweExpectation(exp_id))
        golden.append(MweGoldenEntry(text.strip(), tuple(expected)))
    return tuple(golden)


def reference_explanation() -> str:
    """Краткая ссылка на первую заметку (ТЗ_03 S3.4): детерминированный
    шаблон (LLM не участвует), на языке целевого (пары проекта — ru).
    Точный указатель на первую заметку — поле ``reference_to``
    (segment_id заметки); идентификатор в текст не встраивается:
    64-символьный hex-id — не языковое содержание (измерено: langdetect
    сдвигает русскую фразу с таким id на bg, S3-5)."""
    return "Повторение: см. первую заметку об этом явлении."


def check_grammar_notes(
    *,
    items: Sequence[Mapping[str, Any]],
    candidates: Mapping[str, MweCandidate],
    taxonomy: PhenomenaTaxonomy,
    seg_texts: Mapping[str, str],
    normalize: Callable[[str], str],
    max_chars: int,
    target_language: str,
    seed: int,
) -> tuple[list[tuple[Mapping[str, Any], MweCandidate]], list[dict[str, Any]]]:
    """Детерминированная валидация заметок LLM (ТЗ_03 S3.6):

    1) id — в таксономии языка-источника (S3-1);
    2) заметка не «подброшена»: поверхность — среди детерминированных
       кандидатов (механизм S1.5/S2.6);
    3) цитируемый фрагмент (surface) — в сегменте (S3-2);
    4) длина пояснения ≤ лимита — превышение отклоняется, а не усекается
       молча (S3.1, T-S3-10);
    5) язык пояснения совпадает с целевым (S3-5; langdetect с фикс. сидом —
       детерминированно; текст без определённого языка — отклонение).

    `candidates` — карта «нормализованная поверхность → кандидат» (как S2.6).
    """
    validated: list[tuple[Mapping[str, Any], MweCandidate]] = []
    rejected: list[dict[str, Any]] = []
    for item in items:
        surface = str(item.get("surface", ""))
        phenomenon_id = str(item.get("phenomenon_id", ""))
        explanation = str(item.get("explanation", ""))
        reasons: list[str] = []
        if not taxonomy.is_valid(phenomenon_id):
            reasons.append(f"id явления вне таксономии языка (S3.6): {phenomenon_id}")
        candidate = candidates.get(normalize(surface))
        if candidate is None:
            reasons.append(
                "подброшенная заметка: поверхностная форма не среди "
                "детерминированных кандидатов (S3.6, механизм S1.5)"
            )
        else:
            segment_text = seg_texts.get(candidate.first_segment_id, "")
            if not contains_form(segment_text, surface):
                reasons.append(f"цитата вне сегмента (S3.2): {surface!r}")
        if not explanation.strip():
            reasons.append("пояснение пустое (S3.1)")
        else:
            if len(explanation) > max_chars:
                reasons.append(
                    "длина пояснения выше лимита (S3.1): "
                    f"{len(explanation)} > {max_chars} — усечение запрещено"
                )
            detected = detect_text(explanation, seed)
            if normalize_code(detected.language) != target_language:
                reasons.append(
                    "язык пояснения не совпадает с целевым (S3.5): "
                    f"{detected.language!r} != {target_language!r}"
                )
        if candidate is None or reasons:
            rejected.append(
                {"surface": surface, "phenomenon_id": phenomenon_id, "reasons": reasons}
            )
        else:
            validated.append((item, candidate))
    return validated, rejected
