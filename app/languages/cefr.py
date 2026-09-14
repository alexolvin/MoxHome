"""CEFR-уровень (L1.4): источник — машиночитаемый лексикон либо вычисление.

- `cefr_source: lexicon:<имя>` — уровень из лексикона `config/languages/<code>/
  cefr/<имя>.json` (маппинг словоформа/лемма → уровень); UI — подпись
  `CEFR level (<источник>)`.
- `cefr_source: derived` — уровень вычисляется из частотной полосы + оценки LLM;
  UI — ОБЯЗАТЕЛЬНАЯ подпись `Estimated CEFR level`.
- Лексикон с неясной или запрещающей лицензией в поставку не включается (D6,
  Rule 2 — тихая подмена источника запрещена); аудит наличия/лицензий —
  docs/LIMITATIONS.md (L1-7).
- Файл декларированного лексикона отсутствует (T-L1-12) — язык корректно
  переходит в derived (метка меняется, падений нет); резолвер (L0.3)
  перечисляет отсутствующий файл, переход не молчаливый.
- Уровень CEFR никогда не подаётся как истина: влияет на отбор материала,
  но не блокирует его (TЗ L1 п.4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.errors import ConfigurationError
from app.languages.registry import PackSpec

#: CEFR-уровни по возрастанию (L1.4; S1: «вне диапазона» — дистанция по порядку).
CEFR_ORDER: tuple[str, ...] = ("A1", "A2", "B1", "B2", "C1", "C2")
#: Допустимые уровни CEFR (L1.4; валидация выбора пользователя — S1, ТЗ_03).
CEFR_LEVELS: frozenset[str] = frozenset(CEFR_ORDER)
_DERIVED = "derived"


@dataclass(frozen=True)
class CefrResult:
    """Результат определения CEFR-уровня. `level=None` — уровень не
    устанавливается (не подан как истина, L1.4)."""

    level: str | None
    #: Фактический источник: "derived" либо "lexicon:<имя>".
    source: str
    estimated: bool


class CefrSource:
    """CEFR-источник языка: декларация pack.yaml + фактический файл лексикона."""

    def __init__(self, pack: PackSpec, pack_dir: Path) -> None:
        self._pack = pack
        self._pack_dir = pack_dir
        self._lexicon: dict[str, str] | None = None
        if pack.cefr_source.startswith("lexicon:"):
            self._lexicon = self._load_lexicon(pack.cefr_source.split(":", 1)[1])

    def _load_lexicon(self, name: str) -> dict[str, str] | None:
        path = self._pack_dir / "cefr" / f"{name}.json"
        if not path.is_file():
            return None  # T-L1-12: переход в derived — без исключения
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"CEFR-лексикон не является JSON: {path}: {exc}") from exc
        if not isinstance(data, dict) or not data:
            raise ConfigurationError(f"CEFR-лексикон должен быть непустым JSON-объектом: {path}")
        for word, level in data.items():
            if level not in CEFR_LEVELS:
                raise ConfigurationError(
                    f"CEFR-лексикон {path}: недопустимый уровень {level!r} для {word!r}",
                    details={"allowed": list(CEFR_ORDER)},
                )
        return data

    @property
    def source_name(self) -> str:
        """Декларированный источник: "derived" либо "lexicon:<имя>"."""
        return self._pack.cefr_source

    @property
    def effective_source(self) -> str:
        """Фактический источник: декларация; лексикон без файла — derived (T-L1-12)."""
        if self._lexicon is not None:
            return self._pack.cefr_source
        return _DERIVED

    @property
    def lexicon_available(self) -> bool:
        """Лексикон объявлен И файл на месте (для перечня резолвера, L0.3)."""
        return self._lexicon is not None

    def level_for(self, word: str) -> CefrResult:
        """Уровень словоформы/леммы. Слово вне лексикона — уровень не
        устанавливается (не подан как истина); declared=derived — всегда
        estimated."""
        if self._lexicon is not None:
            level = self._lexicon.get(word) or self._lexicon.get(word.casefold())
            if level is not None:
                return CefrResult(level, self._pack.cefr_source, estimated=False)
            return CefrResult(None, self._pack.cefr_source, estimated=False)
        return CefrResult(None, _DERIVED, estimated=True)


def label(result: CefrResult) -> str:
    """ЕДИНАЯ подпись для отображения (L1-8): `derived` — обязательная
    `Estimated CEFR level`; лексикон — `CEFR level (<источник>)`. UI (U1)
    читает только её — «100% мест отображения» обеспечено единством точки."""
    if result.estimated:
        suffix = f" ({result.level})" if result.level is not None else ""
        return f"Estimated CEFR level{suffix}"
    level = result.level if result.level is not None else "не установлен"
    return f"CEFR level ({result.source}): {level}"


def language_cefr_label(pack: PackSpec) -> str:
    """Подпись ЯЗЫКА в разделах UI (U1.4/U1-5): классификация та же, что у
    ``label`` — `derived` (фактически) → `Estimated CEFR level`; лексикон →
    `CEFR level (lexicon:<имя>)`. Уровень слова здесь не показывается —
    это свойство ``label``, а не языка."""
    if pack.cefr_source == _DERIVED:
        return "Estimated CEFR level"
    return f"CEFR level ({pack.cefr_source})"
