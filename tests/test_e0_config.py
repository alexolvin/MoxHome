"""T-E0-1, T-E0-2: конфигурация — отказ при пропуске обязательных параметров,
отказ wildcard-bind без флага, воспроизводимость валидной загрузки."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import load_settings
from app.core.errors import ConfigurationError


def _missing_keys(excinfo: pytest.ExceptionInfo[ConfigurationError]) -> list[str]:
    details = excinfo.value.details
    assert details is not None
    missing = details["missing"]
    assert isinstance(missing, list)
    return [str(item) for item in missing]


@pytest.mark.parametrize("key", ["SERVER_BIND_ADDRESS", "LLM_BASE_URL", "LLM_MODEL"])
def test_missing_required_env_key(
    key: str, make_env: object, make_yaml: object, tmp_path: Path
) -> None:
    # T-E0-1
    env_file = make_env({key: None})  # type: ignore[operator]
    yaml_file = make_yaml(str(tmp_path / "data"))  # type: ignore[operator]
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(yaml_file, env_file)
    assert key in _missing_keys(excinfo)


def test_missing_storage_root(make_env: object, tmp_path: Path) -> None:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("storage: {}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(yaml_file, make_env())  # type: ignore[operator]
    assert any("storage.root" in item for item in _missing_keys(excinfo))


def test_all_missing_listed_at_once(make_env: object, make_yaml: object, tmp_path: Path) -> None:
    env_file = make_env(
        {"SERVER_BIND_ADDRESS": None, "LLM_BASE_URL": None, "LLM_MODEL": None}
    )  # type: ignore[operator]
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(make_yaml(str(tmp_path / "data")), env_file)  # type: ignore[operator]
    assert set(_missing_keys(excinfo)) == {
        "SERVER_BIND_ADDRESS",
        "LLM_BASE_URL",
        "LLM_MODEL",
    }


def test_wildcard_bind_refused_without_flag(
    make_env: object, make_yaml: object, tmp_path: Path
) -> None:
    # T-E0-2
    env_file = make_env({"SERVER_BIND_ADDRESS": "0.0.0.0"})  # type: ignore[operator]
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(make_yaml(str(tmp_path / "data")), env_file)  # type: ignore[operator]
    assert "0.0.0.0" in excinfo.value.message
    assert excinfo.value.exit_code == 2


def test_wildcard_bind_allowed_with_flag(
    make_env: object, make_yaml: object, tmp_path: Path
) -> None:
    env_file = make_env(
        {"SERVER_BIND_ADDRESS": "0.0.0.0", "ALLOW_WILDCARD_BIND": "true"}
    )  # type: ignore[operator]
    settings = load_settings(make_yaml(str(tmp_path / "data")), env_file)  # type: ignore[operator]
    assert settings.server.allow_wildcard_bind is True


def test_valid_config_loads(make_env: object, make_yaml: object, tmp_path: Path) -> None:
    settings = load_settings(make_yaml(str(tmp_path / "data")), make_env())  # type: ignore[operator]
    assert settings.llm.base_url == "http://127.0.0.1:9999/v1"
    assert settings.llm.model == "test-model"
    assert settings.llm.api_key == "test-key-value-000"
    assert settings.server.port == 8321
    assert settings.storage.root == (tmp_path / "data").resolve()
    for directory in settings.all_data_dirs():
        assert directory.is_dir(), directory
    assert set(settings.llm.profiles) == {"translation", "fill", "analysis"}
    assert settings.llm.profiles["translation"].concurrency == 1
    assert settings.secrets() == ("test-key-value-000",)


def test_profiles_endpoint_override(make_env: object, make_yaml: object, tmp_path: Path) -> None:
    yaml_file = make_yaml(
        str(tmp_path / "data"),
        "llm:\n  profiles:\n    translation:\n      concurrency: 2\n",
    )  # type: ignore[operator]
    env_file = make_env(  # type: ignore[operator]
        {
            "LLM_ANALYSIS_BASE_URL": "http://127.0.0.1:9998/v1",
            "LLM_ANALYSIS_MODEL": "analysis-model",
        }
    )
    settings = load_settings(yaml_file, env_file)
    assert settings.llm.profiles["translation"].concurrency == 2
    assert settings.llm.endpoint("translation") == (
        "http://127.0.0.1:9999/v1",
        "test-model",
        "test-key-value-000",
    )
    assert settings.llm.endpoint("analysis") == (
        "http://127.0.0.1:9998/v1",
        "analysis-model",
        "test-key-value-000",
    )


def test_env_file_missing(make_yaml: object, tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(make_yaml(str(tmp_path / "data")), tmp_path / "absent.env")  # type: ignore[operator]
    assert excinfo.value.details is not None
    assert "problems" in excinfo.value.details


def test_yaml_file_missing(make_env: object, tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_settings(tmp_path / "absent.yaml", make_env())  # type: ignore[operator]


def test_invalid_yaml_value_rejected(
    make_env: object, make_yaml: object, tmp_path: Path
) -> None:
    yaml_file = make_yaml(
        str(tmp_path / "data"),
        "llm:\n  profiles:\n    translation:\n      concurrency: -5\n",
    )  # type: ignore[operator]
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(yaml_file, make_env())  # type: ignore[operator]
    assert excinfo.value.details is not None
    assert "errors" in excinfo.value.details
