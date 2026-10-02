# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
class LangDetectException(Exception):
    """Ошибка детекции: нет текста, нет профилей, невозможно определить (code)."""

    code: int

    def __init__(self, code: int, message: str) -> None: ...
    def get_code(self) -> int: ...
