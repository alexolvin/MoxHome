"""Серия R, R6 (ТЗ §8): импорт — 16 форматов, ebook-convert в контейнере.

Т-подложки:
- T8 (unit): 4 отказа — DRM-EPUB, скан-PDF, CBZ, размер — 400 + ТОЧНАЯ
  причина, файл не остаётся (fail closed); дополнительно — ZIP нескольких
  книг, ZIP без книги, аудио, неподдерживаемый формат, конвертер
  недоступен (не-EPUB отклоняется, EPUB при этом проходит);
- EPUB — синхронный путь: пре-проверка → EPUBCheck (факты в ``.meta.json``)
  → язык → 200; обложка — извлечение (EPUB3 ``cover-image``);
- не-EPUB — 202 ``{import_id}`` → фоновая конвертация → polling
  ``GET /api/imports/{id}`` → ``done``: ``source_format``, оригинал
  ``.source.<ext>`` хранится, книга — первой строкой списка;
  конвертация вызывается с ``--epub-version 3 --language und`` (r6_26);
- ``/api/health`` — поле ``converter`` (ok / binary отсутствует /
  бинарь не запускается);
- no-JS: страница ``/books/upload`` (форма) + ``/imports/{id}``
  (active/done/error/missing);
- удаление книги — вместе с ``.source``/``.cover`` sidecar;
- T17-гвард: 0 кириллицы в app.js, прошивка лист-импорта.

Конвертер — stub-скрипт (handoff R6 решение 14): ``--version`` →
``stub 1.0``; ``IN OUT`` → копия ``tests/fixtures/epub/probe.en.epub``
(EPUBCheck 0/0); вариант с задержкой — ловить промежуточные статусы;
``fail`` в имени входа — exit 3 (состояние ``error``).
Порядок тестов значим: профиль r6 общий, список книг накопительный.
"""

from __future__ import annotations

import base64
import json
import re
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_u1_web import AUTH_SECRET, U1Env, _auth_header, _make_u1_storage

from app.adapters.convert import reset_probe_cache
from app.services.import_book import sweep_orphans
from app.web import views
from app.web.auth import csrf_token
from app.web.strings import UI_STRINGS

S = UI_STRINGS

ROOT = Path(__file__).resolve().parents[1]
PROBE_EPUB = ROOT / "tests" / "fixtures" / "epub" / "probe.en.epub"
#: 1×1 PNG (детерминированные байты, как в tests/fixtures/epub/build.py).
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# ------------------------------------------------------------------ фикстуры


def _write_stub(out: Path, *, delay: float = 0.0, log: Path | None = None) -> Path:
    """Stub-конвертер (решение R6 №14): ``--version`` / ``IN → OUT``;
    ``log`` — дописать аргументы каждого вызова конвертации (r6_26)."""
    body = (
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "--version" ]; then echo "stub 1.0"; exit 0; fi\n'
        'case "$1" in *fail*) echo "stub: forced failure" >&2; exit 3;; esac\n'
    )
    if log is not None:
        body += f'echo "$*" >> "{log}"\n'
    if delay:
        body += f"sleep {delay}\n"
    body += f'cp "{PROBE_EPUB}" "$2"\nexit 0\n'
    out.write_text(body, encoding="utf-8")
    out.chmod(0o755)
    return out


