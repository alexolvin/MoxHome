# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Запуск EPUBCheck (E5.7, D4): Java 17 + jar из конфигурации.

Распределение W3C 5.x: рядом с jar лежит каталог `lib/` — класспасс
собирается как `jar:lib/*`, main-класс `com.adobe.epubcheck.tool.Checker`;
самодостаточный jar (старые релизы) — `java -jar`. Отсутствие Java или jar —
не сбой задания, а `VALIDATION_UNAVAILABLE` (Rule 6: задание не может стать
COMPLETED, только COMPLETED_WITH_WARNINGS с причиной).

Fail-closed (D82): «проверено, 0 ошибок» утверждается только при наличии
итоговой строки `Messages: N fatals / N errors / …` — её печатает EPUBCheck
в конце завершившейся проверки (при любом результате; exit code сам по себе
не индикатор — 1 при ошибках штатен). Аномальное завершение подпроцесса
(крах, OOM, NoClassDefFoundError) без этой строки — `VALIDATION_UNAVAILABLE`
с хвостом вывода, а НЕ 0 ошибок: до V1R так происходило «тихое 0/0»
(прогоны V1.13 и V1R.5 записали 0/0, повторная валидация тех же артефактов —
ошибки).

D85: EPUBCheck выбирает режим по расширению имени файла — имя, не
заканчивающееся на `.epub` (например, `<target>.partial` из
`atomic_finalize`), отклоняется сообщением «Mode required for non-epub
files» (exit 1, без строки `Messages: …`) — т.е. как «не проверено» по
критерию D82. Именно так записывались все «0/0» до V1R. Поэтому проверка
файла с таким именем идёт по временной копии с расширением `.epub` в том
же каталоге (единственный механизм для всех вызовов, Rule 8).

Версия EPUBCheck фиксируется в отчёте (E5.7).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_MAIN_CLASS = "com.adobe.epubcheck.tool.Checker"
_MESSAGE_RE = re.compile(r"^(FATAL|ERROR|WARNING|INFO)\([A-Z0-9-]+\)[: ]\s*(.*)$")
_SUMMARY_RE = re.compile(
    r"^Messages:\s*(\d+)\s*fatals?\s*/\s*(\d+)\s*errors?\s*/"
    r"\s*(\d+)\s*warnings?\s*/\s*(\d+)\s*infos?\s*$",
    re.IGNORECASE,
)


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


def facts(outcome: EpubcheckOutcome) -> dict[str, Any]:
    """Факты EPUBCheck для ``.meta.json`` (ТЗ §15, R6): единая точка
    для синхронного и фонового пути импорта (Rule 8)."""
    return {
        "available": outcome.available,
        "version": outcome.version,
        "errors": len(outcome.fatals) + len(outcome.errors),
        "warnings": len(outcome.warnings),
        "reason": outcome.unavailable_reason,
    }


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


_ParsedOutput = tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    re.Match[str] | None,
]


def _parse_output(output: str) -> _ParsedOutput:
    """(fatal, error, warning, info построчно) + совпадение итоговой строки."""
    fatals: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []
    summary: re.Match[str] | None = None
    for line in output.splitlines():
        match = _MESSAGE_RE.match(line.strip())
        if match is not None:
            level, message = match.group(1), match.group(2)
            if level == "FATAL":
                fatals.append(message)
            elif level == "ERROR":
                errors.append(message)
            elif level == "WARNING":
                warnings.append(message)
            else:
                infos.append(message)
            continue
        summary_match = _SUMMARY_RE.match(line.strip())
        if summary_match is not None:
            summary = summary_match
    return tuple(fatals), tuple(errors), tuple(warnings), tuple(infos), summary


def run_epubcheck(jar_path: Path, epub: Path, timeout_seconds: float) -> EpubcheckOutcome:
    """Валидирует `epub` EPUBCheck. Возвращает факты; недоступность — не исключение.

    D85: имя без расширения `.epub` (например, `<target>.partial`) EPUBCheck
    не валидирует как EPUB (см. модуль) — проверяется временная копия с
    корректным расширением в том же каталоге.
    """
    prefix, main, reason = _resolve(jar_path)
    if reason is not None:
        return EpubcheckOutcome(available=False, version=None, unavailable_reason=reason)

    check_path = epub
    tmp_copy: Path | None = None
    if not epub.name.endswith(".epub"):
        fd, name = tempfile.mkstemp(suffix=".epub", dir=epub.parent)
        tmp_copy = Path(name)
        with os.fdopen(fd, "wb") as out, epub.open("rb") as src:
            shutil.copyfileobj(src, out)
        check_path = tmp_copy
    try:
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
            _command(prefix, main, [str(check_path)]),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    finally:
        if tmp_copy is not None:
            tmp_copy.unlink(missing_ok=True)
    output = f"{proc.stdout}\n{proc.stderr}"
    fatals, errors, warnings, infos, summary = _parse_output(output)
    if summary is None:
        # D82: проверка не завершена (подпроцесс завершился аномально: крах,
        # OOM, NoClassDefFoundError, …). Fail closed (Rule 6/E5.7): без
        # доказательств завершения 0 ошибок не утверждается.
        tail = "\n".join(output.strip().splitlines()[-10:])
        return EpubcheckOutcome(
            available=False,
            version=version,
            unavailable_reason=(
                f"EPUBCheck не завершил проверку: подпроцесс (exit {proc.returncode}) "
                f"не напечатал итоговую строку 'Messages: …'. Хвост вывода: {tail}"
            ),
        )
    fatal_count, error_count = int(summary.group(1)), int(summary.group(2))
    if fatal_count + error_count > len(fatals) + len(errors):
        # D82: валидатор увидел ошибки, но построчный парсер их не опознал
        # (дрейф формата вывода) — результат ненадёжен, fail closed.
        return EpubcheckOutcome(
            available=False,
            version=version,
            unavailable_reason=(
                f"EPUBCheck: в итоговой строке {fatal_count} fatal / {error_count} error, "
                f"распознано построчно {len(fatals)} + {len(errors)} — проверка "
                "считается не завершённой"
            ),
        )
    return EpubcheckOutcome(
        available=True,
        version=version,
        unavailable_reason=None,
        fatals=fatals,
        errors=errors,
        warnings=warnings,
        infos=infos,
    )
