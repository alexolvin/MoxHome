# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Детерминированный QC после перевода (E5.8): 10 проверок из ТЗ.

Работает в стадии validating_output по финальному выходу (target.epub) и
оригиналу (source_path). Всё — факты поимённо (документ, позиция XPath,
фрагмент текста); нарушение → предупреждение (COMPLETED_WITH_WARNINGS),
а не тишина. Полная карта выравнивания (E6) строится отдельно
(`alignment_report.json`): пара «исходный блок ↔ перевод» определяется
консервативно по измеренной разметке движка (E1) — общим ядром
(app/epub/aligner.py): APPEND_BLOCK — перевод = вставленный
sibling/внутренний блок, REPLACE/APPEND_TEXT — изменение элемента на месте.

10 пунктов (ТЗ E5.8, п.8):
  1 число исходных сегментов; 2 число переведённых блоков; 3 пустые переводы;
  4 перевод, побайтово совпадающий с оригиналом; 5 дублирование оригинала;
  6 дублирование перевода; 7 изменения структуры XHTML вне ожидаемой вставки;
  8 битые внутренние ссылки; 9 структура оглавления; 10 метаданные.

Замечания (зафиксированы, E5):
- «Совпадение с оригиналом» (п.4) сравнивается по нормализованному тексту
  (NFC + схлопнутые пробелы, как segment_id, E2.3): fill-вывод движка
  обёрнут пробелами, побайтовое сравнение дало бы ложные срабатывания.
- П.7 сравнивает деревья элементов: теги и атрибуты (ключи lxml — Clark,
  {uri}name), текст; whitespace-only tails — форматирование, не сравнение
  (ресериализация движка меняет их вокруг точек вставки).
  Namespace-декларации не сравниваются: это сериализационная деталь, а не
  структура — движок убирает неиспользуемые декларации корня (измерено в
  E1), и из-за наследования nsmap такая «находка» размывается по всем
  потомкам; реальное namespace-повреждение видно в тегах и атрибутах
  (Clark) и в EPUBCheck (E5-3). Вставленные блоки (переводы) из выхода
  удаляются перед сравнением.
