"""Конвейер задания (E3/E5): исполнение этапов с идемпотентностью (E3.6).

- validating: ридер (E2) → validation.json (вкл. TOC/metadata/сегменты по главам
  — E5.3 «реальные единицы») + детерминированное определение исходного языка
  (L0.4/L0.5, app/languages/detect.py) → секция language_detection; подозрительная
  книга ИЛИ язык требует подтверждения → NEEDS_CONFIRMATION; принятый язык —
  в jobs.source_language; пара (принятый исходный → целевой) перепроверяется
  резолвером (L0.2): unsupported → FAILED с перечнем недостающих (L0.3).
- planning: исходный язык подтверждён (invariant, L0.5) + пара исполняема →
  план перевода (режим задания + движковый submit, E5.2) → plan.json.
- translating: адаптер TranslationEngine (E5.1, D17): `.partial` → внутренняя
  валидация → EPUBCheck → бэкап → `os.replace` (E5.6); прогресс в реальные
  единицы (E5.3); fill-сбои атрибутируются по группе и пишутся в errors.log и
  fill_failures.json (E5.4); кэш — в каталоге задания, изолирован по модели (D15);
  сбой тракт T → `LLMUnavailableError` (endpoint/model в сообщении, T-E5-9);
  кооперативные исключения контроля (E3.8) не заглатываются.
- analyzing / generating: в E3 — явные плейсхолдеры (реализация — L1/S-этапы).
- validating_output: ридер (E2) + 10-пунктный QC (E5.8) + выравнивание
  оригинал ↔ перевод (E6, app/epub/aligner.py) → quality.json +
  alignment_report.json; доля выравнивания — в статусе задания
  (jobs.alignment_coverage), ниже порога — предупреждение (E6.5).
- learning (S1–S5, ТЗ_03): материалы ТОЛЬКО по выровненному подмножеству E6
  и тексту ПОСЛЕ L3. Реализовано S1: отбор лексики (детерминированная
  стадия → LLM-карточки → детерминированная валидация → плотность ≤ N на
  1000 слов) → vocabulary_cards.json + секция s1 в quality.json. Стадия
  всегда существует: learning не включён (learning_level None) — no-op.

Идемпотентность (E3.6): результат этапа адресуется ключом
hash(входные данные + конфигурация + версия промпта); повтор с тем же ключом —
без повторного исполнения (повторный перевод не порождается, E3-3).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from spacy.language import Language

from app.adapters.epub_engine import (
    EngineUsage,
    EpubTranslatorEngine,
    FillFailedEvent,
    GlossaryEntry,
    SubmitMode,
    TranslationRequest,
)
from app.adapters.epub_engine.atomic import atomic_finalize
from app.adapters.epub_engine.epubcheck import run_epubcheck
from app.core.config import Settings
from app.core.errors import AppError, LLMUnavailableError, OutputValidationFailed
from app.core.logging import QUALITY_LOGGER
from app.domain.book import Book
from app.domain.glossary import (
    PARADIGM_VERIFIED,
    Glossary,
    TermMismatch,
)
from app.domain.job import Job
from app.domain.vocabulary import LexisCandidate
from app.epub import aligner
from app.epub.aligner import (
    AlignedPair,
    AlignmentReport,
    DocAlignment,
    ExtraBlock,
    UnalignedSegment,
)
from app.epub.reader import read_epub
from app.epub.rebuild import block_bytes, patch_block, rebuild_epub
from app.glossary.consistency import check_consistency
from app.glossary.fix import (
    FixOutcome,
    FixSelection,
    GlossaryFixer,
    UnverifiedTerm,
    select_fixable,
)
from app.glossary.service import BookGlossaryBundle, ensure_book_glossary, glossary_prefix
from app.glossary.store import BookStore, book_key_of
from app.languages.cards import load_card_schema
from app.languages.cefr import CefrSource
from app.languages.detect import LanguageReport, detect_book
from app.languages.frequency import normalize_text
from app.languages.nlp import load_nlp, nlp_report
from app.languages.phenomena import PhenomenaTaxonomy, for_stage, load_phenomena
from app.languages.registry import PackSpec, aliases_map, load_registry
from app.languages.resolver import PairReport, PairResolver, PairStatus
from app.learning import SUBSTAGE_LEVELS, card_export, study_book
from app.learning.cards import assemble_card, check_vocab_cards
from app.learning.grammar import check_grammar_notes, reference_explanation
from app.learning.mwe import MweCandidate, check_mwe_records, select_mwe_candidates
from app.learning.selection import select_lexis_candidates
from app.learning.study import validate_exercise, validate_question, validate_summary
from app.llm.analysis import AnalysisClient
from app.llm.tasks import task as llm_task
from app.workers.controls import LeaseLost, WorkerCancelled, WorkerPaused
from app.workers.qc import QcInput, run_qc

if TYPE_CHECKING:
    from app.storage.jobs_repo import JobRepository

logger = logging.getLogger("app.worker.pipeline")

#: Источник глоссария (E5.5): хук для L2 (и тестов). В E5 — None (глоссария нет);
#: наполнение — этап L2.
GlossaryProvider = Callable[[Job], tuple[GlossaryEntry, ...]]


def _language_detection_payload(report: LanguageReport) -> dict[str, Any]:
    """Секция validation.json: решение по исходному языку (L0.4/L0.5/L0.6)."""
    return {
        "dc_language": report.dc_language,
        "detected": report.detected,
        "confidence": report.confidence,
        "probabilities": report.probabilities,
        "decision": report.decision.value,
        "accepted_language": report.accepted_language,
        "needs_confirmation": report.needs_confirmation,
        "reasons": list(report.reasons),
        "multilingual": asdict(report.multilingual) if report.multilingual is not None else None,
    }


def _pair_unsupported_message(pair: PairReport) -> str:
    """Сообщение об unsupported-паре: ТОЧНЫЙ перечень недостающего (L0.3)."""
    return (
        f"языковая пара {pair.source} → {pair.target} не поддерживается (L0.2); "
        f"не хватает: {'; '.join(pair.missing)}"
    )


#: Префикс подписи фазы главы (E5.3) — ЕДИНЫЙ источник: колбэк прогресса
#: ``run_translating`` и отображение прогресса UI (``app.web.views``).
CHAPTER_LABEL_PREFIX = "глава "


def translation_phases(validation: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Последовательность фаз движка (E5.3) — (подпись, документ):
    TOC? → metadata? → главы. ЕДИНЫЙ источник: колбэк прогресса
    ``run_translating`` и отображение прогресса UI (U1) строят фазы
    одинаково — из ``validation.json``."""
    phases: list[tuple[str, str]] = []
    if bool(validation.get("toc_present")):
        phases.append(("TOC", "(TOC)"))
    if bool(validation.get("meta_present")):
        phases.append(("metadata", "(OPF metadata)"))
    chapter_count = int(validation["stats"]["chapter_count"])
    chapter_docs = [str(x) for x in validation.get("chapter_documents", [])]
    for index in range(chapter_count):
        doc = (
            chapter_docs[index]
            if index < len(chapter_docs)
            else f"chapter{index + 1}"
        )
        phases.append((f"{CHAPTER_LABEL_PREFIX}{index + 1} / {chapter_count}", doc))
    return phases