@pytest.fixture(scope="module")
def r6_env(u1_env: U1Env) -> U1Env:
    """Профили R6 поверх общего u1_env:
    - ``r6`` — stub-конвертер (16 форматов);
    - ``r6delay`` — stub с задержкой 0,5 с (промежуточные статусы);
    - ``r6big`` — stub + ``limits.upload_max_mb: 1`` (отказ по размеру);
    - ``r6bad`` / ``r6badexec`` — health: binary отсутствует / не запускается;
    - ``main`` (u1_env) — конвертер не задан: только EPUB.
    """
    base = u1_env.base
    stubs = base / "r6_stubs"
    stubs.mkdir(exist_ok=True)
    stub = _write_stub(stubs / "stub")
    stub_delay = _write_stub(stubs / "stub_delay", delay=0.5)
    stub_log = _write_stub(stubs / "stub_log", log=stubs / "stub.log")
    badexec = stubs / "badexec"
    badexec.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    # без chmod: бинарь не запускается (PermissionError)
    badexec.chmod(0o644)
    for name, directory, extra in (
        ("r6", "storage_r6", f"converter:\n  binary: {stub}\n"),
        ("r6delay", "storage_r6delay", f"converter:\n  binary: {stub_delay}\n"),
        (
            "r6big",
            "storage_r6big",
            f"limits:\n  upload_max_mb: 1\nconverter:\n  binary: {stub}\n",
        ),
        (
            "r6bad",
            "storage_r6bad",
            f"converter:\n  binary: {stubs / 'missing'}\n",
        ),
        ("r6badexec", "storage_r6badexec", f"converter:\n  binary: {badexec}\n"),
        ("r6log", "storage_r6log", f"converter:\n  binary: {stub_log}\n"),
    ):
        # U3 3.9: без SERVER_AUTH_SECRET мутации — 403, поэтому у каждого
        # профиля секрет, а клиент шлёт Basic + CSRF (паттерн main).
        settings, db, cfg, env_file = _make_u1_storage(
            base,
            directory,
            u1_env.server.url,
            env_extra=f"SERVER_AUTH_SECRET={AUTH_SECRET}\n",
            yaml_extra=extra,
        )
        u1_env.add_profile(
            name,
            settings,
            db,
            cfg,
            env_file,
            client_headers={
                **_auth_header(),
                "X-U3-CSRF": csrf_token(settings, "any-user"),
            },
        )
    reset_probe_cache()
    return u1_env


@pytest.fixture(autouse=True)
def _probe_hermetic():
    """Кэш пробы конвертера — глобальный; тесты меняют binary между
    профилями (reset до и после каждого теста)."""
    reset_probe_cache()
    yield
    reset_probe_cache()


# ------------------------------------------------------------------ утилиты


def _post_file(
    client: TestClient,
    path: Path,
    *,
    name: str | None = None,
    accept: str = "application/json",
) -> TestClient.Response:
    with path.open("rb") as fh:
        return client.post(
            "/api/books",
            files={"file": (name or path.name, fh)},
            headers={"Accept": accept},
            follow_redirects=False,
        )


def _wait_import(
    client: TestClient,
    import_id: str,
    timeout: float = 60.0,
    final: tuple[str, ...] = ("done", "error"),
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    rec: dict[str, object] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/imports/{import_id}")
        assert response.status_code == 200, response.text
        rec = response.json()
        if rec["status"] in final:
            return rec
        time.sleep(0.1)
    pytest.fail(f"импорт не завершился за {timeout:.0f} с: {rec}")


def _read_meta(input_dir: Path, book: str) -> dict[str, object]:
    return json.loads(
        (input_dir / book.replace(".epub", ".epub.meta.json")).read_text(
            encoding="utf-8"
        )
    )


def _build_cover_epub(out: Path) -> None:
    """EPUB3 с обложкой: manifest-item ``properties="cover-image"``."""
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="cover-id">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="cover-id">'
        "urn:uuid:99999999-1111-2222-3333-444444444444</dc:identifier>"
        "<dc:title>Cover Book</dc:title><dc:creator>Cover Author</dc:creator>"
        "<dc:language>en</dc:language><dc:date>2026-01-01</dc:date>"
        '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>'
        "  </metadata>\n"
        "  <manifest>"
        '<item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
        'properties="nav"/>'
        '<item id="cover" href="cover.png" media-type="image/png" '
        'properties="cover-image"/>'
        "  </manifest>\n"
        "  <spine><itemref idref=\"ch1\"/></spine>\n"
        "</package>\n"
    )
    chapter = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en">\n'
        "<head><title>Chapter One</title></head>\n"
        "<body>\n<h1>Chapter One</h1>\n"
        "<p>The quick brown fox jumps over the lazy dog near the river.</p>\n"
        "<p>She visited the old mill on a cold morning.</p>\n"
        "</body>\n</html>\n"
    )
    nav = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">\n'
        "<head><title>Contents</title></head>\n"
        '<body>\n<nav epub:type="toc" id="toc">\n<h1>Contents</h1>\n<ol>\n'
        '<li><a href="chapter1.xhtml">Chapter One</a></li>\n</ol>\n</nav>\n'
        "</body>\n</html>\n"
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
    files: dict[str, str | bytes] = {
        "META-INF/container.xml": container,
        "OEBPS/content.opf": opf,
        "OEBPS/chapter1.xhtml": chapter,
        "OEBPS/nav.xhtml": nav,
        "OEBPS/cover.png": PNG_1X1,
    }
    with zipfile.ZipFile(out, "w") as archive:
        info = zipfile.ZipInfo("mimetype", date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, "application/epub+zip")
        for file_name, content in files.items():
            info = zipfile.ZipInfo(file_name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)


