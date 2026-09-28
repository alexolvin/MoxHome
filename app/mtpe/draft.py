"""Черновик тракта M (MTPE M3, §7): MT-модель переводит сегменты в черновик.

Состав сегментов — ровно тот, что переводит тракт T (тракт A: `read_epub` →
`Segment[]`, общая функция отбора, копии нет, §7 п.1). Сегмент без букв —
`passthrough` (`draft = src_plain`, в LLM не отправляется, §7 п.2). Строки (по
`\\n`) и длинные строки (по предложениям L1 исходного языка, §7 п.4) — отдельные
запросы; черновик собирается через `\\n`, предложения — одним пробелом.

Прогрев (§4.6) — один раз, только если есть незавершённые сегменты (повторный
запуск завершённой фазы — 0 вызовов LLM). Проверка модели (§7 п.7) — остановка
`model_mismatch`; повторы — только транспортные (§7 п.8). Флаг `draft_bad`
(§7 п.9). Запись `drafts.jsonl` — сразу после черновика, с `fsync`; сырые ответы
— в `raw/`; возобновление — по ключу идемпотентности (§4.8).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tiktoken

from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.epub.reader import read_epub
from app.languages.detect import detect_text
from app.languages.nlp import load_nlp
from app.languages.registry import PackSpec, load_registry
from app.llm.mt import MTCompletionsClient
from app.llm.prompts import PromptResolver
from app.mtpe.markup import encode, has_letters
from app.storage.jobs_repo import JobRepository

#: Сид детектора языка (детерминированность draft_bad, аналог D7).
_DETECT_SEED = 0
#: Подстрока-маркер эха шаблона (MTPE §7 п.9).
_ECHO_MARKER = "Translate this from"


@dataclass
class DraftPhaseResult:
    """Итоги прогона фазы черновика (накопительные счётчики)."""

    segment_count: int = 0
    processed_count: int = 0
    passthrough_count: int = 0
    draft_bad_count: int = 0
    request_count: int = 0
    resumed_count: int = 0


@dataclass(frozen=True)
class _DraftContext:
    """Контекст прогона (снимок задания): шаблон, имена, версия, отпечаток, пара."""

    template: str
    src_name: str
    tgt_name: str
    prompt_version: str
    prompt_task: str
    fingerprint: str
    source_lang: str
    target_lang: str
    pair: str
    min_words: int


class DraftPhase:
    def __init__(self, settings: Settings, repo: JobRepository, job_id: str) -> None:
        self._settings = settings
        self._repo = repo
        self._job_id = job_id
        self._client = MTCompletionsClient(settings, repo, job_id)
        self._mt = settings.llm.profile("mt")
        self._job_dir = settings.job_dir(job_id)
        self._m_dir = self._job_dir / "m"
        self._segments_path = self._m_dir / "segments.jsonl"
        self._drafts_path = self._m_dir / "drafts.jsonl"
        self._registry = load_registry(Path(settings.languages.root))
        self._nlp: Any = None
        self._enc: Any = None

    # ------------------------------------------------------------------ публичное

    def run(
        self,
        source_path: str,
        source_lang: str,
        target_lang: str,
        work_dir: str | None = None,
    ) -> DraftPhaseResult:
        work = Path(work_dir) if work_dir is not None else self._job_dir / "unpack"
        book = read_epub(Path(source_path), work, self._settings)
        return self.run_book(book, source_lang, target_lang)

    def run_book(
        self, book: Any, source_lang: str, target_lang: str
    ) -> DraftPhaseResult:
        """Ядро фазы для готовой книги (тракт A: `read_epub` → `Book`). Точка
        входа для M6 (воркер) и для тестов на синтетической книге."""
        ctx = self._build_context(source_lang, target_lang)
        segments = self._collect_segments(book, ctx)
        self._m_dir.mkdir(parents=True, exist_ok=True)
        self._write_segments(segments)

        done = self._load_done()
        pending = [s for s in segments if done.get(str(s["segment_id"])) != s["key"]]
        result = DraftPhaseResult(
            segment_count=len(segments),
            resumed_count=len(segments) - len(pending),
        )
        if pending:
            # Прогрев фазы (§4.6) — перед первой LLM-операцией; повторный запуск
            # завершённой фазы (pending пусто) — без прогрева (0 вызовов, M3-5).
            ping = ctx.template.format(src_name=ctx.src_name, tgt_name=ctx.tgt_name, text="ping")
            self._client.warmup(ping)
        for seg in pending:
            self._process_segment(seg, ctx, result)
        return result

    # ------------------------------------------------------------------ внутреннее

    def _build_context(self, source_lang: str, target_lang: str) -> _DraftContext:
        src_pack = self._registry.get(source_lang)
        tgt_pack = self._registry.get(target_lang)
        src_name = src_pack.mt_name if src_pack is not None else None
        tgt_name = tgt_pack.mt_name if tgt_pack is not None else None
        if not src_name or not tgt_name:
            raise ConfigurationError(
                f"mt_pe недоступен для пары {source_lang}>{target_lang} (нет mt_name в pack.yaml)",
                details={"source": source_lang, "target": target_lang},
            )
        prompt = PromptResolver(self._settings.prompts_dir).resolve(
            self._mt.prompt_task or "mt_draft", source_lang, target_lang, pair_required=False
        )
        return _DraftContext(
            template=prompt.user_template,
            src_name=src_name,
            tgt_name=tgt_name,
            prompt_version=f"v{prompt.version}",
            prompt_task=self._mt.prompt_task or "mt_draft",
            fingerprint=_profile_fingerprint(self._mt),
            source_lang=source_lang,
            target_lang=target_lang,
            pair=f"{source_lang}>{target_lang}",
            min_words=self._settings.mtpe.lang_check_min_words,
        )

    def _collect_segments(self, book: Any, ctx: _DraftContext) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for chapter in book.chapters:
            for segment in chapter.segments:
                enc = encode(segment.marked_text)
                rows.append(
                    {
                        "segment_id": segment.segment_id,
                        "spine_idx": chapter.spine_index,
                        "order": segment.index_global,
                        "src_encoded": enc.src_encoded,
                        "src_plain": enc.src_plain,
                        "markup_map": [[open_t, close_t] for open_t, close_t in enc.markup_map],
                        "key": _segment_key(
                            enc.src_plain,
                            self._mt.model,
                            ctx.prompt_task,
                            ctx.fingerprint,
                            ctx.pair,
                        ),
                    }
                )
        rows.sort(key=lambda row: row["order"])
        return rows

    def _write_segments(self, segments: list[dict[str, Any]]) -> None:
        tmp = self._segments_path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for row in segments:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(self._segments_path)

    def _load_done(self) -> dict[str, str]:
        if not self._drafts_path.is_file():
            return {}
        done: dict[str, str] = {}
        with self._drafts_path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                row = json.loads(line)
                done[str(row["segment_id"])] = str(row["key"])
        return done

    def _process_segment(
        self, seg: dict[str, Any], ctx: _DraftContext, result: DraftPhaseResult
    ) -> None:
        seg_id = str(seg["segment_id"])
        src_plain = str(seg["src_plain"])
        if not has_letters(src_plain):
            _append_jsonl(
                self._drafts_path,
                {
                    "segment_id": seg_id,
                    "draft": src_plain,
                    "flags": ["passthrough"],
                    "split": 0,
                    "key": seg["key"],
                    "model": "",
                    "prompt_version": "",
                    "ms": 0,
                    "tokens": 0,
                },
            )
            result.processed_count += 1
            result.passthrough_count += 1
            return

        line_drafts: list[str] = []
        truncated = False
        request_count = 0
        tokens_acc = 0
        ms_acc = 0
        for line_index, line in enumerate(src_plain.split("\n")):
            translated: list[str] = []
            for piece_index, piece in enumerate(self._pieces_for_line(line, ctx)):
                prompt = ctx.template.format(
                    src_name=ctx.src_name, tgt_name=ctx.tgt_name, text=piece
                )
                label = f"{seg_id[:12]}_L{line_index}_P{piece_index}"
                mt_result = self._client.complete(prompt, self._max_tokens(piece), label)
                translated.append(mt_result.text)
                request_count += 1
                tokens_acc += mt_result.usage.total
                ms_acc += mt_result.ms
                if mt_result.finish_reason == "length":
                    truncated = True
            line_drafts.append(" ".join(translated).strip())
        draft = "\n".join(line_drafts)

        flags: list[str] = []
        if _is_draft_bad(draft, src_plain, ctx, truncated):
            flags.append("draft_bad")
        _append_jsonl(
            self._drafts_path,
            {
                "segment_id": seg_id,
                "draft": draft,
                "flags": flags,
                "split": request_count,
                "key": seg["key"],
                "model": self._mt.model,
                "prompt_version": ctx.prompt_version,
                "ms": ms_acc,
                "tokens": tokens_acc,
            },
        )
        result.processed_count += 1
        result.request_count += request_count
        if "draft_bad" in flags:
            result.draft_bad_count += 1

    def _pieces_for_line(self, line: str, ctx: _DraftContext) -> list[str]:
        max_seg = self._mt.max_segment_tokens or 0
        if max_seg <= 0 or self._token_count(line) <= max_seg:
            return [line]
        doc = self._source_nlp(ctx.source_lang)(line)
        sentences = [s.text for s in doc.sents if s.text.strip()]
        # Одно предложение длиннее порога отправляется целиком (не дробим дальше).
        return sentences or [line]

    def _source_nlp(self, source_lang: str) -> Any:
        if self._nlp is None:
            pack: PackSpec = self._registry[source_lang]
            self._nlp = load_nlp(pack)
        return self._nlp

    def _token_count(self, text: str) -> int:
        if self._enc is None:
            self._enc = tiktoken.get_encoding(self._mt.token_encoding or "cl100k_base")
        return len(self._enc.encode(text))

    def _max_tokens(self, piece: str) -> int:
        ratio = self._mt.max_tokens_ratio or 0.0
        return math.ceil(self._token_count(piece) * ratio) + 32


# ------------------------------------------------------------------ чистые функции


def _profile_fingerprint(profile: Any) -> str:
    """Отпечаток профиля mt (MTPE §4.8): поля, влияющие на ответ, кроме `base_url`
    и ключа (ключ не в профиле). Модель и задача промпта — отдельно в ключе."""
    data = {
        "temperature": profile.temperature,
        "top_k": profile.top_k,
        "max_tokens_ratio": profile.max_tokens_ratio,
        "max_segment_tokens": profile.max_segment_tokens,
        "stop": list(profile.stop),
        "extra_body": profile.extra_body,
    }
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def _segment_key(
    src_plain: str, model: str, prompt_task: str, fingerprint: str, pair: str
) -> str:
    payload = f"{src_plain}\x00{model}\x00{prompt_task}\x00{fingerprint}\x00{pair}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_draft_bad(
    draft: str, src_plain: str, ctx: _DraftContext, truncated: bool
) -> bool:
    """Флаг `draft_bad` (MTPE §7 п.9): любое из условий."""
    if not draft.strip():
        return True
    if _ECHO_MARKER in draft:
        return True
    if any(line.strip().startswith(f"{ctx.src_name}:") for line in draft.splitlines()):
        return True
    if truncated:
        return True
    if len(src_plain.split()) >= ctx.min_words:
        detection = detect_text(draft, seed=_DETECT_SEED)
        if detection.language is not None and detection.language != ctx.target_lang:
            return True
    return False


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Дописать строку в .jsonl и зафиксировать на диске (fsync, MTPE §7 п.10)."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
