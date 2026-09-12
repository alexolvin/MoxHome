"""Модели предметной области (Book, Chapter, Segment; Job и Card — с E3/L1)."""

from app.domain.book import Book, BookStats, Chapter, Segment

__all__ = ["Book", "BookStats", "Chapter", "Segment"]