def _zip_of(out: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(out, "w") as archive:
        for file_name, content in entries.items():
            archive.writestr(file_name, content)


def _scan_pdf(out: Path) -> None:
    """PDF БЕЗ текстового слоя: content-стрим без операторов Tj/TJ/'."""
    out.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << >> >>\nendobj\n"
        b"4 0 obj\n<< /Length 17 >>\nstream\nq\n1 0 0 1 0 0 cm\nQ\nendstream\n"
        b"endobj\ntrailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
    )


# ------------------------------------------------------------------ EPUB sync


def test_r6_1_native_epub_sync(r6_env: U1Env, tmp_path: Path) -> None:
    """EPUB — синхронный путь: 200 + meta (source_format=epub,
    факты EPUBCheck, detected) + файл и sidecar в библиотеке."""
    profile = r6_env.profiles["r6"]
    source = tmp_path / "r6_native.epub"
    _build_cover_epub(source)  # валидная EPUB3 (обложка здесь не важна)
    response = _post_file(profile.client, source)
    assert response.status_code == 200, response.text
    name = str(response.json()["name"])
    input_dir = profile.settings.input_dir
    assert (input_dir / name).is_file()
    meta = _read_meta(input_dir, name)
    assert meta["source_format"] == "epub"
    assert meta["detected"] == "en"
    assert meta["title"] == "Cover Book"
    assert meta["imported_at"]
    facts = meta["epubcheck"]
    assert isinstance(facts, dict)
    assert facts["available"] is True
    assert facts["errors"] == 0
    assert facts["version"]
    # книга видна в списке
    books = profile.client.get("/api/books").json()
    assert books[0]["name"] == name
    assert books[0]["source_format"] == "epub"


def test_r6_2_epub_cover_extracted(r6_env: U1Env, tmp_path: Path) -> None:
    """Обложка: EPUB3 ``cover-image`` → ``<name>.cover.png`` рядом с
    книгой + ``cover_path`` в meta + GET /api/books/covers (200)."""
    profile = r6_env.profiles["r6"]
    source = tmp_path / "r6_cover.epub"
    _build_cover_epub(source)
    response = _post_file(profile.client, source)
    assert response.status_code == 200, response.text
    name = str(response.json()["name"])
    input_dir = profile.settings.input_dir
    meta = _read_meta(input_dir, name)
    cover_name = str(meta["cover_path"])
    assert cover_name.startswith(name[: -len(".epub")] + ".cover.")
    assert (input_dir / cover_name).is_file()
    cover = profile.client.get(f"/api/books/covers/{name}")
    assert cover.status_code == 200
    assert cover.headers["content-type"].startswith("image/png")
    assert cover.content == PNG_1X1


def test_r6_3_epub_drm_rejected(r6_env: U1Env) -> None:
    """T8: DRM-EPUB (META-INF/encryption.xml) — 400 «файл защищён DRM»,
    файл не остаётся в библиотеке."""
    profile = r6_env.profiles["r6"]
    response = _post_file(profile.client, ROOT / "tests/fixtures/epub/drm.epub")
    assert response.status_code == 400
    assert response.json()["detail"] == S["err_drm"]
    assert list(profile.settings.input_dir.glob("u1-*-drm.epub")) == []


