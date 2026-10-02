# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""R6 пре-проверки (TZ §8): тип по сигнатуре (расширение — подсказка)
и жёсткие отказы ДО конвертации.

``classify`` — единая точка: возвращает ``{"ok": True, "format": fmt}``
(файл проходит; ``format`` — формат книги для ``meta.source_format``)
или ``{"ok": False, "reason": key, "detail": text}`` (отказ; API отдаёт
400 с ``detail`` — T8: точная причина, без трейса).

Проверки: .cbz/.cbr (комиксы), .zip/.fb2.zip (ровно одна книга),
аудио-сигнатура (MP3/FLAC/OGG/WAV), DRM (EPUB — encryption.xml, PDF —
/Encrypt, MOBI/AZW — флаг заголовка), скан-PDF (нет операторов вывода
текста в раскомпрессированных content-стримах; OCR не выполняется).
"""

from __future__ import annotations

import re
import struct
import zipfile
import zlib
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.web.strings import UI_STRINGS

_S = UI_STRINGS

#: Изображения — отличают «комикс»-ZIP от «некнижного» ZIP.
_IMAGE_EXTS = frozenset(
    {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "avif"}
)
#: Операторы вывода текста в PDF-content-стримах: строка ПЕРЕД Tj/TJ/'
#: и строка ПОСЛЕ " (операторы с двумя аргументами позиции).
_PDF_TEXT_RE = re.compile(rb"\s(Tj|TJ|')(\s|[\r\n]|\Z)")
_PDF_DQUOTE_RE = re.compile(rb'\s"\s*\(')


def _reject(key: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": False, "reason": key, "detail": _S[key].format(**(params or {}))}


def _head(path: Path, n: int) -> bytes:
    """Первые ``n`` байт файла (магия форматов — по началу)."""
    with path.open("rb") as handle:
        return handle.read(n)


def _looks_audio(head: bytes) -> bool:
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return True
    if head.startswith(b"fLaC") or head.startswith(b"OggS"):
        return True
    return head.startswith(b"RIFF") and head[8:12] == b"WAVE"


def _epub_drm(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            return any(
                n.upper() == "META-INF/ENCRYPTION.XML" for n in archive.namelist()
            )
    except zipfile.BadZipFile:
        return False


def _mobi_drm(path: Path) -> bool:
    """Флаг DRM в заголовке MOBI (bit 0x80 поля flags, смещение 0x58
    MOBI-заголовка); AZW3 — контейнер ZIP, берётся первая .mobi/.azw."""
    data = path.read_bytes()
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if name.lower().endswith((".mobi", ".azw")):
                        data = archive.read(name)
                        break
                else:
                    return False
        except zipfile.BadZipFile:
            return False
    if len(data) < 4 or data[:4] != b"\x05\x04\x00\x00":
        return False
    if len(data) < 0x20:
        return False
    pdb_size = struct.unpack(">I", data[0x1C : 0x20])[0]
    mobi = data[pdb_size:]
    if len(mobi) < 0x5C:
        return False
    flags = struct.unpack(">I", mobi[0x58:0x5C])[0]
    return bool(flags & 0x80)


def _pdf_has_text(data: bytes) -> bool:
    """Есть ли текстовый слой: хотя бы один раскомпрессированный
    content-стрим с оператором вывода текста."""
    for match in re.finditer(rb"stream\r?\n", data):
        start = match.end()
        end = data.find(b"endstream", start)
        if end == -1:
            continue
        raw = data[start:end]
        raw = _try_inflate(raw)
        if _PDF_TEXT_RE.search(raw) or _PDF_DQUOTE_RE.search(raw):
            return True
    return False


def _try_inflate(raw: bytes) -> bytes:
    """Flate-декомпрессия content-стрима; не Flate — вернуть сырьё."""
    try:
        return zlib.decompress(raw)
    except zlib.error:
        return raw


def _zip_book(path: Path, accept: frozenset[str]) -> tuple[str | None, str | None]:
    """(формат книги или None, причина-отказа или None).

    Ровно одна поддерживаемая книга — конвертация как она (calibre сам
    читает ZIP-обёртку); несколько — «ZIP-архив нескольких книг»;
    только изображения — комикс; иначе — «некнижный ZIP»."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile:
        return None, "err_zip_bad"
    books: list[str] = []
    images = 0
    for name in names:
        lowered = name.lower()
        if lowered.startswith("__macosx/") or lowered.endswith("/.ds_store"):
            continue
        ext = Path(name).suffix.lstrip(".").lower()
        if ext in accept:
            books.append(ext)
        elif ext in _IMAGE_EXTS:
            images += 1
    if len(books) > 1:
        return None, "err_zip_multi"
    if not books:
        return None, "err_comics" if images else "err_zip_no_book"
    return books[0], None


def classify(  # noqa: PLR0911 — отказ-конвейер (ТЗ §8): каждый отказ — свой возврат
    settings: Settings, path: Path, ext: str, size_bytes: int
) -> dict[str, Any]:
    """Пре-проверка сохранённого файла (до конвертации).

    ``ext`` — нормализовано (без точки, строчное). ``size_bytes`` —
    фактический размер (лимит проверяет API до сохранения, здесь —
    перепроверка для прямого вызова)."""
    accept = frozenset(settings.converter.accept)
    if size_bytes > settings.limits.upload_max_mb * 1024 * 1024:
        return _reject("err_upload_too_big", {"mb": settings.limits.upload_max_mb})

    if ext in ("cbz", "cbr"):
        return _reject("err_comics")
    if ext == "htm":
        ext = "html"
    if ext == "zip":
        fmt, key = _zip_book(path, accept)
        if key is not None:
            return _reject(key)
        return {"ok": True, "format": fmt}
    if ext not in accept:
        return _reject(
            "err_format_unsupported",
            {"ext": ext, "formats": ", ".join("." + f for f in settings.converter.accept)},
        )
    if _looks_audio(_head(path, 16)):
        return _reject("err_audiobook")
    key = _content_reject(ext, path)
    if key is not None:
        return _reject(key)
    return {"ok": True, "format": ext}


def _content_reject(ext: str, path: Path) -> str | None:
    """Отказ по содержанию в пределах формата: DRM (EPUB/PDF/MOBI)
    и скан-PDF (ТЗ §8). ``None`` — формат проходит."""
    if ext == "epub":
        return "err_drm" if _epub_drm(path) else None
    if ext == "pdf":
        data = path.read_bytes()
        if b"/Encrypt" in data:
            return "err_drm"
        if not _pdf_has_text(data):
            return "err_scan_pdf"
        return None
    if ext in ("mobi", "azw", "azw3"):
        return "err_drm" if _mobi_drm(path) else None
    return None
