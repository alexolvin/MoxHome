# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Общие фикстуры E0/E4: изоляция окружения, генерация .env и YAML.

E4.1: адреса/модели LLM задаются в YAML-профилях (make_yaml), в .env — только
секреты и адрес бинда (make_env).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from app.core.config import ALL_ENV_KEYS, PROFILE_NAMES

if __package__ in (None, ""):  # запуск не через pytest: tests/ на sys.path
    import sys as _sys
    from pathlib import Path as _Path

    _tests_dir = str(_Path(__file__).resolve().parent)
    if _tests_dir not in _sys.path:
        _sys.path.insert(0, _tests_dir)

# U1Env (tests/test_u1_web.py) — общее окружение для последующих стадий:
# tests/test_u3_web.py просит фикстуру u1_env без собственного дубля.
from test_u1_web import u1_env  # noqa: F401 — фикстура, не вызов

#: Секреты и адрес бинда (E4.1: адрес/модель LLM сюда не входят).
DEFAULT_ENV: dict[str, str] = {
    "SERVER_BIND_ADDRESS": "127.0.0.1",
    "LLM_API_KEY": "test-key-value-000",
}
#: Тестовые адрес/модель по умолчанию для YAML-профилей (тестовая фикстура).
DEFAULT_LLM_BASE_URL = "http://127.0.0.1:9999/v1"
DEFAULT_LLM_MODEL = "test-model"
#: EPUBCheck (D4): локальная установка инструмента в data/tools (gitignored).
#: Без доступного EPUBCheck прогон завершается COMPLETED_WITH_WARNINGS
#: (VALIDATION_UNAVAILABLE, E5.7), поэтому по умолчанию фикстура указывает на
#: реальный jar; E5-11 переопределяет путь на несуществующий.
DEFAULT_EPUBCHECK_JAR = str(
    Path(__file__).resolve().parents[1] / "data" / "tools" / "epubcheck-5.3.0" / "epubcheck.jar"
)
#: Корень репозитория: реестр языков и промпты по умолчанию — данные из репо (L0).
_REPO_ROOT = Path(__file__).resolve().parents[1]
#: Реестр языков (L0.1): по умолчанию — реестр репозитория (абсолютный путь).
DEFAULT_LANGUAGES_ROOT = str(_REPO_ROOT / "config" / "languages")
DEFAULT_LANGUAGE_MATRIX = str(_REPO_ROOT / "config" / "language_matrix.yaml")
#: Промпты (E4/L0): по умолчанию — промпты репозитория (абсолютный путь);
#: тесты, управляющие деревом промптов, передают собственный prompts_root.
DEFAULT_PROMPTS_ROOT = str(_REPO_ROOT / "prompts")


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
    profile_overrides — поимённые переопределения полей профилей (T-E4-8 и пр.),
    prompts_root — корень промптов (по умолчанию llm.prompts_root),
    languages_root/language_matrix — реестр и матрица (L0): по умолчанию данные
    из репозитория (абсолютные пути), чтобы резолвер пар работал в каждом тесте,
    lease_seconds — server.lease_seconds (T-E5-10: аренда должна покрывать весь
    цикл, иначе one-shot-воркер возьмёт «зомби» вместо текущего задания).
    """

    def _make(
        root: str,
        extra: str = "",
        *,
        base_url: str = DEFAULT_LLM_BASE_URL,
        model: str = DEFAULT_LLM_MODEL,
        profile_overrides: Mapping[str, Mapping[str, object]] | None = None,
        epubcheck_jar: str = DEFAULT_EPUBCHECK_JAR,
        prompts_root: str = "",
        languages_root: str = DEFAULT_LANGUAGES_ROOT,
        language_matrix: str = DEFAULT_LANGUAGE_MATRIX,
        lease_seconds: float | None = None,
    ) -> Path:
        overrides = profile_overrides or {}
        profile_lines: list[str] = []
        for name in PROFILE_NAMES:
            fields: dict[str, object] = {"base_url": base_url, "model": model}
            fields.update(overrides.get(name, {}))
            profile_lines.append(f"    {name}:")
            for key, value in fields.items():
                profile_lines.append(f"      {key}: {_yaml_scalar(value)}")
        epubcheck_lines = (
            f"epubcheck:\n  jar_path: {_yaml_scalar(epubcheck_jar)}\n" if epubcheck_jar else ""
        )
        llm_lines = "llm:\n"
        if prompts_root:
            llm_lines += f"  prompts_root: {_yaml_scalar(prompts_root)}\n"
        llm_lines += "  profiles:\n" + "\n".join(profile_lines) + "\n"
        languages_lines = "languages:\n"
        if languages_root:
            languages_lines += f"  root: {_yaml_scalar(languages_root)}\n"
        if language_matrix:
            languages_lines += f"  matrix: {_yaml_scalar(language_matrix)}\n"
        # server.lease_seconds: по умолчанию 30 c; переопределяется, когда тесту
        # нужно, чтобы аренда не истекла на протяжении всего сценария (T-E5-10).
        # Ограничение E3-1 (lease ≤ 2×heartbeat) обязывает поднимать heartbeat
        # вместе с лизой: heartbeat = lease/2 — минимально допустимое.
        server_lines = ""
        if lease_seconds is not None:
            server_lines = (
                f"server:\n"
                f"  heartbeat_seconds: {lease_seconds / 2}\n"
                f"  lease_seconds: {lease_seconds}\n"
            )
        path = tmp_path / "config.yaml"
        path.write_text(
            f"storage:\n  root: {root}\n"
            + server_lines
            + llm_lines
            + epubcheck_lines
            + languages_lines
            + extra,
            encoding="utf-8",
        )
        return path

    return _make
