"""S4 (ТЗ_03): summary, вопросы на понимание, упражнения — детерминированная
валидация и общие хелперы.

Материалы строятся ТОЛЬКО по фактическому тексту главы (выровненное
подмножество E6): вопросы/упражнения привязаны к `segment_id`; сущности
вопроса обязаны присутствовать в тексте главы (S4.4/S4-4); для заданий с
пропуском удалённое слово фактически в исходном предложении (S4.2/S4-2);
множественный выбор — ровно один правильный вариант, без дублей, ответ =
текст правильного варианта (S4.3/S4-3); лексические и грамматические
упражнения — только из уже выпущенных S1-карточек и S3-заметок
(S4.6/S4-6). Ответы — отдельная сущность (`answers.json`, S4.4) и не попадают
в тело главы.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from app.glossary.consistency import contains_form

#: Маркер пропуска — единая конвенция валидации и mock-генератора (дет. тест).
BLANK = "____"

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

_QUESTION_TYPES = ("free", "mc", "tf", "gap")
_QUESTION_LEVELS = ("easy", "standard", "advanced")
_EXERCISE_TYPES = ("mc", "tf", "gap", "translation", "vocab", "grammar", "free")


def split_words(text: str) -> tuple[str, ...]:
    """Слова текста (буквенные последовательности), по порядку."""
    return tuple(_WORD_RE.findall(text))


def blank_first_occurrence(sentence: str, word: str) -> str:
    """Первое вхождение `word` (граница слова, регистронезависимо) → BLANK.

    Детерминированная конвенция, общая для mock-генератора (tests) и
    валидации: предложение с пропуском обязано получаться из исходного
    предложения именно так (S4.2).
    """
    pattern = re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)", re.IGNORECASE)
    return pattern.sub(BLANK, sentence, count=1)


def check_gap(
    original_sentence: str, sentence_with_blank: str, answer: str
) -> list[str]:
    """S4.2: пропущенное слово ∈ исходном предложении; предложение с
    пропуском = исходное с первым вхождением слова замещённым."""
    reasons: list[str] = []
    if not original_sentence.strip() or not sentence_with_blank.strip():
        reasons.append("задание с пропуском: пустое исходное/пропущенное предложение")
        return reasons
    if not answer.strip():
        reasons.append("задание с пропуском: пустой ответ (пропущенное слово)")
        return reasons
    words = {w.casefold() for w in split_words(original_sentence)}
    if answer.casefold() not in words:
        reasons.append(f"пропущенное слово отсутствует в исходном предложении (S4.2): {answer}")
        return reasons
    if sentence_with_blank != blank_first_occurrence(original_sentence, answer):
        reasons.append(
            "предложение с пропуском не совпадает с исходным после замены первого "
            "вхождения слова (S4.2)"
        )
    return reasons


def check_mc(options: list[Mapping[str, Any]], answer: str) -> list[str]:
    """S4.3: ≥2 вариантов, дублей нет, ровно один правильный, ответ = текст
    правильного варианта."""
    reasons: list[str] = []
    texts = [str(opt.get("text", "")) for opt in options]
    if len(options) < 2:
        reasons.append(f"множественный выбор: меньше двух вариантов ({len(options)})")
        return reasons
    casefolded = [t.casefold() for t in texts]
    if len(set(casefolded)) != len(casefolded):
        reasons.append("множественный выбор: дублирующиеся варианты (S4.3)")
    correct = [i for i, opt in enumerate(options) if opt.get("correct") is True]
    if len(correct) != 1:
        reasons.append(
            f"множественный выбор: число правильных вариантов {len(correct)}, а не 1 (S4.3)"
        )
    elif casefolded[correct[0]] != answer.casefold():
        reasons.append("множественный выбор: ответ не совпадает с текстом правильного варианта")
    return reasons


#: Очевидные булевы слова (язык цели) — принимаются как true/false (S4 tf).
_TF_TRUE = frozenset({"true", "yes", "да", "верно", "правильно"})
_TF_FALSE = frozenset({"false", "no", "нет", "неверно", "неправильно"})


def check_tf_answer(answer: str) -> list[str]:
    """Верно/неверно: ответ — true/false (принимает и очевидные синонимы)."""
    a = str(answer).strip().casefold()
    if a in _TF_TRUE or a in _TF_FALSE:
        return []
    return [f"верно/неверно: ответ не true/false (получено {answer!r})"]


def check_entities(
    entities: list[str],
    chapter_text: str,
    chapter_text_target: str | None,
    normalize: Callable[[str], str],
) -> list[str]:
    """S4.4: каждая сущность вопроса присутствует в тексте главы.

    Сущности проверяются в исходнике ИЛИ в переводе: вопросы/упражнения
    формулируются на языке цели, их сущности живут в переводе (S4-4).
    """
    haystacks = [normalize(chapter_text)]
    if chapter_text_target is not None:
        haystacks.append(normalize(chapter_text_target))
    missing = [
        e for e in entities
        if not any(contains_form(h, normalize(e)) for h in haystacks)
    ]
    return [f"сущность отсутствует в главе (S4.4): {m}" for m in missing]


def validate_summary(summary: Mapping[str, Any]) -> list[str]:
    """S4.1: summary непуст (полевой контракт; генерация по названию
    исключена конвейером — в payload только фактический текст главы)."""
    reasons: list[str] = []
    for field in ("brief", "extended"):
        if not str(summary.get(field, "")).strip():
            reasons.append(f"summary: пустое поле {field}")
    for field in ("key_events", "entities", "themes", "concepts"):
        value = summary.get(field, [])
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            reasons.append(f"summary: поле {field} — не массив непустых строк")
    return reasons


def _question_content_reasons(
    qtype: Any, item: Mapping[str, Any], answer: str
) -> list[str]:
    """S4: проверки содержимого по типу вопроса (gap/mc/tf/free)."""
    reasons: list[str] = []
    if qtype == "gap":
        reasons.extend(
            check_gap(
                str(item.get("original_sentence") or ""),
                str(item.get("sentence_with_blank") or ""),
                answer,
            )
        )
    elif qtype == "mc":
        options = item.get("options")
        if not isinstance(options, list):
            reasons.append("множественный выбор: options — не массив")
        else:
            reasons.extend(check_mc(options, answer))
    elif qtype == "tf":
        reasons.extend(check_tf_answer(answer))
    elif qtype == "free" and not answer.strip():
        reasons.append("свободный ответ: пустой эталонный ответ")
    return reasons


def validate_question(
    item: Mapping[str, Any],
    *,
    chapter_seg_ids: frozenset[str],
    chapter_text: str,
    chapter_text_target: str | None = None,
    normalize: Callable[[str], str],
) -> list[str]:
    """S4: валидация ОДНОГО вопроса (детерминированная, mock-проверяемая).

    Возвращает список причин отклонения (пусто — вопрос валиден).
    `chapter_text_target` — перевод главы (для S4.4: сущности языка цели).
    """
    reasons: list[str] = []
    qtype = item.get("type")
    qlevel = item.get("level")
    if qtype not in _QUESTION_TYPES:
        reasons.append(f"неизвестный тип вопроса: {qtype!r}")
    if qlevel not in _QUESTION_LEVELS:
        reasons.append(f"неизвестный уровень вопроса: {qlevel!r}")
    if item.get("segment_id") not in chapter_seg_ids:
        reasons.append(f"segment_id вопроса не принадлежит главе: {item.get('segment_id')!r}")
    if not str(item.get("question", "")).strip():
        reasons.append("пустой текст вопроса")
    entities = item.get("entities") or []
    if not isinstance(entities, list) or any(not isinstance(e, str) for e in entities):
        reasons.append("entities — не массив строк")
    else:
        reasons.extend(
            check_entities(list(entities), chapter_text, chapter_text_target, normalize)
        )
    reasons.extend(
        _question_content_reasons(qtype, item, str(item.get("answer") or ""))
    )
    return reasons


def _exercise_ref_reasons(
    etype: Any,
    item: Mapping[str, Any],
    *,
    chapter_text: str,
    card_ids: frozenset[str],
    note_ids: frozenset[str],
    normalize: Callable[[str], str],
) -> list[str]:
    """S4: проверки источника по типу (vocab/grammar/translation)."""
    reasons: list[str] = []
    if etype == "vocab":
        ref = item.get("source_ref")
        if not isinstance(ref, Mapping) or ref.get("kind") != "card":
            reasons.append("лексическое упражнение: source_ref не указывает на карточку (S4.6)")
        elif ref.get("id") not in card_ids:
            reasons.append(
                f"лексическое упражнение не из выпущенной карточки (S4.6): {ref.get('id')!r}"
            )
    elif etype == "grammar":
        ref = item.get("source_ref")
        if not isinstance(ref, Mapping) or ref.get("kind") != "note":
            reasons.append("грамматическое упражнение: source_ref не указывает на заметку (S4.6)")
        elif ref.get("id") not in note_ids:
            reasons.append(
                f"грамматическое упражнение не из выпущенной заметки (S4.6): {ref.get('id')!r}"
            )
    elif etype == "translation":
        prompt = str(item.get("prompt") or "")
        norm_hay = normalize(chapter_text)
        found = contains_form(norm_hay, normalize(prompt))
        if not found and ": " in prompt:
            # backstop: модель иногда добавляет инструкцию («Переведите…: …»),
            # исходное предложение — после первого «: ».
            found = contains_form(norm_hay, normalize(prompt.split(": ", 1)[-1]))
        if not found:
            reasons.append("перевод: исходное предложение отсутствует в тексте главы")
    return reasons


def _exercise_content_reasons(
    etype: Any,
    item: Mapping[str, Any],
    chapter_text: str,
    chapter_text_target: str | None,
    normalize: Callable[[str], str],
) -> list[str]:
    """S4: проверки содержимого по типу (gap/mc/tf) — те же, что у вопросов,
    плюс сущности для верно/неверно (S4.4)."""
    reasons: list[str] = []
    answer = str(item.get("answer") or "")
    if etype == "gap":
        reasons.extend(
            check_gap(
                str(item.get("original_sentence") or ""),
                str(item.get("sentence_with_blank") or ""),
                answer,
            )
        )
    elif etype == "mc":
        options = item.get("options")
        if not isinstance(options, list):
            reasons.append("множественный выбор: options — не массив")
        else:
            reasons.extend(check_mc(options, answer))
    elif etype == "tf":
        reasons.extend(check_tf_answer(answer))
        entities = item.get("entities") or []
        if not isinstance(entities, list) or any(not isinstance(e, str) for e in entities):
            reasons.append("entities — не массив строк")
        else:
            reasons.extend(
                check_entities(list(entities), chapter_text, chapter_text_target, normalize)
            )
    return reasons


def validate_exercise(
    item: Mapping[str, Any],
    *,
    chapter_seg_ids: frozenset[str],
    chapter_text: str,
    chapter_text_target: str | None = None,
    card_ids: frozenset[str],
    note_ids: frozenset[str],
    allowed_types: frozenset[str],
    normalize: Callable[[str], str],
) -> list[str]:
    """S4: валидация ОДНОГО упражнения (детерминированная).

    `card_ids`/`note_ids` — выпущенные карточки S1 / заметки S3 ГЛАВЫ
    (лексика/грамматика — только из выпущенного, S4.6); `allowed_types` —
    конфиг `limits.exercises_types`; `chapter_text_target` — перевод главы
    (S4.4: сущности tf на языке цели).
    """
    etype = item.get("type")
    if etype not in _EXERCISE_TYPES:
        return [f"неизвестный тип упражнения: {etype!r}"]
    reasons: list[str] = []
    if etype not in allowed_types:
        reasons.append(f"тип упражнения не в конфигурации (limits.exercises_types): {etype}")
    if item.get("segment_id") not in chapter_seg_ids:
        reasons.append(
            f"segment_id упражнения не принадлежит главе: {item.get('segment_id')!r}"
        )
    if not str(item.get("prompt", "")).strip():
        reasons.append("пустой текст задания")
    if not str(item.get("answer") or "").strip():
        reasons.append("пустой ответ упражнения")
    reasons.extend(
        _exercise_content_reasons(etype, item, chapter_text, chapter_text_target, normalize)
    )
    reasons.extend(
        _exercise_ref_reasons(
            etype,
            item,
            chapter_text=chapter_text,
            card_ids=card_ids,
            note_ids=note_ids,
            normalize=normalize,
        )
    )
    return reasons
