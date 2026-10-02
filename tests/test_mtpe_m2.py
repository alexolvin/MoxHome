# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""MTPE M2: экран «LLM и модели» (TZ §6) — способ, проба, бюджеты.

Pytest-часть критериев M2 (Playwright-часть — CONTROL, живой стенд):
- M2-1: mt_pe с пустыми mt/postedit — ``SettingsIncompleteError`` (→ 422),
  ``sha256`` файлов не изменился;
- M2-4: проба — успех mt, ``model_not_listed``, 401, timeout (4 точные строки);
- M2-5: проба другой модели на том же ``base_url`` во время фазы — 409;
- M2-6: один ``base_url`` не пишется в несколько профилей (grep + разные URL);
- M2-8: бюджеты CSS/JS, цвета только из ``:root`` (grep css-settings.css).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

import app
from app.core.config import ALL_PROFILE_NAMES
from app.core.errors import SettingsIncompleteError
from app.domain.job import Job, JobStatus
from app.services import llm_config
from app.services.llm_config import probe_conflict, probe_profile, update_llm_config
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.web.strings import UI_STRINGS

_REPO = Path(__file__).resolve().parents[1]
_STATIC = Path(app.__file__).parent / "web" / "static"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_cfg(
    tmp_path: Path, *, pipeline: str = "single", empty: tuple[str, ...] = ()
) -> tuple[Path, Path]:
    """config.yaml со ВСЕМИ 5 профилями (``empty`` — профили с пустыми
    base_url/model) + реальные languages. Возврат (config.yaml, .env)."""
    profiles: dict[str, dict[str, Any]] = {}
    for name in ALL_PROFILE_NAMES:
        prof: dict[str, Any]
        if name in empty:
            prof = {"base_url": "", "model": ""}
        else:
            prof = {"base_url": "http://127.0.0.1:9999/v1", "model": "m"}
        if name == "mt":
            prof["api"] = "completions"
            prof["prompt_task"] = "mt_draft"
        profiles[name] = prof
    data = {
        "storage": {"root": str(tmp_path / "data")},
        "server": {"bind_address": "127.0.0.1:8321"},
        "translation": {"pipeline": pipeline},
        "llm": {"profiles": profiles},
        "languages": {
            "root": str(_REPO / "config" / "languages"),
            "matrix": str(_REPO / "config" / "language_matrix.yaml"),
        },
    }
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text(
        "SERVER_BIND_ADDRESS=127.0.0.1:8321\n"
        "LLM_API_KEY=test-key-m2\n"
        "SERVER_AUTH_SECRET=mtpe-auth-secret-m2\n",
        encoding="utf-8",
    )
    return cfg, env


def _mt_data(base_url: str, model: str) -> dict[str, Any]:
    """Тело пробы mt (3-я ступень §6.5): prompt = активный mt_draft."""
    return {
        "base_url": base_url,
        "model": model,
        "api_key": "",
        "temperature": 0.2,
        "extra_body": {},
        "api": "completions",
        "top_k": None,
        "stop": [],
        "prompt": "Translate this from English to Russian:\nEnglish: Good morning.\nRussian:",
    }


