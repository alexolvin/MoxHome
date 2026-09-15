"""Атомарная публикация выходного EPUB (E5.6/E5.9; механизм общий с L3).

Порядок (единый для движка E5 и пересборки L3, Rule 8):
`os.fsync` файла `.partial` → внутренняя валидация → EPUBCheck → резервная
копия существующего результата (E5.9) → `os.replace` → fsync каталога.
Файл в состоянии записи (`.partial`) НЕ является результатом ни при каких
условиях: прерывание (включая kill -9) оставляет на месте либо прежний
валидный файл, либо незавершённый `.partial` (T-L3-8).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from app.adapters.epub_engine.epubcheck import EpubcheckOutcome, run_epubcheck
from app.adapters.epub_engine.opf_repair import repair_epub2_opf_namespaces
from app.adapters.epub_engine.validation import validate_output_epub
from app.core.config import Settings
from app.core.errors import OutputValidationFailed

logger = logging.getLogger("app.adapter.epub_engine.atomic")


def fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_finalize(
    partial: Path, target: Path, settings: Settings
) -> tuple[EpubcheckOutcome, Path | None]:
    """Публикует `partial` как `target` атомарно.

    Перед валидацией — ремонт OPF (D83: пересерилизация движка ломает
    пространства имён атрибутов EPUB 2.0; единая точка для E5/L3/S5).
    Внутренняя валидация или EPUBCheck (FATAL/ERROR) — отказ публикации
    (OutputValidationFailed): `target` не изменяется (Rule 6: fail closed).
    Возвращает (результат EPUBCheck, путь резервной копии | None).
    """
    fsync_file(partial)
    repair_epub2_opf_namespaces(partial)
    problems = validate_output_epub(partial, settings)
    if problems:
        raise OutputValidationFailed(
            "внутренняя валидация выхода не пройдена: " + "; ".join(problems)
        )
    epubcheck = run_epubcheck(
        settings.epubcheck_jar_path, partial, settings.epubcheck.timeout_seconds
    )
    if epubcheck.available and (epubcheck.fatals or epubcheck.errors):
        sample = " | ".join((*epubcheck.fatals, *epubcheck.errors)[:5])
        raise OutputValidationFailed(
            f"EPUBCheck {epubcheck.version}: "
            f"{len(epubcheck.fatals)} fatal / {len(epubcheck.errors)} error: {sample}"
        )
    backup_path: Path | None = None
    if target.exists():
        # E5.9: резервная копия перед перезаписью существующего результата.
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup_path = target.with_name(f"{target.name}.bak-{stamp}")
        backup_path.write_bytes(target.read_bytes())
        logger.info("резервная копия существующего результата: %s", backup_path.name)
    partial.replace(target)
    fsync_dir(target.parent)
    return epubcheck, backup_path
