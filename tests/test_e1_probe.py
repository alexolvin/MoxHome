"""T-E1: pytest-тесты к ENGINE_PROBE (критерии E1-2, E1-3, E1-6).

Пороги дублируют CONTROL: тёплый кэш ≤ 2% токенов, смена языка ≥ 90%,
concurrency 1 vs 4 — побайтово одинаковые главы.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest
from epub_translator import SubmitKind

ROOT = Path(__file__).resolve().parents[1]
_PROBE_DIR = str(ROOT / "tests" / "probe")
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

import run_probe  # noqa: E402
from mock_llm_server import MockLLMServer  # noqa: E402


@pytest.fixture()
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work_dir = tmp_path / "e1"
    work_dir.mkdir()
    monkeypatch.setattr(run_probe, "WORK", work_dir)
    return work_dir


def _chapters(epub_path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(epub_path) as archive:
        return {
            name: archive.read(name)
            for name in ("OEBPS/chapter1.xhtml", "OEBPS/chapter2.xhtml")
        }


def test_e1_warm_cache_within_2_percent(work: Path) -> None:
    """T-E1-1: повторный прогон по тёплому кэшу — 0 LLM-вызовов, прирост ≤ 2%."""
    server_a = MockLLMServer()
    server_a.start()
    try:
        run_a = run_probe.run_translate(
            "t1a", submit=SubmitKind.APPEND_BLOCK, server=server_a, cache_name="t1"
        )
        calls_a = len(server_a.requests_log())
    finally:
        server_a.stop()
    assert run_a.exception is None
    assert run_a.total_tokens > 0
    assert calls_a > 0

    server_b = MockLLMServer()
    server_b.start()
    try:
        run_b = run_probe.run_translate(
            "t1b", submit=SubmitKind.APPEND_BLOCK, server=server_b, cache_name="t1"
        )
        calls_b = len(server_b.requests_log())
    finally:
        server_b.stop()
    assert run_b.exception is None
    assert calls_b == 0
    assert run_b.total_tokens <= run_a.total_tokens * 0.02


def test_e1_language_change_invalidates_cache(work: Path) -> None:
    """T-E1-2: смена target_language инвалидирует кэш — прирост ≥ 90%."""
    server_a = MockLLMServer()
    server_a.start()
    try:
        run_a = run_probe.run_translate(
            "t2a", submit=SubmitKind.APPEND_BLOCK, server=server_a, cache_name="t2"
        )
    finally:
        server_a.stop()
    assert run_a.exception is None
    assert run_a.total_tokens > 0

    server_c = MockLLMServer()
    server_c.start()
    try:
        run_c = run_probe.run_translate(
            "t2c",
            submit=SubmitKind.APPEND_BLOCK,
            server=server_c,
            cache_name="t2",
            target_language="de",
        )
    finally:
        server_c.stop()
    assert run_c.exception is None
    assert run_c.total_tokens >= run_a.total_tokens * 0.9


def test_e1_concurrency_preserves_document_order(work: Path) -> None:
    """T-E1-3: concurrency=4 даёт побайтово те же главы, что concurrency=1."""
    server_1 = MockLLMServer()
    server_1.start()
    try:
        run_c1 = run_probe.run_translate(
            "t3c1", submit=SubmitKind.APPEND_BLOCK, server=server_1, concurrency=1
        )
    finally:
        server_1.stop()
    server_4 = MockLLMServer()
    server_4.start()
    try:
        run_c4 = run_probe.run_translate(
            "t3c4", submit=SubmitKind.APPEND_BLOCK, server=server_4, concurrency=4
        )
    finally:
        server_4.stop()
    assert run_c1.exception is None
    assert run_c4.exception is None
    assert _chapters(work / "out_t3c1.epub") == _chapters(work / "out_t3c4.epub")
