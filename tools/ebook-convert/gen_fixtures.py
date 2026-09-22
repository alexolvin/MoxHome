#!/usr/bin/env python3
"""R6 (TZ-R §8): генератор 12 фикстур формата для приёма T7.

Из одного базового текста (2 главы, en) — 12 файлов (handoff R6,
решение 12):
  book.epub   — EPUB3 с обложкой (properties="cover-image");
  book.fb2    — FB2 2.0 (XML);
  book.mobi   — из book.epub РЕАЛЬНЫМ ebook-convert в контейнере;
  book.azw3   — из book.txt РЕАЛЬНЫМ ebook-convert (KF8; из EPUB-с
                навигацией KF8-раундтреп пишет невалидные XML-id
                «N-<hash>» → EPUBCheck RSC-005, замер 2026-09-21,
                calibre 8.5.0; поправка к решению 12);
  book.docx   — минимально валидный docx (OOXML zip);
  book.odt    — минимально валидный odt (ODF zip);
  book.rtf    — базовый RTF;
  book.txt    — plain text;
  book.md     — Markdown;
  book.html   — HTML5;
  book.htmlz  — ZIP одной HTML-книги;
  book.pdf    — ручной PDF-1.4 с текстовым слоем (оператор Tj).

Детерминированность: содержимое фиксировано, даты записей zip —
(2026, 1, 1); вывод воспроизводится. Вывод — .a/tasks/ui-audit/
fixtures/import/ (НЕ коммитится — .a/ вне git; --out переопределяет).
Внешних зависимостей нет (stdlib); .mobi/.azw3 — subprocess-вызов
обёртки рядом (нужен собранный образ, build.sh).

Запуск: python tools/ebook-convert/gen_fixtures.py [--out DIR]
                                              [--skip-calibre]
"""

from __future__ import annotations

import argparse
import base64
import io
import subprocess
import sys
import zipfile
from pathlib import Path

#: Фиксированная дата записей архивов (воспроизводимость).
ZIP_DATE: tuple[int, int, int, int, int, int] = (2026, 1, 1, 0, 0, 0)

#: Базовый текст (единый для всех форматов) — как tests/fixtures/epub.
CHAPTER1_TITLE = "Chapter One"
CHAPTER1_PARAS = (
    "The quick brown fox jumps over the lazy dog near the river.",
    "She visited the old mill on a cold morning.",
    "The third paragraph has no special purpose at all.",
)
CHAPTER2_TITLE = "Chapter Two"
CHAPTER2_PARAS = (
    "A short second chapter for the probe run.",
    "The water beneath the mill moved slowly and quietly.",
)

#: 1x1 PNG (детерминированные байты) — обложка EPUB-фикстуры.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

WRAPPER = Path(__file__).resolve().parent / "ebook-convert"


# ------------------------------------------------------------------ EPUB


