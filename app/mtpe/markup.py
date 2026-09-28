"""Кодек внутренней разметки сегмента (MTPE §4.7) — единственный для M3, M4, M5.

Внутристрочные элементы сегмента нумеруются в порядке документа и заменяются
маркерами: парные — `⟦n⟧…⟦/n⟧`, пустые (`br`, `img` и т.п.) — `⟦n/⟧`. Карта
`n → (тег, атрибуты в исходном порядке)` хранится с сегментом; восстановление
идёт только из карты — модель не пишет теги и атрибуты.

- Литеральные `⟦`/`⟧` в тексте источника экранируются U+E000/U+E001 при кодировании
  и восстанавливаются при декодировании (§4.7 п.4).
- `src_encoded` — текст с маркерами (вход/выход постредактуры M4). Кодек обратим:
  `decode(src_encoded, markup_map) == marked_text` побайтно (§4.7 п.6).
- `src_plain` — текст без маркеров, `br` → `\n` (вход черновика, M3; разбивка по `\n`).

Замена делается на уровне строки (сохраняет исходные байты тегов), а не через
пере-сериализацию дерева — это гарантирует побайтовую обратимость.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

#: Маркеры (§4.7): `⟦` U+27E6 / `⟧` U+27E7. Литералы экранируются в U+E000/U+E001.
OPEN = "\u27e6"  # ⟦
CLOSE = "\u27e7"  # ⟧
_ESCAPE_OPEN = "\ue000"
_ESCAPE_CLOSE = "\ue001"

#: XHTML-void элементы (не имеют закрывающего тега).
_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

#: Один тег: `<name …>`, `</name>` или `<name …/>`. `>` в атрибутных значениях
#: у внутристрочных элементов EPUB не встречается.
_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
#: Маркер в закодированном тексте: `⟦n⟧` (открыть), `⟦/n⟧` (закрыть), `⟦n/⟧` (пустой).
_MARKER_RE = re.compile(
    re.escape(OPEN) + r"(?:/(\d+)|(\d+)/|(\d+))" + re.escape(CLOSE)
)


@dataclass(frozen=True)
class EncodedSegment:
    """Результат кодирования сегмента (§4.8: поля segments.jsonl)."""

    src_encoded: str
    src_plain: str
    #: (open_tag, close_tag) по n (0-based); close_tag = "" для пустых элементов.
    markup_map: tuple[tuple[str, str], ...]


def _localname(tag: str) -> str:
    inner = tag[2:-1] if tag.startswith("</") else tag[1:-1]  # снять `</`/`<` и `>`
    inner = inner.rstrip()
    if inner.endswith("/"):  # self-closing `/>`
        inner = inner[:-1]
    parts = inner.split()
    return parts[0].lower() if parts else ""


def _is_empty(tag: str) -> bool:
    if tag.startswith("</"):
        return False
    if tag.rstrip().endswith("/>"):
        return True
    return _localname(tag) in _VOID


def _escape_literals(text: str) -> str:
    return text.replace(OPEN, _ESCAPE_OPEN).replace(CLOSE, _ESCAPE_CLOSE)


def _pop_matching(stack: list[int], markup_map: list[tuple[str, str]], name: str) -> int:
    if stack and _localname(markup_map[stack[-1]][0]) == name:
        return stack.pop()
    for i in range(len(stack) - 1, -1, -1):
        if _localname(markup_map[stack[i]][0]) == name:
            del stack[i:]
            return i
    raise ValueError(f"непаренный закрывающий тег: {name!r}")


def encode(marked_text: str) -> EncodedSegment:
    """Закодить внутреннюю разметку блока в (src_encoded, src_plain, markup_map)."""
    encoded: list[str] = []
    plain: list[str] = []
    markup_map: list[tuple[str, str]] = []
    stack: list[int] = []
    pos = 0
    for match in _TAG_RE.finditer(marked_text):
        text = marked_text[pos : match.start()]
        encoded.append(_escape_literals(text))
        plain.append(html.unescape(text))
        tag = match.group(0)
        pos = match.end()
        if tag.startswith("</"):
            n = _pop_matching(stack, markup_map, _localname(tag))
            markup_map[n] = (markup_map[n][0], tag)
            encoded.append(f"{OPEN}/{n + 1}{CLOSE}")
        elif _is_empty(tag):
            markup_map.append((tag, ""))
            n = len(markup_map)
            encoded.append(f"{OPEN}{n}/{CLOSE}")
            plain.append("\n" if _localname(tag) == "br" else "")
        else:
            markup_map.append((tag, ""))
            stack.append(len(markup_map) - 1)
            n = len(markup_map)
            encoded.append(f"{OPEN}{n}{CLOSE}")
    tail = marked_text[pos:]
    encoded.append(_escape_literals(tail))
    plain.append(html.unescape(tail))
    return EncodedSegment(
        src_encoded="".join(encoded),
        src_plain="".join(plain),
        markup_map=tuple(markup_map),
    )


def decode(src_encoded: str, markup_map: tuple[tuple[str, str], ...]) -> str:
    """Восстановить внутреннюю разметку из (src_encoded, markup_map) — побайтно."""

    def _repl(match: re.Match[str]) -> str:
        close_n, empty_n, open_n = match.group(1), match.group(2), match.group(3)
        if close_n is not None:
            return markup_map[int(close_n) - 1][1]
        if empty_n is not None:
            return markup_map[int(empty_n) - 1][0]
        return markup_map[int(open_n) - 1][0]

    text = _MARKER_RE.sub(_repl, src_encoded)
    return text.replace(_ESCAPE_OPEN, OPEN).replace(_ESCAPE_CLOSE, CLOSE)


def has_letters(text: str) -> bool:
    """Есть ли буквы (любой письменности) — для пропуска «без перевода» (M3 §7 п.2)."""
    return any(ch.isalpha() for ch in text)
