# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""R6 импорт (TZ §8): не-EPUB-книга → EPUB в потоке веб-процесса
(арена воркера не занимается), состояние
``checking → converting → validating → language → done | error``.

Реестр — в памяти веб-процесса (``GET /api/imports/{id}``); после
рестарта неизвестный id → 404, осиротевшие ``<name>.source.*`` без
пары ``.epub`` — sweep при старте (``sweep_orphans``). Оригинал
после успешного импорта сохраняется (TZ §8: доступный для повторной
конвертации). Ошибка на любом шаге — ``error`` + причина, созданные
файлы удаляются (fail closed).
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.adapters.convert import IMPORT_FLAGS, convert
from app.adapters.epub_engine.epubcheck import facts as epubcheck_facts
from app.adapters.epub_engine.epubcheck import run_epubcheck
from app.core.config import Settings
from app.core.errors import AppError
from app.services.analyze import scan_book
from app.services.covers import process_cover
from app.web import views
from app.web.strings import UI_STRINGS

logger = logging.getLogger("app.web")
_S = UI_STRINGS

STATUS_DONE = "done"
STATUS_ERROR = "error"

_registry: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

# Имя книги ``u1-<uuid8>-<файл>``; для заголовка уберём служебный префикс
# и расширение — иначе calibre кладёт в dc:title имя входного файла
# (``u1-…-…source``), и мусор виден в карточке.
_TITLE_PREFIX_RE = re.compile(r"^u1-[0-9a-f]{8}-")
# Явный «…, by …» / «… - by …» в имени файла → автор (только с разделителем,
# чтобы не резать «A Story by the Sea»).
_BY_AUTHOR_RE = re.compile(r"[,;_\-–—]\s*by\s+", re.IGNORECASE)


def title_author(name: str) -> tuple[str, str | None]:
    """Чистый заголовок (и автор, если в имени файла явно «…, by …»).

    Ввод — имя книги ``u1-<uuid8>-<файл>`` (с расширением). Выход —
    ``(заголовок, автор|None)``; расширение и служебный префикс не в
    заголовок попадают."""
    stem = _TITLE_PREFIX_RE.sub("", name)
    stem = Path(stem).stem.strip() or name
    match = _BY_AUTHOR_RE.search(stem)
    if match:
        title, author = stem.split(match.group(0), 1)
        title, author = title.strip(), author.strip()
        if title and author:
            return title, author
    return stem, None


def get_import(import_id: str) -> dict[str, Any] | None:
    with _lock:
        rec = _registry.get(import_id)
        return dict(rec) if rec is not None else None


def _set(import_id: str, **fields: Any) -> None:
    with _lock:
        _registry[import_id].update(fields)


def sweep_orphans(settings: Settings) -> int:
    """Осиротевшие ``<name>.source.*`` без пары ``.epub`` (рестарт
    посреди конвертации) — удаление при старте веб-процесса."""
    input_dir = settings.input_dir
    if not input_dir.is_dir():
        return 0
    removed = 0
    for path in input_dir.glob("*.source.*"):
        ext = path.suffix
        head = path.name[: -len(ext)] if ext else path.name
        if not head.endswith(".source"):
            continue
        stem = head[: -len(".source")]
        if not (input_dir / f"{stem}.epub").is_file():
            path.unlink(missing_ok=True)
            removed += 1
    if removed:
        logger.info("import_sweep: removed=%d orphan sources", removed)
    return removed


def start_import(settings: Settings, src: Path, name: str, fmt: str) -> str:
    """Запуск конвертации ``<name>.source.<ext> → <name>.epub`` в
    фоновом потоке. Возврат — import_id."""
    import_id = uuid.uuid4().hex[:12]
    with _lock:
        _registry[import_id] = {
            "id": import_id,
            "status": "checking",
            "step": "checking",
            "format": fmt,
            "name": None,
            "error": None,
            "created_at": time.time(),
        }
    thread = threading.Thread(
        target=_run_import,
        args=(settings, import_id, src, name, fmt),
        daemon=True,
    )
    thread.start()
    return import_id


def _run_import(
    settings: Settings, import_id: str, src: Path, name: str, fmt: str
) -> None:
    dest = src.parent / f"{name}.epub"
    cover_path: str | None = None
    try:
        _set(import_id, status="converting", step="converting")
        title, author = title_author(name)
        flags = (*IMPORT_FLAGS, "--title", title)
        if author:
            flags = (*flags, "--authors", author)
        ok, reason = convert(
            settings,
            src,
            dest,
            timeout=settings.converter.timeout_import_seconds,
            flags=flags,
        )
        if not ok:
            raise AppError(_S["err_convert_failed"].format(reason=reason))
        _set(import_id, status="validating", step="validating")
        outcome = run_epubcheck(
            settings.epubcheck_jar_path, dest, settings.epubcheck.timeout_seconds
        )
        if outcome.available and (outcome.fatals or outcome.errors):
            first = " ".join((*outcome.fatals, *outcome.errors)[:3])
            raise AppError(
                _S["err_import_epubcheck"].format(
                    n=len(outcome.fatals) + len(outcome.errors), first=first
                )
            )
        _set(import_id, status="language", step="language")
        try:
            meta = scan_book(settings, dest)
        except AppError as exc:
            raise AppError(_S["err_import_scan"].format(reason=exc.message)) from exc
        cover = process_cover(dest, settings.input_dir / ".covers")
        cover_path = cover.cover_path
        meta = {
            **views.book_meta_defaults(),
            **meta,
            "imported_at": time.time(),
            "source_format": fmt,
            "epubcheck": epubcheck_facts(outcome),
            "cover_path": cover_path,
            "cover": cover.status,
            "cover_sha256": cover.sha256,
        }
        # автор — из имени файла («…, by …»); scan_book автора не читает
        if author is not None and not meta.get("author"):
            meta["author"] = author
        # основной язык пользователя → целевой при импорте (если не задан)
        main = settings.ui.main_language
        if main and not meta.get("target_default"):
            meta["target_default"] = main
        views.write_book_meta(dest, meta)
        _set(import_id, status=STATUS_DONE, step=STATUS_DONE, name=dest.name)
        logger.info("import_done: id=%s name=%s format=%s", import_id, dest.name, fmt)
    except AppError as exc:
        _fail(import_id, src, dest, cover_path, exc.message)
    except Exception as exc:  # неожиданное — fail closed, трейса в UI нет
        logger.exception("import_failed: id=%s", import_id)
        _fail(
            import_id,
            src,
            dest,
            cover_path,
            _S["err_import_internal"].format(reason=type(exc).__name__),
        )


def _fail(
    import_id: str, src: Path, dest: Path, cover_path: str | None, reason: str
) -> None:
    src.unlink(missing_ok=True)
    dest.unlink(missing_ok=True)
    views.book_meta_path(dest).unlink(missing_ok=True)
    if cover_path:
        (dest.parent / cover_path).unlink(missing_ok=True)
    _set(import_id, status=STATUS_ERROR, step=STATUS_ERROR, error=reason)
    logger.warning("import_error: id=%s reason=%s", import_id, reason)