def _epub_files() -> dict[str, str | bytes]:
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="r6fix-id">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="r6fix-id">'
        "urn:uuid:aaaa0000-1111-2222-3333-444455556666</dc:identifier>"
        "<dc:title>Probe Book</dc:title><dc:creator>Probe Author"
        "</dc:creator><dc:language>en</dc:language>"
        "<dc:date>2026-01-01</dc:date>"
        '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>'
        "  </metadata>\n"
        "  <manifest>"
        '<item id="ch1" href="chapter1.xhtml" '
        'media-type="application/xhtml+xml"/>'
        '<item id="ch2" href="chapter2.xhtml" '
        'media-type="application/xhtml+xml"/>'
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
        'properties="nav"/>'
        '<item id="cover" href="cover.png" media-type="image/png" '
        'properties="cover-image"/>'
        "  </manifest>\n"
        "  <spine><itemref idref=\"ch1\"/><itemref idref=\"ch2\"/></spine>\n"
        "</package>\n"
    )

    def chapter(title: str, paras: tuple[str, ...]) -> str:
        body = "".join(f"<p>{p}</p>" for p in paras)
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" lang="en" '
            f'xml:lang="en">\n<head><title>{title}</title></head>\n'
            f"<body>\n<h1>{title}</h1>\n{body}\n</body>\n</html>\n"
        )

    nav = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">\n'
        "<head><title>Contents</title></head>\n"
        '<body>\n<nav epub:type="toc" id="toc">\n<h1>Contents</h1>\n<ol>\n'
        f'<li><a href="chapter1.xhtml">{CHAPTER1_TITLE}</a></li>\n'
        f'<li><a href="chapter2.xhtml">{CHAPTER2_TITLE}</a></li>\n'
        "</ol>\n</nav>\n</body>\n</html>\n"
    )
    container = (
        '<?xml version="1.0"?>\n'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        "  <rootfiles>\n"
        '    <rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/>\n'
        "  </rootfiles>\n</container>\n"
    )
    return {
        "META-INF/container.xml": container,
        "OEBPS/content.opf": opf,
        "OEBPS/chapter1.xhtml": chapter(CHAPTER1_TITLE, CHAPTER1_PARAS),
        "OEBPS/chapter2.xhtml": chapter(CHAPTER2_TITLE, CHAPTER2_PARAS),
        "OEBPS/nav.xhtml": nav,
        "OEBPS/cover.png": PNG_1X1,
    }


def write_epub(out: Path) -> None:
    with zipfile.ZipFile(out, "w") as archive:
        info = zipfile.ZipInfo("mimetype", date_time=ZIP_DATE)
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, "application/epub+zip")
        for name, content in _epub_files().items():
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)


# ------------------------------------------------------------------ FB2


def write_fb2(out: Path) -> None:
    def section(title: str, paras: tuple[str, ...]) -> str:
        body = "".join(f"<p>{p}</p>" for p in paras)
        return f"<section><title><p>{title}</p></title>{body}</section>"

    out.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">\n'
        "  <description>\n"
        "    <title-info><genre>prose</genre>"
        "<author><first-name>Probe</first-name>"
        "<last-name>Author</last-name></author>"
        "<book-title>Probe Book</book-title><lang>en</lang></title-info>\n"
        "    <document-info>"
        "<author><first-name>Probe</first-name>"
        "<last-name>Author</last-name></author>"
        "<book-title>Probe Book</book-title><src-lang>en</src-lang>"
        "<initial-created>2026-01-01T00:00:00</initial-created>"
        "</document-info>\n"
        "  </description>\n"
        "  <body>\n"
        f"    {section(CHAPTER1_TITLE, CHAPTER1_PARAS)}\n"
        f"    {section(CHAPTER2_TITLE, CHAPTER2_PARAS)}\n"
        "  </body>\n"
        "</FictionBook>\n",
        encoding="utf-8",
    )


# ------------------------------------------------------------------ OOXML


def _zip_with_files(
    out: Path, entries: dict[str, bytes], *, first_stored: str | None = None
) -> None:
    with zipfile.ZipFile(out, "w") as archive:
        for name, content in entries.items():
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            if first_stored and name == first_stored:
                info.compress_type = zipfile.ZIP_STORED
            else:
                info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)


def write_docx(out: Path) -> None:
    CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
    CT_RELS = "application/vnd.openxmlformats-package.relationships+xml"
    CT_DOC = (
        "application/vnd.openxmlformats-officedocument"
        ".wordprocessingml.document.main+xml"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Types xmlns="{CT_NS}">'
        f'<Default Extension="rels" ContentType="{CT_RELS}"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/word/document.xml" ContentType="{CT_DOC}"/>'
        "</Types>\n"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships '
        'xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>\n"
    )
    w_ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

    def w_p(text: str, *, bold: bool = False) -> str:
        rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
        return (
            f'<w:p><w:r>{rpr}'
            f'<w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
        )

    paragraphs = [w_p(CHAPTER1_TITLE, bold=True)]
    paragraphs.extend(w_p(p) for p in CHAPTER1_PARAS)
    paragraphs.append(w_p(CHAPTER2_TITLE, bold=True))
    paragraphs.extend(w_p(p) for p in CHAPTER2_PARAS)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<w:document {w_ns}>"
        f"<w:body>{''.join(paragraphs)}<w:sectPr/></w:body></w:document>\n"
    )
    _zip_with_files(
        out,
        {
            "[Content_Types].xml": content_types.encode("utf-8"),
            "_rels/.rels": rels.encode("utf-8"),
            "word/document.xml": document.encode("utf-8"),
        },
    )


