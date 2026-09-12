"""LLM-слой (E4): единая конфигурация профилей, внешние промпты, клиент
анализа (тракт A), стоимость.

Два клиента (E4.1): translation/fill — объекты `epub_translator.LLM`
(конструируются в pipeline из профилей); analysis — AnalysisClient (тракт A).
Второго места, где задаются адрес или модель, не существует.
"""

from __future__ import annotations

from app.llm.analysis import ANALYSIS_SYSTEM_MARKER, AnalysisClient, AnalysisTaskError, TaskResult
from app.llm.cost import format_cost, job_cost_summary
from app.llm.profiles import describe_profiles
from app.llm.prompts import LEVEL_GENERIC, LEVEL_PAIR, LEVEL_SOURCE, PromptError, PromptResolver
from app.llm.tasks import TaskSpec, all_tasks, task

__all__ = [
    "ANALYSIS_SYSTEM_MARKER",
    "LEVEL_GENERIC",
    "LEVEL_PAIR",
    "LEVEL_SOURCE",
    "AnalysisClient",
    "AnalysisTaskError",
    "PromptError",
    "PromptResolver",
    "TaskResult",
    "TaskSpec",
    "all_tasks",
    "describe_profiles",
    "format_cost",
    "job_cost_summary",
    "task",
]
