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
import zipfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

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
from app.languages.nlp import load_nlp, nlp_report
from app.languages.registry import PackSpec, aliases_map, load_registry
from app.languages.resolver import PairReport, PairResolver, PairStatus
from app.learning.cards import assemble_card, check_vocab_cards
from app.learning.selection import select_lexis_candidates
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


class Pipeline:
    def __init__(
        self, settings: Settings, glossary_provider: GlossaryProvider | None = None
    ) -> None:
        self._settings = settings
        self._glossary_provider = glossary_provider

    # ------------------------------------------------------------- служебное

    def job_dir(self, job_id: str) -> Path:
        return self._settings.jobs_dir / job_id

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
        book = read_epub(Path(job.source_path), self.job_dir(job.id) / "unpack", settings)
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
        self._write_json(self.job_dir(job.id) / "glossary_consistency.json", report_dict)
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
        return self._read_json(self.job_dir(job_id) / "validation.json")

    # ------------------------------------------------------------------ этапы

    def run_validating(self, repo: JobRepository, job: Job) -> bool:
        """Читает книгу ридером (E2.5: до любого LLM) + определяет исходный язык (L0.4).

        Возвращает: книга подозрительна ИЛИ язык требует подтверждения (L0.5) —
        воркер переводит в NEEDS_CONFIRMATION. Принятый язык пишется в
        jobs.source_language; пара (принятый → целевой) перепроверяется резолвером:
        unsupported — AppError → FAILED с точным перечнем недостающего (L0.2/L0.3).
        """
        if self._skipped(repo, job, "validating"):
            payload = self._read_json(self.job_dir(job.id) / "validation.json")
            detection = payload.get("language_detection") or {}
            return bool(payload["suspicious"]) or bool(detection.get("needs_confirmation"))
        book = read_epub(Path(job.source_path), self.job_dir(job.id) / "unpack", self._settings)
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
        artifact = self.job_dir(job.id) / "validation.json"
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
        validation = self._read_json(self.job_dir(job.id) / "validation.json")
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
                "cache_root": str(self.job_dir(job.id) / "engine_cache"),
                "translation_model": self._settings.llm.profile("translation").model,
                "fill_model": self._settings.llm.profile("fill").model,
            },
            "book": validation["stats"],
        }
        self._write_json(self.job_dir(job.id) / "plan.json", plan)
        repo.record_stage_result(
            job.id, "planning", self.input_hash(job), str(self.job_dir(job.id) / "plan.json")
        )

    def run_translating(
        self, repo: JobRepository, job: Job, control_check: Callable[[], None]
    ) -> int:
        """Перевод адаптером TranslationEngine (E5.1, D17): `.partial` → валидация
        → EPUBCheck → бэкап → `os.replace` (E5.6). Возвращает число fill-сбоев."""
        if self._skipped(repo, job, "translating"):
            failures_file = self.job_dir(job.id) / "fill_failures.json"
            if failures_file.is_file():
                return int(self._read_json(failures_file)["count"])
            return 0

        settings = self._settings
        job_dir = self.job_dir(job.id)
        target = job_dir / "target.epub"
        validation = self._read_json(job_dir / "validation.json")
        toc_present = bool(validation["toc_present"])
        meta_present = bool(validation["meta_present"])
        chapter_count = int(validation["stats"]["chapter_count"])
        per_chapter = [int(x) for x in validation["per_chapter_segments"]]
        chapter_docs = [str(x) for x in validation["chapter_documents"]]
        total_segments = sum(per_chapter)

        # E5.3: последовательность фаз движка — on_progress вызывается после
        # каждой непустой фазы (concurrency=1: детерминированно).
        phases: list[tuple[str, str]] = []  # (подпись, документ)
        if toc_present:
            phases.append(("TOC", "(TOC)"))
        if meta_present:
            phases.append(("metadata", "(OPF metadata)"))
        for index in range(chapter_count):
            phases.append((f"глава {index + 1} / {chapter_count}", chapter_docs[index]))

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
            if label.startswith("глава "):
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
        self._write_json(self.job_dir(job.id) / f"{stage}.json", payload)
        repo.record_stage_result(
            job.id, stage, self.input_hash(job), str(self.job_dir(job.id) / f"{stage}.json")
        )

    def run_validating_output(self, repo: JobRepository, job: Job) -> list[str]:
        """Проверка результата: ридер (E2) + 10-пунктный QC (E5.8). Возвращает предупреждения."""
        if self._skipped(repo, job, "validating_output"):
            return list(self._read_json(self.job_dir(job.id) / "quality.json")["warnings"])
        job_dir = self.job_dir(job.id)
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
        job_dir = self.job_dir(job.id)
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
        job_dir = self.job_dir(job.id)
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
        идемпотентности (stage_results: "s1_vocabulary", далее S2–S5).
        Возвращает ФИНАЛЬНЫЕ предупреждения — по ним воркер выбирает
        COMPLETED / COMPLETED_WITH_WARNINGS.
        """
        job = repo.require(job.id)
        job_dir = self.job_dir(job.id)
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
        result = repo.get_stage_result(job.id, "s1_vocabulary")
        if result is not None and result.input_hash == key:
            quality_path = job_dir / "quality.json"
            if quality_path.is_file():
                return list(self._read_json(quality_path)["warnings"])
            return []
        return self._s1_vocabulary(repo, job, job_dir, key, glossary_hash)

    def _s1_vocabulary(
        self, repo: JobRepository, job: Job, job_dir: Path, key: str, glossary_hash: str
    ) -> list[str]:
        """S1 (ТЗ_03): отбор лексики. Детерминированная стадия (S1.1) →
        LLM-заполнение/ранжирование по главам (S1.2, общее требование 5) →
        5-пунктная валидация (S1.5) → плотность ≤ N на 1000 слов (S1-3) →
        vocabulary_cards.json. Только выровненное подмножество E6."""
        settings = self._settings
        alignment = self._l3_alignment(self._read_json(job_dir / "alignment_report.json"))
        pairs = tuple(p for d in alignment.docs for p in d.aligned)
        book = read_epub(Path(job.source_path), job_dir / "unpack", settings)
        registry = load_registry(settings.languages_root)
        source_code = job.source_language
        if source_code is None:
            raise AppError("подтверждённый исходный язык не задан (L0.5) — S1 не строится")
        source_pack = registry.get(source_code)
        target_pack = registry.get(job.target_language)
        if source_pack is None or target_pack is None:
            raise AppError(f"язык не найден в реестре: {source_code} / {job.target_language}")
        pack_dir = settings.languages_root / source_pack.code
        card_schema = load_card_schema(pack_dir, source_pack)
        nlp = load_nlp(source_pack)
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
            str(self.job_dir(job.id) / "l3_fix_report.json"),
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