def test_r6_4_epub_broken_rejected(r6_env: U1Env) -> None:
    """EPUB с ошибкой EPUBCheck — 400 «EPUBCheck: N ошибок»,
    файл не остаётся (fail closed)."""
    profile = r6_env.profiles["r6"]
    response = _post_file(profile.client, ROOT / "tests/fixtures/epub/broken.epub")
    assert response.status_code == 400
    detail = response.json()["detail"]
    # broken.epub: 2 fatals / 3 errors (замер EPUBCheck 5.3.0)
    assert detail.startswith("EPUBCheck: 5 ошибок")
    assert list(profile.settings.input_dir.glob("u1-*-broken.epub")) == []


# ------------------------------------------------------------------ импорт


def test_r6_5_non_epub_import_done(r6_env: U1Env, tmp_path: Path) -> None:
    """Не-EPUB: 202 {import_id} → polling → done; source_format=txt;
    оригинал .source.txt хранится; книга — первой строкой."""
    profile = r6_env.profiles["r6"]
    source = tmp_path / "r6_novel.txt"
    source.write_text(
        "The quick brown fox jumps over the lazy dog near the river. "
        "She visited the old mill on a cold morning.\n" * 8,
        encoding="utf-8",
    )
    response = _post_file(profile.client, source)
    assert response.status_code == 202, response.text
    payload = response.json()
    import_id = str(payload["import_id"])
    name = str(payload["name"])
    assert name.endswith(".epub")
    stem = name[: -len(".epub")]
    rec = _wait_import(profile.client, import_id)
    assert rec["status"] == "done"
    assert rec["name"] == name
    assert rec["format"] == "txt"
    assert rec["error"] is None
    input_dir = profile.settings.input_dir
    assert (input_dir / name).is_file()
    assert (input_dir / f"{stem}.source.txt").is_file()  # оригинал хранится
    meta = _read_meta(input_dir, name)
    assert meta["source_format"] == "txt"
    assert meta["detected"] == "en"
    assert meta["epubcheck"]["errors"] == 0
    books = profile.client.get("/api/books").json()
    assert books[0]["name"] == name  # первая строка (ТЗ §8)
    assert books[0]["source_format"] == "txt"


def test_r6_26_convert_invokes_epub3(r6_env: U1Env, tmp_path: Path) -> None:
    """Регрессия: конвертация вызывается с ``--epub-version 3
    --language und`` — умолчание calibre (EPUB2, class="calibre" на
    <html>; locale "C" в OPF) невалидно для EPUBCheck (поправка R6,
    замер 2026-09-22). ВАЖНО: не ``--version`` — глобальный флаг
    calibre (баннер, rc=0, файл не создаётся)."""
    profile = r6_env.profiles["r6log"]
    source = tmp_path / "r6_epub3.txt"
    source.write_text("The mill stood still that year.\n" * 8, encoding="utf-8")
    response = _post_file(profile.client, source)
    assert response.status_code == 202, response.text
    rec = _wait_import(profile.client, str(response.json()["import_id"]))
    assert rec["status"] == "done"
    log = r6_env.base / "r6_stubs" / "stub.log"
    assert log.is_file()
    lines = [line.rstrip() for line in log.read_text(encoding="utf-8").splitlines()]
    assert any(
        line.endswith(" --epub-version 3 --language und") for line in lines
    ), lines


def test_r6_6_import_intermediate_status(r6_env: U1Env, tmp_path: Path) -> None:
    """Stub с задержкой: первый запрос — промежуточный статус
    (checking/converting), затем done."""
    profile = r6_env.profiles["r6delay"]
    source = tmp_path / "r6_note.txt"
    source.write_text("The river was cold that year.\n" * 8, encoding="utf-8")
    response = _post_file(profile.client, source)
    assert response.status_code == 202, response.text
    import_id = str(response.json()["import_id"])
    first = profile.client.get(f"/api/imports/{import_id}").json()
    assert first["status"] in ("checking", "converting")
    assert first["format"] == "txt"
    assert first["name"] is None
    rec = _wait_import(profile.client, import_id)
    assert rec["status"] == "done"


def test_r6_7_import_error_state(r6_env: U1Env, tmp_path: Path) -> None:
    """Ошибка конвертации: status=error + причина (хвост stderr),
    созданные файлы удалены."""
    profile = r6_env.profiles["r6delay"]
    source = tmp_path / "r6_fail.txt"
    source.write_text("fail trigger\n", encoding="utf-8")
    response = _post_file(profile.client, source)
    assert response.status_code == 202, response.text
    import_id = str(response.json()["import_id"])
    rec = _wait_import(profile.client, import_id)
    assert rec["status"] == "error"
    assert rec["error"] == S["err_convert_failed"].format(
        reason="stub: forced failure"
    )
    input_dir = profile.settings.input_dir
    assert list(input_dir.glob("u1-*-r6_fail.txt*")) == []


def test_r6_8_reject_scan_pdf(r6_env: U1Env, tmp_path: Path) -> None:
    """T8: скан-PDF (нет текстовых операторов) — 400, файл не остаётся."""
    profile = r6_env.profiles["r6"]
    source = tmp_path / "r6_scan.pdf"
    _scan_pdf(source)
    response = _post_file(profile.client, source)
    assert response.status_code == 400
    assert response.json()["detail"] == S["err_scan_pdf"]
    assert list(
        profile.settings.input_dir.glob("u1-*-r6_scan.pdf.source.pdf")
    ) == []


def test_r6_9_reject_cbz(r6_env: U1Env, tmp_path: Path) -> None:
    """T8: комикс .cbz — 400, файл не остаётся."""
    profile = r6_env.profiles["r6"]
    source = tmp_path / "r6_comics.cbz"
    _zip_of(source, {"page1.png": PNG_1X1, "page2.png": PNG_1X1})
    response = _post_file(profile.client, source)
    assert response.status_code == 400
    assert response.json()["detail"] == S["err_comics"]
    assert list(
        profile.settings.input_dir.glob("u1-*-r6_comics.cbz.source.cbz")
    ) == []


def test_r6_10_reject_zip_multi(r6_env: U1Env, tmp_path: Path) -> None:
    """ZIP нескольких книг — 400 «ZIP-архив нескольких книг»."""
    profile = r6_env.profiles["r6"]
    source = tmp_path / "r6_multi.zip"
    _zip_of(source, {"a.txt": b"one", "b.html": b"<html></html>"})
    response = _post_file(profile.client, source)
    assert response.status_code == 400
    assert response.json()["detail"] == S["err_zip_multi"]


def test_r6_11_reject_zip_no_book(r6_env: U1Env, tmp_path: Path) -> None:
    """ZIP без книги (и без изображений) — 400 «ZIP не содержит книгу»."""
    profile = r6_env.profiles["r6"]
    source = tmp_path / "r6_zipnobook.zip"
    _zip_of(source, {"data.bin": b"\x00\x01"})
    response = _post_file(profile.client, source)
    assert response.status_code == 400
    assert response.json()["detail"] == S["err_zip_no_book"]


def test_r6_12_reject_size(r6_env: U1Env, tmp_path: Path) -> None:
    """T8: файл больше limits.upload_max_mb (1 МБ в профиле) — 400
    с точной причиной ДО сохранения (файл не создаётся)."""
    profile = r6_env.profiles["r6big"]
    source = tmp_path / "r6_big.txt"
    source.write_bytes(b"x" * (2 * 1024 * 1024))
    response = _post_file(profile.client, source)
    assert response.status_code == 400
    assert response.json()["detail"] == S["err_upload_too_big"].format(mb=1)
    assert list(profile.settings.input_dir.glob("u1-*")) == []


def test_r6_13_reject_audiobook(r6_env: U1Env, tmp_path: Path) -> None:
    """Аудио-сигнатура (MP3) внутри допустимого расширения — 400."""
    profile = r6_env.profiles["r6"]
    source = tmp_path / "r6_audio.pdb"
    source.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 64)
    response = _post_file(profile.client, source)
    assert response.status_code == 400
    assert response.json()["detail"] == S["err_audiobook"]


def test_r6_14_reject_unsupported_and_no_ext(r6_env: U1Env, tmp_path: Path) -> None:
    """Неподдерживаемый формат — 400 со списком форматов; имя без
    расширения — 400 (ext пусто)."""
    profile = r6_env.profiles["r6"]
    exe = tmp_path / "r6_bad.exe"
    exe.write_bytes(b"MZ")
    response = _post_file(profile.client, exe)
    assert response.status_code == 400
    formats = ", ".join("." + f for f in profile.settings.converter.accept)
    assert response.json()["detail"] == S["err_format_unsupported"].format(
        ext="exe", formats=formats
    )
    noext = tmp_path / "r6noext"
    noext.write_bytes(b"no extension")
    response = _post_file(profile.client, noext)
    assert response.status_code == 400
    assert response.json()["detail"] == S["err_format_unsupported"].format(
        ext="", formats=formats
    )


def test_r6_15_converter_unavailable(r6_env: U1Env, tmp_path: Path) -> None:
    """Без calibre (main: binary не задан) — не-EPUB 400, EPUB проходит."""
    profile = r6_env.profiles["main"]
    tmp = tmp_path / "r6_note.txt"
    tmp.write_text("note\n", encoding="utf-8")
    response = _post_file(profile.client, tmp)
    assert response.status_code == 400
    assert response.json()["detail"] == S["err_converter_unavailable"]
    assert list(profile.settings.input_dir.glob("u1-*-r6_note.txt*")) == []
    # EPUB при этом принимается (конвертация не нужна)
    response = _post_file(profile.client, r6_env.books["tiny"])
    assert response.status_code == 200, response.text


def test_r6_16_import_not_found(r6_env: U1Env) -> None:
    """Неизвестный import_id (в т.ч. после рестарта) — 404."""
    profile = r6_env.profiles["r6"]
    response = profile.client.get("/api/imports/no-such-id")
    assert response.status_code == 404
    assert response.json()["detail"] == S["err_import_not_found"].format(
        id="no-such-id"
    )


def test_r6_17_health_converter(r6_env: U1Env) -> None:
    """/api/health — поле converter: ok (stub) / error (binary отсутствует,
    binary не запускается) + detail. Кэш пробы глобальный — сброс между
    профилями (у каждого свой binary)."""
    ok = r6_env.profiles["r6"].client.get("/api/health").json()
    assert ok["converter"] == "ok"
    reset_probe_cache()
    missing = r6_env.profiles["r6bad"].client.get("/api/health").json()
    assert missing["converter"] == "error"
    assert "detail" in missing and "converter" in missing["detail"]
    reset_probe_cache()
    badexec = r6_env.profiles["r6badexec"].client.get("/api/health").json()
    assert badexec["converter"] == "error"
    assert "converter" in badexec["detail"]
    # main: binary не задан
    reset_probe_cache()
    plain = r6_env.profiles["main"].client.get("/api/health").json()
    assert plain["converter"] == "error"


def test_r6_18_cover_404s(r6_env: U1Env, tmp_path: Path) -> None:
    """GET /api/books/covers: 404 (книги нет / обложки нет); traversal —
    ``book_cover_path`` → None."""
    profile = r6_env.profiles["r6"]
    assert profile.client.get("/api/books/covers/nope.epub").status_code == 404
    # книга С обложкой — 200
    source = tmp_path / "r6_cov.epub"
    _build_cover_epub(source)
    response = _post_file(profile.client, source)
    assert response.status_code == 200, response.text
    name = str(response.json()["name"])
    assert profile.client.get(f"/api/books/covers/{name}").status_code == 200
    # книга без обложки — 404
    simple = tmp_path / "r6_simple.epub"
    _build_simple_epub(simple)
    response = _post_file(profile.client, simple)
    assert response.status_code == 200, response.text
    simple_name = str(response.json()["name"])
    assert (
        profile.client.get(f"/api/books/covers/{simple_name}").status_code == 404
    )
    settings = profile.settings
    assert views.book_cover_path(settings, "../secret.epub") is None
    assert views.book_cover_path(settings, "nope.epub") is None
    assert views.book_cover_path(settings, simple_name) is None


