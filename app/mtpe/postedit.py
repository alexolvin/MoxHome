# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Постредактура тракта M (MTPE M4, §4): LLM дорабатывает MT-черновик там, где он
ошибочен, и переносит маркеры оригинала в итоговый текст. Переписывание верного
черновика «покрасивее» — дефект (жёсткие H1–H7 + мягкие S1–S3, §4.5).

Цепочка — один элемент spine (глава); внутри — строго по порядку, контекст —
`mtpe.context_segments` предыдущих сегментов той же главы (оригинал с маркерами
+ итоговый текст, §4.1). Вход модели — 6 блоков (§4.2). Лестница попыток (§4.6):
1 — запрос; 2 — + список нарушенных жёстких проверок; 3 — по предложениям (пропуск,
если пара маркеров пересекает границу). Все провалены → `pe_rejected`: итог =
черновик (без структурных элементов, флаг `markup_dropped`) либо остановка
`pe_unrecoverable`. Системные остановки — по долям `pe_rejected`/`heavy_edit`
(§4.6). Аудит: `quality.log` (100 % сегментов с правками/флагами) + `pe_report.json`
(§4.7). Возобновление — по ключу идемпотентности, запись `final.jsonl` с `fsync`
(как M3).

Пороги и таксономия — данные (Rule 1): `settings.mtpe.*` и
`config/taxonomies/postedit_edits.yaml` (в коде — только схема ответа).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.glossary import Glossary, StyleProfile
from app.glossary.consistency import contains_form, norm_text
from app.languages.registry import load_registry
from app.llm.postedit import PostEditClient
from app.llm.prompts import PromptResolver
from app.mtpe.checks import (
    edit_ratio,
    load_postedit_taxonomy,
    run_hard_checks,
    run_soft_checks,
    text_without_markers,
)
from app.storage.jobs_repo import JobRepository

#: Сид детектора языка (детерминированность H3; аналог `_DETECT_SEED` M3).
_PE_SEED = 0
#: Граница предложения для попытки 3 (оконечная пунктуация + межсловный пробел).
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
#: Маркер (любая форма) для проверки пересечения пары границей предложения.
_ANY_MARKER = re.compile(r"\u27e6\s*([/]?)(\d+)\s*([/]?)\u27e7")


class PostEditStopError(AppError):
    """Системная остановка фазы: `pe_unrecoverable`, `pe_reject_ratio_exceeded`,
    `pe_overedit` (§4.6). `reason` — причина (детерминированно)."""

    def __init__(self, reason: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason, details=details)
        self.reason = reason


@dataclass
class PostEditPhaseResult:
    """Накопительные итоги прогона (для CONTROL)."""

    segment_count: int = 0
    processed_count: int = 0
    rejected_count: int = 0
    heavy_edit_count: int = 0
    request_count: int = 0
    resumed_count: int = 0


@dataclass(frozen=True)
class _PECtx:
    """Контекст одного сегмента на лестнице (бандл параметров)."""

    seg: dict[str, Any]
    context: str
    source_lang: str
    target_lang: str
    glossary: Glossary | None
    style: StyleProfile | None

    @property
    def seg_id(self) -> str:
        return str(self.seg["segment_id"])

    @property
    def original(self) -> str:
        return str(self.seg["src_encoded"])

    @property
    def draft(self) -> str:
        return str(self.seg["draft"])

    @property
    def src_plain(self) -> str:
        return str(self.seg.get("src_plain", ""))

    @property
    def is_draft_bad(self) -> bool:
        return "draft_bad" in self.seg.get("flags", [])


