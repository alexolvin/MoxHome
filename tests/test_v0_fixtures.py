"""V0 (TZ-UI-V §11): фикстуры вредных обложек — структура.

Поведенческие тесты (заглушка + WARNING, процесс жив) — в V2 (V-T8);
тут — только структурная честность закоммиченных фикстур, stdlib-only
(Pillow в проект входит с V2, TZ §8.3).
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "covers"
HUGE_SIZE = 1024 * 1024 * 1024


def _read_opf(epub: Path) -> str:
    with zipfile.ZipFile(epub) as z:
        container = z.read("META-INF/container.xml").decode("utf-8")
        match = re.search(r'full-path="([^"]+)"', container)
        assert match is not None
        return z.read(match.group(1)).decode("utf-8")


def test_all_three_fixtures_are_epub_zips() -> None:
    for name in ("cover-svg.epub", "cover-huge.epub", "cover-broken.epub"):
        path = FIX / name
        assert path.is_file(), f"нет фикстуры {name}"
        with zipfile.ZipFile(path) as z:
            assert z.namelist()[0] == "mimetype"
            assert z.read("mimetype") == b"application/epub+zip"
            assert "OEBPS/content.opf" in z.namelist()


def test_svg_cover_declared_as_cover_image() -> None:
    opf = _read_opf(FIX / "cover-svg.epub")
    assert 'media-type="image/svg+xml"' in opf
    assert 'properties="cover-image"' in opf
    assert "cover.svg" in opf


def test_huge_cover_declared_one_gib() -> None:
    with zipfile.ZipFile(FIX / "cover-huge.epub") as z:
        info = z.getinfo("OEBPS/cover.jpg")
        # Объявленный размер — 1 ГБ (модель вредного архива: проверка
        # ZipInfo.file_size ДО чтения данных, TZ §8.2).
        assert info.file_size == HUGE_SIZE
        # Не читаем данные: при заявленных 1 ГБ чтение упадёт
        # (BadZipFile) — ровно тот отказ, который обязан случиться
        # ДО декодирования в V2.


def test_broken_jpeg_structurally_truncated() -> None:
    with zipfile.ZipFile(FIX / "cover-broken.epub") as z:
        opf = z.read("OEBPS/content.opf").decode("utf-8")
        assert 'media-type="image/jpeg"' in opf
        assert 'properties="cover-image"' in opf
        data = z.read("OEBPS/cover.jpg")
        # JPEG: SOI в начале, EOI (FFD9) в конце. У фикстуры EOI потерян
        # (обрезка) — декодер обязан упасть.
        assert data.startswith(b"\xff\xd8")
        assert not data.endswith(b"\xff\xd9")
