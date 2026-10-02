# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Лёгкий разбор книги (L0): заголовок + определение языка.

Один механизм (Rule 8): ``read_epub`` + ``detect_book`` — те же примитивы,
что стадия validating (E2.5/L0.4). Потребители: API (``POST /api/books``)
и CLI (``moxhome analyze``, U2) — дублирования разбора нет.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.epub.reader import read_epub
from app.languages.detect import detect_book
from app.languages.registry import aliases_map, load_registry


def scan_book(settings: Settings, path: Path) -> dict[str, Any]:
    """Разбор книги: заголовок, статистика, L0-решение по исходному языку.

    ``detect_book`` возвращает решение L0.5 (detected/accepted/reasons);
    вероятности — топ-3. Сбоя файла — ``InputValidationError`` (ридери, E2.5).
    """
    scan_dir = settings.working_dir / f"scan-{uuid.uuid4().hex[:8]}"
    try:
        book = read_epub(path, scan_dir, settings)
        report = detect_book(
            book, settings, aliases=aliases_map(load_registry(settings.languages_root))
        )
    finally:
        shutil.rmtree(scan_dir, ignore_errors=True)
    probabilities = dict(sorted(report.probabilities.items(), key=lambda kv: -kv[1])[:3])
    return {
        "title": book.title,
        "dc_language": report.dc_language,
        "detected": report.detected,
        "confidence": round(report.confidence, 4) if report.confidence is not None else None,
        "probabilities": probabilities,
        "decision": report.decision.value,
        "accepted_language": report.accepted_language,
        "reasons": list(report.reasons),
        "stats": {
            "chapter_count": book.stats.chapter_count,
            "segment_count": book.stats.segment_count,
            "word_count": book.stats.word_count,
        },
    }
