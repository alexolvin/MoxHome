# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""R7 (ТЗ §9): экспорт — 8 форматов, макеты билингвы, карточки, TTL.

Т-подложки:
- 8 форматов из одного задания: epub — копия, остальные — конвертация
  (stub: копия фикстуры на любое расширение); 1×1 — один файл,
  иначе — один ZIP с именем по шаблону `<автор> — <название> (ru)`;
- карточки — набор S5 как есть (3 файла), отчёты — вложенный ZIP
  (quality/alignment/validation, что существует);
- макеты: translation_only — класс + <style> в каждом spine-документе,
  two_columns — таблица 50/50 только для пар с прямым ребёнком <body>
  (вложенные — append, задокументированное ограничение);
- fail closed: 409 (не завершено / уже выполняется), 400 (выбор,
  неизвестный объект/формат/макет, файл объекта, макет без pdf/htmlz,
  макет не билингва-APPEND_BLOCK), 404 (задание/книга/артефакт,
  traversal);
- экспорт файла книги (ТЗ §15): имя без суффикса языка, конвертер —
  гейт для не-EPUB;
- TTL: sweep_exports (старые — удалены, свежие — на месте);
- no-JS (T15): формы → 303 → статус-страница (meta-refresh, download);
- a11y (u3_14) — страницы листа/статуса; JS-провязка (u3_1).

Конвертер — тот же stub, что R6 (handoff R6 решение 14): ``--version`` →
``stub 1.0``; ``IN OUT`` → копия ``tests/fixtures/epub/probe.en.epub``.
Задания создаются репозиторием напрямую (полная цепочка переходов до
COMPLETED) — воркер и LLM в экспорте не участвуют (ТЗ §9: LLM не
запускается).
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import lxml.etree
import lxml.html as lhtml
import pytest
from fastapi.testclient import TestClient
from test_r6_web import _write_stub
from test_u1_web import (
    AUTH_SECRET,
    U1Env,
    _auth_header,
    _make_u1_storage,
    _upload_book,
)
from test_u3_web import _u3_14_check_a11y

from app.adapters.convert import reset_probe_cache
from app.core.ids import new_job_id
from app.domain.job import Job, JobStatus
from app.services.export_artifacts import sweep_exports
from app.web.auth import csrf_token
from app.web.strings import UI_STRINGS

ROOT = Path(__file__).resolve().parents[1]
S = UI_STRINGS

#: Сpine-документ APPEND_BLOCK: оригинал <p> + перевод <p> (sibling).
_CHAPTER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">\n'
    "<head><title>ch1</title></head>\n"
    "<body>\n"
    "<p>Original one.</p>\n"
    "<p>Translation one.</p>\n"
    "<p>Original two.</p>\n"
    "<p>Translation two.</p>\n"
    "</body>\n"
    "</html>\n"
)


# ------------------------------------------------------------------ окружение


@pytest.fixture(scope="module")
def r7_env(u1_env: U1Env) -> U1Env:
    """Профили R7 поверх общего u1_env:
    - ``r7`` — stub-конвертер (8 форматов);
    - ``r7delay`` — stub с задержкой 0,5 с (409 «уже выполняется»);
    - ``r7bad`` — binary отсутствует (гейт конвертера).
    """
    base = u1_env.base
    stubs = base / "r7_stubs"
    stubs.mkdir(exist_ok=True)
    stub = _write_stub(stubs / "stub")
    stub_delay = _write_stub(stubs / "stub_delay", delay=0.5)
    for name, directory, extra in (
        ("r7", "storage_r7", f"converter:\n  binary: {stub}\n"),
        ("r7delay", "storage_r7delay", f"converter:\n  binary: {stub_delay}\n"),
        ("r7bad", "storage_r7bad", f"converter:\n  binary: {stubs / 'missing'}\n"),
    ):
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
    """Кэш пробы конвертера — глобальный; сброс до/после каждого теста."""
    reset_probe_cache()
    yield
    reset_probe_cache()


# ------------------------------------------------------------------ утилиты


def _build_test_epub(out: Path) -> None:
    """Минимальный EPUB с APPEND_BLOCK-структурой: container.xml → OPF →
    spine (md конвертирует сам сервис — решение 12, OPF обязателен)."""
    container = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        "<manifest>"
        '<item id="ch1" href="ch1.xhtml" '
        'media-type="application/xhtml+xml"/>'
        "</manifest>"
        '<spine><itemref idref="ch1"/></spine>'
        "</package>"
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/ch1.xhtml", _CHAPTER)


