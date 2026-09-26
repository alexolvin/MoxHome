"""Обложки (ТЗ §8, V2): извлечение, безопасность, миниатюры, выдача.

§8.1 — порядок извлечения (строго): EPUB3 ``cover-image`` → EPUB2
``<meta name=cover>`` → image-item с ``cover`` в id/href → первый ``<img>``
первого документа spine → заглушка (§8.4).
§8.2 — файлы недоверенные: размер элемента в ZIP (``ZipInfo.file_size``) ≤
20 МБ и коэффициент сжатия ≤ 100 — до чтения; ``Image.MAX_IMAGE_PIXELS``
40 млн; форматы JPEG/PNG/GIF/WebP (SVG не декодируется); любая ошибка →
заглушка + WARNING (имя файла + причина), процесс не падает. Статус пишется
в ``<book>.meta.json``: ``cover`` = ``extracted`` / ``none`` /
``failed:<причина>``.
§8.3 — миниатюры WebP q80 шириной 256 и 512:
``input/.covers/<sha256[:16]>-{256,512}.webp``; создаются при первом запросе
(лениво в endpoint) и при импорте (сразу).
"""

from __future__ import annotations

import hashlib
import io
import logging
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = 40_000_000

logger = logging.getLogger(__name__)

#: §8.2: лимиты недоверенного элемента ZIP.
MAX_COVER_BYTES = 20 * 1024 * 1024
MAX_COMPRESS_RATIO = 100
#: §8.2: допустимые форматы (SVG не декодируется, TIFF не поддерживается).
ALLOWED_MEDIA: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)
_EXT_BY_MEDIA = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
_MEDIA_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}
#: §8.3: ширины миниатюр.
THUMB_WIDTHS: tuple[int, int] = (256, 512)


@dataclass(frozen=True)
class CoverExtract:
    """Итог обработки обложки: ``status`` (§8.2) + имя исходного файла и
    sha256 (когда ``extracted``)."""

    status: str
    cover_path: str | None = None
    sha256: str | None = None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _opf_dir(base: str) -> str:
    parent = Path(base).parent
    return "" if str(parent) == "." else str(parent).replace("\\", "/") + "/"


def _media_from_ext(path: str) -> str:
    return _MEDIA_BY_EXT.get(Path(path).suffix.lower(), "")


def _item_location(item: ET.Element, opf_dir: str) -> tuple[str, str]:
    """(путь в ZIP, media-type) для элемента манифеста."""
    href = (item.get("href") or "").replace("\\", "/")
    path = href.lstrip("/") if href.startswith("/") else opf_dir + href
    return path, (item.get("media-type") or "").lower()


def _find_rootfile(container: ET.Element) -> ET.Element | None:
    for element in container.iter():
        if _local(element.tag) == "rootfile" and (
            element.get("media-type") or ""
        ).endswith("oebps-package+xml"):
            return element
    return None


def _first_img_location(
    archive: zipfile.ZipFile, doc_path: str
) -> tuple[str, str] | None:
    """§8.1 п.4: первый ``<img>`` первого документа spine → (путь, media)."""
    try:
        doc_dir = _opf_dir(doc_path)
        root = ET.fromstring(archive.read(doc_path))
    except (KeyError, ET.ParseError, OSError):
        return None
    for element in root.iter():
        if _local(element.tag) != "img":
            continue
        src = (element.get("src") or "").split("#", 1)[0].split("?", 1)[0]
        if not src:
            continue
        path = src.lstrip("/") if src.startswith("/") else doc_dir + src
        return path, _media_from_ext(path)
    return None


def _find_cover_candidate(  # noqa: PLR0912 — 5 шагов §8.1, каждый — возврат
    opf: ET.Element, archive: zipfile.ZipFile, opf_dir: str
) -> tuple[str, str] | None:
    """§8.1: первый кандидат по порядку → (путь в ZIP, media-type)."""
    items: dict[str, ET.Element] = {}
    cover_id: str | None = None
    spine_ids: list[str] = []
    for element in opf.iter():
        name = _local(element.tag)
        if name == "item":
            item_id = element.get("id")
            if item_id:
                items[item_id] = element
                # п.1 — EPUB3 cover-image.
                if "cover-image" in (element.get("properties") or "").split():
                    cover_id = cover_id or item_id
        elif name == "meta" and element.get("name") == "cover":
            # п.2 — EPUB2 meta cover (не перекрывает п.1).
            if cover_id is None and element.get("content"):
                cover_id = element.get("content")
        elif name == "itemref":
            idref = element.get("idref")
            if idref:
                spine_ids.append(idref)
    # п.1 / п.2.
    if cover_id and cover_id in items:
        return _item_location(items[cover_id], opf_dir)
    # п.3 — image-item с "cover" в id или href (порядок документа).
    for item_id, element in items.items():
        if not (element.get("media-type") or "").lower().startswith("image/"):
            continue
        href = element.get("href") or ""
        if "cover" in item_id.lower() or "cover" in href.lower():
            return _item_location(element, opf_dir)
    # п.4 — первый <img> первого документа spine.
    for idref in spine_ids:
        if idref in items:
            doc_path, _ = _item_location(items[idref], opf_dir)
            found = _first_img_location(archive, doc_path)
            if found is not None:
                return found
    return None