class _MockLLM:
    """Мок-эндпоинт пробы (M2-4): GET /v1/models + POST /v1/{chat/}completions."""

    def __init__(
        self,
        *,
        models: list[str] | None = None,
        post_body: dict[str, Any] | None = None,
        post_status: int = 200,
        models_status: int = 200,
        sleep: float = 0.0,
    ) -> None:
        cfg = {
            "models": models or [],
            "post_body": post_body or {},
            "post_status": post_status,
            "models_status": models_status,
            "sleep": sleep,
        }

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # тишина в логах pytest
                pass

            def _w(self, status: int, body: Any, ct: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("Content-Type", ct)
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                if cfg["sleep"]:
                    time.sleep(cfg["sleep"])
                if self.path.rstrip("/").endswith("/models"):
                    self._w(
                        cfg["models_status"],
                        {"data": [{"id": m} for m in cfg["models"]]},
                    )
                else:
                    self._w(404, {"error": "nf"})

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if cfg["sleep"]:
                    time.sleep(cfg["sleep"])
                self._w(cfg["post_status"], cfg["post_body"])

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._t.start()
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}/v1"

    def shutdown(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


# ------------------------------------------------------------------ M2-1


def test_m2_1_mt_pe_empty_mt_postedit_incomplete(tmp_path: Path) -> None:
    """Выбор mt_pe при пустых mt/postedit — ``SettingsIncompleteError``
    (в API — 422 со сводкой); файлы не изменены (sha256)."""
    cfg, env = _write_cfg(tmp_path, pipeline="single", empty=("mt", "postedit"))
    sha_cfg, sha_env = _sha(cfg), _sha(env)
    valid = {
        name: {"base_url": "http://127.0.0.1:9/v1", "model": "chat-model"}
        for name in ("translation", "fill", "analysis")
    }
    with pytest.raises(SettingsIncompleteError) as exc:
        update_llm_config(
            pipeline="mt_pe",
            profile_fields=valid,
            config_path=cfg,
            env_path=env,
        )
    # сводка: проблемы у mt и postedit (база/модель)
    paths = {p for p, _code in exc.value.details["issues"]}
    assert any("mt" in p for p in paths), paths
    assert any("postedit" in p for p in paths), paths
    # файлы не тронуты
    assert _sha(cfg) == sha_cfg and _sha(env) == sha_env


# ------------------------------------------------------------------ M2-4


def test_m2_4_probe_four_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    """4 из 4 строк итога точные: успех mt, model_not_listed, 401, timeout."""
    # 1) успех mt: модель в списке, ответ модели совпадает, токены из usage
    srv = _MockLLM(
        models=["mt-model"],
        post_body={
            "model": "mt-model",
            "usage": {"completion_tokens": 16},
            "choices": [{"text": "Good morning! Hello."}],
        },
    )
    try:
        r = probe_profile(_mt_data(srv.url, "mt-model"))
        assert r["ok"] is True
        msg = r["message"]
        assert msg.startswith("Ответ ") and msg.endswith("токенов"), msg
        assert "модель mt-model" in msg and "16 токенов" in msg, msg
        assert r.get("response_text"), "mt: текст ответа в результате"
    finally:
        srv.shutdown()
    # 2) model_not_listed: модель НЕ в списке GET /models
    srv = _MockLLM(models=["other-model"])
    try:
        r = probe_profile(_mt_data(srv.url, "mt-model"))
        assert r["ok"] is False
        assert r["message"] == UI_STRINGS["llm_probe_model_not_listed"]
    finally:
        srv.shutdown()
    # 3) 401: GET /models → 401
    srv = _MockLLM(models_status=401, post_body={"error": "denied"})
    try:
        r = probe_profile(_mt_data(srv.url, "mt-model"))
        assert r["ok"] is False
        assert r["message"] == UI_STRINGS["llm_probe_http_401"]
    finally:
        srv.shutdown()
    # 4) timeout: эндпоинт молчит дольше PROBE_TIMEOUT_SECONDS
    monkeypatch.setattr(llm_config, "PROBE_TIMEOUT_SECONDS", 0.2)
    srv = _MockLLM(models=["mt-model"], sleep=1.5)
    try:
        r = probe_profile(_mt_data(srv.url, "mt-model"))
        assert r["ok"] is False
        assert "таймаут" in r["message"], r["message"]
    finally:
        srv.shutdown()


# ------------------------------------------------------------------ M2-5


def test_m2_5_probe_409_diff_model_same_base(tmp_path: Path) -> None:
    """Рабочее задание в LLM-фазе на этом эндпоинте + другая модель → 409;
    та же модель или другая base_url → конфликта нет."""
    db = Database(tmp_path / "jobs.db", 30000)
    repo = JobRepository(db)
    snap = json.dumps(
        {
            "pipeline": "single",
            "profiles": {
                "translation": {"base_url": "http://probe-host/v1", "model": "model-A"}
            },
        }
    )
    job = Job(
        id="j-m2-409",
        status=JobStatus.TRANSLATING,
        source_path="/x",
        target_language="de",
        pipeline="single",
        config_snapshot=snap,
        created_at=1.0,
        updated_at=1.0,
    )
    repo.create(job)
    conflict = probe_conflict(repo, "http://probe-host/v1", "model-B")
    assert conflict is not None
    assert "j-m2-409" in conflict, conflict
    assert probe_conflict(repo, "http://probe-host/v1", "model-A") is None
    assert probe_conflict(repo, "http://other-host/v1", "model-B") is None


# ------------------------------------------------------------------ M2-6


def test_m2_6_no_shared_base_url_write(tmp_path: Path) -> None:
    """Разные base_url по профилям пишутся в каждый (один общий URL в несколько
    профилей не вписывается); в config_edit нет общего эндпоинта."""
    cfg, env = _write_cfg(tmp_path)
    update_llm_config(
        pipeline="single",
        profile_fields={
            "translation": {"base_url": "http://a-host/v1", "model": "m"},
            "fill": {"base_url": "http://b-host/v1", "model": "m"},
            "analysis": {"base_url": "http://c-host/v1", "model": "m"},
        },
        config_path=cfg,
        env_path=env,
    )
    text = cfg.read_text(encoding="utf-8")
    for host in ("a-host", "b-host", "c-host"):
        assert text.count(f"base_url: http://{host}/v1") == 1, host
    # код записи не имеет «общего эндпоинта» (R8-механизм удалён в M2)
    src = (_REPO / "app" / "core" / "config_edit.py").read_text(encoding="utf-8")
    assert "shared_endpoint" not in src
    assert "common_base_url" not in src


# ------------------------------------------------------------------ M2-8


def test_m2_8_budgets_and_colors() -> None:
    """CSS ≤ 24 576 Б, JS ≤ 81 920 Б; цвета в css-settings.css — только :root."""
    css = (_STATIC / "css-settings.css").read_bytes()
    js = (_STATIC / "app.js").read_bytes()
    assert len(css) <= 24576, len(css)
    assert len(js) <= 81920, len(js)
    # литеральные цвета (hex / rgba) в css-settings.css — 0 (все — var(--…))
    literal = re.compile(r"#[0-9A-Fa-f]{3,6}\b|rgba?\(")
    offenders = [ln for ln in css.decode().splitlines() if literal.search(ln)]
    assert not offenders, offenders
    # ключевые строки M2 — в strings.py (не захардкожены в шаблоне)
    for key in (
        "llm_pipeline_label",
        "llm_pipeline_single",
        "llm_pipeline_mt_pe",
        "llm_key_delete_label",
        "llm_probe_409",
    ):
        assert key in UI_STRINGS