def _alignment_report(submit_mode: str = "APPEND_BLOCK") -> dict[str, Any]:
    """alignment_report.json в реальном формате (report_to_dict, E6-5)."""
    return {
        "submit_mode": submit_mode,
        "source_text_source": "test",
        "min_coverage": 0.0,
        "coverage": 1.0,
        "total": 2,
        "aligned": 2,
        "unaligned": 0,
        "extra_blocks": 0,
        "identity_mismatches": 0,
        "docs": [
            {
                "doc": "OEBPS/ch1.xhtml",
                "aligned": [
                    {
                        "segment_id": 1,
                        # позиционный XPath segmenter.positional_xpath:
                        # сегменты без разделителей (/htmlbody[1]p[1])
                        "source_xpath": "/htmlbody[1]p[1]",
                        "source_text": "Original one.",
                        "translation_xpath": "/htmlbody[1]p[2]",
                        "translation_text": "Translation one.",
                        "how": "sibling",
                    },
                    {
                        "segment_id": 2,
                        "source_xpath": "/htmlbody[1]p[3]",
                        "source_text": "Original two.",
                        "translation_xpath": "/htmlbody[1]p[4]",
                        "translation_text": "Translation two.",
                        "how": "sibling",
                    },
                ],
                "unaligned": [],
                "extra_blocks": [],
            }
        ],
    }


_CHAIN = (
    JobStatus.VALIDATING,
    JobStatus.PLANNING,
    JobStatus.TRANSLATING,
    JobStatus.ANALYZING,
    JobStatus.GENERATING,
    JobStatus.VALIDATING_OUTPUT,
    JobStatus.GLOSSARY_FIX,
    JobStatus.LEARNING,
)


def _make_job(
    r7_env: U1Env,
    profile_name: str,
    book: str,
    *,
    mode: str = "bilingual",
    terminal: bool = True,
) -> str:
    """Задание репозиторием (воркер не нужен): CREATED → цепочка → COMPLETED."""
    profile = r7_env.profiles[profile_name]
    repo, settings = profile.repo, profile.settings
    now = time.time()
    job = Job(
        id=new_job_id(),
        status=JobStatus.CREATED,
        source_path=str(settings.input_dir / book),
        target_language="ru",
        mode=mode,
        created_at=now,
        updated_at=now,
    )
    repo.create(job)
    if terminal:
        for status in _CHAIN:
            repo.transition(job.id, status, "r7", actor="test")
        repo.transition(job.id, JobStatus.COMPLETED, "r7", actor="test")
    return job.id


def _write_artifacts(
    r7_env: U1Env,
    profile_name: str,
    job_id: str,
    *,
    learning: bool = True,
    cards: bool = True,
    reports: bool = True,
    submit_mode: str = "APPEND_BLOCK",
) -> Path:
    profile = r7_env.profiles[profile_name]
    jdir = profile.settings.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    _build_test_epub(jdir / "target.epub")
    if learning:
        _build_test_epub(jdir / "learning.epub")
    if cards:
        (jdir / "cards.csv").write_text("q,a\none,dva\n", encoding="utf-8")
        (jdir / "cards.anki.tsv").write_text("one\tdva\n", encoding="utf-8")
        (jdir / "cards.json").write_text(
            json.dumps([{"q": "one", "a": "dva"}]), encoding="utf-8"
        )
    if reports:
        (jdir / "quality.json").write_text('{"score": 1.0}', encoding="utf-8")
        (jdir / "alignment_report.json").write_text(
            json.dumps(_alignment_report(submit_mode)), encoding="utf-8"
        )
        # форма — как у движка (translation_phases читает stats.chapter_count)
        (jdir / "validation.json").write_text(
            json.dumps(
                {
                    "stats": {"chapter_count": 1},
                    "chapter_documents": ["OEBPS/ch1.xhtml"],
                    "errors": 0,
                }
            ),
            encoding="utf-8",
        )
    return jdir


def _set_book_meta(r7_env: U1Env, profile_name: str, book: str) -> None:
    """Метаданные книги — детерминированное имя по шаблону ТЗ §9.

    Merge, а не перезапись: импорт (R6) уже записал sidecar с
    confidence/detected — частичный файл ломает book.html
    (meta.confidence | round)."""
    profile = r7_env.profiles[profile_name]
    meta = profile.settings.input_dir / f"{book}.meta.json"
    existing = (
        json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
    )
    existing.update({"title": "Probe Title", "author": "Probe Author"})
    meta.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")


BASE = "Probe Author — Probe Title (ru)"