def _build_simple_epub(out: Path) -> None:
    """EPUB3 без обложки (та же механика, что _build_cover_epub)."""
    source = out.with_suffix(".withcover.tmp")
    _build_cover_epub(source)
    with zipfile.ZipFile(source, "r") as archive:
        names = [n for n in archive.namelist() if n != "OEBPS/cover.png"]
        data = {n: archive.read(n) for n in names}
    source.unlink()
    # вырезаем cover-item из OPF
    opf = data["OEBPS/content.opf"].decode("utf-8")
    opf = opf.replace(
        '<item id="cover" href="cover.png" media-type="image/png" '
        'properties="cover-image"/>',
        "",
    )
    data["OEBPS/content.opf"] = opf.encode("utf-8")
    with zipfile.ZipFile(out, "w") as archive:
        for file_name, content in data.items():
            archive.writestr(file_name, content)


def test_r6_19_delete_removes_sidecars(r6_env: U1Env, tmp_path: Path) -> None:
    """Удаление книги — вместе с .source и .cover sidecar."""
    profile = r6_env.profiles["r6"]
    source = tmp_path / "r6_delete.txt"
    source.write_text("delete me\n", encoding="utf-8")
    response = _post_file(profile.client, source)
    assert response.status_code == 202, response.text
    name = str(response.json()["name"])
    _wait_import(profile.client, str(response.json()["import_id"]))
    input_dir = profile.settings.input_dir
    stem = name[: -len(".epub")]
    cover = input_dir / f"{stem}.cover.jpg"
    cover.write_bytes(b"\xff\xd8")
    response = profile.client.delete(f"/api/books/{name}")
    assert response.status_code == 200, response.text
    assert not (input_dir / name).exists()
    assert not (input_dir / f"{stem}.source.txt").exists()
    assert not (input_dir / f"{stem}.cover.jpg").exists()
    assert not (input_dir / f"{stem}.epub.meta.json").exists()


def test_r6_20_wants_html_303(r6_env: U1Env, tmp_path: Path) -> None:
    """no-JS (Accept: text/html): .epub → 303 /books; не-EPUB →
    303 /imports/{id}."""
    profile = r6_env.profiles["r6"]
    source = tmp_path / "r6_html.epub"
    _build_simple_epub(source)
    response = _post_file(profile.client, source, accept="text/html")
    assert response.status_code == 303
    assert response.headers["location"] == "/books"
    txt = tmp_path / "r6_html.txt"
    txt.write_text("text\n", encoding="utf-8")
    response = _post_file(profile.client, txt, accept="text/html")
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/imports/")
    rec = _wait_import(profile.client, location[len("/imports/") :])
    assert rec["status"] == "done"


def test_r6_21_newest_first(r6_env: U1Env, tmp_path: Path) -> None:
    """Порядок списка: сначала новые — только что импортированные
    книги первыми строками (в порядке импорта)."""
    profile = r6_env.profiles["r6"]
    names = []
    for title in ("r6_first.txt", "r6_second.txt"):
        source = tmp_path / title
        source.write_text("The fox and the river.\n" * 8, encoding="utf-8")
        response = _post_file(profile.client, source)
        assert response.status_code == 202, response.text
        names.append(str(response.json()["name"]))
        _wait_import(profile.client, str(response.json()["import_id"]))
    books = profile.client.get("/api/books").json()
    assert books[0]["name"] == names[1]
    assert books[1]["name"] == names[0]


def test_r6_22_sweep_orphans(r6_env: U1Env) -> None:
    """sweep_orphans: осиротевший .source без .epub — удалён, пара —
    сохранена."""
    profile = r6_env.profiles["r6big"]
    input_dir = profile.settings.input_dir
    orphan = input_dir / "u1-orphan-0000.source.txt"
    paired_src = input_dir / "u1-paired-0000.source.txt"
    paired_epub = input_dir / "u1-paired-0000.epub"
    orphan.write_bytes(b"orphan")
    paired_src.write_bytes(b"paired")
    paired_epub.write_bytes(b"PK\x03\x04")
    try:
        removed = sweep_orphans(profile.settings)
        assert removed == 1
        assert not orphan.exists()
        assert paired_src.exists()
        assert paired_epub.exists()
    finally:
        for path in (paired_src, paired_epub):
            path.unlink(missing_ok=True)


# ------------------------------------------------------------------ no-JS


