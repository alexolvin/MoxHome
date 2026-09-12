"""Модель Book → Chapter → Segment (E2, тракт A).

Все значения — результат детерминированного чтения EPUB без LLM.
`segment_id` стабилен между запусками: sha256 от пути документа в архиве,
позиционного XPath элемента и нормализованного текста (E2.3).
`char_count`/`word_count` считаются по нормализованному тексту (NFC, схлопнутые
пробелы) — это же представление участвует в оценке токенов.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    segment_id: str
    chapter_id: str
    index_in_chapter: int
    index_global: int
    plain_text: str
    marked_text: str
    char_count: int
    word_count: int


@dataclass(frozen=True)
class Chapter:
    chapter_id: str
    title: str | None
    document_path: str
    spine_index: int
    segments: tuple[Segment, ...]


@dataclass(frozen=True)
class BookStats:
    chapter_count: int
    spine_document_count: int
    segment_count: int
    char_count: int
    word_count: int
    image_count: int
    table_count: int
    footnote_count: int
    has_drm: bool
    token_estimate: int


@dataclass(frozen=True)
class Book:
    title: str | None
    language: str | None
    chapters: tuple[Chapter, ...]
    stats: BookStats
    suspicious: bool
    suspicious_reasons: tuple[str, ...]
    #: Вхождений в TOC (nav ol>li / NCX navPoint) — семантика движка (E5.3:
    #: «реальные единицы» прогресса). 0 — TOC пуст/отсутствует.
    toc_entries: int = 0
    #: Есть ли в OPF metadata-поля, которые движок переводит (title/creator/...)
    #: — семантика движка (E5.3: маппинг progress-вызовов).
    has_translatable_metadata: bool = False