"""

from __future__ import annotations

import copy
import posixpath
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lxml.etree

from app.adapters.epub_engine.validation import find_broken_links
from app.core.config import Settings
from app.domain.book import Book
from app.epub import aligner, segmenter
from app.epub.reader import CONTAINER_PATH, DC_NS, EPUB_OPS_NS, OPF_NS

#: Порог охвата переведённых блоков (E5-2).
COVERAGE_THRESHOLD = 0.995

#: Известное поведение движка epub-translator 0.1.10 (измерено в E1 и
#: E3-прогоне 2026-09-12): при ресериализации spine-документа с корневой
#: разметки удаляется атрибут `lang` (`xml:lang` сохраняется). П.7 фиксирует
#: это поимённо, но предупреждением НЕ является: поведение движка, а не
#: повреждение книги. Любое ИНОЕ изменение — предупреждение.
_KNOWN_RESERIALIZATION_ISSUES = frozenset({"атрибут 'lang' удалён"})


def _is_known_reserialization(item: dict[str, Any]) -> bool:
    """Находка относится к известной ресериализации движка (корень документа)."""
    return item.get("where") == "/" and item.get("issue") in _KNOWN_RESERIALIZATION_ISSUES


@dataclass(frozen=True)
class QcInput:
    """Входные данные QC (собирает конвейер)."""

    settings: Settings
    source_path: Path
    target_path: Path
    src_book: Book
    out_book: Book
    submit_mode: str  # REPLACE | APPEND_BLOCK | APPEND_TEXT
    chapter_count: int
    per_chapter_segments: list[int]
    toc_present: bool
    meta_present: bool
    progress_call_count: int
    fill_failures: list[dict[str, Any]] = field(default_factory=list)
    glossary: dict[str, Any] = field(default_factory=dict)
    epubcheck: dict[str, Any] = field(default_factory=dict)


def run_qc(q: QcInput) -> tuple[dict[str, Any], list[str]]:
    """Возвращает (отчёт для quality.json, список предупреждений)."""
    tags = frozenset(q.settings.segmentation.block_tags)
    warnings: list[str] = []
    qc: dict[str, Any] = {}

    # 1. число исходных сегментов
    qc["1_input_segments"] = {
        "value": q.src_book.stats.segment_count,
        "per_chapter": list(q.per_chapter_segments),
    }

    doc_names = [ch.document_path for ch in q.out_book.chapters]
    doc_results = [_pair_document(q, name, tags) for name in doc_names]

    _qc_coverage(doc_results, qc, warnings)  # п.2
    _qc_pair_items(doc_results, qc, warnings)  # пп.3-4
    _qc_duplicates(doc_results, qc, warnings)  # пп.5-6
    _qc_structure(doc_results, qc, warnings)  # п.7
    _qc_broken_links(q, doc_names, qc, warnings)  # п.8
    _qc_toc(q, qc, warnings)  # п.9
    _qc_metadata(q, qc, warnings)  # п.10
    _qc_progress(q, qc, warnings)
    fill = _qc_fill(q, warnings)
    glossary = _qc_glossary(q, warnings)
    epubcheck = _qc_epubcheck(q, warnings)

    report = {
        "qc": qc,
        "fill_failures": fill,
        "glossary": glossary,
        "epubcheck": epubcheck,
        "warnings": warnings,
    }
    return report, warnings


def _qc_coverage(doc_results: list[_DocResult], qc: dict[str, Any], warnings: list[str]) -> None:
    """П.2: число переведённых блоков (E5-2)."""
    total_src = sum(d.src_count for d in doc_results)
    total_translated = sum(d.translated for d in doc_results)
    shortfall = [s for d in doc_results for s in d.shortfall]
    coverage = (total_translated / total_src) if total_src else 1.0
    qc["2_translated_blocks"] = {
        "total": total_src,
        "translated": total_translated,
        "coverage": round(coverage, 6),
        "threshold": COVERAGE_THRESHOLD,
        "shortfall": shortfall,
    }
    if coverage < COVERAGE_THRESHOLD:
        warnings.append(
            f"E5-2: переведено блоков {total_translated}/{total_src} "
            f"({coverage:.1%} < {COVERAGE_THRESHOLD:.1%}): "
            + "; ".join(_short(s) for s in shortfall[:10])
            + (f" … (+{len(shortfall) - 10})" if len(shortfall) > 10 else "")
        )


def _qc_pair_items(doc_results: list[_DocResult], qc: dict[str, Any], warnings: list[str]) -> None:
    """ПП.3-4: пустые переводы и совпадение с оригиналом."""
    empty = [e for d in doc_results for e in d.empty]
    qc["3_empty_translations"] = {"count": len(empty), "items": empty}
    if empty:
        warnings.append(
            "E5 п.3: пустые переводы: " + "; ".join(_short(e) for e in empty[:10])
        )
    identical = [i for d in doc_results for i in d.identical]
    qc["4_identical_to_original"] = {"count": len(identical), "items": identical}
    if identical:
        warnings.append(
            "E5 п.4: перевод совпадает с оригиналом (вероятно, непереведено): "
            + "; ".join(_short(i) for i in identical[:10])
        )


def _qc_duplicates(doc_results: list[_DocResult], qc: dict[str, Any], warnings: list[str]) -> None:
    """ПП.5-6: дублирование оригинала и перевода."""
    dup_orig = [x for d in doc_results for x in d.dup_original]
    qc["5_original_duplicated"] = {"count": len(dup_orig), "items": dup_orig}
    if dup_orig:
        warnings.append(
            "E5 п.5: оригинал продублирован: " + "; ".join(_short(x) for x in dup_orig[:10])
        )
    dup_trans = [x for d in doc_results for x in d.dup_translation]
    qc["6_translation_duplicated"] = {"count": len(dup_trans), "items": dup_trans}
    if dup_trans:
        warnings.append(
            "E5 п.6: перевод продублирован: " + "; ".join(_short(x) for x in dup_trans[:10])
        )


def _qc_structure(doc_results: list[_DocResult], qc: dict[str, Any], warnings: list[str]) -> None:
    """П.7: изменения структуры XHTML вне ожидаемой вставки."""
    struct = [x for d in doc_results for x in d.structure]
    known = [x for x in struct if _is_known_reserialization(x)]
    unexpected = [x for x in struct if not _is_known_reserialization(x)]
    qc["7_unexpected_structure_changes"] = {
        "count": len(unexpected),
        "items": unexpected,
        "known_engine_reserialization": {"count": len(known), "items": known},
    }
    if unexpected:
        warnings.append(
            "E5 п.7: изменения структуры XHTML вне ожидаемой вставки: "
            + "; ".join(_short(x) for x in unexpected[:10])
        )


def _qc_broken_links(
    q: QcInput, doc_names: list[str], qc: dict[str, Any], warnings: list[str]
) -> None:
    """П.8: битые внутренние ссылки."""
    with zipfile.ZipFile(q.target_path) as archive:
        broken = find_broken_links(archive, doc_names)
    qc["8_broken_links"] = {"count": len(broken), "items": broken}
    if broken:
        warnings.append(
            "E5 п.8: битые внутренние ссылки: " + "; ".join(broken[:10])
        )


def _qc_toc(q: QcInput, qc: dict[str, Any], warnings: list[str]) -> None:
    """П.9: структура оглавления."""
    toc = _check_toc(q.source_path, q.target_path)
    qc["9_toc_structure"] = toc
    if toc["violations"]:
        warnings.append(
            "E5 п.9: структура оглавления изменилась: " + "; ".join(toc["violations"][:10])
        )


def _qc_metadata(q: QcInput, qc: dict[str, Any], warnings: list[str]) -> None:
    """П.10: метаданные."""
    meta = _check_metadata(q.source_path, q.target_path)
    qc["10_metadata"] = meta
    if meta["violations"]:
        warnings.append("E5 п.10: метаданные: " + "; ".join(meta["violations"][:10]))


def _qc_progress(q: QcInput, qc: dict[str, Any], warnings: list[str]) -> None:
    """Прогресс: число вызовов движка == TOC? + metadata? + главы (E5.3)."""
    expected_calls = (
        (1 if q.toc_present else 0) + (1 if q.meta_present else 0) + q.chapter_count
    )
    qc["progress_mapping"] = {
        "calls": q.progress_call_count,
        "expected": expected_calls,
        "consistent": q.progress_call_count == expected_calls,
    }
    if q.progress_call_count != expected_calls:
        warnings.append(
            "E5 п.3 (прогресс): число вызовов on_progress "
            f"({q.progress_call_count}) != ожидаемому ({expected_calls})"
        )


def _qc_fill(q: QcInput, warnings: list[str]) -> dict[str, Any]:
    """Fill-сбои (E5.4): поимённо; число критических событий == число записей."""
    critical = sum(1 for f in q.fill_failures if f.get("over_maximum_retries"))
    if critical:
        warnings.append(
            f"E5 п.4: fill не удался (over_maximum_retries) в {critical} группе(ах) — "
            "фрагменты не переведены, см. fill_failures и errors.log"
        )
    return {"count": len(q.fill_failures), "critical": critical, "events": q.fill_failures}


def _qc_glossary(q: QcInput, warnings: list[str]) -> dict[str, Any]:
    """Глоссарий (E5.5/E5-7): усечение — в отчёт, не молча."""
    glossary = dict(q.glossary)
    if glossary.get("truncated"):
        warnings.append(
            "E5 п.5 (глоссарий): user_prompt усечён — "
            f"включено {glossary.get('kept_terms')}/{glossary.get('total_terms')} терминов "
            f"(лимит {glossary.get('limit_chars')} симв., отброшено "
            f"{glossary.get('dropped_terms')})"
        )
    return glossary


def _qc_epubcheck(q: QcInput, warnings: list[str]) -> dict[str, Any]:
    """EPUBCheck (E5.7): недоступен → причина в отчёте и в предупреждениях (Rule 6)."""
    epubcheck = dict(q.epubcheck)
    if not epubcheck.get("available", False):
        warnings.append(
            f"VALIDATION_UNAVAILABLE: EPUBCheck недоступен: "
            f"{epubcheck.get('unavailable_reason')}"
        )
    return epubcheck


# ------------------------------------------------------------------- служебное


@dataclass
class _DocResult:
    src_count: int = 0
    translated: int = 0
    shortfall: list[dict[str, Any]] = field(default_factory=list)
    empty: list[dict[str, Any]] = field(default_factory=list)
    identical: list[dict[str, Any]] = field(default_factory=list)
    dup_original: list[dict[str, Any]] = field(default_factory=list)
    dup_translation: list[dict[str, Any]] = field(default_factory=list)
    structure: list[dict[str, Any]] = field(default_factory=list)


def _short(item: dict[str, Any]) -> str:
    return f"{item.get('doc', '?')} {item.get('where', '')} {item.get('issue', '')}".strip()


def _text(el: lxml.etree._Element) -> str:
    return segmenter.normalize_text(segmenter.full_text(el))


def _where(el: lxml.etree._Element) -> str:
    return segmenter.positional_xpath(el)


def _pair_document(q: QcInput, doc_name: str, tags: frozenset[str]) -> _DocResult:
    res = _DocResult()
    with zipfile.ZipFile(q.source_path) as src_zip, zipfile.ZipFile(q.target_path) as out_zip:
        src_root = lxml.etree.fromstring(src_zip.read(doc_name))
        out_root = lxml.etree.fromstring(out_zip.read(doc_name))
    if q.submit_mode == "APPEND_BLOCK":
        _pair_append(src_root, out_root, tags, res, doc_name)
    else:
        _pair_inplace(src_root, out_root, tags, res, doc_name)
    return res


def _pair_append(
    src_root: lxml.etree._Element,
    out_root: lxml.etree._Element,
    tags: frozenset[str],
    res: _DocResult,
    doc_name: str,
) -> None:
    """APPEND_BLOCK: исходник сохранён; перевод = вставленный sibling/внутренний блок.

    Сопоставление оригинала в выходе: по id (если есть) либо по (тег, текст)
    монотонно по порядку документа — вставленный блок всегда ПОСЛЕ оригинала
    (измерено, E1). Помеченные `id__translated` блоки — вставки, не оригиналы.
    Ядро пар — общий с E6-выравниванием механизм (app/epub/aligner.py).
    """
    src_blocks = aligner.blocks(src_root, tags)
    out_blocks = aligner.blocks(out_root, tags)
    res.src_count = len(src_blocks)
    pairs = aligner.match_originals(src_blocks, out_blocks, tags)
    out_pos = {id(el): i for i, el in enumerate(out_blocks)}
    used = {out_pos[id(out_el)] for _, out_el in pairs if out_el is not None}
    insertions = {id(el) for i, el in enumerate(out_blocks) if i not in used}
    trans_list: list[lxml.etree._Element] = []
    for src_el, out_el in pairs:
        if out_el is None:
            res.shortfall.append(
                {
                    "doc": doc_name,
                    "where": _where(src_el),
                    "issue": f"оригинал не найден в выходе ({segmenter.localname(src_el)})",
                    "text": _text(src_el)[:60],
                }
            )
            continue
        trans, how = aligner.find_translation(out_el, tags, insertions)
        if trans is None:
            res.shortfall.append(
                {
                    "doc": doc_name,
                    "where": _where(src_el),
                    "issue": f"переведённый блок не найден ({segmenter.localname(src_el)})",
                    "text": _text(src_el)[:60],
                }
            )
            continue
        res.translated += 1
        trans_list.append(trans)
        t_text = _text(trans)
        s_text = _text(src_el)
        if not t_text:
            res.empty.append(
                {
                    "doc": doc_name,
                    "where": _where(trans),
                    "issue": f"пустой перевод ({segmenter.localname(trans)}, {how})",
                }
            )
        elif t_text == s_text:
            res.identical.append(
                {
                    "doc": doc_name,
                    "where": _where(src_el),
                    "issue": "перевод совпадает с оригиналом",
                    "text": s_text[:60],
                }
            )

    # 5/6: дубли по тексту (всё дерево body, не только пары)
    _dup_checks(src_blocks, out_blocks, trans_list, res, doc_name)

    # 7: структура (вставки удалены из выхода перед сравнением)
    res.structure.extend(_structure_diff(src_root, out_root, insertions, doc_name))


def _dup_checks(
    src_blocks: list[lxml.etree._Element],
    out_blocks: list[lxml.etree._Element],
    trans_list: list[lxml.etree._Element],
    res: _DocResult,
    doc_name: str,
) -> None:
    """ПП.5-6: дублирование оригинала и перевода по тексту (всё дерево body).

    Ожидаемое число вхождений текста в выходе = вхождения в исходнике
    (оригинал) + число спаренных переводов с этим же текстом. Больше —
    продублированная вставка.
    """
    src_counts = Counter(_text(el) for el in src_blocks)
    out_counts = Counter(_text(el) for el in out_blocks)
    trans_counts = Counter(_text(el) for el in trans_list)
    for text, cnt_in in src_counts.items():
        expected = cnt_in + trans_counts.get(text, 0)
        if out_counts.get(text, 0) > expected:
            res.dup_original.append(
                {
                    "doc": doc_name,
                    "issue": "оригинал продублирован",
                    "text": text[:60],
                    "in": cnt_in,
                    "expected": expected,
                    "out": out_counts.get(text, 0),
                }
            )
    for text, cnt_trans in trans_counts.items():
        expected = cnt_trans + src_counts.get(text, 0)
        if out_counts.get(text, 0) > expected:
            res.dup_translation.append(
                {
                    "doc": doc_name,
                    "issue": "перевод продублирован",
                    "text": text[:60],
                    "translations": cnt_trans,
                    "sources": src_counts.get(text, 0),
                    "expected": expected,
                    "out": out_counts.get(text, 0),
                }
            )


def _pair_inplace(
    src_root: lxml.etree._Element,
    out_root: lxml.etree._Element,
    tags: frozenset[str],
    res: _DocResult,
    doc_name: str,
) -> None:
    """REPLACE / APPEND_TEXT: перевод = изменение элемента на месте (по позиции)."""
    src_blocks = aligner.blocks(src_root, tags)
    out_blocks = aligner.blocks(out_root, tags)
    res.src_count = len(src_blocks)
    if len(src_blocks) != len(out_blocks):
        res.structure.append(
            {
                "doc": doc_name,
                "where": "(body)",
                "issue": f"число блоков {len(src_blocks)} -> {len(out_blocks)}",
            }
        )
    # strict=False: число блоков в режиме REPLACE может различаться — расхождение
    # уже зафиксировано как структурное выше.
    for s_el, o_el in zip(src_blocks, out_blocks, strict=False):
        s_text = _text(s_el)
        o_text = _text(o_el)
        if o_text != s_text:
            res.translated += 1
            if not o_text:
                res.empty.append(
                    {
                        "doc": doc_name,
                        "where": _where(s_el),
                        "issue": f"пустой перевод ({segmenter.localname(s_el)})",
                    }
                )
        else:
            res.shortfall.append(
                {
                    "doc": doc_name,
                    "where": _where(s_el),
                    "issue": f"элемент не изменён ({segmenter.localname(s_el)})",
                    "text": s_text[:60],
                }
            )
            if o_text:
                res.identical.append(
                    {
                        "doc": doc_name,
                        "where": _where(s_el),
                        "issue": "перевод совпадает с оригиналом",
                        "text": s_text[:60],
                    }
                )
    # В REPLACE/APPEND_TEXT текст — место перевода: сравниваем только структуру.
    res.structure.extend(_structure_diff_ignore_text(src_root, out_root, doc_name))


def _structure_diff(
    src_root: lxml.etree._Element,
    out_root: lxml.etree._Element,
    insertions: set[int],
    doc_name: str,
) -> list[dict[str, Any]]:
    """Сравнение дерева оригинала с выходом БЕЗ вставленных блоков (E5 п.7).

    Удаление из копии — в обратном документном порядке: удаление более позднего
    элемента не сдвигает позиционные индексы более ранних (XPath остаются
    валидными).
    """
    ins_elements = [
        el for el in out_root.iter() if isinstance(el.tag, str) and id(el) in insertions
    ]
    ins_xpaths = [segmenter.positional_xpath(el) for el in ins_elements]
    out_clean = copy.deepcopy(out_root)
    for xpath in reversed(ins_xpaths):
        for el in out_clean.iter():
            if isinstance(el.tag, str) and segmenter.positional_xpath(el) == xpath:
                parent = el.getparent()
                if parent is not None:
                    parent.remove(el)
                break
    return _diff_trees(src_root, out_clean, "/", doc_name)


def _structure_diff_ignore_text(
    src_root: lxml.etree._Element,
    out_root: lxml.etree._Element,
    doc_name: str,
) -> list[dict[str, Any]]:
    """Вариант для REPLACE/APPEND_TEXT: текст — место перевода, сравним структуру."""
    return _diff_trees(src_root, out_root, "/", doc_name, ignore_text=True)


def _diff_trees(
    a: lxml.etree._Element,
    b: lxml.etree._Element,
    path: str,
    doc_name: str,
    ignore_text: bool = False,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add(issue: str, where: str = path) -> None:
        out.append({"doc": doc_name, "where": where, "issue": issue})

    if segmenter.localname(a) != segmenter.localname(b):
        add(f"тег {segmenter.localname(a)} -> {segmenter.localname(b)}")
        return out
    _attrib_diff(a, b, add)
    if not ignore_text and (a.text or "") != (b.text or ""):
        add("текст элемента изменён")
    a_children = [c for c in a if isinstance(c.tag, str)]
    b_children = [c for c in b if isinstance(c.tag, str)]
    if len(a_children) != len(b_children):
        add(f"число дочерних элементов {len(a_children)} -> {len(b_children)}")
        return out
    for i, (ca, cb) in enumerate(zip(a_children, b_children, strict=True)):
        child_path = f"{path}/{segmenter.localname(ca)}[{i + 1}]"
        out.extend(_diff_trees(ca, cb, child_path, doc_name, ignore_text=ignore_text))
        # Whitespace-only tail — форматирование (ресериализация движка меняет
        # его вокруг точек вставки); содержательный tail сравнивается.
        if not ignore_text and (ca.tail or "").strip() != (cb.tail or "").strip():
            add("tail-текст изменён", child_path)
    return out


def _attrib_diff(
    a: lxml.etree._Element, b: lxml.etree._Element, add: Callable[[str], None]
) -> None:
    """Атрибуты; ключи lxml — Clark-нотация {uri}name, поэтому
    namespace-повреждение атрибута видно здесь. Namespace-декларации
    (nsmap) не сравниваются: сериализационная деталь, а не структура
    (см. модульный docstring, замечание к п.7)."""
    a_attr, b_attr = dict(a.attrib), dict(b.attrib)
    for key in sorted(set(a_attr) | set(b_attr)):
        if a_attr.get(key) == b_attr.get(key):
            continue
        if key not in b_attr:
            kind = "удалён"
        elif key not in a_attr:
            kind = "добавлен"
        else:
            kind = f"изменён {a_attr[key]!r} -> {b_attr[key]!r}"
        add(f"атрибут {key!r} {kind}")


def _check_toc(source_path: Path, target_path: Path) -> dict[str, Any]:
    """Структура оглавления: число, порядок и href пунктов nav (E5 п.9)."""
    src_toc = _toc_hrefs(source_path)
    out_toc = _toc_hrefs(target_path)
    violations: list[str] = []
    if src_toc != out_toc and len(src_toc) != len(out_toc):
        violations.append(f"число пунктов {len(src_toc)} -> {len(out_toc)}")
    for i, (a, b) in enumerate(zip(src_toc, out_toc, strict=False)):
        if a != b:
            violations.append(f"пункт {i + 1}: {a} -> {b}")
    return {"src": src_toc, "out": out_toc, "violations": violations}


def _toc_hrefs(epub_path: Path) -> list[str]:
    """href пунктов nav (EPUB3) в порядке документа (NCX не ожидается: E2-файлы EPUB3)."""
    with zipfile.ZipFile(epub_path) as archive:
        nav = _read_nav_doc(archive)
    if nav is None:
        return []
    return _nav_toc_hrefs(nav)


def _read_nav_doc(archive: zipfile.ZipFile) -> lxml.etree._Element | None:
    """nav-документ из manifest (properties='nav'); None — нет/открыть не удалось."""
    names = set(archive.namelist())
    opf_name = _opf_name(archive)
    if opf_name is None:
        return None
    opf = lxml.etree.fromstring(archive.read(opf_name))
    manifest = opf.find(f"{{{OPF_NS}}}manifest")
    if manifest is None:
        return None
    nav_href = _nav_href(manifest)
    if not nav_href:
        return None
    nav_path = posixpath.normpath(
        posixpath.join(posixpath.dirname(opf_name), nav_href)
    )
    if nav_path not in names:
        return None
    return lxml.etree.fromstring(archive.read(nav_path))


def _nav_href(manifest: lxml.etree._Element) -> str | None:
    for item in manifest:
        if isinstance(item.tag, str) and "nav" in (item.get("properties") or "").split():
            return item.get("href")
    return None


def _is_toc_nav(nav_el: lxml.etree._Element) -> bool:
    return nav_el.get(f"{{{EPUB_OPS_NS}}}type") == "toc" or nav_el.get("type") == "toc"


def _nav_toc_hrefs(nav: lxml.etree._Element) -> list[str]:
    hrefs: list[str] = []
    for nav_el in nav.iter():
        if (
            isinstance(nav_el.tag, str)
            and segmenter.localname(nav_el) == "nav"
            and _is_toc_nav(nav_el)
        ):
            for a in nav_el.iter():
                if isinstance(a.tag, str) and segmenter.localname(a) == "a":
                    href = a.get("href")
                    if href:
                        hrefs.append(href)
    return hrefs


def _opf_name(archive: zipfile.ZipFile) -> str | None:
    try:
        container = lxml.etree.fromstring(archive.read(CONTAINER_PATH))
    except Exception:
        return None
    for rootfile in container.iter():
        if not isinstance(rootfile.tag, str):
            continue
        if (
            lxml.etree.QName(rootfile.tag).localname == "rootfile"
            and rootfile.get("media-type") == "application/oebps-package+xml"
            and rootfile.get("full-path")
        ):
            return rootfile.get("full-path")
    return None


def _check_metadata(source_path: Path, target_path: Path) -> dict[str, Any]:
    """Метаданные OPF: набор полей, сохранение identifier/date/language (E5 п.10)."""
    src = _opf_fields(source_path)
    out = _opf_fields(target_path)
    violations: list[str] = []
    for name, value in src.items():
        if name not in out:
            violations.append(f"поле {name} отсутствует в выходе")
        elif name in ("identifier", "date", "language") and out[name] != value:
            violations.append(f"поле {name} изменено: {value!r} -> {out[name]!r}")
    for name in out:
        if name not in src:
            violations.append(f"лишнее поле в выходе: {name}")
    return {"src": src, "out": out, "violations": violations}


def _opf_fields(epub_path: Path) -> dict[str, str]:
    with zipfile.ZipFile(epub_path) as archive:
        opf_name = _opf_name(archive)
        if opf_name is None:
            return {}
        opf = lxml.etree.fromstring(archive.read(opf_name))
    fields: dict[str, str] = {}
    for element in opf.iter():
        if not isinstance(element.tag, str):
            continue
        if element.tag.startswith(f"{{{DC_NS}}}"):
            name = element.tag.split("}")[1]
            if element.text and element.text.strip():
                fields.setdefault(name, element.text.strip())
    return fields
