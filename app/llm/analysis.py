"""Клиент анализа (тракт A, E4.4): структурированный ответ, валидация по
JSON-схеме задачи, ограниченные повторы, полное сохранение сырых ответов.

- Промпт разрешается внешним резолвером (пара → язык → generic); фактически
  применённый файл + версия записываются в задание (E4-2, E4.5).
- Ответ модели валидируется по JSON-схеме задачи (jsonschema). Повторы
  ограничены: profile.retry_times (или явное `attempts`).
- Каждый сырой ответ (включая сетевые сбои) сохраняется в `jobs/<job_id>/raw/`.
- Невалидный ответ после исчерпания повторов — ошибка задачи
  (AnalysisTaskError); пустой артефакт НЕ создаётся (E4-4).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx
import jsonschema

from app.core.config import Settings
from app.core.errors import AppError
from app.llm.prompts import PromptResolver, ResolvedPrompt
from app.llm.tasks import TaskSpec
from app.storage.jobs_repo import JobRepository

logger = logging.getLogger("app.llm.analysis")

#: Системный промпт анализа начинается с этой метки (mock классифицирует kind по ней).
ANALYSIS_SYSTEM_MARKER = "You are an analysis engine"


class AnalysisTaskError(AppError):
    """Ошибка задачи: после исчерпания повторов ответ невалиден (E4-4)."""


@dataclass(frozen=True)
class TaskUsage:
    """Собственные счётчики тракта A (E4.7): из usage ответа OpenAI-эндпоинта."""

    input_tokens: int
    input_cache_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class TaskResult:
    task: str
    payload: dict[str, Any]
    prompt: ResolvedPrompt
    attempts: int
    usage: TaskUsage
    artifact: str


@dataclass(frozen=True)
class _RawAttempt:
    """Съёмка одной попытки (для сырого сохранения, E4.4)."""

    status: int | None
    body: dict[str, Any] | None
    error: str | None
    request_body: dict[str, Any]


@dataclass(frozen=True)
class _TaskContext:
    """Контекст задачи для артефакта (E4-4): что, каким промптом, куда."""

    spec: TaskSpec
    prompt: ResolvedPrompt
    source_lang: str
    target_lang: str


class _UsageAccumulator:
    """Накопительные счётчики токенов (E4.7): затрачены даже на неудачных попытках."""

    def __init__(self, repo: JobRepository, job_id: str, profile: str) -> None:
        self._repo = repo
        self._job_id = job_id
        self._profile = profile
        self._in = 0
        self._cache = 0
        self._out = 0

    def add(self, usage: TaskUsage) -> None:
        self._in += usage.input_tokens
        self._cache += usage.input_cache_tokens
        self._out += usage.output_tokens
        self._repo.add_tokens(self._job_id, self._profile, usage.total)
        self._repo.add_profile_tokens(
            self._job_id,
            self._profile,
            usage.input_tokens,
            usage.input_cache_tokens,
            usage.output_tokens,
        )

    def current(self) -> TaskUsage:
        return TaskUsage(self._in, self._cache, self._out)


def _usage_of(body: dict[str, Any]) -> TaskUsage:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return TaskUsage(0, 0, 0)
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, dict):
        details = {}
    return TaskUsage(
        input_tokens=int(usage.get("prompt_tokens") or 0),
        input_cache_tokens=int(details.get("cached_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
    )


def _extract_json(content: str) -> Any:
    """JSON из ответа модели; допускает markdown-ограду ```json ... ```."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ответ модели не является JSON: {exc}") from exc


