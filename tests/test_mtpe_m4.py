# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Тесты M4 (CONTROL): тракт M — постредактура (MTPE TZ §4).

- M4-1 — потеря структурных элементов в итоговом тексте 0 (сверка карт разметки).
- M4-2 — молчаливые правки среди принятых ответов 0 (пересчёт H6 по final.jsonl).
- M4-3 — полнота аудита: 100 % сегментов с правками/флагами в quality.log.
- M4-4 — возобновление после «kill -9» на 50 %: 0 повторных вызовов, final.jsonl
  побайтно равен непрерывному прогону.
- M4-5 — контекст сегмента — ровно K предыдущих той же главы (эхо segment_id).
- M4-6 — pe_marker_loss на попытках 1–2 → выполнена попытка 3.
- M4-7 — все попытки провалены: без структурных → markup_dropped; со структурными
  → остановка pe_unrecoverable.
- M4-8 — pe_bad_type / pe_silent_edit → отказ H1/H6 и переход по лестнице.
- M4-9 — pe_overedit / pe_reject_burst → остановка на первой контрольной отметке.
- M4-10 — таксономия и пороги в коде app/mtpe/, app/llm/ — 0 вхождений.

Сценарий mock (app/llm/postedit — ветвь chat): `ok` — черновик + маркеры оригинала
(H2/H6 проходят); `pe_marker_loss`, `pe_marker_loss_structural`, `pe_reject_burst`
— без последнего маркера (H2); `pe_bad_type` — type вне таксономии (H1);
`pe_silent_edit` — edits пуст, text ≠ draft (H6); `pe_overedit` — далёкий text (S2).
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings, load_settings
from app.domain.book import Book, BookStats, Chapter, Segment
from app.mtpe.checks import marker_multiset, text_without_markers
from app.mtpe.draft import DraftPhase
from app.mtpe.postedit import PostEditPhase, PostEditStopError
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository

ROOT = Path(__file__).resolve().parents[1]
JAR = str(ROOT / "data" / "tools" / "epubcheck-5.3.0" / "epubcheck.jar")
_LANG_ROOT = str(ROOT / "config" / "languages")
_MATRIX = str(ROOT / "config" / "language_matrix.yaml")
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from mock_llm_server import MockLLMServer  # noqa: E402

#: Пара: источник en, цель ru (есть spaCy en_core_web_sm + mt_name Russian).
_SRC, _TGT = "en", "ru"

#: Регулярка [segment_id] в блоке контекста (M4-5).
_CTX_ID_RE = re.compile(r"^\[([^\]]+)\]$", re.MULTILINE)


@pytest.fixture()
def mock_server() -> Iterator[MockLLMServer]:
    server = MockLLMServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


# ------------------------------------------------------------------ служебное