def write_odt(out: Path) -> None:
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<manifest:manifest xmlns:manifest='
        '"urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
        'manifest:version="1.2">\n'
        '<manifest:file-entry manifest:full-path="/" '
        'manifest:media-type="application/vnd.oasis.opendocument.text"/>\n'
        '<manifest:file-entry manifest:full-path="content.xml" '
        'manifest:media-type="text/xml"/>\n'
        '<manifest:file-entry manifest:full-path="meta.xml" '
        'manifest:media-type="text/xml"/>\n'
        '<manifest:file-entry manifest:full-path="styles.xml" '
        'manifest:media-type="text/xml"/>\n'
        "</manifest:manifest>\n"
    )
    meta = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<office:document-meta '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
        'office:version="1.2">\n'
        "<office:meta>"
        "<meta:generator>gen_fixtures (R6 T7)</meta:generator>"
        "</office:meta>\n"
        "</office:document-meta>\n"
    )
    text_blocks = []
    text_blocks.append(
        f'<text:h text:outline-level="1">{CHAPTER1_TITLE}</text:h>'
    )
    for para in CHAPTER1_PARAS:
        text_blocks.append(f"<text:p>{para}</text:p>")
    text_blocks.append(f'<text:h text:outline-level="1">{CHAPTER2_TITLE}</text:h>')
    for para in CHAPTER2_PARAS:
        text_blocks.append(f"<text:p>{para}</text:p>")
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'office:version="1.2">\n'
        f"<office:body><office:text>{''.join(text_blocks)}</office:text>"
        "</office:body></office:document-content>\n"
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<office:document-styles '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'office:version="1.2"/>\n'
    )
    _zip_with_files(
        out,
        {
            "mimetype": b"application/vnd.oasis.opendocument.text",
            "META-INF/manifest.xml": manifest.encode("utf-8"),
            "content.xml": content.encode("utf-8"),
            "meta.xml": meta.encode("utf-8"),
            "styles.xml": styles.encode("utf-8"),
        },
        first_stored="mimetype",
    )


# ------------------------------------------------------------------ остальное


def write_rtf(out: Path) -> None:
    lines = [
        r"{\rtf1\ansi\ansicpg1252\deff0",
        r"{\fonttbl{\f0 Times New Roman;}}",
        r"\f0",
        f"\\b {CHAPTER1_TITLE}\\b0",
        r"\par",
        *CHAPTER1_PARAS,
        r"\par",
        f"\\b {CHAPTER2_TITLE}\\b0",
        r"\par",
        *CHAPTER2_PARAS,
        r"\par",
        "}",
    ]
    out.write_text("\n".join(lines), encoding="latin-1")


def write_txt(out: Path) -> None:
    lines: list[str] = []
    for title, paras in (
        (CHAPTER1_TITLE, CHAPTER1_PARAS),
        (CHAPTER2_TITLE, CHAPTER2_PARAS),
    ):
        lines.append(title)
        lines.extend(paras)
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")


def write_md(out: Path) -> None:
    lines: list[str] = []
    for title, paras in (
        (CHAPTER1_TITLE, CHAPTER1_PARAS),
        (CHAPTER2_TITLE, CHAPTER2_PARAS),
    ):
        lines.append(f"# {title}")
        lines.append("")
        for para in paras:
            lines.append(para)
            lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")


