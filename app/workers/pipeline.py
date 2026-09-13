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

Идемпотентность (E3.6): результат этапа адресуется ключом
hash(входные данные + конфигурация + версия промпта); повтор с тем же ключом —
без повторного исполнения (повторный перевод не порождается, E3-3).
"""

from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.adapters.epub_engine import (
    EngineUsage,
    EpubTranslatorEngine,
    FillFailedEvent,
    GlossaryEntry,
    SubmitMode,
    TranslationRequest,
)
from app.core.config import Settings
from app.core.errors import AppError, LLMUnavailableError, OutputValidationFailed
from app.core.logging import QUALITY_LOGGER
from app.domain.job import Job
from app.epub import aligner
from app.epub.reader import read_epub
from app.languages.detect import LanguageReport, detect_book
from app.languages.nlp import nlp_report
from app.languages.registry import PackSpec, aliases_map, load_registry
from app.languages.resolver import PairReport, PairResolver, PairStatus
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

        glossary: tuple[GlossaryEntry, ...] = ()
        if self._glossary_provider is not None:
            glossary = tuple(self._glossary_provider(job))

        request = TranslationRequest(
            source_path=Path(job.source_path),
            target_path=target,
            target_language=job.target_language,
            mode=self._submit_mode(job.mode),
            glossary=glossary,
            glossary_limit_chars=settings.limits.glossary_max_user_prompt_chars,
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
        try:
            out_book = read_epub(target, job_dir / "out_unpack", self._settings)
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
                epubcheck=dict(tr.get("epubcheck", {})),
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
