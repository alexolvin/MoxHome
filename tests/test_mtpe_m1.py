# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""MTPE M1: единая проверка настроек LLM (TZ §5.3), миграция jobs, снимок (§4.5).

Покрытие критериев M1:
- T-M1-2: матрица отказов ``validate_llm_settings`` (V1, V2, V3, V4, V5, V7–V10);
- M1-10: статусы ``mt_pe`` пар матрицы совпадают с ``pack.yaml``;
- M1-7:  задание работает на значениях создания (снимок);
- M1-1:  точки входа вызывают ``validate_llm_settings`` (шпион);
- M1-8:  ``POST /api/jobs`` с ``pipeline`` — 400 ``pipeline_in_settings_only``;
- M1-3:  сохранение с проблемами — файлы не изменены (sha256).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from app.core.config import ALL_PROFILE_NAMES, LLMProfile, Settings, load_settings
from app.core.errors import AppError, InputValidationError, SettingsIncompleteError
from app.core.llm_settings import (
    apply_job_config_snapshot,
    build_job_config_snapshot,
    mt_draft_template_issues,
    validate_llm_settings,
)
from app.domain.job import Job, JobStatus
from app.languages.registry import load_registry
from app.languages.resolver import load_matrix, mt_pe_supported
from app.services import jobs as _jobs
from app.services.llm_config import update_llm_config
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.web.app import create_app
from app.web.auth import csrf_token
from app.workers.worker import Worker

# ------------------------------------------------------------------ помощники


def _write_full_config(
    tmp_path: Path,
    *,
    pipeline: str = "single",
    profiles: dict[str, dict[str, Any]] | None = None,
) -> tuple[Path, Path]:
    """config.yaml со ВСЕМИ пятью профилями (T-M1-2: mt_pe проверяется) +
    реальные languages/prompts. Возврат (config.yaml, .env)."""
    profiles = profiles or {}
    default = {"base_url": "http://127.0.0.1:9999/v1", "model": "test-model"}
    llm_profiles: dict[str, dict[str, Any]] = {}
    for name in ALL_PROFILE_NAMES:
        prof = dict(default)
        if name == "mt":
            prof["api"] = "completions"
        prof.update(profiles.get(name, {}))
        llm_profiles[name] = prof
    repo = Path(__file__).resolve().parents[1]
    data = {
        "storage": {"root": str(tmp_path / "data")},
        "server": {"bind_address": "127.0.0.1:8321"},
        "translation": {"pipeline": pipeline},
        "llm": {"profiles": llm_profiles},
        "languages": {
            "root": str(repo / "config" / "languages"),
            "matrix": str(repo / "config" / "language_matrix.yaml"),
        },
    }
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text(
        "SERVER_BIND_ADDRESS=127.0.0.1:8321\n"
        "LLM_API_KEY=test-key-value-000\n"
        "SERVER_AUTH_SECRET=mtpe-auth-secret-000\n",
        encoding="utf-8",
    )
    return cfg, env


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    cfg, env = _write_full_config(tmp_path, **kw)
    return load_settings(cfg, env)


def _set_profile(settings: Settings, name: str, **kw: Any) -> Settings:
    """model_copy-подмена одного профиля (frozen-модель)."""
    prof = settings.llm.profiles[name].model_copy(update=kw)
    llm = settings.llm.model_copy(update={"profiles": {**settings.llm.profiles, name: prof}})
    return settings.model_copy(update={"llm": llm})


def _codes(issues: list) -> list[str]:
    return sorted(i.code for i in issues)


def _v9_bad_extra_body(s: Settings) -> Settings:
    """V9: extra_body не-dict (model_construct — обход схемы, валидация не
    поднимет)."""
    bad = LLMProfile.model_construct(
        base_url="http://127.0.0.1:9999/v1", model="m", extra_body=[1, 2]
    )
    llm = s.llm.model_copy(update={"profiles": {**s.llm.profiles, "translation": bad}})
    return s.model_copy(update={"llm": llm})


# ------------------------------------------------------------------ T-M1-2: матрица


