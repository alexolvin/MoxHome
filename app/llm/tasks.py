"""Реестр задач анализа (тракт A, E4): имя → JSON-схема ответа + pair_required.

JSON-схема — контракт структурированного ответа модели: клиент анализа
обязательно валидирует ответ по схеме (jsonschema); невалидный ответ после
исчерпания повторов — ошибка задачи, а не пустой результат (E4.4).

Задачи — анализ, который будет исполняться на этапах L1/L2 (словарь, грамматика,
глоссарий); E4 закладывает их контракты и инфраструктуру исполнения.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaskSpec:
    name: str
    description: str
    pair_required: bool
    schema: dict[str, Any]


def _object_schema(items_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["items"],
        "additionalProperties": False,
        "properties": {"items": {"type": "array", "items": items_schema}},
    }


_VOCABULARY_SCHEMA = _object_schema(
    {
        "type": "object",
        "required": ["term", "translation", "sentence"],
        "additionalProperties": False,
        "properties": {
            "term": {"type": "string", "minLength": 1},
            "translation": {"type": "string", "minLength": 1},
            "sentence": {"type": "string", "minLength": 1},
            "note": {"type": ["string", "null"]},
        },
    }
)

_GRAMMAR_SCHEMA = _object_schema(
    {
        "type": "object",
        "required": ["rule", "example", "explanation"],
        "additionalProperties": False,
        "properties": {
            "rule": {"type": "string", "minLength": 1},
            "example": {"type": "string", "minLength": 1},
            "explanation": {"type": "string", "minLength": 1},
        },
    }
)

_GLOSSARY_SCHEMA = _object_schema(
    {
        "type": "object",
        "required": ["term", "translation", "justification"],
        "additionalProperties": False,
        "properties": {
            "term": {"type": "string", "minLength": 1},
            "translation": {"type": "string", "minLength": 1},
            "justification": {"type": "string", "minLength": 1},
        },
    }
)

#: Л2.2/L2.3/L2.4: LLM — ТОЛЬКО перевод и краткое пояснение детерминированно
#: отобранных кандидатов + парадигма целевого языка (один раз, вместе с
#: переводом). Поиск кандидатов моделью НЕ выполняется (ТЗ L2.1/L2.2).
_GLOSSARY_ANNOTATION_SCHEMA = _object_schema(
    {
        "type": "object",
        "required": ["term", "translation", "pos", "note", "paradigm"],
        "additionalProperties": False,
        "properties": {
            "term": {"type": "string", "minLength": 1},
            "translation": {"type": "string", "minLength": 1},
            "pos": {"enum": ["noun", "verb", "adjective", "proper_noun", "other"]},
            "note": {"type": ["string", "null"]},
            "paradigm": {
                "type": "object",
                "required": ["inflected", "forms", "coincidences"],
                "additionalProperties": False,
                "properties": {
                    "inflected": {"type": "boolean"},
                    # Порядок значений = порядок имён форм из pack.yaml цели
                    # (передано в payload); [] — несклоняемый термин (L2.4).
                    "forms": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    # Поимённое объяснение совпадающих форм (L2.4):
                    # имя формы → объяснение (напр., «вин. = им. (неодуш.)»).
                    "coincidences": {
                        "type": "object",
                        "additionalProperties": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    }
)

#: Л2.7: стилевой профиль книги — один раз, хранится с книгой.
_BOOK_STYLE_SCHEMA = _object_schema(
    {
        "type": "object",
        "required": ["style", "region", "note"],
        "additionalProperties": False,
        "properties": {
            "style": {"enum": ["formal", "neutral", "literary", "conversational"]},
            "region": {"type": ["string", "null"]},
            "note": {"type": ["string", "null"]},
        },
    }
)

#: L3.3: точечная перепись ОДНОГО сегмента перевода с обязательным
#: употреблением термина глоссария. Ответ — XHTML блока целиком (внутренняя
#: разметка сохраняется; замена подстрокой запрещена — ТЗ L3.3).
_GLOSSARY_FIX_SCHEMA = _object_schema(
    {
        "type": "object",
        "required": ["segment_id", "html"],
        "additionalProperties": False,
        "properties": {
            "segment_id": {"type": "string", "minLength": 1},
            # XHTML переписанного блока (тот же корневой тег и атрибуты).
            "html": {"type": "string", "minLength": 1},
        },
    }
)

#: S1 (ТЗ_03): заполнение словарных карточек детерминированных кандидатов
#: (S1.2). Обязательные поля карточки по языку/POS задаёт card_schema.json
#: языка — валидация карточки идёт по ней (app/learning/cards.py); схема
#: ответа ЛЮБЫХ языков включает опциональные POS-поля всех пяти схем
#: (поле, чуждое POS/языку, провалит карточку по схеме языка — S1-4).
_VOCABULARY_CARD_SCHEMA = _object_schema(
    {
        "type": "object",
        "required": [
            "lemma",
            "meaning",
            "translation",
            "example",
            "example_generated",
            "usefulness",
        ],
        "additionalProperties": False,
        "properties": {
            "lemma": {"type": "string", "minLength": 1},
            "meaning": {"type": "string", "minLength": 1},
            "translation": {"type": "string", "minLength": 1},
            "example": {"type": "string", "minLength": 1},
            # Пример не из книги — ОБЯЗАТЕЛЕН флаг true (S1-6).
            "example_generated": {"type": "boolean"},
            "usefulness": {"type": "integer", "minimum": 1, "maximum": 10},
            # POS-поля карточек (de/nl/fr/it/en, card_schema.json).
            "article": {"type": ["string", "null"]},
            "plural": {"type": ["string", "null"]},
            "diminutive": {"type": ["string", "null"]},
            "gender": {"type": ["string", "null"]},
            "auxiliary": {"type": ["string", "null"]},
            "conjugation_group": {"type": ["string", "null"]},
            "irregular_forms": {
                "type": ["array", "null"],
                "items": {
                    "type": "object",
                    "required": ["tense", "form"],
                    "additionalProperties": False,
                    "properties": {
                        "tense": {"type": "string", "minLength": 1},
                        "form": {"type": "string", "minLength": 1},
                    },
                },
            },
            "main_forms": {
                "type": ["object", "null"],
                "required": ["infinitive", "past", "participle"],
                "additionalProperties": False,
                "properties": {
                    "infinitive": {"type": "string", "minLength": 1},
                    "past": {"type": "string", "minLength": 1},
                    "participle": {"type": "string", "minLength": 1},
                },
            },
            "separable": {"type": ["boolean", "null"]},
            "past_tense": {"type": ["string", "null"]},
            "past_participle": {"type": ["string", "null"]},
            "participle": {"type": ["string", "null"]},
        },
    }
)

#: S2 (ТЗ_03): уточнение и объяснение детерминированных MWE-кандидатов
#: (S2.3: список кандидатов моделью не выдвигается — эхо "surface";
#: phenomenon_id — ТОЛЬКО из таксономии языка-источника, id вне таксономии —
#: ошибка детерминированной валидации, S2-1).
_MWE_NOTE_SCHEMA = _object_schema(
    {
        "type": "object",
        "required": ["surface", "phenomenon_id", "canonical", "meaning", "translation"],
        "additionalProperties": False,
        "properties": {
            "surface": {"type": "string", "minLength": 1},
            "phenomenon_id": {"type": "string", "minLength": 1},
            "canonical": {"type": "string", "minLength": 1},
            "meaning": {"type": "string", "minLength": 1},
            "translation": {"type": "string", "minLength": 1},
        },
    }
)

#: S3 (ТЗ_03): объяснение детерминированных грамматических кандидатов
#: (S3.5: список кандидатов моделью не выдвигается — эхо "surface";
#: phenomenon_id — ТОЛЬКО из таксономии языка-источника; explanation —
#: текст на ЦЕЛЕВОМ языке в пределах лимита (payload: max_explanation_chars);
#: превышение/чужой язык — ошибка детерминированной валидации, S3-1/S3-5).
_GRAMMAR_NOTE_SCHEMA = _object_schema(
    {
        "type": "object",
        "required": ["surface", "phenomenon_id", "explanation"],
        "additionalProperties": False,
        "properties": {
            "surface": {"type": "string", "minLength": 1},
            "phenomenon_id": {"type": "string", "minLength": 1},
            "explanation": {"type": "string", "minLength": 1},
        },
    }
)

_TASKS: dict[str, TaskSpec] = {
    "analysis_vocabulary": TaskSpec(
        name="analysis_vocabulary",
        description="Извлечение ключевой лексики из текста (карточки: термин, перевод, пример).",
        pair_required=False,
        schema=_VOCABULARY_SCHEMA,
    ),
    "analysis_grammar": TaskSpec(
        name="analysis_grammar",
        description="Извлечение грамматических наблюдений (правило, пример, объяснение).",
        pair_required=False,
        schema=_GRAMMAR_SCHEMA,
    ),
    "analysis_glossary": TaskSpec(
        name="analysis_glossary",
        description=(
            "Извлечение терминов/имён, требующих согласованного перевода (глоссарий). "
            "pair_required: выбор терминов зависит от пары языков."
        ),
        pair_required=True,
        schema=_GLOSSARY_SCHEMA,
    ),
    "glossary_annotation": TaskSpec(
        name="glossary_annotation",
        description=(
            "Л2: перевод и краткое пояснение детерминированно отобранных кандидатов "
            "в глоссарий + парадигма целевого языка (одна запись на кандидата; "
            "список кандидатов моделью не выдвигается — ТЗ L2.1/L2.2)."
        ),
        pair_required=False,
        schema=_GLOSSARY_ANNOTATION_SCHEMA,
    ),
    "book_style": TaskSpec(
        name="book_style",
        description="Л2: стилевой профиль книги (formal/neutral/literary/conversational).",
        pair_required=False,
        schema=_BOOK_STYLE_SCHEMA,
    ),
    "glossary_fix": TaskSpec(
        name="glossary_fix",
        description=(
            "L3: переписать ОДИН сегмент перевода, употребив требуемый термин "
            "глоссария в грамматически верной форме; внутренний XHTML "
            "(ссылки, выделения, сноски) сохранить; замена подстрокой запрещена "
            "(ТЗ L3.3). pair_required=false: контекст пары — в payload."
        ),
        pair_required=False,
        schema=_GLOSSARY_FIX_SCHEMA,
    ),
    "vocabulary_card": TaskSpec(
        name="vocabulary_card",
        description=(
            "S1 (ТЗ_03): заполнить карточки детерминированных кандидатов "
            "(значение, перевод, пример из книги или помеченный сгенерированным, "
            "полезность 1..10, POS-поля по card_schema.json языка). Выдвижение "
            "лексии моделью НЕ выполняется (S1.1/S1.2). pair_required=false: "
            "контекст (языки, уровень пользователя, кандидаты) — в payload."
        ),
        pair_required=False,
        schema=_VOCABULARY_CARD_SCHEMA,
    ),
    "mwe_note": TaskSpec(
        name="mwe_note",
        description=(
            "S2 (ТЗ_03): уточнить и объяснить детерминированных MWE-кандидатов "
            "(тип из таксономии языка-источника, канонический вид, значение, "
            "перевод; эхо surface — один item на кандидата). Выдвижение "
            "выражений моделью НЕ выполняется (S2.3). pair_required=false: "
            "контекст (языки, уровень пользователя, кандидаты) — в payload."
        ),
        pair_required=False,
        schema=_MWE_NOTE_SCHEMA,
    ),
    "grammar_note": TaskSpec(
        name="grammar_note",
        description=(
            "S3 (ТЗ_03): объяснить детерминированных грамматических "
            "кандидатов (тип из таксономии языка-источника, краткое "
            "пояснение на ЦЕЛЕВОМ языке в пределах лимита; эхо surface — "
            "один item на кандидата). Выдвижение явлений моделью НЕ "
            "выполняется (S3.5). pair_required=false: контекст (языки, "
            "уровень пользователя, кандидаты, лимит длины) — в payload."
        ),
        pair_required=False,
        schema=_GRAMMAR_NOTE_SCHEMA,
    ),
}


def task(name: str) -> TaskSpec:
    try:
        return _TASKS[name]
    except KeyError as exc:
        raise KeyError(f"неизвестная задача анализа: {name}") from exc


def all_tasks() -> tuple[TaskSpec, ...]:
    return tuple(_TASKS.values())
