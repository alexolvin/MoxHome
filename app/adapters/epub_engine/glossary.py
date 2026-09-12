"""Глоссарий → user_prompt движка (E5.5, TZ 02 L2.5).

Лимит по размеру — из конфигурации (limits.glossary_max_user_prompt_chars).
При превышении термины отбираются по приоритету (число вхождений, убывание;
детерминированный тайбрейк — по термину) и факт усечения (число отброшенных)
фиксируется в результате → QC-отчёт, а не молча. Соблюдение глоссария моделью
на этом этапе не гарантируется (жёсткая правка — этап L3).

Замечание (измерено в E5): движок подставляет user_prompt только в шаблон
translate; в fill-шаблон user_prompt не передаётся (epub_translator 0.1.10).
"""

from __future__ import annotations

from app.adapters.epub_engine.models import GlossaryEntry

_GLOSSARY_HEADER = "Use this glossary consistently when translating:\n"


def build_glossary_prompt(
    entries: tuple[GlossaryEntry, ...], limit_chars: int
) -> tuple[str | None, int, int, int]:
    """Собирает user_prompt глоссария с лимитом.

    Возвращает (user_prompt | None, всего терминов, вошло, символов в prompt).
    Усечение жёсткое: строка, не влезающая в лимит, отбрасывается вместе со всеми
    последующими (порядок — по приоритету). Если не влез ни один термин —
    user_prompt = None (глоссарий не передаётся, усечение = все термины).
    """
    if not entries:
        return None, 0, 0, 0
    ordered = sorted(entries, key=lambda entry: (-entry.occurrences, entry.term))
    lines: list[str] = []
    total_chars = len(_GLOSSARY_HEADER)
    for entry in ordered:
        line = f"- {entry.term} → {entry.translation}\n"
        if total_chars + len(line) > limit_chars:
            break
        lines.append(line)
        total_chars += len(line)
    if not lines:
        return None, len(entries), 0, 0
    return _GLOSSARY_HEADER + "".join(lines), len(entries), len(lines), len(
        _GLOSSARY_HEADER + "".join(lines)
    )
