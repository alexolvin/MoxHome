"""Детерминированный генератор EPUB-фикстур (D9).

Запуск: python tests/fixtures/epub/build.py
Генерирует в tests/fixtures/epub/ (коммитятся в репозиторий):
  probe.en.epub  — E1: 2 главы, rich-разметка (для ENGINE_PROBE);
  complex.epub   — E2: 3 главы, имена файлов перемешаны относительно spine,
                   таблица, dl, img, сноска; эталон сегментов 8/13/5;
  drm.epub       — E2-3: корректная EPUB + META-INF/encryption.xml;
  broken.epub    — E2-7: обрезанный OPF (невалидный XML);
  lang_de/fr/nl/it.epub — E2-6 (Rule 4): минимальные книги на 5 языках
                          (en — probe.en.epub).
Контент и метадаты — фиксированы: повторная генерация даёт побайтово тот же результат
(timestamps фиксируются в zipfile.ZipInfo).
"""

from __future__ import annotations

import base64
import zipfile
from collections.abc import Mapping
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


def _write_epub(out_path: Path, files: Mapping[str, str | bytes]) -> None:
    with zipfile.ZipFile(out_path, "w") as archive:
        # mimetype — первая запись, БЕЗ сжатия (требование EPUB, E5-4)
        info = zipfile.ZipInfo("mimetype", date_time=_FIXED_ZIP_DATE)
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, "application/epub+zip")
        for name, content in files.items():
            info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)


def build_probe_book(out_path: Path) -> None:
    """Собирает EPUB3-фикстуру: 2 главы, rich-разметка, nav, CSS."""
    files: dict[str, str | bytes] = {
        "META-INF/container.xml": CONTAINER_XML,
        "OEBPS/content.opf": CONTENT_OPF,
        "OEBPS/chapter1.xhtml": CHAPTER1_XHTML,
        "OEBPS/chapter2.xhtml": CHAPTER2_XHTML,
        "OEBPS/nav.xhtml": NAV_XHTML,
        "OEBPS/style.css": STYLE_CSS,
    }
    _write_epub(out_path, files)


# ---------------------------------------------------------------- E2 fixtures

# 1x1 PNG (детерминированные байты).
_DOT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# E2-4: имена файлов намеренно НЕ совпадают с порядком spine.
COMPLEX_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="complex-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="complex-id">urn:uuid:22222222-3333-4444-5555-666666666666</dc:identifier>
    <dc:title>Complex Reader Fixture</dc:title>
    <dc:creator>Reader Fixture Author</dc:creator>
    <dc:description>A reader fixture with shuffled file names, tables, lists and a footnote.</dc:description>
    <dc:language>en</dc:language>
    <dc:date>2026-01-01</dc:date>
  </metadata>
  <manifest>
    <item id="ch1" href="z_alpha.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="m_mid.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch3" href="a_last.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="dot" href="img/dot.png" media-type="image/png"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
    <itemref idref="ch3"/>
  </spine>
</package>
"""

COMPLEX_CH1 = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="en" xml:lang="en">
<head>
  <title>Chapter One</title>
</head>
<body>
  <h1 id="c1-h1">Chapter One</h1>
  <p id="c1-p1">The quick <b>brown fox</b> jumps over the <i>lazy dog</i> near the river.
The fox had been waiting for a long time, and the river had not stopped flowing.
Every morning the fishers would walk along the bank, and every evening the fox would
return to the same stone.</p>
  <p id="c1-p2">She visited the <a href="https://example.com/old-mill" id="c1-a1">old mill</a> on a
cold morning. The mill was standing still, and the water beneath it moved slowly.
She stood for a long time and listened to the sound of the water against the
wooden planks of the old bridge.</p>
  <p id="c1-p3">The third paragraph has no special markup at all. It exists to make the chapter
long enough for a stable token estimate, and it carries the weight of the whole
first chapter without any inline elements inside it.</p>
  <ul>
    <li id="c1-li1">First list item with <b>emphasis</b>. The emphasis belongs to the word that
carries the meaning of the sentence.</li>
    <li>Second list item, plain, without any markup at all.</li>
  </ul>
  <blockquote id="c1-bq"><p>The fox said: patience is a virtue, but the river does not wait
for anyone, and the mill does not turn by itself.</p></blockquote>
</body>
</html>
"""

