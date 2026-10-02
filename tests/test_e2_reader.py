# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""T-E2: ридер EPUB и сегментатор (критерии CONTROL E2-1…E2-6 + T-E2-7, T-E2-8)."""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
import tiktoken
from epub_translator import SubmitKind

from app.core.config import Settings, load_settings
from app.core.errors import InputValidationError
from app.domain.book import Book
from app.epub.reader import read_epub

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "epub"
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

import run_probe  # noqa: E402
from mock_llm_server import MockLLMServer  # noqa: E402


@pytest.fixture()
def settings(
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[[str, str], Path],
    tmp_path: Path,
) -> Settings:
    return load_settings(make_yaml(str(tmp_path / "storage"), ""), make_env(None))


def _read(settings: Settings, fixture: str, work: Path) -> Book:
    return read_epub(FIXTURES / fixture, work, settings)


def test_e2_1_segment_ids_stable_across_runs(settings: Settings, tmp_path: Path) -> None:
    """T-E2-1 (E2-1): segment_id идентичен между двумя запусками (разные work-каталоги)."""
    book_a = _read(settings, "complex.epub", tmp_path / "w_a")
    book_b = _read(settings, "complex.epub", tmp_path / "w_b")
    ids_a = [s.segment_id for c in book_a.chapters for s in c.segments]
    ids_b = [s.segment_id for c in book_b.chapters for s in c.segments]
    assert ids_a, "сегменты не найдены"
    assert ids_a == ids_b
    assert len(set(ids_a)) == len(ids_a), "segment_id дублируются внутри книги"


def test_e2_2_segment_count_matches_manual_reference(settings: Settings, tmp_path: Path) -> None:
    """T-E2-2 (E2-2): число сегментов против эталона, подсчитанного вручную."""
    book = _read(settings, "complex.epub", tmp_path / "w")
    # Эталон (вручную по структуре фикстуры):
    #   ch1: h1 + p*3 + li*2 + blockquote + p внутри blockquote = 8
    #   ch2: h2 + p + td*4 + dt*2 + dd*2 + p*2 + p-сноска = 13
    #   ch3: h3 + p + blockquote + p внутри blockquote + li = 5
    counts = [len(chapter.segments) for chapter in book.chapters]
    assert counts == [8, 13, 5]
    assert book.stats.segment_count == 26


def test_e2_3_drm_rejected_without_reading(settings: Settings, tmp_path: Path) -> None:
    """T-E2-3 (E2-3): отклонение по encryption.xml — без попытки чтения содержимого."""
    work = tmp_path / "w_drm"
    with pytest.raises(InputValidationError, match="DRM"):
        _read(settings, "drm.epub", work)
    assert not work.exists(), "книга должна отклоняться до распаковки"


def test_e2_4_chapter_order_follows_spine(settings: Settings, tmp_path: Path) -> None:
    """T-E2-4 (E2-4): порядок глав = порядок spine, а не алфавит имён файлов."""
    book = _read(settings, "complex.epub", tmp_path / "w")
    filenames = [Path(chapter.document_path).name for chapter in book.chapters]
    assert filenames != sorted(filenames), "имена файлов должны быть перемешаны"
    assert [chapter.title for chapter in book.chapters] == [
        "Chapter One",
        "Chapter Two",
        "Chapter Three",
    ]
    assert [chapter.spine_index for chapter in book.chapters] == [0, 1, 2]
    assert [chapter.chapter_id for chapter in book.chapters] == ["ch1", "ch2", "ch3"]


def test_e2_5_token_estimate_within_15_percent(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-E2-5 (E2-5): оценка токенов против фактического расхода, отклонение ≤ 15%.

    «Фактический расход» = сумма токенов текстовых частей (user-сообщений) всех
    translate-запросов движка на той же книге (движок может разбить книгу на
    несколько групп), измеренных на mock-эндпоинте тем же token_encoding.
    """
    book = _read(settings, "complex.epub", tmp_path / "w")
    estimate = book.stats.token_estimate

    engine_work = tmp_path / "engine"
    monkeypatch.setattr(run_probe, "WORK", engine_work)
    monkeypatch.setattr(run_probe, "BOOK", FIXTURES / "complex.epub")
    server = MockLLMServer()
    server.start()
    try:
        run = run_probe.run_translate(
            "e2tokens", submit=SubmitKind.APPEND_BLOCK, server=server
        )
        translate_reqs = [
            item for item in server.requests_log() if item["kind"] == "translate"
        ]
    finally:
        server.stop()
    assert run.exception is None
    assert translate_reqs, "translate-запросов не было"

    encoding = tiktoken.get_encoding("cl100k_base")
    actual = sum(len(encoding.encode(str(req["user"]))) for req in translate_reqs)
    deviation = abs(estimate - actual) / actual
    assert deviation <= 0.15, f"оценка {estimate}, фактически {actual}, отклонение {deviation:.1%}"


def test_e2_6_all_five_languages_parse(settings: Settings, tmp_path: Path) -> None:
    """T-E2-6 (E2-6, Rule 4): разбор фикстур на всех 5 языках без ошибок."""
    books = [
        ("probe.en.epub", "en"),
        ("lang_de.epub", "de"),
        ("lang_fr.epub", "fr"),
        ("lang_nl.epub", "nl"),
        ("lang_it.epub", "it"),
    ]
    parsed: list[tuple[str, int]] = []
    for filename, lang in books:
        book = _read(settings, filename, tmp_path / f"w_{lang}")
        assert book.language == lang
        assert book.stats.segment_count > 0
        parsed.append((lang, book.stats.segment_count))
    assert [lang for lang, _ in parsed] == ["en", "de", "fr", "nl", "it"]


def test_e2_7_broken_opf_gives_clear_error(settings: Settings, tmp_path: Path) -> None:
    """T-E2-7: повреждённый OPF — внятная ошибка приложения, не сырой стек."""
    with pytest.raises(InputValidationError) as excinfo:
        _read(settings, "broken.epub", tmp_path / "w")
    assert "content.opf" in excinfo.value.message
    assert excinfo.value.exit_code == 3


def test_e2_8_nested_markup_split(settings: Settings, tmp_path: Path) -> None:
    """T-E2-8: вложенная разметка — в marked_text сохраняется, в plain_text отсутствует."""
    book = _read(settings, "complex.epub", tmp_path / "w")
    chapter1 = book.chapters[0]
    paragraph = next(s for s in chapter1.segments if s.plain_text.startswith("The quick"))
    assert "<b>" in paragraph.marked_text
    assert "<i>" in paragraph.marked_text
    assert "<" not in paragraph.plain_text
    quote = next(
        s
        for s in chapter1.segments
        if s.plain_text.startswith("The fox said") and "<p>" in s.marked_text
    )
    assert quote.marked_text.lstrip().startswith("<p>")
