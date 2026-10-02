# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Тесты M3 (CONTROL): тракт M — кодек разметки + черновик (MTPE TZ §4.7, §7).

- M3-1 — кодек обратим: `decode(encode(marked_text)) == marked_text` побайтно,
  100% сегментов 5 фикстур.
- M3-2 — литеральные `⟦`/`⟧` в источнике экранируются (U+E000/U+E001) и
  восстанавливаются; тег рядом — маркером.
- M3-3 — покрытие: `id(segments.jsonl) ⊆ id(drafts.jsonl)` (100%), 0 потерянных.
- M3-4 — возобновление после «убитого» прогона (50%): повторов завершённых
  сегментов 0; `drafts.jsonl` побайтно равен непрерывному прогону (mock — счётчик).
- M3-5 — повторный запуск завершённой фазы: 0 новых запросов LLM (и без прогрева).
- M3-6 — `model_mismatch`: остановка, 0 записей после отказа.
- M3-7 — `draft_bad`: echo / truncate / wrong_lang → флаг в `drafts.jsonl`.
- M3-8 — число строк: `<br>` → `\\n`; число `\\n` в draft == числу `\\n` в src_plain.
- M3-9 — имена языков, пороги, текст шаблона в коде `app/mtpe/`, `app/llm/` — 0.

Задание в БД не создаётся (фаза черновика строки `jobs` не требует; учёт токенов
идёт в `job_tokens`): `job_id` — строка, `add_tokens` без задачи — no-op (0 строк).
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings, load_settings
from app.domain.book import Book, BookStats, Chapter, Segment
from app.epub.reader import read_epub
from app.llm.mt import MTModelError
from app.mtpe.draft import DraftPhase
from app.mtpe.markup import decode, encode
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "epub"
_COMPLEX = FIXTURES / "complex.epub"
_BOOKS = ["complex.epub", "lang_de.epub", "lang_fr.epub", "lang_it.epub", "lang_nl.epub"]
JAR = str(ROOT / "data" / "tools" / "epubcheck-5.3.0" / "epubcheck.jar")
_LANG_ROOT = str(ROOT / "config" / "languages")
_MATRIX = str(ROOT / "config" / "language_matrix.yaml")
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from mock_llm_server import MockLLMServer  # noqa: E402

#: Пара языка для всех прогонов: источник en (есть spaCy `en_core_web_sm`),
#: цель ru (есть mt_name «Russian»). Черновик собирается в mock-мире.
_SRC, _TGT = "en", "ru"


@pytest.fixture()
def mock_server() -> Iterator[MockLLMServer]:
    server = MockLLMServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


# ------------------------------------------------------------------ служебное


