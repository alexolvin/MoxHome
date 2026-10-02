# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Серия R, R4 (ТЗ §12): создание задания — мастер в полноэкранном листе.

Т-подложка T6 (цикл «загрузить и запустить» — UI-замером):
- умолчания «как в прошлый раз» — сервером (views.create_defaults, единая
  точка базовой линии services.jobs.create_baseline): форма ?sheet=create;
- чипы «параметры, отличающиеся от умолчания» (jobs.param_chips,
  вычисление в create_job — единая точка со сводкой задания job.html);
- no-JS-создание: POST /jobs/new с опциональными learning_level /
  learning_cefr (303 + create_err, верификация — create_job);
- ?book=<имя> — предвыбор книги (кнопка «Перевести» — R5, механизм R4);
- повтор (retry_of) — параметры исходного задания (не базовой линии);
- CEFR-метка — единая точка app.languages.cefr (Estimated CEFR level);
- «Ещё»: объём (learning_level) + глоссарий (read-only из конфига).

Порядок тестов значим: модуль использует один u1_env (module-scoped),
тест 1 выполняется на ПУСТОЙ БД (системные умолчания, чипы первого задания).
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from lxml import html as lhtml
from test_u1_web import (
    U1Env,
    _auth_header,
    _create_job,
    _upload_book,
)

from app.web import views
from app.web.auth import csrf_token
from app.web.strings import UI_STRINGS

S = UI_STRINGS


def _create_form(doc: lhtml.HtmlElement) -> lhtml.HtmlElement:
    forms = doc.xpath('//form[@id="u3-create-form"]')
    assert len(forms) == 1
    return forms[0]


def _selected(form: lhtml.HtmlElement, name: str) -> str:
    radios = form.xpath(f'//input[@name="{name}" and @type="radio" and @checked]')
    assert len(radios) == 1, f"не выбрано значение для {name}"
    return str(radios[0].get("value"))


def _source_line(doc: lhtml.HtmlElement) -> str:
    lines = doc.xpath('//p[@class="u3-wiz-source"]')
    assert len(lines) == 1
    return str(lines[0].text_content()).strip()


# ------------------------------------------------------------------ умолчания


def test_r4_1_defaults_system(u1_env: U1Env) -> None:
    """R4 §12: без заданий — системные умолчания (target «ru»/первый
    реестра, bilingual, уровень/CEFR не заданы); строка «По умолчанию»;
    CEFR-метка — единая точка (Estimated CEFR level, все пакеты derived)."""
    profile = u1_env.profiles["main"]
    assert not profile.repo.list_jobs()  # пустая БД (первый тест модуля)
    page = profile.client.get("/jobs?sheet=create")
    assert page.status_code == 200, page.text
    doc = lhtml.fromstring(page.text)
    form = _create_form(doc)
    assert _selected(form, "target_language") == "ru"
    assert _selected(form, "mode") == "bilingual"
    assert _selected(form, "learning_cefr") == ""
    assert _selected(form, "learning_level") == ""
    assert _source_line(doc) == S["create_default"]
    labels = doc.xpath('//span[@id="u3-cefr-label"]')
    assert len(labels) == 1
    assert labels[0].text_content().strip() == "Estimated CEFR level"
    # дубля <dialog> на no-JS-странице нет (R3-инвариант)
    assert not doc.xpath('//dialog[@id="u3-sheet-create"]')


def test_r4_2_chips_first_job_defaults(u1_env: U1Env) -> None:
    """R4 §12: первое задание СО ВСЕМИ системными умолчаниями — чипов нет
    (param_chips '', facts []); сводка экрана задания без строки чипов."""
    profile = u1_env.profiles["main"]
    book = _upload_book(profile.client, u1_env.books["tiny"])
    job_id = _create_job(profile.client, book)  # ru + bilingual, без level/cefr
    assert profile.repo.get(job_id).param_chips == ""
    facts = profile.client.get(f"/api/jobs/{job_id}").json()
    assert facts["param_chips"] == []
    page = profile.client.get(f"/jobs/{job_id}")
    doc = lhtml.fromstring(page.text)
    assert not doc.xpath('//span[contains(@class,"u3-chip")]')
    assert not doc.xpath(
        f'//th[normalize-space()="{S["job_row_param_chips"]}"]'
    )


