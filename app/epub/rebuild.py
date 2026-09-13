"""Пересборка выходного EPUB с точечными патчами (L3.5).

Блок патчится на месте по позиционному XPath из карты выравнивания E6
(адресация — `aligner.AlignedPair.translation_xpath`, D31). Архив
пересобирается в `.partial`: `mimetype` — первой записью без сжатия (E5.7),
остальные записи — побайтово как в оригинале, в том же порядке. Публикация
(`.partial` → валидация → `os.replace`) — отдельный шаг
`app/adapters/epub_engine/atomic.py` (единый механизм, Rule 8).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import lxml.etree

from app.core.errors import AppError
from app.epub import aligner, segmenter


def block_bytes(doc_bytes: bytes, xpath: str) -> bytes:
    """XML блока по позиционному XPath (без tail — он принадлежит соседу)."""
    root = lxml.etree.fromstring(doc_bytes)
    element = aligner.resolve_positional_xpath(root, xpath)
    if element is None:
        raise AppError(f"блок не найден по XPath из карты выравнивания: {xpath}")
    return lxml.etree.tostring(element, encoding="unicode").encode("utf-8")


def patch_block(doc_bytes: bytes, xpath: str, new_block: bytes) -> bytes:
    """Меняет блок по позиционному XPath `xpath` на `new_block`.

    `new_block` — полный XML ОДНОГО блока; корневой тег обязан совпадать с
    тегом заменяемого (иначе — сбой, fail closed). Tail после блока
    сохраняется (принадлежит следующему за ним контенту).
    """
    root = lxml.etree.fromstring(doc_bytes)
    element = aligner.resolve_positional_xpath(root, xpath)
    if element is None:
        raise AppError(f"блок не найден по XPath из карты выравнивания: {xpath}")
    new_root = lxml.etree.fromstring(new_block)
    if segmenter.localname(new_root) != segmenter.localname(element):
        raise AppError(
            "тег блока изменился: "
            f"{segmenter.localname(element)} -> {segmenter.localname(new_root)}"
        )
    parent = element.getparent()
    if parent is None:
        raise AppError("патч корня документа запрещён: блок должен быть внутри body")
    new_root.tail = element.tail
    # lxml._Element.replace существует в рантайме, но отсутствует в stubs.
    parent.replace(element, new_root)  # type: ignore[attr-defined]
    return lxml.etree.tostring(root, encoding="unicode").encode()


def rebuild_epub(target: Path, patches: dict[str, bytes], partial: Path) -> None:
    """Пересобирает архив: документы из `patches` (путь в архиве → новый
    контент), всё остальное — побайтово как в `target`, в исходном порядке.

    `mimetype` — первая запись без сжатия (E5.7). Старый `.partial` — не
    результат; новая попытка с нуля (как в движке, E5.6).
    """
    if partial.exists():
        partial.unlink()
    with zipfile.ZipFile(target) as src, zipfile.ZipFile(partial, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename in patches:
                data = patches[info.filename]
            out_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            out_info.compress_type = (
                zipfile.ZIP_STORED if info.filename == "mimetype" else info.compress_type
            )
            out_info.external_attr = info.external_attr
            dst.writestr(out_info, data)
