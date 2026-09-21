"""Модель задания (E3): статусы и явная таблица допустимых переходов (E3.2).

Машина состояний — из ТЗ §E3 (+ L3, ТЗ_02 §L3; + LEARNING, ТЗ_03):
    CREATED → VALIDATING → [NEEDS_CONFIRMATION →] PLANNING → TRANSLATING
    → ANALYZING → GENERATING → VALIDATING_OUTPUT → GLOSSARY_FIX → LEARNING
    → COMPLETED | COMPLETED_WITH_WARNINGS | FAILED
GLOSSARY_FIX (L3) — правка перечисленных расхождений глоссария в выходном
EPUB; стадия всегда существует (при выключенной правке — no-op, T-L3-10).
LEARNING (S1–S5, ТЗ_03) — финальный рабочий этап: learning-материалы по
тексту перевода ПОСЛЕ L3; стадия всегда существует (learning не включён —
no-op). Из любого рабочего состояния — CANCELLED / PAUSED / INTERRUPTED;
INTERRUPTED/PAUSED → (resume) → состояние, с которого прервались
(состояние проверяется: только сохранённый interrupted_from/paused_from).
Переход не из таблицы — ошибка (JobStateError), а не молчаливое присвоение.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class JobStatus(StrEnum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    PLANNING = "PLANNING"
    TRANSLATING = "TRANSLATING"
    ANALYZING = "ANALYZING"
    GENERATING = "GENERATING"
    VALIDATING_OUTPUT = "VALIDATING_OUTPUT"
    #: L3: правка перечисленных расхождений глоссария в выходном EPUB (ТЗ_02 §L3).
    GLOSSARY_FIX = "GLOSSARY_FIX"
    #: S1–S5 (ТЗ_03): learning-материалы по тексту перевода ПОСЛЕ L3; финальный
    #: рабочий этап — терминальный статус по итогам стадии.
    LEARNING = "LEARNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"
    INTERRUPTED = "INTERRUPTED"


#: Состояния, в которых задание выполняется воркером (держит арену, E3.4).
WORKING_STATES: frozenset[JobStatus] = frozenset(
    {
        JobStatus.VALIDATING,
        JobStatus.PLANNING,
        JobStatus.TRANSLATING,
        JobStatus.ANALYZING,
        JobStatus.GENERATING,
        JobStatus.VALIDATING_OUTPUT,
        JobStatus.GLOSSARY_FIX,
        JobStatus.LEARNING,
    }
)
#: Терминальные состояния (переходов из них нет).
TERMINAL_STATES: frozenset[JobStatus] = frozenset(
    {
        JobStatus.COMPLETED,
        JobStatus.COMPLETED_WITH_WARNINGS,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }
)
#: Состояния, которые воркер может занять (PAUSED — только по команде resume).
CLAIMABLE_STATES: frozenset[JobStatus] = frozenset(
    {JobStatus.CREATED, JobStatus.INTERRUPTED, JobStatus.PAUSED}
)

#: Следующее состояние на «нормальном» пути конвейера (E3: E5/E6/L1/S дополнят суть).
NEXT_STAGE: Mapping[JobStatus, JobStatus] = {
    JobStatus.VALIDATING: JobStatus.PLANNING,
    JobStatus.PLANNING: JobStatus.TRANSLATING,
    JobStatus.TRANSLATING: JobStatus.ANALYZING,
    JobStatus.ANALYZING: JobStatus.GENERATING,
    JobStatus.GENERATING: JobStatus.VALIDATING_OUTPUT,
    JobStatus.VALIDATING_OUTPUT: JobStatus.GLOSSARY_FIX,
    # S1–S5 (ТЗ_03): learning-материалы — по тексту перевода ПОСЛЕ L3.
    JobStatus.GLOSSARY_FIX: JobStatus.LEARNING,
}


def _controls(*extra: JobStatus) -> frozenset[JobStatus]:
    """Управляющие переходы из рабочего состояния + ядро таблицы."""
    return frozenset(
        set(extra)
        | {JobStatus.CANCELLED, JobStatus.PAUSED, JobStatus.INTERRUPTED}
    )


#: Явная таблица допустимых переходов (E3.2). Всё, чего нет здесь, — ошибка.
TRANSITIONS: Mapping[JobStatus, frozenset[JobStatus]] = {
    JobStatus.CREATED: frozenset(
        {JobStatus.VALIDATING, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.PAUSED}
    ),
    JobStatus.VALIDATING: _controls(
        JobStatus.NEEDS_CONFIRMATION, JobStatus.PLANNING, JobStatus.FAILED
    ),
    JobStatus.NEEDS_CONFIRMATION: frozenset(
        {JobStatus.PLANNING, JobStatus.CANCELLED, JobStatus.PAUSED}
    ),
    JobStatus.PLANNING: _controls(JobStatus.TRANSLATING, JobStatus.FAILED),
    JobStatus.TRANSLATING: _controls(JobStatus.ANALYZING, JobStatus.FAILED),
    JobStatus.ANALYZING: _controls(JobStatus.GENERATING, JobStatus.FAILED),
    JobStatus.GENERATING: _controls(JobStatus.VALIDATING_OUTPUT, JobStatus.FAILED),
    JobStatus.VALIDATING_OUTPUT: _controls(JobStatus.GLOSSARY_FIX, JobStatus.FAILED),
    # L3: после правки — LEARNING (S1–S5); GLOSSARY_FIX — не финальный этап,
    # как другие рабочие (halt L3.6 — FAILED, D63).
    JobStatus.GLOSSARY_FIX: _controls(JobStatus.LEARNING, JobStatus.FAILED),
    # S1–S5 (ТЗ_03): финальный рабочий этап — терминальный статус по итогам.
    JobStatus.LEARNING: _controls(
        JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_WARNINGS, JobStatus.FAILED
    ),
    # Resume: только в сохранённое *_from-состояние (проверяется в репозитории).
    JobStatus.INTERRUPTED: WORKING_STATES | {JobStatus.CANCELLED, JobStatus.PAUSED},
    JobStatus.PAUSED: WORKING_STATES | {JobStatus.CANCELLED},
    JobStatus.COMPLETED: frozenset(),
    JobStatus.COMPLETED_WITH_WARNINGS: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


#: Уровни learning-материалов (ТЗ_03): управляют сложностью композиций, а не
#: абсолютным фильтром (CEFR/уровень влияют на отбор, но не блокируют).
#: Спеллинг — из ТЗ_03 (первая буква заглавная).
LEARNING_LEVELS: tuple[str, ...] = ("Minimal", "Balanced", "Learning", "Intensive")


@dataclass(frozen=True)
class Job:
    """Снимок задания из БД (редактирование — только через JobRepository)."""

    id: str
    status: JobStatus
    source_path: str
    target_language: str
    #: Режим задания (E5.2): "bilingual" (APPEND_BLOCK по умолчанию) | "translation" (REPLACE).
    mode: str = "bilingual"
    priority: int = 0
    #: Серия R §11/§15: ручное место в очереди («Вперёд очереди»);
    #: воркер сортирует кандидатов по queue_rank DESC, created_at ASC.
    queue_rank: int = 0
    progress: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    confirmed_at: float | None = None
    interrupted_from: JobStatus | None = None
    paused_from: JobStatus | None = None
    resume_requested: bool = False
    cancel_requested: bool = False
    pause_requested: bool = False
    worker_pid: int | None = None
    lease_expires_at: float | None = None
    input_sha256: str = ""
    config_sha256: str = ""
    prompt_version: str = "v0"
    tokens_translation: int = 0
    tokens_fill: int = 0
    tokens_analysis: int = 0
    #: Инвариант (E4-5): tokens_total == tokens_translation + tokens_fill + tokens_analysis.
    tokens_total: int = 0
    #: Доля выровненных сегментов (E6.5) — в статусе задания; ниже порога
    #: (alignment.min_coverage) — COMPLETED_WITH_WARNINGS. None — не измерялась.
    alignment_coverage: float | None = None
    #: Подтверждённый исходный язык (L0): решение детектора + dc:language
    #: (L0.5). Ставится на стадии validating при принятии языка; None — не
    #: подтверждён (NEEDS_CONFIRMATION или джоб не дошёл до validating).
    source_language: str | None = None
    error_message: str | None = None
    #: Learning (S1–S5, ТЗ_03): уровень сложности материалов (LEARNING_LEVELS);
    #: None — learning не включён (стадия LEARNING существует, no-op).
    learning_level: str | None = None
    #: Learning (ТЗ_03): CEFR-уровень пользователя (A1..C2); влияет на отбор,
    #: не является абсолютным фильтром. None — не задан (не влияет).
    learning_cefr: str | None = None