def _html_text() -> str:
    def section(title: str, paras: tuple[str, ...]) -> str:
        body = "".join(f"<p>{p}</p>" for p in paras)
        return f"<h1>{title}</h1>{body}"

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        '<head><meta charset="utf-8"><title>Probe Book</title></head>\n'
        "<body>\n"
        f"{section(CHAPTER1_TITLE, CHAPTER1_PARAS)}\n"
        f"{section(CHAPTER2_TITLE, CHAPTER2_PARAS)}\n"
        "</body>\n</html>\n"
    )


def write_html(out: Path) -> None:
    out.write_text(_html_text(), encoding="utf-8")


def write_htmlz(out: Path) -> None:
    with zipfile.ZipFile(out, "w") as archive:
        info = zipfile.ZipInfo("index.html", date_time=ZIP_DATE)
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, _html_text())


def write_pdf(out: Path) -> None:
    """Ручной PDF-1.4: одна страница, Helvetica, оператор Tj
    (текстовый слой — T7; скан-PDF для T8 генерирует замер)."""
    stream_lines = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    first = True
    for text in (CHAPTER1_TITLE, *CHAPTER1_PARAS, CHAPTER2_TITLE, *CHAPTER2_PARAS):
        if not first:
            stream_lines.append("T*")
        first = False
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream_lines.append(f"({escaped}) Tj")
    stream_lines.append("ET")
    stream = "\n".join(stream_lines).encode("latin-1")
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    )
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref_pos = buf.tell()
    size = len(objects) + 1
    buf.write(f"xref\n0 {size}\n".encode("ascii"))
    buf.write(b"0000000000 65535 f \n")
    for offset in offsets:
        buf.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    buf.write(
        f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n"
        f"{xref_pos}\n%%EOF\n".encode("ascii")
    )
    out.write_bytes(buf.getvalue())


# ------------------------------------------------------------------ calibre


def convert_with_calibre(src: Path, dst: Path) -> None:
    """Реальный ebook-convert в контейнере (обёртка рядом)."""
    if not WRAPPER.is_file():
        raise SystemExit(f"обёртка не найдена: {WRAPPER}")
    proc = subprocess.run(
        [str(WRAPPER), str(src), str(dst)],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        raise SystemExit(f"ebook-convert {src.name} → {dst.name}: {' '.join(tail)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(
            Path(__file__).resolve().parents[2]
            / ".a" / "tasks" / "ui-audit" / "fixtures" / "import"
        ),
        help="каталог вывода (по умолчанию .a/tasks/ui-audit/fixtures/import)",
    )
    parser.add_argument(
        "--skip-calibre",
        action="store_true",
        help="не запускать ebook-convert (mobi/azw3 пропускаются)",
    )
    args = parser.parse_args()
    out_dir = Path(args.out)
    if out_dir.exists():
        for child in out_dir.iterdir():
            if child.is_file():
                child.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)

    writers = (
        ("book.epub", write_epub),
        ("book.fb2", write_fb2),
        ("book.docx", write_docx),
        ("book.odt", write_odt),
        ("book.rtf", write_rtf),
        ("book.txt", write_txt),
        ("book.md", write_md),
        ("book.html", write_html),
        ("book.htmlz", write_htmlz),
        ("book.pdf", write_pdf),
    )
    for name, writer in writers:
        writer(out_dir / name)
        print(f"  {name}")
    if args.skip_calibre:
        print("  mobi/azw3: пропущено (--skip-calibre)")
    else:
        for src_name, name in (
            ("book.epub", "book.mobi"),
            # KF8-раундтреп из EPUB-с-навигацией — невалидные XML-id
            ("book.txt", "book.azw3"),
        ):
            print(f"  {name} (ebook-convert из {src_name})…")
            convert_with_calibre(out_dir / src_name, out_dir / name)
    print(f"создано {len(list(out_dir.glob('book.*')))} фикстур: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
