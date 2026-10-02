# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""LLM-аннотация кандидатов глоссария (ТЗ L2.2/L2.3/L2.4).

LLM привлекается ТОЛЬКО для перевода и краткого пояснения детерминированно
отобранных кандидатов (L2.2) — не для их поиска: список кандидатов уже
сформирован (candidates.py) и воспроизводим между запусками (L2-1).

Парадигма (L2.4) генерируется ОДИН РАЗ, вместе с переводом термина (тот же
вызов LLM), и хранится с записью глоссария. Детерминированная проверка
(без LLM):
- все формы различимы между собой ЛИБО совпадение объяснено поимённо
  (напр., совпадение именительного и винительного для неодушевлённых);
- общая основа у всех форм непустая (наибольший общий префикс ≥ 1 символа);
- число форм = числу имён форм из pack.yaml целевого языка (порядок тот же).
Не прошедшая проверка парадигма ОТКЛАДЫВАЕТСЯ: термин помечается
`paradigm_unverified` и исключается из автоматической правки L3 (L3.2).
Несклоняемые термины помечаются явно (inflected=false, формы не
генерируются — T-L2-11).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.glossary import (
    GLOSSARY_POS,
    PARADIGM_NOT_APPLICABLE,
    PARADIGM_UNVERIFIED,
    PARADIGM_VERIFIED,
    GlossaryTerm,
    Paradigm,
)
from app.glossary.candidates import Candidate
from app.llm.analysis import AnalysisClient
from app.llm.tasks import task
from app.storage.jobs_repo import JobRepository


class GlossaryError(AppError):
    """Сбой построения глоссария (L2): fail closed (Rule 6) — молчаливого
    деградирования (пропущенный термин, пустая аннотация) не допускать."""


@dataclass(frozen=True)
class Annotation:
    """Аннотация кандидата (ответ LLM + результат проверки парадигмы)."""

    term: str
    translation: str
    pos: str
    note: str | None
    paradigm: Paradigm | None
    paradigm_status: str
    paradigm_reason: str | None


def check_paradigm(
    form_names: tuple[str, ...],
    forms: tuple[str, ...],
    coincidences: dict[str, str],
) -> tuple[bool, str]:
    """Детерминированная проверка парадигмы (Л2.4). Возвращает (ok, причина).

    - число форм = числу имён (порядок из pack.yaml цели);
    - все формы непустые;
    - совпадающие формы объяснены поимённо (каждое имя с дублирующимся
      значением имеет непустое объяснение);
    - общая основа (наибольший общий префикс всех форм) непустая.
    """
    if len(forms) != len(form_names):
        return False, (
            f"число форм ({len(forms)}) != числу имён ({len(form_names)}): "
            "парадигма не соответствует списку pack.yaml"
        )
    if any(not form.strip() for form in forms):
        return False, "пустая форма в парадигме"
    duplicates = {
        name for name, form in zip(form_names, forms, strict=True) if forms.count(form) > 1
    }
    unexplained = sorted(name for name in duplicates if not (coincidences.get(name) or "").strip())
    if unexplained:
        return False, (
            "совпадающие формы без объяснения: "
            + ", ".join(f"{n}={forms[list(form_names).index(n)]!r}" for n in unexplained)
        )
    common = _common_prefix(forms)
    if not common:
        return False, "общая основа форм пуста (нет общего префикса)"
    return True, ""


def _common_prefix(values: tuple[str, ...]) -> str:
    if not values:
        return ""
    prefix = values[0]
    for value in values[1:]:
        while not value.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix


def _parse_annotation_item(
    raw: dict[str, Any], names_by_pos: dict[str, tuple[str, ...]]
) -> Annotation | str:
    """Одна запись ответа LLM → Annotation (или причина отказа — строка)."""
    fields, problem = _annotation_fields(raw)
    if fields is None:
        return problem
    paradigm, status, reason, problem = _annotation_paradigm(
        raw, fields["pos"], names_by_pos
    )
    if paradigm is None and problem:
        return problem
    return Annotation(
        term=fields["term"],
        translation=fields["translation"],
        pos=fields["pos"],
        note=fields["note"],
        paradigm=paradigm,
        paradigm_status=status,
        paradigm_reason=reason,
    )


