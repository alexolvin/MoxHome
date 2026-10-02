# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Минимальные стробы lxml.etree (поверхность, используемая app/).

lxml не публикует py.typed; полные стробы (lxml-stubs) устарели.
Стробы описывают только то, что использует проект, — ничего не прячут.
"""

from collections.abc import Iterator, Mapping
from typing import Literal, overload

class _Dtd:
    """Объект DTD (doctype) — используется как значение параметра `doctype`."""


class _DocInfo:
    doctype: _Dtd | None


class _DocumentTree:
    docinfo: _DocInfo

    def getroot(self) -> _Element: ...


class _Element:
    tag: object
    text: str | None
    tail: str | None
    attrib: dict[str, str]

    def getroottree(self) -> _DocumentTree: ...
    def __iter__(self) -> Iterator[_Element]: ...
    def __getitem__(self, index: int) -> _Element: ...
    def getparent(self) -> _Element | None: ...
    def getnext(self) -> _Element | None: ...
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def append(self, element: _Element) -> None: ...
    def remove(self, element: _Element) -> None: ...
    def addnext(self, element: _Element) -> None: ...
    def find(self, path: str) -> _Element | None: ...
    def findall(self, path: str) -> list[_Element]: ...
    def iter(self, tag: str | None = None) -> Iterator[_Element]: ...
    def itertext(self) -> Iterator[str]: ...


class XMLSyntaxError(Exception): ...


class XMLParser:
    def __init__(self, remove_blank_text: bool = False) -> None: ...


class QName:
    def __init__(self, tag: str | _Element) -> None: ...
    localname: str


def Element(
    tag: str,
    attrib: Mapping[str, str] | None = None,
    nsmap: Mapping[str | None, str | None] | None = None,
) -> _Element: ...


def SubElement(
    parent: _Element,
    tag: str,
    attrib: Mapping[str, str] | None = None,
    nsmap: Mapping[str | None, str | None] | None = None,
) -> _Element: ...


@overload
def tostring(element: _Element, *, encoding: Literal["unicode"] = "unicode") -> str: ...


@overload
def tostring(
    element: _Element,
    *,
    encoding: Literal["UTF-8", "utf-8"],
    xml_declaration: bool | None = None,
    doctype: _Dtd | None = None,
) -> bytes: ...


@overload
def fromstring(data: bytes | str) -> _Element: ...


@overload
def fromstring(data: bytes | str, parser: XMLParser) -> _Element: ...
