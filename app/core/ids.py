# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Идентификаторы: job_id сортируемый по времени создания (UTC)."""

from __future__ import annotations

import os
from datetime import UTC, datetime


def new_job_id() -> str:
    """Формат: j-<YYYYmmddTHHMMSSZ>-<6 hex>. Пример: j-20260912T063000Z-a1b2c3."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"j-{stamp}-{os.urandom(3).hex()}"
