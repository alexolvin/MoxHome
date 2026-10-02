# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""T-E5-12…T-E5-18: EPUBCheck fail-closed (D82), валидация .partial (D85),
ремонт OPF (D83).

Контекст (V1R.5): прогоны записали EPUBCheck 0/0, повторная валидация тех же
артефактов — ошибки (V1.13: 8/6/6/102/90; V1R.5: target 8, learning 163).
Корневая причина «тихого 0/0» — D85: движок валидует файл
`<target>.partial`, а EPUBCheck не валидирует имена без `.epub` (exit 1,
«Mode required for non-epub files», без строки `Messages: …`), и D82:
run_epubcheck не требовал доказательств завершения — exit 1 без строки
читался как «0 ошибок». Причина самих ошибок в артефактах — D83
(пересерилизация движком OPF EPUB 2.0). Правки: итоговая строка
`Messages: …` обязательна (D82); файлы без `.epub` проверяются по временной
копии (D85); ремонт пространств имён — единой точкой в atomic_finalize (D83).

Тесты:
- T-E5-12 — краш валидатора (нет итоговой строки) → VALIDATION_UNAVAILABLE,
  НЕ «0 ошибок» (D82, Rule 6).
- T-E5-13 — итоговая строка считает ошибки, построчный парсер их не увидел
  (дрейф формата) → VALIDATION_UNAVAILABLE (D82).
- T-E5-14 — штатное завершение на чистой фикстуре: available, 0 ошибок
  (положительный контроль: fail-closed не ломает нормальный путь).
- T-E5-15 — ремонт OPF: атрибуты метаданных получают OPF-нс, guide/reference
  — без нс, dc:language не трогается; идемпотентность; валидный OPF — no-op.
- T-E5-16 — ремонт в ZIP: порядок записей и mimetype сохранены, повтор — no-op.
- T-E5-17 — интеграция (реальный EPUBCheck): минимальная EPUB 2.0 с битым OPF
  до ремонта — ERROR, после — 0 (skipif без java/jar).
- T-E5-18 — имя без `.epub` (`<target>.partial`): чистый epub — 0 ошибок,
  битый — ошибки обнаружены (реальная проверка, а не отказ), временных
  файлов в каталоге нет (D85).
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest
from lxml import etree

from app.adapters.epub_engine.epubcheck import run_epubcheck
from app.adapters.epub_engine.opf_repair import (
    OPF_NS,
    XSI_NS,
    repair_epub2_opf_namespaces,
    repair_opf_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
JAR = ROOT / "data" / "tools" / "epubcheck-5.3.0" / "epubcheck.jar"
FIXTURE = ROOT / "tests" / "fixtures" / "epub" / "bilingual.epub"

#: OPF в стиле пересерилизации движка epub-translator 0.1.10 (D83):
#: default namespace — OPF, xmlns:opf отсутствует, атрибуты метаданных
#: без нс, guide/reference@type — в XSI-нс. Замерено в target.epub V1/V1R.
CORRUPTED_OPF = (
    "<?xml version='1.0' encoding='UTF-8'?>\n"
    "\n"
    '<package version="2.0" unique-identifier="id" '
    'xmlns="http://www.idpf.org/2007/opf" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:ns2="http://www.w3.org/2001/XMLSchema-instance">\n'
    "  <metadata>\n"
    '    <dc:identifier id="id" scheme="URI">http://example.org/book</dc:identifier>\n'
    '    <dc:creator file-as="Author, Test" role="aut">Test Author</dc:creator>\n'
    "    <dc:title>Test Book</dc:title>\n"
    '    <dc:language ns2:type="dcterms:RFC4646">en</dc:language>\n'
    '    <dc:date event="publication">2001-10-01</dc:date>\n'
    "  </metadata>\n"
    "  <manifest>\n"
    '    <item id="item1" href="ch1.xhtml" media-type="application/xhtml+xml"/>\n'
    '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>\n'
    "  </manifest>\n"
    '  <spine toc="ncx">\n'
    "    <itemref idref=\"item1\"/>\n"
    "  </spine>\n"
    "  <guide>\n"
    '    <reference ns2:type="toc" title="Contents" href="ch1.xhtml"/>\n'
    "  </guide>\n"
    "</package>\n"
)

CHAPTER_XHTML = (
    "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
    '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
    '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n'
    '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">'
    "<head><title>Ch1</title></head><body><p>Hello.</p></body></html>\n"
)

#: EPUB 2.0.1: spine@toc ссылается на NCX (не на XHTML-документ, как в EPUB 3).
NCX = (
    "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
    '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
    "  <head>\n"
    '    <meta name="dtb:uid" content="http://example.org/book"/>\n'
    '    <meta name="dtb:depth" content="1"/>\n'
    '    <meta name="dtb:totalPageCount" content="0"/>\n'
    '    <meta name="dtb:maxPageNumber" content="0"/>\n'
    "  </head>\n"
    "  <docTitle><text>Test Book</text></docTitle>\n"
    "  <navMap>\n"
    '    <navPoint id="navpoint-1" playOrder="1">\n'
    "      <navLabel><text>Ch1</text></navLabel>\n"
    '      <content src="ch1.xhtml"/>\n'
    "    </navPoint>\n"
    "  </navMap>\n"
    "</ncx>\n"
)

CONTAINER_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    "  <rootfiles>\n"
    '    <rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/>\n'
    "  </rootfiles>\n"
    "</container>\n"
)