class Pipeline:
    def __init__(
        self, settings: Settings, glossary_provider: GlossaryProvider | None = None
    ) -> None:
        self._settings = settings
        self._glossary_provider = glossary_provider

    # ------------------------------------------------------------- служебное

    def input_hash(self, job: Job) -> str:
        """Ключ идемпотентности: hash(вход + конфигурация + версия промпта) (E3.6)."""
        payload = "\n".join(
            (job.input_sha256, job.config_sha256, job.prompt_version, job.target_language)
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _skipped(self, repo: JobRepository, job: Job, stage: str) -> bool:
        result = repo.get_stage_result(job.id, stage)
        if result is not None and result.input_hash == self.input_hash(job):
            logger.info("этап %s: ключ идемпотентности совпал — повтор не требуется", stage)
            return True
        return False

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_json(self, path: Path) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    def _submit_mode(self, mode: str) -> SubmitMode:
        """Режим задания → движковый submit (E5.2): translation → REPLACE,
        bilingual → `translation.submit_mode_bilingual` из YAML."""
        if mode == "translation":
            return SubmitMode.REPLACE
        return SubmitMode(self._settings.translation.submit_mode_bilingual)

    def _resolve_pair(self, source: str | None, target: str) -> PairReport | None:
        """Статус пары (L0.2) по фактическим ресурсам. None — исходный язык не задан."""
        if source is None:
            return None
        resolver = PairResolver(
            registry_root=self._settings.languages_root,
            prompts_root=self._settings.prompts_dir,
        )
        return resolver.resolve_pair(source, target)

    def _nlp_section(self, code: str | None) -> dict[str, Any] | None:
        """Секция `nlp` отчёта задания (L1.2): версии пайплайна исходного языка
        зафиксированы в отчёте каждого задания (статическая фиксация —
        docs/HANDOFF.md). None — язык неизвестен или пайплайн не объявлен."""
        if code is None:
            return None
        pack: PackSpec | None = load_registry(self._settings.languages_root).get(code)
        if pack is None or pack.spacy_model is None:
            return None
        return nlp_report(pack)

    def _book_glossary_bundle(self, repo: JobRepository, job: Job) -> BookGlossaryBundle:
        """L2: глоссарий версии книги (сборка на первом задании, загрузка далее;
        L2.8: привязка к хэшу исходного файла). Загружает книгу и пары пакетов
        (исходный — подтверждённый язык задания, L0.5)."""
        settings = self._settings
        book = read_epub(Path(job.source_path), self._settings.job_dir(job.id) / "unpack", settings)
        registry = load_registry(settings.languages_root)
        source_code = job.source_language
        if source_code is None:
            raise AppError(
                "подтверждённый исходный язык не задан (L0.5) — глоссарий не строится"
            )
        source_pack = registry.get(source_code)
        target_pack = registry.get(job.target_language)
        if source_pack is None or target_pack is None:
            raise AppError(
                f"язык не найден в реестре: {source_code} / {job.target_language}"
            )
        return ensure_book_glossary(
            settings,
            repo,
            job,
            book=book,
            source_pack=source_pack,
            target_pack=target_pack,
        )

    def _book_glossary(self, job: Job) -> Glossary | None:
        """L2.6: глоссарий версии книги из хранилища (None — не собран)."""
        store = BookStore(self._settings.books_dir)
        return store.load_glossary(book_key_of(job.source_path), job.input_sha256)

    def _glossary_consistency(
        self, job: Job, alignment: aligner.AlignmentReport
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """L2.6: проверка согласованности перевода по глоссарию (детекция
        только; список расхождений потребляет L3). Возвращает (секция
        quality.json, предупреждения). Секция None — глоссарий выключен
        или глоссарий книги не собран."""
        warnings: list[str] = []
        if not self._settings.glossary.enabled:
            return None, warnings
        book_glossary = self._book_glossary(job)
        if book_glossary is None:
            return None, warnings
        consistency = check_consistency(book_glossary, alignment)
        report_dict = consistency.to_dict()
        report_dict["job_id"] = job.id
        self._write_json(self._settings.job_dir(job.id) / "glossary_consistency.json", report_dict)
        store = BookStore(self._settings.books_dir)
        store.save_mismatches(book_key_of(job.source_path), job.input_sha256, report_dict)
        section: dict[str, Any] = {
            "terms_checked": consistency.terms_checked,
            "occurrences_checked": consistency.occurrences_checked,
            "consistent_occurrences": consistency.consistent_occurrences,
            "consistent_share": round(consistency.share, 6),
            "min_share": self._settings.glossary.consistency_min_share,
            "mismatches": len(consistency.mismatches),
            "report": "glossary_consistency.json",
        }
        if (
            consistency.occurrences_checked
            and consistency.share < self._settings.glossary.consistency_min_share
        ):
            first_m = [f"{m.term} ({m.segment_id})" for m in consistency.mismatches][:10]
            warnings.append(
                f"L2: согласованность терминов ниже порога: "
                f"{consistency.consistent_occurrences}/{consistency.occurrences_checked} "
                f"({consistency.share:.1%} < "
                f"{self._settings.glossary.consistency_min_share:.1%}): "
                + "; ".join(first_m)
            )
        return section, warnings

    def validation(self, job_id: str) -> dict[str, Any]:
        """validation.json задания (L0: секция language_detection; воркер строит
        причину NEEDS_CONFIRMATION из неё же — единый источник, Rule 8)."""
        return self._read_json(self._settings.job_dir(job_id) / "validation.json")

    # ------------------------------------------------------------------ этапы

    def run_validating(self, repo: JobRepository, job: Job) -> bool:
        """Читает книгу ридером (E2.5: до любого LLM) + определяет исходный язык (L0.4).

        Возвращает: книга подозрительна ИЛИ язык требует подтверждения (L0.5) —
        воркер переводит в NEEDS_CONFIRMATION. Принятый язык пишется в
        jobs.source_language; пара (принятый → целевой) перепроверяется резолвером:
        unsupported — AppError → FAILED с точным перечнем недостающего (L0.2/L0.3).
        """
        if self._skipped(repo, job, "validating"):
            payload = self._read_json(self._settings.job_dir(job.id) / "validation.json")
            detection = payload.get("language_detection") or {}
            return bool(payload["suspicious"]) or bool(detection.get("needs_confirmation"))
        book = read_epub(
            Path(job.source_path), self._settings.job_dir(job.id) / "unpack", self._settings
        )
        report = detect_book(
            book, self._settings, aliases=aliases_map(load_registry(self._settings.languages_root))
        )
        if report.accepted_language is not None:
            repo.set_source_language(job.id, report.accepted_language)
        payload = {
            "title": book.title,
            "language": book.language,
            # L0: решение по исходному языку (детектор + dc:language, L0.5).
            "language_detection": _language_detection_payload(report),
            # L1.2: версии NLP-пайплайна исходного языка (None — язык не принят).
            "nlp": self._nlp_section(report.accepted_language),
            "suspicious": book.suspicious,
            "suspicious_reasons": list(book.suspicious_reasons),
            "stats": asdict(book.stats),
            # E5.3: «реальные единицы» — фазы движка и сегменты по главам
            # (последовательность on_progress: TOC? → metadata? → главы).
            "toc_present": book.toc_entries > 0,
            "toc_entries": book.toc_entries,
            "meta_present": book.has_translatable_metadata,
            "per_chapter_segments": [len(chapter.segments) for chapter in book.chapters],
            "chapter_documents": [chapter.document_path for chapter in book.chapters],
        }
        artifact = self._settings.job_dir(job.id) / "validation.json"
        self._write_json(artifact, payload)
        repo.record_stage_result(job.id, "validating", self.input_hash(job), str(artifact))
        if book.suspicious or report.needs_confirmation:
            return True
        pair = self._resolve_pair(report.accepted_language, job.target_language)
        if pair is not None and pair.status is PairStatus.UNSUPPORTED:
            raise AppError(_pair_unsupported_message(pair))
        return False

    def run_planning(self, repo: JobRepository, job: Job) -> None:
        if self._skipped(repo, job, "planning"):
            return
        # source_language пишется в БД стадией validating (или confirm) — снимок,
        # переданный в метод, может быть старше этой записи: читаем свежую строку
        # (воркер и так делает require на каждом этапе).
        job = repo.require(job.id)
        # L0.5: исходный язык подтверждён (детектор+dc на validating либо выбор
        # пользователя при confirm). Без него план строить нельзя (fail closed).
        if job.source_language is None:
            raise AppError(
                "исходный язык не подтверждён (L0.5): подтверждение задания должно "
                "включать выбор исходного языка"
            )
        pair = self._resolve_pair(job.source_language, job.target_language)
        if pair is not None and pair.status is PairStatus.UNSUPPORTED:
            raise AppError(_pair_unsupported_message(pair))
        validation = self._read_json(self._settings.job_dir(job.id) / "validation.json")
        profile = self._settings.llm.profile("translation")
        plan = {
            "target_language": job.target_language,
            "mode": job.mode,
            "submit_mode": self._submit_mode(job.mode).value,
            # L1.2: версии NLP-пайплайна подтверждённого исходного языка.
            "nlp": self._nlp_section(job.source_language),
            "glossary": {"limit_chars": self._settings.limits.glossary_max_user_prompt_chars},
            "engine": {
                "max_group_tokens": profile.max_group_tokens,
                "concurrency": profile.concurrency,
                "max_retries": profile.max_retries,
                "timeout_seconds": profile.timeout_seconds,
                # D15: ключ кэша движка не включает модель → каталоги по моделям.
                "cache_root": str(self._settings.job_dir(job.id) / "engine_cache"),
                "translation_model": self._settings.llm.profile("translation").model,
                "fill_model": self._settings.llm.profile("fill").model,
            },
            "book": validation["stats"],
        }
        plan_path = self._settings.job_dir(job.id) / "plan.json"
        self._write_json(plan_path, plan)
        repo.record_stage_result(job.id, "planning", self.input_hash(job), str(plan_path))

    def run_translating(
        self, repo: JobRepository, job: Job, control_check: Callable[[], None]
    ) -> int:
        """Перевод адаптером TranslationEngine (E5.1, D17): `.partial` → валидация
        → EPUBCheck → бэкап → `os.replace` (E5.6). Возвращает число fill-сбоев."""
        if self._skipped(repo, job, "translating"):
            failures_file = self._settings.job_dir(job.id) / "fill_failures.json"
            if failures_file.is_file():
                return int(self._read_json(failures_file)["count"])
            return 0

        settings = self._settings
        job_dir = self._settings.job_dir(job.id)
        target = job_dir / "target.epub"
        validation = self._read_json(job_dir / "validation.json")
        per_chapter = [int(x) for x in validation["per_chapter_segments"]]
        total_segments = sum(per_chapter)

        # E5.3: последовательность фаз движка — on_progress вызывается после
        # каждой непустой фазы (concurrency=1: детерминированно).
        # Единый источник — translation_phases (его же читает UI, U1).
        phases = translation_phases(validation)

        engine = EpubTranslatorEngine(settings, job_dir)
        fill_failures: list[dict[str, Any]] = []
        flush_tokens, on_progress, on_fill_failed = self._make_callbacks(
            repo,
            job,
            engine,
            phases=phases,
            per_chapter=per_chapter,
            total_segments=total_segments,
            fill_failures=fill_failures,
            control_check=control_check,
        )

        # L2: глоссарий версии книги. Явный provider (тесты/E5) — приоритет;
        # иначе — встроенная сборка (кандидаты детерминированно, LLM — только
        # аннотация; L2.8: хранилище с привязкой к хэшу исходного файла).
        glossary: tuple[GlossaryEntry, ...] = ()
        style_prefix: str = ""
        if self._glossary_provider is not None:
            glossary = tuple(self._glossary_provider(job))
        elif settings.glossary.enabled:
            bundle = self._book_glossary_bundle(repo, job)
            glossary = tuple(
                GlossaryEntry(
                    term=term.term, translation=term.translation, occurrences=term.occurrences
                )
                for term in bundle.glossary.terms
            )
            style_prefix = glossary_prefix(bundle.style)

        request = TranslationRequest(
            source_path=Path(job.source_path),
            target_path=target,
            target_language=job.target_language,
            mode=self._submit_mode(job.mode),
            glossary=glossary,
            glossary_limit_chars=settings.limits.glossary_max_user_prompt_chars,
            glossary_prefix=style_prefix,
            on_progress=on_progress,
            on_fill_failed=on_fill_failed,
        )
        try:
            result = engine.translate_book(request)
        except (LeaseLost, WorkerCancelled, WorkerPaused):
            # Кооперативный контроль (E3.8): исключение поднимается нашим
            # control_check внутри on_progress движка — это НЕ сбой тракт T;
            # propagate к воркеру как есть (CANCELLED / PAUSED / потеря арены).
            raise
        except Exception as exc:
            # Сбой тракт T (недоступный/невалидный эндпоинт и пр.): причина —
            # в сообщении (T-E5-9); ключ API в сообщение не входит.
            base_url, model, _ = settings.llm.endpoint("translation")
            raise LLMUnavailableError(
                f"тракт T недоступен (endpoint {base_url}, model {model}): {exc}"
            ) from exc
        finally:
            flush_tokens()

        self._write_json(
            job_dir / "fill_failures.json",
            {"count": len(fill_failures), "events": fill_failures},
        )
        self._write_json(
            job_dir / "translation_result.json",
            {
                "mode": job.mode,
                "submit_mode": request.mode.value,
                "progress_call_count": result.progress_call_count,
                "glossary": {
                    "total_terms": result.glossary_total_terms,
                    "kept_terms": result.glossary_kept_terms,
                    "dropped_terms": result.glossary_dropped_terms,
                    "limit_chars": settings.limits.glossary_max_user_prompt_chars,
                    "prompt_chars": result.glossary_prompt_chars,
                    "truncated": result.glossary_dropped_terms > 0,
                },
                "epubcheck": {
                    "version": result.epubcheck_version,
                    "available": result.validation_unavailable_reason is None,
                    "errors": result.epubcheck_errors,
                    "warnings": result.epubcheck_warnings,
                    "unavailable_reason": result.validation_unavailable_reason,
                },
                "backup_path": str(result.backup_path) if result.backup_path is not None else None,
            },
        )
        repo.record_stage_result(
            job.id,
            "translating",
            self.input_hash(job),
            str(target),
            tokens=result.usage.total,
        )
        return len(fill_failures)

    def _make_callbacks(
        self,
        repo: JobRepository,
        job: Job,
        engine: EpubTranslatorEngine,
        *,
        phases: list[tuple[str, str]],
        per_chapter: list[int],
        total_segments: int,
        fill_failures: list[dict[str, Any]],
        control_check: Callable[[], None],
    ) -> tuple[Callable[[], None], Callable[[float], None], Callable[[FillFailedEvent], None]]:
        """Служебные колбэки этапа translating (E4.7/E5.3/E5.4): flush токенов,
        «реальные единицы» прогресса, атрибуция fill-сбоев по группе."""
        state = {"calls": 0}
        prev_usage = EngineUsage()

        def _flush_tokens() -> None:
            # E4.7 (тракт T): приросты накопительных счётчиков адаптера.
            nonlocal prev_usage
            now = engine.usage
            repo.add_tokens(
                job.id, "translation", now.translation_total - prev_usage.translation_total
            )
            repo.add_profile_tokens(
                job.id,
                "translation",
                now.translation_input - prev_usage.translation_input,
                now.translation_input_cache - prev_usage.translation_input_cache,
                now.translation_output - prev_usage.translation_output,
            )
            repo.add_tokens(job.id, "fill", now.fill_total - prev_usage.fill_total)
            repo.add_profile_tokens(
                job.id,
                "fill",
                now.fill_input - prev_usage.fill_input,
                now.fill_input_cache - prev_usage.fill_input_cache,
                now.fill_output - prev_usage.fill_output,
            )
            prev_usage = now

        def _phase_at(calls: int) -> tuple[str, str]:
            """Активная фаза по числу завершённых (index = завершённые, 0-based)."""
            return phases[min(calls, len(phases) - 1)]

        def _on_progress(progress: float) -> None:
            repo.set_progress(job.id, progress)
            _flush_tokens()
            state["calls"] += 1
            label, doc = _phase_at(state["calls"] - 1)
            if label.startswith(CHAPTER_LABEL_PREFIX):
                k = int(label.split()[1])
                units = f"{label}, сегментов {sum(per_chapter[:k])} / {total_segments} ({doc})"
            else:
                units = f"{label} ({doc})"
            logger.info("прогресс: %s (дробь движка %.3f)", units, progress)
            # Кооперативные проверки между группами (E3.8).
            control_check()

        def _record_fill_failed(event: FillFailedEvent) -> None:
            # E5.4: событие движка не несёт документ/позицию — атрибутируем по
            # текущей группе (число вызовов прогресса; concurrency=1 —
            # детерминированно). errors.log — через logger.error (E3.9).
            label, doc = _phase_at(state["calls"])
            fill_failures.append(
                {
                    "phase": label,
                    "document": doc,
                    "error_message": event.error_message,
                    "retried_count": event.retried_count,
                    "over_maximum_retries": event.over_maximum_retries,
                }
            )
            logger.error(
                "fill не удался (фаза %s, %s; retried=%d, over_maximum=%s): %s",
                label,
                doc,
                event.retried_count,
                event.over_maximum_retries,
                event.error_message,
            )

        return _flush_tokens, _on_progress, _record_fill_failed

    def run_analyzing(self, repo: JobRepository, job: Job) -> None:
        if self._skipped(repo, job, "analyzing"):
            return
        self._placeholder_stage(repo, job, "analyzing", "анализ текста (L1)")

    def run_generating(self, repo: JobRepository, job: Job) -> None:
        if self._skipped(repo, job, "generating"):
            return
        self._placeholder_stage(repo, job, "generating", "генерация learning-материалов (S1–S5)")

    def _placeholder_stage(self, repo: JobRepository, job: Job, stage: str, purpose: str) -> None:
        payload = {
            "stage": stage,
            "status": "placeholder",
            "note": (
                f"E3: этап-заглушка, {purpose} реализуется на соответствующих этапах; "
                "здесь проверяется механизм задания (состояния, идемпотентность, resume)"
            ),
        }
        stage_path = self._settings.job_dir(job.id) / f"{stage}.json"
        self._write_json(stage_path, payload)
        repo.record_stage_result(job.id, stage, self.input_hash(job), str(stage_path))

    def run_validating_output(self, repo: JobRepository, job: Job) -> list[str]:
        """Проверка результата: ридер (E2) + 10-пунктный QC (E5.8). Возвращает предупреждения."""
        if self._skipped(repo, job, "validating_output"):
            quality = self._read_json(self._settings.job_dir(job.id) / "quality.json")
            return list(quality["warnings"])
        job_dir = self._settings.job_dir(job.id)
        target = job_dir / "target.epub"
        if not target.is_file():
            raise OutputValidationFailed("выходной файл не создан: target.epub")
        if not zipfile.is_zipfile(target):
            raise OutputValidationFailed("выходной файл не является ZIP-архивом")
        tr_file = job_dir / "translation_result.json"
        tr: dict[str, Any] = self._read_json(tr_file) if tr_file.is_file() else {}
        quality, warnings = self._measure_output(
            repo, job, job_dir / "out_unpack", dict(tr.get("epubcheck", {}))
        )
        self._write_json(job_dir / "quality.json", quality)
        quality_logger = logging.getLogger(QUALITY_LOGGER)
        quality_logger.info("QC выхода: %s", json.dumps(quality, ensure_ascii=False))
        repo.record_stage_result(
            job.id,
            "validating_output",
            self.input_hash(job),
            str(job_dir / "quality.json"),
        )
        return warnings

    def _measure_output(
        self,
        repo: JobRepository,
        job: Job,
        out_unpack: Path,
        epubcheck: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Измерение выхода: ридер (E2) + QC (E5.8) + выравнивание (E6) +
        согласованность по глоссарию (L2.6) → (dict quality.json, warnings).

        Единый механизм (Rule 8): используют и `run_validating_output`, и
        пересчёт после правки L3 (D61/L3.9: выравнивание и детерминированный
        QC выполняются заново по исправленному файлу).
        """
        job_dir = self._settings.job_dir(job.id)
        target = job_dir / "target.epub"
        try:
            out_book = read_epub(target, out_unpack, self._settings)
        except Exception as exc:
            raise OutputValidationFailed(f"выходной EPUB не читается ридером: {exc}") from exc
        try:
            src_book = read_epub(Path(job.source_path), job_dir / "unpack", self._settings)
        except Exception as exc:
            raise OutputValidationFailed(f"исходный EPUB не читается ридером: {exc}") from exc

        validation = self._read_json(job_dir / "validation.json")
        in_stats = validation["stats"]
        checks = {
            "chapter_count_in": in_stats["chapter_count"],
            "chapter_count_out": out_book.stats.chapter_count,
            "segment_count_out": out_book.stats.segment_count,
            "chapters_preserved": in_stats["chapter_count"] == out_book.stats.chapter_count,
        }
        if not checks["chapters_preserved"]:
            raise OutputValidationFailed(
                f"число глав изменилось: {checks['chapter_count_in']} "
                f"-> {checks['chapter_count_out']}"
            )

        tr_file = job_dir / "translation_result.json"
        tr: dict[str, Any] = self._read_json(tr_file) if tr_file.is_file() else {}
        ff_file = job_dir / "fill_failures.json"
        fill_events: list[dict[str, Any]] = (
            [dict(x) for x in self._read_json(ff_file)["events"]] if ff_file.is_file() else []
        )

        report, qc_warnings = run_qc(
            QcInput(
                settings=self._settings,
                source_path=Path(job.source_path),
                target_path=target,
                src_book=src_book,
                out_book=out_book,
                submit_mode=str(tr.get("submit_mode", "APPEND_BLOCK")),
                chapter_count=in_stats["chapter_count"],
                per_chapter_segments=[int(x) for x in validation["per_chapter_segments"]],
                toc_present=bool(validation["toc_present"]),
                meta_present=bool(validation["meta_present"]),
                progress_call_count=int(tr.get("progress_call_count", 0)),
                fill_failures=fill_events,
                glossary=dict(tr.get("glossary", {})),
                epubcheck=epubcheck,
            )
        )
        warnings = list(qc_warnings)
        if out_book.stats.segment_count == 0:
            warnings.append("в выходной книге нет сегментов")

        # E6: выравнивание оригинал ↔ перевод — карта (адресация для L3) +
        # доля в статусе задания; ниже порога — предупреждение (E6.5).
        alignment = aligner.build_alignment_report(
            book=src_book,
            source_path=Path(job.source_path),
            target_path=target,
            submit_mode=str(tr.get("submit_mode", "APPEND_BLOCK")),
            block_tags=frozenset(self._settings.segmentation.block_tags),
            min_coverage=self._settings.alignment.min_coverage,
            identical_whitelist=self._settings.alignment.identical_whitelist,
        )
        self._write_json(job_dir / "alignment_report.json", aligner.report_to_dict(alignment))
        repo.set_alignment_coverage(job.id, alignment.coverage)
        if alignment.coverage < self._settings.alignment.min_coverage:
            first = [
                f"{u.doc} {u.source_xpath} ({u.reason})"
                for d in alignment.docs
                for u in d.unaligned
            ][:10]
            warnings.append(
                f"E6: выравнивание ниже порога: {alignment.aligned_count}/{alignment.total} "
                f"({alignment.coverage:.1%} < {self._settings.alignment.min_coverage:.1%}): "
                + "; ".join(first)
            )

        # L2.6: проверка согласованности перевода по глоссарию (детекция
        # только; текст не правится — список расхождений потребляет L3).
        consistency_section, consistency_warnings = self._glossary_consistency(job, alignment)
        warnings.extend(consistency_warnings)

        quality = {
            **report,
            "checks": checks,
            # Все предупреждения (QC + E6) — статус задания и quality.json
            # согласованы (в E5 предупреждение «нет сегментов» в отчёт не
            # попадало — дефект, исправлен в E6).
            "warnings": warnings,
            "alignment": {
                "coverage": alignment.coverage,
                "aligned": alignment.aligned_count,
                "total": alignment.total,
                "unaligned": alignment.unaligned_count,
                "extra_blocks": alignment.extra_count,
                "min_coverage": self._settings.alignment.min_coverage,
                "report": "alignment_report.json",
            },
        }
        if consistency_section is not None:
            quality["glossary_consistency"] = consistency_section
        return quality, warnings

    # ------------------------------------------------------------------ L3

    def run_glossary_fix(self, repo: JobRepository, job: Job) -> list[str]:
        """L3 (ТЗ_02 §L3): правка перечисленных L2 расхождений в выходном EPUB.

        Стадия всегда существует (D63): глоссарий или правка выключены — no-op
        (L3.10: обнаружение и сообщение при этом не выключаются). Возвращает
        ФИНАЛЬНЫЕ предупреждения — по ним воркер выбирает COMPLETED /
        COMPLETED_WITH_WARNINGS.

        Порядок (ТЗ L3): отбор (3 условия, L3.2) → порог объёма ДО любых LLM-
        вызовов (L3-6: превышение — остановка с отчётом) → правка анализом +
        5-пунктная валидация (L3.3/L3.4) → патч по XPath + атомарная
        пересборка (L3.5) → пересчёт выравнивания и QC по исправленному файлу
        (L3.9/D61) → отчёт l3_fix_report.json + запись каждой правки в
        quality.log (L3.7). Идемпотентность: ключ стадии включает хэш
        глоссария (L3.8); повтор по исправленному файлу — 0 расхождений/правок
        (расхождения переопределяются измерением ТЕКУЩЕГО файла, а не
        доверяют устаревшему отчёту L2).
        """
        settings = self._settings
        job_dir = self._settings.job_dir(job.id)
        glossary_cfg = settings.glossary

        if not glossary_cfg.enabled or not glossary_cfg.fix_enabled:
            reason = (
                "глоссарий выключен"
                if not glossary_cfg.enabled
                else "правка L3 выключена (L3.10)"
            )
            return self._l3_no_op(repo, job, job_dir, enabled=False, reason=reason)

        job = repo.require(job.id)
        store = BookStore(settings.books_dir)
        book_key = book_key_of(job.source_path)
        glossary = store.load_glossary(book_key, job.input_sha256)
        l2_path = job_dir / "glossary_consistency.json"
        if glossary is None:
            return self._l3_no_op(
                repo,
                job,
                job_dir,
                enabled=True,
                reason="глоссарий книги не собран — расхождений нет",
            )

        target = job_dir / "target.epub"
        if not target.is_file() or not zipfile.is_zipfile(target):
            raise OutputValidationFailed("L3: target.epub отсутствует или не ZIP-архив")

        # L3.10/D63: обнаружение никогда не выключается — свежее измерение
        # ТЕКУЩЕГО файла (выравнивание + согласованность L2; Rule 8 — тот же
        # механизм, что в validating_output). Первый прогон подтверждает
        # измеренное validating_output; повтор/возобновление — единственный
        # источник правды: исправленный файл даёт 0 расхождений (L3.8),
        # неисправленный — актуальный список.
        quality, _ = self._measure_output(
            repo, job, job_dir / "out_unpack_l3", self._l3_epubcheck_now(job_dir)
        )
        self._write_json(job_dir / "quality.json", quality)

        l2_now = self._read_json(l2_path)
        selection, mismatches = self._l3_selection(job_dir, glossary, l2_now)
        cap = glossary_cfg.fix_max_segment_share
        share = self._l3_share(selection)

        # L3-6: порог объёма — ДО любого LLM-вызова: превышение = системная
        # проблема (неверный глоссарий/модель/детектор) → остановка с отчётом,
        # массовая переписка книги недопустима.
        if share > cap:
            self._l3_halt(repo, job, job_dir, selection=selection, share=share, cap=cap)

        glossary_path = store.glossary_path(book_key, job.input_sha256)
        glossary_hash = (
            hashlib.sha256(glossary_path.read_bytes()).hexdigest()
            if glossary_path.is_file()
            else ""
        )
        key = hashlib.sha256(f"{self.input_hash(job)}|{glossary_hash}".encode()).hexdigest()

        outcomes, changed_docs = self._l3_apply(repo, job, job_dir, glossary, selection)
        applied = [o for o in outcomes if o.applied]
        epubcheck_result = self._l3_rebuild(job_dir, applied, changed_docs)

        # L3.9/D61: пересчёт по исправленному файлу — только если файл
        # изменился; «до»-метрика — из свежего измерения выше (L3-1).
        editable_before = self._editable_share(glossary, l2_path, l2_now)
        if applied and epubcheck_result is not None:
            quality, _ = self._measure_output(
                repo, job, job_dir / "out_unpack_l3", epubcheck_result
            )
            self._write_json(job_dir / "quality.json", quality)
            editable_after = self._editable_share(glossary, l2_path)
        else:
            editable_after = editable_before
        final_warnings = self._l3_finalize_quality(
            job_dir,
            selection=selection,
            outcomes=outcomes,
            share=share,
            cap=cap,
            attempted=len(outcomes),
            applied=len(applied),
            editable_after=editable_after,
        )

        l3bak = job_dir / "target.epub.l3bak"
        report = {
            "stage": "L3",
            "enabled": True,
            "halted": False,
            "fixable_segment_share": round(share, 6),
            "max_segment_share": cap,
            "total_segments": selection.total_segments,
            "mismatches_input": len(mismatches),
            "fixable": [asdict(f) for f in selection.fixable],
            "unverified_terms": [asdict(u) for u in selection.unverified_terms],
            "unverified_share": self._unverified_share(selection.unverified_terms),
            "attempted": len(outcomes),
            "applied": len(applied),
            "rejected": len(outcomes) - len(applied),
            "fixes": [asdict(o) for o in outcomes],
            "editable_share_before": editable_before,
            "editable_share_after": editable_after,
            "glossary_hash": glossary_hash,
            "idempotency_key": key,
            "backup": l3bak.name if l3bak.exists() else None,
            "rebuild": epubcheck_result,
        }
        self._write_json(job_dir / "l3_fix_report.json", report)
        repo.record_stage_result(job.id, "glossary_fix", key, str(job_dir / "l3_fix_report.json"))
        return final_warnings

    # ------------------------------------------------------------- learning (S1–S5)

    def run_learning(self, repo: JobRepository, job: Job) -> list[str]:
        """LEARNING (S1–S5, ТЗ_03): learning-материалы по тексту ПОСЛЕ L3.

        Стадия всегда существует: learning не включён (learning_level None) —
        no-op (аналог L3.10). Подстадии имеют собственные ключи
        идемпотентности (stage_results: "s1_vocabulary", "s2_mwe"; далее
        S3–S5) и входят в уровень по таблице ТЗ_03 (SUBSTAGE_LEVELS):
        подстадия, не входящая в уровень, не исполняется и артефактов не
        выпускает. Возвращает ФИНАЛЬНЫЕ предупреждения — по ним воркер
        выбирает COMPLETED / COMPLETED_WITH_WARNINGS.
        """
        job = repo.require(job.id)
        job_dir = self._settings.job_dir(job.id)
        if job.learning_level is None:
            return self._learning_no_op(repo, job, job_dir)
        glossary_hash = self._learning_glossary_hash(job)
        key = hashlib.sha256(
            "|".join(
                (
                    self.input_hash(job),
                    glossary_hash,
                    job.learning_level or "",
                    job.learning_cefr or "",
                )
            ).encode()
        ).hexdigest()
        level = job.learning_level
        substages = (
            ("s1_vocabulary", SUBSTAGE_LEVELS["s1"], self._s1_vocabulary),
            ("s2_mwe", SUBSTAGE_LEVELS["s2"], self._s2_mwe),
            ("s3_grammar", SUBSTAGE_LEVELS["s3"], self._s3_grammar),
            ("s4_summary", SUBSTAGE_LEVELS["s4_summary"], self._s4_summary),
            ("s4_questions", SUBSTAGE_LEVELS["s4_questions"], self._s4_questions),
            ("s4_exercises", SUBSTAGE_LEVELS["s4_exercises"], self._s4_exercises),
            ("s5_assembly", SUBSTAGE_LEVELS["s5"], self._s5_assembly),
        )
        for stage_key, allowed_levels, run_substage in substages:
            if level not in allowed_levels:
                continue  # уровень не включает подстадию (таблица ТЗ_03)
            result = repo.get_stage_result(job.id, stage_key)
            if result is not None and result.input_hash == key:
                continue  # идемпотентность: подстадия с тем же ключом исполнена
            run_substage(repo, job, job_dir, key, glossary_hash)
        quality_path = job_dir / "quality.json"
        if quality_path.is_file():
            return list(self._read_json(quality_path)["warnings"])
        return []

    def _learning_context(
        self, job: Job
    ) -> tuple[Book, tuple[AlignedPair, ...], PackSpec, Path, Language, str]:
        """Общий контекст подстадий S1–S5: выровненное подмножество E6 +
        пакет и пайплайн языка-ИСТОЧНИКА (learning-материалы строятся по
        исходному тексту — как S1)."""
        settings = self._settings
        job_dir = self._settings.job_dir(job.id)
        alignment = self._l3_alignment(self._read_json(job_dir / "alignment_report.json"))
        pairs = tuple(p for d in alignment.docs for p in d.aligned)
        book = read_epub(Path(job.source_path), job_dir / "unpack", settings)
        registry = load_registry(settings.languages_root)
        source_code = job.source_language
        if source_code is None:
            raise AppError("подтверждённый исходный язык не задан (L0.5) — learning не строится")
        source_pack = registry.get(source_code)
        target_pack = registry.get(job.target_language)
        if source_pack is None or target_pack is None:
            raise AppError(f"язык не найден в реестре: {source_code} / {job.target_language}")
        pack_dir = settings.languages_root / source_pack.code
        nlp = load_nlp(source_pack)
        return book, pairs, source_pack, pack_dir, nlp, source_code

    def _s1_vocabulary(
        self, repo: JobRepository, job: Job, job_dir: Path, key: str, glossary_hash: str
    ) -> list[str]:
        """S1 (ТЗ_03): отбор лексики. Детерминированная стадия (S1.1) →
        LLM-заполнение/ранжирование по главам (S1.2, общее требование 5) →
        5-пунктная валидация (S1.5) → плотность ≤ N на 1000 слов (S1-3) →
        vocabulary_cards.json. Только выровненное подмножество E6."""
        settings = self._settings
        book, pairs, source_pack, pack_dir, nlp, source_code = self._learning_context(job)
        card_schema = load_card_schema(pack_dir, source_pack)
        cefr_source = CefrSource(source_pack, pack_dir)

        candidates = select_lexis_candidates(
            book=book, pairs=pairs, pack=source_pack, nlp=nlp, cefr_source=cefr_source
        )
        seg_word_count = {
            seg.segment_id: seg.word_count for ch in book.chapters for seg in ch.segments
        }
        total_words = sum(seg_word_count.get(p.segment_id, 0) for p in pairs)
        density_cap = settings.limits.vocabulary_max_cards_per_1000_words
        max_cards = math.floor(density_cap * total_words / 1000.0)

        # LLM: по главам (никогда не вся книга в запросе, общее требование 5).
        items, prompt_file, prompt_version = self._s1_llm_items(
            repo, job, book, candidates, source_code
        )
        _, model, _ = settings.llm.endpoint("analysis")

        seg_texts = {
            seg.segment_id: seg.plain_text for ch in book.chapters for seg in ch.segments
        }
        chapter_texts = {
            ch.chapter_id: "\n\n".join(seg.plain_text for seg in ch.segments)
            for ch in book.chapters
        }
        validated, rejected = check_vocab_cards(
            items=items,
            candidates={c.lemma: c for c in candidates},
            seg_texts=seg_texts,
            chapter_texts=chapter_texts,
            card_schema=card_schema,
            nlp=nlp,
        )
        # Дубли леммы в ответе LLM: первая карточка остаётся, остальные —
        # отклонение (дедупликация по лемме на всю книгу, S1.2).
        seen: set[str] = set()
        dedup_validated: list[tuple[dict[str, Any], LexisCandidate]] = []
        for item, candidate in validated:
            if candidate.lemma in seen:
                rejected.append(
                    {"lemma": candidate.lemma, "reasons": ["дублирующая карточка леммы (S1.2)"]}
                )
                continue
            seen.add(candidate.lemma)
            dedup_validated.append((item, candidate))
        # Ранжирование: полезность (LLM) → частота → Zipf → лемма
        # (детерминированный тотальный порядок).
        dedup_validated.sort(
            key=lambda pair: (
                -int(pair[0].get("usefulness", 0)),
                -pair[1].occurrences,
                pair[1].zipf,
                pair[1].lemma.casefold(),
            )
        )
        selected = dedup_validated[:max_cards]
        not_selected = len(dedup_validated) - len(selected)

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cards: list[dict[str, Any]] = [
            assemble_card(
                candidate,
                item,
                user_level=job.learning_level,
                user_cefr=job.learning_cefr,
                source_language=source_code,
                target_language=job.target_language,
                glossary_hash=glossary_hash,
                prompt_file=prompt_file,
                prompt_version=prompt_version,
                model=model,
                profile="analysis",
                timestamp=timestamp,
                cefr_distance=settings.limits.cefr_out_of_range_distance,
            )
            for item, candidate in selected
        ]
        issued = len(cards)
        generated = sum(1 for c in cards if c["example_generated"])
        book_example_share = round((issued - generated) / issued, 6) if issued else None
        density_actual = round(issued * 1000.0 / total_words, 6) if total_words else 0.0
        rejected_lemmas = {r["lemma"] for r in rejected}
        missing = [
            c.lemma for c in candidates if c.lemma not in seen and c.lemma not in rejected_lemmas
        ]

        report: dict[str, Any] = {
            "stage": "S1",
            "enabled": True,
            "source_language": source_code,
            "target_language": job.target_language,
            "user_level": job.learning_level,
            "user_cefr": job.learning_cefr,
            "book_words": total_words,
            "density_cap_per_1000": density_cap,
            "max_cards": max_cards,
            "candidates_total": len(candidates),
            "cards_issued": issued,
            "cards_rejected": len(rejected),
            "cards_not_selected": not_selected,
            "cards_missing": len(missing),
            "book_example_share": book_example_share,
            "density_actual": density_actual,
            "cefr_out_of_range": sum(1 for c in cards if c["cefr_out_of_range"]),
            "glossary_hash": glossary_hash,
            "prompt_file": prompt_file,
            "prompt_version": prompt_version,
            "model": model,
            "profile": "analysis",
            "timestamp": timestamp,
            "idempotency_key": key,
            "candidates": [asdict(c) for c in candidates],
            "cards": cards,
            "rejected": rejected,
            "missing": missing,
        }
        self._write_json(job_dir / "vocabulary_cards.json", report)
        final_warnings = self._s1_finalize_quality(
            job_dir, report=report, rejected=rejected
        )
        repo.record_stage_result(
            job.id, "s1_vocabulary", key, str(job_dir / "vocabulary_cards.json")
        )
        return final_warnings

    def _s1_llm_items(
        self,
        repo: JobRepository,
        job: Job,
        book: Book,
        candidates: tuple[LexisCandidate, ...],
        source_code: str,
    ) -> tuple[list[dict[str, Any]], str, int]:
        """S1.2: LLM-заполнение карточек по главам, батчами (конфиг).
        Возвращает (items, prompt_file, prompt_version) — промпт первого
        вызова (единый для всех батчей, E4-2)."""
        settings = self._settings
        client = AnalysisClient(settings, repo, job.id)
        spec = llm_task("vocabulary_card")
        batch_size = settings.limits.vocabulary_llm_batch
        by_chapter: dict[str, list[LexisCandidate]] = {}
        for candidate in candidates:
            by_chapter.setdefault(candidate.chapter_id, []).append(candidate)
        prompt_file, prompt_version = "", 0
        items: list[dict[str, Any]] = []
        for chapter in book.chapters:
            chapter_cands = by_chapter.get(chapter.chapter_id)
            if not chapter_cands:
                continue
            for start in range(0, len(chapter_cands), batch_size):
                batch = chapter_cands[start : start + batch_size]
                payload = {
                    "source_language": source_code,
                    "target_language": job.target_language,
                    "user_level": job.learning_level,
                    "user_cefr": job.learning_cefr,
                    "chapter_id": chapter.chapter_id,
                    "candidates": [
                        {
                            "lemma": c.lemma,
                            "word_form": c.word_form,
                            "pos": c.pos,
                            "occurrences": c.occurrences,
                            "segment_id": c.first_segment_id,
                            "source_sentence": c.example_sentence,
                            "frequency_band": c.frequency_band,
                            "cefr_level": c.cefr_level,
                        }
                        for c in batch
                    ],
                }
                outcome = client.run_task(
                    spec,
                    source_code,
                    job.target_language,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                )
                if prompt_file == "":
                    prompt_file, prompt_version = outcome.prompt.file, outcome.prompt.version
                raw_items = outcome.payload.get("items")
                if not isinstance(raw_items, list):
                    raise AppError("S1: ответ LLM — items отсутствует или не массив")
                for raw in raw_items:
                    if not isinstance(raw, dict):
                        raise AppError("S1: ответ LLM — элемент items не объект")
                    items.append(raw)
        return items, prompt_file, prompt_version

    def _s1_finalize_quality(
        self, job_dir: Path, *, report: dict[str, Any], rejected: list[dict[str, Any]]
    ) -> list[str]:
        """Секция s1 в quality.json + ФИНАЛЬНЫЕ предупреждения стадии (QC +
        отклонённые карточки + нулевой отбор — S1-4/S1-6/S1-7)."""
        quality_path = job_dir / "quality.json"
        quality = self._read_json(quality_path) if quality_path.is_file() else {}
        base_warnings = list(quality.get("warnings", ()))
        quality["s1"] = {
            "enabled": True,
            "book_words": report["book_words"],
            "max_cards": report["max_cards"],
            "candidates": report["candidates_total"],
            "issued": report["cards_issued"],
            "rejected": report["cards_rejected"],
            "not_selected": report["cards_not_selected"],
            "missing": report["cards_missing"],
            "book_example_share": report["book_example_share"],
            "density_actual": report["density_actual"],
            "report": "vocabulary_cards.json",
        }
        stage_warnings: list[str] = []
        if report["candidates_total"] == 0:
            stage_warnings.append("S1: лексические кандидаты не отобраны (0) — карточек нет")
        if rejected:
            first = [f"{r['lemma']}: {r['reasons'][0]}" for r in rejected[:10]]
            stage_warnings.append(f"S1: {len(rejected)} карточек отклонено: " + "; ".join(first))
        quality["warnings"] = [*base_warnings, *stage_warnings]
        self._write_json(quality_path, quality)
        quality_logger = logging.getLogger(QUALITY_LOGGER)
        quality_logger.info("QC после S1: %s", json.dumps(quality, ensure_ascii=False))
        return list(quality["warnings"])

    def _s2_mwe(
        self, repo: JobRepository, job: Job, job_dir: Path, key: str, glossary_hash: str
    ) -> list[str]:
        """S2 (ТЗ_03): идиомы, устойчивые сочетания и языкоспецифичные MWE
        в терминах таксономии языка-источника. Детерминированный предотбор
        (шаблоны + повторяемость, app/learning/mwe.py) → LLM-объяснение по
        главам (mwe_note, S2.3: выдвижение моделью запрещено) →
        детерминированная валидация (S2.6) → mwe_records.json + секция s2 в
        quality.json. Только выровненное подмножество E6 (исходный текст)."""
        settings = self._settings
        book, pairs, source_pack, pack_dir, nlp, source_code = self._learning_context(job)
        phenomena = load_phenomena(pack_dir)
        taxonomy = PhenomenaTaxonomy(phenomena)
        candidates = select_mwe_candidates(
            book=book,
            pairs=pairs,
            pack=source_pack,
            nlp=nlp,
            phenomena=for_stage(phenomena, "s2"),
        )

        items, prompt_file, prompt_version = self._s2_llm_items(
            repo, job, book, candidates, source_code
        )
        _, model, _ = settings.llm.endpoint("analysis")
        seg_texts = {
            seg.segment_id: seg.plain_text for ch in book.chapters for seg in ch.segments
        }

        def _normalize(text: str) -> str:
            return normalize_text(text, source_pack.normalization)

        candidates_by_surface = {
            normalize_text(candidate.surface, source_pack.normalization): candidate
            for candidate in candidates
        }
        validated, rejected = check_mwe_records(
            items=items,
            candidates=candidates_by_surface,
            taxonomy=taxonomy,
            seg_texts=seg_texts,
            normalize=_normalize,
        )
        # Дубли поверхностной формы в ответе LLM: первая запись остаётся,
        # остальные — отклонение (дедупликация на всю книгу, механизм S1.2).
        seen: set[str] = set()
        dedup_validated: list[tuple[Mapping[str, Any], MweCandidate]] = []
        for item, candidate in validated:
            surface_key = candidate.surface.casefold()
            if surface_key in seen:
                rejected.append(
                    {
                        "surface": candidate.surface,
                        "phenomenon_id": candidate.phenomenon_id,
                        "reasons": ["дублирующая запись MWE (S2.4)"],
                    }
                )
                continue
            seen.add(surface_key)
            dedup_validated.append((item, candidate))
        # Детерминированное ранжирование (LLM в задаче mwe_note ранжирование
        # не выдаёт — только подтверждение/объяснение): вхождения → id →
        # поверхность.
        dedup_validated.sort(
            key=lambda pair: (
                -pair[1].occurrences,
                pair[1].phenomenon_id,
                pair[1].surface.casefold(),
            )
        )

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        records: list[dict[str, Any]] = [
            {
                "surface": candidate.surface,
                "parts": [[part.text, part.index] for part in candidate.parts],
                "canonical": str(item.get("canonical", "")),
                "phenomenon_id": str(item.get("phenomenon_id", "")),
                "meaning": str(item.get("meaning", "")),
                "translation": str(item.get("translation", "")),
                "unit_lemma": candidate.unit_lemma,
                "occurrences": candidate.occurrences,
                "chapter_id": candidate.chapter_id,
                "segment_id": candidate.first_segment_id,
                "segment_ids": list(candidate.segment_ids),
                "source_text_hash": candidate.source_text_hash,
                "example_sentence": candidate.example_sentence,
                "source_language": source_code,
                "target_language": job.target_language,
                "user_level": job.learning_level,
                "user_cefr": job.learning_cefr,
                "glossary_hash": glossary_hash,
                "prompt_file": prompt_file,
                "prompt_version": prompt_version,
                "model": model,
                "profile": "analysis",
                "timestamp": timestamp,
            }
            for item, candidate in dedup_validated
        ]
        seg_word_count = {
            seg.segment_id: seg.word_count for ch in book.chapters for seg in ch.segments
        }
        total_words = sum(seg_word_count.get(p.segment_id, 0) for p in pairs)
        report: dict[str, Any] = {
            "stage": "S2",
            "enabled": True,
            "source_language": source_code,
            "target_language": job.target_language,
            "user_level": job.learning_level,
            "user_cefr": job.learning_cefr,
            "book_words": total_words,
            "candidates_total": len(candidates),
            "records_issued": len(records),
            "records_rejected": len(rejected),
            "separable_records": sum(1 for record in records if record["unit_lemma"]),
            "glossary_hash": glossary_hash,
            "prompt_file": prompt_file,
            "prompt_version": prompt_version,
            "model": model,
            "profile": "analysis",
            "timestamp": timestamp,
            "idempotency_key": key,
            "candidates": [asdict(candidate) for candidate in candidates],
            "records": records,
            "rejected": rejected,
        }
        self._write_json(job_dir / "mwe_records.json", report)
        final_warnings = self._s2_finalize_quality(
            job_dir, report=report, rejected=rejected
        )
        repo.record_stage_result(job.id, "s2_mwe", key, str(job_dir / "mwe_records.json"))
        return final_warnings

    def _s2_llm_items(
        self,
        repo: JobRepository,
        job: Job,
        book: Book,
        candidates: tuple[MweCandidate, ...],
        source_code: str,
    ) -> tuple[list[dict[str, Any]], str, int]:
        """S2.3: LLM-объяснение кандидатов по главам, батчами (конфиг).
        Возвращает (items, prompt_file, prompt_version) — промпт первого
        вызова (единый для всех батчей, E4-2)."""
        settings = self._settings
        client = AnalysisClient(settings, repo, job.id)
        spec = llm_task("mwe_note")
        batch_size = settings.limits.mwe_llm_batch
        by_chapter: dict[str, list[MweCandidate]] = {}
        for candidate in candidates:
            by_chapter.setdefault(candidate.chapter_id, []).append(candidate)
        prompt_file, prompt_version = "", 0
        items: list[dict[str, Any]] = []
        for chapter in book.chapters:
            chapter_cands = by_chapter.get(chapter.chapter_id)
            if not chapter_cands:
                continue
            for start in range(0, len(chapter_cands), batch_size):
                batch = chapter_cands[start : start + batch_size]
                payload = {
                    "source_language": source_code,
                    "target_language": job.target_language,
                    "user_level": job.learning_level,
                    "user_cefr": job.learning_cefr,
                    "chapter_id": chapter.chapter_id,
                    "candidates": [
                        {
                            "surface": candidate.surface,
                            "parts": [[part.text, part.index] for part in candidate.parts],
                            "phenomenon_id": candidate.phenomenon_id,
                            "unit_lemma": candidate.unit_lemma,
                            "occurrences": candidate.occurrences,
                            "segment_id": candidate.first_segment_id,
                            "source_sentence": candidate.example_sentence,
                        }
                        for candidate in batch
                    ],
                }
                outcome = client.run_task(
                    spec,
                    source_code,
                    job.target_language,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                )
                if prompt_file == "":
                    prompt_file, prompt_version = outcome.prompt.file, outcome.prompt.version
                raw_items = outcome.payload.get("items")
                if not isinstance(raw_items, list):
                    raise AppError("S2: ответ LLM — items отсутствует или не массив")
                for raw in raw_items:
                    if not isinstance(raw, dict):
                        raise AppError("S2: ответ LLM — элемент items не объект")
                    items.append(raw)
        return items, prompt_file, prompt_version

    def _s2_finalize_quality(
        self, job_dir: Path, *, report: dict[str, Any], rejected: list[dict[str, Any]]
    ) -> list[str]:
        """Секция s2 в quality.json + ФИНАЛЬНЫЕ предупреждения стадии (QC +
        отклонённые записи + нулевой отбор — S2-1/S2-2)."""
        quality_path = job_dir / "quality.json"
        quality = self._read_json(quality_path) if quality_path.is_file() else {}
        base_warnings = list(quality.get("warnings", ()))
        quality["s2"] = {
            "enabled": True,
            "book_words": report["book_words"],
            "candidates": report["candidates_total"],
            "issued": report["records_issued"],
            "rejected": report["records_rejected"],
            "separable": report["separable_records"],
            "report": "mwe_records.json",
        }
        stage_warnings: list[str] = []
        if report["candidates_total"] == 0:
            stage_warnings.append("S2: MWE-кандидаты не отобраны (0) — записей нет")
        if rejected:
            first = [f"{r['surface']}: {r['reasons'][0]}" for r in rejected[:10]]
            stage_warnings.append(f"S2: {len(rejected)} записей отклонено: " + "; ".join(first))
        quality["warnings"] = [*base_warnings, *stage_warnings]
        self._write_json(quality_path, quality)
        quality_logger = logging.getLogger(QUALITY_LOGGER)
        quality_logger.info("QC после S2: %s", json.dumps(quality, ensure_ascii=False))
        return list(quality["warnings"])

    @staticmethod
    def _s3_split_candidates(
        candidates: tuple[MweCandidate, ...],
        book: Book,
        max_per_chapter: int,
    ) -> tuple[list[MweCandidate], list[MweCandidate]]:
        """Лимит ≤K на главу (S3.3): топ-K по (-вхождения, id, поверхность);
        приоритет = повторяемость в книге (уровень пользователя в таксономии
        для грамматических явлений не заведён — честный предел). Остальные —
        not_selected (фактический счёт для отчёта)."""
        by_chapter: dict[str, list[MweCandidate]] = {}
        for candidate in candidates:
            by_chapter.setdefault(candidate.chapter_id, []).append(candidate)
        selected: list[MweCandidate] = []
        not_selected: list[MweCandidate] = []
        for chapter in book.chapters:
            chapter_cands = sorted(
                by_chapter.get(chapter.chapter_id, ()),
                key=lambda c: (-c.occurrences, c.phenomenon_id, c.surface.casefold()),
            )
            selected.extend(chapter_cands[:max_per_chapter])
            not_selected.extend(chapter_cands[max_per_chapter:])
        return selected, not_selected

    @staticmethod
    def _s3_explain_once(
        selected: list[MweCandidate],
    ) -> tuple[list[MweCandidate], dict[str, MweCandidate]]:
        """Объяснить один раз (S3.4): первый отобранный кандидат явления
        (порядок книги) — развёрнуто (LLM); остальные — краткие ссылки."""
        detailed: list[MweCandidate] = []
        first_by_pid: dict[str, MweCandidate] = {}
        for candidate in selected:
            if candidate.phenomenon_id not in first_by_pid:
                first_by_pid[candidate.phenomenon_id] = candidate
                detailed.append(candidate)
        return detailed, first_by_pid

    @staticmethod
    def _s3_build_notes(
        selected: list[MweCandidate],
        first_by_pid: dict[str, MweCandidate],
        detailed_by_surface: dict[str, tuple[Mapping[str, Any], MweCandidate]],
        rejected: list[dict[str, Any]],
        record: Callable[[MweCandidate, str, str, str | None], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Сборка записей: развёрнутая — пояснение LLM (первый кандидат
        явления); остальные — ссылки (S3.4). Развёрнутая отклонена — запись
        нет (уже в `rejected`); ссылка на отклонённую первую — сама
        отклоняется (ссылка некорректна)."""
        notes: list[dict[str, Any]] = []
        for candidate in selected:
            first = first_by_pid[candidate.phenomenon_id]
            if candidate is first:
                pair = detailed_by_surface.get(candidate.surface.casefold())
                if pair is None:
                    continue
                item, _ = pair
                notes.append(
                    record(candidate, str(item.get("explanation", "")), "detailed", None)
                )
                continue
            if first.surface.casefold() not in detailed_by_surface:
                rejected.append(
                    {
                        "surface": candidate.surface,
                        "phenomenon_id": candidate.phenomenon_id,
                        "reasons": [
                            "первая заметка явления отклонена — ссылка некорректна (S3.4)"
                        ],
                    }
                )
                continue
            notes.append(
                record(
                    candidate,
                    reference_explanation(),
                    "reference",
                    first.first_segment_id,
                )
            )
        return notes

    def _s3_grammar(
        self, repo: JobRepository, job: Job, job_dir: Path, key: str, glossary_hash: str
    ) -> list[str]:
        """S3 (ТЗ_03): контекстные короткие пояснения грамматических явлений
        в терминах таксономии языка-источника. Детерминированный предотбор по
        морфологическим признакам (phenomena.yaml, скоп `stages: [s3]`,
        app/learning/mwe.py) → лимит ≤K заметок на главу (S3.3) → явление
        объясняется ОДИН раз развёрнуто (grammar_note), далее — краткая
        ссылка (S3.4) → детерминированная валидация (S3.6) →
        grammar_notes.json + секция s3 в quality.json."""
        settings = self._settings
        book, pairs, source_pack, pack_dir, nlp, source_code = self._learning_context(job)
        phenomena = load_phenomena(pack_dir)
        taxonomy = PhenomenaTaxonomy(phenomena)
        candidates = select_mwe_candidates(
            book=book,
            pairs=pairs,
            pack=source_pack,
            nlp=nlp,
            phenomena=for_stage(phenomena, "s3"),
        )

        max_per_chapter = settings.limits.grammar_max_notes_per_chapter
        selected, not_selected = self._s3_split_candidates(
            candidates, book, max_per_chapter
        )
        detailed, first_by_pid = self._s3_explain_once(selected)

        items, prompt_file, prompt_version = self._s3_llm_items(
            repo, job, book, tuple(detailed), source_code
        )
        _, model, _ = settings.llm.endpoint("analysis")
        seg_texts = {
            seg.segment_id: seg.plain_text for ch in book.chapters for seg in ch.segments
        }

        def _normalize(text: str) -> str:
            return normalize_text(text, source_pack.normalization)

        candidates_by_surface = {
            _normalize(candidate.surface): candidate for candidate in selected
        }
        validated, rejected = check_grammar_notes(
            items=items,
            candidates=candidates_by_surface,
            taxonomy=taxonomy,
            seg_texts=seg_texts,
            normalize=_normalize,
            max_chars=settings.limits.grammar_note_max_chars,
            target_language=job.target_language,
            seed=settings.language_detection.seed,
        )
        # Дубли поверхности в ответе LLM: первая заметка остаётся, остальные —
        # отклонение (дедупликация на всю книгу, механизм S1.2/S2.4).
        seen: set[str] = set()
        dedup_validated: list[tuple[Mapping[str, Any], MweCandidate]] = []
        for item, candidate in validated:
            surface_key = candidate.surface.casefold()
            if surface_key in seen:
                rejected.append(
                    {
                        "surface": candidate.surface,
                        "phenomenon_id": candidate.phenomenon_id,
                        "reasons": ["дублирующая заметка (S3.3)"],
                    }
                )
                continue
            seen.add(surface_key)
            dedup_validated.append((item, candidate))
        dedup_validated.sort(
            key=lambda pair: (
                -pair[1].occurrences,
                pair[1].phenomenon_id,
                pair[1].surface.casefold(),
            )
        )

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        detailed_by_surface = {
            candidate.surface.casefold(): (item, candidate)
            for item, candidate in dedup_validated
        }

        def _note_record(
            candidate: MweCandidate,
            explanation: str,
            note_type: str,
            reference_to: str | None,
        ) -> dict[str, Any]:
            return {
                "phenomenon_id": candidate.phenomenon_id,
                "surface": candidate.surface,
                "segment_id": candidate.first_segment_id,
                "chapter_id": candidate.chapter_id,
                "explanation": explanation,
                "note_type": note_type,
                "reference_to": reference_to,
                "occurrences": candidate.occurrences,
                "example_sentence": candidate.example_sentence,
                "source_text_hash": candidate.source_text_hash,
                "source_language": source_code,
                "target_language": job.target_language,
                "user_level": job.learning_level,
                "user_cefr": job.learning_cefr,
                "glossary_hash": glossary_hash,
                # Ссылка — детерминированная (LLM не участвует): версии пусты.
                "prompt_file": prompt_file if note_type == "detailed" else "",
                "prompt_version": prompt_version if note_type == "detailed" else 0,
                "model": model if note_type == "detailed" else "",
                "profile": "analysis" if note_type == "detailed" else "",
                "timestamp": timestamp,
            }

        notes = self._s3_build_notes(
            selected, first_by_pid, detailed_by_surface, rejected, _note_record
        )

        per_chapter_issued: dict[str, int] = {}
        for note in notes:
            per_chapter_issued[note["chapter_id"]] = (
                per_chapter_issued.get(note["chapter_id"], 0) + 1
            )
        seg_word_count = {
            seg.segment_id: seg.word_count for ch in book.chapters for seg in ch.segments
        }
        total_words = sum(seg_word_count.get(p.segment_id, 0) for p in pairs)
        report: dict[str, Any] = {
            "stage": "S3",
            "enabled": True,
            "source_language": source_code,
            "target_language": job.target_language,
            "user_level": job.learning_level,
            "user_cefr": job.learning_cefr,
            "book_words": total_words,
            "candidates_total": len(candidates),
            "notes_issued": len(notes),
            "notes_rejected": len(rejected),
            "notes_not_selected": len(not_selected),
            "detailed_notes": sum(1 for note in notes if note["note_type"] == "detailed"),
            "reference_notes": sum(1 for note in notes if note["note_type"] == "reference"),
            "max_notes_per_chapter": max_per_chapter,
            "per_chapter": dict(sorted(per_chapter_issued.items())),
            "glossary_hash": glossary_hash,
            "prompt_file": prompt_file,
            "prompt_version": prompt_version,
            "model": model,
            "profile": "analysis",
            "timestamp": timestamp,
            "idempotency_key": key,
            "candidates": [asdict(candidate) for candidate in candidates],
            "notes": notes,
            "rejected": rejected,
            "not_selected": [
                {"surface": c.surface, "phenomenon_id": c.phenomenon_id}
                for c in not_selected
            ],
        }
        self._write_json(job_dir / "grammar_notes.json", report)
        final_warnings = self._s3_finalize_quality(job_dir, report=report, rejected=rejected)
        repo.record_stage_result(
            job.id, "s3_grammar", key, str(job_dir / "grammar_notes.json")
        )
        return final_warnings

    def _s3_llm_items(
        self,
        repo: JobRepository,
        job: Job,
        book: Book,
        candidates: tuple[MweCandidate, ...],
        source_code: str,
    ) -> tuple[list[dict[str, Any]], str, int]:
        """S3.5: LLM-объяснение развёрнутых кандидатов по главам, батчами
        (конфиг). Возвращает (items, prompt_file, prompt_version) — промпт
        первого вызова (единый для всех батчей, E4-2)."""
        settings = self._settings
        client = AnalysisClient(settings, repo, job.id)
        spec = llm_task("grammar_note")
        batch_size = settings.limits.grammar_llm_batch
        by_chapter: dict[str, list[MweCandidate]] = {}
        for candidate in candidates:
            by_chapter.setdefault(candidate.chapter_id, []).append(candidate)
        prompt_file, prompt_version = "", 0
        items: list[dict[str, Any]] = []
        for chapter in book.chapters:
            chapter_cands = by_chapter.get(chapter.chapter_id)
            if not chapter_cands:
                continue
            for start in range(0, len(chapter_cands), batch_size):
                batch = chapter_cands[start : start + batch_size]
                payload = {
                    "source_language": source_code,
                    "target_language": job.target_language,
                    "user_level": job.learning_level,
                    "user_cefr": job.learning_cefr,
                    "chapter_id": chapter.chapter_id,
                    "max_explanation_chars": settings.limits.grammar_note_max_chars,
                    "candidates": [
                        {
                            "surface": candidate.surface,
                            "parts": [[part.text, part.index] for part in candidate.parts],
                            "phenomenon_id": candidate.phenomenon_id,
                            "occurrences": candidate.occurrences,
                            "segment_id": candidate.first_segment_id,
                            "source_sentence": candidate.example_sentence,
                        }
                        for candidate in batch
                    ],
                }
                outcome = client.run_task(
                    spec,
                    source_code,
                    job.target_language,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                )
                if prompt_file == "":
                    prompt_file, prompt_version = outcome.prompt.file, outcome.prompt.version
                raw_items = outcome.payload.get("items")
                if not isinstance(raw_items, list):
                    raise AppError("S3: ответ LLM — items отсутствует или не массив")
                for raw in raw_items:
                    if not isinstance(raw, dict):
                        raise AppError("S3: ответ LLM — элемент items не объект")
                    items.append(raw)
        return items, prompt_file, prompt_version

    def _s3_finalize_quality(
        self, job_dir: Path, *, report: dict[str, Any], rejected: list[dict[str, Any]]
    ) -> list[str]:
        """Секция s3 в quality.json + ФИНАЛЬНЫЕ предупреждения стадии (QC +
        отклонённые заметки + нулевой отбор — S3-1/S3-3)."""
        quality_path = job_dir / "quality.json"
        quality = self._read_json(quality_path) if quality_path.is_file() else {}
        base_warnings = list(quality.get("warnings", ()))
        quality["s3"] = {
            "enabled": True,
            "book_words": report["book_words"],
            "candidates": report["candidates_total"],
            "issued": report["notes_issued"],
            "rejected": report["notes_rejected"],
            "detailed": report["detailed_notes"],
            "reference": report["reference_notes"],
            "not_selected": report["notes_not_selected"],
            "max_per_chapter": report["max_notes_per_chapter"],
            "per_chapter": report["per_chapter"],
            "report": "grammar_notes.json",
        }
        stage_warnings: list[str] = []
        if report["candidates_total"] == 0:
            stage_warnings.append("S3: грамматические кандидаты не отобраны (0) — заметок нет")
        if rejected:
            first = [f"{r['surface']}: {r['reasons'][0]}" for r in rejected[:10]]
            stage_warnings.append(f"S3: {len(rejected)} заметок отклонено: " + "; ".join(first))
        quality["warnings"] = [*base_warnings, *stage_warnings]
        self._write_json(quality_path, quality)
        quality_logger = logging.getLogger(QUALITY_LOGGER)
        quality_logger.info("QC после S3: %s", json.dumps(quality, ensure_ascii=False))
        return list(quality["warnings"])

    # ------------------------------------------------------------- служебное S4

    def _s4_chapter_data(
        self, book: Book, pairs: tuple[AlignedPair, ...]
    ) -> dict[str, dict[str, Any]]:
        """S4: выровненный текст главы (подмножество E6) — ЕДИНСТВЕННЫЙ
        источник summary/вопросов/упражнений (материалы — по фактическому
        тексту). Глава без выровненных непустых текстовых сегментов (пустая,
        изображение, невыровненная) — вне словаря: зафиксированный пропуск
        (S4-1)."""
        seg_chapter = {
            seg.segment_id: ch.chapter_id for ch in book.chapters for seg in ch.segments
        }
        data: dict[str, dict[str, Any]] = {}
        for pair in pairs:
            chapter_id = seg_chapter.get(pair.segment_id)
            if chapter_id is None:
                continue
            source_text = pair.source_text.strip()
            if not source_text:
                continue  # пустой сегмент — текста нет (S4.1)
            entry = data.setdefault(
                chapter_id, {"segments": [], "text_parts": [], "word_count": 0}
            )
            entry["segments"].append(
                {
                    "segment_id": pair.segment_id,
                    "source": source_text,
                    "translation": pair.translation_text.strip(),
                }
            )
            entry["text_parts"].append(source_text)
            entry["word_count"] += len(source_text.split())
        return data

    @staticmethod
    def _s4_item_id(chapter_id: str, item_type: str, text: str) -> str:
        """S4: детерминированный id вопроса/упражнения
        (chapter|type|текст.casefold) — стабилен между прогонами (answers.json
        и повторные прогоны привязаны к нему)."""
        return uuid.uuid5(
            uuid.NAMESPACE_URL, f"s4:{chapter_id}:{item_type}:{text.casefold()}"
        ).hex

    def _s4_merge_answers(
        self,
        job_dir: Path,
        chapter_id: str,
        kind: str,
        answers: Mapping[str, str],
    ) -> None:
        """S4.4: ответы — ОТДЕЛЬНАЯ сущность (answers.json); в тело главы не
        попадают (S4-5)."""
        path = job_dir / "answers.json"
        data = self._read_json(path) if path.is_file() else {"chapters": {}}
        chapters = data.setdefault("chapters", {})
        chapter = chapters.setdefault(chapter_id, {"questions": {}, "exercises": {}})
        chapter.setdefault(kind, {}).update(dict(answers))
        self._write_json(path, data)

    def _s4_finalize_quality(
        self,
        job_dir: Path,
        *,
        part: str,
        stage_name: str,
        section: dict[str, Any],
        warnings: list[str],
    ) -> list[str]:
        """Секция s4 в quality.json (подсекции summary/questions/exercises
        накапливаются подстадиями) + ФИНАЛЬНЫЕ предупреждения подстадии."""
        quality_path = job_dir / "quality.json"
        quality = self._read_json(quality_path) if quality_path.is_file() else {}
        base_warnings = list(quality.get("warnings", ()))
        quality.setdefault("s4", {})[part] = section
        quality["warnings"] = [*base_warnings, *warnings]
        self._write_json(quality_path, quality)
        quality_logger = logging.getLogger(QUALITY_LOGGER)
        quality_logger.info(
            "QC после %s: %s", stage_name, json.dumps(quality, ensure_ascii=False)
        )
        return list(quality["warnings"])

    def _s4_summary(
        self, repo: JobRepository, job: Job, job_dir: Path, key: str, glossary_hash: str
    ) -> list[str]:
        """S4 (ТЗ_03): summary главы — ТОЛЬКО по фактическому тексту главы
        (название в payload отсутствует — генерация по названию запрещена);
        пустая/нечитаемая глава — НЕ создаётся, пропуск зафиксирован (S4-1).
        → summaries.json + секция s4.summary."""
        settings = self._settings
        book, pairs, _source_pack, _pack_dir, _nlp, source_code = self._learning_context(job)
        chapter_data = self._s4_chapter_data(book, pairs)
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _, model, _ = settings.llm.endpoint("analysis")
        client = AnalysisClient(settings, repo, job.id)
        spec = llm_task("chapter_summary")
        prompt_file, prompt_version = "", 0
        chapters: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        rejected: list[dict[str, Any]] = []
        for chapter in book.chapters:
            data = chapter_data.get(chapter.chapter_id)
            if data is None or not data["segments"]:
                skipped.append(
                    {
                        "chapter_id": chapter.chapter_id,
                        "reason": (
                            "нет выровненных текстовых сегментов в главе "
                            "(пустая глава/изображения) — summary не создаётся (S4.1)"
                        ),
                    }
                )
                continue
            payload = {
                "source_language": source_code,
                "target_language": job.target_language,
                "user_level": job.learning_level,
                "user_cefr": job.learning_cefr,
                "chapter_id": chapter.chapter_id,
                "segments": data["segments"],
            }
            outcome = client.run_task(
                spec,
                source_code,
                job.target_language,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
            if prompt_file == "":
                prompt_file, prompt_version = outcome.prompt.file, outcome.prompt.version
            reasons = validate_summary(outcome.payload)
            if reasons:
                rejected.append(
                    {"chapter_id": chapter.chapter_id, "reasons": reasons}
                )
                continue
            chapters.append(
                {
                    "chapter_id": chapter.chapter_id,
                    "status": "issued",
                    "brief": outcome.payload["brief"],
                    "extended": outcome.payload["extended"],
                    "key_events": outcome.payload["key_events"],
                    "entities": outcome.payload["entities"],
                    "themes": outcome.payload["themes"],
                    "concepts": outcome.payload["concepts"],
                    "word_count": data["word_count"],
                    "source_language": source_code,
                    "target_language": job.target_language,
                    "user_level": job.learning_level,
                    "user_cefr": job.learning_cefr,
                    "glossary_hash": glossary_hash,
                    "prompt_file": prompt_file,
                    "prompt_version": prompt_version,
                    "model": model,
                    "profile": "analysis",
                    "timestamp": timestamp,
                }
            )
        stage_warnings = [
            f"S4 summary: {s['chapter_id']}: {s['reason']}" for s in skipped
        ]
        if rejected:
            first = [f"{r['chapter_id']}: {r['reasons'][0]}" for r in rejected[:10]]
            stage_warnings.append(
                f"S4 summary: {len(rejected)} глав отклонено: " + "; ".join(first)
            )
        report: dict[str, Any] = {
            "stage": "S4",
            "part": "summary",
            "enabled": True,
            "source_language": source_code,
            "target_language": job.target_language,
            "user_level": job.learning_level,
            "user_cefr": job.learning_cefr,
            "chapters_total": len(book.chapters),
            "chapters_issued": len(chapters),
            "chapters_skipped": len(skipped),
            "chapters_rejected": len(rejected),
            "glossary_hash": glossary_hash,
            "prompt_file": prompt_file,
            "prompt_version": prompt_version,
            "model": model,
            "profile": "analysis",
            "timestamp": timestamp,
            "idempotency_key": key,
            "chapters": chapters,
            "skipped": skipped,
            "rejected": rejected,
        }
        self._write_json(job_dir / "summaries.json", report)
        final_warnings = self._s4_finalize_quality(
            job_dir,
            part="summary",
            stage_name="S4 summary",
            section={
                "enabled": True,
                "chapters": len(book.chapters),
                "issued": len(chapters),
                "skipped": len(skipped),
                "rejected": len(rejected),
                "report": "summaries.json",
            },
            warnings=stage_warnings,
        )
        repo.record_stage_result(job.id, "s4_summary", key, str(job_dir / "summaries.json"))
        return final_warnings

    def _s4_question_items(
        self,
        repo: JobRepository,
        job: Job,
        *,
        book: Book,
        chapter_data: dict[str, dict[str, Any]],
        source_code: str,
        normalize: Callable[[str], str],
    ) -> tuple[
        list[tuple[Mapping[str, Any], str]],
        list[dict[str, Any]],
        str,
        int,
    ]:
        """S4: LLM-вопросы по главам (поглавные батчи — никогда не вся книга
        в запросе) + валидация (S4-2/S4-3/S4-4). Возвращает
        (validated, rejected, prompt_file, prompt_version) — промпт первого
        вызова (единый для всех глав, E4-2)."""
        settings = self._settings
        client = AnalysisClient(settings, repo, job.id)
        spec = llm_task("chapter_questions")
        prompt_file, prompt_version = "", 0
        validated: list[tuple[Mapping[str, Any], str]] = []
        rejected: list[dict[str, Any]] = []
        for chapter in book.chapters:
            data = chapter_data.get(chapter.chapter_id)
            if data is None or not data["segments"]:
                continue  # текста нет — вопросов нет (пропуск — в summaries)
            payload = {
                "source_language": source_code,
                "target_language": job.target_language,
                "user_level": job.learning_level,
                "user_cefr": job.learning_cefr,
                "chapter_id": chapter.chapter_id,
                "segments": data["segments"],
                "counts": {
                    "easy": 5,
                    "standard_min": 5,
                    "standard_max": 10,
                    "advanced_min": 1,
                    "advanced_max": 5,
                },
            }
            outcome = client.run_task(
                spec,
                source_code,
                job.target_language,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
            if prompt_file == "":
                prompt_file, prompt_version = outcome.prompt.file, outcome.prompt.version
            raw_items = outcome.payload.get("items")
            if not isinstance(raw_items, list):
                raise AppError("S4: ответ LLM — items отсутствует или не массив")
            seg_ids = frozenset(seg["segment_id"] for seg in data["segments"])
            chapter_text = "\n".join(data["text_parts"])
            for raw in raw_items:
                if not isinstance(raw, dict):
                    raise AppError("S4: ответ LLM — элемент items не объект")
                reasons = validate_question(
                    raw,
                    chapter_seg_ids=seg_ids,
                    chapter_text=chapter_text,
                    normalize=normalize,
                )
                if reasons:
                    rejected.append(
                        {
                            "chapter_id": chapter.chapter_id,
                            "question": raw.get("question"),
                            "type": raw.get("type"),
                            "level": raw.get("level"),
                            "reasons": reasons,
                        }
                    )
                else:
                    validated.append((raw, chapter.chapter_id))
        return validated, rejected, prompt_file, prompt_version

    def _s4_question_records(
        self, dedup: list[tuple[Mapping[str, Any], str]]
    ) -> tuple[
        list[dict[str, Any]], dict[str, dict[str, str]], dict[str, int]
    ]:
        """S4: ответы ОТСЕКАЮТСЯ в отдельную сущность (answers.json, S4.4);
        записи для тела главы — БЕЗ ответов (S4-5) + счёт по уровням."""
        records: list[dict[str, Any]] = []
        answers_by_chapter: dict[str, dict[str, str]] = {}
        by_level: dict[str, int] = {}
        for item, chapter_id in dedup:
            qtype = str(item["type"])
            record: dict[str, Any] = {
                "id": self._s4_item_id(chapter_id, qtype, str(item.get("question", ""))),
                "chapter_id": chapter_id,
                "question": item["question"],
                "type": qtype,
                "level": item["level"],
                "segment_id": item["segment_id"],
                "entities": item.get("entities") or [],
            }
            if qtype == "mc":
                options = item["options"]
                record["options"] = [str(opt["text"]) for opt in options]
                correct_index = next(
                    i for i, opt in enumerate(options) if opt.get("correct") is True
                )
                answer = str(correct_index)
            elif qtype == "gap":
                record["original_sentence"] = item["original_sentence"]
                record["sentence_with_blank"] = item["sentence_with_blank"]
                answer = str(item["answer"])
            else:
                answer = str(item["answer"])
            records.append(record)
            answers_by_chapter.setdefault(chapter_id, {})[record["id"]] = answer
            by_level[record["level"]] = by_level.get(record["level"], 0) + 1
        return records, answers_by_chapter, by_level

    def _s4_questions(
        self, repo: JobRepository, job: Job, job_dir: Path, key: str, glossary_hash: str
    ) -> list[str]:
        """S4 (ТЗ_03): вопросы на понимание трёх уровней (easy — фактические,
        standard — на содержание, advanced — вывод) по фактическому тексту
        главы. Привязка к segment_id; сущности ∈ тексте главы (S4-4); пропуск
        — слово ∈ исходном предложении (S4-2); выбор — ровно один правильный
        (S4-3). Ответы — в answers.json (S4.4/S4-5). → questions.json."""
        settings = self._settings
        book, pairs, source_pack, _pack_dir, _nlp, source_code = self._learning_context(job)
        chapter_data = self._s4_chapter_data(book, pairs)

        def _normalize(text: str) -> str:
            return normalize_text(text, source_pack.normalization)

        validated, rejected, prompt_file, prompt_version = self._s4_question_items(
            repo,
            job,
            book=book,
            chapter_data=chapter_data,
            source_code=source_code,
            normalize=_normalize,
        )
        # Дубли текста вопроса на всю книгу: первый остаётся, остальные —
        # отклонение (механизм S1.2/S2.4).
        seen: set[str] = set()
        dedup: list[tuple[Mapping[str, Any], str]] = []
        for item, chapter_id in validated:
            qkey = f"{item.get('type')}|{str(item.get('question', '')).casefold()}"
            if qkey in seen:
                rejected.append(
                    {
                        "chapter_id": chapter_id,
                        "question": item.get("question"),
                        "type": item.get("type"),
                        "level": item.get("level"),
                        "reasons": ["дублирующийся вопрос (S4)"],
                    }
                )
                continue
            seen.add(qkey)
            dedup.append((item, chapter_id))
        records, answers_by_chapter, by_level = self._s4_question_records(dedup)
        for chapter_id, answers in answers_by_chapter.items():
            self._s4_merge_answers(job_dir, chapter_id, "questions", answers)
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _, model, _ = settings.llm.endpoint("analysis")
        stage_warnings: list[str] = []
        if rejected:
            first = [f"{r['chapter_id']}: {r['reasons'][0]}" for r in rejected[:10]]
            stage_warnings.append(
                f"S4 вопросы: {len(rejected)} отклонено: " + "; ".join(first)
            )
        report: dict[str, Any] = {
            "stage": "S4",
            "part": "questions",
            "enabled": True,
            "source_language": source_code,
            "target_language": job.target_language,
            "user_level": job.learning_level,
            "user_cefr": job.learning_cefr,
            "questions_issued": len(records),
            "questions_rejected": len(rejected),
            "by_level": dict(sorted(by_level.items())),
            "glossary_hash": glossary_hash,
            "prompt_file": prompt_file,
            "prompt_version": prompt_version,
            "model": model,
            "profile": "analysis",
            "timestamp": timestamp,
            "idempotency_key": key,
            "questions": records,
            "rejected": rejected,
        }
        self._write_json(job_dir / "questions.json", report)
        final_warnings = self._s4_finalize_quality(
            job_dir,
            part="questions",
            stage_name="S4 вопросы",
            section={
                "enabled": True,
                "issued": len(records),
                "rejected": len(rejected),
                "by_level": dict(sorted(by_level.items())),
                "report": "questions.json",
            },
            warnings=stage_warnings,
        )
        repo.record_stage_result(job.id, "s4_questions", key, str(job_dir / "questions.json"))
        return final_warnings

    def _s4_exercise_sources(
        self, job_dir: Path
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
        """S4: выпущенные карточки S1 / заметки S3 (по главам) — ЕДИНСТВЕННЫЙ
        источник vocab/grammar-упражнений (S4.6)."""
        cards_by_chapter: dict[str, list[dict[str, Any]]] = {}
        cards_path = job_dir / "vocabulary_cards.json"
        if cards_path.is_file():
            for card in self._read_json(cards_path).get("cards", ()):
                cards_by_chapter.setdefault(str(card.get("chapter_id", "")), []).append(
                    card
                )
        notes_by_chapter: dict[str, list[dict[str, Any]]] = {}
        notes_path = job_dir / "grammar_notes.json"
        if notes_path.is_file():
            for note in self._read_json(notes_path).get("notes", ()):
                notes_by_chapter.setdefault(str(note.get("chapter_id", "")), []).append(
                    note
                )
        return cards_by_chapter, notes_by_chapter

    def _s4_exercise_items(
        self,
        repo: JobRepository,
        job: Job,
        *,
        book: Book,
        chapter_data: dict[str, dict[str, Any]],
        cards_by_chapter: Mapping[str, Sequence[Mapping[str, Any]]],
        notes_by_chapter: Mapping[str, Sequence[Mapping[str, Any]]],
        source_code: str,
        normalize: Callable[[str], str],
    ) -> tuple[
        list[tuple[Mapping[str, Any], str]],
        list[dict[str, Any]],
        str,
        int,
    ]:
        """S4: LLM-упражнения по главам (поглавные батчи) + валидация
        (S4-2/S4-3/S4-4/S4-6; перевод — дословно из главы; типы — конфиг)."""
        settings = self._settings
        allowed_types = frozenset(settings.limits.exercises_types)
        client = AnalysisClient(settings, repo, job.id)
        spec = llm_task("chapter_exercises")
        prompt_file, prompt_version = "", 0
        validated: list[tuple[Mapping[str, Any], str]] = []
        rejected: list[dict[str, Any]] = []
        for chapter in book.chapters:
            data = chapter_data.get(chapter.chapter_id)
            if data is None or not data["segments"]:
                continue  # текста нет — упражнений нет
            cards = list(cards_by_chapter.get(chapter.chapter_id, ()))
            notes = list(notes_by_chapter.get(chapter.chapter_id, ()))
            payload = {
                "source_language": source_code,
                "target_language": job.target_language,
                "user_level": job.learning_level,
                "user_cefr": job.learning_cefr,
                "chapter_id": chapter.chapter_id,
                "segments": data["segments"],
                "cards": [
                    {
                        "id": card["lemma"],
                        "word_form": card.get("word_form") or card["lemma"],
                        "translation": card.get("translation", ""),
                        "segment_id": card.get("segment_id", ""),
                    }
                    for card in cards
                ],
                "notes": [
                    {
                        "id": note["surface"],
                        "explanation": note.get("explanation", ""),
                        "phenomenon_id": note.get("phenomenon_id", ""),
                        "segment_id": note.get("segment_id", ""),
                    }
                    for note in notes
                ],
                "exercise_types": sorted(allowed_types),
            }
            outcome = client.run_task(
                spec,
                source_code,
                job.target_language,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
            if prompt_file == "":
                prompt_file, prompt_version = outcome.prompt.file, outcome.prompt.version
            raw_items = outcome.payload.get("items")
            if not isinstance(raw_items, list):
                raise AppError("S4: ответ LLM — items отсутствует или не массив")
            seg_ids = frozenset(seg["segment_id"] for seg in data["segments"])
            chapter_text = "\n".join(data["text_parts"])
            card_ids = frozenset(str(c["id"]) for c in payload["cards"])
            note_ids = frozenset(str(n["id"]) for n in payload["notes"])
            for raw in raw_items:
                if not isinstance(raw, dict):
                    raise AppError("S4: ответ LLM — элемент items не объект")
                reasons = validate_exercise(
                    raw,
                    chapter_seg_ids=seg_ids,
                    chapter_text=chapter_text,
                    card_ids=card_ids,
                    note_ids=note_ids,
                    allowed_types=allowed_types,
                    normalize=normalize,
                )
                if reasons:
                    rejected.append(
                        {
                            "chapter_id": chapter.chapter_id,
                            "prompt": raw.get("prompt"),
                            "type": raw.get("type"),
                            "reasons": reasons,
                        }
                    )
                else:
                    validated.append((raw, chapter.chapter_id))
        return validated, rejected, prompt_file, prompt_version

    def _s4_exercise_records(
        self, dedup: list[tuple[Mapping[str, Any], str]]
    ) -> tuple[
        list[dict[str, Any]], dict[str, dict[str, str]], dict[str, int]
    ]:
        """S4: ответы ОТСЕКАЮТСЯ в answers.json (S4.4); записи — БЕЗ ответов
        (S4-5) + счёт по типам."""
        records: list[dict[str, Any]] = []
        answers_by_chapter: dict[str, dict[str, str]] = {}
        by_type: dict[str, int] = {}
        for item, chapter_id in dedup:
            etype = str(item["type"])
            record: dict[str, Any] = {
                "id": self._s4_item_id(chapter_id, etype, str(item.get("prompt", ""))),
                "chapter_id": chapter_id,
                "prompt": item["prompt"],
                "type": etype,
                "segment_id": item["segment_id"],
                "source_ref": item.get("source_ref"),
            }
            if etype == "mc":
                options = item["options"]
                record["options"] = [str(opt["text"]) for opt in options]
                correct_index = next(
                    i for i, opt in enumerate(options) if opt.get("correct") is True
                )
                answer = str(correct_index)
            elif etype == "gap":
                record["original_sentence"] = item["original_sentence"]
                record["sentence_with_blank"] = item["sentence_with_blank"]
                answer = str(item["answer"])
            else:
                answer = str(item["answer"])
            records.append(record)
            answers_by_chapter.setdefault(chapter_id, {})[record["id"]] = answer
            by_type[record["type"]] = by_type.get(record["type"], 0) + 1
        return records, answers_by_chapter, by_type

    def _s4_exercises(
        self, repo: JobRepository, job: Job, job_dir: Path, key: str, glossary_hash: str
    ) -> list[str]:
        """S4 (ТЗ_03): упражнения главы (состав типов — конфиг
        limits.exercises_types); лексика — ТОЛЬКО из выпущенных S1-карточек,
        грамматика — ТОЛЬКО из выпущенных S3-заметок (S4-6); перевод —
        предложение дословно из главы; ответы — в answers.json (S4.4).
        → exercises.json."""
        settings = self._settings
        book, pairs, source_pack, _pack_dir, _nlp, source_code = self._learning_context(job)
        chapter_data = self._s4_chapter_data(book, pairs)

        def _normalize(text: str) -> str:
            return normalize_text(text, source_pack.normalization)

        cards_by_chapter, notes_by_chapter = self._s4_exercise_sources(job_dir)
        validated, rejected, prompt_file, prompt_version = self._s4_exercise_items(
            repo,
            job,
            book=book,
            chapter_data=chapter_data,
            cards_by_chapter=cards_by_chapter,
            notes_by_chapter=notes_by_chapter,
            source_code=source_code,
            normalize=_normalize,
        )
        # Дубли текста задания на всю книгу (механизм S1.2/S2.4).
        seen: set[str] = set()
        dedup: list[tuple[Mapping[str, Any], str]] = []
        for item, chapter_id in validated:
            ekey = f"{item.get('type')}|{str(item.get('prompt', '')).casefold()}"
            if ekey in seen:
                rejected.append(
                    {
                        "chapter_id": chapter_id,
                        "prompt": item.get("prompt"),
                        "type": item.get("type"),
                        "reasons": ["дублирующееся упражнение (S4)"],
                    }
                )
                continue
            seen.add(ekey)
            dedup.append((item, chapter_id))
        records, answers_by_chapter, by_type = self._s4_exercise_records(dedup)
        for chapter_id, answers in answers_by_chapter.items():
            self._s4_merge_answers(job_dir, chapter_id, "exercises", answers)
        stage_warnings: list[str] = []
        if rejected:
            first = [f"{r['chapter_id']}: {r['reasons'][0]}" for r in rejected[:10]]
            stage_warnings.append(
                f"S4 упражнения: {len(rejected)} отклонено: " + "; ".join(first)
            )
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _, model, _ = settings.llm.endpoint("analysis")
        report: dict[str, Any] = {
            "stage": "S4",
            "part": "exercises",
            "enabled": True,
            "source_language": source_code,
            "target_language": job.target_language,
            "user_level": job.learning_level,
            "user_cefr": job.learning_cefr,
            "exercises_issued": len(records),
            "exercises_rejected": len(rejected),
            "by_type": dict(sorted(by_type.items())),
            "vocab_from_cards": 1.0 if by_type.get("vocab") else None,
            "grammar_from_notes": 1.0 if by_type.get("grammar") else None,
            "glossary_hash": glossary_hash,
            "prompt_file": prompt_file,
            "prompt_version": prompt_version,
            "model": model,
            "profile": "analysis",
            "timestamp": timestamp,
            "idempotency_key": key,
            "exercises": records,
            "rejected": rejected,
        }
        self._write_json(job_dir / "exercises.json", report)
        final_warnings = self._s4_finalize_quality(
            job_dir,
            part="exercises",
            stage_name="S4 упражнения",
            section={
                "enabled": True,
                "issued": len(records),
                "rejected": len(rejected),
                "by_type": dict(sorted(by_type.items())),
                "vocab_from_cards": report["vocab_from_cards"],
                "grammar_from_notes": report["grammar_from_notes"],
                "report": "exercises.json",
            },
            warnings=stage_warnings,
        )
        repo.record_stage_result(job.id, "s4_exercises", key, str(job_dir / "exercises.json"))
        return final_warnings

    def _s5_collect_artifacts(self, job_dir: Path) -> dict[str, Any]:
        """S5: артефакты вышестоящих стадий (отсутствует — не входит в
        уровень): карточки, MWE, заметки, summary, вопросы, упражнения,
        ответы (+ группировка по главам)."""

        def _list(name: str, field: str) -> list[dict[str, Any]]:
            path = job_dir / name
            if not path.is_file():
                return []
            data = self._read_json(path).get(field)
            return data if isinstance(data, list) else []

        def _by_chapter(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for record in records:
                grouped.setdefault(str(record.get("chapter_id", "")), []).append(record)
            return grouped

        cards = _list("vocabulary_cards.json", "cards")
        mwe_records = _list("mwe_records.json", "records")
        grammar_notes = _list("grammar_notes.json", "notes")
        summaries = _list("summaries.json", "chapters")
        questions = _list("questions.json", "questions")
        exercises = _list("exercises.json", "exercises")
        answers_path = job_dir / "answers.json"
        answers: Mapping[str, Any] = (
            self._read_json(answers_path).get("chapters", {}) if answers_path.is_file() else {}
        )
        return {
            "cards": cards,
            "cards_by": _by_chapter(cards),
            "mwe_by": _by_chapter(mwe_records),
            "grammar_by": _by_chapter(grammar_notes),
            "summaries_by": {
                str(rec.get("chapter_id")): rec
                for rec in summaries
                if isinstance(rec, Mapping)
            },
            "questions": questions,
            "questions_by": _by_chapter(questions),
            "exercises": exercises,
            "exercises_by": _by_chapter(exercises),
            "answers": answers,
        }

    def _s5_build_sections(
        self,
        book: Book,
        target: Path,
        artifacts: Mapping[str, Any],
    ) -> tuple[
        dict[str, bytes], dict[str, list[str]], dict[str, list[str]], list[tuple[str, str]]
    ]:
        """S5: секции по главам (состав — по наличию данных) + оглавление
        (каждая секция — запись, S5.4). Возврат: (doc_patches,
        chapter_anchors, sections_by_chapter, toc_entries)."""
        doc_patches: dict[str, bytes] = {}
        chapter_anchors: dict[str, list[str]] = {}
        sections_by_chapter: dict[str, list[str]] = {}
        toc_entries: list[tuple[str, str]] = []
        with zipfile.ZipFile(target) as archive:
            _opf_path, opf_dir, _nav, _ncx = study_book.locate_structure(archive)
            answers_doc = study_book.join_path(opf_dir, study_book.ANSWERS_HREF)
            for chapter in book.chapters:
                data = {
                    "summary": artifacts["summaries_by"].get(chapter.chapter_id),
                    "vocab": artifacts["cards_by"].get(chapter.chapter_id, []),
                    "expressions": artifacts["mwe_by"].get(chapter.chapter_id, []),
                    "grammar": artifacts["grammar_by"].get(chapter.chapter_id, []),
                    "questions": artifacts["questions_by"].get(chapter.chapter_id, []),
                    "exercises": artifacts["exercises_by"].get(chapter.chapter_id, []),
                }
                sections, present = study_book.render_sections(chapter.chapter_id, data)
                if not present:
                    continue
                doc_path = chapter.document_path
                if doc_path not in archive.namelist():
                    raise AppError(f"S5: документ главы {doc_path} отсутствует в target.epub")
                doc_patches[doc_path] = study_book.append_sections_to_doc(
                    archive.read(doc_path), sections
                )
                chapter_anchors[doc_path] = [f"s5-{t}-{chapter.chapter_id}" for t in present]
                sections_by_chapter[chapter.chapter_id] = present
                for section_type in present:
                    toc_entries.append(
                        (
                            f"{doc_path}#s5-{section_type}-{chapter.chapter_id}",
                            f"{chapter.title} — {study_book.SECTION_TITLES[section_type]}",
                        )
                    )
            toc_entries.append((answers_doc, study_book.ANSWERS_TITLE))
        return doc_patches, chapter_anchors, sections_by_chapter, toc_entries

    @staticmethod
    def _s5_export_cards(
        job_dir: Path, cards: list[dict[str, Any]], pack_dir: Path, source_pack: PackSpec
    ) -> dict[str, Any]:
        """S5.6: экспорт карточек CSV/Anki/JSON (поля — по card_schema.json)."""
        exports: dict[str, Any] = {"cards": len(cards)}
        if not cards:
            return exports
        schema = load_card_schema(pack_dir, source_pack)
        # Имена файлов — card_export.CARDS_FILES (единый источник, Rule 8:
        # писатель, API result и CLI export cards — одна таблица).
        exports["csv"] = card_export.export_cards_csv(
            job_dir / card_export.CARDS_FILES["csv"], cards, schema
        )
        exports["anki"] = card_export.export_cards_anki(
            job_dir / card_export.CARDS_FILES["anki"], cards, schema
        )
        exports["json"] = card_export.export_cards_json(
            job_dir / card_export.CARDS_FILES["json"], cards
        )
        return exports

    def _s5_finalize_quality(
        self,
        job_dir: Path,
        *,
        coverage: float,
        chapters_total: int,
        chapters_with_sections: int,
        skipped: Sequence[tuple[str, str]],
        exports: Mapping[str, Any],
        epubcheck_available: bool,
        epubcheck_reason: str | None,
    ) -> list[str]:
        """S5: секция quality.json + финальные предупреждения (пропущенные
        главы, частичный охват, EPUBCheck недоступен — Rule 6)."""
        stage_warnings: list[str] = []
        if skipped:
            names = "; ".join(f"«{t}»" if t else cid for cid, t in skipped[:10])
            stage_warnings.append(
                f"S5: {len(skipped)} глав без выровненных сегментов "
                f"(вне учебной книги): {names}"
            )
        if coverage < 1.0 - 1e-9:
            stage_warnings.append(
                f"S5: фактический охват {coverage:.1%} (E6) — указан в учебной книге"
            )
        if not epubcheck_available:
            stage_warnings.append(f"S5: EPUBCheck недоступен (Rule 6): {epubcheck_reason}")
        quality_path = job_dir / "quality.json"
        quality = self._read_json(quality_path) if quality_path.is_file() else {}
        base_warnings = list(quality.get("warnings", ()))
        quality["s5"] = {
            "enabled": True,
            "coverage": coverage,
            "chapters_total": chapters_total,
            "chapters_with_sections": chapters_with_sections,
            "skipped": len(skipped),
            "exports": dict(exports),
            "report": "s5_report.json",
        }
        quality["warnings"] = [*base_warnings, *stage_warnings]
        self._write_json(quality_path, quality)
        quality_logger = logging.getLogger(QUALITY_LOGGER)
        quality_logger.info("QC после S5: %s", json.dumps(quality, ensure_ascii=False))
        return list(quality["warnings"])

    def _s5_assembly(
        self, repo: JobRepository, job: Job, job_dir: Path, key: str, glossary_hash: str
    ) -> list[str]:
        """S5 (ТЗ_03): сборка учебного EPUB и экспорт карточек.

        Метод — ДОПОЛНЕНИЕ выходного EPUB тракта (target.epub), не «с нуля»
        (S5.1): вставка секций в конец `<body>` каждой главы (состав — по
        наличию артефактов вышестоящих стадий, т.е. по уровню), правка OPF
        manifest/spine, EPUB3-nav / NCX (каждая секция — запись оглавления,
        S5.4), добавление финального документа «Ответы и охват» в конец
        spine (ответы — только там, S5-5), фактический охват E6 в книге
        (S5-8, расхождение с отчётом E6 = 0). Атомарная выдача:
        `.partial` → внутренняя валидация → EPUBCheck → `os.replace`
        (S5.7/S5.8, Rule 8). Экспорт карточек: CSV/Anki/JSON (S5.6, поля —
        по card_schema.json языка)."""
        settings = self._settings
        job = repo.require(job.id)
        book, pairs, source_pack, pack_dir, _nlp, source_code = self._learning_context(job)

        # S5-8: фактический охват — single source (D36) jobs.alignment_coverage;
        # расхождение с отчётом E6 = 0 (иначе стадия не выпускает книгу).
        coverage = job.alignment_coverage
        if coverage is None:
            raise AppError("S5: jobs.alignment_coverage не установлен (E6 не исполнен?)")
        alignment = self._l3_alignment(self._read_json(job_dir / "alignment_report.json"))
        if abs(alignment.coverage - coverage) > 1e-9:
            raise AppError(
                f"S5: расхождение охвата: задание {coverage:.6f} vs отчёт E6 "
                f"{alignment.coverage:.6f}"
            )
        seg_chapter = {
            seg.segment_id: ch.chapter_id for ch in book.chapters for seg in ch.segments
        }
        aligned_by_chapter: dict[str, int] = {}
        for pair in pairs:
            chapter_id = seg_chapter.get(pair.segment_id)
            if chapter_id is not None:
                aligned_by_chapter[chapter_id] = aligned_by_chapter.get(chapter_id, 0) + 1
        skipped: list[tuple[str, str]] = [
            (ch.chapter_id, ch.title or "")
            for ch in book.chapters
            if aligned_by_chapter.get(ch.chapter_id, 0) == 0
        ]

        artifacts = self._s5_collect_artifacts(job_dir)
        target = job_dir / "target.epub"
        (
            doc_patches,
            chapter_anchors,
            sections_by_chapter,
            toc_entries,
        ) = self._s5_build_sections(book, target, artifacts)

        answers_bytes = study_book.render_answers_document(
            coverage=coverage,
            aligned=alignment.aligned_count,
            total=alignment.total,
            chapters_total=len(book.chapters),
            chapters_with_sections=len(sections_by_chapter),
            skipped=skipped,
            level=job.learning_level or "",
            answers_data=artifacts["answers"],
            questions=artifacts["questions"],
            exercises=artifacts["exercises"],
        )
        partial = job_dir / "learning.epub.partial"
        structure = study_book.assemble_learning_epub(
            target,
            partial,
            doc_patches=doc_patches,
            toc_entries=toc_entries,
            answers_bytes=answers_bytes,
        )
        problems = study_book.validate_study_book(
            partial,
            target,
            chapter_anchors=chapter_anchors,
            toc_anchor_paths=[href for href, _label in toc_entries],
            coverage=coverage,
            block_tags=settings.segmentation.block_tags,
        )
        if problems:
            raise AppError(
                "S5: структурная валидация собранного EPUB не пройдена: " + "; ".join(problems)
            )
        learning_epub = job_dir / "learning.epub"
        epubcheck_out, _bak = atomic_finalize(partial, learning_epub, settings)

        # Экспорт карточек (S5.6): CSV/Anki/JSON, поля — по card_schema.json.
        exports = self._s5_export_cards(job_dir, artifacts["cards"], pack_dir, source_pack)

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        report: dict[str, Any] = {
            "stage": "S5",
            "enabled": True,
            "source_language": source_code,
            "target_language": job.target_language,
            "user_level": job.learning_level,
            "user_cefr": job.learning_cefr,
            "glossary_hash": glossary_hash,
            "idempotency_key": key,
            "coverage": coverage,
            "aligned": alignment.aligned_count,
            "total": alignment.total,
            "chapters_total": len(book.chapters),
            "chapters_with_sections": len(sections_by_chapter),
            "skipped": [{"chapter_id": cid, "title": title} for cid, title in skipped],
            "sections": sections_by_chapter,
            "exports": exports,
            "epub": {
                "entries_total": structure["entries_total"],
                "answers": structure["answers"],
                "nav": structure["nav"],
                "ncx": structure["ncx"],
                "epubcheck": {
                    "version": epubcheck_out.version,
                    "available": epubcheck_out.available,
                    "errors": len(epubcheck_out.errors),
                    "warnings": len(epubcheck_out.warnings),
                    "unavailable_reason": epubcheck_out.unavailable_reason,
                },
            },
            "zipinfo": study_book.zipinfo_verbatim(learning_epub),
            "timestamp": timestamp,
        }
        self._write_json(job_dir / "s5_report.json", report)
        final_warnings = self._s5_finalize_quality(
            job_dir,
            coverage=coverage,
            chapters_total=len(book.chapters),
            chapters_with_sections=len(sections_by_chapter),
            skipped=skipped,
            exports=exports,
            epubcheck_available=epubcheck_out.available,
            epubcheck_reason=epubcheck_out.unavailable_reason,
        )
        repo.record_stage_result(job.id, "s5_assembly", key, str(job_dir / "s5_report.json"))
        return final_warnings

    def _learning_no_op(
        self, repo: JobRepository, job: Job, job_dir: Path
    ) -> list[str]:
        """no-op learning (learning не включён): отчёт + запись стадии;
        предупреждения — из quality.json (стадия всегда существует, ТЗ_03)."""
        report: dict[str, Any] = {
            "stage": "S1",
            "enabled": False,
            "reason": "learning не включён (learning_level не задан)",
            "note": "S1–S5 исполняются только при заданном уровне; стадия существует",
        }
        self._write_json(job_dir / "learning_report.json", report)
        repo.record_stage_result(
            job.id, "learning", self.input_hash(job), str(job_dir / "learning_report.json")
        )
        quality_path = job_dir / "quality.json"
        if quality_path.is_file():
            return list(self._read_json(quality_path)["warnings"])
        return []

    def _learning_glossary_hash(self, job: Job) -> str:
        """Хэш глоссария версии книги (ТЗ_03 общее требование 2); пустая
        строка — глоссарий не собран (выключен и т.п.)."""
        path = BookStore(self._settings.books_dir).glossary_path(
            book_key_of(job.source_path), job.input_sha256
        )
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""

    # ------------------------------------------------------------- служебное L3

    def _l3_no_op(
        self, repo: JobRepository, job: Job, job_dir: Path, *, enabled: bool, reason: str
    ) -> list[str]:
        """no-op L3 (L3.10): отчёт + запись стадии; предупреждения — из
        quality.json (расхождения обнаруживаются и сообщаются, автоправки нет)."""
        report: dict[str, Any] = {
            "stage": "L3",
            "enabled": enabled,
            "reason": reason,
            "note": "расхождения обнаруживаются и сообщаются (L2.6); автоправки нет",
        }
        if enabled:
            report["no_op"] = True
        self._write_json(job_dir / "l3_fix_report.json", report)
        self._record_l3(repo, job)
        quality_path = job_dir / "quality.json"
        if quality_path.is_file():
            return list(self._read_json(quality_path)["warnings"])
        return []

    def _l3_selection(
        self, job_dir: Path, glossary: Glossary, l2_before: dict[str, Any]
    ) -> tuple[FixSelection, tuple[TermMismatch, ...]]:
        """Отбор правимых сегментов (L3.2) из отчёта L2 + карты выравнивания E6."""
        alignment = self._l3_alignment(self._read_json(job_dir / "alignment_report.json"))
        pairs = {p.segment_id: p for d in alignment.docs for p in d.aligned}
        mismatches = tuple(
            TermMismatch(
                term=str(m["term"]),
                translation_expected=str(m["translation_expected"]),
                segment_id=str(m["segment_id"]),
                source_text=str(m["source_text"]),
                translation_text=str(m["translation_text"]),
            )
            for m in l2_before.get("mismatches", ())
        )
        term_stats = {
            str(t["term"]): (int(t["occurrences"]), int(t["consistent"]))
            for t in l2_before.get("terms", ())
        }
        selection = select_fixable(glossary, pairs, mismatches, term_stats, alignment.total)
        return selection, mismatches

    @staticmethod
    def _l3_share(selection: FixSelection) -> float:
        """Доля правимых сегментов (L3-6): сегменты с ≥1 правимым расхождением
        от общего числа исходных сегментов."""
        if not selection.total_segments:
            return 0.0
        return len(selection.fixable_segment_ids) / selection.total_segments

    def _l3_halt(
        self,
        repo: JobRepository,
        job: Job,
        job_dir: Path,
        *,
        selection: FixSelection,
        share: float,
        cap: float,
    ) -> NoReturn:
        """Остановка L3-6: отчёт о превышении + запись стадии + AppError
        (воркер переводит задание в FAILED — fail closed; массовая переписка
        книги недопустима)."""
        report = {
            "stage": "L3",
            "enabled": True,
            "halted": True,
            "reason": (
                f"доля правимых сегментов {share:.1%} > порога {cap:.1%} (L3-6): "
                "системная проблема — остановка с отчётом, правок 0"
            ),
            "fixable_segment_share": round(share, 6),
            "max_segment_share": cap,
            "total_segments": selection.total_segments,
            "fixable": [asdict(f) for f in selection.fixable],
            "unverified_terms": [asdict(u) for u in selection.unverified_terms],
        }
        self._write_json(job_dir / "l3_fix_report.json", report)
        self._record_l3(repo, job)
        raise AppError(
            f"L3-6: доля правимых сегментов {share:.1%} превышает порог "
            f"{cap:.1%} (L3-6) — остановка с отчётом, массовая переписка "
            "книги не выполняется"
        )

    def _l3_apply(
        self,
        repo: JobRepository,
        job: Job,
        job_dir: Path,
        glossary: Glossary,
        selection: FixSelection,
    ) -> tuple[list[FixOutcome], dict[str, bytes]]:
        """Правка каждого правимого сегмента (L3.3/L3.4) + применение
        прошедших валидацию патчей к документам в памяти. L3.7: запись
        каждой применённой правки в quality.log — в момент применения
        (правок без записи — ноль)."""
        settings = self._settings
        store = BookStore(settings.books_dir)
        book_key = book_key_of(job.source_path)
        glossary_cfg = settings.glossary
        style = store.load_style(book_key, job.input_sha256)
        style_note = (
            f"{style.style} ({style.region})"
            if style is not None and style.region
            else style.style if style is not None else ""
        )
        fixer = GlossaryFixer(
            settings,
            repo,
            job.id,
            source_lang=job.source_language or "",
            target_lang=job.target_language,
            spec=llm_task("glossary_fix"),
            sentence_delta=glossary_cfg.fix_sentence_delta,
            seed=settings.language_detection.seed,
            min_confidence=settings.language_detection.min_confidence,
            aliases=aliases_map(load_registry(settings.languages_root)),
        )
        quality_logger = logging.getLogger(QUALITY_LOGGER)
        target = job_dir / "target.epub"
        doc_state: dict[str, bytes] = {}
        doc_original: dict[str, bytes] = {}
        outcomes: list[FixOutcome] = []
        with zipfile.ZipFile(target) as tz, zipfile.ZipFile(Path(job.source_path)) as sz:
            for item in selection.fixable:
                entry = glossary.term(item.term)
                if item.doc not in doc_state:
                    doc_state[item.doc] = tz.read(item.doc)
                    doc_original[item.doc] = doc_state[item.doc]
                if entry is None:
                    outcomes.append(
                        FixOutcome(
                            segment_id=item.segment_id,
                            doc=item.doc,
                            term=item.term,
                            applied=False,
                            validation_points=(0,),
                            reason="термин отсутствует в глоссарии (несогласованный вход)",
                            old_text=item.translation_text,
                        )
                    )
                    continue
                try:
                    translation_xml = block_bytes(doc_state[item.doc], item.translation_xpath)
                    source_xml = block_bytes(sz.read(item.doc), item.source_xpath)
                except AppError as exc:
                    # Карта устарела (файл изменился с момента выравнивания):
                    # правка невозможна — расхождение остаётся (fail closed).
                    outcomes.append(
                        FixOutcome(
                            segment_id=item.segment_id,
                            doc=item.doc,
                            term=item.term,
                            applied=False,
                            validation_points=(0,),
                            reason=f"адресация устарела: {exc.message}",
                            old_text=item.translation_text,
                        )
                    )
                    continue
                outcome = fixer.fix_segment(
                    segment_id=item.segment_id,
                    doc=item.doc,
                    entry=entry,
                    source_xml=source_xml,
                    translation_xml=translation_xml,
                    style_note=style_note,
                )
                outcomes.append(outcome)
                if outcome.applied and outcome.new_html is not None:
                    doc_state[item.doc] = patch_block(
                        doc_state[item.doc], item.translation_xpath,
                        outcome.new_html.encode(),
                    )
                    quality_logger.info(
                        "glossary_fix: %s",
                        json.dumps(
                            {
                                "kind": "glossary_fix",
                                "segment_id": item.segment_id,
                                "doc": item.doc,
                                "term": item.term,
                                "old_variant": outcome.old_text,
                                "new_variant": outcome.new_text,
                                "prompt_file": outcome.prompt_file,
                                "prompt_version": outcome.prompt_version,
                                "model": outcome.model,
                                "timestamp": time.strftime(
                                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                                ),
                            },
                            ensure_ascii=False,
                        ),
                    )
        changed_docs = {
            doc: state
            for doc, state in doc_state.items()
            if state != doc_original.get(doc, state)
        }
        return outcomes, changed_docs

    def _l3_rebuild(
        self, job_dir: Path, applied: list[FixOutcome], changed_docs: dict[str, bytes]
    ) -> dict[str, Any] | None:
        """Атомарная пересборка (L3.5): pre-fix резервная копия (стабильная,
        T-L3-15) → `.partial` → единый механизм публикации (Rule 8:
        fsync → валидация → EPUBCheck → бэкап E5.9 → os.replace → fsync
        каталога). Правок нет — пересборки нет (None)."""
        if not applied:
            return None
        target = job_dir / "target.epub"
        l3bak = job_dir / "target.epub.l3bak"
        if not l3bak.exists():
            l3bak.write_bytes(target.read_bytes())
        partial = job_dir / "target.epub.partial"
        rebuild_epub(target, changed_docs, partial)
        epubcheck_out, _bak = atomic_finalize(partial, target, self._settings)
        return {
            "version": epubcheck_out.version,
            "available": epubcheck_out.available,
            "errors": len(epubcheck_out.errors),
            "warnings": len(epubcheck_out.warnings),
            "unavailable_reason": epubcheck_out.unavailable_reason,
        }

    def _l3_finalize_quality(
        self,
        job_dir: Path,
        *,
        selection: FixSelection,
        outcomes: list[FixOutcome],
        share: float,
        cap: float,
        attempted: int,
        applied: int,
        editable_after: float | None,
    ) -> list[str]:
        """Секция L3 в quality.json + ФИНАЛЬНЫЕ предупреждения стадии (QC +
        отклонённые правки + unverified-термины, L3-1/L3-9)."""
        quality_path = job_dir / "quality.json"
        quality = self._read_json(quality_path) if quality_path.is_file() else {}
        base_warnings = list(quality.get("warnings", ()))
        quality["l3"] = {
            "fixable_segment_share": round(share, 6),
            "max_segment_share": cap,
            "attempted": attempted,
            "applied": applied,
            "rejected": attempted - applied,
            "editable_share_after": editable_after,
            "report": "l3_fix_report.json",
        }
        quality["warnings"] = [*base_warnings, *self._l3_stage_warnings(selection, outcomes)]
        self._write_json(quality_path, quality)
        quality_logger = logging.getLogger(QUALITY_LOGGER)
        quality_logger.info("QC после L3: %s", json.dumps(quality, ensure_ascii=False))
        return list(quality["warnings"])

    def _l3_epubcheck_now(self, job_dir: Path) -> dict[str, Any]:
        """EPUBCheck-секция свежего измерения: результат стадии translating;
        если правка уже опубликована (target отличается от l3bak — возобновление
        после прерванной пересборки), файл перепроверяется заново (старый
        результат больше не соответствует файлу)."""
        target = job_dir / "target.epub"
        l3bak = job_dir / "target.epub.l3bak"
        fixed = False
        if l3bak.is_file():
            try:
                fixed = l3bak.read_bytes() != target.read_bytes()
            except OSError:
                fixed = False
        if not fixed:
            return self._l3_epubcheck_fallback(job_dir)
        outcome = run_epubcheck(self._settings.epubcheck_jar_path, target, 120)
        return {
            "version": outcome.version,
            "available": outcome.available,
            "errors": len(outcome.errors),
            "warnings": len(outcome.warnings),
            "unavailable_reason": outcome.unavailable_reason,
        }

    def _record_l3(self, repo: JobRepository, job: Job) -> None:
        """Запись результата стадии L3 (no-op/halt: ключ = input_hash, L3.8)."""
        repo.record_stage_result(
            job.id, "glossary_fix", self.input_hash(job),
            str(self._settings.job_dir(job.id) / "l3_fix_report.json"),
        )

    @staticmethod
    def _l3_alignment(data: dict[str, Any]) -> AlignmentReport:
        """Карта выравнивания из alignment_report.json (переадресация L3 —
        измеренные translation_xpath; после правки карта пересобирается —
        E6.7, пересчитывается в L3.9)."""
        docs: list[DocAlignment] = []
        for d in data.get("docs", ()):
            doc_name = str(d.get("doc", ""))
            aligned = tuple(
                AlignedPair(
                    segment_id=str(p["segment_id"]),
                    doc=doc_name,
                    source_xpath=str(p["source_xpath"]),
                    source_text=str(p["source_text"]),
                    translation_xpath=str(p["translation_xpath"]),
                    translation_text=str(p["translation_text"]),
                    how=str(p["how"]),
                )
                for p in d.get("aligned", ())
            )
            unaligned = tuple(
                UnalignedSegment(
                    segment_id=str(u["segment_id"]),
                    doc=doc_name,
                    source_xpath=str(u["source_xpath"]),
                    source_text=str(u["source_text"]),
                    reason=str(u["reason"]),
                    detail=str(u["detail"]),
                )
                for u in d.get("unaligned", ())
            )
            extra = tuple(
                ExtraBlock(doc=doc_name, xpath=str(e["xpath"]), text=str(e["text"]))
                for e in d.get("extra_blocks", ())
            )
            docs.append(
                DocAlignment(doc=doc_name, aligned=aligned, unaligned=unaligned, extra_blocks=extra)
            )
        return AlignmentReport(
            submit_mode=str(data.get("submit_mode", "")),
            min_coverage=float(data.get("min_coverage", 0.0)),
            total=int(data.get("total", 0)),
            aligned_count=int(data.get("aligned", 0)),
            unaligned_count=int(data.get("unaligned", 0)),
            extra_count=int(data.get("extra_blocks", 0)),
            coverage=float(data.get("coverage", 0.0)),
            identity_mismatches=int(data.get("identity_mismatches", 0)),
            docs=tuple(docs),
        )

    @staticmethod
    def _l3_epubcheck_fallback(job_dir: Path) -> dict[str, Any]:
        """EPUBCheck-секция для QC при пересчёте без пересборки (файл не
        менялся — результат валиден по measurement'у стадии translating)."""
        tr_file = job_dir / "translation_result.json"
        if tr_file.is_file():
            data: dict[str, Any] = json.loads(tr_file.read_text(encoding="utf-8"))
            return dict(data.get("epubcheck", {}))
        return {}

    @staticmethod
    def _l3_stage_warnings(selection: FixSelection, outcomes: list[FixOutcome]) -> list[str]:
        """Предупреждения стадии L3 (в финальный статус задания)."""
        warnings: list[str] = []
        rejected = [o for o in outcomes if not o.applied]
        if rejected:
            first = [f"{o.term} ({o.segment_id}): {o.reason}" for o in rejected[:10]]
            warnings.append(
                f"L3: {len(rejected)} правка(и) не прошли валидацию — "
                f"расхождения неустранены: " + "; ".join(first)
            )
        unverified_bad = [u for u in selection.unverified_terms if u.consistent < u.occurrences]
        if unverified_bad:
            occ = sum(u.occurrences for u in unverified_bad)
            cons = sum(u.consistent for u in unverified_bad)
            share = cons / occ if occ else 1.0
            names = ", ".join(u.term for u in unverified_bad)
            warnings.append(
                f"L3: термины с не подтверждённой парадигмой (автоправки нет, "
                f"только отчёт): {names}; собственная доля {cons}/{occ} ({share:.1%})"
            )
        return warnings

    @staticmethod
    def _unverified_share(unverified: tuple[UnverifiedTerm, ...]) -> float | None:
        occ = sum(u.occurrences for u in unverified)
        if not occ:
            return None
        return round(sum(u.consistent for u in unverified) / occ, 6)

    def _editable_share(
        self, glossary: Glossary, l2_path: Path, snapshot: dict[str, Any] | None = None
    ) -> float | None:
        """L3-1: доля согласованных вхождений по ПРАВИМОМУ множеству (термины
        с подтверждённой парадигмой); None — вхождений нет."""
        data = snapshot if snapshot is not None else self._read_json(l2_path)
        verified = {
            t.term for t in glossary.terms if t.paradigm_status == PARADIGM_VERIFIED
        }
        occ = sum(
            int(t["occurrences"]) for t in data.get("terms", ()) if t["term"] in verified
        )
        if not occ:
            return None
        cons = sum(
            int(t["consistent"]) for t in data.get("terms", ()) if t["term"] in verified
        )
        return round(cons / occ, 6)