@pytest.mark.parametrize(
    ("pipeline", "mutate", "expected"),
    [
        # V1: способ неизвестен — единственный код, остальные не проверяются.
        (
            "bogus",
            lambda s: s.model_copy(
                update={"translation": s.translation.model_copy(update={"pipeline": "bogus"})}
            ),
            ["validate_pipeline_unknown"],
        ),
        # V2: пустой base_url обязательного профиля.
        (
            "single",
            lambda s: _set_profile(s, "translation", base_url=""),
            ["validate_base_url_empty"],
        ),
        (
            "mt_pe",
            lambda s: _set_profile(s, "mt", base_url="", model=""),
            ["validate_base_url_empty", "validate_model_empty"],
        ),
        # V3: неверный формат эндпоинта (путь не /v1, схема ftp).
        (
            "single",
            lambda s: _set_profile(s, "translation", base_url="http://127.0.0.1:9999/v2"),
            ["validate_base_url_format"],
        ),
        (
            "single",
            lambda s: _set_profile(s, "fill", base_url="ftp://x/v1"),
            ["validate_base_url_format"],
        ),
        # V4: пустая модель / модель с пробелом.
        (
            "single",
            lambda s: _set_profile(s, "analysis", model=""),
            ["validate_model_empty"],
        ),
        (
            "single",
            lambda s: _set_profile(s, "translation", model="a b"),
            ["validate_model_empty"],
        ),
        # V5: api mt != completions.
        (
            "mt_pe",
            lambda s: _set_profile(s, "mt", api="chat"),
            ["validate_api_fixed"],
        ),
        # V8: числовые поля вне границ (top_k=0 < 1).
        (
            "mt_pe",
            lambda s: _set_profile(s, "mt", top_k=0),
            ["validate_out_of_limits"],
        ),
        # V9: extra_body не JSON-объект (model_construct — обход схемы).
        ("single", _v9_bad_extra_body, ["validate_extra_body_invalid"]),
    ],
)
def test_t_m1_2_rejection_matrix(
    tmp_path: Path, pipeline: str, mutate: Any, expected: list[str]
) -> None:
    s = _settings(tmp_path, pipeline=pipeline)
    issues = validate_llm_settings(mutate(s))
    # Ожидаемые коды присутствуют; пустой список — только для валидного конфига.
    assert set(expected) <= set(_codes(issues)), (expected, _codes(issues))


def test_t_m1_2_valid_single_and_mt_pe_no_issues(tmp_path: Path) -> None:
    """Полный конфиг: single и mt_pe (все профили заполнены, все пакеты с
    mt_name) — ноль проблем (V10: есть supported-пары)."""
    assert validate_llm_settings(_settings(tmp_path, pipeline="single")) == []
    assert validate_llm_settings(_settings(tmp_path, pipeline="mt_pe")) == []


def test_t_m1_2_v7_mt_template_placeholders() -> None:
    """V7: шаблон mt_draft — ровно {src_name},{tgt_name},{text}, без других."""
    good = "Translate this from {src_name} to {tgt_name}:\n{src_name}: {text}\n{tgt_name}:\n"
    assert mt_draft_template_issues(good) == []
    # лишний плейсхолдер → код
    assert [i.code for i in mt_draft_template_issues(good + "{extra}\n")] == [
        "validate_mt_template_placeholders"
    ]
    # не те плейсхолдеры → код
    assert mt_draft_template_issues("x {src} {tgt} {body}\n") != []


# ------------------------------------------------------------------ M1-10: резолвер


def test_m1_10_mt_pe_statuses_match_pack_yaml(tmp_path: Path) -> None:
    """Статус mt_pe пары = supported, только когда mt_name задан у обоих языков."""
    repo = Path(__file__).resolve().parents[1]
    registry = load_registry(repo / "config" / "languages")
    matrix = load_matrix(repo / "config" / "language_matrix.yaml")
    # Все пакеты репозитория имеют mt_name → каждая пара матрицы supported.
    for source, target in matrix:
        src, tgt = registry.get(source), registry.get(target)
        if src is None or tgt is None:
            continue
        expected = bool(src.mt_name and tgt.mt_name)
        assert mt_pe_supported(registry, source, target) is expected
    # Противоположный случай: у целевого нет mt_name → unsupported.
    stripped = dict(registry)
    code = next(iter(stripped))
    stripped[code] = stripped[code].model_copy(update={"mt_name": None})
    other = next((c for c in stripped if c != code and stripped[c].mt_name), None)
    if other is not None:
        assert mt_pe_supported(stripped, code, other) is False


# ------------------------------------------------------------------ M1-7: снимок


def test_m1_7_job_works_on_creation_values(tmp_path: Path) -> None:
    """Снимок фиксирует модель создания; после смены конфига задание работает
    на значениях создания (M1-7: «журнал mock с моделью каждого запроса»)."""
    s = _settings(tmp_path, pipeline="single")
    snap = build_job_config_snapshot(s)
    data = json.loads(snap)
    assert data["pipeline"] == "single"
    assert "api_key" not in json.dumps(data), "ключи в снимок не попадают"
    # Смена модели в «текущем» конфиге...
    changed = _set_profile(s, "translation", model="NEW-MODEL")
    # ...задание берёт модель из снимка (значения создания), а не новую.
    restored = apply_job_config_snapshot(changed, snap)
    assert restored.llm.profiles["translation"].model == "test-model"
    # Ключи — из .env (LLMSection не переопределяется снимком).
    assert restored.llm.api_key == changed.llm.api_key
    # Пустой снимок (старое задание) — текущий конфиг без изменений.
    assert apply_job_config_snapshot(changed, "") is changed