COMPLEX_CH2 = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="en" xml:lang="en">
<head>
  <title>Chapter Two</title>
</head>
<body>
  <h2 id="c2-h2">Chapter Two</h2>
  <p id="c2-p1">A second chapter with a table and a definition list. The table below compares
two measurements taken at the mill on the same morning, and the definition list
explains the terms that the old miller used in his notebook.</p>
  <table id="c2-t1">
    <tr><td>alpha</td><td>beta</td></tr>
    <tr><td>gamma</td><td>delta</td></tr>
  </table>
  <dl>
    <dt id="c2-dt1">virtue</dt>
    <dd id="c2-dd1">the quality of being morally right; in the notebook of the miller it is
always written with a capital letter.</dd>
    <dt id="c2-dt2">patience</dt>
    <dd id="c2-dd2">the ability to wait for the water to clear.</dd>
  </dl>
  <p id="c2-p2">An inline figure follows. <img src="img/dot.png" alt="dot"/></p>
  <p id="c2-p3">The fox returned to the river in the evening, and the water was clearer than
in the morning. The miller noted the temperature of the river in his notebook
next to the word patience.<a href="#c2-fn1" role="doc-noteref">[1]</a></p>
  <p id="c2-fn1">Footnote: the river was cold that year, colder than the miller remembered
from the previous season.</p>
</body>
</html>
"""

COMPLEX_CH3 = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="en" xml:lang="en">
<head>
  <title>Chapter Three</title>
</head>
<body>
  <h3 id="c3-h3">Chapter Three</h3>
  <p id="c3-p1">A short final chapter for the reader probe. It keeps the book small while
still carrying enough text to make the token estimate stable against the
measured spend of the translation run.</p>
  <blockquote id="c3-bq"><p>Ends with a question: did the fox really cross the river,
or did it only look at the water for a long time?</p></blockquote>
  <ul>
    <li id="c3-li1">One item only, to remember that lists are segments as well.</li>
  </ul>
</body>
</html>
"""

COMPLEX_NAV = """<?xml version="1.0" encoding="utf-8"?>
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
      <li><a href="z_alpha.xhtml">Chapter One</a></li>
      <li><a href="m_mid.xhtml">Chapter Two</a></li>
      <li><a href="a_last.xhtml">Chapter Three</a></li>
    </ol>
  </nav>
</body>
</html>
"""


def build_complex_book(out_path: Path) -> None:
    """E2: 3 главы с перемешанными именами файлов; эталон сегментов 8/13/5."""
    files: dict[str, str | bytes] = {
        "META-INF/container.xml": CONTAINER_XML,
        "OEBPS/content.opf": COMPLEX_OPF,
        "OEBPS/z_alpha.xhtml": COMPLEX_CH1,
        "OEBPS/m_mid.xhtml": COMPLEX_CH2,
        "OEBPS/a_last.xhtml": COMPLEX_CH3,
        "OEBPS/nav.xhtml": COMPLEX_NAV,
        "OEBPS/style.css": STYLE_CSS,
        "OEBPS/img/dot.png": _DOT_PNG,
    }
    _write_epub(out_path, files)


DRM_CHAPTER = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en">
<head>
  <title>Protected</title>
</head>
<body>
  <h1>Protected Chapter</h1>
  <p>Encapsulated content.</p>
</body>
</html>
"""

DRM_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="drm-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="drm-id">urn:uuid:44444444-5555-6666-7777-888888888888</dc:identifier>
    <dc:title>DRM Protected Book</dc:title>
    <dc:creator>DRM Author</dc:creator>
    <dc:language>en</dc:language>
    <dc:date>2026-01-01</dc:date>
  </metadata>
  <manifest>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
  </spine>
</package>
"""

DRM_ENCRYPTION_XML = """<?xml version="1.0" encoding="utf-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <encryptionScheme>
    <algorithmName>http://www.idpf.org/2008/embedding#RSA-v1-AES128GCM</algorithmName>
  </encryptionScheme>
  <encapsulatedContent>
    <originalContent>
      <uri>OEBPS/chapter1.xhtml</uri>
    </originalContent>
  </encapsulatedContent>
</encryption>
"""


