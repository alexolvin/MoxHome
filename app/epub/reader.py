"""Ридер EPUB (тракт A, D14): детерминированное чтение в Book → Chapter → Segment.

Порядок валидации (E2.5) — до любого обращения к LLM:
ZIP → mimetype первой записью → DRM (отказ) → container.xml → OPF → spine →
разбор каждого XHTML spine-документа. Нечитаемый документ — ошибка, не тишина.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import tiktoken
from lxml import etree

from app.core.config import Settings
from app.core.errors import InputValidationError
from app.domain.book import Book, BookStats, Chapter, Segment
from app.epub import segmenter

MIMETYPE_VALUE = "application/epub+zip"
ENCRYPTION_PATH = "META-INF/encryption.xml"
CONTAINER_PATH = "META-INF/container.xml"
PACKAGE_MEDIA_TYPE = "application/oebps-package+xml"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
CHAPTER_MEDIA_TYPES = ("application/xhtml+xml", "text/html")


def read_epub(source: Path, work_dir: Path, settings: Settings) -> Book:
    """Читает EPUB из `source`, распаковывая во `work_dir` (временный каталог задания)."""
    opf_path = _prepare(source, work_dir)
    base_dir = work_dir.resolve()
    opf_file = _locate_opf(base_dir, opf_path)
    opf = _parse_xml(opf_file.read_bytes(), opf_path)
    spine = _spine_items(opf, _parse_manifest(opf))

    block_tags = frozenset(settings.segmentation.block_tags)
    chapters, segments, image_count, table_count, footnote_count = _build_chapters(
        base_dir, opf_file.parent, spine, block_tags
    )

    encoding = tiktoken.get_encoding(settings.llm.profile("translation").token_encoding)
    book_text = "\n\n".join(segment.plain_text for segment in segments)
    stats = BookStats(
        chapter_count=len(chapters),
        spine_document_count=len(spine),
        segment_count=len(segments),
        char_count=sum(segment.char_count for segment in segments),
        word_count=sum(segment.word_count for segment in segments),
        image_count=image_count,
        table_count=table_count,
        footnote_count=footnote_count,
        has_drm=False,
        token_estimate=len(encoding.encode(book_text)),
    )
    reasons: list[str] = []
    if stats.segment_count == 0:
        reasons.append("zero_segments")
    if stats.spine_document_count == 1:
        reasons.append("single_spine_document")

    return Book(
        title=_dc_text(opf, "title"),
        language=_dc_text(opf, "language"),
        chapters=tuple(chapters),
        stats=stats,
        suspicious=bool(reasons),
        suspicious_reasons=tuple(reasons),
    )


def _prepare(source: Path, work_dir: Path) -> str:
    """Валидация ZIP/mimetype/DRM/container + распаковка. Возвращает путь OPF."""
    if not zipfile.is_zipfile(source):
        raise InputValidationError(f"не является ZIP-архивом: {source.name}")
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if not infos:
            raise InputValidationError(f"пустой ZIP-архив: {source.name}")
        if infos[0].filename != "mimetype":
            raise InputValidationError(
                f"первая запись архива не mimetype: {infos[0].filename} (требуется mimetype)"
            )
        if archive.read("mimetype").decode("utf-8").strip() != MIMETYPE_VALUE:
            raise InputValidationError("mimetype не равен application/epub+zip")
        names = set(archive.namelist())
        if ENCRYPTION_PATH in names:
            raise InputValidationError(
                "книга защищена DRM (META-INF/encryption.xml); чтение отказано, "
                "обход DRM не реализуется"
            )
        if CONTAINER_PATH not in names:
            raise InputValidationError("в архиве нет META-INF/container.xml")
        _unpack(archive, work_dir)
        container = _parse_xml(archive.read(CONTAINER_PATH), CONTAINER_PATH)
    return _find_opf_path(container)


def _locate_opf(base_dir: Path, opf_path: str) -> Path:
    opf_file = (base_dir / opf_path).resolve()
    if not opf_file.is_relative_to(base_dir):
        raise InputValidationError(f"OPF вне каталога распаковки: {opf_path}")
    if not opf_file.is_file():
        raise InputValidationError(f"OPF не найден в архиве: {opf_path}")
    return opf_file


def _build_chapters(
    base_dir: Path,
    opf_dir: Path,
    spine: list[tuple[str, str, str]],
    block_tags: frozenset[str],
) -> tuple[list[Chapter], list[Segment], int, int, int]:
    chapters: list[Chapter] = []
    all_segments: list[Segment] = []
    image_count = 0
    table_count = 0
    footnote_count = 0
    global_index = 0
    for spine_index, (item_id, href, media_type) in enumerate(spine):
        doc_file = (opf_dir / href).resolve()
        if not doc_file.is_relative_to(base_dir):
            raise InputValidationError(f"spine-документ вне каталога распаковки: {href}")
        if not doc_file.is_file():
            raise InputValidationError(f"spine-документ не найден в архиве: {href}")
        if media_type not in CHAPTER_MEDIA_TYPES:
            raise InputValidationError(
                f"spine-документ с неподдерживаемым media-type: {href} ({media_type})"
            )
        # путь в архиве — для segment_id (E2.3): стабилен независимо от run-каталога
        doc_path = doc_file.relative_to(base_dir).as_posix()
        root = _parse_xml(doc_file.read_bytes(), doc_path)
        body = segmenter.find_body(root)
        if body is None:
            raise InputValidationError(f"в spine-документе нет <body>: {doc_path}")
        segments = _segment_body(body, doc_path, item_id, global_index, block_tags)
        global_index += len(segments)
        all_segments.extend(segments)
        image_count += segmenter.count_by_localname(root, "img")
        table_count += segmenter.count_by_localname(root, "table")
        footnote_count += segmenter.count_footnotes(root)
        chapters.append(
            Chapter(
                chapter_id=item_id,
                title=_head_title(root),
                document_path=doc_path,
                spine_index=spine_index,
                segments=tuple(segments),
            )
        )
    return chapters, all_segments, image_count, table_count, footnote_count


def _segment_body(
    body: etree._Element,
    doc_path: str,
    chapter_id: str,
    global_offset: int,
    block_tags: frozenset[str],
) -> list[Segment]:
    segments: list[Segment] = []
    for element in segmenter.find_block_elements(body, block_tags):
        plain = segmenter.full_text(element)
        normalized = segmenter.normalize_text(plain)
        segments.append(
            Segment(
                segment_id=segmenter.make_segment_id(
                    doc_path, segmenter.positional_xpath(element), normalized
                ),
                chapter_id=chapter_id,
                index_in_chapter=len(segments),
                index_global=global_offset + len(segments),
                plain_text=plain,
                marked_text=segmenter.inner_marked_text(element),
                char_count=len(normalized),
                word_count=len(normalized.split()),
            )
        )
    return segments


def _unpack(archive: zipfile.ZipFile, work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    base = work_dir.resolve()
    for info in archive.infolist():
        if info.filename.endswith("/"):
            continue
        target = (work_dir / info.filename).resolve()
        if not target.is_relative_to(base):
            raise InputValidationError(f"недопустимый путь записи в архиве: {info.filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, target.open("wb") as dst:
            dst.write(src.read())


def _parse_xml(data: bytes, where: str) -> etree._Element:
    try:
        return etree.fromstring(data)
    except etree.XMLSyntaxError as exc:
        raise InputValidationError(f"не удаётся разобрать XML {where}: {exc}") from exc


def _find_opf_path(container: etree._Element) -> str:
    for rootfile in container.iter():
        if not isinstance(rootfile.tag, str):
            continue
        if segmenter.localname(rootfile) != "rootfile":
            continue
        if rootfile.get("media-type") == PACKAGE_MEDIA_TYPE:
            full_path = rootfile.get("full-path")
            if isinstance(full_path, str) and full_path:
                return full_path
    raise InputValidationError("в container.xml нет rootfile с OPF (application/oebps-package+xml)")


def _parse_manifest(opf: etree._Element) -> dict[str, etree._Element]:
    manifest = opf.find(f"{{{OPF_NS}}}manifest")
    if manifest is None:
        raise InputValidationError("в OPF нет <manifest>")
    items: dict[str, etree._Element] = {}
    for item in manifest:
        item_id = item.get("id")
        if item_id is not None:
            items[item_id] = item
    return items


def _spine_items(
    opf: etree._Element, manifest: dict[str, etree._Element]
) -> list[tuple[str, str, str]]:
    spine = opf.find(f"{{{OPF_NS}}}spine")
    if spine is None:
        raise InputValidationError("в OPF нет <spine>")
    result: list[tuple[str, str, str]] = []
    for itemref in spine:
        if segmenter.localname(itemref) != "itemref":
            continue
        idref = itemref.get("idref") or ""
        item = manifest.get(idref)
        if item is None:
            raise InputValidationError(f"spine ссылается на неизвестный item manifest: {idref}")
        href = item.get("href")
        if not href:
            raise InputValidationError(f"item manifest без href (id={idref})")
        result.append((idref, href, item.get("media-type") or ""))
    if not result:
        raise InputValidationError("spine не содержит ни одного документа")
    return result


def _head_title(root: etree._Element) -> str | None:
    for child in root:
        if segmenter.localname(child) != "head":
            continue
        for grand in child.iter():
            if segmenter.localname(grand) == "title" and grand.text:
                return grand.text.strip()
        return None
    return None


def _dc_text(opf: etree._Element, field: str) -> str | None:
    for element in opf.iter():
        if not isinstance(element.tag, str):
            continue
        if element.tag == f"{{{DC_NS}}}{field}" and element.text:
            return element.text.strip()
    return None