class PostEditPhase:
    def __init__(self, settings: Settings, repo: JobRepository, job_id: str) -> None:
        self._settings = settings
        self._repo = repo
        self._job_id = job_id
        self._client = PostEditClient(settings, repo, job_id)
        self._profile = settings.llm.profile("postedit")
        self._mtpe = settings.mtpe
        self._job_dir = settings.job_dir(job_id)
        self._m_dir = self._job_dir / "m"
        self._final_path = self._m_dir / "final.jsonl"
        self._quality_path = self._m_dir / "quality.log"
        self._report_path = self._m_dir / "pe_report.json"
        self._taxonomy = load_postedit_taxonomy(self._taxonomy_path())
        self._registry = load_registry(settings.languages_root)
        self._user_template = ""
        self._system_prompt = ""
        self._prompt_version = ""
        self._draft_bad_line = ""
        self._request_count = 0
        # накопители pe_report.json
        self._edit_types: dict[str, int] = {}
        self._attempts_dist: dict[int, int] = {}
        self._rejected_ids: list[str] = []
        self._heavy_ids: list[str] = []
        self._dropped_ids: list[str] = []
        self._numbers_ids: list[str] = []
        self._edit_ratios: list[float] = []

    # ------------------------------------------------------------------ публичное

    def run(
        self,
        source_lang: str,
        target_lang: str,
        glossary: Glossary | None = None,
        style: StyleProfile | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> PostEditPhaseResult:
        """Постредактура (§4). `on_progress(done, total)` — после каждого
        сегмента: сделано итогов (вкл. возобновлённые) / всего (MTPE §4.4, M6)."""
        self._setup_prompt(source_lang, target_lang)
        segments = self._load_segments()
        if not segments:
            raise AppError(
                "postedit: нет выходов черновика (m/segments.jsonl + m/drafts.jsonl)",
                details={"job_id": self._job_id},
            )
        self._m_dir.mkdir(parents=True, exist_ok=True)

        # Записи прошлых прогонов: возобновление (по ключу) + контекст (итоги).
        finals: dict[str, dict[str, Any]] = self._load_finals()
        pending = [
            seg
            for seg in segments
            if finals.get(str(seg["segment_id"]), {}).get("key") != seg["key"]
        ]
        result = PostEditPhaseResult(
            segment_count=len(segments),
            resumed_count=len(segments) - len(pending),
        )
        if pending:
            self._client.warmup("warmup")

        chains = self._group_by_chain(segments)
        processed_since_check = 0
        finals_done = result.resumed_count
        for spine_idx in sorted(chains):
            chain = chains[spine_idx]
            for index, seg in enumerate(chain):
                seg_id = str(seg["segment_id"])
                if finals.get(seg_id, {}).get("key") == seg["key"]:
                    continue  # возобновление: уже в finals (и в контексте)
                context = self._build_context(chain, index, finals)
                ctx = _PECtx(
                    seg, context, source_lang, target_lang, glossary, style
                )
                row = self._process_segment(ctx, result)
                if row is not None:
                    finals[seg_id] = row  # контекст для следующих сегментов
                finals_done += 1
                if on_progress is not None:
                    on_progress(finals_done, result.segment_count)
                processed_since_check += 1
                if (
                    self._mtpe.check_every_segments > 0
                    and processed_since_check % self._mtpe.check_every_segments == 0
                ):
                    self._system_stop_check(result)
        result.request_count = self._request_count
        self._write_report(source_lang, target_lang, glossary, len(segments))
        return result

    # ------------------------------------------------------------------ промпт

    def _setup_prompt(self, source_lang: str, target_lang: str) -> None:
        prompt = PromptResolver(self._settings.prompts_dir).resolve(
            self._profile.prompt_task or "postedit",
            source_lang,
            target_lang,
            pair_required=False,
        )
        self._prompt_version = f"v{prompt.version}"
        self._system_prompt = prompt.system.format(
            taxonomy=", ".join(sorted(self._taxonomy))
        )
        self._user_template = prompt.user_template
        self._draft_bad_line = self._read_draft_bad_line(prompt.file)

    def _read_draft_bad_line(self, file_rel: str) -> str:
        """Строка непригодности черновика (§4.3) — из промпта (данные, не код, M4-10)."""
        path = self._settings.prompts_dir / file_rel
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return ""
        return str(data.get("draft_bad_line", "") or "").strip()

    def _taxonomy_path(self) -> Path:
        return self._settings.storage.root / "config" / "taxonomies" / "postedit_edits.yaml"

    # ------------------------------------------------------------------ ввод

    def _load_segments(self) -> list[dict[str, Any]]:
        segs: dict[str, dict[str, Any]] = {}
        for row in _read_jsonl(self._m_dir / "segments.jsonl"):
            segs[str(row["segment_id"])] = dict(row)
        drafts: dict[str, dict[str, Any]] = {}
        for row in _read_jsonl(self._m_dir / "drafts.jsonl"):
            drafts[str(row["segment_id"])] = dict(row)
        if set(segs) != set(drafts):
            raise AppError(
                "postedit: расхождение segments.jsonl / drafts.jsonl",
                details={"mismatch": sorted(set(segs) ^ set(drafts))[:20]},
            )
        rows: list[dict[str, Any]] = []
        for seg_id, seg in segs.items():
            draft = str(drafts[seg_id].get("draft", ""))
            seg["draft"] = draft
            seg["flags"] = list(drafts[seg_id].get("flags") or [])
            seg["key"] = _pe_key(
                draft, self._profile.model, self._prompt_version, self._fingerprint()
            )
            rows.append(seg)
        rows.sort(key=lambda row: int(row.get("order", 0)))
        return rows

    def _fingerprint(self) -> str:
        data = {"temperature": self._profile.temperature, "extra_body": self._profile.extra_body}
        return json.dumps(data, sort_keys=True, ensure_ascii=False)

    def _group_by_chain(self, segments: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
        chains: dict[int, list[dict[str, Any]]] = {}
        for seg in segments:  # уже отсортированы по `order`
            chains.setdefault(int(seg.get("spine_idx", 0)), []).append(seg)
        return chains

    def _load_finals(self) -> dict[str, dict[str, Any]]:
        finals: dict[str, dict[str, Any]] = {}
        for row in _read_jsonl(self._final_path):
            finals[str(row["segment_id"])] = row
        return finals

    # ------------------------------------------------------------------ контекст и 6 блоков

    def _build_context(
        self, chain: list[dict[str, Any]], index: int, finals: dict[str, dict[str, Any]]
    ) -> str:
        previous = chain[max(0, index - self._mtpe.context_segments) : index]
        blocks: list[str] = []
        for prev in previous:
            row = finals.get(str(prev["segment_id"]))
            if row is None:
                continue
            blocks.append(
                f"[{prev['segment_id']}]\nOriginal: {prev['src_encoded']}\nFinal: {row['text']}"
            )
        return "\n\n".join(blocks) if blocks else "(нет — первый сегмент главы)"

    def _glossary_block(self, seg: dict[str, Any], glossary: Glossary | None) -> str:
        if glossary is None:
            return "absent"
        haystack = norm_text(str(seg.get("src_plain", "")))
        lines: list[str] = []
        for entry in glossary.terms:
            forms = tuple(dict.fromkeys(norm_text(f) for f in entry.source_forms))
            if not any(contains_form(haystack, form) for form in forms):
                continue
            line = f"{entry.term} -> {entry.translation}"
            if entry.paradigm is not None and entry.paradigm_status == "verified":
                line += f" [paradigm: {', '.join(entry.paradigm.forms)}]"
            lines.append(line)
        return "\n".join(lines) if lines else "(нет терминов в сегменте)"

    def _style_block(self, style: StyleProfile | None) -> str:
        if style is None:
            return "(нет)"
        parts = [style.style]
        if style.region:
            parts.append(f"region: {style.region}")
        if style.note:
            parts.append(style.note)
        return " ".join(parts)

    def _build_user_message(self, ctx: _PECtx, original: str, draft: str) -> str:
        src_pack = self._registry.get(ctx.source_lang)
        tgt_pack = self._registry.get(ctx.target_lang)
        draft_bad_line = ("\n" + self._draft_bad_line) if ctx.is_draft_bad else ""
        return self._user_template.format(
            source_language=src_pack.mt_name if src_pack else ctx.source_lang,
            target_language=tgt_pack.mt_name if tgt_pack else ctx.target_lang,
            style_profile=self._style_block(ctx.style),
            glossary=self._glossary_block(ctx.seg, ctx.glossary),
            context=ctx.context,
            original=original,
            draft=draft,
            draft_bad_line=draft_bad_line,
        )

    # ------------------------------------------------------------------ лестница §4.6

    def _process_segment(
        self, ctx: _PECtx, result: PostEditPhaseResult
    ) -> dict[str, Any] | None:
        parsed, attempts = self._ladder(ctx)
        if parsed is not None:
            flags = self._soft_flags(parsed, ctx)
            return self._finalize_accepted(ctx, parsed, flags, attempts, result)
        return self._finalize_rejected(ctx, attempts, result)

    def _ladder(self, ctx: _PECtx) -> tuple[Any, int]:
        """(parsed|None, число ступеней)."""
        user_msg = self._build_user_message(ctx, ctx.original, ctx.draft)
        parsed, problems = self._attempt(user_msg, f"{ctx.seg_id}_a1", ctx)
        if parsed is not None:
            return parsed, 1
        user_msg_2 = user_msg + (
            "\n\nThe previous attempt violated these checks:\n"
            + "\n".join(f"- {p}" for p in problems)
            + "\nFix all of them."
        )
        parsed, _ = self._attempt(user_msg_2, f"{ctx.seg_id}_a2", ctx)
        if parsed is not None:
            return parsed, 2
        if not self._attempt3_allowed(ctx.original):
            return None, 2
        return self._attempt3(ctx), 3

    def _attempt(self, user_msg: str, label: str, ctx: _PECtx) -> tuple[Any, list[str]]:
        """Один запрос + H1–H7. (parsed, []) при проходе; (None, проблемы) — при отказе."""
        res = self._client.complete(self._system_prompt, user_msg, label)
        self._request_count += 1
        parsed = _safe_json(res.content)
        text = str(parsed.get("text", "")) if isinstance(parsed, dict) else ""
        edits = (parsed.get("edits") if isinstance(parsed, dict) else []) or []
        problems = run_hard_checks(
            parsed=parsed,
            text=text,
            original=ctx.original,
            draft=ctx.draft,
            src_plain=ctx.src_plain,
            edits=edits,
            taxonomy=self._taxonomy,
            target_lang=ctx.target_lang,
            min_words=self._mtpe.lang_check_min_words,
            max_sentence_delta=self._mtpe.max_sentence_delta,
            seed=_PE_SEED,
        )
        return (parsed, []) if not problems else (None, problems)

    def _attempt3(self, ctx: _PECtx) -> Any:
        """Попытка 3 (§4.6 п.3): каждое предложение отдельно, результат склеивается
        и проверяется целиком. parsed|None."""
        sentences = (
            [s for s in _SENTENCE_SPLIT.split(ctx.original) if s.strip()] or [ctx.original]
        )
        texts: list[str] = []
        edits: list[Any] = []
        for sentence in sentences:
            user_msg = self._build_user_message(ctx, sentence, ctx.draft)
            res = self._client.complete(self._system_prompt, user_msg, f"{ctx.seg_id}_a3")
            self._request_count += 1
            parsed = _safe_json(res.content)
            if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
                texts.append(str(parsed["text"]))
                sent_edits = parsed.get("edits")
                if isinstance(sent_edits, list):
                    edits.extend(sent_edits)
        joined = "".join(texts)
        joined_parsed = {"text": joined, "edits": edits}
        problems = run_hard_checks(
            parsed=joined_parsed,
            text=joined,
            original=ctx.original,
            draft=ctx.draft,
            src_plain=ctx.src_plain,
            edits=[],
            taxonomy=self._taxonomy,
            target_lang=ctx.target_lang,
            min_words=self._mtpe.lang_check_min_words,
            max_sentence_delta=self._mtpe.max_sentence_delta,
            seed=_PE_SEED,
        )
        return joined_parsed if not problems else None

    def _attempt3_allowed(self, original: str) -> bool:
        """Попытка 3 пропускается, если пара маркеров пересекает границу
        предложения (§4.6 п.3)."""
        for sentence in [s for s in _SENTENCE_SPLIT.split(original) if s.strip()]:
            if not _markers_balanced(sentence):
                return False
        return True

    # ------------------------------------------------------------------ итоги

    def _soft_flags(self, parsed: Any, ctx: _PECtx) -> list[str]:
        text = str(parsed["text"])
        flags = run_soft_checks(
            text=text,
            original=ctx.original,
            draft=ctx.draft,
            glossary=ctx.glossary,
            source_lang="",
            max_edit_ratio=self._mtpe.max_edit_ratio,
            skip_s2=ctx.is_draft_bad,
        )
        self._edit_ratios.append(
            edit_ratio(norm_text(text_without_markers(text)), norm_text(ctx.draft))
        )
        for edit in parsed.get("edits", []) or []:
            if isinstance(edit, dict):
                etype = str(edit.get("type", ""))
                self._edit_types[etype] = self._edit_types.get(etype, 0) + 1
        if "heavy_edit" in flags:
            self._heavy_ids.append(ctx.seg_id)
        if "numbers_mismatch" in flags:
            self._numbers_ids.append(ctx.seg_id)
        return flags

    def _finalize_accepted(
        self,
        ctx: _PECtx,
        parsed: Any,
        flags: list[str],
        attempts: int,
        result: PostEditPhaseResult,
    ) -> dict[str, Any]:
        text, edits = str(parsed["text"]), parsed.get("edits", []) or []
        self._attempts_dist[attempts] = self._attempts_dist.get(attempts, 0) + 1
        result.processed_count += 1
        if "heavy_edit" in flags:
            result.heavy_edit_count += 1
        row = self._append_final(ctx.seg, text, edits, flags, attempts)
        if edits or flags:
            self._append_quality(ctx.seg, text, edits, flags, attempts)
        return row

    def _finalize_rejected(
        self, ctx: _PECtx, attempts: int, result: PostEditPhaseResult
    ) -> dict[str, Any]:
        has_structural = any(close != "" for _open, close in ctx.seg.get("markup_map", []))
        self._attempts_dist[attempts] = self._attempts_dist.get(attempts, 0) + 1
        if ctx.is_draft_bad or has_structural:
            raise PostEditStopError(
                "pe_unrecoverable",
                {"profile": "postedit", "segment_ids": [ctx.seg_id]},
            )
        flags = ["pe_rejected", "markup_dropped"]
        result.rejected_count += 1
        self._rejected_ids.append(ctx.seg_id)
        self._dropped_ids.append(ctx.seg_id)
        row = self._append_final(ctx.seg, ctx.draft, [], flags, attempts)
        self._append_quality(ctx.seg, ctx.draft, [], flags, attempts)
        return row

    # ------------------------------------------------------------------ запись

    def _append_final(
        self, seg: dict[str, Any], text: str, edits: list[Any], flags: list[str], attempts: int
    ) -> dict[str, Any]:
        row = {
            "segment_id": seg["segment_id"],
            "text": text,
            "draft": str(seg["draft"]),
            "edits": edits,
            "flags": flags,
            "attempts": attempts,
            "model": self._profile.model,
            "prompt_version": self._prompt_version,
            "key": seg["key"],
            "ms": 0,
            "tokens": 0,
        }
        _append_jsonl(self._final_path, row)
        return row

    def _append_quality(
        self, seg: dict[str, Any], text: str, edits: list[Any], flags: list[str], attempts: int
    ) -> None:
        _append_jsonl(
            self._quality_path,
            {
                "segment_id": seg["segment_id"],
                "draft": str(seg["draft"]),
                "final": text,
                "edits": edits,
                "flags": flags,
                "attempt": attempts,
                "prompt_version": self._prompt_version,
                "model": self._profile.model,
                "timestamp": int(time.time()),
            },
        )

    # ------------------------------------------------------------------ остановки и отчёт

    def _system_stop_check(self, result: PostEditPhaseResult) -> None:
        total = result.processed_count + result.rejected_count
        if total == 0:
            return
        rejected_ratio = result.rejected_count / total
        heavy_ratio = result.heavy_edit_count / total
        if rejected_ratio > self._mtpe.max_rejected_ratio:
            raise PostEditStopError(
                "pe_reject_ratio_exceeded",
                {
                    "ratio": rejected_ratio,
                    "limit": self._mtpe.max_rejected_ratio,
                    "profile": "postedit",
                    "segment_ids": list(self._rejected_ids),
                },
            )
        if heavy_ratio > self._mtpe.max_heavy_edit_ratio:
            raise PostEditStopError(
                "pe_overedit",
                {
                    "ratio": heavy_ratio,
                    "limit": self._mtpe.max_heavy_edit_ratio,
                    "profile": "postedit",
                    "segment_ids": list(self._heavy_ids),
                },
            )

    def _write_report(
        self,
        source_lang: str,
        target_lang: str,
        glossary: Glossary | None,
        segment_count: int,
    ) -> None:
        report = {
            "source_lang": source_lang,
            "target_lang": target_lang,
            "segment_count": segment_count,
            "edit_types": dict(sorted(self._edit_types.items())),
            "attempts_distribution": {str(k): v for k, v in sorted(self._attempts_dist.items())},
            "pe_rejected": list(self._rejected_ids),
            "heavy_edit": list(self._heavy_ids),
            "markup_dropped": list(self._dropped_ids),
            "numbers_mismatch": list(self._numbers_ids),
            "edit_ratio_median": _percentile(self._edit_ratios, 0.5),
            "edit_ratio_p95": _percentile(self._edit_ratios, 0.95),
            "glossary": "present" if glossary is not None else "absent",
        }
        self._report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


# ------------------------------------------------------------------ чистые функции


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _safe_json(content: str) -> Any:
    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None


def _pe_key(draft: str, model: str, prompt_version: str, fingerprint: str) -> str:
    payload = f"{draft}\x00{model}\x00{prompt_version}\x00{fingerprint}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Дописать строку в .jsonl и зафиксировать на диске (fsync, как M3)."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _markers_balanced(text: str) -> bool:
    """Маркеры сбалансированы: нет пары, пересекающей границу предложения."""
    stack: list[int] = []
    for match in _ANY_MARKER.finditer(text):
        lead, num, trail = match.group(1), int(match.group(2)), match.group(3)
        if lead:
            if not stack or stack[-1] != num:
                return False
            stack.pop()
        elif not trail:
            stack.append(num)
    return not stack


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight
