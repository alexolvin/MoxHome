# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""U3 (ТЗ_04 §U3): verbatim-захваты критериев CONTROL U3.

Запуск: python tests/u3_evidence.py
Захваты: docs/CONTROL/evidence/U3/u3-1…u3-14 (verbatim, Rule 3).

Сценарии (mock-сервер D3 для LLM; реальные spaCy/EPUBCheck/SQLite/FastAPI):
  u3-1   литералы интерфейса вне strings.py = 0 (AST .py + шаблоны + app.js)
  u3-2   паритет таблица/карточки: 6 таблиц, порядок полей == порядок столбцов
  u3-3   CSS: числовые цвета вне :root = 0; width/min-width > 360 px = 0
  u3-4   удаление терминального задания: 4 таблицы × 0, каталог отсутствует
  u3-5   удаление незавершённого → 409 + причина verbatim, расхождение 0
  u3-6   purge: перечень dry-run == фактически удалённые (расхождение 0)
  u3-7   очистка логов: 0 байт + строка факта; quality.log побайтово идентичен
  u3-8   новая версия промпта: v(N+1), vN побайтово идентичен, resolved_prompts
  u3-9   три отказа записи промпта → 400, файлов 0, LLM-запросов 0
  u3-10  матрица доступа: каждый мутирующий маршрут × 3 случая
  u3-11  доступность: label/aria-label, th scope, контраст ≥ 4.5:1, тап 44 px
  u3-12  честность: 7 слов × все страницы, допустимо только verbatim из БД
  u3-13  регрессия: evidence U1 (10) + U2 (4) на коде U3 = 14/14
  u3-14  Rule 4: Languages/Prompts/Jobs с данными по 5 исходным языкам
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import sys
import tempfile
import time
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

import lxml.html as lhtml
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "probe"))

from mock_llm_server import MockLLMServer  # noqa: E402
from test_l3_glossary_fix import _kill_all, _start_worker  # noqa: E402
from test_u1_web import (  # noqa: E402
    U1Profile,
    _auth_header,
    _create_job,
    _synth_u1_slow_book,
    _synth_u1_tiny_book,
    _upload_book,
    _wait_job_terminal,
)
from test_u3_web import _api_mutating  # noqa: E402
from u1_evidence import (  # noqa: E402
    _WORKERS as _U1_WORKERS,
)
from u1_evidence import (  # noqa: E402
    _profile as _u1_profile,
)
from u1_evidence import (  # noqa: E402
    ev_t_u1_10,
    ev_u1_1,
    ev_u1_2,
    ev_u1_3,
    ev_u1_4,
    ev_u1_5,
    ev_u1_6,
    ev_u1_7,
    ev_u1_8,
    ev_u1_9,
)
from u2_evidence import (  # noqa: E402
    _BOOKS as _U2_BOOKS,
)
from u2_evidence import (  # noqa: E402
    _WORKERS as _U2_WORKERS,
)
from u2_evidence import (  # noqa: E402
    _profile as _u2_profile,
)
from u2_evidence import (  # noqa: E402
    ev_t_u2_4,
    ev_u2_1,
    ev_u2_2,
    ev_u2_3,
)

from app.domain.job import JobStatus  # noqa: E402
from app.llm.prompts import VERSION_FILE_RE, PromptResolver  # noqa: E402
from app.storage.database import transaction  # noqa: E402
from app.web.strings import UI_STRINGS  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "U3"
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
VERSIONS = f"python={sys.version.split()[0]} spacy={pkg_version('spacy')}"

_CYRILLIC = re.compile(r"[\u0400-\u04FF]")
_FORBIDDEN = ("completed", "success", "100%", "healthy", "verified", "ready", "done")
_PAGE_URLS = ("jobs", "job_page", "books", "languages", "models", "prompts", "settings")

_WORKERS: list[Any] = []


def header() -> str:
    return f"дата: {STAMP}\nверсии: {VERSIONS}\n\n"


def evidence(name: str, content: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print(f"  [evidence] {path.relative_to(ROOT)}")


def _verdict_ok(content: str) -> bool:
    # Итог — последний токен строки (строки вида «ВЕРДИКТ: … — PASS» или
    # «ВЕРДИКТ: PASS»); в тексте описания допустимы слова-статусы
    # (например, «FAILED + причина в UI … — PASS» в t-u1-10).
    for line in content.splitlines():
        if line.startswith("ВЕРДИКТ:"):
            return line.strip().rsplit(maxsplit=1)[-1] == "PASS"
    return False


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "—"


def _pages_text(client: Any, job_id: str) -> dict[str, str]:
    urls = {
        "jobs": "/jobs",
        "job_page": f"/jobs/{job_id}",
        "books": "/books",
        "languages": "/languages",
        "models": "/models",
        "prompts": "/prompts",
        "settings": "/settings",
    }
    return {key: client.get(url).text for key, url in urls.items()}


def _job_rows(profile: U1Profile, job_id: str) -> dict[str, Any]:
    conn = profile.db.conn
    return {
        "jobs": [
            dict(row)
            for row in conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        ],
        "transitions": [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM transitions WHERE job_id = ? ORDER BY id", (job_id,)
            )
        ],
        "stage_results": [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM stage_results WHERE job_id = ?", (job_id,)
            )
        ],
    }


# ------------------------------------------------------------------ u3-1


def ev_u3_1() -> str:
    lines = [header(), "K: U3-1 — текстовые литералы интерфейса вне strings.py", ""]
    web_dir = ROOT / "app" / "web"
    findings: list[str] = []
    py_files = [p for p in sorted(web_dir.glob("*.py")) if p.name != "strings.py"]
    lines.append(f".py-файлов app/web (кроме strings.py): {len(py_files)}")
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
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstring_ids
                and _CYRILLIC.search(node.value)
            ):
                findings.append(f"{path.name}:{node.lineno}: {node.value!r}")
    n_tpl = 0
    for path in sorted((web_dir / "templates").glob("*.html")):
        n_tpl += 1
        stripped = re.sub(
            r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}",
            "",
            path.read_text(encoding="utf-8"),
            flags=re.DOTALL,
        )
        if _CYRILLIC.search(stripped):
            findings.append(f"{path.name}: кириллица вне Jinja-тегов")
    js = (web_dir / "static" / "app.js").read_text(encoding="utf-8")
    if _CYRILLIC.search(js):
        findings.append("app.js: кириллица")
    lines.append(f"шаблонов проверено: {n_tpl}; app.js: {'проверен' if js else 'НЕТ'}")
    lines.append("")
    lines.append(f"найдено литералов вне strings.py: {len(findings)}")
    for item in findings[:20]:
        lines.append(f"  {item}")
    ok = not findings
    lines.append(
        f"\nВЕРДИКТ: литералов вне strings.py: {len(findings)} — "
        f"{'PASS' if ok else 'FAIL'}"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ u3-2


def ev_u3_2(main: U1Profile, tiny: Path) -> str:
    lines = [
        header(),
        "K: U3-2 — паритет: поля карточки == столбцы (число и порядок)",
        "",
    ]
    client, repo = main.client, main.repo
    book = _upload_book(client, tiny)
    job_id = _create_job(client, book)
    repo.transition(job_id, JobStatus.VALIDATING, "u3-ev-2")
    checked = 0
    mismatches = 0
    for url in (
        "/jobs",
        f"/jobs/{job_id}",
        "/books",
        "/languages",
        "/models",
        "/prompts",
    ):
        page = client.get(url)
        doc = lhtml.fromstring(page.text)
        tables = doc.xpath("//table[@class='u3-table']")
        card_divs = doc.xpath("//div[@class='u3-cards']")
        if len(tables) != len(card_divs):
            mismatches += 1
            lines.append(f"{url}: таблиц {len(tables)} != карточных списков {len(card_divs)}")
            continue
        for idx, (table, card_div) in enumerate(zip(tables, card_divs, strict=True)):
            ths = table.xpath(".//thead//th")
            columns = [t.text_content().strip() for t in ths]
            scopes_ok = all(t.get("scope") == "col" for t in ths)
            articles = card_div.xpath(".//article[@class='u3-card']")
            fields_ok = True
            for article in articles:
                dts = [
                    field.xpath("./dt")[0].text_content().strip()
                    for field in article.xpath(".//div[@class='u3-field']")
                ]
                if dts != columns:
                    fields_ok = False
            if not (scopes_ok and fields_ok and columns):
                mismatches += 1
            checked += 1
            lines.append(
                f"| {url} | таблица #{idx + 1} | столбцов: {len(columns)} | "
                f"полей карточки: {len(columns) if fields_ok else '≠'} | "
                f"scope=col: {'да' if scopes_ok else 'НЕТ'} | "
                f"порядок: {'совпадает' if fields_ok else 'НЕТ'} |"
            )
    lines.append("")
    lines.append(f"таблиц проверено: {checked}; расхождений: {mismatches}")
    ok = mismatches == 0 and checked >= 6
    lines.append(
        f"\nВЕРДИКТ: {checked} таблиц (порог ≥ 6), расхождений {mismatches} — "
        f"{'PASS' if ok else 'FAIL'}"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ u3-3


def _css_rules(css: str) -> list[tuple[str, str]]:
    """(selector, тело) блоков, включая вложенные в @media."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    rules: list[tuple[str, str]] = []

    def scan(text: str, prefix: str) -> None:
        i = 0
        while True:
            brace = text.find("{", i)
            if brace == -1:
                return
            selector = (prefix + text[i:brace]).strip()
            depth = 1
            j = brace + 1
            while j < len(text) and depth:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            body = text[brace + 1 : j - 1]
            if selector.startswith("@media"):
                scan(body, "")
            else:
                rules.append((selector, body))
            i = j

    scan(css, "")
    return rules


def ev_u3_3() -> str:
    lines = [header(), "K: U3-3 — CSS: цвета только в :root; ширины ≤ 360 px", ""]
    css = (ROOT / "app" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    outside = re.sub(r":root\s*\{[^}]*\}", "", css, flags=re.DOTALL)
    color_hits = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", outside)
    widths: list[tuple[str, str, float]] = []
    for selector, body in _css_rules(css):
        for match in re.finditer(
            r"(?:^|;|\s)(?:min-)?width\s*:\s*(\d+(?:\.\d+)?)px", body
        ):
            widths.append(
                (selector.split(",")[0].strip(), match.group(0).strip(), float(match.group(1)))
            )
    lines.append(f"числовых цветов вне :root: {len(color_hits)}")
    for item in color_hits[:10]:
        lines.append(f"  {item}")
    lines.append("")
    lines.append(f"деклараций width/min-width (px): {len(widths)}")
    lines.append("| селектор | декларация | px | ≤ 360 |")
    lines.append("|---|---|---|---|")
    bad_widths = 0
    for selector, decl, value in widths:
        if value > 360:
            bad_widths += 1
        ok_w = "да" if value <= 360 else "НЕТ"
        lines.append(f"| {selector[:48]} | {decl} | {value:.0f} | {ok_w} |")
    max_width = max((v for _s, _d, v in widths), default=0.0)
    ok = not color_hits and bad_widths == 0
    lines.append(f"\nмаксимальная ширина: {max_width:.0f} px")
    lines.append(
        f"\nВЕРДИКТ: цветов вне :root {len(color_hits)}, ширин > 360 px: {bad_widths} — "
        f"{'PASS' if ok else 'FAIL'}"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ u3-4


def ev_u3_4(main: U1Profile, tiny: Path) -> str:
    lines = [header(), "K: U3-4 — удаление терминального задания (БД + каталог)", ""]
    client, repo, settings = main.client, main.repo, main.settings
    book = _upload_book(client, tiny)
    job_id = _create_job(client, book)
    repo.transition(job_id, JobStatus.CANCELLED, "u3-ev-4")
    job_dir = settings.job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.log").write_bytes(b"e" * 123)

    # jobs: PK — `id`; остальные таблицы ссылаются через `job_id` (схема database.py)
    def counts() -> dict[str, int]:
        conn = main.db.conn
        result: dict[str, int] = {}
        for table in ("jobs", "transitions", "job_tokens", "stage_results"):
            column = "id" if table == "jobs" else "job_id"
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = ?", (job_id,)
            ).fetchone()
            result[table] = int(row["n"]) if row is not None else 0
        return result

    before = counts()
    dir_before = job_dir.exists()
    response = client.delete(f"/api/jobs/{job_id}")
    body = response.json()
    after = counts()
    lines.append(f"задание: {job_id} (статус CANCELLED, job.log = 123 байта)")
    lines.append("ответ DELETE /api/jobs/{id} (verbatim JSON):")
    lines.append(json.dumps(body, ensure_ascii=False, indent=2))
    lines.append("")
    lines.append("| таблица | до | после |")
    lines.append("|---|---|---|")
    for table in ("jobs", "transitions", "job_tokens", "stage_results"):
        lines.append(f"| {table} | {before[table]} | {after[table]} |")
    lines.append(f"| каталог {job_dir.name}/ | {'есть' if dir_before else 'нет'} | "
                 f"{'есть' if job_dir.exists() else 'ОТСУТСТВУЕТ'} |")
    lines.append(f"repo.get(job_id) после: {repo.get(job_id)!r}")
    ok = (
        response.status_code == 200
        and all(v == 0 for v in after.values())
        and not job_dir.exists()
    )
    lines.append(
        f"\nВЕРДИКТ: 4 таблицы × 0, каталог отсутствует — {'PASS' if ok else 'FAIL'}"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ u3-5


def ev_u3_5(main: U1Profile, tiny: Path) -> str:
    lines = [
        header(),
        "K: U3-5 — удаление незавершённого задания → 409, данные неизменны",
        "",
    ]
    client, repo, settings = main.client, main.repo, main.settings
    book = _upload_book(client, tiny)
    job_id = _create_job(client, book)
    for status, reason in (
        (JobStatus.VALIDATING, "u3-ev-5-1"),
        (JobStatus.NEEDS_CONFIRMATION, "u3-ev-5-2"),
        (JobStatus.PLANNING, "u3-ev-5-3"),
        (JobStatus.TRANSLATING, "u3-ev-5-4"),
    ):
        repo.transition(job_id, status, reason)
    job_dir = settings.job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.log").write_bytes(b"t" * 64)
    rows_before = _job_rows(main, job_id)
    dir_bytes_before = (job_dir / "job.log").read_bytes()
    response = client.delete(f"/api/jobs/{job_id}")
    detail = response.json().get("detail", "")
    expected = UI_STRINGS["err_job_not_terminal"].format(status="TRANSLATING")
    rows_after = _job_rows(main, job_id)
    dir_bytes_after = (job_dir / "job.log").read_bytes()
    lines.append(f"задание: {job_id} (статус TRANSLATING)")
    lines.append(f"статус ответа: {response.status_code}")
    lines.append(f"причина (verbatim): {detail!r}")
    lines.append(f"ожидаемая строка:  {expected!r}")
    lines.append(f"строки БД (jobs/transitions/stage_results) неизменны: "
                 f"{'да' if rows_before == rows_after else 'НЕТ'}")
    lines.append(f"job.log побайтово идентичен: "
                 f"{'да' if dir_bytes_before == dir_bytes_after else 'НЕТ'}")
    ok = (
        response.status_code == 409
        and detail == expected
        and rows_before == rows_after
        and dir_bytes_before == dir_bytes_after
    )
    lines.append(f"\nВЕРДИКТ: 1/1 отказ, расхождение 0 — {'PASS' if ok else 'FAIL'}")
    return "\n".join(lines)


# ------------------------------------------------------------------ u3-6


def ev_u3_6(main: U1Profile, tiny: Path) -> str:
    lines = [header(), "K: U3-6 — purge: перечень dry-run == фактически удалённые", ""]
    client, repo = main.client, main.repo
    book = _upload_book(client, tiny)
    old_id = _create_job(client, book)
    new_id = _create_job(client, book)
    for job_id in (old_id, new_id):
        repo.transition(job_id, JobStatus.CANCELLED, "u3-ev-6")
    with transaction(main.db.conn):
        main.db.conn.execute(
            "UPDATE jobs SET updated_at = ? WHERE id = ?",
            (time.time() - 2 * 86400, old_id),
        )
    dry = client.post(
        "/api/jobs/purge",
        json={"statuses": ["CANCELLED"], "book": None, "pair": None},
    ).json()
    lines.append("шаг 1 — dry-run (verbatim JSON):")
    lines.append(json.dumps(dry, ensure_ascii=False, indent=2))
    listed = list(dry.get("job_ids", []))
    confirm = client.post(
        "/api/jobs/purge",
        json={
            "statuses": ["CANCELLED"],
            "book": None,
            "pair": None,
            "confirm_token": dry["confirm_token"],
        },
    ).json()
    lines.append("")
    lines.append("шаг 2 — подтверждение (verbatim JSON):")
    lines.append(json.dumps(confirm, ensure_ascii=False, indent=2))
    purged = list(confirm.get("purged", []))
    lines.append("")
    lines.append(f"перечень dry-run: {listed}")
    lines.append(f"удалено:         {purged}")
    diff = sorted(set(listed) ^ set(purged))
    old_alive = repo.get(old_id) is None
    new_alive = repo.get(new_id) is not None
    lines.append(f"старое задание (2 дня) удалено: {'да' if old_alive else 'НЕТ'}")
    lines.append(f"новое задание (свежее) сохранено: {'да' if new_alive else 'НЕТ'}")
    lines.append(f"расхождение (симметричная разность): {diff if diff else '0'}")
    ok = not diff and purged == [old_id] and old_alive and new_alive
    lines.append(f"\nВЕРДИКТ: расхождение {len(diff)} — {'PASS' if ok else 'FAIL'}")
    return "\n".join(lines)


# ------------------------------------------------------------------ u3-7


def ev_u3_7(main: U1Profile, tiny: Path) -> str:
    lines = [header(), "K: U3-7 — очистка логов; quality.log не тронут", ""]
    client, settings = main.client, main.settings
    book = _upload_book(client, tiny)
    job_id = _create_job(client, book)
    job_dir = settings.job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    job_log = job_dir / "job.log"
    errors_log = job_dir / "errors.log"
    quality_log = job_dir / "quality.log"
    job_log.write_bytes(b"j" * 300)
    errors_log.write_bytes(b"e" * 150)
    quality_log.write_bytes(b"q" * 250)
    quality_sha_before = _sha(quality_log)
    response = client.post(f"/api/jobs/{job_id}/logs/clear")
    body = response.json()
    job_after = job_log.read_bytes()
    errors_after = errors_log.read_bytes()
    quality_sha_after = _sha(quality_log)
    lines.append(f"задание: {job_id}")
    lines.append("ответ POST /api/jobs/{id}/logs/clear (verbatim JSON):")
    lines.append(json.dumps(body, ensure_ascii=False, indent=2))
    lines.append("")
    lines.append(f"errors.log после: {len(errors_after)} байт (до: 150)")
    lines.append(f"job.log после: {len(job_after)} байт (до: 300)")
    lines.append("job.log после очистки (строка факта, verbatim):")
    lines.extend(job_after.decode("utf-8").splitlines())
    lines.append("")
    lines.append(f"quality.log: sha256 до = {quality_sha_before}")
    lines.append(f"quality.log: sha256 после = {quality_sha_after}")
    identical = "да" if quality_sha_before == quality_sha_after else "НЕТ"
    lines.append(f"quality.log побайтово идентичен: {identical}")
    quality_mutating = [
        (m, p) for m, p in _api_mutating(main.app) if "quality" in p
    ]
    mutating_txt = f"{quality_mutating}" if quality_mutating else "0"
    lines.append(f"мутирующих /api-маршрутов с «quality» в пути: {mutating_txt}")
    fact_ok = (
        len(errors_after) == 0
        and len(job_after.splitlines()) == 1
        and "300" in job_after.decode("utf-8")
        and "150" in job_after.decode("utf-8")
    )
    ok = (
        response.status_code == 200
        and body.get("cleared") is True
        and fact_ok
        and quality_sha_before == quality_sha_after
        and not quality_mutating
    )
    lines.append(
        f"\nВЕРДИКТ: job.log/errors.log = 0 + строка факта, quality.log идентичен, "
        f"маршрута усечения нет — {'PASS' if ok else 'FAIL'}"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ u3-8


def ev_u3_8(main: U1Profile, tiny: Path) -> str:
    lines = [
        header(),
        "K: U3-8 — новая версия промпта: v(N+1) создан, vN идентичен, "
        "resolved_prompts фиксирует N+1",
        "",
    ]
    client, repo, settings = main.client, main.repo, main.settings
    task_dir = settings.prompts_dir / "book_style"
    versions = sorted(
        int(m.group(1))
        for p in task_dir.iterdir()
        if (m := VERSION_FILE_RE.match(p.name)) is not None
    )
    first = task_dir / f"v{versions[0]}.yaml"
    first_before = first.read_bytes()
    new_content = first_before.decode("utf-8").rstrip("\n") + "\n# U3 evidence (versioning)\n"
    response = client.post(
        "/api/prompts",
        json={"task": "book_style", "level": "generic", "content": new_content},
    )
    body = response.json()
    next_version = versions[-1] + 1
    lines.append(f"задача: book_style (generic); версий до: {versions}")
    lines.append(f"ответ POST /api/prompts (verbatim JSON): {json.dumps(body, ensure_ascii=False)}")
    v1_ok = "да" if first.read_bytes() == first_before else "НЕТ"
    lines.append(f"v{versions[0]} побайтово идентичен: {v1_ok}")
    resolver = PromptResolver(settings.prompts_dir)
    resolved = resolver.resolve("book_style", "en", "ru", pair_required=False)
    lines.append(
        f"резолвер: version={resolved.version} file={resolved.file} level={resolved.level} "
        f"(ожидалось {next_version})"
    )
    book = _upload_book(client, tiny)
    job_id = _create_job(client, book)
    for other in repo.list_jobs():
        if other.id != job_id and other.status is JobStatus.CREATED:
            repo.transition(other.id, JobStatus.CANCELLED, "u3-ev-8-backlog")
    proc = _start_worker(main.cfg, main.env_file, main.settings.storage.root / "ev_worker_u3_8.log")
    _WORKERS.append(proc)
    try:
        facts = _wait_job_terminal(client, job_id, timeout=300)
    finally:
        proc.kill()
        proc.wait()
    lines.append(f"полный прогон: задание {job_id}, статус {facts['status']}")
    row = main.db.conn.execute(
        "SELECT version, file, level FROM resolved_prompts "
        "WHERE job_id = ? AND task = 'book_style'",
        (job_id,),
    ).fetchone()
    lines.append(
        f"resolved_prompts (book_style): version={row[0]} file={row[1]} level={row[2]} "
        f"(ожидалось {next_version})"
    )
    ok = (
        response.status_code == 200
        and body["version"] == next_version
        and body["path"] == f"book_style/v{next_version}.yaml"
        and first.read_bytes() == first_before
        and resolved.version == next_version
        and row is not None
        and row[0] == next_version
        and row[1] == f"book_style/v{next_version}.yaml"
    )
    lines.append(f"\nВЕРДИКТ: v(N+1) создан и применён, vN идентичен — {'PASS' if ok else 'FAIL'}")
    return "\n".join(lines)


# ------------------------------------------------------------------ u3-9


def ev_u3_9(main: U1Profile, server: MockLLMServer) -> str:
    lines = [
        header(),
        "K: U3-9 — отказы записи промпта: 400, файлов 0, LLM-запросов 0",
        "",
    ]
    client = main.client
    prompts_root = main.settings.prompts_dir

    def tree() -> int:
        return len(list(prompts_root.rglob("*.yaml")))

    n_files_before = tree()
    n_llm_before = len(server.requests_log())
    cases = (
        ("невалидный YAML", {"task": "analysis_vocabulary", "level": "generic",
                             "content": "system: [broken\nuser_template: x"}),
        ("нет обязательного поля", {"task": "analysis_vocabulary", "level": "generic",
                                    "content": "system: 'ok'\n"}),
        ("неизвестная задача", {"task": "no_such_task", "level": "generic",
                                "content": "system: 'ok'\nuser_template: 'ok'\n"}),
    )
    statuses: list[int] = []
    for title, payload in cases:
        response = client.post("/api/prompts", json=payload)
        statuses.append(response.status_code)
        lines.append(f"кейс «{title}»: HTTP {response.status_code}, "
                     f"detail: {response.json().get('detail')!r}")
    n_files_after = tree()
    n_llm_after = len(server.requests_log())
    lines.append("")
    lines.append(f"файлов в prompts/: до = {n_files_before}, после = {n_files_after}")
    lines.append(f"LLM-запросов: до = {n_llm_before}, после = {n_llm_after}")
    ok = (
        all(s == 400 for s in statuses)
        and n_files_before == n_files_after
        and n_llm_before == n_llm_after
    )
    lines.append(
        f"\nВЕРДИКТ: 400 × {statuses.count(400)}/3, файлов создано 0, LLM-запросов 0 — "
        f"{'PASS' if ok else 'FAIL'}"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ u3-10


def ev_u3_10(main: U1Profile, dead: U1Profile) -> str:
    lines = [
        header(),
        "K: U3-10 — матрица доступа: каждый мутирующий маршрут × 3 случая",
        "",
    ]
    mutating = _api_mutating(main.app)
    assert mutating, "мутирующие маршруты не найдены"
    concrete = {
        path: re.sub(r"\{[^}]+\}", "x-no-such", path) for _m, path in mutating
    }
    no_csrf = TestClient(main.app, headers=_auth_header())
    lines.append(f"мутирующих маршрутов: {len(mutating)}")
    lines.append("| маршрут | нет секрета | секрет без CSRF | секрет + токен |")
    lines.append("|---|---|---|---|")
    bad = 0
    for method, path in mutating:
        url = concrete[path]
        s1 = dead.client.request(method, url).status_code
        s2 = no_csrf.request(method, url).status_code
        s3 = main.client.request(method, url).status_code
        row_ok = s1 == 403 and s2 == 403 and s3 not in (401, 403)
        if not row_ok:
            bad += 1
        lines.append(f"| {method} {path} | {s1} | {s2} | {s3} (запрос дошёл до обработчика) |")
    # причина verbatim в одном случае (каждый маршрут проверен на 403 выше)
    sample = dead.client.request(mutating[0][0], concrete[mutating[0][1]])
    message = sample.json().get("message", "")
    lines.append("")
    lines.append(f"причина 403 (verbatim, первый маршрут): {message!r}")
    lines.append(f"ожидаемая строка: "
                 f"{UI_STRINGS['err_mutation_requires_secret']!r}")
    ok = bad == 0 and message == UI_STRINGS["err_mutation_requires_secret"]
    lines.append(
        f"\nВЕРДИКТ: {len(mutating)}/{len(mutating)} маршрутов × 3 случая, "
        f"расхождений: {bad} — {'PASS' if ok else 'FAIL'}"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ u3-11


def _wcag_contrast(fg: str, bg: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def luminance(color: str) -> float:
        h = color.lstrip("#")
        r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

    l1, l2 = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def _page_a11y_issues(key: str, text: str) -> list[tuple[str, str]]:
    """(вид, деталь) по одной странице; вид: «label» или «th-scope»."""
    doc = lhtml.fromstring(text)
    issues: list[tuple[str, str]] = []
    for el in doc.xpath("//input | //select | //textarea"):
        if el.get("type") == "hidden":
            continue
        labeled = (
            el.get("aria-label")
            or (el.get("id") and doc.xpath(f"//label[@for='{el.get('id')}']"))
            or el.xpath("ancestor::label")
        )
        if not labeled:
            issues.append((
                "label",
                f"{key}: контрол без label/aria-label: <{el.tag} {el.attrib}>",
            ))
    for el in doc.xpath("//button"):
        if not (el.text_content().strip() or el.get("aria-label")):
            issues.append(("label", f"{key}: кнопка без текста/aria-label"))
    for th in doc.xpath("//table//th"):
        if th.get("scope") not in ("col", "row"):
            issues.append(("th-scope", f"{key}: <th> без scope: {th.attrib}"))
    return issues


def ev_u3_11(main: U1Profile, tiny: Path) -> str:
    lines = [header(), "K: U3-11 — доступность (статически по HTML и CSS)", ""]
    client = main.client
    book = _upload_book(client, tiny)
    job_id = _create_job(client, book)
    issues: list[tuple[str, str]] = []
    for key, text in _pages_text(client, job_id).items():
        issues.extend(_page_a11y_issues(key, text))
    no_label = sum(1 for kind, _d in issues if kind == "label")
    no_scope = sum(1 for kind, _d in issues if kind == "th-scope")
    for _kind, detail in issues[:20]:
        lines.append(detail)
    lines.append(f"контролов без label/aria-label: {no_label}")
    lines.append(f"<th> без scope: {no_scope}")
    # контраст токенов (V1 V-T10, порог Д111: основной текст >= 7,
    # вторичный/статусные >= 4.5, границы/ввод >= 3; критерий — 0
    # нарушений в тёмной теме; светлая — справочно)
    css = (ROOT / "app" / "web" / "static" / "style.css").read_text(encoding="utf-8")

    def _tokens_of(block: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for decl in block.split(";"):
            if ":" in decl:
                k, v = decl.split(":", 1)
                out[k.strip()] = v.strip()
        return out

    # V1 §4: тёмная тема — :root по умолчанию; светлая —
    # :root[data-theme="light"] (переопределения поверх тёмного набора).
    root_match = re.search(r":root\s*\{([^}]*)\}", css, flags=re.DOTALL)
    assert root_match is not None, "нет блока :root в style.css (тёмная тема)"
    dark = _tokens_of(root_match.group(1))
    light_match = re.search(
        r":root\[data-theme=\"light\"\]\s*\{([^}]*)\}", css, flags=re.DOTALL
    )
    assert light_match is not None, "нет :root[data-theme=light] в style.css"
    light = {**dark, **_tokens_of(light_match.group(1))}
    # V1 §4 токены (Д111): основной текст 7, вторичный/статусные 4.5,
    # границы/ввод 3; статусные пары — на собственном фоне и на карточке
    pairs = (
        ("--fg", "--bg", 7.0),
        ("--fg", "--s1", 7.0),
        ("--fg", "--s2", 7.0),
        ("--fg", "--s3", 7.0),
        ("--fgm", "--bg", 4.5),
        ("--fgm", "--s1", 4.5),
        ("--fgm", "--s2", 4.5),
        ("--fgs", "--s1", 4.5),
        ("--acf", "--bg", 4.5),
        ("--acf", "--s1", 4.5),
        ("--st-w", "--st-wb", 4.5),
        ("--st-r", "--st-rb", 4.5),
        ("--st-o", "--st-ob", 4.5),
        ("--st-a", "--st-ab", 4.5),
        ("--st-e", "--st-eb", 4.5),
        ("--st-w", "--s1", 4.5),
        ("--st-r", "--s1", 4.5),
        ("--st-o", "--s1", 4.5),
        ("--st-a", "--s1", 4.5),
        ("--st-e", "--s1", 4.5),
        ("--lns", "--bg", 3.0),
        ("--lns", "--s1", 3.0),
        ("--lns", "--s2", 3.0),
        ("--oac", "--ac", 4.5),
        ("--os", "--acs", 4.5),
        ("--oct", "--cta", 4.5),
    )
    low_contrast = 0
    for theme_name, tokens in (("тёмная", dark), ("светлая", light)):
        lines.append("")
        lines.append(f"| пара | контраст | порог | {theme_name} |")
        lines.append("|---|---|---|---|")
        for fg, bg, threshold in pairs:
            ratio = _wcag_contrast(tokens[fg], tokens[bg])
            if theme_name == "тёмная" and ratio < threshold:
                low_contrast += 1
            lines.append(
                f"| {fg} / {bg} | {ratio:.2f} | >= {threshold:g} "
                f"| {'да' if ratio >= threshold else 'НЕТ'} |"
            )
    # тап-цели: для каждого интерактивного базового селектора существует
    # правило, применимое к нему, с min-height: var(--u3-tap); правила,
    # не объявляющие размер (cursor/font/opacity), нарушением не являются.
    rules = _css_rules(css)
    bases = (
        "button", ".btn", "select", "input", "textarea", "summary",
        ".u3-check", ".u3-tab", ".u3-appbar-back",
    )

    def _applies(part: str, base: str) -> bool:
        if part == base:
            return True
        return (
            part.startswith(base)
            and len(part) > len(base)
            and part[len(base)] in "[.:"
        )

    no_tap = [
        base
        for base in bases
        if not any(
            "min-height: var(--u3-tap)" in body.replace("min-height:var(", "min-height: var(")
            and any(_applies(p.strip(), base) for p in selector.split(","))
            for selector, body in rules
        )
    ]
    px_small = [
        f"{sel}: {m.group(0)}"
        for sel, body in rules
        for m in re.finditer(r"(?:min-)?height:\s*(\d+(?:\.\d+)?)px", body)
        if float(m.group(1)) < 44
        and any(any(_applies(p.strip(), b) for b in bases) for p in sel.split(","))
    ]
    tap = light.get("--u3-tap", "")
    lines.append("")
    lines.append(
        f"тап-цель: --u3-tap = {tap}; "
        f"область: {', '.join(bases)} (ссылки <a> — проверка аудита T3)"
    )
    lines.append(f"базовых селекторов без min-height: var(--u3-tap): "
                 f"{no_tap if no_tap else '0'}")
    lines.append(f"height/min-height в px < 44 на интерактивных элементах: "
                 f"{px_small if px_small else '0'}")
    ok = (
        no_label == 0
        and no_scope == 0
        and low_contrast == 0
        and tap == "44px"
        and not no_tap
        and not px_small
    )
    lines.append(
        f"\nВЕРДИКТ: {no_label}/{no_scope}/{low_contrast}/"
        f"{len(no_tap) + len(px_small)} (label/scope/контраст/тап) — "
        f"{'PASS' if ok else 'FAIL'}"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ u3-12


def ev_u3_12(main: U1Profile, tiny: Path) -> str:
    lines = [header(), "K: U3-12 — честность: 7 слов × все страницы", ""]
    client, repo = main.client, main.repo
    book = _upload_book(client, tiny)
    job_id = _create_job(client, book)
    allowed: set[str] = {status.value for status in JobStatus}
    for job in repo.list_jobs():
        if job.error_message:
            allowed.add(job.error_message)
    for row in main.db.conn.execute("SELECT reason FROM transitions"):
        allowed.add(str(row[0]))
    lines.append(f"допустимый словарь (статусы + ошибки + причины): "
                 f"{sorted(allowed)}")
    lines.append("")
    lines.append("| страница | слово | вхождений | допустимо |")
    lines.append("|---|---|---|---|")
    bad = 0
    for key, text in _pages_text(client, job_id).items():
        lowered = text.lower()
        for word in _FORBIDDEN:
            count = lowered.count(word.lower())
            if count == 0:
                continue
            legit = any(word.lower() in value.lower() for value in allowed)
            if not legit:
                bad += 1
            lines.append(f"| {key} | {word} | {count} | {'да' if legit else 'НЕТ'} |")
    lines.append("")
    lines.append(f"недопустимых вхождений: {bad}")
    ok = bad == 0
    lines.append(f"\nВЕРДИКТ: недопустимых вхождений {bad} — {'PASS' if ok else 'FAIL'}")
    return "\n".join(lines)


# ------------------------------------------------------------------ u3-13


def ev_u3_13(
    main: U1Profile,
    dead: U1Profile,
    auth: U1Profile,
    u2_main: U1Profile,
    u2_dead: U1Profile,
    *,
    server: MockLLMServer,
    base: Path,
    slow: Path,
    tiny: Path,
) -> str:
    lines = [
        header(),
        "K: U3-13 — регрессия: evidence U1 (10) + U2 (4) на коде U3",
        "",
    ]
    _U2_BOOKS.update({"tiny": tiny, "slow": slow})
    # CREATED-остатки предыдущих проверок U3 (u3-2…u3-14): отменяем, чтобы
    # воркерные прогоны U1 обработали свои задания без очереди чужих
    for job in main.repo.list_jobs():
        if job.status is JobStatus.CREATED:
            main.repo.transition(job.id, JobStatus.CANCELLED, "u3-ev-13-backlog")
    checks: list[tuple[str, str]] = []
    checks.append(("u1-1", ev_u1_1(main, server, base, slow)))
    checks.append(("u1-2", ev_u1_2(main, base, tiny)))
    checks.append(("u1-3", ev_u1_3(dead)))
    checks.append(("u1-4", ev_u1_4(main)))
    checks.append(("u1-5", ev_u1_5(main)))
    checks.append(("u1-6", ev_u1_6(main)))
    checks.append(("u1-7", ev_u1_7(auth)))
    checks.append(("u1-8", ev_u1_8(auth, base)))
    checks.append(("u1-9", ev_u1_9(main, server, base)))
    checks.append(("u2-1", ev_u2_1(u2_main)))
    checks.append(("u2-2", ev_u2_2(u2_main, u2_dead, base, server)))
    checks.append(("u2-3", ev_u2_3(u2_main)))
    checks.append(("t-u2-4", ev_t_u2_4(u2_main, base)))
    # t-u1-10 — последним: она останавливает общий mock-сервер (LLM off в
    # середине перевода) и не перезапускает его; U2-проверки, которым живой
    # mock нужен, уже выполнены.
    checks.append(("t-u1-10", ev_t_u1_10(main, server, base, slow)))
    passed = 0
    for name, text in checks:
        ok = _verdict_ok(text)
        passed += 1 if ok else 0
        verdict_line = next(
            (line for line in text.splitlines() if line.startswith("ВЕРДИКТ:")), "—"
        )
        lines.append(f"| {name} | {'PASS' if ok else 'FAIL'} | {verdict_line} |")
    lines.append("")
    lines.append(f"проверок прошло: {passed}/14")
    ok = passed == 14
    lines.append(f"\nВЕРДИКТ: U1+U2 регрессия {passed}/14 — {'PASS' if ok else 'FAIL'}")
    lines.append("")
    lines.append("=" * 72)
    lines.append("ПОЛНЫЕ ЗАХВАТЫ (verbatim, Rule 3)")
    lines.append("=" * 72)
    for name, text in checks:
        lines.append("")
        lines.append(f"########## {name} ##########")
        lines.append(text)
    return "\n".join(lines)


# ------------------------------------------------------------------ u3-14


def ev_u3_14(main: U1Profile) -> str:
    lines = [
        header(),
        "K: U3-14 — Rule 4: страницы с данными по 5 исходным языкам",
        "",
    ]
    matrix = yaml.safe_load(
        (ROOT / "config/language_matrix.yaml").read_text(encoding="utf-8")
    )
    sources = sorted(
        {p["source"] for p in matrix["pairs"] if p["target"] == "ru"}
    )
    lines.append(f"исходные языки (из language_matrix.yaml, target=ru): {sources}")
    client = main.client
    # 5 исходных языков — в <option> формы создания задания (страница /jobs),
    # в реестре (страница /languages) и в уровнях пар (страница /prompts);
    # наличие задания не требуется
    pages = {
        "languages": client.get("/languages").text,
        "prompts": client.get("/prompts").text,
        "jobs": client.get("/jobs").text,
    }
    lines.append("")
    missing = 0
    for key, text in pages.items():
        found = []
        for code in sources:
            if re.search(rf"(?<![a-zA-Z]){re.escape(code)}(?![a-zA-Z])", text):
                found.append(code)
            else:
                missing += 1
        lines.append(f"/{key}: языков {len(found)}/5 — {found}")
    ok = missing == 0 and len(sources) == 5
    lines.append(f"\nнедостающих языков: {missing}")
    lines.append(
        f"\nВЕРДИКТ: 5 исходных языков на 3 страницах — "
        f"{'PASS' if ok else 'FAIL'}"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ main


def _run_all_checks(
    profiles: dict[str, U1Profile],
    u2_main: U1Profile,
    u2_dead: U1Profile,
    *,
    server: MockLLMServer,
    base: Path,
    slow: Path,
    tiny: Path,
) -> list[tuple[str, bool]]:
    results: list[tuple[str, bool]] = []

    def run(name: str, content: str) -> None:
        evidence(name, content)
        results.append((name, _verdict_ok(content)))

    run("u3-1-no-ui-literals.txt", ev_u3_1())
    run("u3-2-table-card-parity.txt", ev_u3_2(profiles["main"], tiny))
    run("u3-3-css-invariants.txt", ev_u3_3())
    run("u3-4-delete-terminal.txt", ev_u3_4(profiles["main"], tiny))
    run("u3-5-delete-non-terminal.txt", ev_u3_5(profiles["main"], tiny))
    run("u3-6-purge-diff.txt", ev_u3_6(profiles["main"], tiny))
    run("u3-7-logs-clear.txt", ev_u3_7(profiles["main"], tiny))
    run("u3-8-prompt-versioning.txt", ev_u3_8(profiles["main"], tiny))
    run("u3-9-prompt-write-rejections.txt", ev_u3_9(profiles["main"], server))
    run("u3-10-access-matrix.txt", ev_u3_10(profiles["main"], profiles["dead"]))
    run("u3-11-a11y.txt", ev_u3_11(profiles["main"], tiny))
    run("u3-12-honesty.txt", ev_u3_12(profiles["main"], tiny))
    run("u3-14-rule4-five-languages.txt", ev_u3_14(profiles["main"]))
    run(
        "u3-13-regression-u1-u2.txt",
        ev_u3_13(
            profiles["main"],
            profiles["dead"],
            profiles["auth"],
            u2_main,
            u2_dead,
            server=server,
            base=base,
            slow=slow,
            tiny=tiny,
        ),
    )
    return results


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="u3-evidence-"))
    books_dir = base / "books"
    books_dir.mkdir(parents=True, exist_ok=True)
    tiny = books_dir / "u3_tiny.epub"
    slow = books_dir / "u3_slow.epub"
    _synth_u1_tiny_book(tiny)
    _synth_u1_slow_book(slow)
    server = MockLLMServer()
    server.start()
    profiles: dict[str, U1Profile] = {}
    u2_main: U1Profile | None = None
    u2_dead: U1Profile | None = None
    try:
        profiles["main"] = _u1_profile(base, "main", server.url, secret=True, client_auth=True)
        profiles["dead"] = _u1_profile(base, "dead", "http://127.0.0.1:1/v1")
        profiles["auth"] = _u1_profile(base, "auth", server.url, secret=True)
        u2_main = _u2_profile(base, "u2main", server.url, secret=True)
        u2_dead = _u2_profile(base, "u2dead", "http://127.0.0.1:1/v1", secret=True)
        print("[u3-evidence] окружение готово")
        assert u2_main is not None and u2_dead is not None
        results = _run_all_checks(
            profiles,
            u2_main,
            u2_dead,
            server=server,
            base=base,
            slow=slow,
            tiny=tiny,
        )
        passed = sum(1 for _name, ok in results if ok)
        bad = [name for name, ok in results if not ok]
        print(
            f"\nпроверок: {len(results)}; прошло: {passed}; "
            f"не прошло: {bad if bad else 'нет'}"
        )
        print(f"\nCONTROL U3: {passed}/{len(results)} PASS")
        return 0 if passed == len(results) else 1
    finally:
        _kill_all(_WORKERS)
        _kill_all(_U1_WORKERS)
        _kill_all(_U2_WORKERS)
        server.stop()
        for profile in (*profiles.values(), u2_main, u2_dead):
            if profile is not None:
                profile.db.close()
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
