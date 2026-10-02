# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
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
    entries: tuple[GlossaryEntry, ...],
    limit_chars: int,
    prefix: str = "",
) -> tuple[str | None, int, int, int]:
    """Собирает user_prompt глоссария с лимитом.

    Возвращает (user_prompt | None, всего терминов, вошло, символов в prompt).
    Усечение жёсткое: строка, не влезающая в лимит, отбрасывается вместе со всеми
    последующими (порядок — по приоритету). Если не влез ни один термин —
    user_prompt = None (глоссарий не передаётся, усечение = все термины).

    `prefix` (L2.7): префикс user_prompt (стилевой профиль книги); стоит
    ПЕРЕД заголовком глоссария и учитывается в том же лимите. Термины без
    влезшего префикса/заголовка — как и раньше: None (усечение = все термины);
    префикс без терминов — сам префикс.
    """
    ordered = sorted(entries, key=lambda entry: (-entry.occurrences, entry.term))
    parts: list[str] = []
    lines: list[str] = []
    total_chars = 0
    if prefix and len(prefix) <= limit_chars:
        parts.append(prefix)
        total_chars += len(prefix)
    if entries and total_chars + len(_GLOSSARY_HEADER) <= limit_chars:
        parts.append(_GLOSSARY_HEADER)
        total_chars += len(_GLOSSARY_HEADER)
        for entry in ordered:
            line = f"- {entry.term} → {entry.translation}\n"
            if total_chars + len(line) > limit_chars:
                break
            lines.append(line)
            parts.append(line)
            total_chars += len(line)
    if entries and not lines:
        # Ни один термин не влез — глоссарий не передаётся, усечение = все
        # термины (E5-7); головый заголовок без терминов не создаётся.
        return None, len(entries), 0, 0
    if not parts:
        return None, 0, 0, 0
    prompt = "".join(parts)
    return prompt, len(entries), len(lines), len(prompt)