def build_drm_book(out_path: Path) -> None:
    """E2-3: корректная EPUB + META-INF/encryption.xml → должна отклоняться."""
    files: dict[str, str | bytes] = {
        "META-INF/container.xml": CONTAINER_XML,
        "META-INF/encryption.xml": DRM_ENCRYPTION_XML,
        "OEBPS/content.opf": DRM_OPF,
        "OEBPS/chapter1.xhtml": DRM_CHAPTER,
    }
    _write_epub(out_path, files)


BROKEN_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Broken"""


def build_broken_book(out_path: Path) -> None:
    """E2-7/T-E2-7: обрезанный OPF (невалидный XML), остальная структура корректна."""
    files: dict[str, str | bytes] = {
        "META-INF/container.xml": CONTAINER_XML,
        "OEBPS/content.opf": BROKEN_OPF,
        "OEBPS/chapter1.xhtml": DRM_CHAPTER,
    }
    _write_epub(out_path, files)


def _lang_chapter(title: str, lang: str, lines: tuple[str, str]) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="{lang}" xml:lang="{lang}">
<head>
  <title>{title}</title>
</head>
<body>
  <h1 id="l-h1">{title}</h1>
  <p id="l-p1">{lines[0]}</p>
  <p id="l-p2">{lines[1]}</p>
</body>
</html>
"""


def _lang_opf(title: str, lang: str, uid: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="lang-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="lang-id">{uid}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator>Language Fixture Author</dc:creator>
    <dc:language>{lang}</dc:language>
    <dc:date>2026-01-01</dc:date>
  </metadata>
  <manifest>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
  </spine>
</package>
"""


def _lang_nav(title: str, lang: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="{lang}" xml:lang="{lang}">
<head>
  <title>Contents</title>
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>Contents</h1>
    <ol>
      <li><a href="chapter1.xhtml">{title}</a></li>
    </ol>
  </nav>
</body>
</html>
"""


def build_lang_book(out_path: Path, lang: str, title: str, uid: str, lines: tuple[str, str]) -> None:
    """E2-6 (Rule 4): минимальная корректная книга на языке `lang` (с nav для движка)."""
    files: dict[str, str | bytes] = {
        "META-INF/container.xml": CONTAINER_XML,
        "OEBPS/content.opf": _lang_opf(title, lang, uid),
        "OEBPS/chapter1.xhtml": _lang_chapter(title, lang, lines),
        "OEBPS/nav.xhtml": _lang_nav(title, lang),
    }
    _write_epub(out_path, files)


LANG_BOOKS: dict[str, tuple[str, str, tuple[str, str]]] = {
    "de": (
        "Deutsche Probe",
        "urn:uuid:33333333-1111-2222-3333-111111111111",
        ("Der schnelle braune Fuchs springt über den faulen Hund am Fluss.",
         "Die Mühle stand still, und das Wasser bewegte sich langsam."),
    ),
    "fr": (
        "Probe française",
        "urn:uuid:33333333-2222-3333-4444-111111111111",
        ("Le rapide renard brun saute par-dessus le chien paresseux près de la rivière.",
         "Le moulin était immobile, et l'eau se déplaçait lentement."),
    ),
    "nl": (
        "Nederlandse Proef",
        "urn:uuid:33333333-3333-4444-5555-111111111111",
        ("De snelle bruine vos springt over de luie hond bij de rivier.",
         "De molen stond stilstaand, en het water bewoeg zich langzaam."),
    ),
    "it": (
        "Prova italiana",
        "urn:uuid:33333333-4444-5555-6666-111111111111",
        ("La volpe marrone veloce salta sopra il cane pigro vicino al fiume.",
         "Il mulino era fermo, e l'acqua si muoveva lentamente."),
    ),
}


def main() -> None:
    fixture_dir = Path(__file__).parent
    build_probe_book(fixture_dir / "probe.en.epub")
    build_complex_book(fixture_dir / "complex.epub")
    build_drm_book(fixture_dir / "drm.epub")
    build_broken_book(fixture_dir / "broken.epub")
    for lang, (title, uid, lines) in LANG_BOOKS.items():
        build_lang_book(fixture_dir / f"lang_{lang}.epub", lang, title, uid, lines)
    print(f"built fixtures in {fixture_dir}")


if __name__ == "__main__":
    main()