def _post_export(
    client: TestClient, job_id: str, **payload: Any
) -> TestClient.Response:
    body: dict[str, Any] = {"objects": ["translation"], "formats": ["epub"]}
    body.update(payload)
    return client.post(f"/api/jobs/{job_id}/export", json=body)


def _wait_export(
    client: TestClient, url: str, timeout: float = 60.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    rec: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(url)
        assert response.status_code == 200, response.text
        rec = response.json()
        if rec["status"] in ("done", "error"):
            return rec
        time.sleep(0.1)
    pytest.fail(f"экспорт не завершился за {timeout:.0f} с: {rec}")


def _read_epub_doc(epub_path: Path | bytes, name: str = "OEBPS/ch1.xhtml"):
    """Документ + ns-карта (xpath по XHTML — только с namespaces)."""
    if isinstance(epub_path, bytes):
        zf = zipfile.ZipFile(io.BytesIO(epub_path))
        data = zf.read(name)
    else:
        with zipfile.ZipFile(epub_path) as zf:
            data = zf.read(name)
    doc = lxml.etree.fromstring(data)
    return doc, {"x": doc.nsmap.get(None)}


# ------------------------------------------------------------------ 8 форматов


def test_r7_1_eight_formats_one_zip(r7_env: U1Env) -> None:
    """T9 (unit): 1 объект × 8 форматов — один ZIP, 8 записей, имя по
    шаблону; каждый файл скачивается (200, непусто)."""
    profile = r7_env.profiles["r7"]
    client, settings = profile.client, profile.settings
    book = _upload_book(client, r7_env.books["tiny"])
    _set_book_meta(r7_env, "r7", book)
    job_id = _make_job(r7_env, "r7", book)
    _write_artifacts(r7_env, "r7", job_id)

    response = _post_export(
        client,
        job_id,
        formats=["epub", "pdf", "docx", "fb2", "azw3", "txt", "md", "htmlz"],
    )
    assert response.status_code == 202, response.text
    rec = _wait_export(client, f"/api/jobs/{job_id}/export")
    assert rec["status"] == "done", rec
    assert rec["files"] == [f"{BASE}.zip"], rec

    response = client.get(f"/api/jobs/{job_id}/export/{BASE}.zip")
    assert response.status_code == 200
    outer = zipfile.ZipFile(io.BytesIO(response.content))  # type: ignore[arg-type]
    names = sorted(outer.namelist())
    assert names == sorted(f"{BASE}.{f}" for f in ("epub", "pdf", "docx", "fb2",
                                                    "azw3", "txt", "md", "htmlz")), names
    for fmt in ("epub", "pdf", "docx", "fb2", "azw3", "txt", "md", "htmlz"):
        entry = f"{BASE}.{fmt}"
        assert len(outer.read(entry)) > 0
        response = client.get(f"/api/jobs/{job_id}/export/{entry}")
        assert response.status_code == 200
        assert len(response.content) > 0
    # md — сервисом (у calibre нет вывода .md, решение 12): в APPEND_BLOCK
    # оба блока — оригинал и перевод
    md_text = outer.read(f"{BASE}.md").decode("utf-8")
    assert "Original one." in md_text
    assert "Translation one." in md_text
    assert not (settings.job_dir(job_id) / "export" / ".layout.tmp.epub").exists()


def test_r7_2_one_object_one_format_single_file(r7_env: U1Env) -> None:
    """1×1 — один файл (ZIP не нужен), имя по шаблону."""
    profile = r7_env.profiles["r7"]
    client = profile.client
    book = _upload_book(client, r7_env.books["tiny"])
    _set_book_meta(r7_env, "r7", book)
    job_id = _make_job(r7_env, "r7", book)
    _write_artifacts(r7_env, "r7", job_id)

    response = _post_export(client, job_id, formats=["epub"])
    assert response.status_code == 202, response.text
    rec = _wait_export(client, f"/api/jobs/{job_id}/export")
    assert rec["status"] == "done", rec
    assert rec["files"] == [f"{BASE}.epub"], rec
    response = client.get(f"/api/jobs/{job_id}/export/{BASE}.epub")
    assert response.status_code == 200
    assert len(response.content) > 0


# ------------------------------------------------------------------ объекты


def test_r7_3_all_objects_multi_format(r7_env: U1Env) -> None:
    """4 объекта × 2 формата: учебная книга — суффикс «study», карточки —
    набор S5 как есть, отчёты — вложенный ZIP (3 JSON)."""
    profile = r7_env.profiles["r7"]
    client = profile.client
    book = _upload_book(client, r7_env.books["tiny"])
    _set_book_meta(r7_env, "r7", book)
    job_id = _make_job(r7_env, "r7", book)
    _write_artifacts(r7_env, "r7", job_id)

    response = _post_export(
        client, job_id, objects=["translation", "learning", "cards", "reports"],
        formats=["epub", "pdf"],
    )
    assert response.status_code == 202, response.text
    rec = _wait_export(client, f"/api/jobs/{job_id}/export")
    assert rec["status"] == "done", rec
    assert rec["files"] == [f"{BASE}.zip"], rec

    outer = zipfile.ZipFile(
        io.BytesIO(  # type: ignore[arg-type]
            client.get(f"/api/jobs/{job_id}/export/{BASE}.zip").content
        )
    )
    names = set(outer.namelist())
    assert names == {
        f"{BASE}.epub", f"{BASE}.pdf",
        f"{BASE} — study.epub", f"{BASE} — study.pdf",
        f"{BASE} — cards.csv", f"{BASE} — cards.anki.tsv", f"{BASE} — cards.json",
        f"{BASE} — reports.zip",
    }, names
    # отчёты — вложенный ZIP, что существует (все три)
    inner = zipfile.ZipFile(outer.open(f"{BASE} — reports.zip"))
    assert sorted(inner.namelist()) == [
        "alignment_report.json", "quality.json", "validation.json",
    ]
    # карточки — побайтово как в каталоге задания
    jdir = profile.settings.job_dir(job_id)
    assert outer.read(f"{BASE} — cards.csv") == (jdir / "cards.csv").read_bytes()


# ------------------------------------------------------------------ макеты


def test_r7_4_layout_translation_only(r7_env: U1Env) -> None:
    """«Только перевод»: оригиналы — класс u3-export-orig + <style> в head
    spine-документа; переводы не тронуты."""
    profile = r7_env.profiles["r7"]
    client = profile.client
    book = _upload_book(client, r7_env.books["tiny"])
    _set_book_meta(r7_env, "r7", book)
    job_id = _make_job(r7_env, "r7", book)
    _write_artifacts(r7_env, "r7", job_id)

    response = _post_export(client, job_id, formats=["epub", "md"],
                            layout="translation_only")
    assert response.status_code == 202, response.text
    rec = _wait_export(client, f"/api/jobs/{job_id}/export")
    assert rec["status"] == "done", rec

    doc, ns = _read_epub_doc(
        client.get(f"/api/jobs/{job_id}/export/{BASE}.epub").content
    )  # type: ignore[arg-type]
    ps = doc.xpath("//x:p", namespaces=ns)
    assert [p.text for p in ps[:2]] == ["Original one.", "Translation one."]
    assert "u3-export-orig" in (ps[0].get("class") or "")
    assert not (ps[1].get("class") or "")
    assert "u3-export-orig" in (ps[2].get("class") or "")
    styles = doc.xpath("//x:head/x:style", namespaces=ns)
    assert any(".u3-export-orig{display:none}" in (st.text or "") for st in styles)

    # md: оригиналы, скрытые классом макета, — отсутствуют (конвертер
    # сервиса, решение 12)
    outer = zipfile.ZipFile(
        io.BytesIO(client.get(f"/api/jobs/{job_id}/export/{BASE}.zip").content)
    )  # type: ignore[arg-type]
    md_text = outer.read(f"{BASE}.md").decode("utf-8")
    assert "Translation one." in md_text
    assert "Original one." not in md_text


def test_r7_5_layout_two_columns(r7_env: U1Env) -> None:
    """«Две колонки» (PDF/HTMLZ): пары с прямым ребёнком <body> → таблица
    50/50 (оригинал | перевод). Одна пост-обработка — один источник:
    epub в пачке несёт тот же макет (все форматы выводятся из
    layout'ированного target.epub). htmlz у stub-конвертера — копия
    фикстуры (структуру внутри проверять нельзя), но скачивается."""
    profile = r7_env.profiles["r7"]
    client = profile.client
    book = _upload_book(client, r7_env.books["tiny"])
    _set_book_meta(r7_env, "r7", book)
    job_id = _make_job(r7_env, "r7", book)
    _write_artifacts(r7_env, "r7", job_id)

    response = _post_export(client, job_id, formats=["htmlz", "epub"],
                            layout="two_columns")
    assert response.status_code == 202, response.text
    rec = _wait_export(client, f"/api/jobs/{job_id}/export")
    assert rec["status"] == "done", rec

    htmlz = client.get(f"/api/jobs/{job_id}/export/{BASE}.htmlz")
    assert htmlz.status_code == 200
    assert htmlz.content[:2] == b"PK"  # zip-пакет htmlz

    doc, ns = _read_epub_doc(
        client.get(f"/api/jobs/{job_id}/export/{BASE}.epub").content
    )  # type: ignore[arg-type]
    tables = doc.xpath("//x:body/x:table", namespaces=ns)
    assert len(tables) == 2, "каждая body-пара — своя таблица"
    first_tds = tables[0].xpath("./x:tr/x:td", namespaces=ns)
    assert len(first_tds) == 2
    assert first_tds[0].xpath("./x:p", namespaces=ns)[0].text == "Original one."
    assert first_tds[1].xpath("./x:p", namespaces=ns)[0].text == "Translation one."
    assert first_tds[0].get("style", "").startswith("width:50%")


def test_r7_6_layout_rejections(r7_env: U1Env) -> None:
    """Макет fail closed: без pdf/htmlz; режим не bilingual; submit_mode не
    APPEND_BLOCK; неизвестный макет — 400 с точной строкой."""
    profile = r7_env.profiles["r7"]
    client = profile.client
    book = _upload_book(client, r7_env.books["tiny"])
    _set_book_meta(r7_env, "r7", book)

    job_id = _make_job(r7_env, "r7", book)
    _write_artifacts(r7_env, "r7", job_id)
    response = _post_export(client, job_id, formats=["epub"], layout="two_columns")
    assert response.status_code == 400
    assert S["err_export_layout_needs_two"] in response.text

    job_trans = _make_job(r7_env, "r7", book, mode="translation")
    _write_artifacts(r7_env, "r7", job_trans)
    response = _post_export(client, job_trans, formats=["pdf"],
                            layout="translation_only")
    assert response.status_code == 400
    assert S["err_export_layout_unavailable"] in response.text

    job_replace = _make_job(r7_env, "r7", book)
    _write_artifacts(r7_env, "r7", job_replace, submit_mode="REPLACE")
    response = _post_export(client, job_replace, formats=["epub"],
                            layout="translation_only")
    assert response.status_code == 400
    assert S["err_export_layout_unavailable"] in response.text

    response = _post_export(client, job_id, formats=["epub"], layout="grid")
    assert response.status_code == 400
    assert S["err_export_unknown_layout"].format(value="grid") in response.text


# ------------------------------------------------------------------ fail closed


def test_r7_7_api_fail_closed(r7_env: U1Env) -> None:
    """409 (не завершено), 404 (задание), 400 (выбор/неизвестное/нет
    файла), 404 артефакт + traversal."""
    profile = r7_env.profiles["r7"]
    client = profile.client
    book = _upload_book(client, r7_env.books["tiny"])
    _set_book_meta(r7_env, "r7", book)

    job_pending = _make_job(r7_env, "r7", book, terminal=False)
    response = _post_export(client, job_pending)
    assert response.status_code == 409
    assert S["err_export_pending"].format(status="CREATED") in response.text

    job_id = _make_job(r7_env, "r7", book)
    _write_artifacts(r7_env, "r7", job_id, learning=False, cards=False, reports=False)
    response = _post_export(client, "j-00000000T000000Z-000000")
    assert response.status_code == 404
    response = _post_export(client, job_id, objects=[], formats=["epub"])
    assert response.status_code == 400
    assert S["err_export_no_selection"] in response.text
    response = _post_export(client, job_id, objects=["summary"], formats=["epub"])
    assert response.status_code == 400
    assert S["err_export_unknown_object"].format(value="summary") in response.text
    response = _post_export(client, job_id, formats=["txt2"])
    assert response.status_code == 400
    assert S["err_export_unknown_format"].format(value="txt2") in response.text
    response = _post_export(client, job_id, objects=["learning"], formats=["epub"])
    assert response.status_code == 400
    assert S["err_export_object_missing"].format(name="learning.epub") in response.text
    response = _post_export(client, job_id, objects=["reports"], formats=["epub"])
    assert response.status_code == 400
    assert S["err_export_object_missing"].format(name="reports") in response.text

    # артефакт: нет / traversal / скрытый — 404
    for artifact in ("missing.epub", "..%2ftarget.epub", ".layout.tmp.epub",
                     "sub%2Ffile.epub"):
        response = client.get(f"/api/jobs/{job_id}/export/{artifact}")
        assert response.status_code == 404, artifact
    # статус без экспорта — 404
    response = client.get(f"/api/jobs/{job_id}/export")
    assert response.status_code == 404
    assert S["err_export_not_found"] in response.text


def test_r7_8_in_progress_409(r7_env: U1Env) -> None:
    """Второй старт того же задания, пока первый выполняется, — 409."""
    profile = r7_env.profiles["r7delay"]
    client = profile.client
    book = _upload_book(client, r7_env.books["tiny"])
    _set_book_meta(r7_env, "r7delay", book)
    job_id = _make_job(r7_env, "r7delay", book)
    _write_artifacts(r7_env, "r7delay", job_id)

    response = _post_export(client, job_id, formats=["pdf"])  # stub: 0,5 с
    assert response.status_code == 202, response.text
    response = _post_export(client, job_id, formats=["epub"])
    assert response.status_code == 409
    assert S["err_export_in_progress"] in response.text
    rec = _wait_export(client, f"/api/jobs/{job_id}/export")
    assert rec["status"] == "done", rec
    # после завершения — снова можно
    response = _post_export(client, job_id, formats=["epub"])
    assert response.status_code == 202, response.text
    assert _wait_export(client, f"/api/jobs/{job_id}/export")["status"] == "done"


# ------------------------------------------------------------------ книга (ТЗ §15)


def test_r7_9_book_export(r7_env: U1Env) -> None:
    """Экспорт исходника: имя без суффикса языка; epub — копия; pdf —
    конвертер; 404 (книги нет); 400 (неизвестный формат; конвертер)."""
    profile = r7_env.profiles["r7"]
    client, settings = profile.client, profile.settings
    book = _upload_book(client, r7_env.books["tiny"])
    _set_book_meta(r7_env, "r7", book)
    name_epub = "Probe Author — Probe Title.epub"

    response = client.post(f"/api/books/{book}/export", json={"format": "epub"})
    assert response.status_code == 202, response.text
    rec = _wait_export(client, f"/api/books/{book}/export")
    assert rec["status"] == "done", rec
    assert rec["files"] == [name_epub], rec
    response = client.get(f"/api/books/{book}/export/{name_epub}")
    assert response.status_code == 200
    assert len(response.content) > 0
    # файл — в input/<name>.export (TTL-область)
    assert (settings.input_dir / f"{book}.export" / name_epub).is_file()

    response = client.post(f"/api/books/{book}/export", json={"format": "pdf"})
    assert response.status_code == 202, response.text
    rec = _wait_export(client, f"/api/books/{book}/export")
    assert rec["status"] == "done", rec
    assert rec["files"] == ["Probe Author — Probe Title.pdf"], rec

    response = client.post(
        "/api/books/no-such-book.epub/export", json={"format": "epub"}
    )
    assert response.status_code == 404
    assert S["err_export_book_not_found"].format(name="no-such-book.epub") in response.text
    response = client.post(f"/api/books/{book}/export", json={"format": "chm"})
    assert response.status_code == 400
    assert S["err_export_unknown_format"].format(value="chm") in response.text

    # гейт конвертера: профиль без binary — не-EPUB 400, EPUB проходит
    # (кэш пробы — одна ячейка: сброс, иначе «ok» профиля r7 утекает)
    reset_probe_cache()
    bad = r7_env.profiles["r7bad"]
    book_bad = _upload_book(bad.client, r7_env.books["tiny"])
    response = bad.client.post(f"/api/books/{book_bad}/export",
                               json={"format": "pdf"})
    assert response.status_code == 400
    assert S["err_converter_unavailable"] in response.text
    response = bad.client.post(f"/api/books/{book_bad}/export",
                               json={"format": "epub"})
    assert response.status_code == 202, response.text
    rec = _wait_export(bad.client, f"/api/books/{book_bad}/export")
    assert rec["status"] == "done", rec


# ------------------------------------------------------------------ TTL


def test_r7_10_ttl_sweep(r7_env: U1Env) -> None:
    """sweep_exports: старше ui.export_ttl_hours — удалено (пустой
    каталог — за ним), свежее — на месте."""
    profile = r7_env.profiles["r7"]
    settings = profile.settings
    assert settings.ui.export_ttl_hours == 48.0
    book = _upload_book(profile.client, r7_env.books["tiny"])
    job_id = _make_job(r7_env, "r7", book)
    jdir = settings.job_dir(job_id)

    edir = jdir / "export"
    edir.mkdir(parents=True)
    old = edir / "old.pdf"
    old.write_bytes(b"old")
    fresh = edir / "fresh.pdf"
    fresh.write_bytes(b"fresh")
    old_mtime = time.time() - 100 * 3600
    os.utime(old, (old_mtime, old_mtime))

    bdir = settings.input_dir / f"{book}.export"
    bdir.mkdir(exist_ok=True)
    bdir_old = bdir / "old.epub"
    bdir_old.write_bytes(b"old")
    os.utime(bdir_old, (old_mtime, old_mtime))

    removed = sweep_exports(settings)
    assert removed == 2, removed
    assert not old.exists() and not bdir_old.exists()
    assert fresh.exists()
    assert edir.exists()  # свежий файл — каталог жив
    assert not bdir.exists()  # пустой — за ним


# ------------------------------------------------------------------ no-JS (T15)


def test_r7_11_nojs_job_sheet_and_status(r7_env: U1Env) -> None:
    """kind=job: форма (объекты предвыбраны, epub отмечен) → 303 →
    статус-страница с download-ссылками; отказ — 303 c ?export_err=."""
    profile = r7_env.profiles["r7"]
    client = profile.client
    book = _upload_book(client, r7_env.books["tiny"])
    _set_book_meta(r7_env, "r7", book)
    job_id = _make_job(r7_env, "r7", book)
    _write_artifacts(r7_env, "r7", job_id)

    doc = client.get(f"/jobs?sheet=export&job={job_id}")
    assert doc.status_code == 200
    assert f'action="/jobs/{job_id}/export"' in doc.text
    assert 'value="translation" checked' in doc.text
    assert 'value="epub" checked' in doc.text
    assert book in doc.text

    response = client.post(
        f"/jobs/{job_id}/export",
        data={"objects": ["translation"], "formats": ["epub"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/jobs/{job_id}/export"
    rec = _wait_export(client, f"/api/jobs/{job_id}/export")
    assert rec["status"] == "done", rec
    page = client.get(f"/jobs/{job_id}/export")
    assert page.status_code == 200
    assert f'href="/api/jobs/{job_id}/export/{quote(f"{BASE}.epub")}" download=' in page.text

    # отказ: пусто — 303 обратно с причиной
    response = client.post(
        f"/jobs/{job_id}/export", data={}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "export_err=" in response.headers["location"]

    # страница статуса без экспорта / неизвестное задание — 404
    job2 = _make_job(r7_env, "r7", book)
    assert client.get(f"/jobs/{job2}/export").status_code == 404
    assert client.get("/jobs/j-00000000T000000Z-000000/export").status_code == 404
    assert client.get("/jobs?sheet=export&job=unknown").status_code == 404


def test_r7_12_nojs_book_sheet(r7_env: U1Env) -> None:
    """kind=book: радио завершённых заданий (POST /jobs/export) + блок
    «Файл книги» (POST /books/{name}/export); без заданий — только блок
    файла."""
    profile = r7_env.profiles["r7"]
    client = profile.client
    book = _upload_book(client, r7_env.books["tiny"])
    _set_book_meta(r7_env, "r7", book)
    job_id = _make_job(r7_env, "r7", book)
    _write_artifacts(r7_env, "r7", job_id)

    page = client.get(f"/jobs?sheet=export&book={book}")
    assert page.status_code == 200
    assert f'value="{job_id}" checked' in page.text
    assert 'action="/jobs/export"' in page.text
    assert f'action="/books/{book}/export"' in page.text

    # «Файл книги» → 303 → статус-страница
    response = client.post(
        f"/books/{book}/export", data={"format": "epub"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/books/{book}/export"
    rec = _wait_export(client, f"/api/books/{book}/export")
    assert rec["status"] == "done", rec
    page = client.get(f"/books/{book}/export")
    assert page.status_code == 200
    name_epub = "Probe Author — Probe Title.epub"
    assert f'href="/api/books/{book}/export/{quote(name_epub)}" download=' in page.text

    # задание через POST /jobs/export (radio) → 303 на статус
    response = client.post(
        "/jobs/export",
        data={
            "job": job_id, "book": book,
            "objects": ["translation"], "formats": ["epub"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/jobs/{job_id}/export"
    _wait_export(client, f"/api/jobs/{job_id}/export")

    # книга без заданий — блок «файл книги» + подсказка
    book2 = _upload_book(client, r7_env.books["slow"])
    page = client.get(f"/jobs?sheet=export&book={book2}")
    assert page.status_code == 200
    assert S["job_export_no_jobs"] in page.text
    assert 'name="job"' not in page.text
    assert f'action="/books/{book2}/export"' in page.text


# ------------------------------------------------------------------ facts / a11y


def test_r7_13_facts_export_block(r7_env: U1Env) -> None:
    """GET /api/jobs/{id}: export.objects — наличие файлов, export.layouts
    — только bilingual + APPEND_BLOCK."""
    profile = r7_env.profiles["r7"]
    client = profile.client
    book = _upload_book(client, r7_env.books["tiny"])

    job_id = _make_job(r7_env, "r7", book)
    _write_artifacts(r7_env, "r7", job_id)
    facts = client.get(f"/api/jobs/{job_id}").json()
    assert facts["export"]["objects"] == {
        "translation": True, "learning": True, "cards": True, "reports": True,
    }
    assert facts["export"]["layouts"] is True

    job_partial = _make_job(r7_env, "r7", book)
    _write_artifacts(r7_env, "r7", job_partial, learning=False, cards=False)
    facts = client.get(f"/api/jobs/{job_partial}").json()
    assert facts["export"]["objects"] == {
        "translation": True, "learning": False, "cards": False, "reports": True,
    }
    assert facts["export"]["layouts"] is True  # alignment_report на месте

    job_trans = _make_job(r7_env, "r7", book, mode="translation")
    _write_artifacts(r7_env, "r7", job_trans)
    facts = client.get(f"/api/jobs/{job_trans}").json()
    assert facts["export"]["layouts"] is False


def test_r7_14_a11y_and_js_wiring(r7_env: U1Env) -> None:
    """u3_14 (label/aria/кнопки/th) на страницах экспорта; u3_1 + точки
    входа: строка (app.js), экран задания, карточка книги (dialog на
    странице — иначе initSheets mёртвит клик)."""
    profile = r7_env.profiles["r7"]
    client = profile.client
    book = _upload_book(client, r7_env.books["tiny"])
    _set_book_meta(r7_env, "r7", book)
    job_id = _make_job(r7_env, "r7", book)
    _write_artifacts(r7_env, "r7", job_id)

    _post_export(client, job_id, formats=["epub"])
    _wait_export(client, f"/api/jobs/{job_id}/export")
    for url in (
        f"/jobs?sheet=export&job={job_id}",
        f"/jobs?sheet=export&book={book}",
        f"/jobs/{job_id}",
        f"/jobs/{job_id}/export",
        f"/books/{book}",
    ):
        text = client.get(url).text
        _u3_14_check_a11y(url, text)

    js = (ROOT / "app/web/static/app.js").read_text(encoding="utf-8")
    assert not re.search(r"[\u0400-\u045f]", js), "кириллица в app.js (T17)"
    for marker in (
        "u3-sheet-export",
        "data-u3-exp-run",
        "fillExportBody",
        "fillExportBook",
        'fetchJSON("/api/jobs/" + enc(jobId) + "/export"',
        'fetchJSON("/api/books/" + enc(arg) + "/export"',
        "data-exp-mode='bookfile'",
        "syncExportTwo",
    ):
        assert marker in js, marker
    for tpl, markers in (
        ("book.html", ('data-sheet-open="u3-sheet-export"',
                       'data-book="{{ detail.name }}"',
                       'id="u3-sheet-export"')),
        ("job.html", ('data-sheet-open="u3-sheet-export"',
                      'id="u3-sheet-export"')),
        ("jobs.html", ('id="u3-sheet-export"', '_sheet_export.html')),
    ):
        text = (ROOT / "app/web/templates" / tpl).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in text, f"{tpl}: {marker}"


def test_r7_15_job_screen_and_row_entry_points(r7_env: U1Env) -> None:
    """Три точки входа (ТЗ §9): экран задания — кнопка «Экспорт»
    (data-sheet-open + data-job); карточка книги — «Экспорт»
    (data-book → /jobs?sheet=export&book=)."""
    profile = r7_env.profiles["r7"]
    client = profile.client
    book = _upload_book(client, r7_env.books["tiny"])
    job_id = _make_job(r7_env, "r7", book)
    _write_artifacts(r7_env, "r7", job_id)

    page = client.get(f"/jobs/{job_id}")
    assert page.status_code == 200
    assert f'data-sheet-open="u3-sheet-export" data-job="{job_id}"' in page.text

    card = client.get(f"/books/{book}")
    assert card.status_code == 200
    assert f'data-sheet-open="u3-sheet-export" data-book="{book}"' in card.text
    # lxml HTML: атрибут декодирует &amp; → & (как R5, _card)
    cdoc = lhtml.fromstring(card.text)
    hrefs = [a.get("href") or "" for a in cdoc.xpath("//a[@href]")]
    assert f"/jobs?sheet=export&book={book}" in hrefs