def test_m1_7_snapshot_survives_db_roundtrip(tmp_path: Path) -> None:
    """Снимок пишется в jobs и читается из _row_to_job без потерь."""
    s = _settings(tmp_path, pipeline="single")
    snap = build_job_config_snapshot(s)
    db = Database(tmp_path / "jobs.db", 30000)
    repo = JobRepository(db)
    job = Job(
        id="j-m1",
        status=JobStatus.CREATED,
        source_path="/x",
        target_language="de",
        pipeline=s.translation.pipeline,
        config_snapshot=snap,
        created_at=1.0,
        updated_at=1.0,
    )
    repo.create(job)
    got = repo.get("j-m1")
    assert got is not None and got.config_snapshot == snap
    assert (
        apply_job_config_snapshot(s, got.config_snapshot).llm.profiles["translation"].model
        == s.llm.profiles["translation"].model
    )


# ------------------------------------------------------------------ M1-1: шпион


def test_m1_1_create_job_calls_validate(tmp_path: Path, monkeypatch: Any) -> None:
    """Точка входа «создание задания» вызывает validate_llm_settings (M1-1)."""
    calls: list[Any] = []
    orig = _jobs.validate_llm_settings

    def spy(settings: Settings) -> list:
        calls.append(settings)
        return orig(settings)

    monkeypatch.setattr(_jobs, "validate_llm_settings", spy)
    # Валидный конфиг → задание создаётся, шпион вызван один раз.
    s = _settings(tmp_path, pipeline="single")
    assert len(calls) == 0  # до вызова
    # create_job требует существующий EPUB-файл; валидность проверена до файла.
    with pytest.raises(InputValidationError):
        _jobs.create_job(
            None, s, source_path=Path("/nonexistent.epub"), target_language="de"
        )
    assert len(calls) == 1, "create_job должен вызвать validate_llm_settings"


def test_m1_1_create_job_rejects_incomplete(tmp_path: Path) -> None:
    """Неполные настройки → create_job отказывает (SettingsIncompleteError)."""
    # mt_pe с пустым mt-профилем → неполные → отказ ДО создания.
    s = _settings(tmp_path, pipeline="mt_pe")
    s = _set_profile(s, "mt", base_url="", model="")
    with pytest.raises(SettingsIncompleteError) as excinfo:
        _jobs.create_job(
            None, s, source_path=Path("/nonexistent.epub"), target_language="de"
        )
    codes = [c for _p, c in (excinfo.value.details or {}).get("issues", [])]
    assert "validate_base_url_empty" in codes


# ------------------------------------------------------------------ M1-3/M1-8: API


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_m1_3_save_with_problems_files_unchanged(tmp_path: Path) -> None:
    """M1-3: сохранение при неполных настройках (mt_pe без mt/postedit) —
    config.yaml и .env не изменились (sha256), отказ SettingsIncompleteError.
    Чат-профили в теле валидны (проходят _profile_changes); проблема — в
    validate_llm_settings (mt_pe требует mt/postedit)."""
    cfg, env = _write_full_config(
        tmp_path,
        pipeline="mt_pe",
        profiles={
            "mt": {"base_url": "", "model": ""},
            "postedit": {"base_url": "", "model": ""},
        },
    )
    before_cfg, before_env = _sha(cfg), _sha(env)
    st = load_settings(cfg, env)
    url = st.llm.profile("translation").base_url
    profile_fields = {
        name: {
            "base_url": url,
            "model": st.llm.profile(name).model,
            "api_key": "",
            "extra_body": "",
            "temperature": st.llm.profile(name).temperature,
            "retry_times": st.llm.profile(name).retry_times,
            "concurrency": st.llm.profile(name).concurrency,
        }
        for name in ("translation", "fill", "analysis")
    }
    with pytest.raises(SettingsIncompleteError):
        update_llm_config(
            pipeline="mt_pe",
            profile_fields=profile_fields,
            config_path=cfg,
            env_path=env,
        )
    assert _sha(cfg) == before_cfg, "config.yaml изменён при отклонённом сохранении"
    assert _sha(env) == before_env, ".env изменён при отклонённом сохранении"


