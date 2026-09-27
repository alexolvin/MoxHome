"""V2 (TZ-UI-V §8): поведенческие тесты обложек — V-T8 + V-T9.

V-T8: 3 вредоносные фикстуры (SVG / заявленные 1 ГБ / обрезанный JPEG) →
заглушка (``failed:<reason>``) + WARNING в журнал, исходный файл НЕ
сохраняется, процесс жив (тест доходит до конца).
V-T9: endpoint отдаёт WebP-miniатюру с ``Cache-Control: … immutable``;
обложки нет / failed / нет файла → 404 (UI — заглушка §8.4).

Фикстуры — закоммичены (tests/fixtures/covers/), структура —
test_v0_fixtures.py.
"""

from __future__ import annotations

import base64
import io
import logging
import shutil
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from test_u1_web import _make_u1_storage

from app.services.covers import (
    CoverExtract,
    ensure_thumbnails,
    extract_cover,
    process_cover,
)
from app.web import views
from app.web.app import create_app

FIX = Path(__file__).parent / "fixtures" / "covers"
ROOT = Path(__file__).resolve().parents[1]

_CONTAINER = (
    '<?xml version="1.0"?>'
    '<container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/></rootfiles></container>'
)
_XHTML = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>ch</title></head>'
    "<body><p>hi</p></body></html>"
)


