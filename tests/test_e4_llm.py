"""Тесты E4: LLM-профили, внешние промпты, клиент анализа, токены, стоимость.

- T-E4-1 — поиск хардкода: URL/модель/ключ в коде — 0 попаданий (E4-1).
- T-E4-2 — разрешение промпта (файл + версия) записано в задание — 100% задач (E4-2).
- T-E4-3 — pair_required без парного промпта → ошибка, 0 тихого generic (E4-3).
- T-E4-4 — невалидный JSON (mock) → ошибка задачи, пустой артефакт не создан (E4-4).
- T-E4-5 — сумма токенов по профилям == сумма по заданию, дискрепансия 0 (E4-5).
- T-E4-6 — цены не заданы → «Cost: unknown» в отчёте, 100% профилей (E4-6).
- T-E4-7 — все 6 сценариев mock ведут себя предсказуемо.
- T-E4-8 — смена модели analysis-профиля не влияет на translation-профиль.
Дополнительно: E4.3 (показ профилей), порядок/версии разрешения промптов.
"""

from __future__ import annotations

import re
import shutil
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

import pytest

from app.core.config import Settings, load_settings
from app.llm.analysis import AnalysisClient, AnalysisTaskError
from app.llm.cost import format_cost, job_cost_summary
from app.llm.profiles import describe_profiles
from app.llm.prompts import LEVEL_GENERIC, LEVEL_PAIR, PromptError, PromptResolver
from app.llm.tasks import all_tasks, task
from app.services.jobs import create_job
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.workers.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "epub"
_COMPLEX = FIXTURES / "complex.epub"
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from mock_llm_server import MockLLMServer  # noqa: E402

#: Адрес по умолчанию для YAML-профилей, когда mock-сервер не нужен (тестовая фикстура).
DEFAULT_LLM_BASE_URL = "http://127.0.0.1:9999/v1"


@pytest.fixture()
def mock_server() -> Iterator[MockLLMServer]:
    server = MockLLMServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


# ------------------------------------------------------------------ служебное


def _settings_for(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
    server: MockLLMServer | None = None,
    profile_overrides: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[Settings, Database, JobRepository]:
    """Настройки + БД + репозиторий на изолированном storage.root (E4.1)."""
    cfg = make_yaml(
        str(tmp_path / "storage"),
        "",
        base_url=server.url if server is not None else DEFAULT_LLM_BASE_URL,
        profile_overrides=profile_overrides,
    )
    settings = load_settings(cfg, make_env(None))
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    return settings, db, JobRepository(db)


def _copy_prompts(settings: Settings) -> Path:
    """Промпты — данные (prompts/ в storage.root); копируем репозиторные в тестовый root."""
    target = settings.prompts_dir
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(ROOT / "prompts", target)
    return target


def _prompt_yaml(system: str = "S") -> str:
    return f"system: {system}\nuser_template: |\n  Text: {{text}}\n"


# ------------------------------------------------------------------ T-E4-1

#: URL: исключены стандартные XML-неймспейсы EPUB (idpf.org, w3.org, purl.org) —
#: это константы протокола, а не адреса LLM-эндпоинтов.
_FORBIDDEN_PATTERNS = [
    (
        re.compile(r"https?://(?!www\.idpf\.org|www\.w3\.org|purl\.org)\S+"),
        "URL",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "API-ключ"),
    (
        re.compile(r"\b(probe-model|test-model|example-model|analysis-model|gpt-4|claude)\b"),
        "имя модели",
    ),
]


def test_e4_1_no_hardcoded_llm_settings_in_app() -> None:
    # E4-1: grep URL/имён моделей/ключей вне config/ и тестовых фикстур — 0 в коде.
    hits: list[str] = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            for pattern, what in _FORBIDDEN_PATTERNS:
                if pattern.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{line_no} [{what}]: {line.strip()}")
    assert hits == [], "\n".join(hits)


# ------------------------------------------------------------------ T-E4-2


def test_e4_2_resolution_recorded_for_all_tasks(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
    mock_server: MockLLMServer,
) -> None:
    # E4-2: фактически применённый промпт (файл + версия) записан в задание — 100% задач.
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, mock_server)
    _copy_prompts(settings)
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        client = AnalysisClient(settings, repo, job.id)
        text = "The quick brown fox jumps over the lazy dog."
        for spec in all_tasks():
            client.run_task(spec, "en", "de", text)
        recorded = {r.task: r for r in repo.list_resolved_prompts(job.id)}
        expected = {spec.name for spec in all_tasks()}
        assert set(recorded) == expected, "не все задачи записали разрешённый промпт"
        for record in recorded.values():
            assert record.file
            assert record.version >= 1
        assert recorded["analysis_vocabulary"].level == LEVEL_GENERIC
        assert recorded["analysis_grammar"].level == LEVEL_GENERIC
        assert recorded["analysis_glossary"].level == LEVEL_PAIR
        assert recorded["analysis_glossary"].file == "analysis_glossary/en/de/v1.yaml"
        for spec in all_tasks():
            artifact = settings.jobs_dir / job.id / "analysis" / f"{spec.name}.json"
            assert artifact.is_file(), f"нет артефакта валидного результата: {spec.name}"
    finally:
        db.close()


