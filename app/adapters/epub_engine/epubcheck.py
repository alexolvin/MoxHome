"""Запуск EPUBCheck (E5.7, D4): Java 17 + jar из конфигурации.

Распределение W3C 5.x: рядом с jar лежит каталог `lib/` — класспасс
собирается как `jar:lib/*`, main-класс `com.adobe.epubcheck.tool.Checker`;
самодостаточный jar (старые релизы) — `java -jar`. Отсутствие Java или jar —
не сбой задания, а `VALIDATION_UNAVAILABLE` (Rule 6: задание не может стать
COMPLETED, только COMPLETED_WITH_WARNINGS с причиной).

Версия EPUBCheck фиксируется в отчёте (E5.7).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_MAIN_CLASS = "com.adobe.epubcheck.tool.Checker"
_MESSAGE_RE = re.compile(r"^(FATAL|ERROR|WARNING|INFO)\([A-Z0-9-]+\)[: ]\s*(.*)$")


@dataclass(frozen=True)
class EpubcheckOutcome:
    """Факты внешней валидации: версия + сообщения по уровням (E5.7)."""

    available: bool
    version: str | None
    unavailable_reason: str | None
    fatals: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    infos: tuple[str, ...] = ()


def _resolve(jar_path: Path) -> tuple[list[str], str, str | None]:
    """(префикс команды java, main-класс, причина недоступности | None)."""
    java = shutil.which("java")
    if java is None:
        return [], "", "Java не найдена (команда `java` отсутствует в PATH)"
    if not jar_path.is_file():
        return [], "", f"EPUBCheck jar не найден: {jar_path}"
    lib_dir = jar_path.parent / "lib"
    if lib_dir.is_dir():
        return [java, "-cp", f"{jar_path}:{lib_dir}/*"], _MAIN_CLASS, None
    return [java, "-jar", str(jar_path)], "", None


def _command(prefix: list[str], main: str, args: list[str]) -> list[str]:
    if main:
        return [*prefix, main, *args]
    return [*prefix, *args]


def run_epubcheck(jar_path: Path, epub: Path, timeout_seconds: float) -> EpubcheckOutcome:
    """Валидирует `epub` EPUBCheck. Возвращает факты; недоступность — не исключение."""
    prefix, main, reason = _resolve(jar_path)
    if reason is not None:
        return EpubcheckOutcome(available=False, version=None, unavailable_reason=reason)

    version_proc = subprocess.run(
        _command(prefix, main, ["--version"]),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    version_text = (version_proc.stdout or version_proc.stderr).strip()
    version = version_text.splitlines()[0] if version_text else "(не удалось определить)"

    proc = subprocess.run(
        _command(prefix, main, [str(epub)]),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    output = f"{proc.stdout}\n{proc.stderr}"
    fatals: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []
    for line in output.splitlines():
        match = _MESSAGE_RE.match(line.strip())
        if match is None:
            continue
        level, message = match.group(1), match.group(2)
        if level == "FATAL":
            fatals.append(message)
        elif level == "ERROR":
            errors.append(message)
        elif level == "WARNING":
            warnings.append(message)
        else:
            infos.append(message)
    return EpubcheckOutcome(
        available=True,
        version=version,
        unavailable_reason=None,
        fatals=tuple(fatals),
        errors=tuple(errors),
        warnings=tuple(warnings),
        infos=tuple(infos),
    )