def _annotation_fields(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Базовые поля (term/translation/pos/note): (поля, "") либо (None, причина)."""
    term = str(raw.get("term", "")).strip()
    translation = str(raw.get("translation", "")).strip()
    pos = str(raw.get("pos", "")).strip()
    if not term or not translation:
        return None, "пустой term/translation"
    if pos not in GLOSSARY_POS:
        return None, f"недопустимый pos: {pos!r}"
    note = raw.get("note")
    note = str(note).strip() if isinstance(note, str) and note.strip() else None
    return {"term": term, "translation": translation, "pos": pos, "note": note}, ""


def _indeclinable_paradigm(
    forms: tuple[str, ...],
) -> tuple[Paradigm | None, str, str | None, str]:
    """Явная метка несклоняемого термина (T-L2-11): форм нет, парадигма
    хранится с inflected=false (не 12 идентичных форм)."""
    if forms:
        return (
            None,
            "",
            None,
            "несклоняемый термин с формами: inflected=false и формы — противоречие",
        )
    return (
        Paradigm(inflected=False),
        PARADIGM_NOT_APPLICABLE,
        "несклоняемый термин (явная метка, L2.4)",
        "",
    )


def _annotation_paradigm(
    raw: dict[str, Any], pos: str, names_by_pos: dict[str, tuple[str, ...]]
) -> tuple[Paradigm | None, str, str | None, str]:
    """Парадигма: (paradigm|None, status, reason, problem). problem не пуст —
    запись отклонена целиком (аннотация невалидна, не «парадигма отклонена»)."""
    para = raw.get("paradigm")
    if not isinstance(para, dict):
        return None, "", None, "paradigm отсутствует или не объект"
    inflected = bool(para.get("inflected"))
    raw_forms = para.get("forms")
    if not isinstance(raw_forms, list):
        return None, "", None, "paradigm.forms отсутствует или не массив"
    forms = tuple(str(f).strip() for f in raw_forms)
    coincidences_raw = para.get("coincidences")
    coincidences: dict[str, str] = {}
    if isinstance(coincidences_raw, dict):
        coincidences = {
            str(k): str(v).strip() for k, v in coincidences_raw.items() if str(v).strip()
        }
    if not inflected:
        return _indeclinable_paradigm(forms)
    names = names_by_pos.get(pos, ())
    if not names:
        return (
            None,
            "",
            None,
            f"термин склоняется, но для pos {pos!r} в pack.yaml цели нет списка "
            "форм парадигмы (должно быть inflected=false)",
        )
    ok, reason = check_paradigm(names, forms, coincidences)
    if ok:
        return (
            Paradigm(
                inflected=True,
                form_names=tuple(names),
                forms=forms,
                coincidences=tuple(sorted(coincidences.items())),
            ),
            PARADIGM_VERIFIED,
            None,
            "",
        )
    # Парадигма отклонена (L2.4): хранится статус, не форма; аннотация валидна.
    return None, PARADIGM_UNVERIFIED, reason, ""


def annotate_candidates(
    candidates: tuple[Candidate, ...],
    settings: Settings,
    repo: JobRepository,
    *,
    job_id: str,
    source_lang: str,
    target_lang: str,
    names_by_pos: dict[str, tuple[str, ...]],
) -> dict[str, Annotation]:
    """Аннотация всех кандидатов (батчи по annotation_batch_size).

    Возвращает mapping surface → Annotation. Кандидат без записи в ответе
    LLM — GlossaryError (fail closed, Rule 6): глоссарий с дырой хуже
    падения задания.
    """
    if not candidates:
        return {}
    batch_size = settings.glossary.annotation_batch_size
    result: dict[str, Annotation] = {}
    client = AnalysisClient(settings, repo, job_id)
    spec = task("glossary_annotation")
    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        payload = {
            "paradigm_forms": {pos: list(names) for pos, names in names_by_pos.items()},
            "candidates": [
                {
                    "term": c.surface,
                    "source_forms": list(c.source_forms),
                    "pos_hint": c.pos_hint,
                    "rule": c.rule,
                    "occurrences": c.occurrences,
                    "first_segment_id": c.first_segment_id,
                }
                for c in batch
            ],
        }
        user_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        outcome = client.run_task(spec, source_lang, target_lang, user_payload)
        items = outcome.payload.get("items")
        if not isinstance(items, list):
            raise GlossaryError("ответ LLM: items отсутствует или не массив")
        seen: set[str] = set()
        for raw in items:
            if not isinstance(raw, dict):
                raise GlossaryError("ответ LLM: элемент items не объект")
            parsed = _parse_annotation_item(raw, names_by_pos)
            if isinstance(parsed, str):
                raise GlossaryError(f"аннотация отклонена: {parsed}")
            if parsed.term in result:
                raise GlossaryError(f"дублирующая аннотация термина: {parsed.term!r}")
            result[parsed.term] = parsed
            seen.add(parsed.term)
        expected = {c.surface.casefold() for c in batch}
        missing = sorted(expected - {t.casefold() for t in result})
        if missing:
            raise GlossaryError(
                "кандидаты без аннотации в ответе LLM (один на каждый, L2.2): "
                + ", ".join(repr(m) for m in missing)
            )
    return result


def terms_from_annotations(
    candidates: tuple[Candidate, ...],
    annotations: dict[str, Annotation],
) -> tuple[GlossaryTerm, ...]:
    """Записи глоссария (L2.3) из кандидатов + аннотаций (детерминированно)."""
    terms: list[GlossaryTerm] = []
    for cand in candidates:
        ann = annotations.get(cand.surface)
        if ann is None:
            continue  # защитное: annotate_candidates уже fail-closed
        terms.append(
            GlossaryTerm(
                term=cand.surface,
                source_forms=cand.source_forms,
                translation=ann.translation,
                pos=ann.pos,
                first_segment_id=cand.first_segment_id,
                occurrences=cand.occurrences,
                note=ann.note,
                user_confirmed=False,
                paradigm=ann.paradigm,
                paradigm_status=ann.paradigm_status,
                paradigm_reason=ann.paradigm_reason,
            )
        )
    return tuple(terms)
