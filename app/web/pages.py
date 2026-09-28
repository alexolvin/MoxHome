"""HTML-страницы U1/U3: разделы Books/Jobs/Settings (+Languages/Models/Prompts).

Каждая страница — серверный shell + данные своего API-endpoint'а
(каждый endpoint из ТЗ имеет потребителя, U1-6). Страница задания
дополнительно рендерит фактические данные сервером (без JS тоже
читается) и обновляет блок прогресса поллингом ``GET /api/jobs/{id}``.

Серия R §5: shell — appbar (h1, назад, меню «Ещё») + таб-бар
(Библиотека/Задания/Настройки) + FAB; /settings — корень с
маршрутными строками; старые пути /languages, /models, /prompts —
301 на /settings/*.

U3:
- строки интерфейса — только из ``app.web.strings`` (критерий U3-1);
- все данные-таблицы рендерятся сервером ДВАЖДЫ: таблица
  (``.u3-table``) и карточный список (``.u3-cards``, тот же набор полей
  в том же порядке, 3.2.5) — переключение по ``ui.mobile_breakpoint_px``;
- CSRF-токен сессии Basic-auth — скрытое поле ``u3_csrf`` в каждой форме
  (3.9.41); параметры автообновления — ``ui.*`` (Rule 1).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData
from starlette.responses import Response

from app.adapters.convert import probe as converter_probe
from app.core.config import ALL_PROFILE_NAMES, Settings
from app.core.errors import AppError, SettingsIncompleteError
from app.core.llm_settings import SettingsIssue, validate_llm_settings
from app.core.worker_signal import worker_alive
from app.domain.job import TERMINAL_STATES, Job, JobStatus
from app.languages.cefr import CEFR_ORDER
from app.languages.registry import load_registry
from app.services import covers
from app.services.export_artifacts import (
    EXPORT_FORMATS,
    ExportInProgress,
    ExportPending,
    latest_export,
    start_book_export,
    start_job_export,
)
from app.services.import_book import get_import
from app.services.jobs import JOB_MODES, confirm_job, create_job
from app.services.llm_config import (
    clear_app_log,
    probe_conflict,
    probe_profile,
    probe_request,
    update_llm_config,
)
from app.storage.jobs_repo import JobRepository, TransitionRecord
from app.web import views
from app.web.auth import LOGIN_COOKIE
from app.web.strings import UI_STRINGS

_S = UI_STRINGS
logger = logging.getLogger("app.web.pages")

_SORT_FIELDS = ("created_at", "updated_at")
_ALL_JOB_STATUS_VALUES = tuple(st.value for st in JobStatus)

# Серия R §5/§6: run-группа — единым источником (views.STATUS_GROUP):
# точка таб-бара («Задания») и точка строки книги.
_RUN_STATUS_VALUES = tuple(
    st.value for st in JobStatus if views.status_group(st.value) == "run"
)


def _page_title(key: str, **kwargs: object) -> str:
    return _S[key].format(**kwargs) if kwargs else _S[key]


# V1 §6: dev-kit (S14) — токены читаются из :root style.css, чтобы свотчи
# не дублировали палитру литералами в шаблоне (T17: строки UI — только
# strings.py; hex-значения токенов — данные, не строки UI).
_STYLE_CSS = Path(__file__).with_name("static") / "style.css"
_TOKEN_RE = re.compile(r"([A-Za-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})")


def _kit_tokens() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    m = re.search(r":root\s*\{([^}]*)\}", _STYLE_CSS.read_text(encoding="utf-8"))
    if not m:
        return out
    for name, hex_ in _TOKEN_RE.findall(m.group(1)):
        if name not in seen:
            seen.add(name)
            out.append({"name": name, "hex": hex_.upper()})
    return out


# Серия R §10: сегментный фильтр — 5 пилюль на 5 статусных групп (§4).
# «Готовы» — ok+warn (окончательные успехи, включая предупреждения).
_GROUP_STATUS_GROUPS: dict[str, tuple[str, ...]] = {
    "all": ("wait", "run", "ok", "warn", "err"),
    "active": ("run",),
    "waiting": ("wait",),
    "done": ("ok", "warn"),
    "errors": ("err",),
}


def _group_statuses(group: str, exact: frozenset[str]) -> tuple[str, ...] | None:
    """Множество статусов для пилюли «группа» + точных статусов (лист).

    ``None`` — фильтр не нужен (всё); пустой кортеж — по построению
    заданий нет (пересечение пусто) — запрос не выполняется.
    """
    if group not in _GROUP_STATUS_GROUPS:
        group = "all"
    pool = {
        st.value for st in JobStatus if views.status_group(st.value) in _GROUP_STATUS_GROUPS[group]
    }
    if exact:
        pool &= exact
        return tuple(sorted(pool))
    return None if group == "all" else tuple(sorted(pool))


def _build_job_rows(
    jobs: list[Job],
    settings: Settings,
    transitions: dict[str, list[TransitionRecord]],
    today_start: float,
) -> dict[str, list[dict[str, object]]]:
    """Серия R §10: строки списка с группировкой (единый источник
    views.job_list_group); время работы — E7/M7 (bulk-переходы, без N+1)."""
    group_rows: dict[str, list[dict[str, object]]] = {
        "active": [],
        "queue": [],
        "done": [],
    }
    title_map = views.book_title_map([Path(j.source_path) for j in jobs])
    cover_map = views.book_cover_map(settings, [Path(j.source_path) for j in jobs])
    for job in jobs:
        elapsed, _eta = views.elapsed_and_eta(job, transitions.get(job.id, []))
        indeterminate = job.status in (JobStatus.PLANNING, JobStatus.VALIDATING_OUTPUT)
        group_rows[
            views.job_list_group(job.status.value, job.updated_at, today_start)
        ].append(
            {
                "job": job,
                "book_name": Path(job.source_path).name,
                "book_title": title_map.get(Path(job.source_path).name)
                or Path(job.source_path).name,
                "cover": cover_map.get(Path(job.source_path).name, {}),
                "progress_display": views.progress_facts(
                    job, views.read_json(settings.job_dir(job.id) / "validation.json")
                )["display"],
                "progress_pct": None
                if indeterminate
                else round((job.progress or 0.0) * 100),
                "elapsed_human": views.human_duration(elapsed),
                "time_label": views.job_time_label(job, elapsed, _eta),
                "status_group": views.status_group(job.status.value),
                "status_glyph": views.status_glyph(job.status.value),
                # реворк 2026-09-26 (п.10): исходный язык строки — как у книги
                "source_display": views.job_source_display(settings, job),
                # Серия R §11: место в очереди (свайп «Вперёд очереди»)
                # и время создания (порядок wait-группы).
                "queue_rank": job.queue_rank,
                "created_at": job.created_at,
            }
        )
    return _sort_job_groups(group_rows)


def _sort_job_groups(
    group_rows: dict[str, list[dict[str, object]]],
) -> dict[str, list[dict[str, object]]]:
    """Очередь (реворк 2026-09-26 §10): active — выполняемое сверху
    (run-статусы, затем пауза/подтверждение; по обновлению desc); queue —
    queue_rank DESC, created_at ASC (порядок drag); done — по обновлению desc."""
    def _upd(row: dict[str, object]) -> float:
        return float(cast(Job, row["job"]).updated_at)

    group_rows["active"].sort(
        key=lambda row: (
            0 if row["status_group"] == "run" else 1,
            -_upd(row),
        )
    )
    group_rows["queue"].sort(
        key=lambda row: (
            -int(cast("int", row["queue_rank"])),
            float(cast("float", row["created_at"])),
        )
    )
    group_rows["done"].sort(key=lambda row: -_upd(row))
    return group_rows


def _jobs_sheet_prep(  # noqa: PLR0917 — параметры листов: sheet + job/retry/book
    repo: JobRepository,
    settings: Settings,
    sheet: str,
    job: str | None,
    retry_of: str | None,
    book: str | None = None,
) -> tuple[dict[str, object] | None, dict[str, object] | None, bool]:
    """Серия R §11/§12 + R7 (§9): данные листов /jobs (no-JS ``?sheet=``).

    Возврат — ``(export, create, export_missing)``: export — данные
    листа экспорта (``kind=job`` — объект по заданию; ``kind=book`` —
    карточка книги: завершённые задания книги + файл книги), create —
    параметры листа создания (предзаполнение из ``retry_of``);
    export_missing — задание не найдено (страница 404)."""
    export_data: dict[str, object] | None = None
    export_missing = False
    if sheet == "export":
        probe_status, _ = converter_probe(settings)
        if job:
            target = repo.get(job)
            if target is None:
                export_missing = True
            else:
                facts = views.job_facts(settings, repo, target, log_lines=1)
                export_data = {
                    "kind": "job",
                    "job_id": target.id,
                    "book_name": Path(target.source_path).name,
                    "files": facts["files"],
                    "result_ready": facts["result_ready"],
                    "export": facts["export"],
                    "converter_ok": probe_status == "ok",  # строка (R6)
                    "formats": list(EXPORT_FORMATS),
                }
        else:
            book_norm = (
                book
                if book and views.book_epub_path(settings, book) is not None
                else None
            )
            completed: list[dict[str, object]] = []
            if book_norm:
                rows = repo.list_jobs(
                    None,
                    0,
                    statuses=("COMPLETED", "COMPLETED_WITH_WARNINGS"),
                    book=book_norm,
                    sort="updated_at",
                    direction="desc",
                )
                for row in rows:
                    completed.append(
                        {
                            "id": row.id,
                            "book_name": Path(row.source_path).name,
                            "status": row.status.value,
                            "updated_at_human": views.human_ts(row.updated_at),
                            "mode": row.mode,
                            "target_language": row.target_language,
                        }
                    )
            export_data = {
                "kind": "book",
                "book": book_norm,
                "jobs": completed,
                "converter_ok": probe_status == "ok",  # строка (R6)
                "formats": list(EXPORT_FORMATS),
            }
    create_data: dict[str, object] | None = None
    if sheet == "create":
        create_data = {}
        if retry_of:
            original = repo.get(retry_of)
            if original is not None:
                create_data = {
                    "retry_of": original.id,
                    "book": Path(original.source_path).name,
                    "target": original.target_language,
                    "mode": original.mode,
                    # R4: повтор — все параметры исходного задания
                    # (уровень/CEFR тоже, а не только target/mode).
                    "learning_level": original.learning_level,
                    "learning_cefr": original.learning_cefr,
                }
    return export_data, create_data, export_missing


def _jobs_new_location(  # noqa: PLR0917 — форма создания: 4 параметра ТЗ + пути
    settings: Settings,
    repo: JobRepository,
    book: str,
    target_language: str,
    mode: str,
    learning_level: str = "",
    learning_cefr: str = "",
) -> str:
    """Серия R T15: no-JS-создание — Location для 303: успех — список,
    отказ (AppError, verbatim) — обратно на лист с причиной.
    R4: опциональные уровень/CEFR (пустая строка — не задан; валидация —
    в create_job, единая точка с JSON-API)."""
    source = Path(book)
    if not source.is_absolute():
        source = settings.input_dir / book
    try:
        create_job(
            repo,
            settings,
            source_path=source,
            target_language=target_language,
            mode=mode,
            learning_level=learning_level or None,
            learning_cefr=learning_cefr or None,
        )
    except AppError as exc:
        return "/jobs?sheet=create&create_err=" + quote(exc.message)
    return "/jobs"


def _jobs_confirm_location(
    repo: JobRepository, job_id: str, source_language: str | None
) -> str:
    """Серия R T15: no-JS-подтверждение — Location для 303: успех — экран
    задания, отказ (AppError, verbatim) — с причиной."""
    try:
        confirm_job(repo, job_id, source_language)
    except AppError as exc:
        return f"/jobs/{job_id}?confirm_err=" + quote(exc.message)
    return f"/jobs/{job_id}"


def _jobs_href(  # noqa: PLR0917 — параметры href: group/sort/status/sheet + book (R5)
    group: str,
    sort_field: str,
    direction: str,
    status_list: list[str] | None = None,
    sheet_name: str | None = None,
    book: str | None = None,
) -> str:
    """Href /jobs с текущими группой/сортировкой/точными статусами
    (серия R §10/§12: меню «Ещё», пилюли, сброс, FAB, листы).
    Серия R §7 (R5): ``book`` — фильтр по книге (карточка, «Экспорт»
    и «Все (N)»); сохраняется в каждом derived-href, сбрасывается
    только явной «сброс»-ссылкой."""
    pairs: list[tuple[str, str]] = []
    if group and group != "all":
        pairs.append(("group", group))
    for value in status_list or []:
        pairs.append(("status", value))
    if sort_field != "updated_at":
        pairs.append(("sort", sort_field))
    if direction != "desc":
        pairs.append(("direction", direction))
    if book:
        pairs.append(("book", book))
    if sheet_name:
        pairs.append(("sheet", sheet_name))
    return "/jobs?" + urlencode(pairs) if pairs else "/jobs"


def _parse_purge_days(raw: str, default: int | float) -> int | float | str:
    """R8 (no-JS purge): пустое — default; нечисло/отрицательное —
    строка ошибки (err_purge_bad_days)."""
    if not raw:
        return default
    try:
        value: int | float = int(raw)
    except ValueError:
        try:
            value = float(raw)
        except ValueError:
            return _S["err_purge_bad_days"]
    if value < 0:
        return _S["err_purge_bad_days"]
    return value


def _resolve_cover_thumb(
    settings: Settings, filename: str, w: int
) -> Path | None:
    """V2 §8.3: путь миниатюры обложки (ленивое создание 256/512) или
    ``None`` (нет книги / обложки / файла / decode — UI покажет заглушку)."""
    dest = views.book_epub_path(settings, filename)
    if dest is None:
        return None
    meta = views.read_book_meta(dest)
    if meta.get("cover") is not None and meta.get("cover") != "extracted":
        return None  # none / failed:<reason> → заглушка §8.4
    stem = filename[: -len(".epub")]
    cover_name = meta.get("cover_path")
    if not (
        cover_name
        and Path(cover_name).name == cover_name
        and cover_name.startswith(stem + ".cover.")
    ):
        return None
    source = settings.input_dir / cover_name
    if not source.is_file():
        return None
    try:
        sha_full = meta.get("cover_sha256") or hashlib.sha256(
            source.read_bytes()
        ).hexdigest()
    except OSError:
        return None
    width = w if w in covers.THUMB_WIDTHS else (256 if w <= 256 else 512)
    covers_dir = settings.input_dir / ".covers"
    thumb = covers.cover_thumbnail_path(covers_dir, sha_full, width)
    if not thumb.exists():
        covers.ensure_thumbnails(source, sha_full, covers_dir)
    return thumb if thumb.exists() else None


#: MTPE M2 (§6.2): правимые текстовые поля карточки (кроме ключа).
_LLM_EDITABLE_FIELDS = (
    "base_url",
    "model",
    "temperature",
    "retry_times",
    "concurrency",
    "timeout_seconds",
    "warmup_timeout_s",
    "top_k",
    "max_tokens_ratio",
    "max_segment_tokens",
    "stop",
    "extra_body",
)


def build_router(  # noqa: PLR0915 — роутер FastAPI: все маршруты в одном замыкании (R1–R5)
    settings: Settings, repo: JobRepository, templates: Jinja2Templates
) -> APIRouter:
    router = APIRouter()

    def ctx(request: Request, title: str, current: str) -> dict[str, object]:
        return {
            "request": request,
            "title": title,
            "auth_on": bool(settings.server.auth_secret),
            "s": _S,
            "current": current,
            "csrf": getattr(request.state, "csrf_token", ""),
            # model_dump: Jinja tojson сериализует plain-dict (установка
            # из YAML — Rule 1; шаблоны числовых дефолтов не имеют).
            "ui": settings.ui.model_dump(),
            # Серия R §5: точка на вкладке «Задания», пока есть run-задание.
            "has_active_job": bool(repo.list_jobs(1, 0, statuses=_RUN_STATUS_VALUES)),
        }

    def registry_codes() -> list[str]:
        return sorted(load_registry(settings.languages_root))

    @router.get("/", include_in_schema=False)
    def index(request: Request) -> Response:
        return RedirectResponse("/jobs", status_code=302)

    # ------------------------------------------------------------- Books
    # R6 (ТЗ §8): проба конвертера — с TTL-кэшем (D110): контейнер не
    # поднимается на каждый запрос; один результат читают страница,
    # лист импорта, no-JS-страница загрузки и /api/health.
    def _converter_ctx() -> dict[str, Any]:
        status, detail = converter_probe(settings)
        ok = status == "ok"  # статус — строка, не bool (fail closed)
        formats = ["epub"] if not ok else list(settings.converter.accept)
        return {
            "converter_ok": ok,
            "converter_detail": detail,
            "import_accept": ",".join("." + fmt for fmt in formats),
            "import_accept_list": formats,
        }

    # R6-шаги импорта → строки (единая точка для no-JS-страницы):
    _IMPORT_STEP_LABELS = {
        "checking": "import_step_check",
        "converting": "import_step_convert",
        "validating": "import_step_validate",
        "language": "import_step_language",
    }

    @router.get("/books")
    def books_page(
        request: Request,
        q: str | None = Query(default=None),
    ) -> Response:
        # Серия R §6: строки книги; `q` — серверный подстроковый фильтр
        # (имя файла + название). Серия R §7 (R5): поиск — строка UI,
        # тот же серверный фильтр (JS — рефетч, no-JS — GET-редирект).
        # R6: FAB — лист импорта (JS) / страница /books/upload (no-JS).
        return templates.TemplateResponse(
            request,
            "books.html",
            ctx(request, _page_title("title_books"), "books")
            | {
                "books": views.list_books(settings, repo, q=q),
                "q": q or "",
                "fab_href": "/books/upload",
                "fab_sheet": "u3-sheet-import",
                "fab_label": "fab_add_book",
                **_converter_ctx(),
            },
        )

    @router.get("/books/upload")
    def book_upload_page(request: Request) -> Response:
        """R6 no-JS: страница загрузки (форма POST /api/books).
        Для не-EPUB API отвечает 303 на ``/imports/{id}`` (прогресс с
        meta-refresh). Регистрация ДО ``/books/{filename}``."""
        return templates.TemplateResponse(
            request,
            "book_upload.html",
            ctx(request, _page_title("import_sheet_title"), "books")
            | _converter_ctx()
            | {"back_href": "/books"},
        )

    @router.get("/imports/{import_id}")
    def import_status_page(request: Request, import_id: str) -> Response:
        """R6 no-JS: прогресс импорта (meta-refresh 1 с). done →
        карточка книги; error → причина + список книг; неизвестный id
        (в т.ч. после рестарта) → 404."""
        rec = get_import(import_id)
        ctx_ = ctx(request, _page_title("import_status_title"), "books") | {
            "back_href": "/books"
        }
        if rec is None:
            return templates.TemplateResponse(
                request,
                "import_status.html",
                ctx_
                | {
                    "state": "missing",
                    "reason": _S["err_import_not_found"].format(id=import_id),
                },
                status_code=404,
            )
        step = rec.get("step") or ""
        step_key = _IMPORT_STEP_LABELS.get(step, "import_step_convert")
        return templates.TemplateResponse(
            request,
            "import_status.html",
            ctx_
            | {
                "state": rec.get("status"),
                "step_label": _S[step_key],
                "name": rec.get("name"),
                "reason": rec.get("error"),
            },
        )

    @router.get("/books/{filename}")
    def book_detail_page(request: Request, filename: str) -> Response:
        """Серия R §7 (R5): карточка книги. Шесть позиций ТЗ: шапка
        (назад/название/⋯), обложка+название+автор+«Перевести»/«Экспорт»,
        блок «Языки» (правка тапом), до 3 последних заданий + «Все (N)»,
        свёрнутый «Технические сведения» (SHA-256), «Удалить книгу»."""
        detail = views.book_detail(settings, repo, filename)
        if detail is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        meta = detail["meta"]
        jobs: list[Job] = detail["jobs"]
        transitions = repo.list_transitions_bulk([job.id for job in jobs])
        today_start = views.today_start_epoch()
        grouped = _build_job_rows(jobs, settings, transitions, today_start)
        # до 3 последних — плоским списком (row, gname) для шаблона;
        # порядок — очередь (active/queue/done), как в списке заданий
        job_rows: list[tuple[dict[str, object], str]] = []
        for gname in ("active", "queue", "done"):
            for row in grouped.get(gname, []):
                job_rows.append((row, gname))
        title = meta.get("title") or detail["name"]
        return templates.TemplateResponse(
            request,
            "book.html",
            ctx(request, title, "books")
            | {
                "detail": detail,
                "meta": meta,
                "title": title,
                "author": meta.get("author"),
                "job_rows": job_rows,
                "total_jobs": detail["total_jobs"],
                "all_jobs_href": "/jobs?book=" + quote(filename),
                "translate_href": "/jobs?sheet=create&book=" + quote(filename),
                # R7 (ТЗ §9): «Экспорт» карточки — тот же лист, что у
                # строки/экрана задания (jobs?sheet=export&book=).
                "export_href": "/jobs?sheet=export&book=" + quote(filename),
                "back_href": "/books",
                "lang_codes": registry_codes(),
                "cefr_levels": list(CEFR_ORDER),
                # ⋯ карточки (ТЗ §7 п.1): «Удалить книгу» — тот же поток
                # (data-book-delete), что и красный текст внизу; no-JS —
                # ссылка на страницу-сценарий удаления.
                "appbar_menu": [
                    {
                        "label": _S["book_delete_btn"],
                        "href": f"/books/{filename}/delete",
                        "attrs": {"data-book-delete": filename},
                    },
                ],
            },
        )

    @router.get("/books/{filename}/cover", include_in_schema=False)
    def book_cover_thumb(
        request: Request,
        filename: str,
        w: Annotated[int, Query(ge=1, le=4096)] = 256,
        v: Annotated[str | None, Query()] = None,  # cache-bust (sha8); не проверяем
    ) -> Response:
        """V2 §8.3: миниатюра обложки WebP. Лениво создаёт 256/512 при
        первом запросе; ``Cache-Control: … immutable`` (кеш браузера, V-T9).
        Обложки нет (none/failed/нет файла) — 404 (UI показывает заглушку
        §8.4). ``v`` — hint для клиента; сервер отдаёт актуальную миниатюру."""
        del request, v
        thumb = _resolve_cover_thumb(settings, filename, w)
        if thumb is None:
            return Response(status_code=404)
        return FileResponse(
            thumb,
            media_type="image/webp",
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    # ------------------------------------------------------------- удаление (no-JS, R5)
    # Тот же сценарий, что JS-лист (единый механизм ТЗ §7): состояние —
    # ДО вызова удаления (usage), три пути (файл / каскад / отмена),
    # каскад — два шага с тем же HMAC-токеном, что API. Голый 409
    # пользователю не показывается — только страница с причиной и путём.
    def _basic_user(request: Request) -> str:
        return str(getattr(request.state, "username", "") or "anonymous")

    def _book_delete_ctx(  # noqa: PLR0917 — request + 5 полей сценария удаления
        request: Request,
        filename: str,
        usage: dict[str, Any],
        step: str,
        error: str | None = None,
        dry_run: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        return (
            ctx(request, _page_title("book_delete_title"), "books")
            | {
                "book_name": filename,
                "usage": usage,
                "step": step,
                "error": error,
                "dry_run": dry_run,
                "back_href": f"/books/{filename}",
            }
        )

    @router.get("/books/{filename}/delete")
    def book_delete_page(request: Request, filename: str) -> Response:
        if views.book_epub_path(settings, filename) is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        usage = views.book_usage(settings, repo, filename)
        assert usage is not None  # файл проверен выше
        return templates.TemplateResponse(
            request, "book_delete.html", _book_delete_ctx(request, filename, usage, "usage")
        )

    @router.post("/books/{filename}/delete")
    def book_delete_post(request: Request, filename: str) -> Response:
        """No-JS «Удалить» (сценарий «заданий нет»): 303 /books; если
        задания появились (гонка) — страница сценария, голый 409 — нет."""
        dest = views.book_epub_path(settings, filename)
        if dest is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        if not repo.list_job_ids_by_book(filename):
            size = views.delete_book_files(settings, dest)
            logger.info(
                "book_delete(no-js): user=%s name=%s bytes=%d",
                _basic_user(request),
                filename,
                size,
            )
            return RedirectResponse("/books", status_code=303)
        usage = views.book_usage(settings, repo, filename)
        assert usage is not None
        return templates.TemplateResponse(
            request,
            "book_delete.html",
            _book_delete_ctx(request, filename, usage, "usage"),
            status_code=409,
        )

    @router.post("/books/{filename}/delete_file_only")
    def book_delete_file_only(request: Request, filename: str) -> Response:
        """No-JS «Удалить только файл»: задания сохраняются со ссылкой на
        (теперь отсутствующий) файл — как API ``?file_only=1``."""
        dest = views.book_epub_path(settings, filename)
        if dest is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        size = views.delete_book_files(settings, dest)
        logger.info(
            "book_delete_file_only(no-js): user=%s name=%s bytes=%d",
            _basic_user(request),
            filename,
            size,
        )
        return RedirectResponse("/books", status_code=303)

    @router.post("/books/{filename}/purge")
    def book_purge_page(
        request: Request,
        filename: str,
        confirm_token: str = Form(default=""),
    ) -> Response:
        """No-JS каскад — два шага (та же логика, что ``POST /api/books/
        {filename}/purge``): шаг 1 — страница подтверждения с токеном,
        шаг 2 — сверка токена и удаление ровно перечисленного."""
        dest = views.book_epub_path(settings, filename)
        if dest is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        usage = views.book_usage(settings, repo, filename)
        assert usage is not None
        if not confirm_token:
            try:
                dry = views.book_purge_dry_run(settings, repo, filename)
            except AppError as exc:
                return templates.TemplateResponse(
                    request,
                    "book_delete.html",
                    _book_delete_ctx(request, filename, usage, "usage", error=exc.message),
                    status_code=409,
                )
            return templates.TemplateResponse(
                request,
                "book_delete.html",
                _book_delete_ctx(request, filename, usage, "confirm", dry_run=dry),
            )
        try:
            dry = views.book_purge_dry_run(settings, repo, filename)
        except AppError as exc:
            return templates.TemplateResponse(
                request,
                "book_delete.html",
                _book_delete_ctx(request, filename, usage, "usage", error=exc.message),
                status_code=409,
            )
        expected = views.book_purge_token(settings.server.auth_secret, filename, dry["job_ids"])
        if not hmac.compare_digest(confirm_token, expected):
            return templates.TemplateResponse(
                request,
                "book_delete.html",
                _book_delete_ctx(
                    request, filename, usage, "usage",
                    error=_S["err_book_purge_token"],
                ),
                status_code=409,
            )
        result = views.book_purge_execute(settings, repo, filename, dry["job_ids"])
        logger.info(
            "book_purge(no-js): user=%s name=%s jobs=%s rows=%d bytes=%d",
            _basic_user(request),
            filename,
            dry["job_ids"],
            result["rows"],
            result["bytes"],
        )
        return RedirectResponse("/books", status_code=303)

    # ------------------------------------------------------------- метаданные (no-JS, R5)
    # Та же пара «одно поле + сохранить», что JS-лист (единый механизм
    # ТЗ §7): страница-форма с ОДНИМ полем; отказ — страница с причиной
    # (fail closed, как API 400), файл не тронут.
    def _book_meta_ctx(  # noqa: PLR0917 — request + 5 полей формы метаданных
        request: Request,
        filename: str,
        field: str,
        value: str,
        kind: str,
        error: str | None = None,
    ) -> dict[str, object]:
        return (
            ctx(request, _page_title("book_meta_title"), "books")
            | {
                "book_name": filename,
                "field": field,
                "fkey": views.BOOK_META_FIELD_KEYS[field],
                "value": value,
                "kind": kind,
                "error": error,
                "lang_codes": registry_codes(),
                "cefr_levels": list(CEFR_ORDER),
                "back_href": f"/books/{filename}",
            }
        )

    @router.get("/books/{filename}/meta")
    def book_meta_page(
        request: Request,
        filename: str,
        field: str = Query(default="title"),
    ) -> Response:
        dest = views.book_epub_path(settings, filename)
        if dest is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        if field not in views.BOOK_META_FIELDS:
            field = "title"
        meta = views.read_book_meta(dest)
        kind = views.BOOK_META_FIELDS[field][0]
        return templates.TemplateResponse(
            request,
            "book_meta.html",
            _book_meta_ctx(request, filename, field, str(meta.get(field) or ""), kind),
        )

    @router.post("/books/{filename}/meta")
    def book_meta_post(
        request: Request,
        filename: str,
        field: str = Form(default=""),
        value: str = Form(default=""),
    ) -> Response:
        dest = views.book_epub_path(settings, filename)
        if dest is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        if field not in views.BOOK_META_FIELDS:
            raise HTTPException(
                status_code=400, detail=_S["err_book_bad_field"].format(field=field)
            )
        try:
            views.update_book_meta(settings, filename, {field: value})
        except AppError as exc:
            kind = views.BOOK_META_FIELDS[field][0]
            return templates.TemplateResponse(
                request,
                "book_meta.html",
                _book_meta_ctx(request, filename, field, value, kind, error=exc.message),
                status_code=400,
            )
        logger.info(
            "book_meta(no-js): user=%s name=%s field=%s",
            _basic_user(request),
            filename,
            field,
        )
        return RedirectResponse(f"/books/{filename}", status_code=303)

    # ------------------------------------------------------------- Jobs (список)
    @router.get("/jobs")
    def jobs_page(  # noqa: PLR0917 — query-параметры FastAPI в сигнатуре
        request: Request,
        group: str = Query(default="all"),
        status: Annotated[list[str] | None, Query()] = None,
        sort: str = Query(default="updated_at"),
        direction: str = Query(default="desc"),
        sheet: str = Query(default=""),
        job: str | None = Query(default=None),
        retry_of: str | None = Query(default=None),
        book: str | None = Query(default=None),
        create_err: str | None = Query(default=None),
        export_err: str | None = Query(default=None),
    ) -> Response:
        """Серия R §10: строки списка, сегментный фильтр (группы §4),
        группировка «Сейчас/Сегодня/Раньше», точный фильтр и очистка —
        в листах (``?sheet=`` — no-JS-вариант той же формы).

        Серия R §11/§12: листы create (создание; ``retry_of`` — повтор
        с параметрами исходного задания) и export (``job`` — артефакты);
        порядок wait-строк отражает queue_rank (§11).

        Пагинации нет: группировка пересекает страницы, всё, что
        подходит фильтру, в списке (``ui.page_size`` остаётся для API)."""
        group_norm = group if group in _GROUP_STATUS_GROUPS else "all"
        exact = frozenset(v for v in (status or []) if v in _ALL_JOB_STATUS_VALUES)
        statuses = _group_statuses(group_norm, exact)
        sort_field = sort if sort in _SORT_FIELDS else "updated_at"
        direction_norm = "asc" if direction == "asc" else "desc"
        # Серия R §7 (R5): фильтр по книге (карточка: «Экспорт», «Все (N)»);
        # незнакомое имя — игнор (как предвыбор мастера, R4).
        book_norm = book if book and views.book_epub_path(settings, book) else None

        if statuses == ():
            jobs: list[Job] = []
        else:
            jobs = repo.list_jobs(
                None,
                0,
                statuses=statuses,
                book=book_norm,
                sort=sort_field,
                direction=direction_norm,
            )

        # Группировка — сервером, единый источник views.job_list_group;
        # время работы — E7/M7 (bulk-переходы, без N+1).
        today_start = views.today_start_epoch()
        transitions = repo.list_transitions_bulk([job.id for job in jobs])
        group_rows = _build_job_rows(jobs, settings, transitions, today_start)

        sheet_norm = sheet if sheet in ("filter", "purge", "create", "export") else ""
        export_data, create_data, export_missing = _jobs_sheet_prep(
            repo, settings, sheet_norm, job, retry_of, book_norm
        )
        if export_missing:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "jobs"),
                status_code=404,
            )

        books_list = views.list_books(settings, repo)
        # R4 (ТЗ §12): ?book=<имя> — мастер открыт с карточки книги
        # (кнопка «Перевести» — R5): книга предвыбрана (JS — шаг 1 скрыт,
        # no-JS — радио отмечено, список на месте — можно сменить).
        # Незнакомое имя — игнор (шаг 1 целиком).
        if (
            sheet_norm == "create"
            and book
            and create_data is not None
            and any(b["name"] == book for b in books_list)
        ):
            create_data["book"] = book

        status_list = [v for v in (status or []) if v in _ALL_JOB_STATUS_VALUES]
        worker_status, worker_detail = worker_alive(settings)
        appbar_menu: list[dict[str, object]] = [
            {"text": _S["jobs_worker_line"].format(status=worker_status)},
            {"text": _S["jobs_menu_sort"]},
            {
                "label": _S["jobs_sort_updated"] + " ↓",
                "href": _jobs_href(group_norm, "updated_at", "desc", status_list, book=book_norm),
                "current": sort_field == "updated_at" and direction_norm == "desc",
            },
            {
                "label": _S["jobs_sort_created"] + " ↓",
                "href": _jobs_href(group_norm, "created_at", "desc", status_list, book=book_norm),
                "current": sort_field == "created_at" and direction_norm == "desc",
            },
            {
                "label": _S["jobs_sort_created"] + " ↑",
                "href": _jobs_href(group_norm, "created_at", "asc", status_list, book=book_norm),
                "current": sort_field == "created_at" and direction_norm == "asc",
            },
            {
                "label": _S["jobs_menu_filter"],
                "href": _jobs_href(
                    group_norm, sort_field, direction_norm, status_list, "filter", book_norm
                ),
                "sheet": "u3-sheet-filter",
            },
            {
                "label": _S["jobs_menu_purge"],
                "href": _jobs_href(
                    group_norm, sort_field, direction_norm, status_list, "purge", book_norm
                ),
                "sheet": "u3-sheet-purge",
            },
        ]
        pills = [
            ("all", _S["jobs_seg_all"]),
            ("active", _S["jobs_seg_active"]),
            ("waiting", _S["jobs_seg_waiting"]),
            ("done", _S["jobs_seg_done"]),
            ("errors", _S["jobs_seg_errors"]),
        ]
        return templates.TemplateResponse(
            request,
            "jobs.html",
            ctx(request, _page_title("title_jobs"), "jobs")
            | {
                "group_rows": group_rows,
                "total": sum(len(rows) for rows in group_rows.values()),
                # V3 §7.4: полоса статистики — глобальные числа из БД
                # (не зависят от фильтра списка).
                "job_stats": views.job_stats(repo.list_jobs(None, 0)),
                "group": group_norm,
                "pills": pills,
                "pill_href": lambda g: _jobs_href(
                    g, sort_field, direction_norm, status_list, book=book_norm
                ),
                "all_statuses": list(_ALL_JOB_STATUS_VALUES),
                "terminal_statuses": [st.value for st in TERMINAL_STATES],
                "f_status": status_list,
                # «Сбросить фильтр» в листе: точные статусы снимаются,
                # группа и сортировка сохраняются.
                "reset_href": _jobs_href(group_norm, sort_field, direction_norm),
                # Серия R §7 (R5): строка фильтра по книге («Книга: <имя>»
                # + сброс — снят ТОЛЬКО фильтр книги, группа/сортировка/
                # статусы сохраняются).
                "book": book_norm,
                "book_reset_href": _jobs_href(
                    group_norm, sort_field, direction_norm, status_list
                ),
                "sort_field": sort_field,
                "sort_direction": direction_norm,
                "worker_status": worker_status,
                "worker_detail": worker_detail,
                "worker_down": not worker_status.startswith("ok"),
                "appbar_menu": appbar_menu,
                "sheet": sheet_norm,
                "export": export_data,
                "create": create_data,
                # причина отказа no-JS-создания (303 c ?create_err=)
                "create_err": create_err if sheet_norm == "create" else None,
                # R7: причина отказа no-JS-экспорта (303 c ?export_err=)
                "export_err": export_err if sheet_norm == "export" else None,
                # лист создания: коды языков (реестр) и режимы задания
                "lang_codes": registry_codes(),
                "job_modes": JOB_MODES,
                "books": books_list,
                # R4 (ТЗ §12): умолчания мастера создания (единая точка
                # базовой линии services.jobs.create_baseline — как у чипов);
                # R5 (ТЗ §7): книга карточки — её target_default/cefr_default
                # перекрывают базовую линию (валидные значения только).
                "create_defaults": views.create_defaults(
                    repo, settings, book=book_norm
                ),
                # R4: «Ещё» — глоссарий read-only из конфига (per-job — вне
                # границ ТЗ §1: изменение pipeline.py)
                "glossary_enabled": settings.glossary.enabled,
                # MTPE M6 (req 6): «Способ перевода» read-only — из конфига;
                # llm_incomplete — конфиг не проходит §5.3 (строка причины,
                # «Запустить» недоступна; сервер — 422).
                "translation_pipeline": settings.translation.pipeline,
                "llm_incomplete": bool(validate_llm_settings(settings)),
                # R8 (ТЗ §13): «Ещё» — LLM-профиль read-only (одна модель,
                # если у всех профилей одинаковая; правится на /settings/llm)
                "llm_model": views.llm_model_summary(_live(request)),
                # Серия R §12: FAB «Новое задание» → лист создания
                # (JS — <dialog>, без JS — страница-форма ?sheet=create).
                "fab_href": _jobs_href(
                    group_norm, sort_field, direction_norm, status_list, "create", book_norm
                ),
                "fab_label": "fab_new_job",
                "fab_sheet": "u3-sheet-create",
            },
        )

    # ------------------------------------------------------------- создание (no-JS)
    @router.post("/jobs/new")
    def job_new(  # noqa: PLR0917 — поля формы создания (ТЗ §12)
        request: Request,
        book: str = Form(...),
        target_language: str = Form(...),
        mode: str = Form(default="bilingual"),
        learning_level: str = Form(default=""),
        learning_cefr: str = Form(default=""),
    ) -> Response:
        """Серия R §12/T15: no-JS-создание (лист ?sheet=create → форма).

        JSON-API ``POST /api/jobs`` без изменений (потребитель — app.js);
        страница-маршрут принимает форму, создаёт задание тем же
        сервисом (create_job — единый механизм) и уводит 303: успех —
        на список, отказ — обратно на лист с причиной (verbatim).
        R4: опциональные learning_level/learning_cefr (мастер «Ещё»/
        «Уровень»; пустые — не заданы, валидация — create_job)."""
        return RedirectResponse(
            _jobs_new_location(
                settings,
                repo,
                book,
                target_language,
                mode,
                learning_level,
                learning_cefr,
            ),
            status_code=303,
        )

    @router.post("/jobs/{job_id}/confirm")
    def job_confirm_form(
        request: Request,
        job_id: str,
        source_language: str | None = Form(default=None),
    ) -> Response:
        """Серия R T15: no-JS-подтверждение NEEDS_CONFIRMATION (форма
        строки). JSON-API ``/api/jobs/{job_id}/confirm`` без изменений
        (потребитель — app.js); форма — тот же сервис ``confirm_job``,
        303 на экран задания (успех) или с причиной (отказ, verbatim)."""
        if repo.get(job_id) is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "jobs"),
                status_code=404,
            )
        return RedirectResponse(
            _jobs_confirm_location(repo, job_id, source_language), status_code=303
        )

    # ------------------------------------------------------------- Job (страница)
    @router.get("/jobs/{job_id}")
    def job_page(
        request: Request,
        job_id: str,
        sheet: str = Query(default=""),
        confirm_err: str | None = Query(default=None),
    ) -> Response:
        job: Job | None = repo.get(job_id)
        if job is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "jobs"),
                status_code=404,
            )
        facts = views.job_facts(settings, repo, job)
        detection = facts.get("language_detection") or {}
        detected = detection.get("detected") or detection.get("accepted_language")
        # V3 §7.5: топ-блок — обложка 64×96, название, чип статуса,
        # крупная полоса прогресса 6 px (процент + время).
        _bpath = Path(job.source_path)
        _bmeta = views.read_book_meta(_bpath)
        _bname = _bpath.name
        _indeterminate = job.status in (
            JobStatus.PLANNING,
            JobStatus.VALIDATING_OUTPUT,
        )
        return templates.TemplateResponse(
            request,
            "job.html",
            ctx(request, _page_title("title_job", id=job.id), "jobs")
            | {
                "job": job,
                "back_href": "/jobs",
                "facts": facts,
                "job_id": job_id,
                "registry_codes": registry_codes(),
                "detected": detected,
                "log_tail_lines": settings.ui.log_tail_lines,
                # Серия R §11: лист удаления — имя книги (no-JS ?sheet=
                # delete: POST-форма — браузер не умеет DELETE из формы,
                # API принимает POST как алиас; JS: <dialog>).
                "book_name": _bname,
                "book_title": _bmeta.get("title") or _bname,
                "book_cover": views.book_cover_facts(settings, _bpath, _bmeta),
                "indeterminate": _indeterminate,
                "progress_pct": (
                    None
                    if _indeterminate
                    else round((job.progress or 0.0) * 100)
                ),
                "sheet": sheet if sheet == "delete" else "",
                # no-JS-подтверждение: причина отказа (303 c ?confirm_err=)
                "confirm_err": confirm_err,
            },
        )

    # MTPE M6 (req 8b): страница постредактуры — server-render, no-JS.
    # Тот же views.postedit_view, что API (Rule 8); фильтр по флагу и
    # пагинация — ссылками (без JS, M6-9).
    @router.get("/jobs/{job_id}/postedit")
    def job_postedit_page(
        request: Request,
        job_id: str,
        page: int = Query(default=1, ge=1),
        flag: str | None = Query(default=None),
    ) -> Response:
        job: Job | None = repo.get(job_id)
        if job is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "jobs"),
                status_code=404,
            )
        data = views.postedit_view(settings, job, page, flag)
        return templates.TemplateResponse(
            request,
            "job_postedit.html",
            ctx(request, _page_title("title_job", id=job_id), "jobs")
            | {
                "job_id": job_id,
                "data": data,
                "flag": flag,
            },
        )

    # ------------------------------------------------------------- Экспорт (R7 no-JS, T15)
    # Тот же сервис, что API (Rule 8): форма → 303 на страницу-статус
    # (meta-refresh 1 с, образец R6 /imports/{id}); отказ — обратно на
    # лист с ?export_err= (строка причины).

    def _export_files(
        job_id: str | None, filename: str | None, names: list[str]
    ) -> list[dict[str, str]]:
        """Ссылки статуса-страницы: download-эндпоинт API (T17: href —
        переменные)."""
        if job_id is not None:
            return [
                {"name": name, "href": f"/api/jobs/{job_id}/export/{quote(name)}"}
                for name in names
            ]
        assert filename is not None  # взаимно с job_id (два вызова выше)
        return [
            {"name": name, "href": f"/api/books/{quote(filename)}/export/{quote(name)}"}
            for name in names
        ]

    def _export_status_page(
        request: Request,
        rec: dict[str, Any],
        files: list[dict[str, str]],
        back_href: str,
    ) -> Response:
        return templates.TemplateResponse(
            request,
            "export_status.html",
            ctx(request, _page_title("job_export_title"), "jobs")
            | {
                "state": rec["status"],
                "done": rec.get("done", 0),
                "total": rec.get("total", 0),
                "error": rec.get("error"),
                "files": files,
                "back_href": back_href,
            },
        )

    @router.get("/jobs/{job_id}/export")
    def job_export_status_page(request: Request, job_id: str) -> Response:
        """Статус экспорта задания (meta-refresh, R7 T15)."""
        if repo.get(job_id) is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "jobs"),
                status_code=404,
            )
        rec = latest_export(f"job:{job_id}")
        if rec is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "jobs"),
                status_code=404,
            )
        return _export_status_page(
            request,
            rec,
            _export_files(job_id, None, [str(n) for n in rec.get("files", [])]),
            f"/jobs/{job_id}",
        )

    @router.post("/jobs/{job_id}/export")
    def job_export_form(
        request: Request,
        job_id: str,
        # B006: дефолт обязателен (FastAPI: default — только через `=`);
        # список handler не мутирует (fail-closed «нет выбора» — 400)
        objects: Annotated[list[str], Form()] = [],  # noqa: B006
        formats: Annotated[list[str], Form()] = [],  # noqa: B006
        layout: str = Form(default="append_block"),
    ) -> Response:
        """Форма листа экспорта (no-JS, страница ?sheet=export&job=)."""
        job = repo.get(job_id)
        if job is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "jobs"),
                status_code=404,
            )
        try:
            start_job_export(settings, job, objects, formats, layout)
        except (ExportPending, ExportInProgress, AppError) as exc:
            return RedirectResponse(
                f"/jobs?sheet=export&job={job_id}"
                f"&export_err={quote(exc.message)}",
                status_code=303,
            )
        return RedirectResponse(f"/jobs/{job_id}/export", status_code=303)

    @router.post("/jobs/export")
    def job_export_form_from_book(
        job: str = Form(default=""),
        book: str = Form(default=""),
        objects: Annotated[list[str], Form()] = [],  # noqa: B006
        formats: Annotated[list[str], Form()] = [],  # noqa: B006
        layout: str = Form(default="append_block"),
    ) -> Response:
        """Форма листа экспорта из карточки книги (no-JS): задание —
        radio (``job``); назад — на лист книги (?sheet=export&book=)."""

        def back(href_err: str) -> RedirectResponse:
            href = "/jobs?sheet=export"
            if book:
                href = f"/jobs?sheet=export&book={quote(book)}"
            return RedirectResponse(href + f"&export_err={quote(href_err)}", status_code=303)

        target = repo.get(job) if job else None
        if target is None:
            return back(_S["err_export_not_found"])
        try:
            start_job_export(settings, target, objects, formats, layout)
        except (ExportPending, ExportInProgress, AppError) as exc:
            return back(exc.message)
        return RedirectResponse(f"/jobs/{target.id}/export", status_code=303)

    @router.get("/books/{filename}/export")
    def book_export_status_page(request: Request, filename: str) -> Response:
        """Статус экспорта файла книги (meta-refresh, R7 T15)."""
        if views.book_epub_path(settings, filename) is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        rec = latest_export(f"book:{filename}")
        if rec is None:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                ctx(request, _page_title("title_not_found"), "books"),
                status_code=404,
            )
        return _export_status_page(
            request,
            rec,
            _export_files(None, filename, [str(n) for n in rec.get("files", [])]),
            f"/books/{quote(filename)}",
        )

    @router.post("/books/{filename}/export")
    def book_export_form(
        request: Request,
        filename: str,
        format: str = Form(default=""),
    ) -> Response:
        """Форма «Файл книги» (no-JS, лист карточки без заданий)."""
        try:
            start_book_export(settings, filename, format)
        except (ExportInProgress, AppError) as exc:
            return RedirectResponse(
                f"/jobs?sheet=export&book={quote(filename)}"
                f"&export_err={quote(exc.message)}",
                status_code=303,
            )
        return RedirectResponse(f"/books/{quote(filename)}/export", status_code=303)

    # ------------------------------------------------------------- Settings (серия R §5, R8)

    def _live(request: Request) -> Settings:
        """R8 (ТЗ §13): живые настройки из app.state — после сохранения
        LLM-настроек процесс подменяет ``app.state.settings``, а замыкание
        ``build_router`` хранит стартовую копию."""
        return cast("Settings", request.app.state.settings)

    @router.get("/settings")
    def settings_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request, "settings.html", ctx(request, _page_title("title_settings"), "settings")
        )

    # --- R8: LLM-профили ---------------------------------------------------

    def _llm_form_ctx(
        request: Request,
        form: FormData | None,
        *,
        error: str | None = None,
        probe: dict[str, Any] | None = None,
        save_issues: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        """Контекст /settings/llm. ``form`` — при отказе: значения из формы
        (ввод сохраняется) поверх данных настроек; ``save_issues`` — сводка
        проблем при отказе сохранения (MTPE §6.4)."""
        st = _live(request)
        data: dict[str, Any] = views.llm_form_view(st)
        if form is not None:
            pipeline_value = form.get("pipeline")
            if pipeline_value is not None:
                data["pipeline"] = str(pipeline_value)
            for prof in data["profiles"]:
                name = str(prof["name"])
                for field in _LLM_EDITABLE_FIELDS:
                    value = form.get(f"{field}_{name}")
                    if value is not None:
                        prof[field] = str(value)
                prof["delete_key"] = form.get(f"delete_key_{name}") is not None
        out: dict[str, object] = {"back_href": "/settings", "form": data}
        if error is not None:
            out["error"] = error
        if probe is not None:
            out["probe"] = probe
        if save_issues is not None:
            out["save_issues"] = save_issues
            # сообщения у полей (MTPE §6.4): path
            # "llm.profiles.<name>.<field>" -> поле "<field>_<name>".
            field_msgs: dict[str, str] = {}
            for item in save_issues:
                parts = str(item.get("path", "")).split(".")
                if len(parts) == 4 and parts[0] == "llm" and parts[1] == "profiles":
                    code = str(item.get("code", ""))
                    field_msgs[f"{parts[3]}_{parts[2]}"] = _S.get(code, code)
            out["field_msgs"] = field_msgs
        return ctx(request, _page_title("title_settings_llm"), "settings") | out

    def _llm_save_fields(
        form: FormData,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Форма сохранения (JS + no-JS; M2: API-эндпоинт записи удалён, D122).

        Возврат (body, строка-ошибка). Профиль в теле — только если его
        base_url-поле есть в форме (R8: 3 профиля, M2: 5)."""
        profile_fields: dict[str, dict[str, Any]] = {}
        for name in ALL_PROFILE_NAMES:
            if form.get(f"base_url_{name}") is None:
                continue
            fields: dict[str, Any] = {
                "base_url": str(form.get(f"base_url_{name}") or "").strip(),
                "model": str(form.get(f"model_{name}") or "").strip(),
                "api_key": str(form.get(f"api_key_{name}") or "").strip(),
                "delete_key": form.get(f"delete_key_{name}") is not None,
                "extra_body": str(form.get(f"extra_body_{name}") or "").strip(),
            }
            for field, cast_, key in (
                ("temperature", float, "err_llm_bad_temperature"),
                ("retry_times", int, "err_llm_bad_retries"),
                ("concurrency", int, "err_llm_bad_concurrency"),
                ("timeout_seconds", float, "err_llm_bad_timeout"),
                ("warmup_timeout_s", float, "err_llm_bad_timeout"),
                ("top_k", int, "err_llm_bad_top_k"),
                ("max_segment_tokens", int, "err_llm_bad_max_segment_tokens"),
            ):
                raw = str(form.get(f"{field}_{name}") or "").strip()
                if raw:
                    try:
                        fields[field] = cast_(raw)
                    except ValueError:
                        return None, _S[key]
            ratio_raw = str(form.get(f"max_tokens_ratio_{name}") or "").strip()
            if ratio_raw:
                try:
                    fields["max_tokens_ratio"] = float(ratio_raw)
                except ValueError:
                    return None, _S["err_llm_bad_max_tokens_ratio"]
            stop_raw = str(form.get(f"stop_{name}") or "").strip()
            if stop_raw:
                fields["stop"] = [line for line in stop_raw.split("\n") if line != ""]
            profile_fields[name] = fields
        return (
            {"pipeline": str(form.get("pipeline") or ""), "profiles": profile_fields},
            None,
        )

    def _llm_apply(
        request: Request, pipeline: str, profile_fields: dict[str, Any]
    ) -> tuple[Settings | None, str | None, list[dict[str, str]] | None]:
        """Общая запись LLM-настроек (JS-API и no-JS — Rule 8): сервис
        + подмена app.state. Возврат (Settings, строка-ошибка, сводка проблем)."""
        config_path = request.app.state.config_path
        env_path = request.app.state.env_path
        if config_path is None or env_path is None:
            return None, _S["err_llm_paths_missing"], None
        try:
            new_settings = update_llm_config(
                pipeline=pipeline or None,
                profile_fields=profile_fields,
                config_path=config_path,
                env_path=env_path,
            )
        except SettingsIncompleteError as exc:
            # details["issues"] — пары (path, code) (формат update_llm_config).
            raw = exc.details.get("issues", []) if isinstance(exc.details, dict) else []
            issues: list[dict[str, str]] = []
            for item in raw if isinstance(raw, list) else []:
                if isinstance(item, SettingsIssue):
                    issues.append({"path": item.path, "code": item.code})
                elif isinstance(item, (list, tuple)) and len(item) == 2:
                    issues.append({"path": str(item[0]), "code": str(item[1])})
            return None, _S["llm_save_incomplete"], issues
        except AppError as exc:
            return None, exc.message, None
        request.app.state.settings = new_settings
        request.app.state.llm_settings_issues = validate_llm_settings(new_settings)
        return new_settings, None, None

    @router.get("/settings/llm")
    def settings_llm_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request, "settings_llm.html", _llm_form_ctx(request, None)
        )

    @router.post("/settings/llm")
    async def settings_llm_save(request: Request) -> Response:
        """MTPE §6.4 no-JS: сохранение — тот же сервис, что PUT (Rule 8).

        Успех — 303; отказ валидации — 400, форма со сводкой проблем."""
        form = await request.form()
        body, form_error = _llm_save_fields(form)
        if body is None:
            return templates.TemplateResponse(
                request,
                "settings_llm.html",
                _llm_form_ctx(request, form, error=form_error),
                status_code=400,
            )
        new, error, issues = _llm_apply(
            request,
            str(body["pipeline"]),
            cast(dict[str, Any], body["profiles"]),
        )
        if new is None:
            # неполные для способа — 422 (MTPE TZ §5.3), файлы не тронуты.
            return templates.TemplateResponse(
                request,
                "settings_llm.html",
                _llm_form_ctx(request, form, error=error, save_issues=issues),
                status_code=422,
            )
        logger.info(
            "llm_save(no-js): user=%s pipeline=%s",
            _basic_user(request),
            new.translation.pipeline,
        )
        return RedirectResponse("/settings/llm", status_code=303)

    @router.post("/settings/llm/probe")
    async def settings_llm_probe(request: Request) -> Response:
        """MTPE §6.5 no-JS: проба профиля — кнопка формы (formaction,
        name="profile") отправляет форму сюда; результат — баннер (200).
        Конфиг НЕ пишет (тот же сервис, что POST /api/llm/probe)."""
        form = await request.form()
        st = _live(request)
        profile = str(form.get("profile") or "")
        if profile not in ALL_PROFILE_NAMES:
            return templates.TemplateResponse(
                request,
                "settings_llm.html",
                _llm_form_ctx(
                    request,
                    form,
                    error=_S["err_llm_unknown_profile"].format(profile=profile or "—"),
                ),
                status_code=400,
            )
        base_url = str(form.get(f"base_url_{profile}") or "")
        try:
            temperature: float | None = float(
                str(form.get(f"temperature_{profile}") or "").strip()
            )
        except ValueError:
            temperature = None
        if temperature is None:
            result: dict[str, Any] = {"ok": False, "message": _S["err_llm_bad_temperature"]}
        else:
            probe_body: dict[str, Any] = {
                "profile": profile,
                "base_url": base_url,
                "model": str(form.get(f"model_{profile}") or ""),
                "api_key": str(form.get(f"api_key_{profile}") or ""),
                "temperature": temperature,
                "extra_body": str(form.get(f"extra_body_{profile}") or ""),
            }
            top_k_raw = str(form.get(f"top_k_{profile}") or "").strip()
            if top_k_raw:
                probe_body["top_k"] = top_k_raw
            stop_raw = str(form.get(f"stop_{profile}") or "").strip()
            if stop_raw:
                probe_body["stop"] = [line for line in stop_raw.split("\n") if line]
            try:
                data = probe_request(probe_body, st)
            except AppError as exc:
                result = {"ok": False, "message": exc.message}
            else:
                conflict = probe_conflict(repo, data["base_url"], data["model"])
                if conflict is not None:
                    result = {"ok": False, "message": conflict}
                else:
                    result = probe_profile(data)
        result["profile"] = profile
        return templates.TemplateResponse(
            request,
            "settings_llm.html",
            _llm_form_ctx(request, form, probe=result),
        )

    @router.get("/settings/languages")
    def settings_languages_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "settings_languages.html",
            ctx(request, _page_title("title_languages"), "settings")
            | {
                "back_href": "/settings",
                "data": views.languages_view(_live(request)),
            },
        )

    @router.get("/settings/prompts")
    def settings_prompts_page(request: Request) -> Response:
        st = _live(request)
        return templates.TemplateResponse(
            request,
            "settings_prompts.html",
            ctx(request, _page_title("title_prompts"), "settings")
            | {
                "back_href": "/settings",
                "rows": views.prompts_view(st),
                "tasks": views.prompts_tasks_view(st),
                "registry_codes": registry_codes(),
            },
        )

    # --- R8: внешний вид -----------------------------------------------------

    @router.get("/settings/appearance")
    def settings_appearance_page(request: Request) -> Response:
        """Тема/плотность — cookies u3-theme/u3-density (отсутствуют =
        системная/обычная; читает inline-скрипт base.html)."""
        theme = request.cookies.get("u3-theme", "")
        density = request.cookies.get("u3-density", "")
        return templates.TemplateResponse(
            request,
            "settings_appearance.html",
            ctx(request, _page_title("title_settings_appearance"), "settings")
            | {
                "back_href": "/settings",
                "theme": theme if theme in ("light", "dark") else "",
                "density": density if density in ("compact",) else "",
            },
        )

    @router.post("/settings/appearance")
    async def settings_appearance_save(request: Request) -> Response:
        form = await request.form()
        theme = str(form.get("theme") or "")
        density = str(form.get("density") or "")
        response = RedirectResponse("/settings/appearance", status_code=303)
        if theme in ("light", "dark"):
            response.set_cookie("u3-theme", theme, max_age=365 * 24 * 3600, samesite="strict")
        else:
            response.delete_cookie("u3-theme")
        if density == "compact":
            response.set_cookie("u3-density", density, max_age=365 * 24 * 3600, samesite="strict")
        else:
            response.delete_cookie("u3-density")
        return response

    # --- R8: обслуживание ----------------------------------------------------

    def _maintenance_ctx(
        request: Request, *, error: str | None = None
    ) -> dict[str, object]:
        st = _live(request)
        out: dict[str, object] = {
            "back_href": "/settings",
            "data": views.maintenance_view(st, repo),
        }
        if error is not None:
            out["error"] = error
        return ctx(request, _page_title("title_settings_maintenance"), "settings") | out

    @router.get("/settings/maintenance")
    def settings_maintenance_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request, "settings_maintenance.html", _maintenance_ctx(request)
        )

    @router.post("/settings/maintenance/logs-clear")
    def settings_maintenance_logs_clear(request: Request) -> Response:
        """no-JS «Очистить логи» — тот же механизм, что POST /api/logs/clear."""
        st = _live(request)
        clear_app_log(st, _basic_user(request))
        return RedirectResponse("/settings/maintenance", status_code=303)

    @router.post("/settings/maintenance/purge")
    async def settings_maintenance_purge(
        request: Request,
        days: str = Form(default=""),
        confirm_token: str = Form(default=""),
    ) -> Response:
        """R8 no-JS purge — два шага (механизм общий с /api/jobs/purge —
        views.jobs_purge_*): шаг 1 — страница подтверждения с токеном,
        шаг 2 — удаление ровно перечисленного. Дней: пусто =
        ui.purge_min_age_days, иначе число ≥ 0."""
        st = _live(request)
        parsed = _parse_purge_days(days.strip(), st.ui.purge_min_age_days)
        if isinstance(parsed, str):
            return templates.TemplateResponse(
                request,
                "settings_maintenance.html",
                _maintenance_ctx(request, error=parsed),
                status_code=400,
            )
        min_age_days: int | float = parsed
        dry = views.jobs_purge_resolve(
            st,
            repo,
            statuses=sorted(s.value for s in TERMINAL_STATES),
            book=None,
            source=None,
            target=None,
            min_age_days=min_age_days,
        )
        if not confirm_token:
            if not dry["job_ids"]:
                return templates.TemplateResponse(
                    request,
                    "settings_maintenance.html",
                    _maintenance_ctx(
                        request,
                        error=_S["err_purge_no_match"].format(days=min_age_days),
                    ),
                    status_code=409,
                )
            return templates.TemplateResponse(
                request,
                "settings_maintenance_purge.html",
                ctx(request, _page_title("maint_purge_confirm_title"), "settings")
                | {
                    "back_href": "/settings/maintenance",
                    "days": min_age_days,
                    "dry": dry,
                    "purge_info": _S["maint_purge_confirm_info"].format(
                        count=dry["count"], bytes=views.human_size(dry["total_bytes"])
                    ),
                },
            )
        if not hmac.compare_digest(confirm_token, dry["confirm_token"]):
            return templates.TemplateResponse(
                request,
                "settings_maintenance_purge.html",
                ctx(request, _page_title("maint_purge_confirm_title"), "settings")
                | {
                    "back_href": "/settings/maintenance",
                    "days": min_age_days,
                    "dry": dry,
                    "purge_info": _S["maint_purge_confirm_info"].format(
                        count=dry["count"], bytes=views.human_size(dry["total_bytes"])
                    ),
                    "error": _S["err_purge_token"],
                },
                status_code=409,
            )
        try:
            rows = views.jobs_purge_execute(st, repo, dry["job_ids"])
        except AppError as exc:
            return templates.TemplateResponse(
                request,
                "settings_maintenance.html",
                _maintenance_ctx(request, error=exc.message),
                status_code=409,
            )
        logger.info(
            "maintenance_purge(no-js): user=%s ids=%s rows=%d",
            _basic_user(request),
            dry["job_ids"],
            rows,
        )
        return RedirectResponse("/settings/maintenance", status_code=303)

    # --- R8: о системе -------------------------------------------------------

    @router.get("/settings/about")
    def settings_about_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "settings_about.html",
            ctx(request, _page_title("title_settings_about"), "settings")
            | {
                "back_href": "/settings",
                "about": views.about_view(_live(request)),
            },
        )

    @router.get("/readme")
    def readme_page(request: Request) -> Response:
        st = _live(request)
        path = st.storage.root / "README.md"
        content = path.read_text(encoding="utf-8") if path.is_file() else ""
        return templates.TemplateResponse(
            request,
            "readme.html",
            ctx(request, _page_title("title_readme"), "settings")
            | {
                "back_href": "/settings/about",
                "content": content,
                "missing": not content,
            },
        )

    # V1 §6: dev-kit (S14) — витрина компонентов; строки — strings.py
    # (kit_*), токены — :root style.css (сервером, см. _kit_tokens).
    # §7.9: доступна только при ui.dev_kit=true, иначе 404 (по умолчанию false).
    @router.get("/dev/kit", include_in_schema=False)
    def dev_kit_page(request: Request) -> Response:
        if not _live(request).ui.dev_kit:
            raise HTTPException(status_code=404)
        return templates.TemplateResponse(
            request,
            "dev_kit.html",
            ctx(request, _page_title("title_dev_kit"), "")
            | {"swatches": _kit_tokens()},
        )

    @router.post("/logout")
    def logout_post(request: Request) -> Response:
        """Выйти (ТЗ §13 «О системе»): cookie-логин удаляется, 303 /login."""
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(LOGIN_COOKIE)
        return response

    # --- старые пути: 301 (серия R §5) ------------------------------------
    @router.get("/languages", include_in_schema=False)
    def languages_redirect() -> Response:
        return RedirectResponse("/settings/languages", status_code=301)

    @router.get("/models", include_in_schema=False)
    def models_redirect() -> Response:
        return RedirectResponse("/settings/llm", status_code=301)

    @router.get("/prompts", include_in_schema=False)
    def prompts_redirect() -> Response:
        return RedirectResponse("/settings/prompts", status_code=301)

    return router