def _failed(epub: Path, reason: str) -> CoverExtract:
    logger.warning("cover_failed: file=%s reason=%s", epub.name, reason)
    return CoverExtract(f"failed:{reason}")


def _extract(epub: Path) -> CoverExtract:  # noqa: PLR0911 — fail-closed, путь на ошибку
    """§8.1 + §8.2: находит и извлекает исходную обложку в
    ``<stem>.cover.<ext>``; возвращает статус (``extracted``/``none``/
    ``failed:<reason>``) + имя файла + sha256."""
    try:
        with zipfile.ZipFile(epub) as archive:
            names = set(archive.namelist())
            if "META-INF/container.xml" not in names:
                return CoverExtract("none")
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = _find_rootfile(container)
            if rootfile is None:
                return CoverExtract("none")
            opf_path = rootfile.get("full-path") or ""
            opf_dir = _opf_dir(opf_path)
            opf = ET.fromstring(archive.read(opf_path))
            candidate = _find_cover_candidate(opf, archive, opf_dir)
            if candidate is None:
                return CoverExtract("none")
            archive_path, media_type = candidate
            try:
                info = archive.getinfo(archive_path)
            except KeyError:
                return _failed(epub, "missing-cover-file")
            # §8.2: лимиты до чтения.
            if info.file_size > MAX_COVER_BYTES:
                return _failed(epub, "size-limit")
            if info.compress_size == 0:
                if info.file_size > 0:
                    return _failed(epub, "compression-ratio")
            elif info.file_size / info.compress_size > MAX_COMPRESS_RATIO:
                return _failed(epub, "compression-ratio")
            media = media_type or _media_from_ext(archive_path)
            if media not in ALLOWED_MEDIA:
                return _failed(epub, "unsupported-format")
            data = archive.read(archive_path)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError):
        return _failed(epub, "epub-read-error")
    # §8.2: полное декодирование (повреждённый JPEG и т.п. → заглушка).
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:  # любая ошибка декода → заглушка (§8.2)
        return _failed(epub, "decode-error")
    cover_name = f"{epub.stem}.cover{_EXT_BY_MEDIA[media]}"
    try:
        (epub.parent / cover_name).write_bytes(data)
    except OSError:
        return _failed(epub, "write-error")
    return CoverExtract("extracted", cover_name, hashlib.sha256(data).hexdigest())


def process_cover(epub: Path, covers_dir: Path | None = None) -> CoverExtract:
    """§8.1+§8.2 (+§8.3): извлекает исходную обложку; при ``covers_dir`` —
    сразу создаёт миниатюры (импорт). Возврат — :class:`CoverExtract`."""
    result = _extract(epub)
    if (
        result.status == "extracted"
        and covers_dir is not None
        and result.cover_path
        and result.sha256
    ):
        ensure_thumbnails(epub.parent / result.cover_path, result.sha256, covers_dir)
    return result


def cover_thumbnail_path(covers_dir: Path, sha256_hex: str, width: int) -> Path:
    """§8.3: ``<sha256[:16]>-<width>.webp`` внутри ``.covers``."""
    return covers_dir / f"{sha256_hex[:16]}-{width}.webp"


def ensure_thumbnails(
    source: Path, sha256_hex: str, covers_dir: Path
) -> dict[int, Path]:
    """§8.3: создаёт недостающие миниатюры 256/512 WebP q80. Ошибка —
    WARNING (процесс жив); существующие файлы не пересоздаёт."""
    covers_dir.mkdir(parents=True, exist_ok=True)
    out: dict[int, Path] = {}
    for width in THUMB_WIDTHS:
        path = cover_thumbnail_path(covers_dir, sha256_hex, width)
        out[width] = path
        if path.exists():
            continue
        try:
            opened = Image.open(source)
            opened.load()
            rgb = opened.convert("RGB")
            height = max(1, int(width * rgb.height / rgb.width))
            resized = rgb.resize((width, height), Image.Resampling.LANCZOS)
            tmp = path.with_name(path.name + ".tmp")
            resized.save(tmp, "WEBP", quality=80)
            tmp.replace(path)
        except Exception as exc:  # любая ошибка → WARNING (§8.3)
            logger.warning(
                "cover_thumb_failed: file=%s width=%d reason=%s",
                source.name,
                width,
                type(exc).__name__,
            )
    return out


def extract_cover(epub: Path) -> str | None:
    """Совместимость (R6): имя исходной обложки или ``None`` (без миниатюр)."""
    return process_cover(epub).cover_path
