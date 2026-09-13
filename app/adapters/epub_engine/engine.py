"""Единственный адаптер над движком epub-translator (E5.1, D2, D17, D15).

Прикладной код НЕ импортирует `epub_translator` нигде, кроме этого пакета
(Rule 8). Атомарность (E5.6): движок пишет в `<target>.partial` → внутренняя
валидация → EPUBCheck → бэкап существующего результата (E5.9) → `os.replace`
→ fsync файла и каталога. Файл в состоянии записи не считается результатом
ни при каких условиях.

Кэш (D15): ключ кэша движка `sha512(messages + version:target_language)` не
включает модель (измерено в E1 P6), поэтому кэш-каталог изолирован ПО МОДЕЛИ:
`engine_cache/<модель>/` — у профилей translation и fill разные модели дают
разные каталоги (без изоляции смена модели дала бы «прозрачные» ответы старой
модели, Rule 6).

Прогресс: движок вызывает on_progress дробью 0..1 (свои веса: TOC 5%,
metadata 5%, главы 90%). Адаптер лишь синхронизирует счётчики токенов и
транслирует вызов; «реальные единицы» (глава X / N, сегментов A / B) считает
конвейер, знающий структуру книги (E5.3).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from epub_translator import LLM, SubmitKind, translate
from epub_translator.xml_translator.callbacks import (
    FillFailedEvent as _EngineFillFailedEvent,
)

from app.adapters.epub_engine.atomic import atomic_finalize
from app.adapters.epub_engine.epubcheck import EpubcheckOutcome
from app.adapters.epub_engine.glossary import build_glossary_prompt
from app.adapters.epub_engine.models import (
    EngineUsage,
    FillFailedEvent,
    SubmitMode,
    TranslationRequest,
    TranslationResult,
    ValidationStatus,
)
from app.core.config import Settings

logger = logging.getLogger("app.adapter.epub_engine")

#: Отображение режима адаптера → движковый SubmitKind (E5.2).
_SUBMIT_BY_MODE: dict[SubmitMode, SubmitKind] = {
    SubmitMode.REPLACE: SubmitKind.REPLACE,
    SubmitMode.APPEND_BLOCK: SubmitKind.APPEND_BLOCK,
    SubmitMode.APPEND_TEXT: SubmitKind.APPEND_TEXT,
}

_UNSAFE_MODEL_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _safe_model_dir(model: str) -> str:
    """Имя каталога кэша по имени модели (без спецсимволов пути)."""
    return _UNSAFE_MODEL_CHARS.sub("_", model) or "model"


class EpubTranslatorEngine:
    """Единственная реализация TranslationEngine (E5.1).

    Конструируется конвейером (композиция) с настройками и каталогом задания;
    внешний интерфейс — только `translate_book` (Protocol). `usage` —
    накопительные счётчики токенов для flush'а конвейера (E4.7, тракт T)
    на границах групп.
    """

    def __init__(self, settings: Settings, job_dir: Path) -> None:
        self._settings = settings
        self._job_dir = job_dir
        self._usage = EngineUsage()
        self._progress_calls = 0

    @property
    def usage(self) -> EngineUsage:
        """Снимок накопленных токенов (конвейер сравнивает с предыдущим)."""
        return self._usage

    def translate_book(self, request: TranslationRequest) -> TranslationResult:
        settings = self._settings
        partial = request.target_path.with_name(request.target_path.name + ".partial")
        if partial.exists():
            # Старый .partial прерванного прогона — не результат; новая попытка с нуля.
            partial.unlink()

        user_prompt, total_terms, kept_terms, prompt_chars = build_glossary_prompt(
            request.glossary,
            request.glossary_limit_chars,
            request.glossary_prefix,
        )
        dropped_terms = total_terms - kept_terms

        translation_profile = settings.llm.profile("translation")
        translation_llm, fill_llm = self._make_llms(settings)

        def _sync_usage() -> None:
            self._usage = EngineUsage(
                translation_input=int(translation_llm.input_tokens),
                translation_input_cache=int(translation_llm.input_cache_tokens),
                translation_output=int(translation_llm.output_tokens),
                fill_input=int(fill_llm.input_tokens),
                fill_input_cache=int(fill_llm.input_cache_tokens),
                fill_output=int(fill_llm.output_tokens),
            )

        def _on_progress(progress: float) -> None:
            self._progress_calls += 1
            _sync_usage()
            if request.on_progress is not None:
                request.on_progress(progress)

        def _on_fill_failed(event: _EngineFillFailedEvent) -> None:
            if request.on_fill_failed is not None:
                request.on_fill_failed(
                    FillFailedEvent(
                        error_message=str(event.error_message),
                        retried_count=int(event.retried_count),
                        over_maximum_retries=bool(event.over_maximum_retries),
                    )
                )

        submit = _SUBMIT_BY_MODE[request.mode]
        # Адаптер не переинтерпретирует исключения движка: кооперативные
        # (WorkerCancelled/WorkerPaused/LeaseLost) и фактические сбои
        # propagate вверх; семантику «тракт T недоступен» (T-E5-9) назначает
        # конвейер, знающий контекст эндпоинта и контроль (E3.8).
        translate(
            source_path=request.source_path,
            target_path=partial,
            target_language=request.target_language,
            submit=submit,
            user_prompt=user_prompt,
            max_retries=translation_profile.max_retries,
            max_group_tokens=translation_profile.max_group_tokens,
            concurrency=translation_profile.concurrency,
            translation_llm=translation_llm,
            fill_llm=fill_llm,
            on_progress=_on_progress,
            on_fill_failed=_on_fill_failed,
        )
        _sync_usage()
        epubcheck, backup_path = self._finalize(partial, request.target_path, settings)

        validation_status = (
            ValidationStatus.OK if epubcheck.available else ValidationStatus.UNAVAILABLE
        )
        result = TranslationResult(
            target_path=request.target_path,
            usage=self._usage,
            glossary_total_terms=total_terms,
            glossary_kept_terms=kept_terms,
            glossary_dropped_terms=dropped_terms,
            glossary_prompt_chars=prompt_chars,
            epubcheck_version=epubcheck.version,
            epubcheck_errors=len(epubcheck.errors) + len(epubcheck.fatals),
            epubcheck_warnings=len(epubcheck.warnings),
            validation_status=validation_status,
            validation_unavailable_reason=epubcheck.unavailable_reason,
            backup_path=backup_path,
            progress_call_count=self._progress_calls,
        )
        if epubcheck.available:
            logger.info(
                "EPUBCheck %s: %d fatal, %d error, %d warning",
                epubcheck.version,
                len(epubcheck.fatals),
                len(epubcheck.errors),
                len(epubcheck.warnings),
            )
        else:
            logger.warning("EPUBCheck недоступен: %s", epubcheck.unavailable_reason)
        return result

    # ------------------------------------------------------------- служебное

    def _make_llms(self, settings: Settings) -> tuple[Any, Any]:
        """LLM-объекты профилей translation/fill (D15: кэш изолирован ПО МОДЕЛИ)."""
        translation_profile = settings.llm.profile("translation")
        fill_profile = settings.llm.profile("fill")
        base_url, model, api_key = settings.llm.endpoint("translation")
        fill_url, fill_model, fill_api_key = settings.llm.endpoint("fill")

        cache_root = self._job_dir / "engine_cache"
        translation_cache = cache_root / _safe_model_dir(model)
        fill_cache = cache_root / _safe_model_dir(fill_model)
        translation_cache.mkdir(parents=True, exist_ok=True)
        fill_cache.mkdir(parents=True, exist_ok=True)

        translation_llm: Any = LLM(
            key=api_key,
            url=base_url,
            model=model,
            token_encoding=translation_profile.token_encoding,
            cache_path=translation_cache,
            timeout=translation_profile.timeout_seconds,
            temperature=translation_profile.temperature,
            retry_times=translation_profile.retry_times,
            log_dir_path=self._job_dir / "engine_log" / "translation",
        )
        fill_llm: Any = LLM(
            key=fill_api_key,
            url=fill_url,
            model=fill_model,
            token_encoding=fill_profile.token_encoding,
            cache_path=fill_cache,
            timeout=fill_profile.timeout_seconds,
            temperature=fill_profile.temperature,
            retry_times=fill_profile.retry_times,
            log_dir_path=self._job_dir / "engine_log" / "fill",
        )
        return translation_llm, fill_llm

    def _finalize(
        self, partial: Path, target: Path, settings: Settings
    ) -> tuple[EpubcheckOutcome, Path | None]:
        """Атомарное завершение (E5.6): общий механизм (Rule 8) — см.
        app/adapters/epub_engine/atomic.py; L3 пересобирает выход тем же путём."""
        return atomic_finalize(partial, target, settings)