class AnalysisClient:
    def __init__(
        self,
        settings: Settings,
        repo: JobRepository,
        job_id: str,
        *,
        resolver: PromptResolver | None = None,
        attempts: int | None = None,
    ) -> None:
        self._settings = settings
        self._repo = repo
        self._job_id = job_id
        self._resolver = resolver or PromptResolver(settings.prompts_dir)
        self._attempts = attempts
        self._job_dir = settings.jobs_dir / job_id
        self._raw_dir = self._job_dir / "raw"

    # ------------------------------------------------------------------ публичное

    def run_task(
        self,
        spec: TaskSpec,
        source_lang: str,
        target_lang: str,
        user_payload: str,
    ) -> TaskResult:
        profile = self._settings.llm.profile("analysis")
        base_url, model, api_key = self._settings.llm.endpoint("analysis")
        prompt = self._resolver.resolve(
            spec.name, source_lang, target_lang, pair_required=spec.pair_required
        )
        # E4-2: фактически применённый промпт (файл + версия) записан в задание.
        self._repo.record_resolved_prompt(
            self._job_id, spec.name, prompt.file, prompt.version, prompt.level
        )

        total_attempts = self._attempts if self._attempts is not None else profile.retry_times
        url = f"{base_url.rstrip('/')}/chat/completions"
        user_prompt = prompt.user_template.format(
            text=user_payload, source_language=source_lang, target_language=target_lang
        )
        request_body: dict[str, Any] = {
            "model": model,
            "temperature": profile.temperature,
            # не-streaming: один JSON-ответ с usage (как у OpenAI при stream: false)
            "stream": False,
            "messages": [
                {"role": "system", "content": self._system_prompt(spec, prompt)},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        usage_acc = _UsageAccumulator(self._repo, self._job_id, "analysis")
        last_problem = "попыток не было"
        with httpx.Client(timeout=profile.timeout_seconds) as client:
            for attempt in range(1, total_attempts + 1):
                attempt_info = self._http_attempt(client, url, request_body, headers)
                self._save_raw(spec.name, attempt, attempt_info)
                if attempt_info.error is not None:
                    last_problem = attempt_info.error
                    continue
                if attempt_info.status != 200 or attempt_info.body is None:
                    last_problem = f"HTTP {attempt_info.status} (ответ не является JSON)"
                    continue
                usage_acc.add(_usage_of(attempt_info.body))
                parsed, problem = self._parse_and_validate(attempt_info.body, spec)
                if parsed is None:
                    last_problem = problem
                    continue
                logger.info(
                    "задача %s: успех за %d попыток(и), токенов %d",
                    spec.name,
                    attempt,
                    usage_acc.current().total,
                )
                context = _TaskContext(spec, prompt, source_lang, target_lang)
                return self._finish(context, parsed, attempt, usage_acc.current())
        raise AnalysisTaskError(
            f"задача {spec.name} не выполнена: после {total_attempts} попыток ответ невалиден "
            f"(последняя проблема: {last_problem}); пустой артефакт не создаётся (E4-4)"
        )

    # ------------------------------------------------------------------ внутреннее

    @staticmethod
    def _system_prompt(spec: TaskSpec, prompt: ResolvedPrompt) -> str:
        schema = json.dumps(spec.schema, ensure_ascii=False, indent=2)
        return (
            f"{ANALYSIS_SYSTEM_MARKER}. Task: {spec.name}. "
            "Return a JSON object that strictly matches this schema:\n"
            f"{schema}\n\n{prompt.system}"
        )

    @staticmethod
    def _http_attempt(
        client: httpx.Client,
        url: str,
        request_body: dict[str, Any],
        headers: dict[str, str],
    ) -> _RawAttempt:
        """Одна HTTP-попытка: (статус, тело, сетевая ошибка)."""
        try:
            response = client.post(url, json=request_body, headers=headers)
        except httpx.HTTPError as exc:
            return _RawAttempt(None, None, f"{type(exc).__name__}: {exc}", request_body)
        status = response.status_code
        try:
            body: Any = response.json()
        except ValueError:
            body = None
        body_out: dict[str, Any] | None = body if isinstance(body, dict) else None
        return _RawAttempt(status, body_out, None, request_body)

    @staticmethod
    def _parse_and_validate(
        body: dict[str, Any], spec: TaskSpec
    ) -> tuple[dict[str, Any] | None, str]:
        """(скачанный payload, проблема). payload None — попытка невалидна (повтор)."""
        choices = body.get("choices")
        content: Any = (
            choices[0].get("message", {}).get("content")
            if isinstance(choices, list) and choices
            else None
        )
        if not isinstance(content, str):
            return None, "в ответе нет choices[0].message.content (строка)"
        try:
            parsed = _extract_json(content)
        except ValueError as exc:
            return None, str(exc)
        try:
            jsonschema.validate(instance=parsed, schema=spec.schema)
        except jsonschema.ValidationError as exc:
            return None, f"ответ не соответствует JSON-схеме задачи: {exc.message}"
        return parsed, ""

    def _save_raw(self, task_name: str, attempt: int, attempt_info: _RawAttempt) -> None:
        """Полный сырой ответ каждой попытки (E4.4: аудит после завершения)."""
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "task": task_name,
            "attempt": attempt,
            "http_status": attempt_info.status,
            "error": attempt_info.error,
            "request": attempt_info.request_body,
            "response": attempt_info.body,
        }
        path = self._raw_dir / f"{task_name}_attempt_{attempt}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    def _finish(
        self,
        context: _TaskContext,
        payload: dict[str, Any],
        attempts: int,
        usage: TaskUsage,
    ) -> TaskResult:
        """Артефакт задачи (создаётся только при валидном ответе, E4-4)."""
        spec = context.spec
        prompt = context.prompt
        artifact_dir = self._job_dir / "analysis"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact = artifact_dir / f"{spec.name}.json"
        artifact.write_text(
            json.dumps(
                {
                    "task": spec.name,
                    "source_language": context.source_lang,
                    "target_language": context.target_lang,
                    "prompt": {
                        "file": prompt.file,
                        "version": prompt.version,
                        "level": prompt.level,
                    },
                    "attempts": attempts,
                    "usage": {
                        "input": usage.input_tokens,
                        "input_cache": usage.input_cache_tokens,
                        "output": usage.output_tokens,
                    },
                    "payload": payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return TaskResult(
            task=spec.name,
            payload=payload,
            prompt=prompt,
            attempts=attempts,
            usage=usage,
            artifact=str(artifact),
        )