def _m4_settings(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    server: MockLLMServer,
    *,
    mtpe_overrides: Mapping[str, object] | None = None,
) -> tuple[Settings, Database, JobRepository]:
    root = tmp_path / "storage"
    root.mkdir(parents=True, exist_ok=True)
    prompts = root / "prompts"
    if not prompts.exists():
        shutil.copytree(ROOT / "prompts", prompts)
    tax_dir = root / "config" / "taxonomies"
    if not tax_dir.exists():
        shutil.copytree(ROOT / "config" / "taxonomies", tax_dir)

    base = server.url
    mt = {
        "base_url": base,
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
    mtpe_lines = ""
    if mtpe_overrides:
        mtpe_lines = "mtpe:\n" + "".join(
            f"  {key}: {value}\n" for key, value in mtpe_overrides.items()
        )
    cfg = (
        f"storage:\n  root: {json.dumps(str(root), ensure_ascii=False)}\n"
        "translation:\n  pipeline: mt_pe\n"
        f"epubcheck:\n  jar_path: {json.dumps(JAR, ensure_ascii=False)}\n"
        f"languages:\n  root: {json.dumps(_LANG_ROOT, ensure_ascii=False)}\n"
        f"  matrix: {json.dumps(_MATRIX, ensure_ascii=False)}\n"
        f"llm:\n  prompts_root: prompts\n  profiles:\n{profiles}\n" + mtpe_lines
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


def _single_segment_book(marked_text: str) -> Book:
    word_count = len(marked_text.split())
    segment = Segment(
        segment_id="s1", chapter_id="c1", index_in_chapter=0, index_global=0,
        plain_text="", marked_text=marked_text, char_count=len(marked_text),
        word_count=word_count,
    )
    chapter = Chapter("c1", "t", "c1.xhtml", 0, (segment,))
    stats = BookStats(1, 1, 1, len(marked_text), word_count, 0, 0, 0, False, 10)
    return Book("t", _SRC, (chapter,), stats, False, ())


def _multi_segment_book(n: int) -> Book:
    segments: list[Segment] = []
    for i in range(1, n + 1):
        text = f"Single line segment number {i} is short."
        segments.append(
            Segment(
                segment_id=f"s{i}", chapter_id="c1", index_in_chapter=i - 1,
                index_global=i - 1, plain_text="", marked_text=text,
                char_count=len(text), word_count=len(text.split()),
            )
        )
    chapter = Chapter("c1", "t", "c1.xhtml", 0, tuple(segments))
    stats = BookStats(1, 1, n, sum(s.char_count for s in segments),
                      sum(s.word_count for s in segments), 0, 0, 0, False, 10)
    return Book("t", _SRC, (chapter,), stats, False, ())


def _wbr_book(n: int) -> Book:
    """n однострочных сегментов с пустым элементом `<wbr/>` (маркер `⟦n/⟧`,
    без структурных элементов; `pe_marker_loss` снимает его → H2 → pe_rejected)."""
    segments: list[Segment] = []
    for i in range(1, n + 1):
        text = f"Hello<wbr/>world number {i} here."
        segments.append(
            Segment(
                segment_id=f"s{i}", chapter_id="c1", index_in_chapter=i - 1,
                index_global=i - 1, plain_text="", marked_text=text,
                char_count=len(text), word_count=len(text.split()),
            )
        )
    chapter = Chapter("c1", "t", "c1.xhtml", 0, tuple(segments))
    stats = BookStats(1, 1, n, sum(s.char_count for s in segments),
                      sum(s.word_count for s in segments), 0, 0, 0, False, 10)
    return Book("t", _SRC, (chapter,), stats, False, ())


def _structural_book() -> Book:
    """Один сегмент с парным (структурным) элементом `<b>`."""
    marked = "The <b>important</b> word here."
    return _single_segment_book(marked)


def _run_mtpe(
    settings: Settings,
    repo: JobRepository,
    job_id: str,
    book: Book,
    server: MockLLMServer,
    *,
    scenario: str,
) -> Any:
    """Черновик (M3) → постредактура (M4) на том же job_id. Возвращает результат фазы."""
    server.set_scenario(scenario)
    DraftPhase(settings, repo, job_id).run_book(book, _SRC, _TGT)
    return PostEditPhase(settings, repo, job_id).run(_SRC, _TGT)


def _last_postedit_user(server: MockLLMServer) -> str:
    """Последнее user-сообщение postedit (kind=postedit) из журнала mock."""
    for req in reversed(server.requests_log()):
        if req.get("kind") == "postedit":
            return str(req.get("user", ""))
    return ""


def _context_block(user: str) -> str:
    marker = "Context (previous segments of this chapter):\n"
    end = "\n\nOriginal (with markers):\n"
    start = user.find(marker)
    stop = user.find(end)
    if start == -1 or stop == -1:
        return ""
    return user[start + len(marker) : stop]


# ------------------------------------------------------------------ M4-1


def test_m4_1_no_structural_loss(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M4-1: итоговый текст сохраняет ВСЕ маркеры оригинала (потеря структурных = 0).
    settings, db, repo = _m4_settings(tmp_path, make_env, mock_server)
    try:
        _run_mtpe(settings, repo, "j-m4-1", _structural_book(), mock_server, scenario="ok")
        m_dir = settings.job_dir("j-m4-1") / "m"
        segments = {str(r["segment_id"]): r for r in _read_rows(m_dir / "segments.jsonl")}
        finals = _read_rows(m_dir / "final.jsonl")
        assert finals, "нет итогов"
        for row in finals:
            original = str(segments[str(row["segment_id"])]["src_encoded"])
            assert marker_multiset(str(row["text"])) == marker_multiset(original), (
                "M4-1: в итоговом тексте потеряны маркеры (структурные элементы)"
            )
    finally:
        db.close()


# ------------------------------------------------------------------ M4-2


def test_m4_2_no_silent_edits(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M4-2: среди принятых (не pe_rejected) нет молчаливых правок (H6).
    settings, db, repo = _m4_settings(tmp_path, make_env, mock_server)
    try:
        _run_mtpe(settings, repo, "j-m4-2", _multi_segment_book(3), mock_server, scenario="ok")
        finals = _read_rows(settings.job_dir("j-m4-2") / "m" / "final.jsonl")
        assert finals, "нет итогов"
        for row in finals:
            if "pe_rejected" in row["flags"]:
                continue
            if row["edits"]:
                continue  # правка объявлена — не молчаливая
            plain = re.sub(r"\s+", " ", text_without_markers(str(row["text"])).strip())
            draft = re.sub(r"\s+", " ", str(row["draft"]).strip())
            assert plain == draft, f"M4-2: молчаливая правка в {row['segment_id']}"
    finally:
        db.close()


# ------------------------------------------------------------------ M4-3


def test_m4_3_audit_completeness(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M4-3: 100 % сегментов с правками/флагами в quality.log (записей без аудита — 0).
    settings, db, repo = _m4_settings(tmp_path, make_env, mock_server)
    try:
        _run_mtpe(settings, repo, "j-m4-3", _wbr_book(2), mock_server, scenario="pe_overedit")
        m_dir = settings.job_dir("j-m4-3") / "m"
        finals = _read_rows(m_dir / "final.jsonl")
        quality = _read_rows(m_dir / "quality.log")
        audited = {str(r["segment_id"]) for r in quality}
        need_audit = {
            str(r["segment_id"]) for r in finals if r["edits"] or r["flags"]
        }
        assert need_audit, "нет сегментов с правками/флагами для проверки аудита"
        assert need_audit == audited, (
            f"M4-3: аудит не полон (нужно {need_audit}, есть {audited})"
        )
    finally:
        db.close()


# ------------------------------------------------------------------ M4-4


def test_m4_4_resume_after_kill(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M4-4: «убитый» прогон на 50 % → возобновление без повторов; final.jsonl
    # побайтно равен непрерывному прогону (1 сегмент = 1 запрос, mock — счётчик).
    settings, db, repo = _m4_settings(tmp_path, make_env, mock_server)
    job_id = "j-m4-4"
    book = _multi_segment_book(4)
    try:
        mock_server.set_scenario("ok")
        DraftPhase(settings, repo, job_id).run_book(book, _SRC, _TGT)
        final = settings.job_dir(job_id) / "m" / "final.jsonl"
        PostEditPhase(settings, repo, job_id).run(_SRC, _TGT)
        full_1 = final.read_text(encoding="utf-8")

        lines = final.read_text(encoding="utf-8").splitlines()
        final.write_text("".join(line + "\n" for line in lines[:2]), encoding="utf-8")
        before = len(mock_server.requests_log())
        result = PostEditPhase(settings, repo, job_id).run(_SRC, _TGT)
        run2_pe = [r for r in mock_server.requests_log()[before:] if r["kind"] == "postedit"]
        # Ровно незавершённая половина (2 сегмента × 1 запрос), ни одного повтора.
        assert len(run2_pe) == 2, "повторов завершённых сегментов нет"
        assert result.resumed_count == 2
        assert final.read_text(encoding="utf-8") == full_1, "final.jsonl == непрерывный прогон"
    finally:
        mock_server.set_scenario("ok")
        db.close()


# ------------------------------------------------------------------ M4-5


def test_m4_5_context_exactly_k(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M4-5: контекст сегмента — ровно K предыдущих той же главы (K=2).
    settings, db, repo = _m4_settings(
        tmp_path, make_env, mock_server, mtpe_overrides={"context_segments": 2}
    )
    try:
        _run_mtpe(settings, repo, "j-m4-5", _multi_segment_book(4), mock_server, scenario="ok")
        user = _last_postedit_user(mock_server)
        ids = _CTX_ID_RE.findall(_context_block(user))
        # Последний сегмент (s4): контекст = ровно s2, s3 (две предыдущих).
        assert ids == ["s2", "s3"], f"M4-5: контекст должен быть ровно K предыдущих, а {ids}"
    finally:
        db.close()


# ------------------------------------------------------------------ M4-6


def test_m4_6_attempt3_runs(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M4-6: pe_marker_loss на попытках 1–2 → выполнена попытка 3 (attempts==3).
    settings, db, repo = _m4_settings(tmp_path, make_env, mock_server)
    try:
        result = _run_mtpe(
            settings, repo, "j-m4-6", _wbr_book(1), mock_server, scenario="pe_marker_loss"
        )
        finals = _read_rows(settings.job_dir("j-m4-6") / "m" / "final.jsonl")
        assert len(finals) == 1
        row = finals[0]
        assert row["attempts"] == 3, f"M4-6: попытка 3 не выполнена (attempts={row['attempts']})"
        assert result.request_count == 3, "3 запроса = попытки 1, 2, 3"
        assert "pe_rejected" in row["flags"]
    finally:
        db.close()


# ------------------------------------------------------------------ M4-7


def test_m4_7_rejected_no_structural_markup_dropped(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M4-7 (без структурных): все попытки провалены → pe_rejected + markup_dropped.
    settings, db, repo = _m4_settings(tmp_path, make_env, mock_server)
    try:
        _run_mtpe(settings, repo, "j-m4-7a", _wbr_book(1), mock_server, scenario="pe_marker_loss")
        finals = _read_rows(settings.job_dir("j-m4-7a") / "m" / "final.jsonl")
        assert len(finals) == 1
        row = finals[0]
        assert "pe_rejected" in row["flags"], "флаг pe_rejected"
        assert "markup_dropped" in row["flags"], "флаг markup_dropped (нет структурных)"
    finally:
        db.close()


def test_m4_7_rejected_structural_unrecoverable(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M4-7 (со структурными): все попытки провалены → остановка pe_unrecoverable.
    settings, db, repo = _m4_settings(tmp_path, make_env, mock_server)
    try:
        with pytest.raises(PostEditStopError) as excinfo:
            _run_mtpe(settings, repo, "j-m4-7b", _structural_book(), mock_server,
                      scenario="pe_marker_loss_structural")
        assert excinfo.value.reason == "pe_unrecoverable"
        assert excinfo.value.details["segment_ids"] == ["s1"]
    finally:
        mock_server.set_scenario("ok")
        db.close()


# ------------------------------------------------------------------ M4-8


@pytest.mark.parametrize("scenario,check", [("pe_bad_type", "H1"), ("pe_silent_edit", "H6")])
def test_m4_8_bad_type_silent_edit(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
    scenario: str,
    check: str,
) -> None:
    # M4-8: pe_bad_type → отказ H1, pe_silent_edit → отказ H6; переход по лестнице.
    settings, db, repo = _m4_settings(tmp_path, make_env, mock_server)
    job_id = f"j-m4-8-{check}"
    try:
        _run_mtpe(settings, repo, job_id, _wbr_book(1), mock_server, scenario=scenario)
        finals = _read_rows(settings.job_dir(job_id) / "m" / "final.jsonl")
        assert len(finals) == 1
        row = finals[0]
        assert "pe_rejected" in row["flags"], f"{check}: сегмент отклонён"
        assert row["attempts"] == 3, f"{check}: переход по лестнице (attempts=3)"
    finally:
        mock_server.set_scenario("ok")
        db.close()


# ------------------------------------------------------------------ M4-9


def test_m4_9_pe_overedit_stop(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M4-9: pe_overedit → остановка на первой контрольной отметке (check_every=2).
    settings, db, repo = _m4_settings(
        tmp_path, make_env, mock_server, mtpe_overrides={"check_every_segments": 2}
    )
    try:
        with pytest.raises(PostEditStopError) as excinfo:
            _run_mtpe(settings, repo, "j-m4-9a", _multi_segment_book(4), mock_server,
                      scenario="pe_overedit")
        assert excinfo.value.reason == "pe_overedit"
        finals = _read_rows(settings.job_dir("j-m4-9a") / "m" / "final.jsonl")
        assert len(finals) == 2, "остановка на первой отметке: обработано 2 сегмента"
    finally:
        mock_server.set_scenario("ok")
        db.close()


def test_m4_9_pe_reject_burst_stop(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    mock_server: MockLLMServer,
) -> None:
    # M4-9: pe_reject_burst → остановка на первой контрольной отметке (check_every=2).
    settings, db, repo = _m4_settings(
        tmp_path, make_env, mock_server, mtpe_overrides={"check_every_segments": 2}
    )
    try:
        with pytest.raises(PostEditStopError) as excinfo:
            _run_mtpe(
                settings, repo, "j-m4-9b", _wbr_book(4), mock_server,
                scenario="pe_reject_burst",
            )
        assert excinfo.value.reason == "pe_reject_ratio_exceeded"
        finals = _read_rows(settings.job_dir("j-m4-9b") / "m" / "final.jsonl")
        assert len(finals) == 2, "остановка на первой отметке: 2 pe_rejected"
    finally:
        mock_server.set_scenario("ok")
        db.close()


# ------------------------------------------------------------------ M4-10


def test_m4_10_no_taxonomy_or_thresholds_in_code() -> None:
    # M4-10: таксономия (id) и пороги (значения) в коде app/mtpe/, app/llm/ — 0.
    taxonomy_ids = [
        "ACC-MISTRANSLATION", "ACC-OMISSION", "ACC-ADDITION", "ACC-RETRANSLATE",
        "TERM-GLOSSARY", "GRAM-AGREEMENT", "CTX-CONSISTENCY", "FLU-CALQUE",
        "PUNCT-TYPOGRAPHY",
    ]
    thresholds = ["0.40", "0.10", "0.005"]
    needles = [*taxonomy_ids, *thresholds]
    hits: list[str] = []
    for sub in ("mtpe", "llm"):
        for path in sorted((ROOT / "app" / sub).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                if needle in text:
                    hits.append(f"{path.relative_to(ROOT)}: {needle!r}")
    assert hits == [], "\n".join(hits)
