"""R7 экспорт (TZ §9): артефакты задания / исходник книги → 8 форматов.

Зеркало R6-импорта: фоновый поток веб-процесса (арена воркера не
занимается), реестр в памяти веб-процесса, fail closed (ошибка — файлы
пачки удаляются), TTL-очистка при старте веб-процесса
(``sweep_exports``, ``ui.export_ttl_hours``).

Объекты (множественный выбор): ``translation`` (target.epub),
``learning`` (learning.epub), ``cards`` (набор S5 как есть), ``reports``
(три JSON — всегда в вложенный ZIP). Форматы (8): ``epub`` — напрямую
(копия), ``md`` — сервисом (у calibre нет плагина вывода .md, замер
стенда, handoff R7 решение 12), остальные — ``ebook-convert``
(обёртка D110, формат — по расширению dst), таймаут
``converter.timeout_export_seconds``.
Один объект + один формат → один файл, иначе — один ZIP
(`<автор> — <название> (ru).<ext>`). Макеты билингвы — пост-обработка
EPUB до конвертации, LLM не запускается (handoff R7 решение 5).
"""

from __future__ import annotations

import json
import logging
import posixpath
import re
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

import lxml.etree

from app.adapters.convert import convert, probe
from app.core.config import Settings
from app.core.errors import AppError
from app.domain.job import Job, JobStatus
from app.epub import segmenter
from app.epub.aligner import resolve_positional_xpath
from app.learning.card_export import CARDS_FILES
from app.web import views
from app.web.strings import UI_STRINGS

logger = logging.getLogger("app.web")
_S = UI_STRINGS

#: Объекты листа экспорта (TZ §9 «Что именно экспортируется»).
EXPORT_OBJECTS: tuple[str, ...] = ("translation", "learning", "cards", "reports")
#: Форматы вывода (TZ §9 «Форматы вывода»): epub — напрямую,
#: md — сервисом (у calibre нет вывода .md, решение 12),
#: остальные — конвертация (формат — по расширению).
EXPORT_FORMATS: tuple[str, ...] = (
    "epub",
    "pdf",
    "docx",
    "fb2",
    "azw3",
    "txt",
    "md",
    "htmlz",
)
#: Макеты билингвы (TZ §9 «Макет билингвы»).
LAYOUTS: tuple[str, ...] = ("append_block", "translation_only", "two_columns")
LAYOUT_TWO_COLUMN_FORMATS: frozenset[str] = frozenset({"pdf", "htmlz"})
#: Класс скрытия оригинальных блоков (макет «Только перевод»).
LAYOUT_CLASS = "u3-export-orig"
#: Отчёты объекта ``reports`` — вложенный ZIP (TZ §9 «одним ZIP»).
REPORT_FILES: tuple[str, ...] = (
    "quality.json",
    "alignment_report.json",
    "validation.json",
)
#: Имя файла/входного архива — без спецсимволов, ≤ 150 (handoff R7 №4).
_NAME_MAX = 150
_NAME_BAD_RE = re.compile(r"[\x00-\x1f/\\]")
_COMPLETED: tuple[JobStatus, ...] = (
    JobStatus.COMPLETED,
    JobStatus.COMPLETED_WITH_WARNINGS,
)


class ExportPending(AppError):
    """Задание не завершено (409)."""


class ExportInProgress(AppError):
    """Экспорт этого задания/книги уже выполняется (409)."""


class BookFileMissing(AppError):
    """Файл книги не найден (404)."""


# ------------------------------------------------------------------ имена


def sanitize_name(name: str) -> str:
    """Имя для файловой системы: без ``/``, ``\\``, управляющих символов;
    пробелы сжаты, ≤ 150; пусто → ``export``."""
    name = _NAME_BAD_RE.sub(" ", name or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:_NAME_MAX].strip(" .") or "export"


def export_base_name(title: str, author: str, language: str | None = None) -> str:
    """Основа имени по шаблону TZ §9: ``<автор> — <название> (ru)``.

    Пустые части пропускаются; ``language`` — суффикс языка задания
    (у исходника книги нет — это оригинал)."""
    base = " — ".join(p for p in (author, title) if p)
    if language:
        base = f"{base} ({language})" if base else f"({language})"
    return sanitize_name(base)


# ------------------------------------------------------------------ реестр

_registry: dict[str, dict[str, Any]] = {}
_active: dict[str, str] = {}  # scope_key -> export_id
_lock = threading.Lock()


