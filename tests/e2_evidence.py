# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Сборщик verbatim-evidence для CONTROL E2 (не тест; assertions — в test_e2_reader.py).

Запуск: python tests/e2_evidence.py
Захваты: docs/CONTROL/evidence/E2/e2-1…e2-6_*.txt
"""

from __future__ import annotations

import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "probe"))

import run_probe  # noqa: E402
import tiktoken  # noqa: E402
from epub_translator import SubmitKind  # noqa: E402
from mock_llm_server import MockLLMServer  # noqa: E402

from app.core.config import Settings, load_settings  # noqa: E402
from app.core.errors import InputValidationError  # noqa: E402
from app.domain.book import Book, Segment  # noqa: E402
from app.epub.reader import read_epub  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "E2"
FIXTURES = ROOT / "tests" / "fixtures" / "epub"


def evidence(name: str, content: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print(f"  [evidence] {path.relative_to(ROOT)}")


def _all_segments(book: Book) -> list[Segment]:
    return [s for c in book.chapters for s in c.segments]


def ev_e2_1(stamp: str, book_a: Book, book_b: Book) -> None:
    ids_a = [s.segment_id for s in _all_segments(book_a)]
    ids_b = [s.segment_id for s in _all_segments(book_b)]
    evidence(
        "e2-1_segment_id_stability.txt",
        "\n".join(
            [
                f"##### E2-1: стабильность segment_id (verbatim)\nдата: {stamp}",
                "фикстура: complex.epub; два независимых запуска (work-каталоги run_a, run_b)",
                f"запуск A: сегментов={len(ids_a)}",
                f"запуск B: сегментов={len(ids_b)}",
                f"совпадение списков segment_id (порядок+значения): {ids_a == ids_b}",
                f"уникальных в A: {len(set(ids_a))} из {len(ids_a)}",
                "",
                "первые 5 segment_id запуска A:",
                *[f"  {sid}" for sid in ids_a[:5]],
                "",
                "первые 5 segment_id запуска B:",
                *[f"  {sid}" for sid in ids_b[:5]],
            ]
        ),
    )


def ev_e2_2(stamp: str, book: Book) -> None:
    chapter_lines = [
        f"spine[{chapter.spine_index}] chapter_id={chapter.chapter_id} "
        f"document={chapter.document_path} title={chapter.title!r} "
        f"segments={len(chapter.segments)}"
        for chapter in book.chapters
    ]
    evidence(
        "e2-2_segment_count.txt",
        "\n".join(
            [
                f"##### E2-2: число сегментов на complex.epub (verbatim)\nдата: {stamp}",
                "эталон (вручную по структуре фикстуры): ch1=8, ch2=13, ch3=5, итого 26",
                *chapter_lines,
                f"итого сегментов: {book.stats.segment_count}",
                f"совпадение с эталоном: "
                f"{[len(c.segments) for c in book.chapters] == [8, 13, 5]}",
            ]
        ),
    )


def ev_e2_3(stamp: str, work: Path, settings: Settings) -> None:
    drm_message = ""
    drm_work = work / "drm"
    try:
        read_epub(FIXTURES / "drm.epub", drm_work, settings)
        drm_message = "(ОШИБКА: книга не отклонена)"
    except InputValidationError as exc:
        drm_message = str(exc)
    evidence(
        "e2-3_drm_rejection.txt",
        "\n".join(
            [
                f"##### E2-3: отклонение drm.epub по encryption.xml (verbatim)\nдата: {stamp}",
                f"сообщение ошибки: {drm_message}",
                f"work-каталог создан (распаковка): {drm_work.exists()}",
            ]
        ),
    )


def ev_e2_4(stamp: str, book: Book) -> None:
    with zipfile.ZipFile(FIXTURES / "complex.epub") as archive:
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
    spine_href_order = [line.strip() for line in opf.splitlines() if "<itemref" in line]
    titles = [chapter.title for chapter in book.chapters]
    order_ok = titles == ["Chapter One", "Chapter Two", "Chapter Three"]
    evidence(
        "e2-4_spine_order.txt",
        "\n".join(
            [
                f"##### E2-4: порядок глав vs spine (verbatim)\nдата: {stamp}",
                "порядок itemref в spine (из архива):",
                *spine_href_order,
                "алфавитный порядок имён файлов:",
                *[
                    f"  {name}"
                    for name in sorted(["a_last.xhtml", "m_mid.xhtml", "z_alpha.xhtml"])
                ],
                "фактический порядок ридера (spine_index: title -> document):",
                *[
                    f"  {chapter.spine_index}: {chapter.title} -> {chapter.document_path}"
                    for chapter in book.chapters
                ],
                "совпадение с spine: 100%" if order_ok else "НЕСОВПАДЕНИЕ С SPINE",
            ]
        ),
    )


def ev_e2_5(stamp: str, work: Path, settings: Settings) -> None:
    book_c = read_epub(FIXTURES / "complex.epub", work / "run_c", settings)
    estimate = book_c.stats.token_estimate
    run_probe.WORK = work / "engine"
    run_probe.BOOK = FIXTURES / "complex.epub"
    server = MockLLMServer()
    server.start()
    try:
        run = run_probe.run_translate("e2ev", submit=SubmitKind.APPEND_BLOCK, server=server)
        translate_reqs = [r for r in server.requests_log() if r["kind"] == "translate"]
    finally:
        server.stop()
    encoding = tiktoken.get_encoding("cl100k_base")
    per_req = [len(encoding.encode(str(r["user"]))) for r in translate_reqs]
    actual = sum(per_req)
    deviation = abs(estimate - actual) / actual
    evidence(
        "e2-5_token_estimate.txt",
        "\n".join(
            [
                f"##### E2-5: оценка токенов vs фактический расход (verbatim)\nдата: {stamp}",
                "книга: complex.epub; encoding=cl100k_base (тот же, что у движка, из конфига)",
                f"оценка ридера (stats.token_estimate): {estimate}",
                f"translate-запросов движком: {len(translate_reqs)} "
                f"(движок разбил книгу на группы)",
                *[
                    f"  запрос {i + 1}: user-текст {len(str(req['user']))} символов = "
                    f"{tokens} токенов"
                    for i, (req, tokens) in enumerate(zip(translate_reqs, per_req, strict=True))
                ],
                f"фактический расход (сумма): {actual}",
                f"отклонение: {deviation:.1%}   (порог E2-5: <= 15%)",
                f"исключение движка: {run.exception}",
            ]
        ),
    )


def ev_e2_6(stamp: str, work: Path, settings: Settings) -> None:
    lang_rows = []
    for filename, lang in [
        ("probe.en.epub", "en"),
        ("lang_de.epub", "de"),
        ("lang_fr.epub", "fr"),
        ("lang_nl.epub", "nl"),
        ("lang_it.epub", "it"),
    ]:
        book_l = read_epub(FIXTURES / filename, work / f"lang_{lang}", settings)
        lang_rows.append(
            f"{filename}: dc:language={book_l.language} (ожид. {lang}) — "
            f"глав={book_l.stats.chapter_count}, сегментов={book_l.stats.segment_count}, "
            f"ошибок=0"
        )
    evidence(
        "e2-6_five_languages.txt",
        "\n".join(
            [
                f"##### E2-6: разбор фикстур на 5 языках (verbatim)\nдата: {stamp}",
                *lang_rows,
                "итого: 5/5 без ошибок",
            ]
        ),
    )


def main() -> int:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "Z"
    settings = load_settings(ROOT / "config" / "config.yaml", ROOT / ".env")
    work = ROOT / "cache" / "e2_evidence"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    book_a = read_epub(FIXTURES / "complex.epub", work / "run_a", settings)
    book_b = read_epub(FIXTURES / "complex.epub", work / "run_b", settings)
    ev_e2_1(stamp, book_a, book_b)
    ev_e2_2(stamp, book_a)
    ev_e2_3(stamp, work, settings)
    ev_e2_4(stamp, book_a)
    ev_e2_5(stamp, work, settings)
    ev_e2_6(stamp, work, settings)
    print("E2 evidence собран")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
