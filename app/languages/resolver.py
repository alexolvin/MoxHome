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
from app.languages.cefr import CefrSource
from app.languages.phenomena import load_phenomena
from app.languages.registry import PackSpec, load_golden, load_lemma_reference, load_registry
from app.llm.prompts import PromptError, PromptResolver
from app.llm.tasks import all_tasks
from app.llm.tasks import task as task_spec


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


def mt_pe_supported(
    registry: dict[str, PackSpec], source: str, target: str
) -> bool:
    """Статус способа mt_pe для пары (MTPE TZ §5.5): `supported`, когда
    `mt_name` задан у обоих языков; иначе `unsupported`. Статуса degraded
    у mt_pe нет. Единая точка: резолвер и validate_llm_settings V10."""
    src_pack = registry.get(source.strip().lower())
    tgt_pack = registry.get(target.strip().lower())
    return bool(src_pack and tgt_pack and src_pack.mt_name and tgt_pack.mt_name)


def _l1_resource_deficits(src_pack: PackSpec, pack_dir: Path) -> list[str]:
    """L1-ресурсы пакета (мягкие): наличие каждого ресурса проверяется отдельно,
    отсутствие каждого — своё точное сообщение (L0.3)."""
    soft: list[str] = []
    if not load_phenomena(pack_dir):
        soft.append(f"нет таксономии явлений: {src_pack.code}/phenomena.yaml (L1.5)")
    if not (pack_dir / src_pack.card_schema).is_file():
        soft.append(f"нет схемы карточки: {src_pack.code}/{src_pack.card_schema} (L1.6)")
    if not load_lemma_reference(pack_dir):
        soft.append(
            f"нет эталонного набора лемматизации: {src_pack.code}/lemma_reference.yaml (L1-2)"
        )
    if not src_pack.frequency_bands:
        soft.append(f"нет частотных полос: {src_pack.code}/pack.yaml (frequency_bands, L1.3)")
    if (
        src_pack.cefr_source.startswith("lexicon:")
        and not CefrSource(src_pack, pack_dir).lexicon_available
    ):
        name = src_pack.cefr_source.split(":", 1)[1]
        soft.append(
            f"CEFR-лексикон не найден: {src_pack.code}/cefr/{name}.json "
            f"(язык переходит в derived, L1.4/T-L1-12)"
        )
    return soft


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
    # L1: ресурсы языкового пакета — данные, отсутствие каждого — в перечне (L0.3).
    pack_dir = registry_root / source
    soft.extend(_l1_resource_deficits(src_pack, pack_dir))
    golden = load_golden(pack_dir)
    if not golden:
        soft.append(f"нет golden-набора исходного языка: {source}/golden.yaml")
    elif not any(entry.phenomena for entry in golden):
        soft.append(f"в golden-наборе нет размеченных явлений: {source}/golden.yaml (L1/T-L1-10)")
    return hard, soft


def _prompt_deficits(
    prompt_resolver: PromptResolver, source: str, target: str, tasks: tuple[str, ...]
) -> tuple[list[str], list[TaskPromptInfo]]:
    """Недостатки промптов пары — мягкие (L0.2: «промптов пары нет → задание разрешено»).

    Задачи — пространство имён промптов пакета исходного языка (pack.yaml:
    prompt_tasks — данные, L1.1); нет пакета/списка — все задачи реестра.
    pair_required-задача без pair-уровня не молчит (E4.3): сообщение прямо
    указывает, что на этапе анализа будет ошибка — тихого fallback нет.
    """
    missing: list[str] = []
    prompts: list[TaskPromptInfo] = []
    for name in tasks:
        spec = task_spec(name)
        try:
            resolved = prompt_resolver.resolve(
                name, source, target, pair_required=spec.pair_required
            )
        except PromptError:
            suffix = (
                " (pair_required: ошибка на этапе анализа, E4.3 — тихого fallback нет)"
                if spec.pair_required
                else ""
            )
            message = f"промпт задачи {name} не резолвится (пара {source}/{target}){suffix}"
            missing.append(message)
            prompts.append(TaskPromptInfo(name, "missing", None))
            continue
        prompts.append(TaskPromptInfo(name, resolved.level, resolved.version))
        if resolved.level != "pair":
            missing.append(
                f"нет pair-промпта задачи {name} (пара {source}/{target}) "
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
        # Пространство имён промптов — данные пакета (L1.1); нет пакета — все задачи.
        tasks = (
            src_pack.prompt_tasks
            if src_pack is not None and src_pack.prompt_tasks
            else tuple(t.name for t in all_tasks())
        )
        prompt_missing, prompts = _prompt_deficits(self._prompt_resolver, source, target, tasks)
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