def test_resolver_level_and_version_selection(tmp_path: Path) -> None:
    # Разрешение: пара → язык → generic; версия = наибольший N.
    root = tmp_path / "prompts"
    for directory in ("t/en/de", "t/en", "t"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "t" / "v2.yaml").write_text(_prompt_yaml("generic"), encoding="utf-8")
    (root / "t" / "en" / "v1.yaml").write_text(_prompt_yaml("source"), encoding="utf-8")
    (root / "t" / "en" / "de" / "v1.yaml").write_text(_prompt_yaml("pair1"), encoding="utf-8")
    (root / "t" / "en" / "de" / "v3.yaml").write_text(_prompt_yaml("pair3"), encoding="utf-8")
    resolver = PromptResolver(root)
    pair = resolver.resolve("t", "en", "de", pair_required=False)
    assert (pair.file, pair.version, pair.level) == ("t/en/de/v3.yaml", 3, "pair")
    source = resolver.resolve("t", "en", "fr", pair_required=False)
    assert (source.file, source.version, source.level) == ("t/en/v1.yaml", 1, "source")
    generic = resolver.resolve("t", "ru", "de", pair_required=False)
    assert (generic.file, generic.version, generic.level) == ("t/v2.yaml", 2, "generic")
    with pytest.raises(PromptError):
        resolver.resolve("no_such_task", "en", "de", pair_required=False)


# ------------------------------------------------------------------ T-E4-3


def test_e4_3_pair_required_without_pair_is_error(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
    mock_server: MockLLMServer,
) -> None:
    # E4-3: pair_required-задача без парного промпта → ошибка, 0 тихого generic.
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, mock_server)
    _copy_prompts(settings)
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        spec = task("analysis_glossary")
        assert spec.pair_required
        # generic-промпт существует, но тихо использоваться не имеет права
        assert (settings.prompts_dir / "analysis_glossary" / "v1.yaml").is_file()
        client = AnalysisClient(settings, repo, job.id, attempts=1)
        with pytest.raises(PromptError, match="pair_required"):
            client.run_task(spec, "fr", "de", "Le renard brun saute.")
        recorded = [
            r for r in repo.list_resolved_prompts(job.id) if r.task == "analysis_glossary"
        ]
        assert recorded == [], "запись разрешённого промпта — значит, был фолбэк"
        assert not (settings.jobs_dir / job.id / "raw").exists(), "были LLM-запросы"
    finally:
        db.close()


# ------------------------------------------------------------------ T-E4-4


def test_e4_4_invalid_json_task_error_no_artifact(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
    mock_server: MockLLMServer,
) -> None:
    # E4-4: невалидный JSON (mock bad_json) → ошибка задачи, пустой артефакт не создан.
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, mock_server)
    _copy_prompts(settings)
    mock_server.set_scenario("bad_json")
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        client = AnalysisClient(settings, repo, job.id, attempts=2)
        with pytest.raises(AnalysisTaskError, match="analysis_vocabulary"):
            client.run_task(task("analysis_vocabulary"), "en", "de", "The fox jumps.")
        job_dir = settings.jobs_dir / job.id
        assert not (job_dir / "analysis" / "analysis_vocabulary.json").exists()
        raw = sorted(p.name for p in (job_dir / "raw").iterdir())
        assert raw == [
            "analysis_vocabulary_attempt_1.json",
            "analysis_vocabulary_attempt_2.json",
        ], "сырые ответы всех попыток должны быть сохранены"
        final = repo.require(job.id)
        assert final.tokens_analysis > 0, "токены неудачных попыток не должны теряться"
    finally:
        mock_server.set_scenario("ok")
        db.close()


# ------------------------------------------------------------------ T-E4-5


def test_e4_5_profile_sums_equal_job_total(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
    mock_server: MockLLMServer,
) -> None:
    # E4-5: сумма токенов по профилям == сумма по заданию — дискрепансия 0.
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, mock_server)
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        pipeline = Pipeline(settings)
        job_now = repo.require(job.id)
        pipeline.run_validating(repo, job_now)
        pipeline.run_planning(repo, job_now)
        pipeline.run_translating(repo, job_now, lambda: None)
        final = repo.require(job.id)
        profile_sum = final.tokens_translation + final.tokens_fill + final.tokens_analysis
        assert final.tokens_total == profile_sum, (
            f"дискрепансия: total={final.tokens_total} сумма_профилей={profile_sum}"
        )
        assert final.tokens_translation + final.tokens_fill > 0
        # E4.7: разбивка — для каждого профиля input + output == сумма профиля
        for name, total in (
            ("translation", final.tokens_translation),
            ("fill", final.tokens_fill),
        ):
            row = repo.profile_tokens(job.id)[name]
            assert row.input_tokens + row.output_tokens == total
    finally:
        db.close()


