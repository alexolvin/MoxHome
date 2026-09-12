"""Общие фикстуры E0: изоляция окружения, генерация .env и YAML."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from app.core.config import ALL_ENV_KEYS

DEFAULT_ENV: dict[str, str] = {
    "SERVER_BIND_ADDRESS": "127.0.0.1",
    "LLM_BASE_URL": "http://127.0.0.1:9999/v1",
    "LLM_MODEL": "test-model",
    "LLM_API_KEY": "test-key-value-000",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Убрать переменные приложения из окружения хоста: тесты не зависят от него."""
    for key in ALL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def make_env(tmp_path: Path) -> Callable[[Mapping[str, str | None] | None], Path]:
    def _make(mutations: Mapping[str, str | None] | None = None) -> Path:
        data = dict(DEFAULT_ENV)
        for key, value in (mutations or {}).items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        path = tmp_path / "test.env"
        path.write_text(
            "\n".join(f"{key}={value}" for key, value in data.items()) + "\n",
            encoding="utf-8",
        )
        return path

    return _make


@pytest.fixture()
def make_yaml(tmp_path: Path) -> Callable[[str, str], Path]:
    def _make(root: str, extra: str = "") -> Path:
        path = tmp_path / "config.yaml"
        path.write_text(f"storage:\n  root: {root}\n{extra}", encoding="utf-8")
        return path

    return _make
