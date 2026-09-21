"""T-U3-1..15 — переработка Web UI (TZ U3 §7).

Окружение — как U1Env (mock-сервер, профили, книги), новых тестовых
механизмов не вводится (TZ §7). T-U3-15 — реальный ``app.js`` исполняется
Node-харнессом со стабами DOM/fetch: поведение автообновления (3.11.47-50)
проверяется по последовательности запросов к API.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import time
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from lxml import html as lhtml

from app.core.config import UiSection
from app.domain.job import JobStatus
from app.llm.prompts import VERSION_FILE_RE, PromptResolver
from app.storage.database import transaction
from app.web.auth import csrf_token
from app.web.strings import UI_STRINGS

ROOT = Path(__file__).resolve().parents[1]
_TESTS_DIR = str(Path(__file__).resolve().parent)
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from test_l3_glossary_fix import _start_worker  # noqa: E402
from test_u1_web import (  # noqa: E402
    AUTH_SECRET,
    U1Env,
    _auth_header,
    _create_job,
    _make_u1_storage,
    _upload_book,
    _wait_job_terminal,
)

_CYRILLIC = re.compile(r"[\u0400-\u04FF]")


def _api_mutating(app: Any) -> list[tuple[str, str]]:
    """(method, path) мутирующих /api-маршрутов (с развёртыванием
    ленивых ``_IncludedRouter`` — include_router не разворачивается в
    ``app.routes`` напрямую)."""

    def flatten(routes: list[Any]) -> list[Any]:
        flat: list[Any] = []
        for route in routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                flat.extend(flatten(list(original.routes)))
            else:
                flat.append(route)
        return flat

    mutating: list[tuple[str, str]] = []
    for route in flatten(list(app.routes)):
        path = str(getattr(route, "path", ""))
        if not path.startswith("/api"):
            continue
        for method in sorted(getattr(route, "methods", None) or set()):
            if method in {"POST", "PUT", "DELETE", "PATCH"}:
                mutating.append((method, path))
    return mutating


# ------------------------------------------------------------------ T-U3-1


def test_u3_1_no_ui_literals_outside_strings() -> None:
    """U3-1: 0 текстовых литералов интерфейса вне strings.py.

    - ``app/web/*.py`` (кроме ``strings.py``): кириллицы нет в строковых
      константах (докстринги и комментарии не являются UI-литералами);
    - шаблоны: кириллицы нет вне Jinja-тегов (``{{ }}`` / ``{% %}`` / ``{# #}``);
    - ``app.js``: кириллицы нет вообще (видимые строки — только U3_STRINGS).
    """
    web_dir = ROOT / "app" / "web"
    py_files = [p for p in sorted(web_dir.glob("*.py")) if p.name != "strings.py"]
    assert py_files, "app/web/*.py не найдены"
    for path in py_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstring_ids: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                body = node.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstring_ids.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstring_ids:
                    continue
                assert not _CYRILLIC.search(node.value), (
                    f"{path.name}:{node.lineno}: кириллица в строковой константе: "
                    f"{node.value!r}"
                )
    for path in sorted((web_dir / "templates").glob("*.html")):
        stripped = re.sub(
            r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}",
            "",
            path.read_text(encoding="utf-8"),
            flags=re.DOTALL,
        )
        assert not _CYRILLIC.search(stripped), f"{path.name}: кириллица вне Jinja-тегов"
    js = (web_dir / "static" / "app.js").read_text(encoding="utf-8")
    assert not _CYRILLIC.search(js), "app.js: кириллица (видимые строки — только U3_STRINGS)"


# ------------------------------------------------------------------ T-U3-2


def test_u3_2_table_card_parity(u1_env: U1Env) -> None:
    """U3-2: для каждой таблицы — поля карточки == столбцы (число и порядок),
    ``<th scope="col">``; минимум 5 таблиц. R2: /jobs и /books — строки
    (§6/§10), дубль таблица/карточки на них снят (осталось 5)."""
    profile = u1_env.profiles["main"]
    client, repo = profile.client, profile.repo
    book = _upload_book(client, u1_env.books["tiny"])
    job_id = _create_job(client, book)
    # хотя бы один переход — чтобы страница задания несла таблицу transitions
    repo.transition(job_id, JobStatus.VALIDATING, "u3-t2")
    checked = 0
    for url in ("/jobs", f"/jobs/{job_id}", "/books", "/languages", "/models", "/prompts"):
        page = client.get(url)
        assert page.status_code == 200, (url, page.status_code)
        doc = lhtml.fromstring(page.text)
        tables = doc.xpath("//table[@class='u3-table']")
        card_divs = doc.xpath("//div[@class='u3-cards']")
        assert len(tables) == len(card_divs), (
            f"{url}: таблиц {len(tables)} != карточных списков {len(card_divs)}"
        )
        for table, card_div in zip(tables, card_divs, strict=True):
            ths = table.xpath(".//thead//th")
            assert ths, f"{url}: в таблице нет th"
            assert all(t.get("scope") == "col" for t in ths), (
                f"{url}: th без scope=col: {[t.attrib for t in ths]}"
            )
            columns = [t.text_content().strip() for t in ths]
            articles = card_div.xpath(".//article[@class='u3-card']")
            assert articles, f"{url}: нет карточек"
            for article in articles:
                dts = [
                    field.xpath("./dt")[0].text_content().strip()
                    for field in article.xpath(".//div[@class='u3-field']")
                ]
                assert dts == columns, f"{url}: поля карточки {dts} != столбцы {columns}"
            checked += 1
    assert checked >= 5, f"проверено таблиц: {checked} < 5"


# ------------------------------------------------------------------ T-U3-3


def _strip_root_blocks(css: str) -> str:
    """R1: вырезать ВСЕ токеновые блоки — базовый :root, :root внутри
    @media (prefers-color-scheme: dark) и :root[data-theme="light|dark"]
    (4 блока; литералы цвета разрешены только в них — T18)."""
    spans: list[tuple[int, int]] = []
    for m in re.finditer(r":root(?:\[data-theme=\"(?:light|dark)\"\])?\s*\{", css):
        start = m.end() - 1
        depth, i = 1, start + 1
        while i < len(css) and depth:
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
            i += 1
        spans.append((m.start(), i))
    assert spans, "токеновые блоки :root не найдены"
    for start, end in reversed(spans):
        css = css[:start] + css[end:]
    return css


def test_u3_3_css_invariants() -> None:
    """U3-3: числовые цвета вне токеновых блоков — 0; width/min-width >
    360 px — 0. R1: блоков :root стало 4 (светлая + тёмная темы, T18)."""
    css = (ROOT / "app" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    assert re.search(r":root\s*\{", css), "нет блока :root"
    outside = _strip_root_blocks(css)
    for match in re.finditer(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", outside):
        pytest.fail(f"числовой цвет вне токеновых блоков: {match.group(0)!r}")
    for match in re.finditer(r"(?:^|[\s,{;])(?:min-)?width\s*:\s*(\d+(?:\.\d+)?)px", outside):
        value = float(match.group(1))
        assert value <= 360, f"ширина {value} px > 360 px: {match.group(0)!r}"


# ------------------------------------------------------------------ T-U3-4


def test_u3_4_delete_terminal_job(u1_env: U1Env) -> None:
    """U3-4: удаление терминального задания — 5 таблиц к 0, каталог удалён,
    ответ несёт число строк и байты каталога."""
    profile = u1_env.profiles["main"]
    client, repo, settings = profile.client, profile.repo, profile.settings
    book = _upload_book(client, u1_env.books["tiny"])
    job_id = _create_job(client, book)
    # cancel — асинхронный запрос (переход выполняет воркер); без воркера в
    # тесте терминальный статус ставим прямым доменным переходом
    repo.transition(job_id, JobStatus.CANCELLED, "u3-t4")
    job_dir = settings.job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.log").write_bytes(b"x" * 123)

    def counts() -> dict[str, int]:
        conn = profile.db.conn
        return {
            "jobs": conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()[0],
            "transitions": conn.execute(
                "SELECT COUNT(*) FROM transitions WHERE job_id = ?", (job_id,)
            ).fetchone()[0],
            "stage_results": conn.execute(
                "SELECT COUNT(*) FROM stage_results WHERE job_id = ?", (job_id,)
            ).fetchone()[0],
            "resolved_prompts": conn.execute(
                "SELECT COUNT(*) FROM resolved_prompts WHERE job_id = ?", (job_id,)
            ).fetchone()[0],
            "job_tokens": conn.execute(
                "SELECT COUNT(*) FROM job_tokens WHERE job_id = ?", (job_id,)
            ).fetchone()[0],
        }

    before = counts()
    assert before["jobs"] == 1
    response = client.delete(f"/api/jobs/{job_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == job_id
    assert body["bytes"] == 123
    assert counts() == dict.fromkeys(before, 0)
    assert not job_dir.exists() and not job_dir.is_symlink()
    assert repo.get(job_id) is None


# ------------------------------------------------------------------ T-U3-5


def test_u3_5_delete_non_terminal_rejected(u1_env: U1Env) -> None:
    """U3-5: удаление в TRANSLATING → 409 + причина verbatim; строки БД и
    каталог побайтово/построчно неизменны."""
    profile = u1_env.profiles["main"]
    client, repo, settings = profile.client, profile.repo, profile.settings
    book = _upload_book(client, u1_env.books["tiny"])
    job_id = _create_job(client, book)
    for target in (
        JobStatus.VALIDATING,
        JobStatus.NEEDS_CONFIRMATION,
        JobStatus.PLANNING,
        JobStatus.TRANSLATING,
    ):
        repo.transition(job_id, target, "u3-t5")
    job_dir = settings.job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / "job.log"
    log_path.write_bytes(b"y" * 77)
    conn = profile.db.conn
    row_sql = "SELECT * FROM jobs WHERE id = ?"
    tr_sql = "SELECT * FROM transitions WHERE job_id = ? ORDER BY id"
    row_before = dict(conn.execute(row_sql, (job_id,)).fetchone())
    transitions_before = [dict(r) for r in conn.execute(tr_sql, (job_id,))]
    log_before = log_path.read_bytes()
    response = client.delete(f"/api/jobs/{job_id}")
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == UI_STRINGS["err_job_not_terminal"].format(
        status="TRANSLATING"
    )
    assert dict(conn.execute(row_sql, (job_id,)).fetchone()) == row_before
    assert [dict(r) for r in conn.execute(tr_sql, (job_id,))] == transitions_before
    assert log_path.read_bytes() == log_before
    assert repo.get(job_id) is not None


# ------------------------------------------------------------------ T-U3-6


def test_u3_6_purge_dry_run_vs_fact(u1_env: U1Env) -> None:
    """U3-6: purge — перечень dry-run == фактически удалённые job_id; задание
    младше ``ui.purge_min_age_days`` не удаляется; повторный токен → 409."""
    profile = u1_env.profiles["main"]
    client, repo, settings = profile.client, profile.repo, profile.settings
    book = _upload_book(client, u1_env.books["tiny"])
    old_id = _create_job(client, book)
    new_id = _create_job(client, book)
    for job_id in (old_id, new_id):
        repo.transition(job_id, JobStatus.CANCELLED, "u3-t6")
    # «старое» задание откатываем на 2 дня — за порог ui.purge_min_age_days
    conn = profile.db.conn
    with transaction(conn):
        conn.execute(
            "UPDATE jobs SET updated_at = ? WHERE id = ?",
            (time.time() - 2 * 86400, old_id),
        )
    payload = {"statuses": ["CANCELLED"], "book": None, "pair": None}
    step1 = client.post("/api/jobs/purge", json=payload)
    assert step1.status_code == 200, step1.text
    dry = step1.json()
    assert dry["dry_run"] is True
    assert dry["job_ids"] == [old_id]
    assert dry["count"] == 1
    assert new_id not in dry["job_ids"]
    step2 = client.post("/api/jobs/purge", json={**payload, "confirm_token": dry["confirm_token"]})
    assert step2.status_code == 200, step2.text
    done = step2.json()
    assert done["dry_run"] is False
    assert done["purged"] == [old_id]
    assert repo.get(old_id) is None
    assert not settings.job_dir(old_id).exists()
    assert repo.get(new_id) is not None  # молодое задание выжило
    # токен при изменившемся составе — 409
    stale = client.post("/api/jobs/purge", json={**payload, "confirm_token": dry["confirm_token"]})
    assert stale.status_code == 409
    assert stale.json()["detail"] == UI_STRINGS["err_purge_token"]


# ------------------------------------------------------------------ T-U3-7


def test_u3_7_logs_clear_quality_untouched(u1_env: U1Env) -> None:
    """U3-7: logs/clear — job.log/errors.log усекаются + строка факта;
    quality.log побайтово не тронут; маршрута его усечения не существует."""
    profile = u1_env.profiles["main"]
    client, settings = profile.client, profile.settings
    book = _upload_book(client, u1_env.books["tiny"])
    job_id = _create_job(client, book)
    job_dir = settings.job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    job_log = job_dir / "job.log"
    errors_log = job_dir / "errors.log"
    quality_log = job_dir / "quality.log"
    job_log.write_bytes(b"j" * 300)
    errors_log.write_bytes(b"e" * 150)
    quality_before = b"q" * 250
    quality_log.write_bytes(quality_before)
    response = client.post(f"/api/jobs/{job_id}/logs/clear")
    assert response.status_code == 200, response.text
    assert response.json() == {"cleared": True, "job_bytes": 300, "errors_bytes": 150}
    assert errors_log.read_bytes() == b""
    lines = job_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, lines
    assert "any-user" in lines[0]
    assert "300" in lines[0] and "150" in lines[0]
    assert quality_log.read_bytes() == quality_before
    # 3.5.26: маршрута, усекающего quality.log, не существует
    quality_mutating = [
        (method, path) for method, path in _api_mutating(profile.app) if "quality" in path
    ]
    assert quality_mutating == [], quality_mutating


# ------------------------------------------------------------------ T-U3-8


def test_u3_8_prompt_new_version_and_resolution(u1_env: U1Env) -> None:
    """U3-8: новая версия v(N+1) создана, vN побайтово идентичен, резолвер
    выбирает N+1, ``resolved_prompts`` фиксирует N+1 на полном прогоне.

    Целевая задача — ``book_style`` (L2-глоссарий, generic-уровень: стилевой
    профиль книги не зависит от пары языков): задача есть в реестре и в
    справочных промптах и разрешается при каждом полном прогоне новой книги.
    """
    profile = u1_env.profiles["main"]
    client, repo, settings = profile.client, profile.repo, profile.settings
    prompts_root = settings.prompts_dir
    task_dir = prompts_root / "book_style"
    versions = sorted(
        int(match.group(1))
        for path in task_dir.iterdir()
        if (match := VERSION_FILE_RE.match(path.name)) is not None
    )
    assert versions, "нет версий book_style в справочных промптах"
    v_first = task_dir / f"v{versions[0]}.yaml"
    v_first_before = v_first.read_bytes()
    new_content = (
        v_first_before.decode("utf-8").rstrip("\n")
        + "\n# U3 test marker (versioning test)\n"
    )
    response = client.post(
        "/api/prompts",
        json={"task": "book_style", "level": "generic", "content": new_content},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    next_version = versions[-1] + 1
    assert body["version"] == next_version
    assert body["path"] == f"book_style/v{next_version}.yaml"
    assert v_first.read_bytes() == v_first_before  # побайтово идентичен
    assert (prompts_root / body["path"]).read_text(encoding="utf-8") == new_content
    # резолвер выбирает новую версию (generic — базовый каталог задачи)
    resolver = PromptResolver(prompts_root)
    resolved = resolver.resolve("book_style", "en", "ru", pair_required=False)
    assert resolved.version == next_version
    assert resolved.file == f"book_style/v{next_version}.yaml"
    assert resolved.level == "generic"
    # полный прогон фиксирует N+1 в resolved_prompts (E4-2 — провенанс промпта)
    book = _upload_book(client, u1_env.books["tiny"])
    job_id = _create_job(client, book)
    # CREATED-остатки предыдущих тестов модуля: отменяем, чтобы run_once
    # (одно задание за запуск) обработал ровно это задание
    for other in repo.list_jobs():
        if other.id != job_id and other.status is JobStatus.CREATED:
            repo.transition(other.id, JobStatus.CANCELLED, "u3-t8-backlog")
    proc = _start_worker(profile.cfg, profile.env_file, u1_env.base / "worker_u3_8.log")
    u1_env.workers.append(proc)
    try:
        facts = _wait_job_terminal(client, job_id, timeout=240)
    finally:
        proc.kill()
        proc.wait()
    assert facts["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS"), facts["status"]
    row = profile.db.conn.execute(
        "SELECT version, file, level FROM resolved_prompts "
        "WHERE job_id = ? AND task = 'book_style'",
        (job_id,),
    ).fetchone()
    assert row is not None, "resolved_prompts: нет строки задачи book_style"
    assert row[0] == next_version
    assert row[1] == f"book_style/v{next_version}.yaml"
    assert row[2] == "generic"


# ------------------------------------------------------------------ T-U3-9


def test_u3_9_prompt_write_rejections(u1_env: U1Env) -> None:
    """U3-9: три отказа записи промпта (невалидный YAML, нет обязательного
    поля, неизвестная задача) → 400; созданных файлов 0, LLM-запросов 0."""
    profile = u1_env.profiles["main"]
    client = profile.client
    prompts_root = profile.settings.prompts_dir

    def snapshot() -> list[str]:
        return sorted(
            str(p.relative_to(prompts_root))
            for p in prompts_root.rglob("*")
            if p.is_file()
        )

    before_files = snapshot()
    requests_before = len(u1_env.server.requests_log())
    cases = (
        {
            "task": "analysis_vocabulary",
            "level": "generic",
            "content": "system: [broken\nuser_template: x",
        },  # невалидный YAML
        {
            "task": "analysis_vocabulary",
            "level": "generic",
            "content": "system: 'ok'\n",
        },  # нет user_template
        {
            "task": "no_such_task",
            "level": "generic",
            "content": "system: 'ok'\nuser_template: 'ok'\n",
        },  # неизвестная задача
    )
    for index, payload in enumerate(cases):
        response = client.post("/api/prompts", json=payload)
        assert response.status_code == 400, (index, response.text)
    assert snapshot() == before_files, "созданы файлы при отказе записи"
    assert len(u1_env.server.requests_log()) == requests_before, "LLM-запросы при 400"


# ------------------------------------------------------------------ T-U3-10


def test_u3_10_last_version_delete_rejected(u1_env: U1Env) -> None:
    """U3-10: удаление последней версии уровня разрешения → 409, файл на месте."""
    profile = u1_env.profiles["main"]
    client = profile.client
    prompts_root = profile.settings.prompts_dir
    version_files = [
        p for p in prompts_root.rglob("v*.yaml") if VERSION_FILE_RE.match(p.name)
    ]
    assert version_files, "нет файлов версий промптов"
    single = next(
        (
            p
            for p in version_files
            if sum(1 for sib in p.parent.iterdir() if VERSION_FILE_RE.match(sib.name)) == 1
        ),
        None,
    )
    assert single is not None, "не нашлось уровня с единственной версией"
    rel = single.relative_to(prompts_root).as_posix()
    response = client.delete("/api/prompts/version", params={"path": rel})
    assert response.status_code == 409, response.text
    assert single.is_file(), "последняя версия уровня удалена"


# ------------------------------------------------------------------ T-U3-11


def test_u3_11_access_matrix(u1_env: U1Env) -> None:
    """U3-10/3.9: каждый мутирующий маршрут × (нет секрета → 403; секрет без
    CSRF → 403; секрет + токен → не 403/401)."""
    main = u1_env.profiles["main"]
    dead = u1_env.profiles["dead"]
    mutating = _api_mutating(main.app)
    assert mutating, "мутирующие маршруты не найдены"

    def concrete(path: str) -> str:
        return re.sub(r"\{[^}]+\}", "x-no-such", path)

    # 1) нет секрета: 403 + причина verbatim (тело middleware: error/message)
    for method, path in mutating:
        response = dead.client.request(method, concrete(path))
        assert response.status_code == 403, (method, path, response.status_code)
        assert response.json()["message"] == UI_STRINGS["err_mutation_requires_secret"]
    # 2) секрет без CSRF: 403
    no_csrf = TestClient(main.app, headers=_auth_header())
    for method, path in mutating:
        response = no_csrf.request(method, concrete(path))
        assert response.status_code == 403, (method, path, response.status_code, response.text)
    # 3) секрет + токен: не 403 и не 401 (запрос дошёл до обработчика)
    for method, path in mutating:
        response = main.client.request(method, concrete(path))
        assert response.status_code not in (401, 403), (
            method,
            path,
            response.status_code,
            response.text,
        )


# ------------------------------------------------------------------ T-U3-12


def test_u3_12_delete_unsafe_path_rejected(u1_env: U1Env) -> None:
    """U3-5/3.5.23: каталог задания вне storage.root (символическая ссылка) —
    500, ничего не удалено."""
    profile = u1_env.profiles["main"]
    client, repo, settings = profile.client, profile.repo, profile.settings
    book = _upload_book(client, u1_env.books["tiny"])
    job_id = _create_job(client, book)
    repo.transition(job_id, JobStatus.CANCELLED, "u3-t12")
    job_dir = settings.job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    outside = u1_env.base / f"outside_{job_id}"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "keep.bin").write_bytes(b"keep" * 50)
    shutil.rmtree(job_dir)
    job_dir.symlink_to(outside)
    response = client.delete(f"/api/jobs/{job_id}")
    assert response.status_code == 500, response.text
    assert repo.get(job_id) is not None
    assert outside.is_dir() and (outside / "keep.bin").is_file()
    assert job_dir.is_symlink()
    # уборка: ссылка и строка задания
    job_dir.unlink()
    repo.delete_job(job_id)


# ------------------------------------------------------------------ T-U3-13


def test_u3_13_pagination_and_filters(u1_env: U1Env) -> None:
    """TZ T-U3-13: сумма страниц == list_jobs без фильтра; фильтр по статусу —
    точное подмножество. Размер страницы — из ui (YAML) отдельным профилем."""
    if "u3small" not in u1_env.profiles:
        settings, db, cfg, env_file = _make_u1_storage(
            u1_env.base,
            "storage_u3small",
            u1_env.server.url,
            env_extra=f"SERVER_AUTH_SECRET={AUTH_SECRET}\n",
            yaml_extra="ui:\n  page_size: 2\n",
        )
        u1_env.add_profile(
            "u3small",
            settings,
            db,
            cfg,
            env_file,
            client_headers={
                **_auth_header(),
                "X-U3-CSRF": csrf_token(settings, "any-user"),
            },
        )
    profile = u1_env.profiles["u3small"]
    client, repo, settings = profile.client, profile.repo, profile.settings
    assert settings.ui.page_size == 2
    book = _upload_book(client, u1_env.books["tiny"])
    ids = [_create_job(client, book) for _ in range(5)]
    for job_id in ids[:3]:
        repo.transition(job_id, JobStatus.CANCELLED, "u3-t13")
    # обход всех страниц limit/offset (3.4.16)
    seen: list[str] = []
    offset = 0
    while True:
        response = client.get("/api/jobs", params={"limit": 2, "offset": offset})
        assert response.status_code == 200
        rows = response.json()
        if not rows:
            break
        assert len(rows) <= 2
        seen.extend(row["id"] for row in rows)
        offset += 2
        assert offset <= 100, "цикл страниц не сходится"
    assert sorted(seen) == sorted(ids)
    assert len(seen) == len(repo.list_jobs())
    # фильтр по статусу — точное подмножество
    response = client.get("/api/jobs", params={"status": "CANCELLED"})
    assert sorted(row["id"] for row in response.json()) == sorted(ids[:3])
    response = client.get("/api/jobs", params={"status": "CREATED"})
    assert sorted(row["id"] for row in response.json()) == sorted(ids[3:])


# ------------------------------------------------------------------ T-U3-14


_U3_14_FORBIDDEN = ("completed", "success", "100%", "healthy", "verified", "ready", "done")


def _u3_14_check_a11y(url: str, text: str) -> None:
    """T-U3-14: доступность — label/aria-label у контролов, th scope."""
    doc = lhtml.fromstring(text)
    for el in doc.xpath("//input | //select | //textarea"):
        if el.get("type") == "hidden":
            continue
        labelled = (
            el.get("aria-label")
            or (el.get("id") and doc.xpath(f"//label[@for='{el.get('id')}']"))
            or el.xpath("./ancestor::label")
        )
        assert labelled, (url, "контрол без label/aria-label", el.attrib)
    for el in doc.xpath("//button"):
        assert el.text_content().strip() or el.get("aria-label"), (
            url,
            "кнопка без подписи",
            el.attrib,
        )
    for th in doc.xpath("//th"):
        assert th.get("scope") in ("col", "row"), (url, "th без scope", th.attrib)


def _u3_14_check_honesty(url: str, text: str, allowed: set[str]) -> None:
    """T-U3-14: честность — запрещённые слова только как verbatim-значения
    из БД. R2: сканируем только видимый текст (не сырой HTML): ключи групп
    в URL (group=done), классы, data-*, JSON в <script> (ключи вида
    jobs_seg_done) — не статусные заявления."""
    doc = lhtml.fromstring(text)
    for node in doc.xpath("//script | //style"):
        node.getparent().remove(node)
    lowered = doc.text_content().lower()
    for word in _U3_14_FORBIDDEN:
        if word.lower() in lowered:
            assert any(word.lower() in value.lower() for value in allowed), (url, word)


def test_u3_14_a11y_and_honesty(u1_env: U1Env) -> None:
    """TZ T-U3-14: доступность (label/aria-label, th scope) и честность
    (слова п. 46 — только verbatim-значения из БД) по HTML всех страниц."""
    profile = u1_env.profiles["main"]
    client, repo = profile.client, profile.repo
    book = _upload_book(client, u1_env.books["tiny"])
    job_id = _create_job(client, book)
    repo.transition(job_id, JobStatus.CANCELLED, "u3-t14")
    urls = (
        "/jobs",
        f"/jobs/{job_id}",
        "/books",
        "/languages",
        "/models",
        "/prompts",
        "/settings",
    )
    pages = {url: client.get(url).text for url in urls}
    for url, text in pages.items():
        assert text.lstrip().lower().startswith("<!doctype html"), url
    # доступность
    for url, text in pages.items():
        _u3_14_check_a11y(url, text)
    # честность: допустимый словарь — ВСЕ значения статусов (фильтр/
    # пачка-чистки рисуют полный набор, а не только статусы существующих
    # заданий), тексты ошибок, причины переходов. R2: процент — реальный
    # прогресс из БД (строка задания).
    allowed: set[str] = {status.value for status in JobStatus}
    for job in repo.list_jobs():
        if job.error_message:
            allowed.add(job.error_message)
        # R2: строка задания показывает процент прогресса — фактическое значение
        allowed.add(f"{round((job.progress or 0.0) * 100)}%")
    for row in profile.db.conn.execute("SELECT reason FROM transitions"):
        allowed.add(str(row[0]))
    for url, text in pages.items():
        _u3_14_check_honesty(url, text, allowed)


# ------------------------------------------------------------------ T-U3-15

#: Node-харнесс: исполняет реальный app.js со стабами DOM/fetch и печатает
#: один JSON-строкой каждый запрос к API ({"t": ms, "url": ...}) и финальную
#: строку ({"final": true, ...}). Новых механизмов вне node нет.
_HARNESS_JS = """
import { readFileSync } from "node:fs";

const [scenario, appJsPath, stringsFile, uiFile] = process.argv.slice(2);
const t0 = Date.now();
const calls = [];

function makeEl(id) {
  const el = {
    id: id || "",
    value: "",
    disabled: false,
    open: false,
    dataset: {},
    files: null,
    style: {},
    children: [],
    parentNode: null,
    _attrs: {},
    _listeners: {},
    _text: "",
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      toggle(c, force) {
        const want = force === undefined ? !this._set.has(c) : Boolean(force);
        if (want) this._set.add(c); else this._set.delete(c);
        return want;
      },
      contains(c) { return this._set.has(c); },
    },
    getAttribute(n) { return n in el._attrs ? el._attrs[n] : null; },
    setAttribute(n, v) { el._attrs[n] = String(v); },
    removeAttribute(n) { delete el._attrs[n]; },
    hasAttribute(n) { return n in el._attrs; },
    addEventListener(type, fn) { (el._listeners[type] = el._listeners[type] || []).push(fn); },
    querySelector(sel) {
      if (sel && sel.indexOf(".u3-list-body") !== -1) return el._listBody || null;
      return null;
    },
    querySelectorAll() { return []; },
    appendChild(c) {
      if (c && typeof c === "object") { c.parentNode = el; el.children.push(c); }
      return c;
    },
    append() { for (let i = 0; i < arguments.length; i++) el.appendChild(arguments[i]); },
    prepend(c) {
      if (c && typeof c === "object") { c.parentNode = el; el.children.unshift(c); }
      return c;
    },
    remove() {
      if (el.parentNode) {
        const i = el.parentNode.children.indexOf(el);
        if (i >= 0) el.parentNode.children.splice(i, 1);
        el.parentNode = null;
      }
    },
    reset() {},
    showModal() { el.open = true; },
    close() { el.open = false; },
    focus() {},
    blur() {},
  };
  Object.defineProperty(el, "textContent", {
    configurable: true,
    get() { return el._text; },
    set(v) { el._text = String(v); },
  });
  Object.defineProperty(el, "innerHTML", {
    configurable: true,
    get() { return el._text; },
    set(v) { el._text = String(v); el.children.length = 0; },
  });
  return el;
}

const els = {};
const documentStub = {
  hidden: false,
  visibilityState: "visible",
  getElementById(id) {
    if (!els[id]) els[id] = makeEl(id);
    return els[id];
  },
  createElement(tag) {
    if (tag === "template") {
      const t = makeEl("template");
      t.content = { firstElementChild: null };
      Object.defineProperty(t, "innerHTML", {
        get() { return t._text; },
        set(v) { t._text = String(v); t.content.firstElementChild = makeEl("row"); },
      });
      return t;
    }
    return makeEl(tag);
  },
  querySelector(sel) {
    if (sel.startsWith("meta")) {
      return { getAttribute: (n) => (n === "content" ? "test-csrf" : null) };
    }
    return null;
  },
  querySelectorAll() {
    return [];
  },
  _listeners: {},
  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  },
  dispatch(type) {
    for (const fn of this._listeners[type] || []) fn();
  },
};

globalThis.document = documentStub;
globalThis.window = {
  location: { search: "", href: "http://stub/jobs", reload() {} },
  addEventListener() {},
};
globalThis.history = { pushState() {}, back() {}, replaceState() {} };
globalThis.alert = () => {};
globalThis.confirm = () => true;

function scenarioMode(n) {
  const job = (status) => {
    const ok = status === "COMPLETED";
    return {
      id: "job-1",
      status,
      status_group: ok ? "ok" : "run",
      status_glyph: ok ? "✓" : "◐",
      group: "now",
      source_language: "en",
      target_language: "ru",
      mode: "bilingual",
      progress: 0.5,
      progress_pct: ok ? 100 : 50,
      progress_display: "x",
      alignment_coverage: null,
      error: null,
      book_name: "b.epub",
      elapsed_seconds: 600,
      created_at: 0,
      updated_at: 0,
      created_at_human: "t",
      updated_at_human: "t",
    };
  };
  if (scenario === "visible") return [job("TRANSLATING")];
  if (scenario === "terminal") return [job("COMPLETED")];
  if (scenario === "restart") return [job("COMPLETED")];
  if (scenario === "errors") return n <= 2 ? [job("TRANSLATING")] : "error";
  throw new Error("unknown scenario: " + scenario);
}

globalThis.fetch = async (url, opts) => {
  const t = Date.now() - t0;
  const m = (opts && opts.method) || "GET";
  calls.push({ t, url, m });
  console.log(JSON.stringify({ t, url, m }));
  if (m === "POST" && url === "/api/jobs") {
    return { ok: true, status: 200,
      text: async () => JSON.stringify({ id: "job-9", status: "CREATED" }) };
  }
  const mode = scenarioMode(calls.length);
  if (mode === "error") {
    return { ok: false, status: 500, text: async () => JSON.stringify({ detail: "boom" }) };
  }
  return { ok: true, status: 200, text: async () => JSON.stringify(mode) };
};

documentStub.getElementById("u3-strings").textContent = readFileSync(stringsFile, "utf8");
documentStub.getElementById("u3-ui").textContent = readFileSync(uiFile, "utf8");
const u1jobs = documentStub.getElementById("u1-jobs");
u1jobs.dataset = { u1Fetch: "/api/jobs", u1Render: "jobs", u1Group: "all" };
u1jobs._listBody = makeEl("u3-list-body");

if (scenario === "restart") {
  /* wizard form: one checked book radio + named selects, so the real
     submit handler runs POST /api/jobs -> jobsRefresh() */
  const f = documentStub.getElementById("u3-create-form");
  const radio = {
    checked: true,
    value: "b.epub",
    dataset: { title: "B", detected: "en", dc: "en" },
    addEventListener() {},
  };
  f.querySelectorAll = () => [radio];
  f.target_language = { value: "ru", addEventListener() {} };
  f.mode = { value: "bilingual" };
  f.learning_level = { value: "", addEventListener() {} };
  f.learning_cefr = { value: "" };
}

