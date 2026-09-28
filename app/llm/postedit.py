"""Транспорт постредактуры (MTPE M4, §4.2) — ветвь `chat` того же клиента `app/llm/`.

Второго клиента нет (§4.9): это третья ветвь клиента тракта A рядом с `chat`
(анализ) и `completions` (черновик, `mt.py`). Запрос — `POST {base_url}/chat/completions`
с системным промптом (схема JSON + правила §4.2) и пользовательским сообщением (§4.2,
6 блоков). Ответ — `choices[0].message.content` (JSON `{text, edits}`); валидация по
схеме и жёсткие проверки H1–H7 — в фазе (лестница §4.6), клиент только транспорт.

Прогрев (§4.6) — `max_tokens: 1`, `warmup_timeout_s`; поле `model` сверяется с моделью
снимка задания (`model_mismatch` — остановка). Повторы — только транспортные отказы
(таймаут, 5xx, обрыв); при `temperature: 0` содержательный повтор запрещён. Сырой запрос/
ответ каждой попытки — в `jobs/<job_id>/raw/`; токены — в учёт `profile=postedit`.
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

logger = logging.getLogger("app.llm.postedit")


class PostEditError(AppError):
    """Базовая ошибка транспорта постредактуры (профиль postedit)."""


class PostEditModelError(PostEditError):
    """Поле `model` ответа != модели снимка задания (§4.6) — остановка."""


class PostEditWarmupError(PostEditError):
    """Отказ прогрева (§4.6): warmup_timeout / model_mismatch / сеть."""


@dataclass(frozen=True)
class PostEditResult:
    """Один рабочий запрос: сырой вывод модели (JSON-строка), модель, метрика."""

    content: str
    model: str
    attempts: int
    usage: TaskUsage
    ms: int


@dataclass(frozen=True)
class _RawAttempt:
    status: int | None
    body: dict[str, Any] | None
    error: str | None
    request_body: dict[str, Any]


class PostEditClient:
    def __init__(self, settings: Settings, repo: JobRepository, job_id: str) -> None:
        self._settings = settings
        self._repo = repo
        self._job_id = job_id
        self._job_dir = settings.job_dir(job_id)
        self._raw_dir = self._job_dir / "raw"
        self._profile = settings.llm.profile("postedit")
        self._base_url, self._model, self._api_key = settings.llm.endpoint("postedit")

    # ------------------------------------------------------------------ публичное

    def warmup(self, prompt: str) -> None:
        """Прогрев фазы (§4.6): `chat/completions`, `max_tokens: 1`, `warmup_timeout_s`."""
        url = f"{self._base_url.rstrip('/')}/chat/completions"
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": "warmup"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 1,
            "temperature": 0,
            "stream": False,
        }
        body.update(self._profile.extra_body or {})
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            with httpx.Client(timeout=self._profile.warmup_timeout_s) as client:
                response = client.post(url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise PostEditWarmupError(
                "warmup_timeout",
                details={"profile": "postedit", "timeout_s": self._profile.warmup_timeout_s},
            ) from exc
        except httpx.HTTPError as exc:
            raise PostEditWarmupError(
                "connection_error",
                details={"profile": "postedit", "error": f"{type(exc).__name__}: {exc}"},
            ) from exc
        if response.status_code != 200:
            raise PostEditWarmupError(
                "http", details={"profile": "postedit", "status": response.status_code}
            )
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise PostEditWarmupError("invalid_json", details={"profile": "postedit"}) from exc
        model = payload.get("model") if isinstance(payload, dict) else None
        if model and model != self._model:
            raise PostEditWarmupError(
                "model_mismatch",
                details={"profile": "postedit", "expected": self._model, "actual": model},
            )

    def complete(self, system_prompt: str, user_prompt: str, label: str) -> PostEditResult:
        """Один рабочий запрос: повторы только на транспортные отказы. `content` —
        сырой вывод модели (JSON-строка); валидация и жёсткие проверки — в фазе."""
        profile = self._profile
        total_attempts = profile.retries or 3
        url = f"{self._base_url.rstrip('/')}/chat/completions"
        body: dict[str, Any] = {
            "model": self._model,
            "temperature": profile.temperature,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        body.update(profile.extra_body or {})
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

        usage_acc = _UsageAccumulator(self._repo, self._job_id, "postedit")
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
                    raise PostEditError(
                        f"postedit: HTTP {attempt_info.status} (не транспортный повтор)",
                        details={"label": label, "status": attempt_info.status},
                    )
                usage_acc.add(_usage_of(attempt_info.body))
                content, model = self._content_of(attempt_info.body)
                if model and model != self._model:
                    raise PostEditModelError(
                        "model_mismatch",
                        details={"label": label, "expected": self._model, "actual": model},
                    )
                return PostEditResult(
                    content, model, attempt, usage_acc.current(), _ms(start)
                )
        raise PostEditError(
            f"postedit: после {total_attempts} попыток (последняя: {last_problem})",
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
    def _content_of(body: dict[str, Any]) -> tuple[str, str]:
        """(message.content, model). Нет строки — пустая (H1 отклонит, лестница решит)."""
        choices = body.get("choices")
        content: Any = (
            choices[0].get("message", {}).get("content")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else None
        )
        if not isinstance(content, str):
            content = ""
        return content, str(body.get("model", ""))

    def _save_raw(self, label: str, attempt: int, attempt_info: _RawAttempt) -> None:
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "profile": "postedit",
            "label": label,
            "attempt": attempt,
            "http_status": attempt_info.status,
            "error": attempt_info.error,
            "request": attempt_info.request_body,
            "response": attempt_info.body,
        }
        path = self._raw_dir / f"pe_{label}_attempt_{attempt}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
