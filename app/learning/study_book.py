"""S5 (ТЗ_03): сборка учебного EPUB — дополнение выходного EPUB тракта (T).

Метод (ТЗ_03 §S5): НЕ «с нуля» — распаковка `target.epub`, вставка XHTML-
секций в КОНЕЦ `<body>` каждой главы (отдельные секции, смешивание с
текстом запрещено), правка OPF (manifest+spine), EPUB3-оглавления (nav) и
NCX (EPUB2), ДОБАВЛЕНИЕ финального документа «Ответы и охват» в конец
spine, упаковка обратно. Оригинальные документы сохраняются по байтам
(кроме вставок), `mimetype` — первой записью без сжатия (S5-1/S5-2/S5-3).

Структура главы (порядок секций): текст → Сводка → Ключевая лексика →
Выражения → Грамматика → Вопросы → Упражнения. Состав секций определяет
наличие артефактов (управляется уровнем в вышестоящих стадиях). Ответы —
ТОЛЬКО в финальном документе книги (S5-5): маркер «Ответ:» в добавленных
секциях глав запрещён.

Детерминизм: записям из `target.epub` сохраняются исходные имена, порядок
и `date_time`; новым записям — фиксированная `date_time`.
"""

from __future__ import annotations

import posixpath
import re
import shutil
import subprocess
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lxml import etree

from app.core.errors import AppError
from app.epub import segmenter

XHTML = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"
OPF_NS = "http://www.idpf.org/2007/opf"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
XML_NS = "http://www.w3.org/XML/1998/namespace"

#: Финальный документ «Ответы и охват» (относительно каталога OPF).
ANSWERS_HREF = "study-answers.xhtml"
ANSWERS_ID = "s5-answers"
ANSWERS_TITLE = "Ответы и охват"

#: Маркер ответа — ТОЛЬКО в финальном документе (S5-5).
ANSWER_MARKER = "Ответ:"

#: Порядок и заголовки секций главы.
SECTION_ORDER: tuple[str, ...] = (
    "summary",
    "vocab",
    "expressions",
    "grammar",
    "questions",
    "exercises",
)
SECTION_TITLES: dict[str, str] = {
    "summary": "Сводка главы",
    "vocab": "Ключевая лексика",
    "expressions": "Выражения",
    "grammar": "Грамматика",
    "questions": "Вопросы на понимание",
    "exercises": "Упражнения",
}

#: Фиксированная дата новых zip-записей (детерминизм повторной сборки).
_NEW_ENTRY_DATE = (1980, 1, 1, 0, 0, 0)

_OPTION_LETTERS = "АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩ"

_COVERAGE_RE = re.compile(r'data-s5-coverage="([0-9.]+)"')


def _el(parent: etree._Element, tag: str, **attrs: str) -> etree._Element:
    element = etree.SubElement(parent, f"{{{XHTML}}}{tag}")
    for key, value in attrs.items():
        element.set(key, value)
    return element


def _sub(parent: etree._Element, name: str, text: str | None = None) -> etree._Element:
    element = etree.SubElement(parent, f"{{{XHTML}}}{name}")
    if text is not None:
        element.text = text
    return element


def _span(parent: etree._Element, text: str) -> etree._Element:
    span = etree.SubElement(parent, f"{{{XHTML}}}span")
    span.text = text
    return span


def _make_section(chapter_id: str, section_type: str) -> etree._Element:
    section = etree.Element(f"{{{XHTML}}}section")
    section.set("class", "s5-section")
    section.set("id", f"s5-{section_type}-{chapter_id}")
    return section


def _serialize(root: etree._Element) -> bytes:
    doctype = root.getroottree().docinfo.doctype
    if doctype is not None:
        return etree.tostring(
            root, encoding="utf-8", xml_declaration=True, doctype=doctype
        )
    return etree.tostring(root, encoding="utf-8", xml_declaration=True)


# ------------------------------------------------------------------ структура


