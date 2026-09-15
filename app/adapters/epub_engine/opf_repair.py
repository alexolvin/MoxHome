"""Ремонт пространств имён атрибутов EPUB 2.0 OPF (D83).

Движок epub-translator 0.1.10 (переписывать запрещено, ТЗ §5.1) при
переводе метаданных пересерилизует весь content.opf своим
``XMLLikeNode.save()`` и в этом ломает пространства имён атрибутов EPUB 2.0:

- метаданные (``dc:identifier``/``dc:creator``/``dc:contributor``/
  ``dc:publisher``/``dc:title``/``dc:date``): атрибуты ``scheme``,
  ``file-as``, ``role``, ``event`` теряют пространство имён
  ``http://www.idpf.org/2007/opf`` (атрибут без префикса в XML не попадает
  ни в default namespace, ни в какое-либо другое) → нарушается OPF 2.0.1
  (EPUBCheck ``ERROR(RSC-005)``; измерено на книгах V1: 8/6/6/102/90
  ошибок на 5 исходниках, исходники сами чисты — 0/0/0);
- ``<guide><reference>``: атрибут ``type`` переносится в пространство имён
  XMLSchema-instance; по OPF 2.0.1 он должен быть без пространства имён.

Ремонт выполняется в адаптере ДО публикации и валидации (единая точка —
``atomic_finalize``, Rule 8: покрывает выход движка E5, пересборку L3 и
сборку S5), идемпотентен и является no-op для EPUB 3 (перечисленные
атрибуты в OPF 3 на этих элементах не используются).
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"

#: Элементы метаданных OPF 2.0, несущие атрибуты пространства имён OPF.
_METADATA_TAGS = frozenset(
    {
        f"{{{DC_NS}}}{name}"
        for name in ("identifier", "creator", "contributor", "publisher", "title", "date")
    }
)
#: Их атрибуты обязаны находиться в пространстве имён OPF (OPF 2.0.1 XSD).
_METADATA_ATTRS = frozenset(("scheme", "file-as", "role", "event"))


def _repair_tree(root: etree._Element) -> bool:
    changed = False
    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str):
            continue
        if tag in _METADATA_TAGS:
            for name in _METADATA_ATTRS:
                value = el.get(name)
                if value is None:
                    value = el.get(f"{{{XSI_NS}}}{name}")
                    if value is None:
                        continue
                    del el.attrib[f"{{{XSI_NS}}}{name}"]
                else:
                    del el.attrib[name]
                if el.get(f"{{{OPF_NS}}}{name}") is None:
                    el.set(f"{{{OPF_NS}}}{name}", value)
                    changed = True
        elif tag == f"{{{OPF_NS}}}reference":
            xsi_type = el.get(f"{{{XSI_NS}}}type")
            if xsi_type is not None and el.get("type") is None:
                del el.attrib[f"{{{XSI_NS}}}type"]
                el.set("type", xsi_type)
                changed = True
    return changed


def repair_opf_bytes(opf: bytes) -> tuple[bytes, bool]:
    """Чистая функция: (исправленные байты OPF, были ли изменения)."""
    parser = etree.XMLParser(remove_blank_text=False)
    root = etree.fromstring(opf, parser)
    changed = _repair_tree(root)
    if not changed:
        return opf, False
    return (
        etree.tostring(root, xml_declaration=True, encoding="UTF-8"),
        True,
    )


def _find_opf_path(archive: zipfile.ZipFile) -> str | None:
    try:
        container = etree.fromstring(archive.read("META-INF/container.xml"))
    except (KeyError, etree.XMLSyntaxError):
        return None
    rootfile = container.find(f"{{{_CONTAINER_NS}}}rootfiles/{{{_CONTAINER_NS}}}rootfile")
    if rootfile is None:
        return None
    path = rootfile.get("full-path")
    return path if path and path in archive.namelist() else None


def _replace_zip_entry(epub_path: Path, entry: str, data: bytes) -> None:
    """Замена одной записи ZIP с сохранением порядка и compress_type."""
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{epub_path.name}.", suffix=".tmp", dir=str(epub_path.parent)
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with zipfile.ZipFile(epub_path) as zin, zipfile.ZipFile(
            tmp_path, "w", allowZip64=True
        ) as zout:
            for info in zin.infolist():
                payload = data if info.filename == entry else zin.read(info.filename)
                zout.writestr(info, payload)
        tmp_path.replace(epub_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def repair_epub2_opf_namespaces(epub_path: Path) -> bool:
    """Чинит OPF в EPUB на месте. True — записи были изменены."""
    if not epub_path.is_file():
        return False
    with zipfile.ZipFile(epub_path) as archive:
        opf_path = _find_opf_path(archive)
        if opf_path is None:
            return False
        opf_bytes = archive.read(opf_path)
    repaired, changed = repair_opf_bytes(opf_bytes)
    if not changed:
        return False
    _replace_zip_entry(epub_path, opf_path, repaired)
    return True
