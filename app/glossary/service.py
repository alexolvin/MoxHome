# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Оркестрация глоссария книги (L2): сборка при первом задании версии,
загрузка при повторных (L2.8: привязка к хэшу исходного файла).

Порядок (все шаги — в `ensure_book_glossary`):
1. Регистрация версии книги (L2.8): новый хэш — новая версия, прежние
   помечаются `superseded` (не применяются молча, T-L2-9).
2. Глоссарий версии: есть в хранилище — загрузить; нет — построить:
   кандидаты (детерминированно, L2.1) → аннотация LLM (L2.2/L2.3/L2.4)
   → записи глоссария (L2.3) → сохранить.
3. Стилевой профиль (L2.7): один раз на версию книги, хранится с книгой;
   пробел текста — из конфигурации (glossary.style_sample_chars).

Сбой любого шага — GlossaryError (fail closed, Rule 6): задание не
переводится «на глазок» без глоссария, если глоссарий включён.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.book import Book
from app.domain.glossary import Glossary, StyleProfile
from app.domain.job import Job
from app.glossary.annotation import (
    GlossaryError,
    annotate_candidates,
    terms_from_annotations,
)
from app.glossary.candidates import extract_candidates
from app.glossary.store import BookStore, book_key_of
from app.languages.nlp import load_nlp
from app.languages.registry import PackSpec
from app.llm.analysis import AnalysisClient
from app.llm.tasks import task
from app.storage.jobs_repo import JobRepository


@dataclass(frozen=True)
class BookGlossaryBundle:
    """Результат ensure: глоссарий версии + стилевой профиль + флаг «собран
    сейчас» (False — загружен из хранилища, повторная аннотация не делалась)."""

    glossary: Glossary
    style: StyleProfile | None
    built_now: bool


def ensure_book_glossary(
    settings: Settings,
    repo: JobRepository,
    job: Job,
    *,
    book: Book,
    source_pack: PackSpec,
    target_pack: PackSpec,
) -> BookGlossaryBundle:
    if not settings.glossary.enabled:
        raise GlossaryDisabledError(
            "глоссарий выключен в конфигурации (glossary.enabled: false, L2/D60)"
        )
    store = BookStore(settings.books_dir)
    book_key = book_key_of(job.source_path)
    source_hash = job.input_sha256

    # L2.8: версия = хэш исходного файла; прежние версии — superseded.
    store.register_version(book_key, source_hash)

    glossary = store.load_glossary(book_key, source_hash)
    style = store.load_style(book_key, source_hash)
    built_now = False

    if glossary is None:
        nlp = load_nlp(source_pack)
        candidates = extract_candidates(book, source_pack, nlp, settings)
        annotations = annotate_candidates(
            candidates,
            settings,
            repo,
            job_id=job.id,
            source_lang=source_pack.code,
            target_lang=job.target_language,
            names_by_pos=dict(target_pack.paradigm_forms),
        )
        glossary = Glossary(
            source_language=source_pack.code,
            target_language=job.target_language,
            terms=terms_from_annotations(candidates, annotations),
            source_hash=source_hash,
        )
        store.save_glossary(book_key, source_hash, glossary)
        built_now = True

    if style is None:
        sample = ""
        for chapter in book.chapters:
            for segment in chapter.segments:
                sample += segment.plain_text + "\n"
                if len(sample) >= settings.glossary.style_sample_chars:
                    break
            if len(sample) >= settings.glossary.style_sample_chars:
                break
        style = _determine_style(
            settings,
            repo,
            job,
            source_lang=source_pack.code,
            target_lang=job.target_language,
            sample=sample,
        )
        store.save_style(book_key, source_hash, style)

    return BookGlossaryBundle(glossary=glossary, style=style, built_now=built_now)


class GlossaryDisabledError(AppError):
    """Глоссарий включён в код вызова, но выключен в конфигурации (L2/D60)."""


def _determine_style(
    settings: Settings,
    repo: JobRepository,
    job: Job,
    *,
    source_lang: str,
    target_lang: str,
    sample: str,
) -> StyleProfile:
    client = AnalysisClient(settings, repo, job.id)
    outcome = client.run_task(task("book_style"), source_lang, target_lang, sample)
    items: Any = outcome.payload.get("items")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise GlossaryError("ответ LLM (book_style): пустой список items")
    first: dict[str, Any] = items[0]
    return StyleProfile(
        style=str(first["style"]),
        region=first.get("region"),
        note=first.get("note"),
    )


def glossary_prefix(style: StyleProfile | None) -> str:
    """Строка стилевого профиля для user_prompt (L2.7: «применяется во всех
    последующих заданиях по ней»; механизм — префикс глоссарийного
    user_prompt, E5.5: user_prompt действует только в translate-шаблоне)."""
    if style is None:
        return ""
    region = f" (region: {style.region})" if style.region else ""
    return f"Style profile: {style.style}{region}. Keep this style consistently throughout.\n"
