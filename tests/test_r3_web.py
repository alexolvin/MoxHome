"""R3 — управление заданиями (TZ-UI-R §11/§14/§15): бэкенд-часть.

Т-подложка T5 (кнопки первичных действий — UI-замером) и T15 (no-JS-
маршруты — UI-замером при javascript_enabled=false):
- POST /api/jobs/bulk — pause/cancel (отчёт applied/skipped), delete
  (fail closed + два шага с confirm_token, механизм purge), ошибки;
- PATCH /api/jobs/{id} — только queue_rank; валидация; терминал — 409;
- порядок кандидатов воркера — queue_rank DESC, created_at ASC;
- multipart CSRF: no-JS-форма загрузки /api/books (u3_csrf в теле,
  без заголовка);
- no-JS-страницы: POST /jobs/new (303 + create_err),
  POST /jobs/{id}/confirm (303 + confirm_err), ?sheet=create/export/delete;
- строка списка (построитель #1): первичное действие по статусу (§11),
  панель свайпа (только допустимое + logs), скрытый u3_csrf в формах.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from lxml import html as lhtml
from test_u1_web import (
    U1Env,
    _auth_header,
    _create_job,
    _upload_book,
)

from app.domain.job import JobStatus
from app.web.auth import csrf_token
from app.web.strings import UI_STRINGS

ROOT = Path(__file__).resolve().parents[1]

#: Цепочки доменных переходов (воркер в этих тестах не нужен) до целевого
#: статуса из CREATED — по TRANSITIONS (app/domain/job.py).
_PATHS: dict[JobStatus, tuple[JobStatus, ...]] = {
    JobStatus.NEEDS_CONFIRMATION: (JobStatus.VALIDATING, JobStatus.NEEDS_CONFIRMATION),
    JobStatus.PAUSED: (JobStatus.PAUSED,),
    JobStatus.TRANSLATING: (JobStatus.VALIDATING, JobStatus.PLANNING, JobStatus.TRANSLATING),
    JobStatus.FAILED: (JobStatus.FAILED,),
    JobStatus.CANCELLED: (JobStatus.CANCELLED,),
    JobStatus.COMPLETED: (
        JobStatus.VALIDATING,
        JobStatus.PLANNING,
        JobStatus.TRANSLATING,
        JobStatus.ANALYZING,
        JobStatus.GENERATING,
        JobStatus.VALIDATING_OUTPUT,
        JobStatus.GLOSSARY_FIX,
        JobStatus.LEARNING,
        JobStatus.COMPLETED,
    ),
}


def _set_status(profile: Any, job_id: str, status: JobStatus) -> None:
    """Прямые доменные переходы из CREATED до целевого статуса."""
    for target in _PATHS[status]:
        profile.repo.transition(job_id, target, "r3-test")


# ------------------------------------------------------------------ bulk


def test_r3_1_bulk_pause_cancel_report(u1_env: U1Env) -> None:
    """R3 §11: bulk pause/cancel — один запрос; допустимые применены
    (флаги в БД, переход делает воркер), терминальные пропущены с
    причиной (отчёт)."""
    profile = u1_env.profiles["main"]
    client, repo = profile.client, profile.repo
    book = _upload_book(client, u1_env.books["tiny"])
    created_id = _create_job(client, book)
    paused_id = _create_job(client, book)
    _set_status(profile, paused_id, JobStatus.PAUSED)
    terminal_id = _create_job(client, book)
    _set_status(profile, terminal_id, JobStatus.CANCELLED)

    for action in ("pause", "cancel"):
        response = client.post(
            "/api/jobs/bulk",
            json={"action": action, "ids": [created_id, paused_id, terminal_id]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["action"] == action
        assert sorted(body["applied"]) == sorted([created_id, paused_id])
        assert body["skipped"] == [
            {
                "id": terminal_id,
                "reason": UI_STRINGS["bulk_skip_terminal"].format(status="CANCELLED"),
            }
        ]
    # CREATED: команда — флаг (переход делает воркер в точке контроля),
    # БД: оба флага стоят, статус без изменений
    row = profile.db.conn.execute(
        "SELECT pause_requested, cancel_requested FROM jobs WHERE id = ?",
        (created_id,),
    ).fetchone()
    assert row["pause_requested"] == 1 and row["cancel_requested"] == 1
    assert repo.get(created_id).status is JobStatus.CREATED
    # PAUSED: воркер не исполняет и не применяет флагов (занимает PAUSED
    # только по resume) — отмена из PAUSED немедленный переход
    assert repo.get(paused_id).status is JobStatus.CANCELLED


def test_r3_2_bulk_delete_two_step(u1_env: U1Env) -> None:
    """R3 §11: массовое удаление — два шага с confirm_token (механизм
    purge): dry-run — отсортированный состав + байты; исполнение — ровно
    он, каталоги и строки удалены; чужой токен — 409, ничего нетронуто."""
    profile = u1_env.profiles["main"]
    client, repo, settings = profile.client, profile.repo, profile.settings
    book = _upload_book(client, u1_env.books["tiny"])
    ids = sorted(_create_job(client, book) for _ in range(2))
    for job_id in ids:
        _set_status(profile, job_id, JobStatus.CANCELLED)
        job_dir = settings.job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "job.log").write_bytes(b"x" * 64)

    step1 = client.post(
        "/api/jobs/bulk", json={"action": "delete", "ids": list(reversed(ids))}
    )
    assert step1.status_code == 200, step1.text
    dry = step1.json()
    assert dry["dry_run"] is True
    assert dry["job_ids"] == ids  # входной порядок нормализован
    assert dry["count"] == 2
    assert dry["total_bytes"] == 128

    step2 = client.post(
        "/api/jobs/bulk",
        json={"action": "delete", "ids": ids, "confirm_token": dry["confirm_token"]},
    )
    assert step2.status_code == 200, step2.text
    done = step2.json()
    assert done["dry_run"] is False
    assert done["purged"] == ids
    assert done["count"] == 2
    # строки по всем таблицам: 2 задания × (1 jobs + 1 transition)
    assert done["rows"] == 4
    for job_id in ids:
        assert repo.get(job_id) is None
        assert not settings.job_dir(job_id).exists()

    # токен от другого состава — 409; задание живо
    other_id = _create_job(client, book)
    _set_status(profile, other_id, JobStatus.CANCELLED)
    other_dir = settings.job_dir(other_id)
    other_dir.mkdir(parents=True, exist_ok=True)
    stale = client.post(
        "/api/jobs/bulk",
        json={"action": "delete", "ids": [other_id], "confirm_token": dry["confirm_token"]},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"] == UI_STRINGS["err_bulk_token"]
    assert repo.get(other_id) is not None
    # уборка: свой токен — своё удаление
    step1b = client.post("/api/jobs/bulk", json={"action": "delete", "ids": [other_id]})
    step2b = client.post(
        "/api/jobs/bulk",
        json={
            "action": "delete",
            "ids": [other_id],
            "confirm_token": step1b.json()["confirm_token"],
        },
    )
    assert step2b.status_code == 200, step2b.text
    assert repo.get(other_id) is None


def test_r3_3_bulk_delete_non_terminal_409(u1_env: U1Env) -> None:
    """R3 §11: массовое удаление с одним не-терминальным → 409, ничего
    не удалено (fail closed)."""
    profile = u1_env.profiles["main"]
    client, repo, settings = profile.client, profile.repo, profile.settings
    book = _upload_book(client, u1_env.books["tiny"])
    term_id = _create_job(client, book)
    _set_status(profile, term_id, JobStatus.CANCELLED)
    work_id = _create_job(client, book)
    _set_status(profile, work_id, JobStatus.TRANSLATING)
    work_dir = settings.job_dir(work_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "keep.log").write_bytes(b"z" * 10)

    response = client.post(
        "/api/jobs/bulk", json={"action": "delete", "ids": [term_id, work_id]}
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == UI_STRINGS["err_bulk_not_terminal"].format(
        ids=f"{work_id} (TRANSLATING)"
    )
    assert repo.get(term_id) is not None and repo.get(work_id) is not None
    assert (work_dir / "keep.log").read_bytes() == b"z" * 10


def test_r3_4_bulk_errors(u1_env: U1Env) -> None:
    """R3 §11: ошибки bulk — неизвестное действие (400), нет ids (400),
    неизвестное задание (404)."""
    profile = u1_env.profiles["main"]
    client = profile.client
    book = _upload_book(client, u1_env.books["tiny"])
    job_id = _create_job(client, book)

    for action in ("resume", ""):  # пусто — как и нет action
        response = client.post(
            "/api/jobs/bulk", json={"action": action, "ids": [job_id]}
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == UI_STRINGS["err_bulk_bad_action"].format(
            action=action
        )
    for bad_body in (
        {"action": "pause"},
        {"action": "pause", "ids": []},
        {"action": "pause", "ids": "nope"},
    ):
        response = client.post("/api/jobs/bulk", json=bad_body)
        assert response.status_code == 400, bad_body
        assert response.json()["detail"] == UI_STRINGS["err_bulk_no_ids"]
    response = client.post(
        "/api/jobs/bulk", json={"action": "pause", "ids": ["no-such-id"]}
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == UI_STRINGS["err_job_not_found"].format(
        id="no-such-id"
    )


# ------------------------------------------------------------------ PATCH / очередь


def test_r3_5_patch_queue_rank(u1_env: U1Env) -> None:
    """R3 §15: PATCH /api/jobs/{id} — единственное поле queue_rank;
    лишние поля — 400; не-целое/отрицательное/bool — 400; терминал — 409;
    неизвестное задание — 404."""
    profile = u1_env.profiles["main"]
    client, repo = profile.client, profile.repo
    book = _upload_book(client, u1_env.books["tiny"])
    job_id = _create_job(client, book)

    response = client.patch(f"/api/jobs/{job_id}", json={"queue_rank": 7})
    assert response.status_code == 200, response.text
    assert response.json() == {"id": job_id, "status": "CREATED", "queue_rank": 7}
    assert repo.get(job_id).queue_rank == 7

    for bad_body in ({"queue_rank": 1, "mode": "bilingual"}, {"priority": 0}):
        response = client.patch(f"/api/jobs/{job_id}", json=bad_body)
        assert response.status_code == 400, bad_body
        assert response.json()["detail"] == UI_STRINGS["err_job_patch_fields"]
    for bad_value in ("3", -1, True, 1.5, None):
        response = client.patch(f"/api/jobs/{job_id}", json={"queue_rank": bad_value})
        assert response.status_code == 400, bad_value
        assert response.json()["detail"] == UI_STRINGS["err_queue_rank_bad"]

    term_id = _create_job(client, book)
    _set_status(profile, term_id, JobStatus.FAILED)
    response = client.patch(f"/api/jobs/{term_id}", json={"queue_rank": 1})
    assert response.status_code == 409, response.text
    assert repo.get(term_id).queue_rank == 0

    response = client.patch("/api/jobs/no-such-id", json={"queue_rank": 1})
    assert response.status_code == 404


def test_r3_6_claim_order_by_queue_rank(u1_env: U1Env) -> None:
    """R3 §15: кандидат воркера — queue_rank DESC, created_at ASC:
    «Вперёд очереди» меняет выбор; равный ранг — FIFO.
    Высокие ранги (100/50) — чтобы кандидаты из других тестов модуля
    (ранг 0) не влияли на выбор."""
    profile = u1_env.profiles["main"]
    client, repo = profile.client, profile.repo
    book = _upload_book(client, u1_env.books["tiny"])
    a = _create_job(client, book)
    b = _create_job(client, book)
    c = _create_job(client, book)
    client.patch(f"/api/jobs/{a}", json={"queue_rank": 100})
    client.patch(f"/api/jobs/{b}", json={"queue_rank": 100})
    client.patch(f"/api/jobs/{c}", json={"queue_rank": 50})
    assert repo.next_claimable().id == a  # равный макс. ранг — created_at ASC
    client.patch(f"/api/jobs/{a}", json={"queue_rank": 0})
    assert repo.next_claimable().id == b
    client.patch(f"/api/jobs/{b}", json={"queue_rank": 0})
    assert repo.next_claimable().id == c


# ------------------------------------------------------------------ CSRF (multipart)


def test_r3_7_multipart_csrf_books(u1_env: U1Env) -> None:
    """R3 T15: multipart CSRF — u3_csrf в теле формы (без заголовка)
    принимается и файл доходит до эндпоинта (body-replay); без токена —
    403 (fail closed). Plus: no-JS-удаление (DELETE, urlencoded-тело)."""
    profile = u1_env.profiles["main"]
    settings, repo = profile.settings, profile.repo
    no_csrf_header = TestClient(profile.app, headers=_auth_header())
    token = csrf_token(settings, "any-user")
    path = u1_env.books["tiny"]
    with path.open("rb") as fh:
        response = no_csrf_header.post(
            "/api/books", files={"file": (path.name, fh, "application/epub+zip")}
        )
    assert response.status_code == 403, response.text
    with path.open("rb") as fh:
        response = no_csrf_header.post(
            "/api/books",
            files={"file": (path.name, fh, "application/epub+zip")},
            data={"u3_csrf": token},
        )
    assert response.status_code == 200, response.text

    # no-JS-удаление: u3_csrf в urlencoded-теле (DELETE — JS-путь;
    # no-JS-форма шлёт POST-алиас — test_r3_14)
    book_name = _upload_book(profile.client, u1_env.books["tiny"])
    job_id = _create_job(profile.client, book_name)
    _set_status(profile, job_id, JobStatus.CANCELLED)
    response = no_csrf_header.request(
        "DELETE", f"/api/jobs/{job_id}", data={}
    )
    assert response.status_code == 403, response.text
    response = no_csrf_header.request(
        "DELETE", f"/api/jobs/{job_id}", data={"u3_csrf": token}
    )
    assert response.status_code == 200, response.text
    assert repo.get(job_id) is None


# ------------------------------------------------------------------ no-JS-маршруты


def test_r3_8_nojs_create_form(u1_env: U1Env) -> None:
    """R3 T15: no-JS-создание — POST /jobs/new (форма листа ?sheet=create,
    БЕЗ X-U3-CSRF: токен — в поле формы): успех — 303 на /jobs, задание
    создано; отказ — 303 обратно на лист с create_err, задание не создано;
    без токена — 403."""
    profile = u1_env.profiles["main"]
    settings, repo = profile.settings, profile.repo
    book = _upload_book(profile.client, u1_env.books["tiny"])
    # реальный no-JS-браузер: Basic-авторизации нет и заголовка CSRF нет —
    # но форма несёт u3_csrf (middleware: urlencoded-ветка, body-replay)
    no_csrf_header = TestClient(profile.app, headers=_auth_header())
    token = csrf_token(settings, "any-user")
    before = len(repo.list_jobs())

    response = no_csrf_header.post(
        "/jobs/new",
        data={
            "book": book,
            "target_language": "ru",
            "mode": "bilingual",
            "u3_csrf": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/jobs"
    assert len(repo.list_jobs()) == before + 1

    response = no_csrf_header.post(
        "/jobs/new",
        data={
            "book": "no_such.epub",
            "target_language": "ru",
            "mode": "bilingual",
            "u3_csrf": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"].startswith("/jobs?sheet=create&create_err=")
    assert len(repo.list_jobs()) == before + 1

    response = no_csrf_header.post(
        "/jobs/new",
        data={"book": book, "target_language": "ru", "mode": "bilingual"},
        follow_redirects=False,
    )
    assert response.status_code == 403, response.text
    assert len(repo.list_jobs()) == before + 1


def test_r3_9_nojs_confirm_form(u1_env: U1Env) -> None:
    """R3 T15: no-JS-подтверждение — POST /jobs/{id}/confirm (форма,
    токен в u3_csrf, без заголовка): успех — 303 на экран задания
    (статус → PLANNING), исходный язык из select записывается;
    неизвестное задание — 404-страница."""
    profile = u1_env.profiles["main"]
    settings, repo = profile.settings, profile.repo
    client = profile.client
    book = _upload_book(client, u1_env.books["tiny"])
    no_csrf_header = TestClient(profile.app, headers=_auth_header())
    token = csrf_token(settings, "any-user")

    job_id = _create_job(client, book)
    _set_status(profile, job_id, JobStatus.NEEDS_CONFIRMATION)

    response = no_csrf_header.post(
        f"/jobs/{job_id}/confirm", data={"u3_csrf": token}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == f"/jobs/{job_id}"
    assert repo.get(job_id).status is JobStatus.PLANNING

    job_id2 = _create_job(client, book)
    _set_status(profile, job_id2, JobStatus.NEEDS_CONFIRMATION)
    response = no_csrf_header.post(
        f"/jobs/{job_id2}/confirm",
        data={"source_language": "fr", "u3_csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert repo.get(job_id2).source_language == "fr"

    response = client.post(
        "/jobs/no-such-id/confirm", data={"u3_csrf": token}, follow_redirects=False
    )
    assert response.status_code == 404


# ------------------------------------------------------------------ ?sheet= страницы


def test_r3_10_sheet_pages(u1_env: U1Env) -> None:
    """R3 §14: no-JS-листы серверными страницами: ?sheet=create (форма
    /jobs/new, предзаполнение retry_of, без дубля <dialog>), ?sheet=export
    (имя книги; неизвестное задание — 404), ?sheet=delete на экране
    задания (POST-форма — браузер не умеет DELETE из формы;
    JS-вариант — контейнер-диалог)."""
    profile = u1_env.profiles["main"]
    client = profile.client
    book = _upload_book(client, u1_env.books["tiny"])
    job_id = _create_job(client, book)

    page = client.get("/jobs?sheet=create")
    assert page.status_code == 200, page.text
    doc = lhtml.fromstring(page.text)
    forms = doc.xpath('//form[@id="u3-create-form"]')
    assert len(forms) == 1
    assert forms[0].get("action") == "/jobs/new"
    assert not doc.xpath('//dialog[@id="u3-sheet-create"]')

    page = client.get(f"/jobs?sheet=create&retry_of={job_id}")
    assert page.status_code == 200, page.text
    doc = lhtml.fromstring(page.text)
    selected = doc.xpath(
        '//form[@id="u3-create-form"]//select[@name="book"]//option[@selected]'
    )
    assert len(selected) == 1 and selected[0].get("value") == book

    _set_status(profile, job_id, JobStatus.CANCELLED)
    page = client.get(f"/jobs?sheet=export&job={job_id}")
    assert page.status_code == 200, page.text
    doc = lhtml.fromstring(page.text)
    assert doc.xpath('//p[@class="u3-export-book"]')
    assert not doc.xpath('//dialog[@id="u3-sheet-export"]')
    assert client.get("/jobs?sheet=export&job=no-such-id").status_code == 404

    page = client.get(f"/jobs/{job_id}?sheet=delete")
    assert page.status_code == 200, page.text
    doc = lhtml.fromstring(page.text)
    forms = doc.xpath(f'//form[@action="/api/jobs/{job_id}"]')
    assert len(forms) == 1, "no-JS-форма удаления не найдена"
    assert forms[0].get("method") == "post"
    submit = forms[0].xpath('.//button[@type="submit"]')
    assert len(submit) == 1
    # formmethod=DELETE невалиден: браузер шлёт форму GET'ом (T15-дефект)
    assert not submit[0].get("formmethod")

    page = client.get(f"/jobs/{job_id}")
    assert page.status_code == 200, page.text
    doc = lhtml.fromstring(page.text)
    assert doc.xpath('//button[@id="u3-del-job-confirm"]')
    assert doc.xpath('//dialog[@id="u3-sheet-delete-job"]')


# ------------------------------------------------------------------ строки списка


def _assert_swipe(row: Any, status: JobStatus, job_id: str) -> None:
    """R3 §11: панель свайпа строки — только допустимые действия + logs
    (queue — только CREATED; delete — только терминальные)."""
    swipe = row.xpath('.//span[@class="u3-row-swipe"]')
    assert len(swipe) == 1, job_id
    swaps = {b.get("data-swap") for b in swipe[0].xpath('.//button[@data-swap]')}
    logs = swipe[0].xpath(f'.//a[@href="/jobs/{job_id}#logs"]')
    assert len(logs) == 1, job_id
    if status in (JobStatus.CREATED, JobStatus.NEEDS_CONFIRMATION, JobStatus.TRANSLATING):
        assert "pause" in swaps, job_id
    if status in (JobStatus.COMPLETED, JobStatus.FAILED):
        assert "delete" in swaps and "cancel" not in swaps, job_id
    else:
        assert "cancel" in swaps and "delete" not in swaps, job_id
    assert ("queue" in swaps) == (status is JobStatus.CREATED), job_id


def test_r3_11_row_actions_per_status(u1_env: U1Env) -> None:
    """R3 §11 (подложка T5): в строке (построитель #1) первичное действие
    по таблице §11 для каждой группы статусов; панель свайпа — только
    допустимое + logs; в формах — скрытый u3_csrf."""
    profile = u1_env.profiles["main"]
    client = profile.client
    book = _upload_book(client, u1_env.books["tiny"])
    cases: list[tuple[JobStatus, str]] = []
    for status in (
        JobStatus.NEEDS_CONFIRMATION,
        JobStatus.CREATED,
        JobStatus.TRANSLATING,
        JobStatus.PAUSED,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
    ):
        job_id = _create_job(client, book)
        if status is not JobStatus.CREATED:
            _set_status(profile, job_id, status)
        cases.append((status, job_id))

    page = client.get("/jobs?group=all")
    assert page.status_code == 200, page.text
    doc = lhtml.fromstring(page.text)

    for status, job_id in cases:
        rows = doc.xpath(f'//article[@data-id="{job_id}"]')
        assert len(rows) == 1, job_id
        row = rows[0]
        assert row.get("data-status") == status.value

        action_el = row.xpath('.//span[@class="u3-row-action"]')
        assert len(action_el) == 1, job_id
        action_el = action_el[0]
        if status is JobStatus.NEEDS_CONFIRMATION:
            form = action_el.xpath('.//form')
            assert len(form) == 1
            assert form[0].get("action") == f"/jobs/{job_id}/confirm"
        elif status in (JobStatus.CREATED, JobStatus.TRANSLATING):
            form = action_el.xpath('.//form')
            assert len(form) == 1
            assert form[0].get("action") == f"/api/jobs/{job_id}/pause"
        elif status is JobStatus.PAUSED:
            form = action_el.xpath('.//form')
            assert len(form) == 1
            assert form[0].get("action") == f"/api/jobs/{job_id}/resume"
        elif status is JobStatus.COMPLETED:
            link = action_el.xpath('.//a[@data-sheet-open="u3-sheet-export"]')
            assert len(link) == 1
            assert link[0].get("data-job") == job_id
        else:  # FAILED
            link = action_el.xpath('.//a[@data-row-retry]')
            assert len(link) == 1
            assert f"retry_of={job_id}" in (link[0].get("href") or "")

        _assert_swipe(row, status, job_id)

        # no-JS-формы несут CSRF (urlencoded-отправка)
        for form in row.xpath('.//form'):
            csrf_inputs = form.xpath('.//input[@name="u3_csrf"]')
            assert len(csrf_inputs) == 1, job_id
            assert csrf_inputs[0].get("value")


def test_r3_12_wait_group_sort(u1_env: U1Env) -> None:
    """R3 §11: группа «Ожидание» в /jobs — queue_rank DESC, created_at ASC
    (серверная сторона; JS-сортировка — та же ключевая функция)."""
    profile = u1_env.profiles["main"]
    client = profile.client
    book = _upload_book(client, u1_env.books["tiny"])
    a = _create_job(client, book)
    b = _create_job(client, book)
    c = _create_job(client, book)
    response = client.patch(f"/api/jobs/{c}", json={"queue_rank": 5})
    assert response.status_code == 200, response.text

    page = client.get("/jobs?group=waiting")
    assert page.status_code == 200, page.text
    positions = [
        page.text.index(f'data-id="{job_id}"') for job_id in (c, a, b)
    ]
    assert positions == sorted(positions), (
        f"поднятое задание {c} должно быть первым, затем FIFO {a}, {b}"
    )


def test_r3_13_cancel_from_paused_immediate(u1_env: U1Env) -> None:
    """R3 §11: отмена PAUSED-задания — немедленный переход (воркер PAUSED
    не исполняет и флаг cancel_requested не применял бы никогда): статус
    сразу CANCELLED, флаг сброшен, строка перехода записана."""
    profile = u1_env.profiles["main"]
    client, repo = profile.client, profile.repo
    book = _upload_book(client, u1_env.books["tiny"])
    job_id = _create_job(client, book)
    _set_status(profile, job_id, JobStatus.PAUSED)

    response = client.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code == 200, response.text
    assert response.json() == {"id": job_id, "status": "CANCELLED"}
    job = repo.get(job_id)
    assert job.status is JobStatus.CANCELLED
    assert job.cancel_requested is False

    rows = profile.db.conn.execute(
        "SELECT from_status, to_status, actor FROM transitions "
        "WHERE job_id = ? ORDER BY at",
        (job_id,),
    ).fetchall()
    assert (rows[-1]["from_status"], rows[-1]["to_status"]) == ("PAUSED", "CANCELLED")
    assert rows[-1]["actor"] == "api"


def test_r3_14_nojs_delete_post_form(u1_env: U1Env) -> None:
    """R3 T15: no-JS-удаление — POST /api/jobs/{id} (форма ?sheet=delete,
    токен в u3_csrf, без заголовка). Браузер не может отправить DELETE
    из HTML-формы (formmethod=DELETE невалиден — запрос уходит GET'ом),
    поэтому API принимает POST как алиас DELETE: терминал — удалено;
    незавершённое — 409; без токена — 403 (fail closed)."""
    profile = u1_env.profiles["main"]
    settings, repo = profile.settings, profile.repo
    no_csrf_header = TestClient(profile.app, headers=_auth_header())
    token = csrf_token(settings, "any-user")
    book = _upload_book(profile.client, u1_env.books["tiny"])

    work_id = _create_job(profile.client, book)
    response = no_csrf_header.post(f"/api/jobs/{work_id}", data={"u3_csrf": token})
    assert response.status_code == 409, response.text
    assert repo.get(work_id) is not None

    job_id = _create_job(profile.client, book)
    _set_status(profile, job_id, JobStatus.CANCELLED)
    response = no_csrf_header.post(f"/api/jobs/{job_id}", data={"u3_csrf": token})
    assert response.status_code == 200, response.text
    assert repo.get(job_id) is None

    job_id2 = _create_job(profile.client, book)
    _set_status(profile, job_id2, JobStatus.CANCELLED)
    response = no_csrf_header.post(f"/api/jobs/{job_id2}", data={})
    assert response.status_code == 403, response.text
    assert repo.get(job_id2) is not None