def test_r4_3_defaults_last_job(u1_env: U1Env) -> None:
    """R4 §12: есть задания — умолчания «как в прошлый раз» (последнее по
    created_at): target/mode/уровень/CEFR последнего в селектах; строка
    «Как в прошлый раз · изменить»."""
    profile = u1_env.profiles["main"]
    book = _upload_book(profile.client, u1_env.books["tiny"])
    _create_job(
        profile.client,
        book,
        target="de",
        mode="translation",
        level="Intensive",
        cefr="B1",
    )
    page = profile.client.get("/jobs?sheet=create")
    doc = lhtml.fromstring(page.text)
    form = _create_form(doc)
    assert _selected(form, "target_language") == "de"
    assert _selected(form, "mode") == "translation"
    assert _selected(form, "learning_cefr") == "B1"
    assert _selected(form, "learning_level") == "Intensive"
    assert (
        _source_line(doc)
        == f'{S["create_last_time"]} · {S["create_change"]}'
    )


# ------------------------------------------------------------------ разметка


def test_r4_4_wizard_markup(u1_env: U1Env) -> None:
    """R4 §12: мастер — ОДНА форма (единый механизм JS/no-JS): шаг 1 —
    строки-радио §6 (data-detected/data-dc для живой сводки) + поиск;
    шаг 2 — 4 селекta; «Ещё» — объём (learning_level) + глоссарий
    read-only из конфига; CEFR-метка под уровнем."""
    profile = u1_env.profiles["main"]
    _upload_book(profile.client, u1_env.books["tiny"])
    _upload_book(profile.client, u1_env.books["slow"])
    page = profile.client.get("/jobs?sheet=create")
    doc = lhtml.fromstring(page.text)
    form = _create_form(doc)
    assert form.get("action") == "/jobs/new"
    # шаг 1: по одной строке-радио на книгу (загрузка создаёт уникальное имя)
    n_books = len(views.list_books(profile.settings, profile.repo))
    radios = form.xpath('//input[@name="book" and @type="radio"]')
    assert len(radios) == n_books and n_books >= 2
    assert len({r.get("value") for r in radios}) == n_books
    for radio in radios:
        row = radio.getparent()
        assert "u3-wiz-row" in str(row.get("class")).split()
        # data-детекция на радио (живая сводка «Языки» в JS)
        assert radio.get("data-detected") is not None
        assert radio.get("data-dc") is not None
    # поиск (no-JS — скрыт CSS, в разметке — есть)
    assert form.xpath('//input[@id="u3-wiz-book-q" and @type="search"]')
    # шаг 2: четыре поля (target/mode/cefr в сводке, level в «Ещё») —
    # V3 §7.6: выбор — радио-чипы (не <select>)
    for name in ("target_language", "mode", "learning_cefr", "learning_level"):
        assert form.xpath(f'//input[@name="{name}" and @type="radio"]'), name
    # CEFR-метка — единая точка (derived — «Estimated CEFR level»)
    label = doc.xpath('//span[@id="u3-cefr-label"]')
    assert label and "CEFR level" in label[0].text_content()
    # «Ещё»: подсказка объёма + глоссарий read-only (конфиг) +
    # строка LLM-профиля (R8, решение 12)
    assert doc.xpath('//p[@id="u3-vol-hint"]')
    glossary = doc.xpath('//p[contains(@class,"u3-wiz-readonly")]')
    assert len(glossary) == 2
    expected_glossary = S["create_more_glossary"] + ": " + (
        S["create_glossary_on"]
        if profile.settings.glossary.enabled
        else S["create_glossary_off"]
    )
    assert glossary[0].text_content().strip() == expected_glossary
    assert S["create_more_llm"] in glossary[1].text_content()


