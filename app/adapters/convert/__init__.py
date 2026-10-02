# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Адаптер инструмента конвертации (TZ-R §8, R6): Calibre ``ebook-convert``
в Podman-контейнере без сети (D110).

Два примитива:

- ``probe`` — проба наличия: ``<обёртка> --version`` (без монтирования,
  D110); результат кэшируется на TTL секунд, чтобы ``/api/health`` и
  лист импорта не поднимали контейнер на каждый запрос;
- ``convert`` — ``<обёртка> IN OUT [опции]`` (subprocess, один поток
  веб-процесса — арена воркера не занимается, TZ §8/§9); таймаут и
  опции — параметры: импорт — ``timeout_import_seconds`` +
  :data:`IMPORT_FLAGS` (EPUB3 и язык "und" — см. комментарий к константе),
  экспорт (R7) — ``timeout_export_seconds`` + без опций.

Оба возвращают факты, не поднимают исключений. Пре-проверки (типы,
DRM, скан-PDF, ZIP) — ``app.adapters.convert.checks``.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from app.core.config import Settings

#: TTL кэша пробы, с (TZ §8: проба на старте; health не должен запускать
#: контейнер на каждый запрос).
PROBE_TTL_SECONDS = 300.0
#: Таймаут самой пробы, с (холодный старт контейнера).
PROBE_TIMEOUT_SECONDS = 30.0

_probe_cache: list[tuple[float, str, str]] = []


def reset_probe_cache() -> None:
    """Сброс кэша (тесты: смена ``converter.binary`` между прогонами)."""
    _probe_cache.clear()


def _probe_once(settings: Settings) -> tuple[str, str]:
    binary = settings.converter.binary
    if not binary or not Path(binary).is_file():
        return "error", "не установлен calibre (converter.binary не задан или обёртка не найдена)"
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "error", f"проба не выполнена: {type(exc).__name__}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        return "error", " ".join(tail) or f"exit {proc.returncode}"
    lines = (proc.stdout or "").strip().splitlines()
    return "ok", lines[0] if lines else "версия не определена"


def probe(settings: Settings, ttl_seconds: float = PROBE_TTL_SECONDS) -> tuple[str, str]:
    """Проба конвертера: ``(status, detail)`` — ``ok`` + версия /
    ``error`` + причина. Результат кэшируется на ``ttl_seconds``."""
    now = time.monotonic()
    if _probe_cache and now - _probe_cache[0][0] < ttl_seconds:
        return _probe_cache[0][1], _probe_cache[0][2]
    result = _probe_once(settings)
    _probe_cache.clear()
    _probe_cache.append((now, result[0], result[1]))
    return result


#: Фиксированные опции импорта (R6, замер 2026-09-22, calibre 8.5.0):
#: --epub-version 3 — умолчание calibre (EPUB2) для ODF-входа даёт
#: class="calibre" на <html> — невалидно для EPUBCheck (RSC-005);
#: --language und — контейнер без LANG пишет в OPF locale "C"
#: (невалидный BCP-47, OPF-092); опция действует только при неизвестном
#: языке входа, реальный язык детектит приложение (шаг «язык»).
#: ВАЖНО: не `--version` — глобальный флаг calibre, печатает баннер и
#: завершается без конвертации (rc=0!).
IMPORT_FLAGS: tuple[str, ...] = ("--epub-version", "3", "--language", "und")


def convert(
    settings: Settings,
    src: Path,
    dst: Path,
    *,
    timeout: float,
    flags: tuple[str, ...] = (),
) -> tuple[bool, str]:
    """Конвертация ``src → dst`` обёрткой. Возврат ``(ok, причина)``.

    ``timeout`` — жёсткий таймаут (импорт — ``timeout_import_seconds``,
    экспорт — ``timeout_export_seconds``, R7); ``flags`` — опции вывода
    (импорт — :data:`IMPORT_FLAGS`, экспорт — пусто: входной EPUB уже
    несёт язык, формат выхода — по расширению dst). exit code ≠ 0 —
    причина из хвоста stderr (без трейса, T8)."""
    try:
        proc = subprocess.run(
            [settings.converter.binary, str(src), str(dst), *flags],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"превышен таймаут конвертации ({timeout:.0f} с)"
    except OSError as exc:
        return False, f"запуск конвертера не выполнен: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        return False, " ".join(tail) or f"конвертер завершился с кодом {proc.returncode}"
    if not dst.is_file():
        return False, "конвертер завершился, файл не создан"
    return True, ""
