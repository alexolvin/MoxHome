"""Транспорт `completions` клиента тракта A (MTPE §4.9) — рядом с `chat`.

Второго клиента нет: это вторая транспортная ветвь того же клиента `app/llm/`.
Запрос — `POST {base_url}/completions`, поле `prompt` (заполненный шаблон
`mt_draft`); ответ — `choices[0].text` + `finish_reason` + `usage`. Повторы —
только транспортные отказы (таймаут, 5xx, обрыв); при `temperature: 0`
содержательный повтор запрещён (даёт тот же ответ). Прогрев (§4.6) — отдельный
запрос с `max_tokens: 1` и `warmup_timeout_s`; поле `model` ответа сверяется с
моделью снимка задания. Каждый сырой запрос/ответ — в `jobs/<job_id>/raw/`.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import AppError
from app.llm.analysis import TaskUsage, _usage_of, _UsageAccumulator
from app.storage.jobs_repo import JobRepository

logger = logging.getLogger("app.llm.mt")


class MTError(AppError):
    """Базовая ошибка транспорта `completions` (профиль mt)."""


class MTModelError(MTError):
    """Поле `model` ответа != модели снимка задания (M3 §7 п.7) — остановка."""


class MTWarmupError(MTError):
    """Отказ прогрева (MTPE §4.6): warmup_timeout / model_mismatch / сеть."""


@dataclass(frozen=True)
class MTResult:
    """Один рабочий запрос черновика: текст, модель, причина останова, метрика."""

    text: str
    model: str
    finish_reason: str
    usage: TaskUsage
    attempts: int
    ms: int


@dataclass(frozen=True)
class _RawAttempt:
    status: int | None
    body: dict[str, Any] | None
    error: str | None
    request_body: dict[str, Any]


class MTCompletionsClient:
    def __init__(self, settings: Settings, repo: JobRepository, job_id: str) -> None:
        self._settings = settings
        self._repo = repo
        self._job_id = job_id
        self._job_dir = settings.job_dir(job_id)
        self._raw_dir = self._job_dir / "raw"
        self._profile = settings.llm.profile("mt")
        self._base_url, self._model, self._api_key = settings.llm.endpoint("mt")

    # ------------------------------------------------------------------ публичное

    def warmup(self, prompt: str) -> None:
        """Прогрев фазы (§4.6): `/completions`, `max_tokens: 1`, `warmup_timeout_s`.

        Отказ — MTWarmupError с причиной (`warmup_timeout` / `model_mismatch` /
        сетевая / HTTP). Результат прогрева на выход не влияет."""
        url = f"{self._base_url.rstrip('/')}/completions"
        body: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "max_tokens": 1,
            "temperature": 0,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            with httpx.Client(timeout=self._profile.warmup_timeout_s) as client:
                response = client.post(url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise MTWarmupError(
                "warmup_timeout",
                details={"profile": "mt", "timeout_s": self._profile.warmup_timeout_s},
            ) from exc
        except httpx.HTTPError as exc:
            raise MTWarmupError(
                "connection_error",
                details={"profile": "mt", "error": f"{type(exc).__name__}: {exc}"},
            ) from exc
        if response.status_code != 200:
            raise MTWarmupError("http", details={"profile": "mt", "status": response.status_code})
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise MTWarmupError("invalid_json", details={"profile": "mt"}) from exc
        model = payload.get("model") if isinstance(payload, dict) else None
        if model and model != self._model:
            raise MTWarmupError(
                "model_mismatch",
                details={"profile": "mt", "expected": self._model, "actual": model},
            )

    def complete(self, prompt: str, max_tokens: int, label: str) -> MTResult:
        """Один рабочий запрос черновика: повторы только на транспортные отказы.

        `model` ответа сверяется с моделью снимка (MTModelError — остановка).
        Сырой запрос/ответ каждой попытки — в `raw/`; токены — в учёт (profile=mt).
        """
        profile = self._profile
        total_attempts = profile.retries or 3
        url = f"{self._base_url.rstrip('/')}/completions"
        body: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "max_tokens": int(max_tokens),
            "temperature": profile.temperature,
            "stream": False,
        }
        if profile.top_k is not None:
            body["top_k"] = profile.top_k
        if profile.stop:
            body["stop"] = list(profile.stop)
        body.update(profile.extra_body or {})
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

        usage_acc = _UsageAccumulator(self._repo, self._job_id, "mt")
        start = time.monotonic()
        last_problem = "попыток не было"
        with httpx.Client(timeout=profile.timeout_s) as client:
            for attempt in range(1, total_attempts + 1):
                attempt_info = self._http_attempt(client, url, body, headers)
                self._save_raw(label, attempt, attempt_info)
                if attempt_info.error is not None:
                    last_problem = attempt_info.error  # транспорт: таймаут/обрыв
                    continue
                if attempt_info.status is not None and attempt_info.status >= 500:
                    last_problem = f"HTTP {attempt_info.status}"  # транспорт: 5xx
                    continue
                if attempt_info.status != 200 or attempt_info.body is None:
                    # 4xx / не-JSON — не транспортный отказ: повтор запрещён.
                    raise MTError(
                        f"mt completions: HTTP {attempt_info.status} (не транспортный повтор)",
                        details={"label": label, "status": attempt_info.status},
                    )
                usage_acc.add(_usage_of(attempt_info.body))
                result = self._parse(attempt_info.body, attempt, usage_acc.current(), start)
                if result.model and result.model != self._model:
                    raise MTModelError(
                        "model_mismatch",
                        details={"label": label, "expected": self._model, "actual": result.model},
                    )
                return result
        raise MTError(
            f"mt completions: после {total_attempts} попыток (последняя: {last_problem})",
            details={"label": label},
        )

    # ------------------------------------------------------------------ внутреннее

    @staticmethod
    def _http_attempt(
        client: httpx.Client, url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> _RawAttempt:
        try:
            response = client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            return _RawAttempt(None, None, f"{type(exc).__name__}: {exc}", body)
        status = response.status_code
        try:
            parsed: Any = response.json()
        except ValueError:
            parsed = None
        out: dict[str, Any] | None = parsed if isinstance(parsed, dict) else None
        return _RawAttempt(status, out, None, body)

    @staticmethod
    def _parse(
        body: dict[str, Any], attempt: int, usage: TaskUsage, start: float
    ) -> MTResult:
        choices = body.get("choices")
        text: Any = (
            choices[0].get("text")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else None
        )
        finish_reason: Any = (
            choices[0].get("finish_reason")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else None
        )
        if not isinstance(text, str):
            # Транспорт был успешен (200), но ответа нет — повтор бесполезен
            # (temperature 0), возвращаем пустой текст: draft_bad поставит флаг.
            return MTResult("", str(body.get("model", "")), "error", usage, attempt, _ms(start))
        return MTResult(
            text,
            str(body.get("model", "")),
            str(finish_reason or ""),
            usage,
            attempt,
            _ms(start),
        )

    def _save_raw(self, label: str, attempt: int, attempt_info: _RawAttempt) -> None:
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "profile": "mt",
            "label": label,
            "attempt": attempt,
            "http_status": attempt_info.status,
            "error": attempt_info.error,
            "request": attempt_info.request_body,
            "response": attempt_info.body,
        }
        path = self._raw_dir / f"mt_{label}_attempt_{attempt}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
