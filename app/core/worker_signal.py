"""Сигнал «воркер жив» (D87): heartbeat-файл в корне storage.

Воркер — отдельный процесс (ТЗ §5.4); API/UI не знают его PID, пока он не
занял задание (``jobs.worker_pid``). Чтобы интерфейс честно показывал
«воркер онлайн/оффлайн» (в т.ч. когда очередь пуста), цикл воркера
(``run_forever``) периодически перезаписывает файл heartbeat; health-проба
и страницы читают его. Файл — служебный, в корне storage (паттерн
контрольного файла health-пробы), в git не попадает (runtime-каталог).

Формат (2 строки): ``<pid>`` и ``<epoch>`` последнего письма.
Жив — если письмо не старше ``3 × server.heartbeat_seconds``.
"""

from __future__ import annotations

import time
from pathlib import Path

from app.core.config import Settings

#: Имя файла heartbeat в корне storage (намеренная константа, V1.2-класс).
HEARTBEAT_FILE = ".worker-heartbeat"


def heartbeat_path(settings: Settings) -> Path:
    return settings.storage.root / HEARTBEAT_FILE


def write_heartbeat(settings: Settings, pid: int) -> None:
    """Перезаписать heartbeat (вызывается циклом воркера каждый период)."""
    path = heartbeat_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n{time.time()}\n", encoding="utf-8")


def worker_alive(settings: Settings) -> tuple[str, str]:
    """Статус воркера для UI/health: ``(статус, пояснение)``.

    Статус — ``ok (pid N)`` при свежем heartbeat, ``error`` — если файла
    нет (воркер не запущен / запущен без --loop) или письмо протухло
    (процесс умер/завис). Никакого агрегированного «healthy» (U1-3).
    """
    max_age = 3.0 * settings.server.heartbeat_seconds
    try:
        lines = heartbeat_path(settings).read_text(encoding="utf-8").split()
    except FileNotFoundError:
        return "error", "heartbeat не найден (воркер не запущен?)"
    except OSError as exc:
        return "error", f"heartbeat не читается: {exc}"
    if len(lines) < 2:
        return "error", "heartbeat повреждён (пустой файл)"
    pid, ts_raw = lines[0], lines[1]
    try:
        age = time.time() - float(ts_raw)
    except ValueError:
        return "error", "heartbeat повреждён (нет timestamps)"
    if age > max_age:
        return "error", f"оффлайн (последний heartbeat {int(age)} c назад, pid {pid})"
    return f"ok (pid {pid})", ""
