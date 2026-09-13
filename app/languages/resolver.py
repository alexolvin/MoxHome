"""Резолвер пар языков (L0.2): статус вычисляется по фактически доступным ресурсам.

Матрица (`config/language_matrix.yaml`) объявляет НАМЕРЕНИЕ; статус — по ресурсам
(формулировки — из ТЗ L0 п.2):
- **supported** — всё на месте: пакеты source+target, spaCy-модель источника
  (объявлена и установлена), источник частотности, CEFR объявлен, prompt-файлы
  пары, фикстура и golden-набор;
- **degraded** — «пакет исходного языка есть, промптов пары нет → откат на
  source→generic, задание разрешено, но UI и отчёт помечают пару как degraded»
  (то же для частотности/фикстуры/golden; для pair_required-задачи без pair-уровня
  добавлено прямое указание: на этапе анализа будет ошибка, E4.3 — тихого fallback
  нет);
- **unsupported** — «нет пакета исходного языка либо нет spaCy-модели → задание не
  создаётся» (L0.2) + расширение D38: нет пакета ЦЕЛЕВОГО языка — пара не
  исполнима вовсе (L2 требует парадигму целевого языка), джоб не создаётся (L0.7).

«Пара не поддерживается» без перечня недостающих ресурсов — недопустимый ответ
(L0.3): поле `missing` перечисляет ТОЧНО, чего не хватает.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import spacy
import wordfreq
import yaml

from app.core.errors import ConfigurationError
from app.languages.registry import PackSpec, load_golden, load_registry
from app.llm.prompts import PromptError, PromptResolver
from app.llm.tasks import all_tasks


class PairStatus(StrEnum):
    SUPPORTED = "supported"
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class TaskPromptInfo:
    """Уровень разрешения промпта задачи для пары (E4.3): pair / source / generic."""

    task: str
    level: str
    version: int | None


@dataclass(frozen=True)
class PairReport:
    """Статус пары (L0.2/L0.3): `missing` пуст только для supported."""

    source: str
    target: str
    status: PairStatus
    #: Точный перечень недостающих ресурсов (L0.3).
    missing: tuple[str, ...]
    #: Уровни промптов по задачам (E4: pair → language → generic).
    prompts: tuple[TaskPromptInfo, ...]

    @property
    def creatable(self) -> bool:
        """Джоб можно создать (L0.2): всё, кроме unsupported."""
        return self.status is not PairStatus.UNSUPPORTED


def spacy_model_available(model: str) -> bool:
    """spaCy-модель установлена (единая точка проверки; L0-4: шов для симуляции удаления)."""
    return bool(spacy.util.is_package(model))


def frequency_source_available(source: str, code: str) -> bool:
    """Источник частотности доступен для языка. Неизвестный источник — False (Rule 6)."""
    if source != "wordfreq":
        return False
    return code in wordfreq.available_languages()


def load_matrix(path: Path) -> tuple[tuple[str, str], ...]:
    """Матрица пар (L0.2). Неверная структура/дубли — ConfigurationError (Rule 6)."""
    if not path.is_file():
        raise ConfigurationError(
            f"матрица пар языков не найдена: {path} (config/language_matrix.yaml)",
            details={"path": str(path)},
        )
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"не удалось разобрать матрицу {path}: {exc}") from exc
    pairs_data = data.get("pairs") if isinstance(data, dict) else None
    if not isinstance(pairs_data, list) or not pairs_data:
        raise ConfigurationError(f"матрица {path}: 'pairs' должен быть непустым списком")
    pairs: list[tuple[str, str]] = []
    for entry in pairs_data:
        if not isinstance(entry, dict):
            raise ConfigurationError(f"матрица {path}: запись пары должна быть mapping")
        source, target = entry.get("source"), entry.get("target")
        if not isinstance(source, str) or not source.strip():
            raise ConfigurationError(
                f"матрица {path}: 'source' должен быть непустым кодом", details={"entry": entry}
            )
        if not isinstance(target, str) or not target.strip():
            raise ConfigurationError(
                f"матрица {path}: 'target' должен быть непустым кодом", details={"entry": entry}
            )
        pair = (source.strip().lower(), target.strip().lower())
        if pair in pairs:
            raise ConfigurationError(f"матрица {path}: дублирующаяся пара: {pair[0]}→{pair[1]}")
        pairs.append(pair)
    return tuple(pairs)


def _source_pack_deficits(
    src_pack: PackSpec | None,
    source: str,
    spacy_available: Callable[[str], bool],
    registry_root: Path,
    project_root: Path,
) -> tuple[list[str], list[str]]:
    """Недостатки пакета исходного языка: (жёсткие, мягкие) — точные сообщения (L0.3)."""
    hard: list[str] = []
    soft: list[str] = []
    if src_pack is None:
        hard.append(f"нет пакета исходного языка: {source}/pack.yaml")
        return hard, soft
    if src_pack.spacy_model is None:
        hard.append("для исходного языка не объявлена spaCy-модель (pack.yaml: spacy_model)")
    elif not spacy_available(src_pack.spacy_model):
        hard.append(f"spaCy-модель не установлена: {src_pack.spacy_model}")
    if not frequency_source_available(src_pack.frequency_source, source):
        soft.append(f"источник частотности недоступен: {src_pack.frequency_source} (язык {source})")
    if not src_pack.fixture:
        soft.append("нет фикстуры исходного языка (pack.yaml: fixture)")
    elif not (project_root / src_pack.fixture).is_file():
        soft.append(f"фикстура не найдена: {src_pack.fixture}")
    if not load_golden(registry_root / source):
        soft.append(f"нет golden-набора исходного языка: {source}/golden.yaml")
    return hard, soft


def _prompt_deficits(
    prompt_resolver: PromptResolver, source: str, target: str
) -> tuple[list[str], list[TaskPromptInfo]]:
    """Недостатки промптов пары — мягкие (L0.2: «промптов пары нет → задание разрешено»).

    pair_required-задача без pair-уровня не молчит (E4.3): сообщение прямо
    указывает, что на этапе анализа будет ошибка — тихого fallback нет.
    """
    missing: list[str] = []
    prompts: list[TaskPromptInfo] = []
    for task in all_tasks():
        try:
            resolved = prompt_resolver.resolve(
                task.name, source, target, pair_required=task.pair_required
            )
        except PromptError:
            suffix = (
                " (pair_required: ошибка на этапе анализа, E4.3 — тихого fallback нет)"
                if task.pair_required
                else ""
            )
            message = f"промпт задачи {task.name} не резолвится (пара {source}/{target}){suffix}"
            missing.append(message)
            prompts.append(TaskPromptInfo(task.name, "missing", None))
            continue
        prompts.append(TaskPromptInfo(task.name, resolved.level, resolved.version))
        if resolved.level != "pair":
            missing.append(
                f"нет pair-промпта задачи {task.name} (пара {source}/{target}) "
                f"— fallback на уровень {resolved.level}"
            )
    return missing, prompts


class PairResolver:
    """Статус пары по фактическим ресурсам (L0.2), точный перечень недостающих (L0.3).

    `spacy_available` — шов (L0-4): симуляция удаления модели без её фактического
    удаления (evidence/тесты инлайнят lambda; прода — спаци-утилиты).
    """

    def __init__(
        self,
        *,
        registry_root: Path,
        prompts_root: Path,
        spacy_available: Callable[[str], bool] = spacy_model_available,
    ) -> None:
        self._registry_root = registry_root
        self._registry: dict[str, PackSpec] = load_registry(registry_root)
        self._prompt_resolver = PromptResolver(prompts_root)
        self._spacy_available = spacy_available

    @property
    def registry(self) -> dict[str, PackSpec]:
        return self._registry

    def resolve_pair(self, source: str, target: str) -> PairReport:
        source = source.strip().lower()
        target = target.strip().lower()
        src_pack = self._registry.get(source)

        hard: list[str] = []
        missing: list[str] = []
        if target not in self._registry:
            message = f"нет пакета целевого языка: {target}/pack.yaml"
            hard.append(message)
            missing.append(message)
        hard_src, soft_src = _source_pack_deficits(
            src_pack, source, self._spacy_available, self._registry_root, self._project_root()
        )
        hard.extend(hard_src)
        missing.extend(hard_src)
        missing.extend(soft_src)
        prompt_missing, prompts = _prompt_deficits(self._prompt_resolver, source, target)
        missing.extend(prompt_missing)

        if hard:
            status = PairStatus.UNSUPPORTED
        elif missing:
            status = PairStatus.DEGRADED
        else:
            status = PairStatus.SUPPORTED
        return PairReport(source, target, status, tuple(missing), tuple(prompts))

    def _project_root(self) -> Path:
        """Корень проекта — два уровня выше корня реестра (layout TZ: <root>/config/languages)."""
        return self._registry_root.resolve().parent.parent
