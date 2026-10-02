# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Писатель сегментов (MTPE M5, §4.9) — четвёртое разрешённое исключение Rule 8.

Дублирует сборку EPUB **движка** (`epub-translator 0.1.10`, эталон
`docs/ENGINE_PROBE.md`), а не тракт A (D131): текст модели из `final.jsonl`
вставляется **только как lxml-текстовые узлы**, элементы создаются **только из
`markup_map`** сегмента (маркеры `⟦n⟧`/`⟦/n⟧`/`⟦n/⟧` → теги исходника); разбор
ответа модели как XHTML **запрещён** (M5-5: `<script>`/`&`/`<b>` в тексте модели
экранируются, новых элементов вне карты 0).

Вставка/замена — по `SubmitKind` (то же поле, что у тракта T): APPEND_BLOCK —
клоновка блока `Element(tag, attribs)` + вставка sibling (id `__translated`, tail
оригинала → переводу), REPLACE — замена контента на месте (тег/id/XPath хранятся,
суффикса нет). **text-root** (как `aligner.text_root`): блок, чей текст целиком в
одном внутреннем блоке, переводит внутренний элемент (вставка ВНУТРЬ контейнера).
**Двойная сегментация** контейнера (`blockquote` + внутренний `p`) — один
переводный блок, берётся финал листового сегмента (блок == text-root).
**id-дедупликация** document-wide: 1-е вхождение хранит id, 2-е → `id__translated`,
3-е → `id__translated_2` (посыпает и id блока, и inline-якоря). **OPF/навигация —
побайто** (поток сегментов M-тракта = только spine-контент; движок меняет только
их текст, а M5-2 сравнивает скелет). Атомарная запись + EPUBCheck — **вызовы E5**
(`rebuild_epub` + `atomic_finalize`), не копии. Выравнивание — **штатный E6**;
`write_map.json` (`segment_id` → XPath блока перевода) — только для проверки M5-4.
"""

from __future__ import annotations

import json
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

from app.adapters.epub_engine.atomic import atomic_finalize
from app.core.config import Settings
from app.core.errors import AppError
from app.epub import segmenter
from app.epub.aligner import TRANSLATED_MARKER, text_root
from app.epub.reader import read_epub
from app.epub.rebuild import rebuild_epub
from app.mtpe.markup import CLOSE, OPEN

#: Поддерживаемые SubmitKind (те, что использует проект по умолчанию).
_SUPPORTED: frozenset[str] = frozenset({"APPEND_BLOCK", "REPLACE"})

#: Маркер в final (то же, что `markup._MARKER_RE`): `⟦/n⟧`/`⟦n/⟧`/`⟦n⟧`.
_MARKER_RE = re.compile(
    re.escape(OPEN) + r"(?:/(\d+)|(\d+)/|(\d+))" + re.escape(CLOSE)
)


@dataclass
class WriterResult:
    """Итоги прогона писателя (для CONTROL M5 / M6)."""

    segment_count: int = 0
    doc_count: int = 0
    write_map: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _DocContext:
    """Всё, что писателю одного документа нужно от прогона (не байты)."""

    final_by_id: dict[str, dict[str, Any]]
    seg_by_id: dict[str, dict[str, Any]]
    submit_mode: str
    block_tags: frozenset[str]


class WriterPhase:
    """Писатель сегментов: исходный EPUB + segments/final.jsonl → выходной EPUB."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._block_tags = frozenset(settings.segmentation.block_tags)

    def run(
        self,
        source_epub: Path,
        output_epub: Path,
        m_dir: Path,
        submit_mode: str,
    ) -> WriterResult:
        if submit_mode not in _SUPPORTED:
            raise AppError(
                f"писатель M5 не поддерживает SubmitKind {submit_mode!r} "
                "(по умолчанию проект использует APPEND_BLOCK / REPLACE)",
                details={"submit_mode": submit_mode},
            )
        finals = _read_jsonl(m_dir / "final.jsonl")
        segments = _read_jsonl(m_dir / "segments.jsonl")
        final_by_id = {str(r["segment_id"]): r for r in finals}
        seg_by_id = {str(r["segment_id"]): r for r in segments}

        # Книга → главы (document_path + сегменты). read_epub — общая функция
        # (тракт A), дублировать сегментацию не нужно (Rule 8).
        with tempfile.TemporaryDirectory(prefix="mtpe-m5-") as tmp:
            book = read_epub(source_epub, Path(tmp), self._settings)

        ctx = _DocContext(
            final_by_id, seg_by_id, submit_mode, self._block_tags
        )
        patches: dict[str, bytes] = {}
        write_map: dict[str, str] = {}
        doc_count = 0
        with zipfile.ZipFile(source_epub) as archive:
            for chapter in book.chapters:
                doc_path = chapter.document_path
                doc_bytes = archive.read(doc_path)
                new_bytes, doc_map = _write_doc(
                    doc_bytes, doc_path, chapter.segments, ctx
                )
                if new_bytes != doc_bytes:
                    patches[doc_path] = new_bytes
                write_map.update(doc_map)
                doc_count += 1

        partial = output_epub.with_name(output_epub.name + ".partial")
        rebuild_epub(source_epub, patches, partial)
        atomic_finalize(partial, output_epub, self._settings)
        (m_dir / "write_map.json").write_text(
            json.dumps(write_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return WriterResult(
            segment_count=len(final_by_id), doc_count=doc_count, write_map=write_map
        )


# ------------------------------------------------------------------ документ


def _write_doc(
    doc_bytes: bytes,
    doc_path: str,
    segments: tuple[Any, ...],
    ctx: _DocContext,
) -> tuple[bytes, dict[str, str]]:
    """Писатель одного spine-документа: (новые байты, write_map по сегментам)."""
    # fromstring/tostring(encoding="unicode") — та же сериализация, что rebuild.py
    # (L3/E5), Rule 8: EPUBCheck принимает XHTML без XML-декларации (движок так же).
    root = etree.fromstring(doc_bytes)
    body = segmenter.find_body(root)
    if body is None:
        raise AppError(f"в spine-документе нет <body>: {doc_path}")
    block_tags = ctx.block_tags

    # segment_id → элемент блока (та же формула, что ридер/сегментатор, E2.3).
    block_by_id: dict[str, Any] = {}
    for element in segmenter.find_block_elements(body, block_tags):
        normalized = segmenter.normalize_text(segmenter.full_text(element))
        if not normalized:
            continue
        sid = segmenter.make_segment_id(
            doc_path, segmenter.positional_xpath(element), normalized
        )
        block_by_id[sid] = element

    # Группируем сегменты документа по text-root (двойная сегментация контейнера
    # → один переводный блок; берём финал листового сегмента: блок == text-root).
    groups: dict[int, list[tuple[Any, Any]]] = {}
    for seg in segments:
        sid = str(seg.segment_id)
        if sid not in ctx.final_by_id:
            raise AppError(f"нет итога (final.jsonl) для сегмента {sid}")
        block = block_by_id.get(sid)
        if block is None:
            raise AppError(f"блок сегмента {sid} не найден в {doc_path}")
        root_el = text_root(block, block_tags)
        groups.setdefault(id(root_el), []).append((seg, block))

    doc_map: dict[str, str] = {}
    for segs in groups.values():
        leaf = next(
            ((s, b) for s, b in segs if b is text_root(b, block_tags)), None
        )
        if leaf is None:
            leaf = segs[0]
        leaf_seg, leaf_block = leaf
        root_el = text_root(leaf_block, block_tags)
        leaf_sid = str(leaf_seg.segment_id)
        final_text = str(ctx.final_by_id[leaf_sid].get("text", ""))
        markup_map = _parse_markup_map(ctx.seg_by_id[leaf_sid])
        trans_xpath = _write_block(root_el, final_text, markup_map, ctx.submit_mode)
        for seg, _ in segs:
            doc_map[str(seg.segment_id)] = trans_xpath

    if ctx.submit_mode == "APPEND_BLOCK":
        _dedup_ids(root)

    new_bytes = etree.tostring(root, encoding="unicode").encode("utf-8")
    return new_bytes, doc_map


def _write_block(
    root_el: Any, final_text: str, markup_map: list[tuple[str, str]], submit_mode: str
) -> str:
    """APPEND_BLOCK — клоновка + вставка sibling; REPLACE — замена контента на месте.

    Возвращает позиционный XPath блока перевода (write_map / сверка с E6)."""
    if submit_mode == "REPLACE":
        _fill_content(root_el, final_text, markup_map)
        return segmenter.positional_xpath(root_el)
    clone = etree.Element(root_el.tag, root_el.attrib)
    _fill_content(clone, final_text, markup_map)
    clone.tail = root_el.tail
    root_el.tail = None
    parent = root_el.getparent()
    if parent is None:
        raise AppError("перевод корня документа запрещён: блок должен быть внутри body")
    parent.insert(parent.index(root_el) + 1, clone)
    return segmenter.positional_xpath(clone)


# ------------------------------------------------------------------ контент


def _fill_content(container: Any, final_text: str, markup_map: list[tuple[str, str]]) -> None:
    """Заполняет `container` контентом: текст модели — как текстовые узлы,
    элементы — только из `markup_map` (разбор ответа как XHTML не делается)."""
    for child in list(container):
        container.remove(child)
    container.text = None
    stack: list[Any] = [container]
    for kind, n in _tokenize(final_text):
        if kind == "text":
            _append_text(stack[-1], n)
        elif kind == "open":
            element = _make_element(markup_map[n - 1][0])
            stack[-1].append(element)
            stack.append(element)
        elif kind == "void" and _tag_name(markup_map[n - 1][0]) == "br":
            # Пустой внутристрочный элемент. Движок в перевод несёт только
            # элементы с текстом и `<br>` (разделитель строк); прочие пустые
            # (img, wbr, …) — нетекстовая медиа — в переводе не дублируются
            # (M5-2: скелет выхода M5 == скелет выхода тракта T).
            element = _make_element(markup_map[n - 1][0])
            stack[-1].append(element)
        elif kind == "close" and len(stack) > 1:
            stack.pop()


def _tag_name(open_tag: str) -> str:
    """Локальное имя тега из строки открытого тега (`<br/>` → `br`)."""
    parts = open_tag[1:].strip().split()
    return parts[0].rstrip("/").lower() if parts else ""


def _append_text(element: Any, text: str) -> None:
    """Дописать литеральный текст: в `.text`, если детей нет, иначе в `.tail`
    последнего ребёнка (lxml-интерливинг). Текст экранируется lxml автоматически."""
    if not text:
        return
    if len(element) == 0:
        element.text = (element.text or "") + text
    else:
        element[-1].tail = (element[-1].tail or "") + text


def _make_element(open_tag: str) -> Any:
    """Элемент из trusted-тега исходника (из `markup_map`), без содержимого."""
    tag = open_tag.rstrip()
    if not tag.endswith("/>"):
        tag = (tag[:-1] + "/>") if tag.endswith(">") else tag + "/>"
    return etree.fromstring(tag)


def _tokenize(final_text: str) -> list[tuple[str, Any]]:
    """final (text_encoded) → токены: ('text', s) | ('open'/'void'/'close', n)."""
    tokens: list[tuple[str, Any]] = []
    pos = 0
    for match in _MARKER_RE.finditer(final_text):
        if match.start() > pos:
            tokens.append(("text", final_text[pos : match.start()]))
        close_n, void_n, open_n = match.group(1), match.group(2), match.group(3)
        if close_n is not None:
            tokens.append(("close", int(close_n)))
        elif void_n is not None:
            tokens.append(("void", int(void_n)))
        else:
            tokens.append(("open", int(open_n)))
        pos = match.end()
    if pos < len(final_text):
        tokens.append(("text", final_text[pos:]))
    return tokens


# ------------------------------------------------------------------ id / jsonl


def _dedup_ids(root: Any) -> None:
    """Document-wide дедупликация id (как движок, E1 §3 п.2): 1-е вхождение хранит,
    2-е → `id__translated`, 3-е → `id__translated_2`, … Посыпает и id блока, и
    inline-якоря внутри вставленного переводного поддерева."""
    seen: dict[str, int] = {}
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        element_id = element.get("id")
        if element_id is None:
            continue
        occurrence = seen.get(element_id, 0) + 1
        seen[element_id] = occurrence
        if occurrence == 2:
            element.set("id", f"{element_id}{TRANSLATED_MARKER}")
        elif occurrence >= 3:
            element.set("id", f"{element_id}{TRANSLATED_MARKER}_{occurrence - 1}")


def _parse_markup_map(row: dict[str, Any]) -> list[tuple[str, str]]:
    raw = row.get("markup_map") or []
    return [(str(pair[0]), str(pair[1])) for pair in raw]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
