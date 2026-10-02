# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Модели предметной области (Book, Chapter, Segment; Job — E3; Card — L1)."""

from app.domain.book import Book, BookStats, Chapter, Segment
from app.domain.job import (
    CLAIMABLE_STATES,
    NEXT_STAGE,
    TERMINAL_STATES,
    TRANSITIONS,
    WORKING_STATES,
    Job,
    JobStatus,
)

__all__ = [
    "CLAIMABLE_STATES",
    "NEXT_STAGE",
    "TERMINAL_STATES",
    "TRANSITIONS",
    "WORKING_STATES",
    "Book",
    "BookStats",
    "Chapter",
    "Job",
    "JobStatus",
    "Segment",
]
