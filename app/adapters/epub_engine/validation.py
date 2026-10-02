# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Внутренняя структурная валидация выходного EPUB (E5.7, тракт A).

Жёсткий шлюз ПЕРЕД `os.replace` (E5.6): не пройдена → результата нет, задание
FAILED (`OutputValidationFailed`). Проверки (ТЗ E5.7, п.1):
  mimetype первой записью и без сжатия; container.xml; OPF (manifest/spine);
  nav/NCX; разбор всех spine-XHTML; целостность внутренних ссылок; изображения.
Читает только ZIP-архив (без распаковки).
"""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import lxml.etree

from app.core.config import Settings
from app.epub.reader import (
    CONTAINER_PATH,
    MIMETYPE_VALUE,
    OPF_NS,
)

_EXTERNAL_SCHEMES = ("http:", "https:", "mailto:", "urn:", "data:")
_HTML_EXTS = (".xhtml", ".html")


def validate_output_epub(path: Path, settings: Settings) -> list[str]:
    """Возвращает список проблем (пустой — валидно).

    `settings` входит в сигнатуру как единая точка валидации выхода (E5.7);
    текущие проверки не зависят от конфигурации.
    """
    del settings
    if not path.is_file():
        return ["выходной файл отсутствует"]
    if not zipfile.is_zipfile(path):
        return ["выходной файл не является ZIP-архивом"]
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if not infos:
            return ["пустой ZIP-архив"]
        if infos[0].filename != "mimetype":
            return [f"первая запись не mimetype: {infos[0].filename}"]
        return _validate_archive(archive, infos)


@dataclass
class _ValidationContext:
    """Состояние поочерёдных стадий валидации (каждая зависит от предыдущих)."""

    archive: zipfile.ZipFile
    names: set[str]
    problems: list[str]
    opf_name: str | None = None
    opf_dir: str = ""
    opf: lxml.etree._Element | None = None
    manifest: dict[str, lxml.etree._Element] | None = None
    spine: list[str] = field(default_factory=list)
    spine_docs: list[str] = field(default_factory=list)


def _validate_archive(archive: zipfile.ZipFile, infos: list[zipfile.ZipInfo]) -> list[str]:
    problems: list[str] = []
    if infos[0].compress_type != zipfile.ZIP_STORED:
        problems.append("mimetype записан со сжатием (требуется без сжатия)")
    try:
        mime = archive.read("mimetype").decode("utf-8").strip()
    except Exception as exc:
        problems.append(f"mimetype не читается: {exc}")
    else:
        if mime != MIMETYPE_VALUE:
            problems.append(f"mimetype != {MIMETYPE_VALUE}: {mime!r}")

    context = _ValidationContext(archive=archive, names=set(archive.namelist()), problems=problems)
    stages = (
        _stage_container,
        _stage_opf,
        _stage_manifest,
        _stage_spine,
        _stage_toc,
        _stage_spine_docs,
    )
    for stage in stages:
        if not stage(context):
            break
    else:
        problems.extend(find_broken_links(archive, context.spine_docs))
    return problems


def _stage_container(ctx: _ValidationContext) -> bool:
    if CONTAINER_PATH not in ctx.names:
        ctx.problems.append("нет META-INF/container.xml")
        return False
    ctx.opf_name = _container_opf(ctx.archive)
    if ctx.opf_name is None:
        ctx.problems.append("container.xml: rootfile с OPF не найден")
        return False
    ctx.opf_dir = posixpath.dirname(ctx.opf_name)
    return True


def _stage_opf(ctx: _ValidationContext) -> bool:
    if ctx.opf_name is None:
        return False
    ctx.opf = _parse(ctx.archive, ctx.opf_name, ctx.problems)
    return ctx.opf is not None


def _stage_manifest(ctx: _ValidationContext) -> bool:
    if ctx.opf is None:
        return False
    ctx.manifest = _manifest(ctx.opf, ctx.problems)
    return ctx.manifest is not None


def _stage_spine(ctx: _ValidationContext) -> bool:
    if ctx.opf is None or ctx.manifest is None:
        return False
    spine = _spine(ctx.opf, ctx.manifest, ctx.problems)
    if spine is None:
        return False
    ctx.spine = spine
    return True


def _stage_toc(ctx: _ValidationContext) -> bool:
    if ctx.opf is None or ctx.manifest is None:
        return False
    return _check_toc(ctx.archive, ctx.opf_dir, ctx.manifest, ctx.names, ctx.problems)


def _stage_spine_docs(ctx: _ValidationContext) -> bool:
    if ctx.opf_dir == "" or not ctx.spine:
        return False
    docs = _check_spine_xhtml(ctx.archive, ctx.opf_dir, ctx.spine, ctx.names, ctx.problems)
    if docs is None:
        return False
    ctx.spine_docs = docs
    return True


# ------------------------------------------------------------------- служебное


def _container_opf(archive: zipfile.ZipFile) -> str | None:
    try:
        container = lxml.etree.fromstring(archive.read(CONTAINER_PATH))
    except lxml.etree.XMLSyntaxError:
        return None
    for rootfile in container.iter():
        if not isinstance(rootfile.tag, str):
            continue
        if (
            lxml.etree.QName(rootfile.tag).localname == "rootfile"
            and rootfile.get("media-type") == "application/oebps-package+xml"
            and rootfile.get("full-path")
        ):
            return rootfile.get("full-path")
    return None


def _parse(
    archive: zipfile.ZipFile, name: str, problems: list[str]
) -> lxml.etree._Element | None:
    try:
        return lxml.etree.fromstring(archive.read(name))
    except Exception as exc:
        problems.append(f"не удаётся разобрать XML {name}: {exc}")
        return None


def _manifest(
    opf: lxml.etree._Element, problems: list[str]
) -> dict[str, lxml.etree._Element] | None:
    manifest = opf.find(f"{{{OPF_NS}}}manifest")
    if manifest is None:
        problems.append("в OPF нет <manifest>")
        return None
    items: dict[str, lxml.etree._Element] = {}
    for item in manifest:
        item_id = item.get("id")
        if item_id:
            items[item_id] = item
    return items


def _spine(
    opf: lxml.etree._Element,
    manifest: dict[str, lxml.etree._Element],
    problems: list[str],
) -> list[str] | None:
    spine = opf.find(f"{{{OPF_NS}}}spine")
    if spine is None:
        problems.append("в OPF нет <spine>")
        return None
    result: list[str] = []
    for itemref in spine:
        if not isinstance(itemref.tag, str):
            continue
        if lxml.etree.QName(itemref.tag).localname != "itemref":
            continue
        item = manifest.get(itemref.get("idref") or "")
        if item is None or not item.get("href"):
            problems.append(
                f"spine: неизвестный item или без href (idref={itemref.get('idref')!r})"
            )
            return None
        result.append(item.get("href") or "")
    if not result:
        problems.append("spine пуст")
        return None
    return result


def _check_toc(
    archive: zipfile.ZipFile,
    opf_dir: str,
    manifest: dict[str, lxml.etree._Element],
    names: set[str],
    problems: list[str],
) -> bool:
    """nav (EPUB3, properties=\"nav\") или NCX (EPUB2): есть в manifest, в архиве, читается."""
    found = False
    for item in manifest.values():
        if not isinstance(item.tag, str):
            continue
        properties = (item.get("properties") or "").split()
        media_type = item.get("media-type") or ""
        if "nav" in properties or media_type == "application/x-dtbncx+xml":
            href = item.get("href") or ""
            full = posixpath.normpath(posixpath.join(opf_dir, href))
            if full not in names:
                problems.append(f"TOC-документ из manifest отсутствует в архиве: {full}")
                return False
            if _parse(archive, full, problems) is None:
                return False
            found = True
    if not found:
        problems.append("нет nav/NCX (TOC) в manifest")
        return False
    return True


def _check_spine_xhtml(
    archive: zipfile.ZipFile,
    opf_dir: str,
    spine: list[str],
    names: set[str],
    problems: list[str],
) -> list[str] | None:
    docs: list[str] = []
    for href in spine:
        full = posixpath.normpath(posixpath.join(opf_dir, href))
        if full not in names:
            problems.append(f"spine-документ отсутствует в архиве: {full}")
            return None
        if _parse(archive, full, problems) is None:
            return None
        docs.append(full)
    return docs


def _resolve_file(base_dir: str, ref: str) -> str | None:
    """Путь к файлу в архиве; None — внешняя схема (проверке не подлежит)."""
    if ref.startswith(_EXTERNAL_SCHEMES):
        return None
    clean = ref.split("#", 1)[0]
    if not clean:
        return None
    return posixpath.normpath(posixpath.join(base_dir, clean))


def _ids_of(root: lxml.etree._Element) -> set[str]:
    ids: set[str] = set()
    for element in root.iter():
        if isinstance(element.tag, str):
            element_id = element.get("id")
            if element_id:
                ids.add(element_id)
    return ids


def find_broken_links(archive: zipfile.ZipFile, spine_docs: list[str]) -> list[str]:
    """Битые внутренние ссылки и отсутствующие изображения (E5.7 / QC п.8).

    Проверяет `a`/`link` @href и `img`/`audio`/`video`/`source` @src в
    `spine_docs`: файл на месте, якорь `#id` существует. Внешние схемы
    (http/https/mailto/urn/data) проверке не подлежат. Возвращает список
    проблем поимённо (документ, ссылка).
    """
    names = set(archive.namelist())
    parsed: dict[str, lxml.etree._Element] = {}
    for doc in spine_docs:
        try:
            parsed[doc] = lxml.etree.fromstring(archive.read(doc))
        except Exception:
            continue
    problems: list[str] = []

    def check_ref(ref: str, where: str, kind: str) -> None:
        base_dir = posixpath.dirname(where)
        file_path = _resolve_file(base_dir, ref)
        fragment = ref.split("#", 1)[1] if "#" in ref else None
        target_doc: str | None
        if file_path is None:
            target_doc = where  # только фрагмент в том же документе
        else:
            if file_path not in names:
                problems.append(f"битая {kind}: {where} -> {ref} (файл отсутствует)")
                return
            target_doc = file_path if file_path.endswith(_HTML_EXTS) else None
        if fragment and target_doc is not None:
            if target_doc not in parsed:
                try:
                    parsed[target_doc] = lxml.etree.fromstring(archive.read(target_doc))
                except Exception:
                    problems.append(f"ссылка на неразбираемый документ: {where} -> {ref}")
                    return
            if fragment not in _ids_of(parsed[target_doc]):
                problems.append(f"битая {kind}: {where} -> {ref} (нет якоря #{fragment})")

    for doc, root in parsed.items():
        for element in root.iter():
            if not isinstance(element.tag, str):
                continue
            local = lxml.etree.QName(element.tag).localname
            href = element.get("href")
            if href and local in ("a", "link"):
                check_ref(href, doc, "ссылка")
            src = element.get("src")
            if src and local in ("img", "audio", "video", "source"):
                kind = "изображение" if local == "img" else "ссылка на медиа"
                check_ref(src, doc, kind)
    return problems