def _make_epub2(path: Path, opf: str) -> None:
    """Минимальная EPUB 2.0: mimetype первой записью, без сжатия."""
    with zipfile.ZipFile(path, "w", allowZip64=True) as zf:
        info = zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, "application/epub+zip")
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/ch1.xhtml", CHAPTER_XHTML)
        zf.writestr("OEBPS/toc.ncx", NCX)


# ------------------------------------------------------------------ D82


def _run_version_only(args: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, 0, stdout="EPUBCheck v5.3.0\n", stderr="")


def test_e5_12_crashed_validator_is_unavailable_not_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T-E5-12 (D82): краш подпроцесса — нет строки 'Messages: …' →
    VALIDATION_UNAVAILABLE, а не «проверено, 0 ошибок» (Rule 6)."""

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "--version" in args:
            return _run_version_only(args, **kwargs)
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="Validating using EPUB version 2.0.1 rules.\n",
            stderr=(
                'Exception in thread "main" java.lang.NoClassDefFoundError: net/sf/saxon/om/\n'
                "\tat com.adobe.epubcheck.tool.Checker.main(Checker.java:1)\n"
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    jar = tmp_path / "epubcheck.jar"
    jar.write_bytes(b"fake")
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"zip")

    outcome = run_epubcheck(jar, epub, 30)

    assert outcome.available is False, "краш не может маскироваться под доступность"
    assert outcome.fatals == () and outcome.errors == ()
    assert outcome.unavailable_reason is not None
    assert "не завершил проверку" in outcome.unavailable_reason
    assert "NoClassDefFoundError" in outcome.unavailable_reason


def test_e5_13_summary_vs_parsed_drift_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T-E5-13 (D82): в итоговой строке ошибки, построчный парсер их не
    опознал — результат ненадёжен → VALIDATION_UNAVAILABLE."""

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "--version" in args:
            return _run_version_only(args, **kwargs)
        return subprocess.CompletedProcess(
            args,
            1,
            stdout=(
                "Validating using EPUB version 2.0.1 rules.\n"
                "NEW(FORMAT-1): some unrecognized error line\n"
                "Messages: 0 fatals / 3 errors / 0 warnings / 0 infos\n"
                "Check finished with errors\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    jar = tmp_path / "epubcheck.jar"
    jar.write_bytes(b"fake")
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"zip")

    outcome = run_epubcheck(jar, epub, 30)

    assert outcome.available is False
    assert outcome.unavailable_reason is not None
    assert "распознано построчно" in outcome.unavailable_reason


@pytest.mark.skipif(
    shutil.which("java") is None or not JAR.is_file(),
    reason="EPUBCheck (java + jar) недоступен в окружении",
)
def test_e5_14_clean_fixture_still_available() -> None:
    """T-E5-14: штатное завершение — available, 0 ошибок (fail-closed не
    ломает нормальный путь)."""
    outcome = run_epubcheck(JAR, FIXTURE, 300)
    assert outcome.available is True, f"{outcome.unavailable_reason}"
    assert outcome.fatals == () and outcome.errors == ()
    assert outcome.version


# ------------------------------------------------------------------ D83


def test_e5_15_repair_opf_bytes() -> None:
    """T-E5-15 (D83): ремонт пространств имён; dc:language не трогается;
    идемпотентность; валидный OPF — no-op."""
    repaired, changed = repair_opf_bytes(CORRUPTED_OPF.encode("utf-8"))
    assert changed
    root = etree.fromstring(repaired)
    dc = "http://purl.org/dc/elements/1.1/"
    identifier = root.find(f"{{{OPF_NS}}}metadata/{{{dc}}}identifier")
    creator = root.find(f"{{{OPF_NS}}}metadata/{{{dc}}}creator")
    date = root.find(f"{{{OPF_NS}}}metadata/{{{dc}}}date")
    language = root.find(f"{{{OPF_NS}}}metadata/{{{dc}}}language")
    reference = root.find(f"{{{OPF_NS}}}guide/{{{OPF_NS}}}reference")
    assert identifier is not None and identifier.get(f"{{{OPF_NS}}}scheme") == "URI"
    assert identifier.get("scheme") is None
    assert creator is not None
    assert creator.get(f"{{{OPF_NS}}}file-as") == "Author, Test"
    assert creator.get(f"{{{OPF_NS}}}role") == "aut"
    assert date is not None and date.get(f"{{{OPF_NS}}}event") == "publication"
    # dc:language@type остаётся в XSI-нс (как в исходнике), ремонт не трогает
    assert language is not None
    assert language.get(f"{{{XSI_NS}}}type") == "dcterms:RFC4646"
    # guide/reference: type — без пространства имён (OPF 2.0.1)
    assert reference is not None
    assert reference.get("type") == "toc"
    assert reference.get(f"{{{XSI_NS}}}type") is None

    # идемпотентность: повторный ремонт — no-op
    again, changed_again = repair_opf_bytes(repaired)
    assert not changed_again
    assert again == repaired


def test_e5_16_repair_epub_zip_preserves_structure(tmp_path: Path) -> None:
    """T-E5-16 (D83): замена OPF-записи сохраняет порядок записей,
    mimetype — первый и без сжатия; повторный ремонт — no-op."""
    epub = tmp_path / "book.epub"
    _make_epub2(epub, CORRUPTED_OPF)
    assert repair_epub2_opf_namespaces(epub) is True

    with zipfile.ZipFile(epub) as zf:
        names = zf.namelist()
        assert names[0] == "mimetype"
        assert zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/epub+zip"
        root = etree.fromstring(zf.read("OEBPS/content.opf"))
        dc = "http://purl.org/dc/elements/1.1/"
        identifier = root.find(f"{{{OPF_NS}}}metadata/{{{dc}}}identifier")
        assert identifier is not None and identifier.get(f"{{{OPF_NS}}}scheme") == "URI"
        # остальные записи не изменены
        assert zf.read("OEBPS/ch1.xhtml").decode("utf-8") == CHAPTER_XHTML

    assert repair_epub2_opf_namespaces(epub) is False


def test_e5_16b_repair_missing_file_noop(tmp_path: Path) -> None:
    assert repair_epub2_opf_namespaces(tmp_path / "нет.epub") is False


@pytest.mark.skipif(
    shutil.which("java") is None or not JAR.is_file(),
    reason="EPUBCheck (java + jar) недоступен в окружении",
)
def test_e5_17_repaired_epub2_passes_epubcheck(tmp_path: Path) -> None:
    """T-E5-17 (D83, интеграция): минимальная EPUB 2.0 с битым OPF до
    ремонта не проходит EPUBCheck, после ремонта — 0 ошибок. Фиксирует и
    саму невалидность (гарант, что ремонт не «no-op при зелёном тесте»),
    и эффект ремонта."""
    epub = tmp_path / "book.epub"
    _make_epub2(epub, CORRUPTED_OPF)
    before = run_epubcheck(JAR, epub, 300)
    assert before.available is True, f"EPUBCheck должен завершиться: {before.unavailable_reason}"
    assert before.errors, "битый OPF обязан давать ERROR (иначе тест бессмыслен)"

    assert repair_epub2_opf_namespaces(epub) is True
    after = run_epubcheck(JAR, epub, 300)
    assert after.available is True, f"{after.unavailable_reason}"
    assert after.fatals == () and after.errors == (), f"после ремонта: {after.errors}"


@pytest.mark.skipif(
    shutil.which("java") is None or not JAR.is_file(),
    reason="EPUBCheck (java + jar) недоступен в окружении",
)
def test_e5_18_partial_name_is_validated(tmp_path: Path) -> None:
    """T-E5-18 (D85): файл с именем без `.epub` (именно так движок валидует
    `<target>.partial` в atomic_finalize) EPUBCheck отклоняет без проверки
    («Mode required for non-epub files», exit 1). С фиксом: битый OPF —
    ошибки обнаружены (проверка реально шла), чистый — 0 ошибок, временных
    копий в каталоге не остаётся."""
    broken = tmp_path / "target.epub.partial"
    _make_epub2(broken, CORRUPTED_OPF)
    broken_outcome = run_epubcheck(JAR, broken, 300)
    assert broken_outcome.available is True, broken_outcome.unavailable_reason
    assert broken_outcome.errors, "битый OPF обязан дать ERROR — проверка шла"

    clean = tmp_path / "clean.epub.partial"
    _make_epub2(clean, CORRUPTED_OPF)
    assert repair_epub2_opf_namespaces(clean) is True
    clean_outcome = run_epubcheck(JAR, clean, 300)
    assert clean_outcome.available is True, clean_outcome.unavailable_reason
    assert clean_outcome.fatals == () and clean_outcome.errors == ()

    leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".epub"]
    assert leftovers == [], f"временные копии не убраны: {leftovers}"