def locate_structure(archive: zipfile.ZipFile) -> tuple[str, str, str | None, str | None]:
    """(opf_path, каталог OPF, nav_path, ncx_path) по container.xml и manifest."""
    names = set(archive.namelist())
    if "META-INF/container.xml" not in names:
        raise AppError("S5: в target.epub нет META-INF/container.xml")
    container = etree.fromstring(archive.read("META-INF/container.xml"))
    rootfile = container.find(f"{{{CONTAINER_NS}}}rootfiles/{{{CONTAINER_NS}}}rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        raise AppError("S5: в container.xml нет rootfile")
    opf_path = rootfile.get("full-path")
    if opf_path not in names:
        raise AppError(f"S5: OPF {opf_path} отсутствует в target.epub")
    opf_dir = posixpath.dirname(opf_path)
    opf = etree.fromstring(archive.read(opf_path))
    manifest = opf.find(f"{{{OPF_NS}}}manifest")
    if manifest is None:
        raise AppError("S5: в OPF нет manifest")
    nav_path: str | None = None
    ncx_path: str | None = None
    for item in manifest.findall(f"{{{OPF_NS}}}item"):
        properties = (item.get("properties") or "").split()
        if "nav" in properties and nav_path is None:
            nav_path = join_path(opf_dir, item.get("href") or "")
        if item.get("media-type") == "application/x-dtbncx+xml" and ncx_path is None:
            ncx_path = join_path(opf_dir, item.get("href") or "")
    return opf_path, opf_dir, nav_path, ncx_path


def join_path(directory: str, href: str) -> str:
    if href.startswith("/"):
        return href.lstrip("/")
    base = directory + "/" if directory else ""
    return posixpath.normpath(base + href)


def append_sections_to_doc(doc_bytes: bytes, sections: Sequence[etree._Element]) -> bytes:
    """Вставка секций в КОНЕЦ `<body>`; остальной документ — без изменений."""
    root = etree.fromstring(doc_bytes, etree.XMLParser(remove_blank_text=False))
    body = segmenter.find_body(root)
    if body is None:
        raise AppError("S5: в документе главы нет <body>")
    for section in sections:
        body.append(section)
    return _serialize(root)


# ------------------------------------------------------------------ секции


def _vocab_word_cell(card: Mapping[str, Any]) -> str:
    parts: list[str] = []
    article = card.get("article")
    if isinstance(article, str) and article:
        parts.append(article)
    parts.append(str(card.get("lemma", "")))
    word = " ".join(parts)
    notes: list[str] = []
    plural = card.get("plural")
    if isinstance(plural, str) and plural:
        notes.append(f"мн. ч. {plural}")
    main_forms = card.get("main_forms")
    if isinstance(main_forms, Mapping) and main_forms.get("infinitive"):
        notes.append(f"inf. {main_forms['infinitive']}")
    gender = card.get("gender")
    if isinstance(gender, str) and gender:
        notes.append(gender)
    if card.get("separable") is True:
        notes.append("разделяемый")
    if notes:
        word += " (" + ", ".join(notes) + ")"
    return word


def _render_vocab(chapter_id: str, cards: Sequence[Mapping[str, Any]]) -> etree._Element:
    section = _make_section(chapter_id, "vocab")
    _sub(section, "h2", SECTION_TITLES["vocab"])
    table = _el(section, "table")
    head_row = _el(table, "tr")
    for head in ("Слово", "Перевод", "Значение", "Пример"):
        _sub(head_row, "th", head)
    for card in cards:
        row = _el(table, "tr")
        _sub(row, "td", _vocab_word_cell(card))
        _sub(row, "td", str(card.get("translation", "")))
        _sub(row, "td", str(card.get("meaning", "")))
        _sub(row, "td", str(card.get("example", "")))
    return section


def _render_expressions(chapter_id: str, records: Sequence[Mapping[str, Any]]) -> etree._Element:
    section = _make_section(chapter_id, "expressions")
    _sub(section, "h2", SECTION_TITLES["expressions"])
    ul = _el(section, "ul")
    for record in records:
        li = etree.SubElement(ul, f"{{{XHTML}}}li")
        surface = str(record.get("surface", ""))
        if surface:
            _sub(li, "b", surface)
            _span(li, " — ")
        translation = str(record.get("translation", ""))
        if translation:
            _span(li, translation)
        canonical = str(record.get("canonical", ""))
        if canonical and canonical != surface:
            _span(li, f" (формы: {canonical})")
    return section


def _render_grammar(chapter_id: str, notes: Sequence[Mapping[str, Any]]) -> etree._Element:
    section = _make_section(chapter_id, "grammar")
    _sub(section, "h2", SECTION_TITLES["grammar"])
    ul = _el(section, "ul")
    for note in notes:
        li = etree.SubElement(ul, f"{{{XHTML}}}li")
        surface = str(note.get("surface", ""))
        if surface:
            _sub(li, "b", surface)
            _span(li, " — ")
        explanation = str(note.get("explanation", ""))
        if explanation:
            _span(li, explanation)
    return section


def _options_list(parent: etree._Element, options: Sequence[str]) -> etree._Element:
    ul = _el(parent, "ul")
    for index, option in enumerate(options):
        letter = (
            _OPTION_LETTERS[index] if index < len(_OPTION_LETTERS) else str(index + 1)
        )
        _sub(ul, "li", f"{letter}) {option}")
    return ul


def _render_questions(chapter_id: str, questions: Sequence[Mapping[str, Any]]) -> etree._Element:
    section = _make_section(chapter_id, "questions")
    _sub(section, "h2", SECTION_TITLES["questions"])
    ol = _el(section, "ol")
    for question in questions:
        li = _sub(ol, "li", str(question.get("question", "")))
        options = question.get("options")
        if isinstance(options, Sequence) and options:
            _options_list(li, [str(o) for o in options])
    return section


def _render_exercises(chapter_id: str, exercises: Sequence[Mapping[str, Any]]) -> etree._Element:
    section = _make_section(chapter_id, "exercises")
    _sub(section, "h2", SECTION_TITLES["exercises"])
    ol = _el(section, "ol")
    for exercise in exercises:
        li = _sub(ol, "li", str(exercise.get("prompt", "")))
        options = exercise.get("options")
        if isinstance(options, Sequence) and options:
            _options_list(li, [str(o) for o in options])
    return section


def _render_summary(chapter_id: str, summary: Mapping[str, Any]) -> etree._Element:
    section = _make_section(chapter_id, "summary")
    _sub(section, "h2", SECTION_TITLES["summary"])
    brief = str(summary.get("brief", "")).strip()
    if brief:
        p = _el(section, "p")
        _sub(p, "b", "Кратко. ")
        _span(p, brief)
    extended = str(summary.get("extended", "")).strip()
    if extended:
        _sub(section, "p", extended)
    for label, key in (
        ("Ключевые события:", "key_events"),
        ("Сущности:", "entities"),
        ("Темы:", "themes"),
        ("Понятия:", "concepts"),
    ):
        values = summary.get(key)
        if isinstance(values, Sequence) and values:
            _sub(section, "p", label)
            ul = _el(section, "ul")
            for value in values:
                _sub(ul, "li", str(value))
    return section


_SECTION_RENDERERS: dict[str, Any] = {
    "vocab": _render_vocab,
    "expressions": _render_expressions,
    "grammar": _render_grammar,
    "questions": _render_questions,
    "exercises": _render_exercises,
}


def render_sections(
    chapter_id: str, data: Mapping[str, Any]
) -> tuple[list[etree._Element], list[str]]:
    """Секции главы по наличию данных; порядок — SECTION_ORDER.

    Возврат: (элементы секций, существующие типы секций).
    """
    elements: list[etree._Element] = []
    present: list[str] = []
    for section_type in SECTION_ORDER:
        if section_type == "summary":
            summary = data.get("summary")
            if (
                isinstance(summary, Mapping)
                and str(summary.get("status", "issued")) == "issued"
            ):
                elements.append(_render_summary(chapter_id, summary))
                present.append(section_type)
            continue
        records = data.get(section_type)
        if records:
            elements.append(_SECTION_RENDERERS[section_type](chapter_id, records))
            present.append(section_type)
    return elements, present


# ------------------------------------------------------------------ ответы


def _answer_text(record: Mapping[str, Any], answer: str) -> str:
    """Человекочитаемый ответ для финального документа."""
    options = record.get("options")
    if isinstance(options, Sequence) and options:
        try:
            index = int(answer)
        except (TypeError, ValueError):
            return answer
        if 0 <= index < len(options):
            return str(options[index])
        return answer
    if answer in ("true", "false"):
        return "Верно" if answer == "true" else "Неверно"
    return answer


def render_answers_document(
    *,
    coverage: float,
    aligned: int,
    total: int,
    chapters_total: int,
    chapters_with_sections: int,
    skipped: Sequence[tuple[str, str]],
    level: str,
    answers_data: Mapping[str, Mapping[str, Mapping[str, str]]],
    questions: Sequence[Mapping[str, Any]],
    exercises: Sequence[Mapping[str, Any]],
) -> bytes:
    """Финальный документ «Ответы и охват»: охват E6 + все ответы (S5-5, S5-8)."""
    root = etree.Element(f"{{{XHTML}}}html", nsmap={None: XHTML, "xml": XML_NS})
    root.set("lang", "ru")
    root.set(f"{{{XML_NS}}}lang", "ru")
    head = _el(root, "head")
    _sub(head, "title", "Ответы и охват")
    body = _el(root, "body")
    _sub(body, "h1", "Ответы и охват")

    coverage_section = etree.SubElement(body, f"{{{XHTML}}}section")
    coverage_section.set("id", "s5-coverage")
    _sub(coverage_section, "h2", "Охват")
    coverage_p = _el(coverage_section, "p")
    coverage_p.set("data-s5-coverage", f"{coverage:.6f}")
    coverage_p.set("data-s5-aligned", str(aligned))
    coverage_p.set("data-s5-total", str(total))
    coverage_p.text = f"Выравнивание (E6): {coverage:.1%} ({aligned} из {total} сегментов)."
    _sub(
        coverage_section,
        "p",
        f"Уровень: {level}. Глав в книге: {chapters_total}; "
        f"с учебными материалами: {chapters_with_sections}; "
        f"без учебных материалов: {len(skipped)}.",
    )
    if skipped:
        ul = _el(coverage_section, "ul")
        for chapter_id, title in skipped:
            label = f"«{title}»" if title else chapter_id
            _sub(ul, "li", f"{label} — без выровненных сегментов")

    answers_section = etree.SubElement(body, f"{{{XHTML}}}section")
    answers_section.set("id", "s5-answers")
    _sub(answers_section, "h2", "Ответы")
    if not questions and not exercises:
        _sub(answers_section, "p", "Ответов нет (уровень не включает вопросы и упражнения).")
    chapter_order: list[str] = []
    for record in (*questions, *exercises):
        chapter_id = str(record.get("chapter_id", ""))
        if chapter_id not in chapter_order:
            chapter_order.append(chapter_id)
    for chapter_id in chapter_order:
        chapter_answers = answers_data.get(chapter_id, {})
        question_answers: Mapping[str, str] = chapter_answers.get("questions", {})
        exercise_answers: Mapping[str, str] = chapter_answers.get("exercises", {})
        chapter_questions = [r for r in questions if str(r.get("chapter_id", "")) == chapter_id]
        chapter_exercises = [r for r in exercises if str(r.get("chapter_id", "")) == chapter_id]
        if not chapter_questions and not chapter_exercises:
            continue
        _sub(answers_section, "h3", chapter_id)
        if chapter_questions:
            ol = _el(answers_section, "ol")
            for record in chapter_questions:
                answer = str(question_answers.get(str(record.get("id", "")), ""))
                li = _el(ol, "li")
                _sub(li, "b", str(record.get("question", "")))
                _span(li, f" — {ANSWER_MARKER} {_answer_text(record, answer)}")
        if chapter_exercises:
            ol = _el(answers_section, "ol")
            for record in chapter_exercises:
                answer = str(exercise_answers.get(str(record.get("id", "")), ""))
                li = _el(ol, "li")
                _sub(li, "b", str(record.get("prompt", "")))
                _span(li, f" — {ANSWER_MARKER} {_answer_text(record, answer)}")
    return _serialize(root)


# ------------------------------------------------------------------ OPF/nav/NCX


def update_opf(opf_bytes: bytes) -> bytes:
    """Добавление финального документа в manifest + конец spine."""
    opf = etree.fromstring(opf_bytes)
    manifest = opf.find(f"{{{OPF_NS}}}manifest")
    spine = opf.find(f"{{{OPF_NS}}}spine")
    if manifest is None or spine is None:
        raise AppError("S5: в OPF нет manifest/spine")
    existing = {item.get("id") for item in manifest.findall(f"{{{OPF_NS}}}item")}
    if ANSWERS_ID not in existing:
        item = etree.SubElement(manifest, f"{{{OPF_NS}}}item")
        item.set("id", ANSWERS_ID)
        item.set("href", ANSWERS_HREF)
        item.set("media-type", "application/xhtml+xml")
    spine_ids = {ref.get("idref") for ref in spine.findall(f"{{{OPF_NS}}}itemref")}
    if ANSWERS_ID not in spine_ids:
        itemref = etree.SubElement(spine, f"{{{OPF_NS}}}itemref")
        itemref.set("idref", ANSWERS_ID)
    return _serialize(opf)


def _toc_nav(nav_root: etree._Element) -> etree._Element:
    for nav in nav_root.iter(f"{{{XHTML}}}nav"):
        if nav.get(f"{{{EPUB_NS}}}type") == "toc":
            return nav
    for nav in nav_root.iter(f"{{{XHTML}}}nav"):
        return nav
    raise AppError("S5: в nav.xhtml нет <nav>")


def _top_level_ol(toc_nav: etree._Element) -> etree._Element:
    for ol in toc_nav.iter(f"{{{XHTML}}}ol"):
        parent = ol.getparent()
        if parent is not None and segmenter.localname(parent) == "nav":
            return ol
    for ol in toc_nav.iter(f"{{{XHTML}}}ol"):
        return ol
    raise AppError("S5: в nav.xhtml нет списка оглавления (<ol> в <nav>)")


def update_nav(nav_bytes: bytes, entries: Sequence[tuple[str, str]]) -> bytes:
    """Плоские записи оглавления в конец верхнего <ol> nav[epub:type=toc]."""
    nav_root = etree.fromstring(nav_bytes, etree.XMLParser(remove_blank_text=False))
    toc_nav = _toc_nav(nav_root)
    ol = _top_level_ol(toc_nav)
    for href, label in entries:
        li = etree.SubElement(ol, f"{{{XHTML}}}li")
        a = etree.SubElement(li, f"{{{XHTML}}}a")
        a.set("href", href)
        a.text = label
    return _serialize(nav_root)


def update_ncx(ncx_bytes: bytes, entries: Sequence[tuple[str, str]]) -> bytes:
    """navPoints в конец navMap; playOrder — продолжение нумерации (max+1)."""
    ncx = etree.fromstring(ncx_bytes)
    nav_map = ncx.find(f"{{{NCX_NS}}}navMap")
    if nav_map is None:
        raise AppError("S5: в NCX нет navMap")
    play = 1
    for nav_point in nav_map:
        order = nav_point.get("playOrder")
        if order and order.isdigit():
            play = max(play, int(order) + 1)
    for href, label in entries:
        nav_point = etree.SubElement(nav_map, f"{{{NCX_NS}}}navPoint")
        nav_point.set("id", f"s5-navpoint-{play}")
        nav_point.set("playOrder", str(play))
        nav_label = etree.SubElement(nav_point, f"{{{NCX_NS}}}navLabel")
        etree.SubElement(nav_label, f"{{{NCX_NS}}}text").text = label
        content = etree.SubElement(nav_point, f"{{{NCX_NS}}}content")
        content.set("src", href)
        play += 1
    return _serialize(ncx)


# ------------------------------------------------------------------ сборка


def assemble_learning_epub(
    target: Path,
    partial: Path,
    *,
    doc_patches: Mapping[str, bytes],
    toc_entries: Sequence[tuple[str, str]],
    answers_bytes: bytes,
) -> dict[str, Any]:
    """Сборка learning.epub.partial: target.epub + вставки + финальный документ.

    `toc_entries` — (архивный путь-якорь, подпись): для глав — путь
    документа с «#якорь», для финального документа — его путь. Href-ы в
    nav/NCX пересчитываются относительно их каталогов.
    """
    if partial.exists():
        partial.unlink()
    with zipfile.ZipFile(target) as archive:
        order = archive.namelist()
        if not order or order[0] != "mimetype":
            raise AppError("S5: target.epub: mimetype не первой записью")
        entries = {name: archive.read(name) for name in order}
        dates = {name: archive.getinfo(name).date_time for name in order}
        opf_path, opf_dir, nav_path, ncx_path = locate_structure(archive)

    new_entries = dict(entries)
    answers_path = join_path(opf_dir, ANSWERS_HREF)
    new_entries[opf_path] = update_opf(entries[opf_path])
    new_entries[answers_path] = answers_bytes

    nav_dir = posixpath.dirname(nav_path) if nav_path else opf_dir
    ncx_dir = posixpath.dirname(ncx_path) if ncx_path else opf_dir
    if nav_path:
        nav_entries = [(_rel(href, nav_dir), label) for href, label in toc_entries]
        new_entries[nav_path] = update_nav(entries[nav_path], nav_entries)
    if ncx_path:
        ncx_entries = [(_rel(href, ncx_dir), label) for href, label in toc_entries]
        new_entries[ncx_path] = update_ncx(entries[ncx_path], ncx_entries)
    for doc_path, doc_bytes in doc_patches.items():
        if doc_path not in entries:
            raise AppError(f"S5: документ {doc_path} отсутствует в target.epub")
        new_entries[doc_path] = doc_bytes

    with zipfile.ZipFile(partial, "w") as dst:
        for name in order:
            info = zipfile.ZipInfo(name, date_time=dates[name])
            info.compress_type = (
                zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            )
            dst.writestr(info, new_entries[name])
        answers_info = zipfile.ZipInfo(answers_path, date_time=_NEW_ENTRY_DATE)
        answers_info.compress_type = zipfile.ZIP_DEFLATED
        dst.writestr(answers_info, new_entries[answers_path])

    return {
        "opf": opf_path,
        "nav": nav_path,
        "ncx": ncx_path,
        "answers": answers_path,
        "entries_total": len(order) + 1,
    }


def _rel(archive_path: str, directory: str) -> str:
    base, sep, fragment = archive_path.partition("#")
    rel = posixpath.relpath(base, directory) if directory else base
    return rel + (sep + fragment if sep else "")


# ------------------------------------------------------------------ валидация


def _check_mimetype(pzip: zipfile.ZipFile, issues: list[str]) -> None:
    """S5-2: mimetype первой записью, без сжатия, с верным содержимым."""
    p_names = pzip.namelist()
    if not p_names or p_names[0] != "mimetype":
        issues.append("mimetype не первой записью")
        return
    if pzip.getinfo("mimetype").compress_type != zipfile.ZIP_STORED:
        issues.append("mimetype сжат (ожидается без сжатия)")
    if pzip.read("mimetype") != b"application/epub+zip":
        issues.append("mimetype: неверное содержимое")


def _check_untouched(
    tzip: zipfile.ZipFile, pzip: zipfile.ZipFile, structural: set[str], issues: list[str]
) -> None:
    """S5-3: все записи target, кроме структурных, — побайтово и на месте."""
    p_names = pzip.namelist()
    for name in tzip.namelist():
        if name in structural:
            continue
        if name not in p_names:
            issues.append(f"запись {name} отсутствует в сборке")
        elif pzip.read(name) != tzip.read(name):
            issues.append(f"запись {name} изменена (ожидается побайтово)")


def _check_chapter_doc(
    tzip: zipfile.ZipFile,
    pzip: zipfile.ZipFile,
    *,
    doc: str,
    anchors: Sequence[str],
    tags: frozenset[str],
    issues: list[str],
) -> None:
    """S5-3/4/5 для одной главы: блоки — префикс, якоря, без маркера ответа."""
    old_body = segmenter.find_body(etree.fromstring(tzip.read(doc)))
    new_body = segmenter.find_body(etree.fromstring(pzip.read(doc)))
    if old_body is None or new_body is None:
        issues.append(f"{doc}: нет <body>")
        return
    old_blocks = [
        segmenter.full_text(e) for e in segmenter.find_block_elements(old_body, tags)
    ]
    new_blocks = [
        segmenter.full_text(e) for e in segmenter.find_block_elements(new_body, tags)
    ]
    if new_blocks[: len(old_blocks)] != old_blocks:
        issues.append(f"{doc}: оригинальные блоки текста изменены")
    new_text = pzip.read(doc).decode("utf-8", errors="replace")
    for anchor in anchors:
        if f'id="{anchor}"' not in new_text:
            issues.append(f"{doc}: якорь {anchor} отсутствует")
    for section in new_body.iter(f"{{{XHTML}}}section"):
        if (
            section.get("class") == "s5-section"
            and ANSWER_MARKER in etree.tostring(section, encoding="unicode")
        ):
            issues.append(f"{doc}: «{ANSWER_MARKER}» в учебной секции главы")


def _check_nav(
    pzip: zipfile.ZipFile,
    p_nav: str,
    p_answers: str,
    toc_anchor_paths: Sequence[str],
    issues: list[str],
) -> None:
    """S5-4: запись оглавления на каждый якорь (кроме самого nav и answers)."""
    nav_text = pzip.read(p_nav).decode("utf-8", errors="replace")
    for anchor_path in toc_anchor_paths:
        base = anchor_path.partition("#")[0]
        if base in (p_nav, p_answers):
            continue
        href = _rel(anchor_path, posixpath.dirname(p_nav))
        if f'href="{href}"' not in nav_text:
            issues.append(f"nav.xhtml: запись оглавления {href} отсутствует")


def _check_opf_and_answers(
    pzip: zipfile.ZipFile, p_opf: str, p_answers: str, coverage: float, issues: list[str]
) -> None:
    """S5-8: финальный документ в OPF (manifest+spine) и охват E6 в нём."""
    opf_root = etree.fromstring(pzip.read(p_opf))
    manifest = opf_root.find(f"{{{OPF_NS}}}manifest")
    if manifest is not None:
        manifest_ids = {
            item.get("id") for item in manifest.findall(f"{{{OPF_NS}}}item")
        }
        if ANSWERS_ID not in manifest_ids:
            issues.append("OPF: финальный документ не в manifest")
    spine = opf_root.find(f"{{{OPF_NS}}}spine")
    if spine is not None:
        spine_ids = {ref.get("idref") for ref in spine.findall(f"{{{OPF_NS}}}itemref")}
        if ANSWERS_ID not in spine_ids:
            issues.append("OPF: финальный документ не в spine")
    if p_answers not in pzip.namelist():
        issues.append("финальный документ отсутствует в сборке")
        return
    answers_text = pzip.read(p_answers).decode("utf-8", errors="replace")
    match = _COVERAGE_RE.search(answers_text)
    if match is None:
        issues.append("финальный документ: нет data-s5-coverage")
    elif abs(float(match.group(1)) - coverage) > 1e-9:
        issues.append(f"финальный документ: охват {match.group(1)} != E6 {coverage:.6f}")


def validate_study_book(
    partial: Path,
    target: Path,
    *,
    chapter_anchors: Mapping[str, Sequence[str]],
    toc_anchor_paths: Sequence[str],
    coverage: float,
    block_tags: Sequence[str],
) -> list[str]:
    """Внутренняя валидация собранного EPUB (fail-closed; S5-2/3/4/5/8).

    Возврат: список проблем (пусто — всё хорошо).
    """
    issues: list[str] = []
    with zipfile.ZipFile(partial) as pzip, zipfile.ZipFile(target) as tzip:
        _check_mimetype(pzip, issues)
        t_opf, _t_dir, t_nav, t_ncx = locate_structure(tzip)
        p_opf, p_dir, p_nav, _p_ncx = locate_structure(pzip)
        p_answers = join_path(p_dir, ANSWERS_HREF)
        structural = {
            t_opf,
            p_opf,
            p_answers,
            *chapter_anchors,
            *(path for path in (t_nav, t_ncx, p_nav) if path is not None),
        }
        _check_untouched(tzip, pzip, structural, issues)
        for doc, anchors in chapter_anchors.items():
            _check_chapter_doc(
                tzip,
                pzip,
                doc=doc,
                anchors=anchors,
                tags=frozenset(block_tags),
                issues=issues,
            )
        if p_nav:
            _check_nav(pzip, p_nav, p_answers, toc_anchor_paths, issues)
        _check_opf_and_answers(pzip, p_opf, p_answers, coverage, issues)
    return issues


# ------------------------------------------------------------------ отчёт


def zipinfo_verbatim(epub: Path) -> str:
    """Вывод `zipinfo` (список записей с размерами) — verbatim в отчёт (S5-2)."""
    zipinfo = shutil.which("zipinfo")
    if zipinfo is None:
        return (
            "zipinfo отсутствует в окружении (BLOCKER); список записей вместо zipinfo:\n"
            + _zip_list_fallback(epub)
        )
    try:
        completed = subprocess.run(
            [zipinfo, str(epub)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"zipinfo не выполнен: {exc}"
    return completed.stdout or completed.stderr


def _zip_list_fallback(epub: Path) -> str:
    lines: list[str] = []
    with zipfile.ZipFile(epub) as archive:
        for info in archive.infolist():
            lines.append(f"{info.filename}\t{info.file_size}\t{info.compress_type}")
    return "\n".join(lines)
