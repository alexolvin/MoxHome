"""Конвейер задания (E3): исполнение этапов с идемпотентностью (E3.6).

- validating: ридер (E2) → validation.json; подозрительная книга → NEEDS_CONFIRMATION.
- planning: план перевода (параметры движка + структура книги) → plan.json.
- translating: движок epub-translator; кэш — в каталоге задания (E3.7). В E5 этап
  заменяется адаптером TranslationEngine (D17), механизм идемпотентности сохраняется.
- analyzing / generating: в E3 — явные плейсхолдеры (реализация — L1/S-этапы).
- validating_output: детерминированная проверка результата ридером (E2) → quality.json.

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

from epub_translator import LLM, SubmitKind, translate

from app.core.config import Settings
from app.core.errors import OutputValidationFailed
from app.core.logging import QUALITY_LOGGER
from app.domain.job import Job
from app.epub.reader import read_epub

if TYPE_CHECKING:
    from app.storage.jobs_repo import JobRepository

logger = logging.getLogger("app.worker.pipeline")

SUBMIT_MODE = "APPEND_BLOCK"  # bilingual по умолчанию (E5: режимы задания)


class Pipeline:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

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

    # ------------------------------------------------------------------ этапы

    def run_validating(self, repo: JobRepository, job: Job) -> bool:
        """Читает книгу ридером (E2.5: до любого LLM). Возвращает: книга подозрительна."""
        if self._skipped(repo, job, "validating"):
            return bool(self._read_json(self.job_dir(job.id) / "validation.json")["suspicious"])
        book = read_epub(Path(job.source_path), self.job_dir(job.id) / "unpack", self._settings)
        payload = {
            "title": book.title,
            "language": book.language,
            "suspicious": book.suspicious,
            "suspicious_reasons": list(book.suspicious_reasons),
            "stats": asdict(book.stats),
        }
        artifact = self.job_dir(job.id) / "validation.json"
        self._write_json(artifact, payload)
        repo.record_stage_result(job.id, "validating", self.input_hash(job), str(artifact))
        return book.suspicious

    def run_planning(self, repo: JobRepository, job: Job) -> None:
        if self._skipped(repo, job, "planning"):
            return
        validation = self._read_json(self.job_dir(job.id) / "validation.json")
        profile = self._settings.llm.profile("translation")
        plan = {
            "target_language": job.target_language,
            "submit_mode": SUBMIT_MODE,
            "engine": {
                "max_group_tokens": profile.max_group_tokens,
                "concurrency": profile.concurrency,
                "max_retries": profile.max_retries,
                "timeout_seconds": profile.timeout_seconds,
                "cache_dir": str(self.job_dir(job.id) / "engine_cache"),
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
        """Перевод движком; кэш — внутри каталога задания (E3.7). Возвращает число fill-сбоев."""
        fill_failures: list[Any] = []

        def _record_fill_failed(event: Any) -> None:
            fill_failures.append(event)
            logger.error(
                "fill не удался (retried=%s over_maximum=%s): %s",
                getattr(event, "retried_count", None),
                getattr(event, "over_maximum_retries", None),
                getattr(event, "error_message", event),
            )

        if self._skipped(repo, job, "translating"):
            return 0

        # Движок подключается здесь: в E5 эти строки заменяются адаптером
        # TranslationEngine (D17), остальная логика этапа не меняется.
        settings = self._settings
        cache_dir = self.job_dir(job.id) / "engine_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.job_dir(job.id) / "target.epub"
        translation_profile = settings.llm.profile("translation")
        fill_profile = settings.llm.profile("fill")
        base_url, model, api_key = settings.llm.endpoint("translation")

        translation_llm: Any = LLM(
            key=api_key,
            url=base_url,
            model=model,
            token_encoding=translation_profile.token_encoding,
            cache_path=cache_dir,
            timeout=translation_profile.timeout_seconds,
            temperature=translation_profile.temperature,
            retry_times=translation_profile.retry_times,
            log_dir_path=self.job_dir(job.id) / "engine_log" / "translation",
        )
        fill_llm: Any = LLM(
            key=api_key,
            url=base_url,
            model=model,
            token_encoding=fill_profile.token_encoding,
            cache_path=cache_dir,
            timeout=fill_profile.timeout_seconds,
            temperature=fill_profile.temperature,
            retry_times=fill_profile.retry_times,
            log_dir_path=self.job_dir(job.id) / "engine_log" / "fill",
        )

        counters = {"translation": 0, "fill": 0}

        def _flush_tokens() -> None:
            delta_t = int(translation_llm.total_tokens) - counters["translation"]
            delta_f = int(fill_llm.total_tokens) - counters["fill"]
            counters["translation"] = int(translation_llm.total_tokens)
            counters["fill"] = int(fill_llm.total_tokens)
            repo.add_tokens(job.id, "translation", delta_t)
            repo.add_tokens(job.id, "fill", delta_f)

        def _on_progress(progress: float) -> None:
            repo.set_progress(job.id, progress)
            _flush_tokens()
            # Кооперативные проверки между группами (E3.8).
            control_check()

        try:
            translate(
                source_path=Path(job.source_path),
                target_path=target,
                target_language=job.target_language,
                submit=SubmitKind.APPEND_BLOCK,
                max_retries=translation_profile.max_retries,
                max_group_tokens=translation_profile.max_group_tokens,
                concurrency=translation_profile.concurrency,
                translation_llm=translation_llm,
                fill_llm=fill_llm,
                on_progress=_on_progress,
                on_fill_failed=_record_fill_failed,
            )
        finally:
            _flush_tokens()

        failures_file = self.job_dir(job.id) / "fill_failures.json"
        self._write_json(failures_file, {"count": len(fill_failures)})
        repo.record_stage_result(
            job.id,
            "translating",
            self.input_hash(job),
            str(target),
            tokens=counters["translation"] + counters["fill"],
        )
        return len(fill_failures)

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
        """Детерминированная проверка выходного EPUB ридером (E2). Возвращает предупреждения."""
        if self._skipped(repo, job, "validating_output"):
            return list(self._read_json(self.job_dir(job.id) / "quality.json")["warnings"])
        failures_file = self.job_dir(job.id) / "fill_failures.json"
        if failures_file.is_file():
            fill_failures = int(self._read_json(failures_file)["count"])
        else:
            fill_failures = 0
        target = self.job_dir(job.id) / "target.epub"
        if not target.is_file():
            raise OutputValidationFailed("выходной файл не создан: target.epub")
        if not zipfile.is_zipfile(target):
            raise OutputValidationFailed("выходной файл не является ZIP-архивом")
        try:
            out_book = read_epub(target, self.job_dir(job.id) / "out_unpack", self._settings)
        except Exception as exc:
            raise OutputValidationFailed(f"выходной EPUB не читается ридером: {exc}") from exc

        in_stats = self._read_json(self.job_dir(job.id) / "validation.json")["stats"]
        checks = {
            "chapter_count_in": in_stats["chapter_count"],
            "chapter_count_out": out_book.stats.chapter_count,
            "segment_count_out": out_book.stats.segment_count,
            "chapters_preserved": in_stats["chapter_count"] == out_book.stats.chapter_count,
        }
        warnings: list[str] = []
        if fill_failures > 0:
            warnings.append(f"fill не удался в {fill_failures} группе(ах); фрагменты не переведены")
        if out_book.stats.segment_count == 0:
            warnings.append("в выходной книге нет сегментов")
        if not checks["chapters_preserved"]:
            raise OutputValidationFailed(
                f"число глав изменилось: {checks['chapter_count_in']} "
                f"-> {checks['chapter_count_out']}"
            )
        quality = {"checks": checks, "warnings": warnings, "fill_failures": fill_failures}
        self._write_json(self.job_dir(job.id) / "quality.json", quality)
        quality_logger = logging.getLogger(QUALITY_LOGGER)
        quality_logger.info("QC выхода: %s", json.dumps(quality, ensure_ascii=False))
        repo.record_stage_result(
            job.id,
            "validating_output",
            self.input_hash(job),
            str(self.job_dir(job.id) / "quality.json"),
        )
        return warnings