def test_m1_8_post_jobs_pipeline_rejected(u1_env: Any) -> None:
    """M1-8: POST /api/jobs с полем pipeline в теле — 400 pipeline_in_settings_only."""
    client = u1_env.profiles["main"].client
    resp = client.post(
        "/api/jobs",
        json={"book": "x.epub", "target_language": "de", "pipeline": "mt_pe"},
    )
    assert resp.status_code == 400, resp.text
    assert "pipeline_in_settings_only" in resp.text or "pipeline" in resp.text


# ------------------------------------------------------------------ M1-5: атомарность


def test_m1_5_config_write_atomic(tmp_path: Path, monkeypatch: Any) -> None:
    """M1-5: сбой os.replace в момент записи — файл целиком старый (не «половина
    нового»), .partial-временные убраны. Механизм: .partial → os.replace."""
    cfg, env = _write_full_config(tmp_path, pipeline="single")
    cfg_before, env_before = _sha(cfg), _sha(env)
    calls: list[str] = []

    def _boom(src, dst, *a: Any, **k: Any) -> None:
        calls.append(dst)
        raise OSError("simulated kill -9 at os.replace")

    monkeypatch.setattr(os, "replace", _boom)
    st = load_settings(cfg, env)
    url = st.llm.profile("translation").base_url
    profile_fields = {
        name: {
            "base_url": url,
            "model": st.llm.profile(name).model,
            "api_key": "",
            "extra_body": "",
            "temperature": st.llm.profile(name).temperature,
            "retry_times": st.llm.profile(name).retry_times,
            "concurrency": st.llm.profile(name).concurrency,
        }
        for name in ("translation", "fill", "analysis")
    }
    with pytest.raises(AppError):
        update_llm_config(
            pipeline="single",
            profile_fields=profile_fields,
            config_path=cfg,
            env_path=env,
        )
    # Оригиналы — побайтово старая версия (целиком старый, не «половина нового»).
    assert _sha(cfg) == cfg_before, "config.yaml повреждён при сбое os.replace"
    assert _sha(env) == env_before, ".env повреждён при сбое os.replace"
    # .partial-временные убраны (finally) — следов не осталось.
    assert not list(cfg.parent.glob("*.partial")), "остался .partial-артефакт"
    assert calls, "os.replace не вызывался — запись не атомарная"


# ------------------------------------------------------------------ M1-6: неполный конфиг


def _incomplete_mt_pe(tmp_path: Path) -> Settings:
    """mt_pe с пустыми mt/postedit — неполные (V2/V4)."""
    return _settings(
        tmp_path,
        pipeline="mt_pe",
        profiles={
            "mt": {"base_url": "", "model": ""},
            "postedit": {"base_url": "", "model": ""},
        },
    )


def _basic_header(settings: Settings, username: str = "admin") -> dict[str, str]:
    secret = settings.server.auth_secret
    token = base64.b64encode(f"{username}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_m1_6_web_incomplete_200_banner_422(tmp_path: Path) -> None:
    """M1-6: старт с неполным конфигом — веб отвечает 200 с полосой «Настройки
    LLM неполны»; POST /api/jobs — 422 (Basic-auth + CSRF)."""
    s = _incomplete_mt_pe(tmp_path)
    app = create_app(s)
    client = TestClient(app)
    headers = _basic_header(s)
    page = client.get("/settings/llm", headers=headers)
    assert page.status_code == 200, page.text
    # Полоса «Настройки LLM неполны» (role=alert, класс u3-warn).
    assert 'role="alert"' in page.text
    assert "Настройки LLM неполны" in page.text
    # Создание задания отклонено (422 с кодами §5.3); мутация — с CSRF-токеном.
    csrf = csrf_token(s, "admin")
    resp = client.post(
        "/api/jobs",
        json={"book": "x.epub", "target_language": "de", "csrf": csrf},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


def test_m1_6_worker_incomplete_takes_zero(tmp_path: Path) -> None:
    """M1-6: воркер с неполными настройками за 3 полла не берёт задания."""
    s = _incomplete_mt_pe(tmp_path)
    db = Database(tmp_path / "jobs.db", 30000)
    repo = JobRepository(db)
    job = Job(
        id="j-m6",
        status=JobStatus.CREATED,
        source_path="/x",
        target_language="de",
        created_at=1.0,
        updated_at=1.0,
    )
    repo.create(job)
    # config_path=None → без релоада: воркер работает на переданных настройках.
    worker = Worker(s, repo)
    for _ in range(3):
        assert worker.run_once() is None
    got = repo.get("j-m6")
    assert got is not None and got.status == JobStatus.CREATED, (
        "воркер взял задание при неполных настройках"
    )
    assert worker._settings_incomplete() is True