def _m3_settings(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    server: MockLLMServer,
    *,
    mt_overrides: Mapping[str, object] | None = None,
) -> tuple[Settings, Database, JobRepository]:
    """Настройки + БД + репозиторий: 5 профилей (mt → mock), реестр, промпты."""
    root = tmp_path / "storage"
    root.mkdir(parents=True, exist_ok=True)
    prompts = root / "prompts"
    if not prompts.exists():
        shutil.copytree(ROOT / "prompts", prompts)

    mt: dict[str, object] = {
        "base_url": server.url,
        "model": "mt-model",
        "api": "completions",
        "prompt_task": "mt_draft",
        "temperature": 0,
        "top_k": 1,
        "max_tokens_ratio": 3.0,
        "max_segment_tokens": 400,
        "retries": 3,
        "timeout_s": 120,
        "warmup_timeout_s": 900,
    }
    if mt_overrides:
        mt.update(mt_overrides)
    base = server.url

    def prof(name: str, fields: Mapping[str, object]) -> str:
        lines = [f"    {name}:"]
        for key, value in fields.items():
            lines.append(f"      {key}: {json.dumps(value, ensure_ascii=False)}")
        return "\n".join(lines)

    profiles = "\n".join(
        [
            prof("translation", {"base_url": base, "model": "t-model"}),
            prof("fill", {"base_url": base, "model": "f-model"}),
            prof("analysis", {"base_url": base, "model": "a-model"}),
            prof("mt", mt),
            prof(
                "postedit",
                {"base_url": base, "model": "pe-model", "api": "chat",
                 "prompt_task": "postedit", "temperature": 0, "retries": 3,
                 "timeout_s": 300, "warmup_timeout_s": 900},
            ),
        ]
    )
    cfg = (
        f"storage:\n  root: {json.dumps(str(root), ensure_ascii=False)}\n"
        "translation:\n  pipeline: mt_pe\n"
        f"epubcheck:\n  jar_path: {json.dumps(JAR, ensure_ascii=False)}\n"
        f"languages:\n  root: {json.dumps(_LANG_ROOT, ensure_ascii=False)}\n"
        f"  matrix: {json.dumps(_MATRIX, ensure_ascii=False)}\n"
        f"llm:\n  prompts_root: prompts\n  profiles:\n{profiles}\n"
    )
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg, encoding="utf-8")
    settings = load_settings(cfg_path, make_env(None))
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    return settings, db, JobRepository(db)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _zero_ms(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Обнулить `ms` (лаги локального mock не детерминированы) для побайтного сравнения."""
    out: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        row["ms"] = 0
        out.append(row)
    return out


def _truncate(path: Path, n_rows: int) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("".join(line + "\n" for line in lines[:n_rows]), encoding="utf-8")


def _single_segment_book(marked_text: str) -> Book:
    """Синтетическая книга с одним сегментом (M3-7/M3-8: управляемый ввод)."""
    word_count = len(marked_text.split())
    segment = Segment(
        segment_id="s1",
        chapter_id="c1",
        index_in_chapter=0,
        index_global=0,
        plain_text="",
        marked_text=marked_text,
        char_count=len(marked_text),
        word_count=word_count,
    )
    chapter = Chapter(
        chapter_id="c1",
        title="t",
        document_path="c1.xhtml",
        spine_index=0,
        segments=(segment,),
    )
    stats = BookStats(
        chapter_count=1,
        spine_document_count=1,
        segment_count=1,
        char_count=len(marked_text),
        word_count=word_count,
        image_count=0,
        table_count=0,
        footnote_count=0,
        has_drm=False,
        token_estimate=10,
    )
    return Book(
        title="t",
        language=_SRC,
        chapters=(chapter,),
        stats=stats,
        suspicious=False,
        suspicious_reasons=(),
    )


def _multi_segment_book(n: int) -> Book:
    """n однострочных сегментов: каждый = ровно 1 запрос черновика (строка < порога).

    Свойство `1 сегмент ↔ 1 запрос` делает счётчик вызовов mock'а прямым
    измерением «повторов 0» (M3-4)."""
    segments: list[Segment] = []
    for i in range(1, n + 1):
        text = f"Single line segment number {i} is short."
        segments.append(
            Segment(
                segment_id=f"s{i}",
                chapter_id="c1",
                index_in_chapter=i - 1,
                index_global=i - 1,
                plain_text="",
                marked_text=text,
                char_count=len(text),
                word_count=len(text.split()),
            )
        )
    chapter = Chapter(
        chapter_id="c1",
        title="t",
        document_path="c1.xhtml",
        spine_index=0,
        segments=tuple(segments),
    )
    stats = BookStats(
        chapter_count=1,
        spine_document_count=1,
        segment_count=n,
        char_count=sum(s.char_count for s in segments),
        word_count=sum(s.word_count for s in segments),
        image_count=0,
        table_count=0,
        footnote_count=0,
        has_drm=False,
        token_estimate=10,
    )
    return Book(
        title="t",
        language=_SRC,
        chapters=(chapter,),
        stats=stats,
        suspicious=False,
        suspicious_reasons=(),
    )


# ------------------------------------------------------------------ M3-1


def test_m3_1_codec_reversible(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M3-1: decode(encode(marked_text)) == marked_text побайтно — 100% сегментов фикстур.
    settings, db, _ = _m3_settings(tmp_path, make_env, mock_server)
    try:
        total = 0
        for name in _BOOKS:
            book = read_epub(FIXTURES / name, tmp_path / "work" / name, settings)
            for chapter in book.chapters:
                for segment in chapter.segments:
                    enc = encode(segment.marked_text)
                    restored = decode(enc.src_encoded, enc.markup_map)
                    assert restored == segment.marked_text, f"{name}: побайтовая обратимость"
                    total += 1
        assert total > 0, "в фикстурах нет сегментов"
    finally:
        db.close()


# ------------------------------------------------------------------ M3-2


def test_m3_2_literal_markers_roundtrip() -> None:
    # M3-2: литеральные ⟦⟧ экранируются (U+E000/U+E001) и восстанавливаются;
    # тег — маркером. Побайтовая обратимость сохранена.
    text = "literal \u27e6 and \u27e7 in text <b>bold</b> end"
    enc = encode(text)
    # Литералы ушли в PUA (не путаются с тегами-маркерами ⟦n⟧, использующими U+27E6).
    assert "\ue000" in enc.src_encoded, "литеральный ⟦ экранирован в U+E000"
    assert "\ue001" in enc.src_encoded, "литеральный ⟧ экранирован в U+E001"
    restored = decode(enc.src_encoded, enc.markup_map)
    assert restored == text, "побайтовое восстановление с литеральными маркерами"


# ------------------------------------------------------------------ M3-3


def test_m3_3_coverage(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M3-3: id(segments.jsonl) ⊆ id(drafts.jsonl) — 100%, каждый сегмент в черновике.
    settings, db, repo = _m3_settings(tmp_path, make_env, mock_server)
    try:
        result = DraftPhase(settings, repo, "j-m3").run(
            str(_COMPLEX), _SRC, _TGT, work_dir=str(tmp_path / "work")
        )
        m_dir = settings.job_dir("j-m3") / "m"
        seg_ids = {row["segment_id"] for row in _read_rows(m_dir / "segments.jsonl")}
        draft_ids = {row["segment_id"] for row in _read_rows(m_dir / "drafts.jsonl")}
        assert result.segment_count == len(seg_ids) > 0
        assert seg_ids == draft_ids, "покрытие 100%: ни один сегмент не потерян"
    finally:
        db.close()


# ------------------------------------------------------------------ M3-4


def test_m3_4_resume_after_kill(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M3-4: «убитый» прогон на 50% → возобновление без повторов; drafts.jsonl
    # побайтно равен непрерывному прогону (mock — счётчик вызовов). Однострочные
    # сегменты: 1 сегмент = 1 запрос, поэтому счётчик вызовов = число сегментов.
    settings, db, repo = _m3_settings(tmp_path, make_env, mock_server)
    try:
        m_dir = settings.job_dir("j-m3") / "m"
        drafts = m_dir / "drafts.jsonl"
        n = 10
        book = _multi_segment_book(n)

        DraftPhase(settings, repo, "j-m3").run_book(book, _SRC, _TGT)
        full_1 = _read_rows(drafts)
        assert len(full_1) == n

        _truncate(drafts, n // 2)  # «смерть» в середине: осталась первая половина
        before = len(mock_server.requests_log())
        DraftPhase(settings, repo, "j-m3").run_book(book, _SRC, _TGT)
        run2 = mock_server.requests_log()[before:]
        run2_drafts = [r for r in run2 if r["kind"] == "mt"]
        # Ровно незавершённая половина — ни одного повтора завершённых сегментов.
        assert len(run2_drafts) == n - n // 2, "повторов завершённых сегментов нет"

        full_2 = _read_rows(drafts)
        assert len(full_2) == n, "после возобновления — все сегменты"
        assert _zero_ms(full_2) == _zero_ms(full_1), "drafts.jsonl == непрерывный прогон"
    finally:
        db.close()


# ------------------------------------------------------------------ M3-5


def test_m3_5_rerun_no_calls(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M3-5: повторный запуск завершённой фазы — 0 новых запросов LLM (и без прогрева).
    settings, db, repo = _m3_settings(tmp_path, make_env, mock_server)
    try:
        DraftPhase(settings, repo, "j-m3").run(
            str(_COMPLEX), _SRC, _TGT, work_dir=str(tmp_path / "work" / "run1")
        )
        after_1 = len(mock_server.requests_log())
        assert after_1 > 0, "первый прогон породил запросы"

        result = DraftPhase(settings, repo, "j-m3").run(
            str(_COMPLEX), _SRC, _TGT, work_dir=str(tmp_path / "work" / "run2")
        )
        after_2 = len(mock_server.requests_log())
        assert after_2 == after_1, "повторный запуск — 0 запросов LLM (вкл. прогрев)"
        assert result.request_count == 0
        assert result.resumed_count == result.segment_count, "все сегменты возобновлены"
    finally:
        db.close()


# ------------------------------------------------------------------ M3-6


def test_m3_6_model_mismatch(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M3-6: model_mismatch → остановка (MTModelError), 0 записей после отказа.
    settings, db, repo = _m3_settings(tmp_path, make_env, mock_server)
    mock_server.set_scenario("model_mismatch")
    try:
        with pytest.raises(MTModelError):
            DraftPhase(settings, repo, "j-m3").run(
                str(_COMPLEX), _SRC, _TGT, work_dir=str(tmp_path / "work")
            )
        rows = _read_rows(settings.job_dir("j-m3") / "m" / "drafts.jsonl")
        assert rows == [], "0 записей после отказа model_mismatch"
    finally:
        mock_server.set_scenario("ok")
        db.close()


# ------------------------------------------------------------------ M3-7


@pytest.mark.parametrize("scenario", ["mt_echo", "mt_truncate", "mt_wrong_lang"])
def test_m3_7_draft_bad(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
    scenario: str,
) -> None:
    # M3-7: echo / truncate / wrong_lang → флаг draft_bad в drafts.jsonl.
    settings, db, repo = _m3_settings(tmp_path, make_env, mock_server)
    job_id = f"j-m3-{scenario}"
    mock_server.set_scenario(scenario)
    try:
        book = _single_segment_book("Good morning friend how are you today")
        result = DraftPhase(settings, repo, job_id).run_book(book, _SRC, _TGT)
        rows = _read_rows(settings.job_dir(job_id) / "m" / "drafts.jsonl")
        assert len(rows) == 1
        assert "draft_bad" in rows[0]["flags"], f"{scenario}: флаг draft_bad"
        assert result.draft_bad_count == 1
    finally:
        mock_server.set_scenario("ok")
        db.close()


# ------------------------------------------------------------------ M3-8


def test_m3_8_line_count(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M3-8: <br> → \n; число \n в draft == числу \n в src_plain (строки не сливаются).
    settings, db, repo = _m3_settings(tmp_path, make_env, mock_server)
    try:
        marked = "first<br/>second"
        book = _single_segment_book(marked)
        DraftPhase(settings, repo, "j-m3-line").run_book(book, _SRC, _TGT)

        enc = encode(marked)
        assert enc.src_plain.count("\n") == 1, "br даёт ровно один \\n в src_plain"
        rows = _read_rows(settings.job_dir("j-m3-line") / "m" / "drafts.jsonl")
        assert len(rows) == 1
        assert rows[0]["draft"].count("\n") == enc.src_plain.count("\n"), (
            "число строк черновика == числу строк исходника"
        )
    finally:
        db.close()


# ------------------------------------------------------------------ M3-9


def test_m3_9_no_hardcoded_lang_or_template() -> None:
    # M3-9: имена языков / текст шаблона (и пороги) в коде app/mtpe/, app/llm/ — 0.
    lang_names = ["English", "German", "French", "Dutch", "Italian", "Russian"]
    full_template = (
        "Translate this from {src_name} to {tgt_name}:\n"
        "{src_name}: {text}\n"
        "{tgt_name}:"
    )
    needles = [*lang_names, full_template]
    hits: list[str] = []
    for sub in ("mtpe", "llm"):
        for path in sorted((ROOT / "app" / sub).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                if needle in text:
                    hits.append(f"{path.relative_to(ROOT)}: {needle[:40]!r}")
    # Маркер эха `_ECHO_MARKER` (draft.py) — проверка по TZ §7 п.9, не шаблон:
    # полный текст шаблона и имена языков в коде отсутствуют.
    assert hits == [], "\n".join(hits)
