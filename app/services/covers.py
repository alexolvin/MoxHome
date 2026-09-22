"""R6 (TZ §8, решение R5 №8): извлечение обложки из готового EPUB.

EPUB3: manifest-item с ``properties="cover-image"``; EPUB2:
``<meta name="cover" content="<id>">`` → тот же item. Результат — файл
``<name>.cover.<ext>`` рядом с книгой; имя файла — ``cover_path``
sidecar-метаданных. Обложки нет — ``None`` (UI — плитка-буква).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

_EXT_BY_MEDIA = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/tiff": ".tiff",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _opf_dir(base: str) -> str:
    parent = Path(base).parent
    return "" if str(parent) == "." else str(parent).replace("\\", "/") + "/"


def _find_cover_id(
    opf: ET.Element,
) -> tuple[dict[str, ET.Element], str | None]:
    """(item id → элемент, id обложки | None): EPUB3 — properties
    ``cover-image``; EPUB2 — ``<meta name="cover" content="id">``."""
    items: dict[str, ET.Element] = {}
    cover_id: str | None = None
    for element in opf.iter():
        name = _local(element.tag)
        if name == "item":
            item_id = element.get("id")
            if item_id:
                items[item_id] = element
                if cover_id is None and "cover-image" in (
                    element.get("properties") or ""
                ).split():
                    cover_id = item_id
        elif name == "meta" and element.get("name") == "cover":
            if cover_id is None and element.get("content"):
                cover_id = element.get("content")
    return items, cover_id


def extract_cover(epub: Path) -> str | None:
    """Извлекает обложку в ``<name>.cover.<ext>`` (рядом с книгой).

    Возврат — имя файла (``cover_path`` для ``.meta.json``) или ``None``
    (обложки нет / не извлечена)."""
    try:
        with zipfile.ZipFile(epub) as archive:
            names = set(archive.namelist())
            if "META-INF/container.xml" not in names:
                return None
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = None
            for element in container.iter():
                if _local(element.tag) == "rootfile" and (
                    element.get("media-type") or ""
                ).endswith("oebps-package+xml"):
                    rootfile = element
                    break
            if rootfile is None:
                return None
            opf_path = rootfile.get("full-path") or ""
            opf_dir = _opf_dir(opf_path)
            opf = ET.fromstring(archive.read(opf_path))
            items, cover_id = _find_cover_id(opf)
            if cover_id is None or cover_id not in items:
                return None
            item = items[cover_id]
            href = (item.get("href") or "").replace("\\", "/")
            media_type = (item.get("media-type") or "").lower()
            archive_path = (
                href.lstrip("/") if href.startswith("/") else opf_dir + href
            )
            data = archive.read(archive_path)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError):
        return None

    suffix = Path(href).suffix.lower()
    ext = _EXT_BY_MEDIA.get(media_type) or (
        suffix if suffix in _EXT_BY_MEDIA.values() else ".jpg"
    )
    cover_name = f"{epub.stem}.cover{ext}"
    (epub.parent / cover_name).write_bytes(data)
    return cover_name
