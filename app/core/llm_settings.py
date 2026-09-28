"""Единая проверка настроек LLM (MTPE TZ §5.3).

`validate_llm_settings(settings) -> list[SettingsIssue]` — ЕДИНСТВЕННАЯ точка
проверки полей настроек LLM (TZ §5.3 «иных проверок этих полей в коде нет»).
`SettingsIssue = (path, code)`; текст сообщения — из `app.web.strings` по `code`
(префикс `validate_`, либо `pipeline_in_settings_only` / `pair_unsupported_mt_pe`
для отказа создания задания).

Пять точек применения (TZ §5.3): (1) сохранение настроек; (2) старт веб-сервера
(полоса «Настройки LLM неполны»); (3) воркер перед взятием задания; (4) создание
задания (API/мастер/CLI); (5) новая версия промпта mt_draft (V7).

Профили, обязательные для способа (TZ §4.1): single → translation/fill/analysis;
mt_pe → analysis/mt/postedit. Непустота base_url/model, формат эндпоинта, api,
промпты и числовые пределы проверяются ТОЛЬКО для обязательных профилей.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlparse

from app.core.config import LLMProfile, Settings
from app.core.errors import ConfigurationError
from app.languages.registry import load_registry
from app.languages.resolver import load_matrix, mt_pe_supported
from app.llm.prompts import VERSION_FILE_RE, _best_version, _parse_prompt_file

__all__ = [
    "SettingsIssue",
    "apply_job_config_snapshot",
    "build_job_config_snapshot",
    "mt_draft_template_issues",
    "validate_llm_settings",
]

_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


class SettingsIssue(NamedTuple):
    """Проблема настройки: путь (dot-нотация) + код (ключ в `app.web.strings`)."""

    path: str
    code: str


#: Профили, обязательные для каждого способа перевода (TZ §4.1).
_REQUIRED_PROFILES: dict[str, tuple[str, ...]] = {
    "single": ("translation", "fill", "analysis"),
    "mt_pe": ("analysis", "mt", "postedit"),
}

#: Ограничения числовых полей mt-профилей (TZ §5.3 V8): (min, max); max=None —
#: верхней нет. Проверяются только для обязательных профилей (mt → mt_pe).
_PROFILE_NUMERIC_LIMITS: dict[str, dict[str, tuple[float, float | None]]] = {
    "mt": {
        "top_k": (1, None),
        "max_tokens_ratio": (0, None),
        "max_segment_tokens": (1, None),
        "retries": (0, None),
        "timeout_s": (0, None),
    },
    "postedit": {
        "retries": (0, None),
        "timeout_s": (0, None),
    },
}

#: Ограничения числовых полей раздела mtpe (TZ §5.3 V8) — проверяются всегда.
_MTPE_NUMERIC_LIMITS: dict[str, tuple[float, float | None]] = {
    "context_segments": (0, None),
    "lang_check_min_words": (0, None),
    "max_edit_ratio": (0, 1),
    "max_heavy_edit_ratio": (0, 1),
    "max_sentence_delta": (0, None),
    "max_rejected_ratio": (0, 1),
    "check_every_segments": (1, None),
}

#: Плейсхолдеры начальной версии mt_draft (TZ §5.4) — ровно эти, без других.
_MT_DRAFT_PLACEHOLDERS = frozenset({"{src_name}", "{tgt_name}", "{text}"})


def _check_base_url(profile: LLMProfile, path: str, issues: list[SettingsIssue]) -> None:
    base_url = profile.base_url
    if not base_url.strip():
        issues.append(SettingsIssue(f"{path}.base_url", "validate_base_url_empty"))
        return
    parsed = urlparse(base_url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.path.rstrip("/") != "/v1"
    ):
        issues.append(SettingsIssue(f"{path}.base_url", "validate_base_url_format"))


def _check_model(profile: LLMProfile, path: str, issues: list[SettingsIssue]) -> None:
    model = profile.model
    if not model.strip() or any(ch.isspace() for ch in model):
        issues.append(SettingsIssue(f"{path}.model", "validate_model_empty"))


def _check_api(name: str, profile: LLMProfile, path: str, issues: list[SettingsIssue]) -> None:
    expected = "completions" if name == "mt" else "chat"
    if profile.api != expected:
        issues.append(SettingsIssue(f"{path}.api", "validate_api_fixed"))


def _check_numeric(
    value: Any, bounds: tuple[float, float | None], path: str, issues: list[SettingsIssue]
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        issues.append(SettingsIssue(path, "validate_out_of_limits"))
        return
    lo, hi = bounds
    if value < lo or (hi is not None and value > hi):
        issues.append(SettingsIssue(path, "validate_out_of_limits"))


def _prompt_task_active(prompts_root: Path, task: str) -> bool:
    """Задача существует и имеет активную версию (vN.yaml на любом уровне)."""
    task_dir = prompts_root / task
    if not task_dir.is_dir():
        return False
    return any(VERSION_FILE_RE.match(c.name) for c in task_dir.rglob("*.yaml"))


def mt_draft_template_issues(user_template: str) -> list[SettingsIssue]:
    """V7: текст шаблона mt_draft содержит ровно {src_name},{tgt_name},{text}
    и никаких других. Пустой список — шаблон корректен."""
    found = set(_PLACEHOLDER_RE.findall(user_template))
    if found != _MT_DRAFT_PLACEHOLDERS:
        return [
            SettingsIssue("llm.profiles.mt.prompt_task", "validate_mt_template_placeholders")
        ]
    return []


def _check_mt_draft_template(prompts_root: Path, issues: list[SettingsIssue]) -> None:
    """V7: активная версия mt_draft содержит ровно {src_name},{tgt_name},{text}."""
    version_file = _best_version(prompts_root / "mt_draft")
    if version_file is None:
        return  # отсутствие задачи уже помечено V6
    try:
        _version, _system, user_template = _parse_prompt_file(version_file)
    except ConfigurationError:
        issues.append(
            SettingsIssue("llm.profiles.mt.prompt_task", "validate_mt_template_placeholders")
        )
        return
    issues.extend(mt_draft_template_issues(user_template))


def _has_mt_pe_pair(settings: Settings) -> bool:
    """V10: хотя бы одна пара матрицы со статусом supported для mt_pe (TZ §5.5)."""
    try:
        matrix = load_matrix(settings.language_matrix_path)
        registry = load_registry(settings.languages_root)
    except ConfigurationError:
        return False
    return any(mt_pe_supported(registry, source, target) for source, target in matrix)


def validate_llm_settings(settings: Settings) -> list[SettingsIssue]:
    """Проверить настройки LLM (TZ §5.3 V1–V10). Пустой список — всё в порядке."""
    issues: list[SettingsIssue] = []
    pipeline = settings.translation.pipeline

    # V1: способ перевода — single | mt_pe.
    if pipeline not in ("single", "mt_pe"):
        issues.append(SettingsIssue("translation.pipeline", "validate_pipeline_unknown"))
        return issues  # обязательные профили не определяются — дальнейших проверок нет

    required = _REQUIRED_PROFILES[pipeline]
    profiles = settings.llm.profiles

    for name in required:
        profile = profiles.get(name)
        path = f"llm.profiles.{name}"
        if profile is None:
            issues.append(SettingsIssue(f"{path}.base_url", "validate_base_url_empty"))
            continue
        # V2/V3: эндпоинт; V4: модель; V5: api; V9: extra_body.
        _check_base_url(profile, path, issues)
        _check_model(profile, path, issues)
        _check_api(name, profile, path, issues)
        extra_body: Any = profile.extra_body  # Any: model_construct может дать не-dict (V9)
        if not isinstance(extra_body, dict):
            issues.append(SettingsIssue(f"{path}.extra_body", "validate_extra_body_invalid"))
        # V8: числовые поля профиля (только у обязательных mt-профилей).
        for field, bounds in _PROFILE_NUMERIC_LIMITS.get(name, {}).items():
            value = getattr(profile, field)
            if value is not None:
                _check_numeric(value, bounds, f"{path}.{field}", issues)

    # V6/V7: промпт обязательного профиля с prompt_task (mt → mt_draft, postedit → postedit).
    for name in required:
        profile = profiles.get(name)
        if profile is None or profile.prompt_task is None:
            continue
        if not _prompt_task_active(settings.prompts_dir, profile.prompt_task):
            issues.append(
                SettingsIssue(f"llm.profiles.{name}.prompt_task", "validate_prompt_missing")
            )
            continue
        if name == "mt":
            _check_mt_draft_template(settings.prompts_dir, issues)

    # V8: числовые поля раздела mtpe (всегда).
    for field, bounds in _MTPE_NUMERIC_LIMITS.items():
        _check_numeric(getattr(settings.mtpe, field), bounds, f"mtpe.{field}", issues)

    # V10: mt_pe — хотя бы одна supported-пара (TZ §5.5).
    if pipeline == "mt_pe" and not _has_mt_pe_pair(settings):
        issues.append(SettingsIssue("mtpe", "validate_no_mt_pairs"))

    return issues


def build_job_config_snapshot(settings: Settings) -> str:
    """MTPE TZ §4.5: снимок задания — pipeline + значения обязательных профилей
    (БЕЗ ключей) на момент создания. Ключи читаются из .env по имени профиля в
    момент вызова, поэтому в снимок не попадают. JSON-строка."""
    pipeline = settings.translation.pipeline
    if pipeline not in _REQUIRED_PROFILES:
        return json.dumps(
            {"pipeline": pipeline, "profiles": {}}, ensure_ascii=False
        )
    profiles = {
        name: settings.llm.profiles[name].model_dump()
        for name in _REQUIRED_PROFILES[pipeline]
        if name in settings.llm.profiles
    }
    return json.dumps(
        {"pipeline": pipeline, "profiles": profiles}, ensure_ascii=False
    )


def apply_job_config_snapshot(settings: Settings, snapshot_json: str) -> Settings:
    """MTPE TZ §4.5: Settings, где значения профилей — из снимка задания
    (значения создания). Ключи — из .env (в снимке их нет). Пустой/чужой снимок
    или невалидное поле — settings без изменений (не ломает старые задания)."""
    if not snapshot_json:
        return settings
    try:
        data: Any = json.loads(snapshot_json)
    except ValueError:
        return settings
    if not isinstance(data, dict):
        return settings
    profiles_data = data.get("profiles")
    if not isinstance(profiles_data, dict):
        return settings
    profiles = dict(settings.llm.profiles)
    for name, raw in profiles_data.items():
        if not isinstance(raw, dict):
            continue
        try:
            profiles[str(name)] = LLMProfile.model_validate(raw)
        except Exception:  # невалидное поле снимка — оставляем текущее
            continue
    llm = settings.llm.model_copy(update={"profiles": profiles})
    return settings.model_copy(update={"llm": llm})