# ------------------------------------------------------------------ T-E4-6


def test_e4_6_cost_unknown_without_prices(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
) -> None:
    # E4-6: цены не заданы в конфигурации → «Cost: unknown» в отчёте — 100% профилей.
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml)
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        repo.add_tokens(job.id, "translation", 1234)
        summary = job_cost_summary(settings, repo, repo.require(job.id))
        lines = [line for line in summary.splitlines() if line.startswith("cost: ")]
        assert len(lines) == 3, "в отчёте должны быть все три профиля"
        assert all("Cost: unknown" in line for line in lines)
        # с заданными ценами — стоимость показывается численно (выдумка запрещена)
        profile = settings.llm.profile("translation")
        assert not profile.cost_defined
        assert format_cost(profile, 1000, 1000) == "Cost: unknown"
        priced = profile.model_copy(
            update={"input_price_per_mtok": 2.0, "output_price_per_mtok": 8.0}
        )
        assert format_cost(priced, 1_000_000, 1_000_000) == (
            "Cost: 10.0000 USD (input 2.00/Mtok, output 8.00/Mtok)"
        )
    finally:
        db.close()


# ------------------------------------------------------------------ T-E4-7


@pytest.mark.parametrize(
    ("scenario", "expect_ok"),
    [
        ("ok", True),
        ("timeout", False),
        ("http500", False),
        ("empty", False),
        ("bad_json", False),
        ("refuse", False),
    ],
)
def test_e4_7_mock_scenarios_predictable(
    scenario: str,
    expect_ok: bool,
    *,
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
    mock_server: MockLLMServer,
) -> None:
    # T-E4-7: все 6 сценариев mock (E4.6) предсказуемы для клиента анализа.
    # timeout: таймаут клиента (1 с) короче сна сервера (30 с) → детерминированный ReadTimeout.
    overrides = {"analysis": {"timeout_seconds": 1.0}}
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, mock_server, overrides)
    _copy_prompts(settings)
    mock_server.set_scenario(scenario)
    try:
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        client = AnalysisClient(settings, repo, job.id, attempts=1)
        if expect_ok:
            result = client.run_task(task("analysis_vocabulary"), "en", "de", "The fox jumps.")
            assert result.payload == {"items": []}
            assert result.attempts == 1
        else:
            with pytest.raises(AnalysisTaskError):
                client.run_task(task("analysis_vocabulary"), "en", "de", "The fox jumps.")
        raw = sorted(p.name for p in (settings.jobs_dir / job.id / "raw").iterdir())
        assert raw == ["analysis_vocabulary_attempt_1.json"], "сырой ответ сохранён"
    finally:
        mock_server.set_scenario("ok")
        db.close()


# ------------------------------------------------------------------ T-E4-8


def test_e4_8_analysis_model_change_does_not_affect_translation(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
    mock_server: MockLLMServer,
) -> None:
    # T-E4-8: другая модель у analysis-профиля не влияет на translation-профиль.
    overrides = {
        "analysis": {"base_url": "http://127.0.0.1:1/v1", "model": "analysis-other-model"}
    }
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml, mock_server, overrides)
    try:
        assert settings.llm.endpoint("analysis")[1] == "analysis-other-model"
        job = create_job(repo, settings, source_path=_COMPLEX, target_language="de")
        pipeline = Pipeline(settings)
        job_now = repo.require(job.id)
        pipeline.run_validating(repo, job_now)
        pipeline.run_planning(repo, job_now)
        pipeline.run_translating(repo, job_now, lambda: None)
        final = repo.require(job.id)
        assert final.tokens_translation + final.tokens_fill > 0
        requests = mock_server.requests_log()
        assert requests, "перевод не породил LLM-запросов"
        assert all(r["model"] == "test-model" for r in requests), (
            "translation-запросы пошли не в translation-модель"
        )
    finally:
        db.close()


# ------------------------------------------------------------------ E4.3 (доп.)


def test_describe_profiles_shows_all_three_profiles(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
) -> None:
    # E4.3: перед стартом задания CLI показывает провайдер/эндпоинт/модель по профилям.
    settings, db, _ = _settings_for(tmp_path, make_env, make_yaml)
    try:
        text = describe_profiles(settings)
        for name in ("translation", "fill", "analysis"):
            assert f"profile: {name}" in text
        assert text.count("endpoint: http://127.0.0.1:9999/v1") == 3
        assert text.count("model: test-model") == 3
        assert text.count("provider: openai-compatible") == 3
        assert "api_key: задан" in text
    finally:
        db.close()
