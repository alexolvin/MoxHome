"""Детерминированный генератор фикстур ВРЕДНЫХ обложек (TZ-UI-V §11, V-T8).

Запуск: python tests/fixtures/covers/build.py
Генерирует в tests/fixtures/covers/ (коммитятся в репозиторий):
  cover-svg.epub    — обложка image/svg+xml с properties="cover-image"
                      (SVG запрещён §8.2 → заглушка);
  cover-huge.epub   — cover.jpg с ОБЪЯВЛЕННЫМ размером 1 ГБ в заголовках
                      zip (фактический payload мал; проверка
                      ZipInfo.file_size ДО чтения, §8.2);
  cover-broken.epub — cover.jpg с properties="cover-image", JPEG обрезан
                      посередине (декодирование падает → заглушка).

Контент и метадаты фиксированы (timestamps в ZipInfo). JPEG генерируется
Pillow — запускать в окружении с Pillow (audit-venv; в проекте — с V2,
TZ §8.3); закоммиченные байты — источник правды, скрипт — для
регенерации.
"""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

from PIL import Image

# Фиксированная дата записей архива (воспроизводимость, как в epub/build.py).
_FIXED_ZIP_DATE: tuple[int, int, int, int, int, int] = (2026, 9, 23, 0, 0, 0)
OUT_DIR = Path(__file__).resolve().parent
HUGE_SIZE = 1024 * 1024 * 1024  # 1 ГБ, объявленный размер (TZ §11)

CONTAINER_XML = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

CHAPTER_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en">
<head>
  <title>Chapter One</title>
</head>
<body>
  <h1 id="h1">Chapter One</h1>
  <p id="p1">Cover fixture for the TZ-UI-V security tests (V-T8).</p>
</body>
</html>
"""


def _opf(cover_item: str, title: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:uuid:{title.lower().replace(' ', '-')}-cover-fixture</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">2026-09-23T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    {cover_item}
  </manifest>
  <spine>
    <itemref idref="ch1"/>
  </spine>
</package>
"""


def _small_jpeg() -> bytes:
    """Маленький корректный JPEG 16×24 (Pillow, фиксированный quality)."""
    buf = io.BytesIO()
    Image.new("RGB", (16, 24), (120, 30, 30)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _write_epub(
    path: Path,
    cover_name: str,
    cover_bytes: bytes,
    cover_item: str,
    title: str,
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("mimetype", date_time=_FIXED_ZIP_DATE)
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, "application/epub+zip")
        for name, content in (
            ("META-INF/container.xml", CONTAINER_XML),
            ("OEBPS/content.opf", _opf(cover_item, title)),
            ("OEBPS/chapter1.xhtml", CHAPTER_XHTML),
            (f"OEBPS/{cover_name}", None),
        ):
            info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, cover_bytes if content is None else content)


def _patch_declared_size(path: Path, member: str, declared: int) -> None:
    """Замена ОБЪЯВЛЁННЫХ размеров элемента zip (локальный заголовок +
    центральный каталог) на `declared` без изменения payload — модель
    «вредного архива»: чтение данных падает, метаданные вводят в
    заблуждение (проверка размера ДО чтения, TZ §8.2)."""
    data = bytearray(path.read_bytes())
    name_b = member.encode()
    # Локальные заголовки: имя смещено на 30 байт (name_len @ +26).
    patched_local = False
    i = 0
    while (i := data.find(b"PK\x03\x04", i)) >= 0:
        nlen = struct.unpack_from("<H", data, i + 26)[0]
        if data[i + 30 : i + 30 + nlen] == name_b:
            struct.pack_into("<I", data, i + 18, declared)  # compressed
            struct.pack_into("<I", data, i + 22, declared)  # uncompressed
            patched_local = True
        i += 4
    # Центральный каталог: имя смещено на 46 байт (name_len @ +28).
    patched_central = False
    i = 0
    while (i := data.find(b"PK\x01\x02", i)) >= 0:
        nlen = struct.unpack_from("<H", data, i + 28)[0]
        if data[i + 46 : i + 46 + nlen] == name_b:
            struct.pack_into("<I", data, i + 20, declared)  # compressed
            struct.pack_into("<I", data, i + 24, declared)  # uncompressed
            patched_central = True
        i += 4
    if not (patched_local and patched_central):
        raise SystemExit(f"элемент {member} не найден в {path.name}")
    path.write_bytes(bytes(data))


def build() -> None:
    # 1. SVG-обложка (запрещённый формат, §8.2).
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="180">'
        b"<rect width='120' height='180' fill='#222'/>"
        b"<text x='10' y='90' fill='#fff'>SVG</text></svg>"
    )
    _write_epub(
        OUT_DIR / "cover-svg.epub",
        "cover.svg",
        svg,
        '<item id="cover" href="cover.svg" media-type="image/svg+xml" properties="cover-image"/>',
        "Cover SVG Fixture",
    )

    # 2. Объявленный размер 1 ГБ (payload — маленький корректный JPEG).
    _write_epub(
        OUT_DIR / "cover-huge.epub",
        "cover.jpg",
        _small_jpeg(),
        '<item id="cover" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>',
        "Cover Huge Fixture",
    )
    _patch_declared_size(OUT_DIR / "cover-huge.epub", "OEBPS/cover.jpg", HUGE_SIZE)

    # 3. Битый JPEG: корректный, затем обрезан до 60 % (EOI потерян).
    jpeg = _small_jpeg()
    broken = jpeg[: max(2, int(len(jpeg) * 0.6))]
    _write_epub(
        OUT_DIR / "cover-broken.epub",
        "cover.jpg",
        broken,
        '<item id="cover" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>',
        "Cover Broken Fixture",
    )

    print("готово: cover-svg.epub, cover-huge.epub, cover-broken.epub")


if __name__ == "__main__":
    build()
