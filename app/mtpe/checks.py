# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Детерминированные проверки ответа постредактуры (MTPE §4.5) + таксономия (данные).

Жёсткие H1–H7: любой отказ ведёт на следующую ступень лестницы §4.6. Мягкие S1–S3:
ответ принимается, сегмент получает флаг. Числа (пороги) — из конфига, не из кода
(Rule 1); таксономия — данные из config/taxonomies/postedit_edits.yaml (M4-10: в коде
только схема ответа, id таксономии — из файла).

Маркеры — тот же формат, что и кодек M3: `⟦n⟧` (открытие), `⟦/n⟧` (закрытие),
`⟦n/⟧` (пустой). `text_without_markers` снимает их (PUA-экранированные литералы
U+E000/U+E001 при этом сохраняются — раскрывает писатель, M5).
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from app.domain.glossary import Glossary
from app.glossary.consistency import contains_form, norm_text
from app.glossary.fix import sentence_count
from app.languages.detect import detect_text

#: Любая маркерная форма: `⟦n⟧` (open), `⟦/n⟧` (close), `⟦n/⟧` (empty).
_MARKER_ANY = re.compile(r"\u27e6\s*([/]?)(\d+)\s*([/]?)\u27e7")
_DIGITS = re.compile(r"\d+")


def load_postedit_taxonomy(taxonomy_path: str | Path) -> frozenset[str]:
    """Допустимые id правок из файла таксономии (M4-10: данные, не код)."""
    data = yaml.safe_load(Path(taxonomy_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("edits"), list):
        raise ValueError(f"таксономия повреждена (нет списка edits): {taxonomy_path}")
    return frozenset(str(edit["id"]) for edit in data["edits"])


#: Схема ответа постредактуры (константа кода, не таксономия): {text, edits[]}.
POSTEDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"type": {"type": "string"}, "note": {"type": "string"}},
                "required": ["type"],
            },
        },
    },
    "required": ["text", "edits"],
}


def markers_of(text: str) -> list[tuple[int, str]]:
    """Маркеры текста в порядке: (n, kind), kind ∈ {"open","close","empty"}."""
    result: list[tuple[int, str]] = []
    for match in _MARKER_ANY.finditer(text):
        lead, num, trail = match.group(1), int(match.group(2)), match.group(3)
        kind = "close" if lead else ("empty" if trail else "open")
        result.append((num, kind))
    return result


def marker_multiset(text: str) -> Counter[tuple[int, str]]:
    return Counter(markers_of(text))


def text_without_markers(text: str) -> str:
    return _MARKER_ANY.sub("", text)


def check_nesting(text: str) -> bool:
    """Пары маркеров корректно вложены (стек: open — push, close — pop, empty — pass)."""
    stack: list[int] = []
    for num, kind in markers_of(text):
        if kind == "open":
            stack.append(num)
        elif kind == "close":
            if not stack or stack[-1] != num:
                return False
            stack.pop()
    return not stack


def digit_sequences(text: str) -> Counter[str]:
    """Мультимножество цифровых последовательностей (S3)."""
    return Counter(_DIGITS.findall(text))


def levenshtein(a: str, b: str) -> int:
    """Расстояние Левенштейна (динамическое программирование, две строки)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            insert = current[j - 1] + 1
            delete = previous[j] + 1
            substitute = previous[j - 1] + (ca != cb)
            current.append(min(insert, delete, substitute))
        previous = current
    return previous[-1]


def edit_ratio(a: str, b: str) -> float:
    """Нормированное расстояние Левенштейна: dist / max(len) (S2)."""
    longest = max(len(a), len(b))
    if longest == 0:
        return 0.0
    return levenshtein(a, b) / longest


# ------------------------------------------------------------------ жёсткие H1–H7


def check_h1(parsed: Any, taxonomy: frozenset[str]) -> list[str]:
    """H1: JSON валиден (парсится фаза-м), edits[].type — из таксономии."""
    problems: list[str] = []
    if not isinstance(parsed, dict) or not isinstance(parsed.get("text"), str):
        return ["H1: ответ не объект с полем text (строка)"]
    edits = parsed.get("edits")
    if not isinstance(edits, list):
        return ["H1: поле edits отсутствует или не массив"]
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict) or edit.get("type") not in taxonomy:
            problems.append(f"H1: edits[{i}].type вне таксономии")
    return problems


def check_h2(text: str, original: str) -> list[str]:
    """H2: мультимножество маркеров == оригиналу и пары корректно вложены."""
    problems: list[str] = []
    if marker_multiset(text) != marker_multiset(original):
        missing = marker_multiset(original) - marker_multiset(text)
        extra = marker_multiset(text) - marker_multiset(original)
        problems.append(
            f"H2: маркеры не совпадают с оригиналом (нет {len(missing)}, лишних {len(extra)})"
        )
    if not check_nesting(text):
        problems.append("H2: маркеры не вложены корректно")
    return problems


def check_h3(text: str, target_lang: str, min_words: int, seed: int) -> list[str]:
    """H3: язык text без маркеров — целевой (сегменты не короче min_words).

    Как у черновика (M3): считаем только уверенную детекцию — язык не
    определён → не отказываем."""
    plain = text_without_markers(text)
    if len(plain.split()) < min_words:
        return []
    detection = detect_text(plain, seed)
    if detection.language is None:
        return []
    if detection.language != target_lang:
        confidence = detection.confidence
        return [
            f"H3: язык — {detection.language}"
            f"{' (p=' + format(confidence, '.2f') + ')' if confidence is not None else ''}"
            f", а не {target_lang}"
        ]
    return []


def check_h4(text: str, src_plain: str) -> list[str]:
    """H4: text не пуст и не равен src_plain (не пересказ оригинала)."""
    plain = norm_text(text_without_markers(text)).strip()
    if not plain:
        return ["H4: text пуст"]
    if plain == norm_text(src_plain).strip():
        return ["H4: text совпадает с исходным (src_plain)"]
    return []


def check_h5(text: str, original: str, max_sentence_delta: int) -> list[str]:
    """H5: |#предложений(text) - #предложений(оригинал)| ≤ max_sentence_delta."""
    text_count = sentence_count(text_without_markers(text))
    orig_count = sentence_count(text_without_markers(original))
    if abs(text_count - orig_count) > max_sentence_delta:
        return [f"H5: предложений {text_count} против {orig_count} (допуск ±{max_sentence_delta})"]
    return []