(0, eval)(readFileSync(appJsPath, "utf8"));
documentStub.dispatch("DOMContentLoaded");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function main() {
  if (scenario === "visible") {
    await sleep(600);
    documentStub.hidden = true;
    documentStub.visibilityState = "hidden";
    documentStub.dispatch("visibilitychange");
    await sleep(800);
    documentStub.hidden = false;
    documentStub.visibilityState = "visible";
    documentStub.dispatch("visibilitychange");
    await sleep(600);
  } else if (scenario === "errors") {
    await sleep(2800);
  } else if (scenario === "terminal") {
    await sleep(1500);
  } else if (scenario === "restart") {
    await sleep(500);
    els["u3-create-form"]._listeners["submit"][0]({ preventDefault() {} });
    await sleep(500);
  }
  const refresh = els["u3-jobs-refresh"] ? els["u3-jobs-refresh"].textContent : "";
  console.log(JSON.stringify({ final: true, calls: calls.length, refresh }));
}
main().then(() => process.exit(0));
"""


def _run_harness(
    node: str, tmp_path: Path, scenario: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Один прогон харнесса; возврат (запросы, финальная строка)."""
    harness = tmp_path / "harness.mjs"
    harness.write_text(_HARNESS_JS, encoding="utf-8")
    strings_file = tmp_path / "strings.json"
    strings_file.write_text(json.dumps(UI_STRINGS, ensure_ascii=False), encoding="utf-8")
    ui_file = tmp_path / "ui.json"
    # интервалы ускорены (0.2 с) для детерминированного таймлайна сценария
    ui_file.write_text(
        json.dumps(
            UiSection(refresh_seconds=0.2, refresh_seconds_max=0.8).model_dump()
        ),
        encoding="utf-8",
    )
    app_js = ROOT / "app" / "web" / "static" / "app.js"
    result = subprocess.run(
        [node, str(harness), scenario, str(app_js), str(strings_file), str(ui_file)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    entries = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    requests = [entry for entry in entries if not entry.get("final")]
    final = next(entry for entry in entries if entry.get("final"))
    return requests, final


_NODE = shutil.which("node")


@pytest.mark.skipif(_NODE is None, reason="node не установлен — харнесс T-U3-15")
@pytest.mark.parametrize("scenario", ["visible", "errors", "terminal", "restart"])
def test_u3_15_autorefresh_harness(tmp_path: Path, scenario: str) -> None:
    """T-U3-15: автообновление — пауза при document.hidden (один немедленный
    опрос при возврате), экспоненциальный backoff до ui.refresh_seconds_max
    при ошибке API (сообщение verbatim), остановка на терминальном статусе.
    R4 «restart»: остановленный (все задания терминальные) список
    пробуждает jobsRefresh после создания (мастер → POST /api/jobs) —
    иначе новая строка не появляется без перезагрузки страницы (дефект
    T6-замера). Проверка — по последовательности запросов к API."""
    assert _NODE is not None, "node не установлен"
    requests, final = _run_harness(_NODE, tmp_path, scenario)
    times = [int(c["t"]) for c in requests]
    if scenario == "visible":
        # нормальный ритм до hidden (0/200/400), тишина в окне hidden,
        # один немедленный опрос при возврате видимости
        assert times, "запросов нет"
        assert times[0] < 100
        assert len([t for t in times if t < 500]) >= 3, f"ритм до hidden сломан: {times}"
        assert not any(500 <= t <= 1350 for t in times), f"запросы при hidden: {times}"
        assert any(1350 <= t <= 1650 for t in times), f"нет немедленного опроса: {times}"
    elif scenario == "errors":
        # 200/200 (OK), затем backoff 400/800/800 (потолок ui.refresh_seconds_max)
        assert len(times) >= 5, f"слишком мало запросов: {times}"
        gaps = [b - a for a, b in pairwise(times)]
        assert abs(gaps[0] - 200) <= 150, gaps
        assert abs(gaps[1] - 200) <= 150, gaps
        assert abs(gaps[2] - 400) <= 200, gaps
        assert abs(gaps[3] - 800) <= 300, gaps
        if len(gaps) > 4:
            assert abs(gaps[4] - 800) <= 300, gaps
        # видимое сообщение об ошибке verbatim
        assert "boom" in final["refresh"], final["refresh"]
    elif scenario == "terminal":
        assert len(times) == 1, f"запросы после терминального статуса: {times}"
    else:  # restart
        posts = [c for c in requests if c.get("m") == "POST"]
        gets = [
            c
            for c in requests
            if c.get("m") != "POST" and c["url"].startswith("/api/jobs")
        ]
        assert len(posts) == 1, f"POST /api/jobs не отправлен: {requests}"
        # до submit — ровно один фетч (стоп на терминальном, R2 §10)
        assert len([c for c in gets if int(c["t"]) < 450]) == 1, (
            f"первый фетч/стоп сломан: {[int(c['t']) for c in gets]}"
        )
        # после создания — полный рефетч списка (jobsRefresh пробудил)
        assert len(gets) == 2, f"список не обновился после создания: {requests}"
