"""Выравнивание оригинал ↔ перевод (E6): пары (segment_id источника, текст перевода).

Механизм выведен из измеренной разметки движка epub-translator 0.1.10
(E1, `docs/ENGINE_PROBE.md` §3 P3 и «Критические следствия для E5/E6», п.1):

- APPEND_BLOCK (bilingual): оригинал сохранён; перевод вставлен следующим
  sibling'ом корня сегмента (вложенные структуры — blockquote, таблица —
  корень сегмента = внутренний элемент, позиция считается от него); у блока
  с id перевод помечен `id__translated` (дедупликация — суффиксы `_2`…),
  у блока без id — позиция + копия тега (измерено: тег и все атрибуты
  копируются).
- REPLACE / APPEND_TEXT (translation): элемент изменён на месте —
  позиционный XPath и тег сохранены, текст заменён переводом; исходного
  текста в выходе нет (источник учебных материалов — исходный файл,
  T-E6-8).

Обязательные проверки каждой пары (TZ E6.3):
1. Исходный элемент идентифицирован тем же тождественным ключом, что при
   сегментации — (путь в архиве, позиционный XPath, нормализованный текст)
   = `segment_id` (E2.3) — и `segment_id` ПЕРЕСЧИТЫВАЕТСЯ ИЗ ВЫХОДНОГО
   файла: найденный в выходе оригинал обязан иметь ровно тот
   нормализованный текст, что при сегментации (текст читается из выходного
   элемента); несовпадение → пара не создаётся (`xpath_mismatch`).
   Примечание (измерено в E6): позиционный XPath оригинала в выходе может
   СДВИГАТЬСЯ — вставленные перед ним переводы-однотеговые сдвигают индекс;
   поэтому тождество проверяется по референсу сегментации
   (документ + XPath + текст), а не по XPath-координате в выходе.
2. Перевод: не пустой (`empty`), не идентичен оригиналу (`identical`;
   белый список — настраиваемый критерий), у блоков с id маркер
   `id__translated` совпадает, тег скопирован (разметка, измеренная в E1).
   Сопоставление по порядковому номеру без проверки содержимого/атрибутов
   не используется как единственный критерий (TZ E6.2).

Невыровненные сегменты не отбрасываются: поимённо в
`alignment_report.json` с причиной: `missing_translation`, `xpath_mismatch`,
`empty`, `identical`; лишние вставленные блоки — `extra_block`.

Карта хранит позиционный XPath блока перевода в выходном файле —
адресация для точечного патча на L3 (после правки выравнивание
пересчитывается по исправленному файлу; переиспользование карты,
построенной до правки, запрещено — TZ E6.7).
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lxml.etree

from app.domain.book import Book, Segment
from app.epub import segmenter

#: Маркер движка для вставленных блоков перевода (измерено, E1 §3 п.2).
TRANSLATED_MARKER = "__translated"

REASON_MISSING_TRANSLATION = "missing_translation"
REASON_XPATH_MISMATCH = "xpath_mismatch"
REASON_EMPTY = "empty"
REASON_IDENTICAL = "identical"
REASON_EXTRA_BLOCK = "extra_block"

#: Все допустимые причины (TZ E6.4: набор фиксирован).
REASONS: frozenset[str] = frozenset(
    {
        REASON_MISSING_TRANSLATION,
        REASON_XPATH_MISMATCH,
        REASON_EMPTY,
        REASON_IDENTICAL,
        REASON_EXTRA_BLOCK,
    }
)


@dataclass(frozen=True)
class AlignedPair:
    """Выровненная пара: исходный сегмент ↔ блок перевода в выходе."""

    segment_id: str
    doc: str
    source_xpath: str  # XPath оригинала при сегментации (референс тождества)
    source_text: str  # нормализованный текст ИЗ ИСХОДНОГО файла
    translation_xpath: str  # XPath блока перевода в выходе (патч-адрес, E6.7)
    translation_text: str  # нормализованный текст перевода из выхода
    how: str  # sibling | inside (APPEND_BLOCK) | inplace (REPLACE/APPEND_TEXT)


@dataclass(frozen=True)
class UnalignedSegment:
    """Невыровненный сегмент — поимённо с причиной (TZ E6.4)."""

    segment_id: str
    doc: str
    source_xpath: str
    source_text: str
    reason: str  # missing_translation | xpath_mismatch | empty | identical
    detail: str


@dataclass(frozen=True)
class ExtraBlock:
    """Вставленный в выход блок, не принадлежащий ни одному исходному сегменту."""

    doc: str
    xpath: str
    text: str


@dataclass(frozen=True)
class DocAlignment:
    doc: str
    aligned: tuple[AlignedPair, ...]
    unaligned: tuple[UnalignedSegment, ...]
    extra_blocks: tuple[ExtraBlock, ...]


@dataclass(frozen=True)
class AlignmentReport:
    submit_mode: str  # APPEND_BLOCK | REPLACE | APPEND_TEXT
    min_coverage: float
    total: int  # исходных сегментов
    aligned_count: int
    unaligned_count: int
    extra_count: int
    coverage: float  # aligned / total (доля выравнивания)
    identity_mismatches: int  # пар, не созданных по тождеству оригинала (E6-2)
    docs: tuple[DocAlignment, ...]


# ------------------------------------------------------------------- ядро пар

def blocks(root: lxml.etree._Element, tags: frozenset[str]) -> list[lxml.etree._Element]:
    """Блочные элементы из настраиваемого множества, в порядке документа.

    Пустые по тексту блоки исключены — то же правило, что в ридере
    (app/epub/reader.py, _segment_body): сегмента для них нет, единицы
    перевода нет; инвариант «сегментов == блоков» (align_document) требует
    единого правила отбора на всех сторонах (исходник и выход, E5/E6).
    """
    body = segmenter.find_body(root)
    if body is None:
        return []
    return [
        el for el in body.iter()
        if segmenter.localname(el) in tags
        and segmenter.normalize_text(segmenter.full_text(el))
    ]


def match_originals(
    src_blocks: list[lxml.etree._Element],
    out_blocks: list[lxml.etree._Element],
    tags: frozenset[str],
) -> list[tuple[lxml.etree._Element, lxml.etree._Element | None]]:
    """Оригинал в выходе: по id (если есть) либо по (тег, оригинальный текст)
    монотонно по порядку документа; вставленные (`…__translated`) блоки —
    не оригиналы (измерено, E1: вставка всегда ПОСЛЕ оригинала). Текст —
    `original_text`: у контейнера (blockquote) полный текст выхода содержит
    и оригинал, и вставленный внутрь перевод (E1 §3 п.5)."""
    pairs: list[tuple[lxml.etree._Element, lxml.etree._Element | None]] = []
    last_pos = -1
    for src_el in src_blocks:
        src_tag = segmenter.localname(src_el)
        src_text = segmenter.normalize_text(segmenter.full_text(src_el))
        src_id = src_el.get("id")
        matched: lxml.etree._Element | None = None
        for i, out_el in enumerate(out_blocks):
            if i <= last_pos:
                continue
            if (out_el.get("id") or "").endswith(TRANSLATED_MARKER):
                continue
            if src_id is not None:
                if out_el.get("id") == src_id:
                    matched = out_el
                    break
            elif (
                segmenter.localname(out_el) == src_tag
                and original_text(out_el, src_el, tags) == src_text
                and not out_el.get("id")
            ):
                matched = out_el
                break
        if matched is not None:
            last_pos = next(i for i, el in enumerate(out_blocks) if el is matched)
        pairs.append((src_el, matched))
    return pairs


def _insertion_children(
    out_el: lxml.etree._Element,
    src_el: lxml.etree._Element,
    tags: frozenset[str],
) -> set[int]:
    """Какие блочные дети выходного элемента — вставленные переводы (id()).

    Блочные дети выхода сопоставляются с детьми оригинала монотонно по
    (тег, оригинальный текст); несопоставленные — вставки. Инлайн-дети
    (не-блоковые) вставками не бывают: движок вставляет на блочном уровне
    (измерено, E1 §3).
    """
    src_bl = [
        c for c in src_el if isinstance(c.tag, str) and segmenter.localname(c) in tags
    ]
    out_bl = [
        c for c in out_el if isinstance(c.tag, str) and segmenter.localname(c) in tags
    ]
    matched: set[int] = set()
    last = -1
    for s_c in src_bl:
        s_text = segmenter.normalize_text(segmenter.full_text(s_c))
        for i in range(last + 1, len(out_bl)):
            o_c = out_bl[i]
            if (
                segmenter.localname(o_c) == segmenter.localname(s_c)
                and original_text(o_c, s_c, tags) == s_text
            ):
                matched.add(id(o_c))
                last = i
                break
    return {id(c) for c in out_bl} - matched


def original_text(
    out_el: lxml.etree._Element,
    src_el: lxml.etree._Element,
    tags: frozenset[str],
) -> str:
    """Нормализованный текст выходного элемента БЕЗ вставленных переводов.

    Нужно для проверки тождества оригинала (TZ E6.3): у контейнера
    (blockquote) полный текст выхода содержит и оригинал, и вставленный
    ВНУТРЬ перевод (измерено, E1 §3 п.5: перевод — sibling внутреннего
    элемента, а не после контейнера). Поддерево каждой вставки исключается
    целиком; её tail — тоже (принадлежит вставке).
    """
    ins = _insertion_children(out_el, src_el, tags)
    parts: list[str] = []
    if out_el.text:
        parts.append(out_el.text)
    for child in out_el:
        if not isinstance(child.tag, str):
            continue
        if id(child) in ins:
            continue
        if segmenter.localname(child) in tags:
            src_child = next(
                (
                    s
                    for s in src_el
                    if isinstance(s.tag, str)
                    and segmenter.localname(s) == segmenter.localname(child)
                    and original_text(child, s, tags)
                    == segmenter.normalize_text(segmenter.full_text(s))
                ),
                None,
            )
            if src_child is not None:
                parts.append(original_text(child, src_child, tags))
                if child.tail:
                    parts.append(child.tail)
            continue
        parts.append("".join(child.itertext()))
        if child.tail:
            parts.append(child.tail)
    return segmenter.normalize_text("".join(parts))


def find_translation(
    out_el: lxml.etree._Element,
    tags: frozenset[str],
    insertions: set[int],
    expect: str = "any",
) -> tuple[lxml.etree._Element | None, str]:
    """Перевод для найденного в выходе оригинала (разметка, измеренная в E1).

    Кандидаты: 1) следующий sibling (тот же родитель) — обычная вставка и
    деформированная таблица (ячейка-перевод в том же <tr>); 2) вставленный
    дочерний блок — blockquote (перевод ВНУТРЬ, E1 §3 п.5). `expect` —
    измеренное положение перевода для данного оригинала: "sibling"
    (текстовый корень = сам элемент) | "inside" (контейнер: текстовый корень
    внутри — sibling контейнера не является его переводом: сдвинутый чужой
    блок в конце документа не создаёт «уверенной неверной пары», T-E6-4)
    | "any" (QC: оба)."""
    parent = out_el.getparent()
    nxt = out_el.getnext()
    sibling: lxml.etree._Element | None = None
    if (
        parent is not None
        and nxt is not None
        and nxt.getparent() is parent
        and id(nxt) in insertions
        and segmenter.localname(nxt) in tags
    ):
        sibling = nxt
    child: lxml.etree._Element | None = None
    for c in out_el:
        if id(c) in insertions and segmenter.localname(c) in tags:
            child = c
            break
    if expect == "sibling":
        return (sibling, "sibling") if sibling is not None else (None, "")
    if expect == "inside":
        return (child, "inside") if child is not None else (None, "")
    if sibling is not None:
        return sibling, "sibling"
    if child is not None:
        return child, "inside"
    return None, ""


def text_root(src_el: lxml.etree._Element, tags: frozenset[str]) -> lxml.etree._Element:
    """Элемент, текст которого движок переводит (его `block_parent`;
    измерено, E1 §3 п.5): если весь текст сегмента находится в ОДНОМ
    внутреннем блочном элементе (blockquote с одним `<p>`), корнем
    является этот внутренний элемент — тег и маркер перевода копируются
    от него, вставка — внутрь. Иначе корень = сам сегментный элемент."""
    desc = [
        d
        for d in src_el.iter()
        if d is not src_el
        and isinstance(d.tag, str)
        and segmenter.localname(d) in tags
    ]
    if len(desc) == 1 and segmenter.normalize_text(segmenter.full_text(desc[0])) == (
        segmenter.normalize_text(segmenter.full_text(src_el))
    ):
        return desc[0]
    return src_el


def marker_ok(src_id: str, trans_id: str | None) -> bool:
    """Маркер перевода у блока с id (E1 §3 п.2): `id__translated`,
    дедупликация — `id__translated_2`, `id__translated_3`, …"""
    if trans_id is None:
        return False
    prefix = f"{src_id}{TRANSLATED_MARKER}"
    if trans_id == prefix:
        return True
    if trans_id.startswith(prefix):
        suffix = trans_id[len(prefix) :]
        return suffix.startswith("_") and suffix[1:].isdigit()
    return False


def _xpath_step(
    current: lxml.etree._Element, rest: str
) -> tuple[lxml.etree._Element, str] | None:
    """Один шаг спуска: имя сегмента — префикс `rest` до первого '['
    (дерево управляет токенизацией: имя пробуют от наибольшего к наименьшему,
    индекс — факультативный), возвращается (элемент, остаток строки)."""
    m = re.match(r"([^\[\]]+)", rest)
    if m is None:
        return None
    name_run = m.group(1)
    for k in range(len(name_run), 0, -1):
        cand = name_run[:k]
        tail = rest[k:]
        index: int | None = None
        if tail.startswith("["):
            em = re.match(r"\[(\d+)\]", tail)
            if em is None:
                continue
            index = int(em.group(1))
            tail = tail[em.end() :]
        count = 0
        for child in current:
            if segmenter.localname(child) == cand:
                count += 1
                if index is None or count == index:
                    return child, tail
    return None


def resolve_positional_xpath(
    root: lxml.etree._Element, xpath: str
) -> lxml.etree._Element | None:
    """Разбор позиционного XPath формата `segmenter.positional_xpath`
    (без разделителей между сегментами: /htmlbody[1]p[3]; корневой сегмент
    без индекса) по дереву (E6-7: проверка, что сохранённый XPath блока
    перевода указывает на элемент)."""
    if not xpath.startswith("/"):
        return None
    rest = xpath[1:]
    # Корневой сегмент — сам `root` (индекса у него нет по формату):
    # имя должно совпадать с localname(root), дальше — спуск по детям.
    m = re.match(r"([^\[\]]+)", rest)
    if m is None:
        return None
    name_run = m.group(1)
    for k in range(len(name_run), 0, -1):
        if name_run[:k] == segmenter.localname(root) and not rest[k:].startswith("["):
            rest = rest[k:]
            break
    else:
        return None
    current = root
    while rest:
        step = _xpath_step(current, rest)
        if step is None:
            return None
        current, rest = step
    return current


# ------------------------------------------------------------- выравнивание

def _src_text(el: lxml.etree._Element) -> str:
    return segmenter.normalize_text(segmenter.full_text(el))


def _is_original(
    out_el: lxml.etree._Element, src_blocks: list[lxml.etree._Element], tags: frozenset[str]
) -> bool:
    """Может ли `out_el` быть оригиналом (а не вставленным переводом).

    Нужно для честного `extra_block`: блоки, не сопоставленные монотонно
    (порядок нарушен), остаются оригиналами — не «лишними вставками».
    Тождество — как при проверке пар: по id (у оригиналов id не оканчивается
    маркером; измерено, E1) либо по (тег, оригинальный текст) — `original_text`,
    т.к. у контейнера текст выхода содержит и вставленный внутрь перевод.
    """
    oid = out_el.get("id")
    if oid is not None:
        if oid.endswith(TRANSLATED_MARKER):
            return False
        return any(s.get("id") == oid for s in src_blocks)
    return any(
        segmenter.localname(s) == segmenter.localname(out_el)
        and original_text(out_el, s, tags)
        == segmenter.normalize_text(segmenter.full_text(s))
        for s in src_blocks
    )


def align_document(
    *,
    doc: str,
    src_root: lxml.etree._Element,
    out_root: lxml.etree._Element,
    segments: tuple[Segment, ...],
    submit_mode: str,
    tags: frozenset[str],
    whitelist: re.Pattern[str] | None,
) -> DocAlignment:
    """Выравнивание одного spine-документа (глава 1:1 с документом, E2)."""
    src_blocks = blocks(src_root, tags)
    if len(segments) != len(src_blocks):
        raise ValueError(
            f"документ {doc}: число сегментов ({len(segments)}) != "
            f"число блочных элементов ({len(src_blocks)})"
        )
    # Инвариант: segment_id сегментов согласован с перечислением
    # (ридер использует те же функции); нарушение — сбой, не «книга».
    for seg, el in zip(segments, src_blocks, strict=True):
        expected = segmenter.make_segment_id(
            doc, segmenter.positional_xpath(el), _src_text(el)
        )
        if expected != seg.segment_id:
            raise ValueError(f"документ {doc}: segment_id не совпадает при перечитывании")

    if submit_mode == "APPEND_BLOCK":
        return _align_append(
            doc=doc, src_blocks=src_blocks, out_root=out_root,
            segments=segments, tags=tags, whitelist=whitelist,
        )
    return _align_inplace(
        doc=doc, src_blocks=src_blocks, out_root=out_root,
        segments=segments, tags=tags, whitelist=whitelist,
    )


def _align_append(
    *,
    doc: str,
    src_blocks: list[lxml.etree._Element],
    out_root: lxml.etree._Element,
    segments: tuple[Segment, ...],
    tags: frozenset[str],
    whitelist: re.Pattern[str] | None,
) -> DocAlignment:
    out_blocks = blocks(out_root, tags)
    pairs = match_originals(src_blocks, out_blocks, tags)
    out_pos = {id(el): i for i, el in enumerate(out_blocks)}
    used_idx = {out_pos[id(out_el)] for _, out_el in pairs if out_el is not None}
    insertions = {id(el) for i, el in enumerate(out_blocks) if i not in used_idx}
    used_trans: set[int] = set()  # id(el) выбранных переводов
    aligned: list[AlignedPair] = []
    unaligned: list[UnalignedSegment] = []

    for seg, src_el, (_, out_el) in zip(segments, src_blocks, pairs, strict=True):
        src_xpath = segmenter.positional_xpath(src_el)
        s_text = _src_text(src_el)
        if out_el is None:
            unaligned.append(
                UnalignedSegment(
                    seg.segment_id, doc, src_xpath, s_text,
                    REASON_XPATH_MISMATCH, "оригинал не найден в выходе",
                )
            )
            continue
        # segment_id пересчитывается из выходного файла (TZ E6.3): референс —
        # (документ, XPath сегментации), текст — оригинальный контент
        # выходного элемента (у контейнера полный текст выхода содержит и
        # вставленный внутрь перевод — E1 §3 п.5).
        recomputed = segmenter.make_segment_id(
            doc, src_xpath, original_text(out_el, src_el, tags)
        )
        if recomputed != seg.segment_id:
            unaligned.append(
                UnalignedSegment(
                    seg.segment_id, doc, src_xpath, s_text,
                    REASON_XPATH_MISMATCH,
                    "пересчитанный segment_id не совпадает с исходным "
                    "(текст оригинала в выходе изменён)",
                )
            )
            continue
        # Положение, тег и маркер перевода определяются от текстового корня
        # (block_parent движка; E1 §3 п.5): у контейнера (blockquote с одним
        # <p>) перевод копирует тег внутреннего элемента и вставляется ВНУТРЬ,
        # у остальных — следующий sibling.
        root = text_root(src_el, tags)
        expect = "inside" if root is not src_el else "sibling"
        trans, how = find_translation(out_el, tags, insertions, expect)
        if trans is None:
            unaligned.append(
                UnalignedSegment(
                    seg.segment_id, doc, src_xpath, s_text,
                    REASON_MISSING_TRANSLATION,
                    f"переведённый блок не найден ({segmenter.localname(src_el)})",
                )
            )
            continue
        if segmenter.localname(trans) != segmenter.localname(root):
            unaligned.append(
                UnalignedSegment(
                    seg.segment_id, doc, src_xpath, s_text,
                    REASON_MISSING_TRANSLATION,
                    f"тег перевода не совпадает: ожидался {segmenter.localname(root)}, "
                    f"найдён {segmenter.localname(trans)}",
                )
            )
            continue
        root_id = root.get("id")
        if root_id is not None and not marker_ok(root_id, trans.get("id")):
            unaligned.append(
                UnalignedSegment(
                    seg.segment_id, doc, src_xpath, s_text,
                    REASON_MISSING_TRANSLATION,
                    f"маркер перевода не совпадает: ожидался {root_id}{TRANSLATED_MARKER}, "
                    f"найдён {trans.get('id')!r}",
                )
            )
            continue
        used_trans.add(id(trans))
        t_text = _src_text(trans)
        if not t_text:
            unaligned.append(
                UnalignedSegment(
                    seg.segment_id, doc, src_xpath, s_text,
                    REASON_EMPTY, f"пустой перевод ({how})",
                )
            )
            continue
        if t_text == s_text and not (whitelist is not None and whitelist.fullmatch(t_text)):
            unaligned.append(
                UnalignedSegment(
                    seg.segment_id, doc, src_xpath, s_text,
                    REASON_IDENTICAL, "перевод совпадает с оригиналом",
                )
            )
            continue
        aligned.append(
            AlignedPair(
                segment_id=seg.segment_id,
                doc=doc,
                source_xpath=src_xpath,
                source_text=s_text,
                translation_xpath=segmenter.positional_xpath(trans),
                translation_text=t_text,
                how=how,
            )
        )

    # extra_block — вставленный блок, не принадлежащий ни одной паре;
    # оригиналы, не сопоставленные по порядку, — НЕ вставки (D37):
    extra = tuple(
        ExtraBlock(
            doc=doc,
            xpath=segmenter.positional_xpath(el),
            text=_src_text(el),
        )
        for el in out_blocks
        if id(el) in insertions
        and id(el) not in used_trans
        and not _is_original(el, src_blocks, tags)
    )
    return DocAlignment(
        doc=doc, aligned=tuple(aligned), unaligned=tuple(unaligned), extra_blocks=extra
    )


def _align_inplace(
    *,
    doc: str,
    src_blocks: list[lxml.etree._Element],
    out_root: lxml.etree._Element,
    segments: tuple[Segment, ...],
    tags: frozenset[str],
    whitelist: re.Pattern[str] | None,
) -> DocAlignment:
    """REPLACE / APPEND_TEXT: элемент изменён на месте — адрес (позиционный
    XPath) и тег сохранены, текст заменён переводом. Исходный текст пары
    берётся ИЗ ИСХОДНОГО файла (в выходе оригинала нет, T-E6-8)."""
    out_blocks = blocks(out_root, tags)
    out_by_xpath = {segmenter.positional_xpath(el): el for el in out_blocks}
    src_xpaths = {segmenter.positional_xpath(el) for el in src_blocks}
    aligned: list[AlignedPair] = []
    unaligned: list[UnalignedSegment] = []

    for seg, src_el in zip(segments, src_blocks, strict=True):
        src_xpath = segmenter.positional_xpath(src_el)
        s_text = _src_text(src_el)
        out_el = out_by_xpath.get(src_xpath)
        if out_el is None:
            unaligned.append(
                UnalignedSegment(
                    seg.segment_id, doc, src_xpath, s_text,
                    REASON_XPATH_MISMATCH, "элемент отсутствует в том же XPath в выходе",
                )
            )
            continue
        if segmenter.localname(out_el) != segmenter.localname(src_el):
            unaligned.append(
                UnalignedSegment(
                    seg.segment_id, doc, src_xpath, s_text,
                    REASON_XPATH_MISMATCH,
                    f"тег изменён: {segmenter.localname(src_el)} -> {segmenter.localname(out_el)}",
                )
            )
            continue
        t_text = _src_text(out_el)
        if not t_text:
            unaligned.append(
                UnalignedSegment(
                    seg.segment_id, doc, src_xpath, s_text, REASON_EMPTY, "пустой перевод",
                )
            )
            continue
        if t_text == s_text and not (whitelist is not None and whitelist.fullmatch(t_text)):
            unaligned.append(
                UnalignedSegment(
                    seg.segment_id, doc, src_xpath, s_text,
                    REASON_IDENTICAL, "перевод совпадает с оригиналом",
                )
            )
            continue
        aligned.append(
            AlignedPair(
                segment_id=seg.segment_id,
                doc=doc,
                source_xpath=src_xpath,
                source_text=s_text,
                translation_xpath=src_xpath,
                translation_text=t_text,
                how="inplace",
            )
        )

    extra = tuple(
        ExtraBlock(doc=doc, xpath=xpath, text=_src_text(el))
        for el in out_blocks
        if (xpath := segmenter.positional_xpath(el)) not in src_xpaths
    )
    return DocAlignment(
        doc=doc, aligned=tuple(aligned), unaligned=tuple(unaligned), extra_blocks=extra
    )


def build_alignment_report(
    *,
    book: Book,
    source_path: Path,
    target_path: Path,
    submit_mode: str,
    block_tags: frozenset[str],
    min_coverage: float,
    identical_whitelist: re.Pattern[str] | None,
) -> AlignmentReport:
    """Полная карта выравнивания книги (все spine-документы исходной книги)."""
    tags = frozenset(tag.lower() for tag in block_tags)
    docs: list[DocAlignment] = []
    with zipfile.ZipFile(source_path) as source_zip, zipfile.ZipFile(target_path) as target_zip:
        out_names = set(target_zip.namelist())
        for chapter in book.chapters:
            doc = chapter.document_path
            if doc not in out_names:
                unaligned = tuple(
                    UnalignedSegment(
                        seg.segment_id,
                        doc,
                        "",
                        segmenter.normalize_text(seg.plain_text),
                        REASON_XPATH_MISMATCH,
                        "документ отсутствует в выходном файле",
                    )
                    for seg in chapter.segments
                )
                docs.append(
                    DocAlignment(doc=doc, aligned=(), unaligned=unaligned, extra_blocks=())
                )
                continue
            src_root = lxml.etree.fromstring(source_zip.read(doc))
            out_root = lxml.etree.fromstring(target_zip.read(doc))
            docs.append(
                align_document(
                    doc=doc,
                    src_root=src_root,
                    out_root=out_root,
                    segments=chapter.segments,
                    submit_mode=submit_mode,
                    tags=tags,
                    whitelist=identical_whitelist,
                )
            )

    total = sum(len(d.aligned) + len(d.unaligned) for d in docs)
    aligned_count = sum(len(d.aligned) for d in docs)
    unaligned_count = sum(len(d.unaligned) for d in docs)
    extra_count = sum(len(d.extra_blocks) for d in docs)
    coverage = round(aligned_count / total, 6) if total else 1.0
    identity_mismatches = sum(
        1 for d in docs for u in d.unaligned if u.reason == REASON_XPATH_MISMATCH
    )
    return AlignmentReport(
        submit_mode=submit_mode,
        min_coverage=min_coverage,
        total=total,
        aligned_count=aligned_count,
        unaligned_count=unaligned_count,
        extra_count=extra_count,
        coverage=coverage,
        identity_mismatches=identity_mismatches,
        docs=tuple(docs),
    )


def report_to_dict(report: AlignmentReport) -> dict[str, Any]:
    """Сериализация отчёта в dict (детерминированный порядок — E6-5)."""
    return {
        "submit_mode": report.submit_mode,
        "source_text_source": (
            "исходный файл (сегментация); в режиме REPLACE исходный текст "
            "в выходном файле отсутствует — источник учебных материалов: "
            "исходный файл (T-E6-8)"
        ),
        "min_coverage": report.min_coverage,
        "coverage": report.coverage,
        "total": report.total,
        "aligned": report.aligned_count,
        "unaligned": report.unaligned_count,
        "extra_blocks": report.extra_count,
        "identity_mismatches": report.identity_mismatches,
        "docs": [
            {
                "doc": d.doc,
                "aligned": [
                    {
                        "segment_id": p.segment_id,
                        "source_xpath": p.source_xpath,
                        "source_text": p.source_text,
                        "translation_xpath": p.translation_xpath,
                        "translation_text": p.translation_text,
                        "how": p.how,
                    }
                    for p in d.aligned
                ],
                "unaligned": [
                    {
                        "segment_id": u.segment_id,
                        "source_xpath": u.source_xpath,
                        "source_text": u.source_text,
                        "reason": u.reason,
                        "detail": u.detail,
                    }
                    for u in d.unaligned
                ],
                "extra_blocks": [
                    {"xpath": e.xpath, "text": e.text} for e in d.extra_blocks
                ],
            }
            for d in report.docs
        ],
    }