def get_export(export_id: str) -> dict[str, Any] | None:
    with _lock:
        rec = _registry.get(export_id)
        return dict(rec) if rec is not None else None


def latest_export(scope_key: str) -> dict[str, Any] | None:
    """Последняя запись scope'а (активная — она; статус-страницы no-JS
    и поллинг читают её — один экспорт на задание/книгу)."""
    with _lock:
        last: dict[str, Any] | None = None
        for rec in _registry.values():
            if rec["scope_key"] != scope_key:
                continue
            if last is None or rec["created_at"] > last["created_at"]:
                last = rec
        return dict(last) if last is not None else None


def is_active(scope_key: str) -> bool:
    with _lock:
        rec = _registry.get(_active.get(scope_key, ""))
        return bool(rec is not None and rec["status"] == "converting")


def _set(export_id: str, **fields: Any) -> None:
    with _lock:
        _registry[export_id].update(fields)


def _release(export_id: str) -> None:
    with _lock:
        rec = _registry.get(export_id)
        if rec is not None and _active.get(rec["scope_key"]) == export_id:
            del _active[rec["scope_key"]]


def _cleanup(paths: list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


# ------------------------------------------------------------------ макеты


def _localname(el: lxml.etree._Element) -> str:
    return segmenter.localname(el)


def _add_class(el: lxml.etree._Element, cls: str) -> None:
    current = el.get("class") or ""
    if cls in current.split():
        return
    el.set("class", (current + " " + cls).strip())


def _find_element_parent(el: lxml.etree._Element) -> lxml.etree._Element | None:
    """Родитель-элемент (комментарии/PI в lxml — тоже «сёстры» в
    getprevious-цепочке; для чистых XHTML они отсутствуют, но гоняем
    проверку тега: у комментария тег не str)."""
    parent = el.getparent()
    if parent is None or not isinstance(parent.tag, str):
        return None
    return parent


def _preceding_sibling_element(
    el: lxml.etree._Element,
) -> lxml.etree._Element | None:
    prev = el.getprevious()  # type: ignore[attr-defined]
    while prev is not None and not isinstance(prev.tag, str):
        prev = prev.getprevious()
    return prev  # type: ignore[no-any-return]


def _layout_document(
    data: bytes, aligned: list[dict[str, Any]], layout: str
) -> bytes:
    """Один spine-документ: пары из aligned-карты → класс/таблица.

    Пары дедуплицируются по translation_xpath (blockquote даёт две пары
    с одним переводом). Оригинал = предшествующий sibling перевода с
    ТОЖЕ тегами (движок копирует тег, E1 §3); проверка не прошла — пара
    не трогается (fail-safe, handoff R7 решение 5).

    Две фазы: сначала все пары резолвятся в неизменённом дереве —
    two_columns меняет его (таблица вместо <p>), и позиционные
    xpath следующих пар перестают резолвиться (индексы сдвигаются)."""
    root = lxml.etree.fromstring(data)
    ns = root.nsmap.get(None)  # type: ignore[attr-defined]

    def el(tag: str) -> str:
        return f"{{{ns}}}{tag}" if ns else tag

    style = f".{LAYOUT_CLASS}{{display:none}}"
    seen: set[int] = set()
    pairs: list[tuple[lxml.etree._Element, lxml.etree._Element]] = []
    for pair in aligned:
        trans = resolve_positional_xpath(root, pair.get("translation_xpath") or "")
        if trans is None or id(trans) in seen:
            continue
        seen.add(id(trans))
        orig = _preceding_sibling_element(trans)
        if orig is None or _localname(orig) != _localname(trans):
            continue
        pairs.append((trans, orig))
    changed = 0
    for trans, orig in pairs:
        parent = _find_element_parent(orig)
        if parent is None:
            continue
        if layout == "translation_only":
            _add_class(orig, LAYOUT_CLASS)
            changed += 1
        elif layout == "two_columns":
            # Только пары, где оригинал — прямой ребёнок <body>
            # (parent — общий с переводом; вложенные — остаются в append,
            # handoff R7 решение 5).
            if _localname(parent) != "body":
                continue
            table = lxml.etree.Element(el("table"))
            table.set(
                "style",
                "width:100%;border-collapse:collapse;table-layout:fixed;"
                "margin:0 0 1em",
            )
            tr = lxml.etree.SubElement(table, el("tr"))
            td1 = lxml.etree.SubElement(tr, el("td"))
            td1.set("style", "width:50%;vertical-align:top;padding:0 .5em 0 0")
            td2 = lxml.etree.SubElement(tr, el("td"))
            td2.set("style", "width:50%;vertical-align:top")
            parent.replace(orig, table)  # type: ignore[attr-defined]
            td1.append(orig)
            td2.append(trans)  # переносит из исходного родителя
            changed += 1
    if layout == "translation_only" and changed:
        head = root.find(el("head"))
        if head is not None:
            present = any(
                _localname(st) == "style" and style in (st.text or "")
                for st in head
            )
            if not present:
                style_el = lxml.etree.Element(el("style"))
                style_el.text = style
                head.insert(0, style_el)  # type: ignore[attr-defined]
    # standalone — сохранить декларацию исходного документа (EPUB XHTML)
    return lxml.etree.tostring(  # type: ignore[call-overload, no-any-return]
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def apply_layout(
    src_epub: Path, dst_epub: Path, layout: str, alignment: dict[str, Any]
) -> int:
    """Пост-обработка билингв-EPUB (TZ §9): LLM не запускается.

    ``append_block`` — копия без правок. Остальные — правка только
    spine-документов из ``aligned`` карты; прочие записи архива
    копируются побайтово."""
    if layout == "append_block":
        shutil.copyfile(src_epub, dst_epub)
        return 0
    docs = {
        d["doc"]: d.get("aligned", [])
        for d in alignment.get("docs", [])
        if isinstance(d, dict) and d.get("doc") and d.get("aligned")
    }
    changed = 0
    with (
        zipfile.ZipFile(src_epub) as zin,
        zipfile.ZipFile(dst_epub, "w", zipfile.ZIP_DEFLATED) as zout,
    ):
        for info in zin.infolist():
            data = zin.read(info.filename)
            aligned = docs.get(info.filename)
            if aligned:
                data = _layout_document(data, aligned, layout)
                changed += 1
            zout.writestr(info, data)
    return changed


# ------------------------------------------------------------------ markdown

#: Calibre не умеет выводить .md (замер стенда: «No plugin to handle») —
#: Markdown генерирует сам сервис: spine-документы по порядку,
#: XHTML → Markdown (handoff R7 решение 12).
_MD_CONTAINER = "META-INF/container.xml"
_MD_OPF_NS = "http://www.idpf.org/2007/opf"
_MD_OPF_MEDIA = "application/oebps-package+xml"
_MD_DOC_MEDIA = ("application/xhtml+xml", "text/html")
_MD_SKIP = frozenset({"script", "style", "head", "title", "meta", "link"})


def _md_inline(el: lxml.etree._Element) -> str:
    """Инлайн-контент → Markdown: strong/em/code/a сохраняются, остальное
    сплющивается (минимальный конвертер, разметка глубже — по тексту)."""
    if _localname(el) in _MD_SKIP:
        return ""
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        if not isinstance(child.tag, str):
            continue
        inner = _md_inline(child)
        tag = _localname(child)
        if tag in ("strong", "b") and inner:
            parts.append(f"**{inner.strip()}**")
        elif tag in ("em", "i") and inner:
            parts.append(f"*{inner.strip()}*")
        elif tag == "code" and inner:
            parts.append(f"`{inner}`")
        elif tag == "a" and inner:
            href = child.get("href")
            parts.append(f"[{inner}]({href})" if href else inner)
        else:
            parts.append(inner)
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _md_blocks(el: lxml.etree._Element, out: list[str]) -> None:  # noqa: PLR0912, PLR0915 — конвертер: 10 блочных типов
    """Блочное дерево → Markdown-блоки в порядке документа.

    Блоки с классом ``u3-export-orig`` (макет «Только перевод»)
    пропускаются — как и в остальных форматах, видит то, что показывает
    макет (двухколоночные таблицы — строкой «| оригинал | перевод |»)."""
    for child in el:
        if not isinstance(child.tag, str):
            continue
        tag = _localname(child)
        if tag in _MD_SKIP:
            continue
        if LAYOUT_CLASS in (child.get("class") or "").split():
            continue
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = segmenter.normalize_text(_md_inline(child))
            if text:
                out.append("#" * int(tag[1]) + " " + text)
        elif tag == "p":
            text = segmenter.normalize_text(_md_inline(child))
            if text:
                out.append(text)
        elif tag == "hr":
            out.append("---")
        elif tag == "pre":
            code = segmenter.full_text(child).strip()
            if code:
                out.append("```\n" + code + "\n```")
        elif tag == "blockquote":
            inner: list[str] = []
            _md_blocks(child, inner)
            if inner:
                lines = [
                    f"> {line}" if line.strip() else ">"
                    for block in inner
                    for line in block.splitlines()
                ]
                out.append("\n".join(lines))
        elif tag in ("ul", "ol"):
            items: list[str] = []
            for li in child:
                if not isinstance(li.tag, str) or _localname(li) != "li":
                    continue
                text = segmenter.normalize_text(_md_inline(li))
                if not text:
                    continue
                prefix = f"{len(items) + 1}. " if tag == "ol" else "- "
                items.append(prefix + text)
            if items:
                out.append("\n".join(items))
        elif tag == "table":
            rows: list[str] = []
            for tr in child.iter():
                if not isinstance(tr.tag, str) or _localname(tr) != "tr":
                    continue
                cells: list[str] = []
                for td in tr:
                    if not isinstance(td.tag, str) or _localname(td) not in (
                        "td",
                        "th",
                    ):
                        continue
                    text = segmenter.normalize_text(_md_inline(td)).replace(
                        "|", "\\|"
                    )
                    if text:
                        cells.append(text)
                if cells:
                    rows.append("| " + " | ".join(cells) + " |")
            if rows:
                out.append("\n".join(rows))
        else:
            _md_blocks(child, out)


def _xhtml_to_markdown(data: bytes) -> str:
    root = lxml.etree.fromstring(data)
    body = segmenter.find_body(root)
    if body is None:
        return ""
    blocks: list[str] = []
    _md_blocks(body, blocks)
    return ("\n\n".join(blocks) + "\n") if blocks else ""


def _spine_documents(epub: Path) -> list[bytes]:
    """Spine-документы по порядку (container.xml → OPF → manifest → spine),
    чтение из архива без распаковки; не-XHTML и отсутствующие — пропускаются."""
    with zipfile.ZipFile(epub) as zf:
        names = set(zf.namelist())
        if _MD_CONTAINER not in names:
            raise ValueError("в архиве нет container.xml")
        container = lxml.etree.fromstring(zf.read(_MD_CONTAINER))
        opf_path: str | None = None
        for node in container.iter():
            if not isinstance(node.tag, str) or _localname(node) != "rootfile":
                continue
            if node.get("media-type") == _MD_OPF_MEDIA:
                opf_path = node.get("full-path")
                break
        if not opf_path or opf_path not in names:
            raise ValueError(f"OPF не найден в архиве: {opf_path!r}")
        opf = lxml.etree.fromstring(zf.read(opf_path))
        manifest: dict[str, lxml.etree._Element] = {}
        for manifest_item in opf.findall(
            f"{{{_MD_OPF_NS}}}manifest/{{{_MD_OPF_NS}}}item"
        ):
            item_id = manifest_item.get("id")
            if item_id:
                manifest[item_id] = manifest_item
        docs: list[bytes] = []
        for itemref in opf.findall(f"{{{_MD_OPF_NS}}}spine/{{{_MD_OPF_NS}}}itemref"):
            item = manifest.get(itemref.get("idref") or "")
            if item is None:
                continue
            href = item.get("href")
            if not href or (item.get("media-type") or "") not in _MD_DOC_MEDIA:
                continue
            path = posixpath.normpath(
                posixpath.join(posixpath.dirname(opf_path), href)
            )
            if path in names:
                docs.append(zf.read(path))
    if not docs:
        raise ValueError("spine-документов в архиве нет")
    return docs


def _epub_to_markdown(epub: Path, dst: Path) -> None:
    """EPUB → Markdown (решение 12): calibre не выводит .md (замер стенда),
    поэтому сервис генерирует сам: spine по порядку, блоки, скрытые
    макетом, — пропущены."""
    try:
        parts = [_xhtml_to_markdown(data) for data in _spine_documents(epub)]
    except (
        OSError,
        ValueError,
        KeyError,
        lxml.etree.XMLSyntaxError,
    ) as exc:
        raise AppError(_S["err_export_internal"].format(reason=f"md: {exc}")) from exc
    text = "\n\n".join(part for part in parts if part)
    if not text:
        raise AppError(
            _S["err_export_internal"].format(reason="md: текста в документах нет")
        )
    dst.write_text(text, encoding="utf-8")


# ------------------------------------------------------------------ план


def _job_base(job: Job) -> str:
    """Имя по шаблону TZ §9: метаданные книги задания, фолбэк — имя файла."""
    source = Path(job.source_path)
    meta = views.read_book_meta(source)
    title = str(meta.get("title") or "").strip() or source.stem
    author = str(meta.get("author") or "").strip()
    return export_base_name(title, author, job.target_language)


def _job_plan(job: Job, objects: list[str], formats: list[str]) -> tuple[str, list[dict[str, Any]]]:
    """Пачка файлов: (base, items). item: kind — ``epub`` (копия или
    конвертация; ``src=None`` — исходный target.epub, макет применяется
    в раннере), ``copy`` (как есть), ``reports`` (вложенный ZIP)."""
    base = _job_base(job)
    items: list[dict[str, Any]] = []
    for obj in objects:
        if obj == "translation":
            for fmt in formats:
                items.append({"kind": "epub", "src": None, "name": f"{base}.{fmt}"})
        elif obj == "learning":
            for fmt in formats:
                items.append(
                    {
                        "kind": "epub",
                        "src": "learning.epub",
                        "name": f"{base} — study.{fmt}",
                    }
                )
        elif obj == "cards":
            for fname in CARDS_FILES.values():
                items.append(
                    {"kind": "copy", "src": fname, "name": f"{base} — {fname}"}
                )
        else:  # reports
            items.append({"kind": "reports", "name": f"{base} — reports.zip"})
    return base, items


# ------------------------------------------------------------------ запуск


def prepare_job_export(  # noqa: PLR0912 — fail-closed: каждая проверка — свой отказ
    settings: Settings, job: Job, objects: list[str], formats: list[str], layout: str
) -> tuple[str, list[dict[str, Any]]]:
    """Валидация (fail-closed, точные строки strings.py) + план.

    Единая точка для API и no-JS-страницы (Rule 8). Поднимает
    ``ExportPending`` (409), ``AppError`` (400)."""
    if job.status not in _COMPLETED:
        raise ExportPending(
            _S["err_export_pending"].format(status=job.status.value)
        )
    if not objects or not formats:
        raise AppError(_S["err_export_no_selection"])
    for value in objects:
        if value not in EXPORT_OBJECTS:
            raise AppError(_S["err_export_unknown_object"].format(value=value))
    for value in formats:
        if value not in EXPORT_FORMATS:
            raise AppError(_S["err_export_unknown_format"].format(value=value))
    if layout not in LAYOUTS:
        raise AppError(_S["err_export_unknown_layout"].format(value=layout))
    jdir = settings.job_dir(job.id)
    for obj in objects:
        if obj == "translation" and not (jdir / "target.epub").is_file():
            raise AppError(_S["err_export_object_missing"].format(name="target.epub"))
        if obj == "learning" and not (jdir / "learning.epub").is_file():
            raise AppError(
                _S["err_export_object_missing"].format(name="learning.epub")
            )
        if obj == "cards" and not any(
            (jdir / name).is_file() for name in CARDS_FILES.values()
        ):
            raise AppError(_S["err_export_object_missing"].format(name="cards"))
        if obj == "reports" and not any(
            (jdir / name).is_file() for name in REPORT_FILES
        ):
            raise AppError(_S["err_export_object_missing"].format(name="reports"))
    if layout != "append_block":
        # «Две колонки» — только при выбранном pdf/htmlz (конкретнее —
        # раньше общей причины недоступности макета).
        if layout == "two_columns" and not (set(formats) & LAYOUT_TWO_COLUMN_FORMATS):
            raise AppError(_S["err_export_layout_needs_two"])
        if job.mode != "bilingual":
            raise AppError(_S["err_export_layout_unavailable"])
        alignment = _read_json(jdir / "alignment_report.json") or {}
        if alignment.get("submit_mode") != "APPEND_BLOCK":
            raise AppError(_S["err_export_layout_unavailable"])
    return _job_plan(job, objects, formats)


def start_job_export(
    settings: Settings,
    job: Job,
    objects: list[str],
    formats: list[str],
    layout: str,
) -> str:
    """План + запуск фонового потока. Возврат ``export_id``; поднимает
    ``ExportPending`` (409) / ``ExportInProgress`` (409) / ``AppError``
    (400)."""
    base, items = prepare_job_export(settings, job, objects, formats, layout)
    scope = f"job:{job.id}"
    export_id = uuid.uuid4().hex[:12]
    with _lock:
        rec = _registry.get(_active.get(scope, ""))
        if rec is not None and rec["status"] == "converting":
            raise ExportInProgress(_S["err_export_in_progress"])
        _registry[export_id] = {
            "id": export_id,
            "scope_key": scope,
            "status": "converting",
            "done": 0,
            "total": len(items),
            "files": [],
            "error": None,
            "created_at": time.time(),
        }
        _active[scope] = export_id
    threading.Thread(
        target=_run_job_export,
        args=(settings, job, export_id, base, items, objects, formats, layout),
        daemon=True,
    ).start()
    return export_id


def prepare_book_export(settings: Settings, filename: str, fmt: str) -> tuple[str, str]:
    """Валидация экспорта исходника книги (TZ §15). Возврат
    ``(name, fmt)``. Поднимает ``BookFileMissing`` (404), ``AppError``
    (400)."""
    if fmt not in EXPORT_FORMATS:
        raise AppError(_S["err_export_unknown_format"].format(value=fmt))
    src = settings.input_dir / filename
    if not src.is_file():
        raise BookFileMissing(
            _S["err_export_book_not_found"].format(name=filename)
        )
    # md — сервисом (решение 12), конвертер не нужен
    if fmt not in ("epub", "md"):
        status, _ = probe(settings)
        if status != "ok":  # статус — строка, не bool (fail closed, R6)
            raise AppError(_S["err_converter_unavailable"])
    meta = views.read_book_meta(src)
    title = str(meta.get("title") or "").strip() or src.stem
    author = str(meta.get("author") or "").strip()
    return f"{export_base_name(title, author)}.{fmt}", fmt


def start_book_export(settings: Settings, filename: str, fmt: str) -> str:
    """Запуск фонового потока экспорта исходника книги. Возврат
    ``export_id``; подъёмает ``ExportInProgress`` (409)."""
    name, fmt = prepare_book_export(settings, filename, fmt)
    scope = f"book:{filename}"
    export_id = uuid.uuid4().hex[:12]
    with _lock:
        rec = _registry.get(_active.get(scope, ""))
        if rec is not None and rec["status"] == "converting":
            raise ExportInProgress(_S["err_export_in_progress"])
        _registry[export_id] = {
            "id": export_id,
            "scope_key": scope,
            "status": "converting",
            "done": 0,
            "total": 1,
            "files": [],
            "error": None,
            "created_at": time.time(),
        }
        _active[scope] = export_id
    threading.Thread(
        target=_run_book_export,
        args=(settings, export_id, filename, name, fmt),
        daemon=True,
    ).start()
    return export_id


# ------------------------------------------------------------------ раннеры


def _build_reports_zip(jdir: Path, dst: Path) -> None:
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in REPORT_FILES:
            path = jdir / name
            if path.is_file():
                zf.write(path, arcname=name)


def _build_zip(edir: Path, dst: Path, names: list[str]) -> None:
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            zf.write(edir / name, arcname=name)


def _run_job_export(  # noqa: PLR0912, PLR0915, PLR0917 — фоновая нить: 8 форматов
    settings: Settings,
    job: Job,
    export_id: str,
    base: str,
    items: list[dict[str, Any]],
    objects: list[str],
    formats: list[str],
    layout: str,
) -> None:
    jdir = settings.job_dir(job.id)
    edir = jdir / "export"
    edir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    layout_tmp: Path | None = None
    try:
        translation_src = jdir / "target.epub"
        if layout != "append_block" and "translation" in objects:
            alignment = _read_json(jdir / "alignment_report.json") or {}
            if alignment.get("submit_mode") != "APPEND_BLOCK":
                # Дубль проверки prepare (гонка правки файла — не
                # доводим до «половины пачки»).
                raise AppError(_S["err_export_layout_unavailable"])
            layout_tmp = edir / ".layout.tmp.epub"
            apply_layout(translation_src, layout_tmp, layout, alignment)
            translation_src = layout_tmp
        total = len(items)
        _set(export_id, total=total)
        for i, item in enumerate(items, start=1):
            _set(export_id, done=i - 1)
            dst = edir / item["name"]
            kind = item["kind"]
            if kind == "epub":
                src = (
                    translation_src
                    if item["src"] is None
                    else jdir / str(item["src"])
                )
                if item["name"].endswith(".epub"):
                    shutil.copyfile(src, dst)
                elif item["name"].endswith(".md"):
                    _epub_to_markdown(src, dst)  # calibre не выводит .md (решение 12)
                else:
                    ok, reason = convert(
                        settings,
                        src,
                        dst,
                        timeout=settings.converter.timeout_export_seconds,
                    )
                    if not ok:
                        raise AppError(
                            _S["err_convert_failed"].format(reason=reason)
                        )
            elif kind == "copy":
                shutil.copyfile(jdir / str(item["src"]), dst)
            else:  # reports
                _build_reports_zip(jdir, dst)
            created.append(dst)
            _set(export_id, done=i)
        files = [path.name for path in created]
        if len(files) > 1:
            outer = edir / f"{base}.zip"
            _build_zip(edir, outer, files)
            created.append(outer)
            files = [outer.name]
        _set(export_id, status="done", files=files)
        logger.info(
            "export_done: id=%s job=%s files=%s", export_id, job.id, files
        )
    except AppError as exc:
        _cleanup(created)
        _set(export_id, status="error", error=exc.message)
        logger.warning("export_error: id=%s reason=%s", export_id, exc.message)
    except Exception as exc:  # неожиданное — fail closed, трейса в UI нет
        _cleanup(created)
        _set(
            export_id,
            status="error",
            error=_S["err_export_internal"].format(reason=type(exc).__name__),
        )
        logger.exception("export_failed: id=%s", export_id)
    finally:
        if layout_tmp is not None:
            layout_tmp.unlink(missing_ok=True)
        _release(export_id)


def _run_book_export(
    settings: Settings,
    export_id: str,
    filename: str,
    name: str,
    fmt: str,
) -> None:
    src = settings.input_dir / filename
    edir = settings.input_dir / f"{filename}.export"
    edir.mkdir(exist_ok=True)
    created: list[Path] = []
    try:
        dst = edir / name
        if fmt == "epub":
            shutil.copyfile(src, dst)
        elif fmt == "md":
            _epub_to_markdown(src, dst)  # calibre не выводит .md (решение 12)
        else:
            ok, reason = convert(
                settings,
                src,
                dst,
                timeout=settings.converter.timeout_export_seconds,
            )
            if not ok:
                raise AppError(_S["err_convert_failed"].format(reason=reason))
        created.append(dst)
        _set(export_id, done=1, status="done", files=[dst.name])
        logger.info("export_done: id=%s book=%s file=%s", export_id, filename, name)
    except AppError as exc:
        _cleanup(created)
        _set(export_id, status="error", error=exc.message)
        logger.warning("export_error: id=%s reason=%s", export_id, exc.message)
    except Exception as exc:
        _cleanup(created)
        _set(
            export_id,
            status="error",
            error=_S["err_export_internal"].format(reason=type(exc).__name__),
        )
        logger.exception("export_failed: id=%s", export_id)
    finally:
        _release(export_id)


# ------------------------------------------------------------------ TTL


def _sweep_dir(directory: Path, cutoff: float) -> int:
    if not directory.is_dir():
        return 0
    removed = 0
    for path in directory.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    try:
        if not any(directory.iterdir()):
            directory.rmdir()
    except OSError:
        pass
    return removed


def sweep_exports(settings: Settings) -> int:
    """Очистка по TTL (TZ §9/§15): ``jobs/*/export/*`` и
    ``input/<name>.export/*`` старше ``ui.export_ttl_hours`` — при старте
    веб-процесса (рядом с ``sweep_orphans``)."""
    cutoff = time.time() - settings.ui.export_ttl_hours * 3600.0
    removed = 0
    jobs_dir = settings.jobs_dir
    if jobs_dir.is_dir():
        for job_dir in jobs_dir.iterdir():
            if job_dir.is_dir() and job_dir.name.startswith("j-"):
                removed += _sweep_dir(job_dir / "export", cutoff)
    if settings.input_dir.is_dir():
        for book_dir in settings.input_dir.glob("*.export"):
            removed += _sweep_dir(book_dir, cutoff)
    if removed:
        logger.info(
            "export_sweep: removed=%d files older than %.0f h",
            removed,
            settings.ui.export_ttl_hours,
        )
    return removed
