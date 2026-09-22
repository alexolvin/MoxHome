"""Серия R, R5 (ТЗ §7 + §15): «Библиотека» и карточка книги.

Т-подложка T10 (удаление книги с заданиями — лист с тремя путями, голый
409 в UI не попадает; состояние — GET /usage ДО вызова удаления) и T17
(0 текстовых литералов вне strings.py):
- sidecar ``.meta.json`` (§15): новые загрузки пишут сразу; ленивая
  миграция из ``.detect.json`` (старый файл НЕ удаляется);
- карточка ``/books/{filename}`` (6 позиций §7: обложка-буква 96×136,
  «Перевести»/«Экспорт», блок «Языки», до 3 последних заданий + «Все (N)»,
  свёрнутые «Технические сведения», красный «Удалить книгу»);
- правка метаданных (6 полей, один механизм «одно поле + сохранить»):
  PATCH fail-closed 400, пустое значение — очистка, no-JS-страница-форма;
- ``target_default``/``cefr_default`` реально действуют (create_defaults);
- удаление: ``file_only=1`` (задания сохраняются со ссылкой на файл),
  каскад двумя шагами с HMAC ``confirm_token`` (только терминальные),
  голый DELETE с заданиями — 409 (регрессия U3);
- поиск /books?q= (страница + API, серверный фильтр), фильтр /jobs?book=
  (строка «Книга: <имя>» + сброс; незнакомое имя — игнор);
- no-JS: сценарий удаления страницами, два POST-шага каскада, мета;
- T17: скан templates/ (кириллица вне Jinja-выражений) и static/
  (app.js — кириллица запрещена, u3_1; CSS — только в комментариях).

Порядок тестов значим: модуль использует один u1_env (module-scoped),
каждый тест грузит СВОЮ книгу (имя уникально: u1-<hex>-<имя>).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient
from lxml import html as lhtml
from test_u1_web import (
    U1Env,
    _auth_header,
    _create_job,
    _upload_book,
)

from app.domain.job import JobStatus
from app.web import views
from app.web.auth import csrf_token
from app.web.strings import UI_STRINGS

S = UI_STRINGS

# ------------------------------------------------------------------ утилиты


def _meta_path(profile: U1Env, book: str) -> Path:
    return profile.settings.input_dir / book.replace(".epub", ".epub.meta.json")


def _read_meta(profile: U1Env, book: str) -> dict[str, object]:
    return json.loads(_meta_path(profile, book).read_text(encoding="utf-8"))


def _cancel(repo: object, job_id: str, reason: str = "r5-test") -> None:
    repo.transition(job_id, JobStatus.CANCELLED, reason)  # type: ignore[attr-defined]


def _card(page: TestClient.Response) -> lhtml.HtmlElement:
    return lhtml.fromstring(page.text)


# ------------------------------------------------------------------ §15: sidecar


def test_r5_1_upload_writes_meta_sidecar(u1_env: U1Env) -> None:
    """R5 §15: новая загрузка пишет сразу ``.meta.json`` (детекция +
    дефолты + imported_at); старого формата ``.detect.json`` нет."""
    profile = u1_env.profiles["main"]
    book = _upload_book(profile.client, u1_env.books["tiny"])
    meta_file = _meta_path(profile, book)
    assert meta_file.is_file()
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta["imported_at"]
    assert meta["detected"]  # L0-детекция из скана
    assert meta["title"]  # название — из EPUB (scan_book), не из R5-полей
    # R5-поля (sidecar-расширение §15) — дефолты
    for field in ("author", "source_language", "target_default", "cefr_default", "note"):
        assert meta[field] is None
    assert not (profile.settings.input_dir / book.replace(".epub", ".epub.detect.json")).exists()


def test_r5_2_lazy_migration_from_detect_json(u1_env: U1Env) -> None:
    """R5 §15: ленивая миграция — при первом обращении читается старый
    ``.detect.json`` и пишется ``.meta.json`` (поля переносятся, новые —
    дефолты; СТАРЫЙ файл НЕ удаляется)."""
    profile = u1_env.profiles["main"]
    book = _upload_book(profile.client, u1_env.books["tiny"])
    dest = profile.settings.input_dir / book
    legacy = {
        "detected": "en",
        "dc_language": "en",
        "confidence": 0.97,
        "decision": "accepted",
        "title": "Legacy Title",
        "stats": {"chapter_count": 1, "segment_count": 1, "word_count": 1},
    }
    _meta_path(profile, book).unlink()
    detect_file = dest.with_suffix(".epub.detect.json")
    detect_file.write_text(json.dumps(legacy), encoding="utf-8")

    page = profile.client.get(f"/books/{book}")
    assert page.status_code == 200, page.text

    meta = _read_meta(profile, book)
    assert meta["detected"] == "en"
    assert meta["title"] == "Legacy Title"
    assert meta["author"] is None  # новое поле — дефолт
    assert detect_file.exists()  # старый файл сохранён
    assert lhtml.fromstring(page.text).xpath('//h2[@class="u3-book-title"]')


# ------------------------------------------------------------------ карточка §7


def test_r5_3_card_render(u1_env: U1Env) -> None:
    """R5 §7: карточка — 6 позиций: обложка-буква + название/автор,
    «Перевести»/«Экспорт», «Языки» (4 строки), до 3 последних заданий +
    «Все (N)», свёрнутые «Технические сведения» (SHA-256 — mono), красный
    «Удалить книгу». 6 полей — ссылки на лист одного поля (data-book-field)."""
    profile = u1_env.profiles["main"]
    settings, repo, client = profile.settings, profile.repo, profile.client
    book = _upload_book(client, u1_env.books["tiny"])
    client.patch(f"/api/books/{book}", json={"title": "Карточка", "author": "Автор"})
    job_ids = [_create_job(client, book) for _ in range(4)]
    for job_id in job_ids:
        _cancel(repo, job_id)

    page = client.get(f"/books/{book}")
    assert page.status_code == 200, page.text
    doc = _card(page)

    # (2) шапка: обложка-буква + название (meta.title) + автор
    cover = doc.xpath('//span[@class="u3-book-cover"]')
    assert len(cover) == 1 and cover[0].text_content() == "К"
    title = doc.xpath('//h2[@class="u3-book-title"]')
    assert len(title) == 1 and title[0].text_content() == "Карточка"
    # 6 полей — тап открывает лист одного поля (единый механизм)
    fields = {
        a.get("data-book-field")
        for a in doc.xpath('//a[@data-book-field]')
    }
    assert fields == {
        "title", "author", "note", "source_language", "target_default", "cefr_default"
    }
    # кнопки: «Перевести» → ?sheet=create&book=, «Экспорт» →
    # ?sheet=export&book= (R7: лист экспорта — та же точка, что у
    # строки/экрана задания)
    translate = doc.xpath('//a[contains(@class,"u3-book-action") and @href]')
    hrefs = sorted(a.get("href") for a in translate)
    assert hrefs == sorted(
        [
            f"/jobs?sheet=create&book={book}",
            f"/jobs?sheet=export&book={book}",
        ]
    )
    labels = [a.text_content() for a in translate]
    assert labels == [S["book_translate_btn"], S["book_export_btn"]]

    # (3) блок «Языки»: 4 строки (исходный/уверенность/цель/CEFR)
    lang_rows = doc.xpath('//section[contains(@class,"u3-book-block")]//div[@class="u3-book-row"]')
    assert len(lang_rows) == 4

    # (4) задания: 3 последние строки + «Все (4)»
    rows = doc.xpath('//article[contains(@class,"u3-row-job")]')
    assert len(rows) == 3
    shown = {r.get("data-id") for r in rows}
    assert shown <= set(job_ids)
    all_link = doc.xpath('//a[contains(@class,"u3-book-all")]')
    assert len(all_link) == 1
    assert all_link[0].text_content() == S["book_jobs_all"].format(n=4)
    assert all_link[0].get("href") == f"/jobs?book={book}"

    # (5) технические сведения: SHA-256 файла — mono, overflow-wrap
    sha256 = hashlib.sha256((settings.input_dir / book).read_bytes()).hexdigest()
    sha_cells = doc.xpath('//dd[@class="u3-mono u3-wrap"]')
    assert any(c.text_content() == sha256 for c in sha_cells)
    tech = doc.xpath('//details[@class="u3-book-tech"]')
    assert len(tech) == 1 and not tech[0].get("open")

    # (6) «Удалить книгу» — красный текст + data-book-delete (тот же поток)
    delete = doc.xpath('//a[@class="u3-book-delete"]')
    assert len(delete) == 1
    assert delete[0].get("data-book-delete") == book
    assert delete[0].text_content() == S["book_delete_btn"]
    # ⋯ appbar — тот же элемент (одна точка потока удаления)
    menu_delete = doc.xpath('//a[@data-book-delete]')
    assert len(menu_delete) == 2


def test_r5_4_card_404_and_traversal(u1_env: U1Env) -> None:
    """R5 §7: неизвестной книги — 404; traversal (имя не из input/) — 404."""
    profile = u1_env.profiles["main"]
    client = profile.client
    assert client.get("/books/no_such_book.epub").status_code == 404
    assert client.get("/books/%2E%2E%2Fno_such.epub").status_code == 404
    assert client.get("/api/books/no_such_book.epub/usage").status_code == 404


# ------------------------------------------------------------------ usage (T10)


def test_r5_5_usage_empty_and_unknown(u1_env: U1Env) -> None:
    """T10: GET /usage — ДО вызова удаления (состояние листа): заданий нет
    — {count: 0}; неизвестной книги — 404."""
    profile = u1_env.profiles["main"]
    client = profile.client
    book = _upload_book(client, u1_env.books["tiny"])
    usage = client.get(f"/api/books/{book}/usage").json()
    assert usage == {"name": book, "count": 0, "jobs": []}
    assert client.get("/api/books/ghost.epub/usage").status_code == 404


def test_r5_6_usage_lists_jobs(u1_env: U1Env) -> None:
    """T10: usage перечисляет задания со статусом и признаком
    терминальности (питает и список листа, и решение «каскад доступен»)."""
    profile = u1_env.profiles["main"]
    client, repo = profile.client, profile.repo
    book = _upload_book(client, u1_env.books["tiny"])
    j1 = _create_job(client, book)  # CREATED — не терминальное
    j2 = _create_job(client, book)
    _cancel(repo, j2)  # CANCELLED — терминальное

    usage = client.get(f"/api/books/{book}/usage").json()
    assert usage["count"] == 2
    by_id = {j["id"]: j for j in usage["jobs"]}
    assert by_id[j1]["terminal"] is False
    assert by_id[j1]["status"] == "CREATED"
    assert by_id[j2]["terminal"] is True
    assert by_id[j2]["status"] == "CANCELLED"
    assert by_id[j1]["status_group"] == "wait"
    assert by_id[j2]["status_group"] == "err"


# ------------------------------------------------------------------ PATCH мета


def test_r5_7_patch_updates_meta(u1_env: U1Env) -> None:
    """R5 §15: PATCH — частичное обновление (6 полей), возврат — полный
    sidecar; чужие поля не трогаются; файл на диске обновлён."""
    profile = u1_env.profiles["main"]
    client = profile.client
    book = _upload_book(client, u1_env.books["tiny"])
    page = client.patch(f"/api/books/{book}", json={"title": "Полное имя"})
    assert page.status_code == 200, page.text
    assert page.json()["title"] == "Полное имя"
    assert page.json()["author"] is None
    assert _read_meta(profile, book)["title"] == "Полное имя"

    page = client.patch(f"/api/books/{book}", json={"author": "И. Автор"})
    assert page.status_code == 200
    assert page.json()["title"] == "Полное имя"  # не затронуто
    assert page.json()["author"] == "И. Автор"


def test_r5_8_patch_fail_closed(u1_env: U1Env) -> None:
    """R5 §15: fail closed — неизвестное поле/язык/CEFR/длина/пустой
    патч — 400, файл не тронут."""
    profile = u1_env.profiles["main"]
    client = profile.client
    book = _upload_book(client, u1_env.books["tiny"])
    before = _meta_path(profile, book).read_bytes()

    cases = [
        ({"bogus": "x"}, S["err_book_bad_field"].format(field="bogus")),
        (
            {"source_language": "xx-unknown"},
            S["err_book_bad_language"].format(code="xx-unknown"),
        ),
        ({"cefr_default": "Z9"}, S["err_book_bad_cefr"].format(code="Z9")),
        (
            {"title": "x" * 201},
            S["err_book_meta_too_long"].format(field="title", limit=200),
        ),
        ({}, S["err_book_meta_empty"]),
    ]
    for patch, expected in cases:
        page = client.patch(f"/api/books/{book}", json=patch)
        assert page.status_code == 400, (patch, page.text)
        assert page.json()["detail"] == expected
    assert _meta_path(profile, book).read_bytes() == before


def test_r5_9_patch_clears_field(u1_env: U1Env) -> None:
    """R5 §15: пустое значение = очистка поля (sidecar: null)."""
    profile = u1_env.profiles["main"]
    client = profile.client
    book = _upload_book(client, u1_env.books["tiny"])
    assert client.patch(f"/api/books/{book}", json={"title": "Было"}).status_code == 200
    page = client.patch(f"/api/books/{book}", json={"title": ""})
    assert page.status_code == 200
    assert page.json()["title"] is None
    assert _read_meta(profile, book)["title"] is None


def test_r5_10_create_defaults_book_override(u1_env: U1Env) -> None:
    """R5 решение 3: target_default/cefr_default книги реально действуют —
    create_defaults (единая точка: страница ?book= и базовая линия)
    подставляет их; невалидные значения — игнор (базовая линия)."""
    profile = u1_env.profiles["main"]
    client, repo, settings = profile.client, profile.repo, profile.settings
    book = _upload_book(client, u1_env.books["tiny"])
    page = client.patch(
        f"/api/books/{book}", json={"target_default": "de", "cefr_default": "B1"}
    )
    assert page.status_code == 200, page.text

    defaults = views.create_defaults(repo, settings, book=book)
    assert defaults["target"] == "de"
    assert defaults["learning_cefr"] == "B1"
    # форма ?book= — те же умолчания в селектах
    form_page = client.get(f"/jobs?sheet=create&book={book}")
    doc = lhtml.fromstring(form_page.text)
    form = doc.xpath('//form[@id="u3-create-form"]')[0]
    # lxml: `//x` — абсолютный путь от корня документа (весь документ!),
    # для поддерева элемента — `.//x` / `./x`.
    selected = {
        s.get("name"): str(o.get("value"))
        for s in form.xpath('.//select')
        for o in s.xpath('./option[@selected]')
    }
    assert selected["target_language"] == "de"
    assert selected["learning_cefr"] == "B1"
    # невалидные значения sidecar — базовая линия (не падает, не мусор)
    client.patch(f"/api/books/{book}", json={"target_default": "de"})
    bad = client.patch(f"/api/books/{book}", json={"cefr_default": ""})
    assert bad.status_code == 200
    defaults = views.create_defaults(repo, settings, book=book)
    assert defaults["learning_cefr"] in (None, "")


# ------------------------------------------------------------------ удаление (T10)


def test_r5_11_delete_file_only_keeps_jobs(u1_env: U1Env) -> None:
    """T10 путь 1: DELETE ?file_only=1 — файл уходит, задания сохраняются
    (source_path — как есть, бэкенд U3 не меняется)."""
    profile = u1_env.profiles["main"]
    client, repo, settings = profile.client, profile.repo, profile.settings
    book = _upload_book(client, u1_env.books["tiny"])
    job_ids = [_create_job(client, book) for _ in range(2)]
    for job_id in job_ids:
        _cancel(repo, job_id)

    page = client.delete(f"/api/books/{book}?file_only=1")
    assert page.status_code == 200, page.text
    assert page.json() == {"deleted": book, "file_only": True}
    assert not (settings.input_dir / book).exists()
    assert not _meta_path(profile, book).exists()  # sidecar тоже
    assert set(repo.list_job_ids_by_book(book)) == set(job_ids)  # задания на месте
    for job_id in job_ids:
        assert repo.get(job_id).source_path  # ссылка на файл сохранена


def test_r5_12_bare_delete_409_regression(u1_env: U1Env) -> None:
    """U3-регрессия: голый DELETE на книгу с заданиями — 409 (контракт
    API не меняется; UI в R5 через usage-лист, но API-граница прежняя)."""
    profile = u1_env.profiles["main"]
    client, repo, settings = profile.client, profile.repo, profile.settings
    book = _upload_book(client, u1_env.books["tiny"])
    job_id = _create_job(client, book)
    _cancel(repo, job_id)

    page = client.delete(f"/api/books/{book}")
    assert page.status_code == 409
    assert job_id in page.json()["detail"]
    assert (settings.input_dir / book).exists()  # книга на месте


def test_r5_13_delete_simple(u1_env: U1Env) -> None:
    """T10 сценарий (а): заданий нет — DELETE — файл и оба sidecar
    удаляются; книга исчезает из списка."""
    profile = u1_env.profiles["main"]
    client, settings = profile.client, profile.settings
    book = _upload_book(client, u1_env.books["tiny"])
    page = client.delete(f"/api/books/{book}")
    assert page.status_code == 200, page.text
    assert page.json() == {"deleted": book, "file_only": False}
    assert not (settings.input_dir / book).exists()
    assert not _meta_path(profile, book).exists()
    names = [b["name"] for b in client.get("/api/books").json()]
    assert book not in names


def test_r5_14_purge_dry_run(u1_env: U1Env) -> None:
    """T10 путь 2, шаг 1: POST /purge без токена — состав терминальных
    заданий + confirm_token (HMAC, 32 hex). Нет заданий/книги — 409."""
    profile = u1_env.profiles["main"]
    client, repo = profile.client, profile.repo
    book = _upload_book(client, u1_env.books["tiny"])
    j1 = _create_job(client, book)
    j2 = _create_job(client, book)
    _cancel(repo, j1)
    _cancel(repo, j2)

    dry = client.post(f"/api/books/{book}/purge", json={}).json()
    assert dry["book"] == book
    assert dry["job_ids"] == sorted([j1, j2])
    assert dry["count"] == 2
    assert dry["total_bytes"] >= 0
    assert re.fullmatch(r"[0-9a-f]{32}", dry["confirm_token"])
    # повторный dry-run — тот же состав (идемпотентно до мутаций)
    assert client.post(f"/api/books/{book}/purge", json={}).json()["job_ids"] == dry["job_ids"]

    empty_book = _upload_book(client, u1_env.books["tiny"])
    assert client.post(f"/api/books/{empty_book}/purge", json={}).status_code == 409
    assert client.post("/api/books/ghost.epub/purge", json={}).status_code == 409


def test_r5_15_purge_nonterminal_409(u1_env: U1Env) -> None:
    """T10: есть не-терминальное задание — каскад 409 с перечнем (сначала
    отмена); в UI кнопка каскада отключена (лист по usage)."""
    profile = u1_env.profiles["main"]
    client = profile.client
    book = _upload_book(client, u1_env.books["tiny"])
    j1 = _create_job(client, book)  # CREATED — не терминальное
    j2 = _create_job(client, book)
    _cancel(profile.repo, j2)

    page = client.post(f"/api/books/{book}/purge", json={})
    assert page.status_code == 409
    assert j1 in page.json()["detail"]
    assert j2 not in page.json()["detail"]


def test_r5_16_purge_bad_token_409(u1_env: U1Env) -> None:
    """T10: шаг 2 с чужим/протухшим токеном — 409, ничего не удалено."""
    profile = u1_env.profiles["main"]
    client, repo, settings = profile.client, profile.repo, profile.settings
    book = _upload_book(client, u1_env.books["tiny"])
    job_id = _create_job(client, book)
    _cancel(repo, job_id)

    page = client.post(f"/api/books/{book}/purge", json={"confirm_token": "0" * 32})
    assert page.status_code == 409
    # расхождение состава: токен от ДРУГОГО состава — тоже 409
    other_book = _upload_book(client, u1_env.books["tiny"])
    other_job = _create_job(client, other_book)
    _cancel(repo, other_job)
    other_token = client.post(f"/api/books/{other_book}/purge", json={}).json()["confirm_token"]
    page = client.post(f"/api/books/{book}/purge", json={"confirm_token": other_token})
    assert page.status_code == 409
    assert (settings.input_dir / book).exists()
    assert repo.get(job_id) is not None


def test_r5_17_purge_confirm(u1_env: U1Env) -> None:
    """T10 путь 2, шаг 2: тот же состав + confirm_token — удаляется ровно
    перечисленное: файл + sidecar + задания (строки БД + каталоги)."""
    profile = u1_env.profiles["main"]
    client, repo, settings = profile.client, profile.repo, profile.settings
    book = _upload_book(client, u1_env.books["tiny"])
    job_ids = [_create_job(client, book) for _ in range(2)]
    for job_id in job_ids:
        _cancel(repo, job_id)
    other_book = _upload_book(client, u1_env.books["tiny"])
    other_job = _create_job(client, other_book)
    _cancel(repo, other_job)  # чужая книга не пострадает

    dry = client.post(f"/api/books/{book}/purge", json={}).json()
    page = client.post(f"/api/books/{book}/purge", json={"confirm_token": dry["confirm_token"]})
    assert page.status_code == 200, page.text
    assert page.json()["purged"] == sorted(job_ids)
    assert not (settings.input_dir / book).exists()
    assert not _meta_path(profile, book).exists()
    for job_id in job_ids:
        assert repo.get(job_id) is None
    assert repo.list_job_ids_by_book(book) == []
    # чужое задание и чужая книга — на месте
    assert repo.get(other_job) is not None
    assert (settings.input_dir / other_book).exists()


# ------------------------------------------------------------------ фильтры


def test_r5_18_jobs_book_filter(u1_env: U1Env) -> None:
    """R5 решение 4: /jobs?book=<имя> — строка «Книга: <имя>» + сброс
    (снят ТОЛЬКО фильтр книги); список — только задания этой книги.
    Незнакомое имя — игнор (фильтр не включается, строки — как /jobs)."""
    profile = u1_env.profiles["main"]
    client = profile.client
    book = _upload_book(client, u1_env.books["tiny"])
    my_job = _create_job(client, book)

    page = client.get(f"/jobs?book={book}")
    assert page.status_code == 200, page.text
    doc = _card(page)
    strip = doc.xpath('//p[@class="u3-fstrip"]')
    assert len(strip) == 1
    assert S["jobs_book_strip"].format(name=book) in strip[0].text_content()
    reset = strip[0].xpath('.//a[@class="u3-fstrip-reset"]')
    assert len(reset) == 1 and reset[0].get("href") == "/jobs"
    rows = doc.xpath('//noscript//article[contains(@class,"u3-row-job")]')
    assert rows and all(r.get("data-book") == book for r in rows)
    assert my_job in {r.get("data-id") for r in rows}

    doc = _card(client.get("/jobs?book=ghost.epub"))
    assert not doc.xpath('//p[@class="u3-fstrip"]')
    # data-атрибут для JS-фильтра (дельта) — пусто при игноре
    assert doc.xpath('//div[@id="u1-jobs"]/@data-u1-book') == [""]


def test_r5_19_books_search(u1_env: U1Env) -> None:
    """R5 решение 2: поиск — один механизм (GET /books?q=; серверный
    фильтр имя+название): страница и API фильтруются одинаково;
    без совпадений — состояние empty-filter со сбросом на /books."""
    profile = u1_env.profiles["main"]
    client = profile.client
    book = _upload_book(client, u1_env.books["tiny"])
    marker = book.split("-")[-2]  # уникальные 8 hex между префиксом и именем

    api = [b["name"] for b in client.get("/api/books", params={"q": marker}).json()]
    assert book in api and len(api) < len(client.get("/api/books").json())

    doc = _card(client.get("/books", params={"q": marker}))
    rows = doc.xpath('//noscript//article[contains(@class,"u3-row-book")]')
    assert [r.get("data-name") for r in rows] == api
    # форма поиска — с текущим q (no-JS — GET)
    form = doc.xpath('//form[@id="u1-books-search"]')
    assert len(form) == 1
    assert form[0].xpath('./input[@name="q"]/@value') == [marker]

    doc = _card(client.get("/books", params={"q": "no_such_xyz"}))
    assert doc.xpath('//noscript//div[@data-state="empty-filter"]')


# ------------------------------------------------------------------ no-JS


def test_r5_20_nojs_delete_simple(u1_env: U1Env) -> None:
    """T10 no-JS, сценарий (а): страница /books/{name}/delete без заданий —
    вопрос + «Удалить»/«Отмена»; POST — 303 /books, файл удалён."""
    profile = u1_env.profiles["main"]
    settings = profile.settings
    book = _upload_book(profile.client, u1_env.books["tiny"])
    page = profile.client.get(f"/books/{book}/delete")
    assert page.status_code == 200, page.text
    doc = _card(page)
    assert S["book_delete_simple_q"].format(name=book) in doc.text_content()
    form = doc.xpath(f'//form[@action="/books/{book}/delete"]')
    assert len(form) == 1

    no_csrf = TestClient(profile.app, headers=_auth_header())
    response = no_csrf.post(
        f"/books/{book}/delete",
        data={"u3_csrf": csrf_token(settings, "any-user")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/books"
    assert not (settings.input_dir / book).exists()


def test_r5_21_nojs_delete_with_jobs(u1_env: U1Env) -> None:
    """T10 no-JS, сценарий (б): страница — «N заданий» + список статусов +
    3 формы (только файл / каскад / отмена). file_only — 303, задания
    сохраняются; не-терминальное — предупреждение и БЕЗ формы каскада."""
    profile = u1_env.profiles["main"]
    settings, repo = profile.settings, profile.repo
    book = _upload_book(profile.client, u1_env.books["tiny"])
    j1 = _create_job(profile.client, book)  # не-терминальное
    j2 = _create_job(profile.client, book)
    _cancel(repo, j2)

    doc = _card(profile.client.get(f"/books/{book}/delete"))
    assert S["book_delete_referenced"].format(n=2) in doc.text_content()
    assert j1 in doc.text_content() and j2 in doc.text_content()
    assert (
        S["book_delete_nonterminal"].format(n=2, ids=j1) in doc.text_content()
    )
    # форм: file_only + отмена (каскада НЕТ — есть не-терминальное)
    forms = doc.xpath('//form')
    assert [f.get("action") for f in forms] == [f"/books/{book}/delete_file_only"]

    no_csrf = TestClient(profile.app, headers=_auth_header())
    response = no_csrf.post(
        f"/books/{book}/delete_file_only",
        data={"u3_csrf": csrf_token(settings, "any-user")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/books"
    assert not (settings.input_dir / book).exists()
    assert set(repo.list_job_ids_by_book(book)) == {j1, j2}

    # все задания терминальные — форма каскада есть (два POST-шага — r5_22)
    book2 = _upload_book(profile.client, u1_env.books["tiny"])
    for _ in range(2):
        _cancel(repo, _create_job(profile.client, book2))
    doc = _card(profile.client.get(f"/books/{book2}/delete"))
    actions = [f.get("action") for f in doc.xpath('//form')]
    assert actions == [f"/books/{book2}/delete_file_only", f"/books/{book2}/purge"]


def test_r5_22_nojs_purge_two_step(u1_env: U1Env) -> None:
    """T10 no-JS, каскад: POST /purge без токена — страница-подтверждение
    (шаг 2) с confirm_token; с токеном — 303 /books, всё удалено.
    Чужой токен — 409 с причиной (голый 409 — только как страница)."""
    profile = u1_env.profiles["main"]
    settings, repo = profile.settings, profile.repo
    book = _upload_book(profile.client, u1_env.books["tiny"])
    job_ids = [_create_job(profile.client, book) for _ in range(2)]
    for job_id in job_ids:
        _cancel(repo, job_id)
    no_csrf = TestClient(profile.app, headers=_auth_header())
    token = csrf_token(settings, "any-user")

    # чужой токен — 409, причина на странице (не голый)
    response = no_csrf.post(
        f"/books/{book}/purge",
        data={"u3_csrf": token, "confirm_token": "f" * 32},
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert S["err_book_purge_token"] in response.text
    assert repo.get(job_ids[0]) is not None

    # шаг 1: подтверждение с токеном
    response = no_csrf.post(
        f"/books/{book}/purge", data={"u3_csrf": token}, follow_redirects=False
    )
    assert response.status_code == 200, response.text
    confirm_doc = lhtml.fromstring(response.text)
    confirm_tokens = confirm_doc.xpath('//input[@name="confirm_token"]/@value')
    assert len(confirm_tokens) == 1
    # шаг 2: тот же состав — удаление
    response = no_csrf.post(
        f"/books/{book}/purge",
        data={"u3_csrf": token, "confirm_token": confirm_tokens[0]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/books"
    assert not (settings.input_dir / book).exists()
    for job_id in job_ids:
        assert repo.get(job_id) is None


def test_r5_23_nojs_meta_page(u1_env: U1Env) -> None:
    """R5 §7 no-JS: страница-форма ОДНОГО поля (текст/select по kind);
    POST — 303 на карточку, sidecar обновлён; невалидное значение —
    400 с причиной (fail closed), файл не тронут; неизвестное поле — 400."""
    profile = u1_env.profiles["main"]
    settings, client = profile.settings, profile.client
    book = _upload_book(client, u1_env.books["tiny"])
    no_csrf = TestClient(profile.app, headers=_auth_header())
    token = csrf_token(settings, "any-user")

    # текст: author (предзаполнено пусто)
    doc = _card(client.get(f"/books/{book}/meta?field=author"))
    form = doc.xpath(f'//form[@action="/books/{book}/meta"]')
    assert len(form) == 1
    assert doc.xpath('//input[@name="value" and @type="text"]')
    assert doc.xpath('//input[@name="field" and @value="author"]')

    response = no_csrf.post(
        f"/books/{book}/meta",
        data={"u3_csrf": token, "field": "author", "value": "No-JS Автор"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/books/{book}"
    assert _read_meta(profile, book)["author"] == "No-JS Автор"

    # lang: select из реестра; cefr: select уровней
    doc = _card(client.get(f"/books/{book}/meta?field=target_default"))
    assert doc.xpath('//select[@name="value"]//option[@value="en"]')
    doc = _card(client.get(f"/books/{book}/meta?field=cefr_default"))
    assert doc.xpath('//select[@name="value"]//option[@value="B1"]')

    # fail closed: невалидный CEFR — 400 + причина, файл не тронут
    before = _meta_path(profile, book).read_bytes()
    response = no_csrf.post(
        f"/books/{book}/meta",
        data={"u3_csrf": token, "field": "cefr_default", "value": "Z9"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert S["err_book_bad_cefr"].format(code="Z9") in response.text
    assert _meta_path(profile, book).read_bytes() == before
    # неизвестное поле — 400
    response = no_csrf.post(
        f"/books/{book}/meta",
        data={"u3_csrf": token, "field": "bogus", "value": "x"},
        follow_redirects=False,
    )
    assert response.status_code == 400


# ------------------------------------------------------------------ разметка / T17


def test_r5_24_book_row_links_and_js_wiring(u1_env: U1Env) -> None:
    """R5 §6/§7: строка книги — ссылка на карточку (тап по строке); меню
    «⋯» — «Удалить книгу» (data-book-delete, href — no-JS-сценарий).
    app.js: потоки удаления и мета (единые обработчики), строка
    строится как ссылка, литералов русского в JS нет (u3_1 — r5_25)."""
    profile = u1_env.profiles["main"]
    book = _upload_book(profile.client, u1_env.books["tiny"])
    doc = _card(profile.client.get("/books"))
    links = doc.xpath('//a[@class="u3-row-link"]')
    assert any(a.get("href") == f"/books/{book}" for a in links)
    menus = doc.xpath(
        f'//a[@data-book-delete="{book}"][@class="u3-row-menu-item u3-danger"]'
    )
    assert len(menus) == 1
    assert menus[0].get("href") == f"/books/{book}/delete"

    js = (Path(__file__).resolve().parents[1] / "app/web/static/app.js").read_text(
        encoding="utf-8"
    )
    for marker in (
        "bindBookDeleteFlow",
        "bindBookMeta",
        "u3-sheet-book-delete",
        "u3-sheet-book-meta",
        "/usage",
        "file_only=1",
        "confirm_token",
    ):
        assert marker in js, marker
    # строка книги в JS — ссылка на карточку (построитель #2 = #1)
    assert "u3-row-link' href='/books/" in js


def test_r5_25_t17_no_literals_outside_strings() -> None:
    """T17: 0 текстовых литералов вне strings.py — templates/ (кириллица
    вне Jinja-выражений {{ }} / {% %} / {# #}) и static/ (app.js —
    кириллица запрещена ВКЛЮЧАЯ комментарии, u3_1; CSS — только в
    комментариях). Гвард дублирует замер .a/tasks/ui-audit/t17_literals.py.
    Статический аудит — окружение не нужно."""
    root = Path(__file__).resolve().parents[1]
    cyr = re.compile(r"[\u0400-\u045f]")
    jinja = re.compile(r"\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}", re.S)

    violations: list[str] = []
    for path in sorted((root / "app/web/templates").glob("*.html")):
        text = jinja.sub("", path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(text.splitlines(), 1):
            if cyr.search(line):
                violations.append(f"{path.name}:{lineno}: {line.strip()[:60]}")
    for path in sorted((root / "app/web/static").glob("*.js")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if cyr.search(line):
                violations.append(f"{path.name}:{lineno}: {line.strip()[:60]}")
    for path in sorted((root / "app/web/static").glob("*.css")):
        no_comments = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
        for lineno, line in enumerate(no_comments.splitlines(), 1):
            if cyr.search(line):
                violations.append(f"{path.name}:{lineno}: {line.strip()[:60]}")
    assert not violations, "текстовые литералы вне strings.py:\n" + "\n".join(violations)