def _opf(with_cover: bool) -> str:
    cover_item = (
        '<item id="cover" href="cover.jpg" media-type="image/jpeg" '
        'properties="cover-image"/>'
        if with_cover
        else ""
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="uid"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="uid">v2</dc:identifier><dc:title>V2</dc:title>'
        f"</metadata><manifest>{cover_item}"
        '<item id="ch1" href="chapter1.xhtml" '
        'media-type="application/xhtml+xml"/></manifest>'
        "<spine><itemref idref=\"ch1\"/></spine></package>"
    )


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (300, 400), (200, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


def _detailed_cover_bytes() -> bytes:
    """«Книжная» обложка с деталью (градиент + растровые точки + полосы):
    сложнее плоского цвета — WebP q80 256w не вписывается в 30 КБ, поэтому
    тест проходит путь адаптивного снижения качества (V-T9)."""
    w, h = 300, 400
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        d.line(
            [(0, y), (w, y)],
            fill=(int(180 - 120 * t), int(40 + 80 * t), int(90 - 60 * t)),
        )
    for y in range(0, h, 6):
        for x in range(0, w, 6):
            r = 2 if ((x // 6 + y // 6) % 2) else 1
            d.ellipse([x, y, x + r, y + r], fill=(255, 240, 220))
    for i in range(5):
        d.rectangle([20, 60 + i * 50, w - 20, 80 + i * 50], fill=(20, 20, 30))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def _make_epub(
    dest: Path, *, with_cover: bool, cover_bytes: bytes | None = None
) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", _CONTAINER)
        z.writestr("OEBPS/content.opf", _opf(with_cover))
        z.writestr("OEBPS/chapter1.xhtml", _XHTML)
        if with_cover:
            z.writestr(
                "OEBPS/cover.jpg",
                cover_bytes if cover_bytes is not None else _jpeg_bytes(),
            )


def _covers_for(epub: Path) -> Path:
    return epub.parent / ".covers"


# --------------------------------------------------------------- V-T8 (unit)


@pytest.mark.parametrize(
    ("fixture", "reason"),
    [
        ("cover-svg.epub", "unsupported-format"),
        ("cover-huge.epub", "size-limit"),
        ("cover-broken.epub", "decode-error"),
    ],
)
def test_v2_t8_malicious_fixture_rejected(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, fixture: str, reason: str
) -> None:
    """Вредоносная обложка → failed:<reason> + WARNING, файл не сохраняется."""
    dest = tmp_path / f"{fixture[:-5]}.epub"
    shutil.copyfile(FIX / fixture, dest)
    with caplog.at_level(logging.WARNING, logger="app.services.covers"):
        result = process_cover(dest, _covers_for(dest))
    assert result.status == f"failed:{reason}", result
    assert result.cover_path is None
    assert result.sha256 is None
    # исходный файл обложки не сохранён рядом с книгой
    assert not any(dest.parent.glob(f"{dest.stem}.cover.*"))
    # WARNING с именем файла и причиной (§8.2)
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "cover_failed" in r.getMessage() and dest.name in r.getMessage()
        for r in warnings
    ), caplog.text


def test_v2_t8_process_survives(tmp_path: Path) -> None:
    """Процесс жив: последовательная обработка всех трёх фикстур не падает."""
    results: list[CoverExtract] = []
    for name in ("cover-svg.epub", "cover-huge.epub", "cover-broken.epub"):
        dest = tmp_path / name
        shutil.copyfile(FIX / name, dest)
        results.append(process_cover(dest, _covers_for(dest)))
    assert all(r.status.startswith("failed:") for r in results)


def test_v2_extract_valid_cover_and_thumbnails(tmp_path: Path) -> None:
    """Корректная JPEG-обложка → extracted + исходник + миниатюры 256/512."""
    dest = tmp_path / "valid.epub"
    _make_epub(dest, with_cover=True)
    covers_dir = _covers_for(dest)
    result = process_cover(dest, covers_dir)
    assert result.status == "extracted", result
    assert result.cover_path is not None
    assert (dest.parent / result.cover_path).is_file()
    assert result.sha256 is not None and len(result.sha256) == 64
    # миниатюры созданы при извлечении (§8.3 «при импорте — сразу»)
    t256 = covers_dir / f"{result.sha256[:16]}-256.webp"
    t512 = covers_dir / f"{result.sha256[:16]}-512.webp"
    assert t256.is_file() and t512.is_file()
    with Image.open(t256) as img:
        assert img.width == 256
    with Image.open(t512) as img:
        assert img.width == 512
    # V-T9: миниатюра 256w ≤ 30 КБ
    assert t256.stat().st_size <= 30 * 1024, t256.stat().st_size


def test_v2_t9_adaptive_quality_keeps_256_under_budget(tmp_path: Path) -> None:
    """V-T9 (адаптивное качество): детальная обложка, которую WebP q80 256w
    не вписывает в 30 КБ, — код снижает качество, и итог ≤ 30 КБ при ширине
    256. (q80 — номинал; жёсткий потолок — 30 КБ, TZ §8.3 + V-T9.)"""
    dest = tmp_path / "detailed.epub"
    _make_epub(dest, with_cover=True, cover_bytes=_detailed_cover_bytes())
    covers_dir = _covers_for(dest)
    result = process_cover(dest, covers_dir)
    assert result.status == "extracted" and result.sha256
    t256 = covers_dir / f"{result.sha256[:16]}-256.webp"
    assert t256.is_file()
    assert t256.stat().st_size <= 30 * 1024, t256.stat().st_size
    with Image.open(t256) as img:
        assert img.width == 256
    # 512w не имеет бюджета TZ — просто создается с правильной шириной
    t512 = covers_dir / f"{result.sha256[:16]}-512.webp"
    with Image.open(t512) as img:
        assert img.width == 512


def test_v2_ensure_thumbnails_lazy(tmp_path: Path) -> None:
    """Ленивое создание: миниатюры генерируются при первом запросе."""
    dest = tmp_path / "valid.epub"
    _make_epub(dest, with_cover=True)
    covers_dir = _covers_for(dest)
    result = process_cover(dest)  # без covers_dir — только исходник
    assert result.status == "extracted" and result.sha256
    source = dest.parent / result.cover_path
    assert not covers_dir.exists()
    ensure_thumbnails(source, result.sha256, covers_dir)
    assert (covers_dir / f"{result.sha256[:16]}-256.webp").is_file()


# --------------------------------------------------------------- V-T9 (HTTP)


def test_v2_t9_endpoint_webp_immutable_and_404(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Endpoint: 200 WebP + immutable; нет обложки / failed → 404."""
    base = tmp_path_factory.mktemp("v2cov")
    settings, db, _cfg, _env = _make_u1_storage(base, "storage_v2", "http://127.0.0.1:9")
    app = create_app(settings, db=db)
    client = TestClient(app)
    try:
        input_dir = settings.input_dir
        # (1) книга с корректной обложкой → 200 WebP + immutable
        good = input_dir / "v2good.epub"
        _make_epub(good, with_cover=True)
        cov = process_cover(good, input_dir / ".covers")
        assert cov.status == "extracted" and cov.sha256
        views.write_book_meta(
            good,
            {
                **views.book_meta_defaults(),
                "cover_path": cov.cover_path,
                "cover": cov.status,
                "cover_sha256": cov.sha256,
            },
        )
        r = client.get(f"/books/{good.name}/cover", params={"w": 256})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("image/webp")
        assert "immutable" in r.headers.get("cache-control", "")
        with Image.open(io.BytesIO(r.content)) as img:
            assert img.width == 256
        # (2) книга без обложки (cover=none) → 404
        none = input_dir / "v2none.epub"
        _make_epub(none, with_cover=False)
        views.write_book_meta(none, {**views.book_meta_defaults(), "cover": "none"})
        assert client.get(f"/books/{none.name}/cover").status_code == 404
        # (3) книга с failed-обложкой → 404
        bad = input_dir / "v2bad.epub"
        shutil.copyfile(FIX / "cover-broken.epub", bad)
        views.write_book_meta(
            bad, {**views.book_meta_defaults(), "cover": "failed:decode-error"}
        )
        assert client.get(f"/books/{bad.name}/cover").status_code == 404
        # (4) неизвестная книга → 404
        assert client.get("/books/does-not-exist.epub/cover").status_code == 404
    finally:
        db.close()


def test_v2_t9_legacy_meta_without_cover_field(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Legacy-мета (до V2: есть cover_path, нет cover/cover_sha256) —
    endpoint по-прежнему отдаёт миниатюру (sha256 по файлу)."""
    base = tmp_path_factory.mktemp("v2legacy")
    settings, db, _cfg, _env = _make_u1_storage(base, "storage_v2l", "http://127.0.0.1:9")
    app = create_app(settings, db=db)
    client = TestClient(app)
    try:
        input_dir = settings.input_dir
        good = input_dir / "v2legacy.epub"
        _make_epub(good, with_cover=True)
        # извлекаем ТОЛЬКО исходник (как R6), мета без cover/cover_sha256
        cover_name = extract_cover(good)
        assert cover_name is not None
        meta = {**views.book_meta_defaults(), "cover_path": cover_name}
        meta.pop("cover")
        meta.pop("cover_sha256")
        views.write_book_meta(good, meta)
        r = client.get(f"/books/{good.name}/cover", params={"w": 512})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("image/webp")
        with Image.open(io.BytesIO(r.content)) as img:
            assert img.width == 512
    finally:
        db.close()


def test_v2_png_1x1_still_rejected_as_source_format_ok(tmp_path: Path) -> None:
    """PNG-обложка допустима (§8.2 allowlist: JPEG/PNG/GIF/WebP)."""
    dest = tmp_path / "pngcover.epub"
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
        "hKmMIQAAAABJRU5ErkJggg=="
    )
    with zipfile.ZipFile(dest, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", _CONTAINER)
        z.writestr(
            "OEBPS/content.opf",
            _opf(with_cover=True).replace(
                'href="cover.jpg" media-type="image/jpeg"',
                'href="cover.png" media-type="image/png"',
            ).replace("cover.jpg", "cover.png"),
        )
        z.writestr("OEBPS/chapter1.xhtml", _XHTML)
        z.writestr("OEBPS/cover.png", png)
    result = process_cover(dest, _covers_for(dest))
    assert result.status == "extracted", result


def test_v2_grid_nojs_renders_with_and_without_cover(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """no-JS-сетка /books рендерится (200): карточки с обложкой (srcset) и
    заглушка §8.4. Регрессия: Jinja2 НЕ передаёт ``loop`` в ``{% include %}``
    — флаг «первые 9 — eager» вычисляется в books.html (``cover_eager``),
    а не в _book_card.html (там ``loop.index0`` → UndefinedError → 500)."""
    base = tmp_path_factory.mktemp("v2grid")
    settings, db, _cfg, _env = _make_u1_storage(base, "storage_v2g", "http://127.0.0.1:9")
    app = create_app(settings, db=db)
    client = TestClient(app)
    try:
        input_dir = settings.input_dir
        for i, has in enumerate((True, False, True)):
            b = input_dir / f"v2grid{i}.epub"
            _make_epub(b, with_cover=has)
            meta = {**views.book_meta_defaults()}
            if has:
                cov = process_cover(b, input_dir / ".covers")
                assert cov.status == "extracted" and cov.sha256
                meta.update(
                    cover_path=cov.cover_path, cover=cov.status, cover_sha256=cov.sha256
                )
            else:
                meta["cover"] = "none"
            views.write_book_meta(b, meta)
        r = client.get("/books")
        assert r.status_code == 200, r.text
        assert "u3-book-grid" in r.text
        assert r.text.count("u3-book-cover-img") == 2
        assert "u3-book-ph" in r.text
        assert 'loading="eager"' in r.text  # 3 позиции ≤ 9 → eager
    finally:
        db.close()