def test_r4_5_book_preselect(u1_env: U1Env) -> None:
    """R4 §12: ?book=<имя> — книга предвыбрана (радио отмечено; JS —
    шаг 1 сворачивается на клиенте); незнакомое имя — игнор (без отметки).
    Механизм для кнопки «Перевести» (R5)."""
    profile = u1_env.profiles["main"]
    book = _upload_book(profile.client, u1_env.books["tiny"])
    page = profile.client.get(f"/jobs?sheet=create&book={book}")
    doc = lhtml.fromstring(page.text)
    form = _create_form(doc)
    checked = form.xpath('//input[@name="book" and @checked]')
    assert len(checked) == 1 and checked[0].get("value") == book
    # «Языки»: источник — из метаданных книги (детекция)
    src = doc.xpath('//span[@id="u3-lang-src"]')
    assert src and src[0].text_content().strip() != "—"

    page = profile.client.get("/jobs?sheet=create&book=no_such.epub")
    doc = lhtml.fromstring(page.text)
    form = _create_form(doc)
    assert not form.xpath('//input[@name="book" and @checked]')


# ------------------------------------------------------------------ no-JS


def test_r4_6_nojs_create_levels(u1_env: U1Env) -> None:
    """R4 T15-регрессия: no-JS-создание с уровнем/CEFR (поля формы →
    create_job, единая точка валидации): успех — 303 /jobs, параметры в
    БД; токен — в u3_csrf (без заголовка)."""
    profile = u1_env.profiles["main"]
    settings, repo = profile.settings, profile.repo
    book = _upload_book(profile.client, u1_env.books["tiny"])
    no_csrf_header = TestClient(profile.app, headers=_auth_header())
    token = csrf_token(settings, "any-user")
    before = len(repo.list_jobs())

    response = no_csrf_header.post(
        "/jobs/new",
        data={
            "book": book,
            "target_language": "ru",
            "mode": "bilingual",
            "learning_level": "Intensive",
            "learning_cefr": "B2",
            "u3_csrf": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/jobs"
    assert len(repo.list_jobs()) == before + 1
    job = repo.list_jobs(1, 0, sort="created_at", direction="desc")[0]
    assert job.learning_level == "Intensive"
    assert job.learning_cefr == "B2"


def test_r4_7_nojs_create_invalid_level(u1_env: U1Env) -> None:
    """R4: невалидные learning_level/learning_cefr (ручное редактирование
    формы) — fail closed: 303 с create_err (verbatim), задание не создано."""
    profile = u1_env.profiles["main"]
    settings, repo = profile.settings, profile.repo
    book = _upload_book(profile.client, u1_env.books["tiny"])
    no_csrf_header = TestClient(profile.app, headers=_auth_header())
    token = csrf_token(settings, "any-user")
    before = len(repo.list_jobs())

    for bad_field, bad_value in (
        ("learning_level", "Bogus"),
        ("learning_cefr", "Z9"),
    ):
        response = no_csrf_header.post(
            "/jobs/new",
            data={
                "book": book,
                "target_language": "ru",
                "mode": "bilingual",
                bad_field: bad_value,
                "u3_csrf": token,
            },
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text
        assert response.headers["location"].startswith(
            "/jobs?sheet=create&create_err="
        )
        assert len(repo.list_jobs()) == before


# ------------------------------------------------------------------ чипы


def test_r4_8_chips_diff(u1_env: U1Env) -> None:
    """R4 §12: чипы — только отличия от базовой линии (последнее задание):
    target/mode отличаются — два чипа в порядке полей; level/cefr равны —
    чипов нет; рендер в сводке экрана задания."""
    profile = u1_env.profiles["main"]
    book = _upload_book(profile.client, u1_env.books["tiny"])
    # базовая линия: свежее задание с параметрами X
    _create_job(
        profile.client, book, target="ru", mode="bilingual", level="Intensive", cefr="B1"
    )
    # новое задание: target/mode другие, level/cefr — как в базе
    job_id = _create_job(
        profile.client, book, target="de", mode="translation", level="Intensive", cefr="B1"
    )
    job = profile.repo.get(job_id)
    assert [(c["f"], c["v"]) for c in json.loads(job.param_chips)] == [
        ("target", "de"),
        ("mode", "translation"),
    ]
    facts = profile.client.get(f"/api/jobs/{job_id}").json()
    assert facts["param_chips"] == [
        {"f": "target", "v": "de"},
        {"f": "mode", "v": "translation"},
    ]
    page = profile.client.get(f"/jobs/{job_id}")
    doc = lhtml.fromstring(page.text)
    chips = doc.xpath('//span[contains(@class,"u3-chip")]')
    assert [c.text_content().strip() for c in chips] == [
        f'{S["chip_target"]}: de',
        f'{S["chip_mode"]}: translation',
    ]


def test_r4_9_chips_removed_level(u1_env: U1Env) -> None:
    """R4 §12: снятые параметры (в базе были, в новом задании — нет) —
    тоже чипы (честность: «убрали» видно); значение — «Не задан»."""
    profile = u1_env.profiles["main"]
    book = _upload_book(profile.client, u1_env.books["tiny"])
    _create_job(
        profile.client, book, target="ru", mode="bilingual", level="Intensive", cefr="B1"
    )
    job_id = _create_job(profile.client, book, target="ru", mode="bilingual")
    chips = profile.client.get(f"/api/jobs/{job_id}").json()["param_chips"]
    assert chips == [{"f": "level", "v": None}, {"f": "cefr", "v": None}]
    page = profile.client.get(f"/jobs/{job_id}")
    doc = lhtml.fromstring(page.text)
    texts = [
        c.text_content().strip()
        for c in doc.xpath('//span[contains(@class,"u3-chip")]')
    ]
    assert texts == [
        f'{S["chip_level"]}: {S["common_not_set"]}',
        f'{S["chip_cefr"]}: {S["common_not_set"]}',
    ]


def test_r4_10_retry_prefill_original(u1_env: U1Env) -> None:
    """R4 §12: повтор (retry_of) — параметры ИСХОДНОГО задания, а не
    базовой линии: исходное без уровня (новое — с Intensive/B1) →
    селекты уровня/CEFR — «Не задан»."""
    profile = u1_env.profiles["main"]
    book = _upload_book(profile.client, u1_env.books["tiny"])
    # порядок: A (без уровня) → B (Intensive/B1; B — базовая линия)
    job_a = _create_job(profile.client, book, target="ru", mode="bilingual")
    _create_job(
        profile.client, book, target="ru", mode="bilingual", level="Intensive", cefr="B1"
    )
    page = profile.client.get(f"/jobs?sheet=create&retry_of={job_a}")
    doc = lhtml.fromstring(page.text)
    form = _create_form(doc)
    assert _selected(form, "learning_level") == ""
    assert _selected(form, "learning_cefr") == ""
    # книга и язык повторения — исходного
    checked = form.xpath('//input[@name="book" and @checked]')
    assert len(checked) == 1 and checked[0].get("value") == book
    assert _selected(form, "target_language") == "ru"


def test_r4_11_api_empty_level_strings(u1_env: U1Env) -> None:
    """R4: явные пустые строки в learning_level/learning_cefr в JSON API
    (селект «Не задан» в JS-мастере) = не задано: 200, параметры в БД
    None (нормализация на границе API — та же семантика, что no-JS-путь;
    замер T6 поймал 400 до фикса)."""
    profile = u1_env.profiles["main"]
    book = _upload_book(profile.client, u1_env.books["tiny"])
    response = profile.client.post(
        "/api/jobs",
        json={
            "book": book,
            "target_language": "ru",
            "mode": "bilingual",
            "learning_level": "",
            "learning_cefr": "",
        },
    )
    assert response.status_code == 200, response.text
    job = profile.repo.get(response.json()["id"])
    assert job.learning_level is None
    assert job.learning_cefr is None
