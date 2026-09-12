"""Адаптер движка epub-translator (E5.1, D2): единственная точка импорта движка.

- `TranslationEngine` — Protocol (интерфейс по ТЗ E5).
- `EpubTranslatorEngine` — единственная реализация; прикладной код работает
  через неё и не импортирует `epub_translator` (Rule 8, D17).
- Типы запроса/результата, глоссарий, EPUBCheck, внутренняя валидация —
  модули пакета (импорт движка только в `engine.py`).
"""

from app.adapters.epub_engine.engine import EpubTranslatorEngine
from app.adapters.epub_engine.models import (
    EngineUsage,
    FillFailedEvent,
    GlossaryEntry,
    SubmitMode,
    TranslationEngine,
    TranslationRequest,
    TranslationResult,
    ValidationStatus,
)

__all__ = [
    "EngineUsage",
    "EpubTranslatorEngine",
    "FillFailedEvent",
    "GlossaryEntry",
    "SubmitMode",
    "TranslationEngine",
    "TranslationRequest",
    "TranslationResult",
    "ValidationStatus",
]
