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


@pytest.mark.parametrize("key", ["SERVER_BIND_ADDRESS"])
def test_missing_required_env_key(
    key: str, make_env: object, make_yaml: object, tmp_path: Path
) -> None:
    # T-E0-1 (E4.1: в .env остался единственный обязательный адрес — bind)
    env_file = make_env({key: None})  # type: ignore[operator]
    yaml_file = make_yaml(str(tmp_path / "data"))  # type: ignore[operator]
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(yaml_file, env_file)
    assert key in _missing_keys(excinfo)


@pytest.mark.parametrize("profile", ["translation", "fill", "analysis"])
@pytest.mark.parametrize("field", ["base_url", "model"])
def test_missing_required_llm_profile_field(
    profile: str,
    field: str,
    make_env: object,
    make_yaml: object,
    tmp_path: Path,
) -> None:
    # E4.1: адрес и модель — только в YAML-профилях; пропуск → отказ старта.
    yaml_file = make_yaml(  # type: ignore[operator]
        str(tmp_path / "data"), profile_overrides={profile: {field: ""}}
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(yaml_file, make_env())  # type: ignore[operator]
    assert f"llm.profiles.{profile}.{field} (YAML-конфиг)" in _missing_keys(excinfo)


def test_missing_storage_root(make_env: object, tmp_path: Path) -> None:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("storage: {}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(yaml_file, make_env())  # type: ignore[operator]
    assert any("storage.root" in item for item in _missing_keys(excinfo))


def test_all_missing_listed_at_once(
    make_env: object, tmp_path: Path
) -> None:
    # Пустой YAML (нет root и всех LLM-профилей) + пустой bind — всё в одном списке.
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("storage: {}\n", encoding="utf-8")
    env_file = make_env({"SERVER_BIND_ADDRESS": None})  # type: ignore[operator]
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(yaml_file, env_file)
    assert set(_missing_keys(excinfo)) == {
        "SERVER_BIND_ADDRESS",
        "storage.root (YAML-конфиг)",
        "llm.profiles.translation.base_url (YAML-конфиг)",
        "llm.profiles.translation.model (YAML-конфиг)",
        "llm.profiles.fill.base_url (YAML-конфиг)",
        "llm.profiles.fill.model (YAML-конфиг)",
        "llm.profiles.analysis.base_url (YAML-конфиг)",
        "llm.profiles.analysis.model (YAML-конфиг)",
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
    # E4.1: адрес и модель — только в YAML-профилях (env-вариантов не существует).
    translation = settings.llm.profiles["translation"]
    assert translation.base_url == "http://127.0.0.1:9999/v1"
    assert translation.model == "test-model"
    assert settings.llm.api_key == "test-key-value-000"
    assert settings.server.port == 8321
    assert settings.storage.root == (tmp_path / "data").resolve()
    for directory in settings.all_data_dirs():
        assert directory.is_dir(), directory
    assert set(settings.llm.profiles) == {"translation", "fill", "analysis"}
    assert translation.concurrency == 1
    assert settings.secrets() == ("test-key-value-000",)


def test_profiles_endpoint_override(make_env: object, make_yaml: object, tmp_path: Path) -> None:
    # E4.1: переопределение — в YAML-профилях; у analysis может быть свой API-ключ.
    yaml_file = make_yaml(  # type: ignore[operator]
        str(tmp_path / "data"),
        profile_overrides={
            "translation": {"concurrency": 2},
            "analysis": {"base_url": "http://127.0.0.1:9998/v1", "model": "analysis-model"},
        },
    )
    env_file = make_env({"LLM_ANALYSIS_API_KEY": "analysis-key-000"})  # type: ignore[operator]
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
        "analysis-key-000",
    )


def test_env_file_missing(make_yaml: object, tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(make_yaml(str(tmp_path / "data")), tmp_path / "absent.env")  # type: ignore[operator]
    assert excinfo.value.details is not None
    assert "problems" in excinfo.value.details


def test_prompts_root_default_is_storage_subdir(
    make_env: object, make_yaml: object, tmp_path: Path
) -> None:
    # D62: без ключа — <storage.root>/prompts (семантика E4.5 сохраняется).
    settings = load_settings(make_yaml(str(tmp_path / "data")), make_env())  # type: ignore[operator]
    assert settings.llm.prompts_root == "prompts"
    assert settings.prompts_dir == settings.storage.root / "prompts"


def test_prompts_root_explicit_resolved(
    make_env: object, make_yaml: object, tmp_path: Path
) -> None:
    # D62: YAML-ключ реально читается; абсолютный путь — как есть,
    # относительный — от storage.root.
    absolute = tmp_path / "custom_prompts"
    absolute.mkdir()
    settings = load_settings(  # type: ignore[operator]
        make_yaml(str(tmp_path / "data"), prompts_root=str(absolute)), make_env()  # type: ignore[operator]
    )
    assert settings.prompts_dir == absolute.resolve()
    root = tmp_path / "data2"
    (root / "relative_prompts").mkdir(parents=True)
    settings = load_settings(  # type: ignore[operator]
        make_yaml(str(root), prompts_root="relative_prompts"), make_env()  # type: ignore[operator]
    )
    assert settings.prompts_dir == (root / "relative_prompts").resolve()


def test_prompts_root_missing_dir_refused(
    make_env: object, make_yaml: object, tmp_path: Path
) -> None:
    # D62: явно заданный несуществующий каталог — отказ старта (fail closed).
    yaml_file = make_yaml(  # type: ignore[operator]
        str(tmp_path / "data"), prompts_root=str(tmp_path / "no_such_prompts")
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(yaml_file, make_env())  # type: ignore[operator]
    assert "prompts_root" in excinfo.value.message


def test_yaml_file_missing(make_env: object, tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_settings(tmp_path / "absent.yaml", make_env())  # type: ignore[operator]


def test_invalid_yaml_value_rejected(
    make_env: object, make_yaml: object, tmp_path: Path
) -> None:
    yaml_file = make_yaml(
        str(tmp_path / "data"),
        profile_overrides={"translation": {"concurrency": -5}},
    )  # type: ignore[operator]
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(yaml_file, make_env())  # type: ignore[operator]
    assert excinfo.value.details is not None
    assert "errors" in excinfo.value.details
