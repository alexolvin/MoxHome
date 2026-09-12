"""Сегментатор XHTML: блочные элементы → сегменты с устойчивыми идентификаторами (E2.2–4)."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from lxml import etree

_WS_RE = re.compile(r"\s+")


def localname(element: etree._Element) -> str:
    tag = element.tag
    if not isinstance(tag, str):
        return ""
    return str(etree.QName(tag).localname)


def normalize_text(text: str) -> str:
    """NFC + схлопывание пробелов, без изменения регистра (E2.3)."""
    return _WS_RE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def positional_xpath(element: etree._Element) -> str:
    """Позиционный XPath от корня: /html/body/p[3] (1-нумерация среди равнотеговых)."""
    parts: list[str] = []
    node: etree._Element | None = element
    while node is not None:
        name = localname(node)
        parent = node.getparent()
        if parent is None:
            parts.append(f"/{name}")
        else:
            index = 1
            for sibling in parent:
                if sibling is node:
                    break
                if localname(sibling) == name:
                    index += 1
            parts.append(f"{name}[{index}]")
        node = parent
    return "".join(reversed(parts))


def _plain_copy(element: etree._Element) -> etree._Element:
    """Копия поддерева без namespace-деклараций: тег — localname, атрибуты — как есть."""
    plain = etree.Element(localname(element))
    for key, value in element.attrib.items():
        plain.set(key, value)
    plain.text = element.text
    for child in element:
        plain.append(_plain_copy(child))
    return plain


def inner_marked_text(element: etree._Element) -> str:
    """Внутренняя разметка блока: собственный текст + дети (без тэга блока).

    Сериализуется без дублирующих namespace-деклараций, вставленных lxml.
    """
    parts: list[str] = []
    if element.text:
        parts.append(element.text)
    for child in element:
        parts.append(etree.tostring(_plain_copy(child), encoding="unicode"))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def full_text(element: etree._Element) -> str:
    """Весь текстовый контент блока (включая вложенные элементы)."""
    return "".join(element.itertext())


def make_segment_id(document_path: str, xpath: str, normalized: str) -> str:
    payload = f"{document_path}\n{xpath}\n{normalized}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def find_block_elements(root: etree._Element, block_tags: frozenset[str]) -> list[etree._Element]:
    """Блочные элементы из настраиваемого множества, в порядке документа (E2.2)."""
    tags = {tag.lower() for tag in block_tags}
    return [
        element
        for element in root.iter()
        if localname(element) in tags
    ]


def count_by_localname(root: etree._Element, name: str) -> int:
    return sum(1 for element in root.iter() if localname(element) == name)


def count_footnotes(root: etree._Element) -> int:
    epub_ns = "http://www.idpf.org/2007/ops"
    return sum(
        1
        for element in root.iter()
        if isinstance(element.tag, str)
        and (
            element.get("role") == "doc-noteref"
            or element.get(f"{{{epub_ns}}}type") == "footnote"
        )
    )


def find_body(root: etree._Element) -> etree._Element | None:
    for child in root:
        if localname(child) == "body":
            return child
    return None
