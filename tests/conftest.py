"""Общие фикстуры E0/E4: изоляция окружения, генерация .env и YAML.

E4.1: адреса/модели LLM задаются в YAML-профилях (make_yaml), в .env — только
секреты и адрес бинда (make_env).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from app.core.config import ALL_ENV_KEYS, PROFILE_NAMES

#: Секреты и адрес бинда (E4.1: адрес/модель LLM сюда не входят).
DEFAULT_ENV: dict[str, str] = {
    "SERVER_BIND_ADDRESS": "127.0.0.1",
    "LLM_API_KEY": "test-key-value-000",
}
#: Тестовые адрес/модель по умолчанию для YAML-профилей (тестовая фикстура).
DEFAULT_LLM_BASE_URL = "http://127.0.0.1:9999/v1"
DEFAULT_LLM_MODEL = "test-model"


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


def _yaml_scalar(value: object) -> str:
    text = str(value)
    if (text and any(ch in text for ch in ":#{}[]&*!|>%@\"'")) or text != text.strip():
        return f'"{text}"'
    return text


@pytest.fixture()
def make_yaml(
    tmp_path: Path,
) -> Callable[..., Path]:
    """Генерирует config.yaml со storage.root и полными LLM-профилями (E4.1).

    Параметры: root, extra (доп. YAML ниже), base_url/model (для всех профилей),
    profile_overrides — поимённые переопределения полей профилей (T-E4-8 и пр.).
    """

    def _make(
        root: str,
        extra: str = "",
        *,
        base_url: str = DEFAULT_LLM_BASE_URL,
        model: str = DEFAULT_LLM_MODEL,
        profile_overrides: Mapping[str, Mapping[str, object]] | None = None,
    ) -> Path:
        overrides = profile_overrides or {}
        profile_lines: list[str] = []
        for name in PROFILE_NAMES:
            fields: dict[str, object] = {"base_url": base_url, "model": model}
            fields.update(overrides.get(name, {}))
            profile_lines.append(f"    {name}:")
            for key, value in fields.items():
                profile_lines.append(f"      {key}: {_yaml_scalar(value)}")
        path = tmp_path / "config.yaml"
        path.write_text(
            f"storage:\n  root: {root}\nllm:\n  profiles:\n"
            + "\n".join(profile_lines)
            + "\n"
            + extra,
            encoding="utf-8",
        )
        return path

    return _make
