# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Внешние промпты (E4.5): `prompts/<task>/<source>/<target>/vN.yaml`.

Порядок разрешения: пара (source/target) → исходный язык → generic.
Версия уровня: наибольший N среди vN.yaml. Фактически применённый файл и
его версия записываются в задание (100% задач, E4-2) — делает клиент анализа.

`pair_required: true` — парный промпт ОБЯЗАТЕЛЕН: без него ошибка (E4-3),
тихий откат на generic (и на уровень языка) запрещён.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.errors import ConfigurationError

#: Имя файла промпта: vN.yaml, N — целая версия. ЕДИНЫЙ источник: резолвер,
#: web-слой (``app.web.views.prompts_view``) и запись новых версий
#: (``app.web.api``, U3 3.7).
VERSION_FILE_RE = re.compile(r"^v(\d+)\.yaml$")

LEVEL_PAIR = "pair"
LEVEL_SOURCE = "source"
LEVEL_GENERIC = "generic"


class PromptError(ConfigurationError):
    """Промпт не найден / нарушен pair_required: задача не может быть исполнена."""


@dataclass(frozen=True)
class ResolvedPrompt:
    """Разрешённый промпт: файл, версия, уровень, содержимое."""

    task: str
    file: str  # путь, относительный от корня промптов
    version: int
    level: str  # pair | source | generic
    system: str
    user_template: str


def _parse_prompt_file(path: Path) -> tuple[int, str, str]:
    match = VERSION_FILE_RE.match(path.name)
    if match is None:
        raise PromptError(f"недопустимое имя файла промпта (нужно vN.yaml): {path.name}")
    version = int(match.group(1))
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PromptError(f"не удалось разобрать YAML промпта {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PromptError(f"корень файла промпта должен быть mapping: {path}")
    system = data.get("system")
    user_template = data.get("user_template")
    if not isinstance(system, str) or not system.strip():
        raise PromptError(f"в промпте нет 'system' (непустая строка): {path}")
    if not isinstance(user_template, str) or not user_template.strip():
        raise PromptError(f"в промпте нет 'user_template' (непустая строка): {path}")
    return version, system, user_template


def _best_version(directory: Path) -> Path | None:
    """Файл vN.yaml с наибольшим N в каталоге уровня (None — файлов нет)."""
    if not directory.is_dir():
        return None
    best: tuple[int, Path] | None = None
    for candidate in sorted(directory.iterdir()):
        match = VERSION_FILE_RE.match(candidate.name)
        if match is None:
            continue
        version = int(match.group(1))
        if best is None or version > best[0]:
            best = (version, candidate)
    return best[1] if best is not None else None


class PromptResolver:
    """Разрешает промпт задачи по (task, source_lang, target_lang)."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def resolve(
        self,
        task: str,
        source_lang: str,
        target_lang: str,
        *,
        pair_required: bool,
    ) -> ResolvedPrompt:
        if pair_required:
            # pair_required: только уровень пары; без него — ошибка (E4-3).
            path = _best_version(self._root / task / source_lang / target_lang)
            if path is None:
                raise PromptError(
                    f"задача {task} имеет pair_required=true, парного промпта нет: "
                    f"{task}/{source_lang}/{target_lang}/vN.yaml; тихий откат на "
                    "generic запрещён (E4-3)"
                )
            level = LEVEL_PAIR
        else:
            candidates: list[tuple[str, Path | None]] = [
                (LEVEL_PAIR, _best_version(self._root / task / source_lang / target_lang)),
                (LEVEL_SOURCE, _best_version(self._root / task / source_lang)),
                (LEVEL_GENERIC, _best_version(self._root / task)),
            ]
            level = ""
            path = None
            for candidate_level, candidate_path in candidates:
                if candidate_path is not None:
                    level, path = candidate_level, candidate_path
                    break
            if path is None:
                raise PromptError(
                    f"нет промпта задачи {task} ни на уровне пары, ни языка, ни generic "
                    f"(корень: {self._root})"
                )
        version, system, user_template = _parse_prompt_file(path)
        return ResolvedPrompt(
            task=task,
            file=str(path.relative_to(self._root)),
            version=version,
            level=level,
            system=system,
            user_template=user_template,
        )