def test_r6_23_nojs_upload_page(r6_env: U1Env) -> None:
    """GET /books/upload: форма POST /api/books; accept по конвертеру
    (16 форматов); без конвертера — строка «только EPUB» + accept .epub."""
    page = r6_env.profiles["r6"].client.get("/books/upload")
    assert page.status_code == 200
    assert 'action="/api/books"' in page.text
    assert 'name="file"' in page.text
    formats = r6_env.profiles["r6"].settings.converter.accept
    accept = ",".join("." + fmt for fmt in formats)
    assert f'accept="{accept}"' in page.text
    assert S["import_sheet_title"] in page.text
    # кэш пробы глобальный: сброс, чтобы main не получил «ok» от stub
    reset_probe_cache()
    plain = r6_env.profiles["main"].client.get("/books/upload")
    assert plain.status_code == 200
    assert 'accept=".epub"' in plain.text
    assert S["import_converter_unavailable"] in plain.text


def test_r6_24_nojs_import_status_pages(r6_env: U1Env, tmp_path: Path) -> None:
    """GET /imports/{id}: active (meta-refresh) → done (ссылка на
    карточку); error — причина; неизвестный id — 404."""
    profile = r6_env.profiles["r6delay"]
    source = tmp_path / "r6_nojs.txt"
    source.write_text("The mill stood still.\n" * 8, encoding="utf-8")
    response = _post_file(profile.client, source, accept="text/html")
    assert response.status_code == 303
    import_id = response.headers["location"][len("/imports/") :]
    page = profile.client.get(f"/imports/{import_id}")
    assert page.status_code == 200
    assert 'http-equiv="refresh"' in page.text
    assert S["import_status_active"] in page.text
    rec = _wait_import(profile.client, import_id)
    assert rec["status"] == "done"
    page = profile.client.get(f"/imports/{import_id}")
    assert page.status_code == 200
    assert S["import_status_done_line"].format(name=str(rec["name"])) in page.text
    # error: stub-сбой
    bad = tmp_path / "r6_nojs_fail.txt"
    bad.write_text("fail\n", encoding="utf-8")
    response = _post_file(profile.client, bad, accept="text/html")
    assert response.status_code == 303
    bad_id = response.headers["location"][len("/imports/") :]
    bad_rec = _wait_import(profile.client, bad_id)
    assert bad_rec["status"] == "error"
    page = profile.client.get(f"/imports/{bad_id}")
    assert page.status_code == 200
    assert S["import_status_error_line"].format(
        reason=str(bad_rec["error"])
    ) in page.text
    assert "stub: forced failure" in page.text
    # неизвестный id — 404
    missing = profile.client.get("/imports/unknown-import")
    assert missing.status_code == 404
    assert S["err_import_not_found"].format(id="unknown-import") in missing.text


# ------------------------------------------------------------------ T17 / JS


def test_r6_25_t17_guard_and_js_wiring() -> None:
    """T17-гвард (R6): 0 кириллицы в app.js; прошивка лист-импорта
    (drop-зона, очередь, polling, тост с действием) и шаблонные строки."""
    root = Path(__file__).resolve().parents[1]
    js = (root / "app/web/static/app.js").read_text(encoding="utf-8")
    cyr = re.compile(r"[\u0400-\u045f]")
    assert not any(cyr.search(line) for line in js.splitlines()), (
        "кириллица в app.js (T17)"
    )
    for marker in (
        "bindImportSheet",
        "u3-import-drop",
        "u3-import-files",
        "u3-import-list",
        "u3-import-summary",
        'fetchJSON("/api/books"',
        'fetchJSON("/api/imports/" + enc(id))',
        "import_progress_of",
        "import_toast_translate",
        '/jobs?sheet=create&book=',
    ):
        assert marker in js, f"app.js: нет маркера {marker}"
    templates = root / "app/web/templates"
    for name in (
        "_book_import_sheet.html",
        "book_upload.html",
        "import_status.html",
    ):
        assert (templates / name).is_file(), f"нет шаблона {name}"
    books = (templates / "books.html").read_text(encoding="utf-8")
    assert 'id="u3-sheet-import"' in books
    assert "data-sheet-open" in books
    assert "_book_import_sheet.html" in books
    row = (templates / "_book_row.html").read_text(encoding="utf-8")
    assert "/api/books/covers/" in row
