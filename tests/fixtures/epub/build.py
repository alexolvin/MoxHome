"""Детерминированный генератор EPUB-фикстур (D9).

Запуск: python tests/fixtures/epub/build.py
Генерирует файлы в tests/fixtures/epub/ (коммитятся в репозиторий).
Контент и метадаты — фиксированы: повторная генерация даёт побайтово тот же результат,
кроме zip-метаданных (timestamps фиксируются в zipfile.ZipInfo).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

# Фиксированная дата записей архива (воспроизводимость).
_FIXED_ZIP_DATE: tuple[int, int, int, int, int, int] = (2026, 1, 1, 0, 0, 0)

CONTAINER_XML = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

STYLE_CSS = """body { font-family: serif; }
h1, h2 { color: #222; }
"""

CHAPTER1_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="en" xml:lang="en">
<head>
  <title>Chapter One</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <h1 id="h1-1">Chapter One</h1>
  <p id="p1">The quick <b>brown fox</b> jumps over the <i>lazy dog</i> near the river.</p>
  <p id="p2">She visited the <a href="https://example.com/old-mill" id="a1">old mill</a> on a cold morning.</p>
  <p>The third paragraph has no id and contains a <strong>strong phrase</strong> and a <code>code span</code>.</p>
  <ul>
    <li id="li1">First list item with <b>emphasis</b>.</li>
    <li>Second list item, plain.</li>
  </ul>
  <blockquote id="bq1"><p>The fox said: patience is a virtue.</p></blockquote>
  <p id="p3">End of chapter one.</p>
</body>
</html>
"""

CHAPTER2_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="en" xml:lang="en">
<head>
  <title>Chapter Two</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <h2 id="h2-1">Chapter Two</h2>
  <p id="q1">A short second chapter for the probe run.</p>
  <table id="t1">
    <tr><td>alpha</td><td>beta</td></tr>
    <tr><td>gamma</td><td>delta</td></tr>
  </table>
</body>
</html>
"""

NAV_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="en" xml:lang="en">
<head>
  <title>Contents</title>
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>Contents</h1>
    <ol>
      <li><a href="chapter1.xhtml">Chapter One</a></li>
      <li><a href="chapter2.xhtml">Chapter Two</a></li>
    </ol>
  </nav>
</body>
</html>
"""

CONTENT_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:uuid:11111111-2222-3333-4444-555555555555</dc:identifier>
    <dc:title>Probe Book</dc:title>
    <dc:creator>Probe Author</dc:creator>
    <dc:description>A small probe book for engine verification.</dc:description>
    <dc:subject>probe</dc:subject>
    <dc:language>en</dc:language>
    <dc:date>2026-01-01</dc:date>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
  </spine>
</package>
"""


def build_probe_book(out_path: Path) -> None:
    """Собирает EPUB3-фикстуру: 2 главы, rich-разметка, nav, CSS."""
    files: dict[str, str] = {
        "META-INF/container.xml": CONTAINER_XML,
        "OEBPS/content.opf": CONTENT_OPF,
        "OEBPS/chapter1.xhtml": CHAPTER1_XHTML,
        "OEBPS/chapter2.xhtml": CHAPTER2_XHTML,
        "OEBPS/nav.xhtml": NAV_XHTML,
        "OEBPS/style.css": STYLE_CSS,
    }
    with zipfile.ZipFile(out_path, "w") as archive:
        # mimetype — первая запись, БЕЗ сжатия (требование EPUB, E5-4)
        info = zipfile.ZipInfo("mimetype", date_time=_FIXED_ZIP_DATE)
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, "application/epub+zip")
        for name, content in files.items():
            info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)


def main() -> None:
    fixture_dir = Path(__file__).parent
    build_probe_book(fixture_dir / "probe.en.epub")
    print(f"built: {fixture_dir / 'probe.en.epub'}")


if __name__ == "__main__":
    main()
