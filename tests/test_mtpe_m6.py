"""CONTROL M6 (docs/CONTROL/M6.md): способ mt_pe — ветвление TRANSLATING,
прогресс §4.4, прогрев LLM-стадий (§4.6), причины остановки (req 9), 422,
страница постредактуры, CLI. Тесты воспроизводят критерии CONTROL M6.

Требует jar EPUBCheck (как M4/M5)."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.cli import _way_phase_line
from app.core.config import load_settings
from app.core.errors import AppError
from app.domain.job import Job, JobStatus
from app.llm.warmup import LLMWarmupError, warmup_profile
from app.services.jobs import SettingsIncompleteError, create_job
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.web import views
from app.workers.pipeline import Pipeline
from app.workers.worker import _format_stop_reason
from tests.probe.mock_llm_server import MockLLMServer

ROOT = Path(__file__).parent.parent
JAR = (ROOT / "config" / "epubcheck.jar").resolve().as_posix()
_LANG_ROOT = (ROOT / "config" / "languages").as_posix()
_MATRIX = (ROOT / "config" / "language_matrix.yaml").as_posix()
FIXTURES = Path(__file__).parent / "fixtures" / "epub"
_COMPLEX = FIXTURES / "complex.epub"


@pytest.fixture
def mock_server() -> Iterator[MockLLMServer]:
    server = MockLLMServer()
    server.start()
    yield server
    server.stop()


def _m6_settings(
    tmp_path: Path,
    make_env,
    server: MockLLMServer,
    *,
    pipeline: str = "mt_pe",
    warmup_timeout_s: float = 900.0,
    mt_model: str = "mt-model",
    page_size: int = 50,
):
    """Настройки + БД + репозиторий: 5 профилей (mt/postedit → mock), реестр,
    промпты. `pipeline`/`warmup_timeout_s`/`mt_model`/`page_size` — параметры."""
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
        "model": mt_model,
        "api": "completions",
        "prompt_task": "mt_draft",
        "temperature": 0,
        "top_k": 1,
        "max_tokens_ratio": 3.0,
        "max_segment_tokens": 400,
        "retries": 3,
        "timeout_s": 120,
        "warmup_timeout_s": warmup_timeout_s,
    }

    def prof(name, fields):
        return "\n".join(
            [f"    {name}:"]
            + [f"      {k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fields.items()]
        )

    profiles = "\n".join(
        [
            prof("translation", {"base_url": base, "model": "t-model"}),
            prof("fill", {"base_url": base, "model": "f-model"}),
            prof("analysis", {"base_url": base, "model": "a-model"}),
            prof("mt", mt),
            prof(
                "postedit",
                {
                    "base_url": base,
                    "model": "pe-model",
                    "api": "chat",
                    "prompt_task": "postedit",
                    "temperature": 0,
                    "retries": 3,
                    "timeout_s": 300,
                    "warmup_timeout_s": 900.0,
                },
            ),
        ]
    )
    cfg = (
        f"storage:\n  root: {json.dumps(str(root))}\n"
        f"ui:\n  page_size: {page_size}\n"
        f"translation:\n  pipeline: {pipeline}\n"
        f"epubcheck:\n  jar_path: {json.dumps(JAR)}\n"
        f"languages:\n  root: {json.dumps(_LANG_ROOT)}\n  matrix: {json.dumps(_MATRIX)}\n"
        f"llm:\n  prompts_root: prompts\n  profiles:\n{profiles}\n"
    )
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg, encoding="utf-8")
    settings = load_settings(cfg_path, make_env(None))
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    return settings, db, JobRepository(db)


# --- M6-7: 6 стоп-причин → (reason, error_message) --------------------------------

def test_m6_7_stop_reason_format() -> None:
    """Код причины дословно + профиль + число и список segment_id (req 9 / M6-7)."""
    cases = [
        ("warmup_timeout", "mt"),
        ("model_mismatch", "mt"),
        ("pe_unrecoverable", "postedit"),
        ("pe_reject_ratio_exceeded", "postedit"),
        ("pe_overedit", "postedit"),
        ("settings_invalid", "mt"),
    ]
    for code, profile in cases:
        reason, message = _format_stop_reason(
            AppError(code, details={"profile": profile, "segment_ids": []})
        )
        assert message == f"{code}; профиль {profile}; сегментов 0", (code, message)
        assert reason == f"сбой этапа: {message}"

    # с segment_id — число и список дословно
    reason, message = _format_stop_reason(
        AppError(
            "pe_overedit",
            details={"profile": "postedit", "segment_ids": ["c001-s001", "c001-s002"]},
        )
    )
    assert message == "pe_overedit; профиль postedit; сегментов 2: c001-s001, c001-s002", message
    assert reason == f"сбой этапа: {message}"

    # не стоп-причина (без details.profile) — обычный сбой этапа, не меняется
    reason, message = _format_stop_reason(AppError("boom"))
    assert message == "boom"
    assert reason == "сбой этапа: boom"


# --- M6-8: создание задания с неполными / неподдерживаемым способом (422) ---------

def test_m6_8a_create_422_incomplete(tmp_path, make_env, mock_server) -> None:
    """Неполные LLM-настройки → SettingsIncompleteError (API 422) ДО проверки файла."""
    settings, _db, repo = _m6_settings(tmp_path, make_env, mock_server, mt_model="")
    with pytest.raises(SettingsIncompleteError):
        create_job(repo, settings, source_path=_COMPLEX, target_language="ru")


def test_m6_8b_create_422_unsupported_pair(tmp_path, make_env, mock_server, monkeypatch) -> None:
    """mt_pe для пары без mt_name → 422 pair_unsupported_mt_pe (не 400).

    В актуальном реестре у всех 6 языков есть mt_name, поэтому ветку провоцирует
    monkeypatch `mt_pe_supported` (импорт в services.jobs) → False."""
    settings, _db, repo = _m6_settings(tmp_path, make_env, mock_server)
    monkeypatch.setattr("app.services.jobs.mt_pe_supported", lambda *a, **k: False)
    with pytest.raises(SettingsIncompleteError) as exc:
        create_job(repo, settings, source_path=_COMPLEX, target_language="ru")
    assert "pair_unsupported_mt_pe" in str(exc.value)


# --- M6-4: отказ прогрева → warmup_timeout + профиль -----------------------------

def test_m6_4_warmup_timeout(tmp_path, make_env, mock_server) -> None:
    """Прогрев не отвечает за N с → LLMWarmupError(warmup_timeout, profile)."""
    settings, _db, _repo = _m6_settings(tmp_path, make_env, mock_server, warmup_timeout_s=1.0)
    mock_server.set_scenario("warmup_timeout")
    with pytest.raises(LLMWarmupError) as exc:
        warmup_profile(settings, "mt", source_lang="en", target_lang="ru")
    assert exc.value.message == "warmup_timeout"
    assert exc.value.details["profile"] == "mt"


# --- M6-9: страница постредактуры (данные представления) -------------------------

def test_m6_9_postedit_view(tmp_path, make_env, mock_server) -> None:
    """3 строки/сегмент (исходник/черновик/итог) + серверный diff + флаг + пагинация."""
    settings, _db, repo = _m6_settings(tmp_path, make_env, mock_server, page_size=2)
    job = Job(
        id="jobm69",
        status=JobStatus.TRANSLATING,
        source_path=str(_COMPLEX),
        target_language="ru",
        pipeline="mt_pe",
    )
    repo.create(job)
    m_dir = settings.job_dir("jobm69") / "m"
    m_dir.mkdir(parents=True, exist_ok=True)
    seg = [
        {"segment_id": "s1", "src_plain": "Hello world"},
        {"segment_id": "s2", "src_plain": "Goodbye"},
        {"segment_id": "s3", "src_plain": "See you"},
    ]
    fin = [
        {"segment_id": "s1", "text": "Привет <b>мир</b>", "draft": "Привет мир",
         "flags": ["markup"], "attempts": 1},
        {"segment_id": "s2", "text": "Пока", "draft": "Пока", "flags": [], "attempts": 1},
        {"segment_id": "s3", "text": "До встречи", "draft": "Давай свидимся",
         "flags": ["numbers_mismatch"], "attempts": 2},
    ]
    (m_dir / "segments.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in seg) + "\n", encoding="utf-8"
    )
    (m_dir / "final.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in fin) + "\n", encoding="utf-8"
    )

    data = views.postedit_view(settings, repo.require("jobm69"), page=1, flag=None)
    assert data["total_segments"] == 3
    assert data["flag_count"] == {"markup": 1, "numbers_mismatch": 1}
    assert [s["segment_id"] for s in data["segments"]] == ["s1", "s2"]  # page_size=2
    assert data["total_pages"] == 2
    # s1: черновик «Привет мир» → итог «Привет <b>мир</b>» — вставка разметки
    assert "<ins>" in data["segments"][0]["diff"]

    # фильтр по флагу
    filtered = views.postedit_view(
        settings, repo.require("jobm69"), page=1, flag="numbers_mismatch"
    )
    assert filtered["total_segments"] == 1
    assert filtered["segments"][0]["segment_id"] == "s3"

    # вторая страница
    page2 = views.postedit_view(settings, repo.require("jobm69"), page=2, flag=None)
    assert [s["segment_id"] for s in page2["segments"]] == ["s3"]


# --- M6-11: CLI «способ перевода» + фаза ----------------------------------------

def test_m6_11_cli_way_phase_line() -> None:
    """Строка статуса: single — только способ; mt_pe — способ + фаза + дробь §4.4."""
    assert _way_phase_line({"pipeline": "single", "phase": None, "phase_progress": None}) == (
        "способ перевода: single"
    )
    assert (
        _way_phase_line({"pipeline": "mt_pe", "phase": "m_draft", "phase_progress": 0.44})
        == "способ перевода: mt_pe · фаза: m_draft · 44%"
    )
    # m_assemble — не LLM-фаза, дробь не показывается
    assert (
        _way_phase_line({"pipeline": "mt_pe", "phase": "m_assemble", "phase_progress": None})
        == "способ перевода: mt_pe · фаза: m_assemble"
    )
    # завершено (phase None) — только способ
    assert (
        _way_phase_line({"pipeline": "mt_pe", "phase": None, "phase_progress": None})
        == "способ перевода: mt_pe"
    )


# --- M6-10 + M6-2: полный прогон mt_pe — фазы, прогресс §4.4, прогрев -----------

def test_m6_10_mt_pe_phases_progress_warmup(tmp_path, make_env, mock_server) -> None:
    """М6-10: фазы m_draft → m_postedit → m_assemble → NULL + прогресс §4.4.
    М6-2: первый запрос к mt/pe — прогрев (max_tokens==1 / ping)."""
    settings, _db, repo = _m6_settings(tmp_path, make_env, mock_server)
    job = Job(
        id="jobm610",
        status=JobStatus.CREATED,
        source_path=str(_COMPLEX),
        target_language="ru",
        mode="bilingual",
        pipeline="mt_pe",
        source_language="en",
    )
    repo.create(job)

    phases: list[str | None] = []
    progress: list[float] = []
    orig_phase, orig_progress = repo.set_phase, repo.set_progress

    def _spy_phase(job_id, phase):
        phases.append(phase)
        return orig_phase(job_id, phase)

    def _spy_progress(job_id, frac):
        progress.append(frac)
        return orig_progress(job_id, frac)

    repo.set_phase = _spy_phase  # type: ignore[method-assign]
    repo.set_progress = _spy_progress  # type: ignore[method-assign]

    Pipeline(settings).run_translating(repo, job, lambda: None)

    # M6-10: последовательность фаз + сброс
    assert phases == ["m_draft", "m_postedit", "m_assemble", None], phases
    # M6-10: прогресс §4.4 — после черновиков 0.5, в конце 1.0
    assert any(abs(p - 0.5) < 1e-9 for p in progress), progress
    assert abs(progress[-1] - 1.0) < 1e-9, progress[-1:]
    assert max(progress) <= 1.0 + 1e-9

    # M6-2: прогрев ДО первой рабочей операции (журнал mock)
    log = mock_server.requests_log()
    mt_reqs = [r for r in log if r.get("model") == "mt-model"]
    pe_reqs = [r for r in log if r.get("model") == "pe-model"]
    assert mt_reqs, "нет запросов к mt-model"
    assert mt_reqs[0]["kind"] == "mt_warmup", mt_reqs[0]
    assert any(r.get("kind") == "mt" for r in mt_reqs[1:]), "нет рабочих mt-запросов"
    assert pe_reqs, "нет запросов к pe-model"
    # прогрев постредактуры — текст «warmup» (PostEditPhase, M4), не «ping»
    assert pe_reqs[0].get("user") == "warmup", pe_reqs[0]
    assert any(r.get("user") != "warmup" for r in pe_reqs[1:]), "нет рабочих pe-запросов"
