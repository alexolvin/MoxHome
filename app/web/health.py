"""``/api/health`` (U1.8): покомпонентное состояние, никогда общий флаг.

Форма ответа — как в ТЗ (пример)::

    {"app":"ok","database":"ok","storage":"ok","llm":{...},"epubcheck":"ok",
     "nlp":{"en":"ok",...}}

``llm`` — по профильным пробовам (GET ``{base_url}/models``, Bearer-ключ
из ``.env``, таймаут 5 c): ``{"translation":"ok","fill":"error",...}`` —
состав по компонентам, без агрегации в единый флаг (критерий U1-3: при
выключенном LLM — ``error`` у компонента и отсутствие ``healthy: true``
во всём ответе). ``nlp`` — по языковым пакетам: доступен ли spaCy-пайплайн
(``spacy_model_available`` — тот же пробы, что у резолвера L0).
``epubcheck`` — jar на месте и java доступна (тот же ``_resolve``, что и
конвейер — единая точка, Rule 8).
"""

from __future__ import annotations

import os
import shutil

import httpx

from app.core.config import PROFILE_NAMES, Settings
from app.languages.registry import load_registry
from app.languages.resolver import spacy_model_available
from app.storage.jobs_repo import JobRepository


def _probe_database(repo: JobRepository) -> tuple[str, str]:
    try:
        repo.ping()
        return "ok", ""
    except Exception as exc:  # любой сбой БД — "error"
        return "error", f"SELECT 1: {exc}"


def _probe_storage(settings: Settings) -> tuple[str, str]:
    probe = settings.storage.root / f".u1-health-{os.getpid()}"
    try:
        probe.write_bytes(b"u1")
        ok = probe.read_bytes() == b"u1"
        return ("ok" if ok else "error"), "" if ok else "чтение/запись не совпала"
    except OSError as exc:
        return "error", str(exc)
    finally:
        probe.unlink(missing_ok=True)


def _probe_llm(settings: Settings) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in PROFILE_NAMES:
        base_url, _model, api_key = settings.llm.endpoint(name)
        url = base_url.rstrip("/") + "/models"
        try:
            response = httpx.get(
                url,
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                timeout=5.0,
            )
        except httpx.HTTPError as exc:
            result[name] = f"error ({type(exc).__name__})"
        else:
            if response.status_code < 500:
                result[name] = "ok"
            else:
                result[name] = f"error (HTTP {response.status_code})"
    return result


def _probe_nlp(settings: Settings) -> dict[str, str]:
    registry = load_registry(settings.languages_root)
    result: dict[str, str] = {}
    for code, pack in sorted(registry.items()):
        if pack.spacy_model is None:
            result[code] = "n/a (целевой язык)"
        elif spacy_model_available(pack.spacy_model):
            result[code] = "ok"
        else:
            result[code] = f"error (нет пайплайна {pack.spacy_model})"
    return result


def _probe_epubcheck(settings: Settings) -> tuple[str, str]:
    jar = settings.epubcheck_jar_path
    if not jar.is_file():
        return "error", f"jar не найден: {jar}"
    if shutil.which("java") is None:
        return "error", "java не найдена в PATH"
    return "ok", ""


def health_report(settings: Settings, repo: JobRepository) -> dict[str, object]:
    """Собранное покомпонентное состояние (единая точка для /api/health)."""
    db_status, db_detail = _probe_database(repo)
    storage_status, storage_detail = _probe_storage(settings)
    epub_status, epub_detail = _probe_epubcheck(settings)
    report: dict[str, object] = {
        "app": "ok",
        "database": db_status,
        "storage": storage_status,
        "llm": _probe_llm(settings),
        "epubcheck": epub_status,
        "nlp": _probe_nlp(settings),
    }
    details: dict[str, str] = {}
    if db_detail:
        details["database"] = db_detail
    if storage_detail:
        details["storage"] = storage_detail
    if epub_detail:
        details["epubcheck"] = epub_detail
    if details:
        report["detail"] = details
    return report