def check_h6(text: str, draft: str, edits: list[Any]) -> list[str]:
    """H6: edits пустой ⇒ text (без маркеров) == draft (молчаливая правка запрещена)."""
    if edits:
        return []
    plain = norm_text(text_without_markers(text)).strip()
    draft_norm = norm_text(draft).strip()
    if plain != draft_norm:
        return ["H6: edits пуст, но text отличается от черновика (молчаливая правка)"]
    return []


def check_h7(text: str, original: str) -> list[str]:
    """H7: для сегментов с переносами — число строк == числу строк оригинала."""
    if "\n" not in original:
        return []
    text_lines = len(text_without_markers(text).splitlines())
    orig_lines = len(original.splitlines())
    if text_lines != orig_lines:
        return [f"H7: строк {text_lines}, в оригинале {orig_lines}"]
    return []


def run_hard_checks(
    *,
    parsed: Any,
    text: str,
    original: str,
    draft: str,
    src_plain: str,
    edits: list[Any],
    taxonomy: frozenset[str],
    target_lang: str,
    min_words: int,
    max_sentence_delta: int,
    seed: int,
) -> list[str]:
    """Все H1–H7 разом (порядок H1→H7); пустой список — все пройдены."""
    problems = check_h1(parsed, taxonomy)
    if not problems:
        problems += check_h2(text, original)
        if not problems:
            problems += check_h3(text, target_lang, min_words, seed)
            if not problems:
                problems += check_h4(text, src_plain)
                if not problems:
                    problems += check_h5(text, original, max_sentence_delta)
                    if not problems:
                        problems += check_h6(text, draft, edits)
                        if not problems:
                            problems += check_h7(text, original)
    return problems


# ------------------------------------------------------------------ мягкие S1–S3


def check_s1(text: str, original: str, glossary: Glossary | None, source_lang: str) -> str | None:
    """S1: для термина (с подтверждённой парадигмой) из оригинала в тексте есть
    форма парадигмы; иначе флаг glossary_miss."""
    if glossary is None:
        return None
    text_norm = norm_text(text_without_markers(text))
    original_norm = norm_text(original)
    for entry in glossary.terms:
        source_forms = tuple(dict.fromkeys(norm_text(f) for f in entry.source_forms))
        if not any(contains_form(original_norm, form) for form in source_forms):
            continue
        expected = tuple(dict.fromkeys(norm_text(f) for f in entry.expected_forms()))
        if not any(contains_form(text_norm, form) for form in expected):
            return "glossary_miss"
    return None


def check_s2(text: str, draft: str, max_edit_ratio: float) -> str | None:
    """S2: нормированное расстояние Левенштейна(text, черновик) ≤ max_edit_ratio;
    иначе heavy_edit."""
    if edit_ratio(norm_text(text_without_markers(text)), norm_text(draft)) > max_edit_ratio:
        return "heavy_edit"
    return None


def check_s3(text: str, original: str) -> str | None:
    """S3: мультимножество цифровых последовательностей оригинала == тексту;
    иначе numbers_mismatch."""
    if digit_sequences(original) != digit_sequences(text):
        return "numbers_mismatch"
    return None


def run_soft_checks(
    *,
    text: str,
    original: str,
    draft: str,
    glossary: Glossary | None,
    source_lang: str,
    max_edit_ratio: float,
    skip_s2: bool,
) -> list[str]:
    """Все S1–S3 разом; `skip_s2` — для draft_bad (S2 пропускается, TZ §4.3)."""
    flags: list[str] = []
    s1 = check_s1(text, original, glossary, source_lang)
    if s1:
        flags.append(s1)
    if not skip_s2:
        s2 = check_s2(text, draft, max_edit_ratio)
        if s2:
            flags.append(s2)
    s3 = check_s3(text, original)
    if s3:
        flags.append(s3)
    return flags
