# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""S5: доказательства CONTROL (verbatim).

Запуск: python tests/s5_evidence.py
Захваты: docs/CONTROL/evidence/S5/s5-1…s5-9 + t-s5-10/11/12 (verbatim).

Сценарии (mock-сервер D3, детерминированный; реальные spaCy-модели):
  s5-1  learning EPUB на 5 языковых фикстурах: EPUBCheck 0 ERROR (verbatim)
  s5-2  mimetype — первой записью без сжатия, остальные сжаты;
        zipinfo verbatim в отчёте
  s5-3  сохранность обложки/изображений/стилей/ссылок: записи target,
        кроме структурных, побайтово; оригинальные блоки — префикс
  s5-4  записи оглавления для КАЖДОЙ добавленной секции (100%)
  s5-5  «Ответ:» в телах глав — 0; все ответы в финальном документе
  s5-6  обязательные поля карточек (артикль de/nl, род fr/it) — 100%
        строк CSV/Anki/JSON
  s5-7  Anki-импорт — BLOCKER (Anki в окружении отсутствует);
        структура экспорта verbatim
  s5-8  охват в книге = E6 (jobs.alignment_coverage), расхождение 0
  s5-9  kill -9 ×10 на записи learning EPUB: 0 повреждённых принятых
  t-s5-10  Minimal: без секций лексики/грамматики/вопросов
  t-s5-11  Intensive: эквивалентность «артефакт ⇔ секция» по главам
  t-s5-12  искусственный охват 90%: отражён в книге, пропущены перечислены

Реальные LLM-прогоны не проводились (BLOCKER-1): контент (карточки,
сводки, вопросы, упражнения) — mock (детерминированная генерация);
сборка учебной книги, EPUBCheck, экспорт карточек, атомарная выдача,
kill-устойчивость — реальные механизмы.
"""

from __future__ import annotations

import csv
import json
import os
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "probe"))

from mock_llm_server import MockLLMServer  # noqa: E402
from test_l2_glossary import _make_storage  # noqa: E402
from test_l3_glossary_fix import _kill_all, _start_worker, _wait_status  # noqa: E402
from test_s4_study import _synth_s4_en1_book, _synth_s4_en_book  # noqa: E402
from test_s5_study_book import (  # noqa: E402
    S5Env,
    S5Run,
    _answers_doc,
    _artifact_chapters,
    _epub_valid,
    _nav_hrefs,
    _rerun_s5_assembly,
    _s5_report,
    _synth_s5_10_book,
    _synth_s5_lang_book,
    _wait_terminal,
)

from app.adapters.epub_engine.epubcheck import _command, _resolve  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.domain.job import JobStatus  # noqa: E402
from app.epub import segmenter  # noqa: E402
from app.epub.reader import read_epub  # noqa: E402
from app.languages.cards import load_card_schema  # noqa: E402
from app.languages.registry import load_registry  # noqa: E402
from app.learning import card_export  # noqa: E402
from app.services.jobs import confirm_job, create_job  # noqa: E402
from app.storage.jobs_repo import JobRepository  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "S5"
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
VERSIONS = f"python={sys.version.split()[0]} spacy={pkg_version('spacy')}"

_LANG_ORDER = ("en", "de", "fr", "it", "nl")


def evidence(name: str, content: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print(f"  [evidence] {path.relative_to(ROOT)}")


def _header() -> str:
    return f"дата: {STAMP}\nверсии: {VERSIONS}\n\n"


def _epubcheck_verbatim(settings: Settings, epub: Path) -> str:
    """Сырой вывод EPUBCheck (та же команда, что в движке E5)."""
    prefix, main, reason = _resolve(settings.epubcheck_jar_path)
    assert prefix, reason
    proc = subprocess.run(
        _command(prefix, main, [str(epub)]),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    return f"{proc.stdout}\n{proc.stderr}".rstrip() + "\n"


def _read_json(run: S5Run, name: str) -> dict[str, Any]:
    path = run.job_dir / name
    if not path.is_file():
        return {}
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


# Наполняется в main(): профиль → Settings (для вспомогательных сборщиков).
_SETTINGS: dict[str, Settings] = {}


def _settings_of(run: S5Run) -> Settings:
    return _SETTINGS[run.profile]


# ------------------------------------------------------------------ S5-1


def _ev_s5_1(env: S5Env, lang_runs: dict[str, S5Run]) -> None:
    settings = env._settings["main"]
    ok = True
    blocks: list[str] = []
    for lang in _LANG_ORDER:
        run = lang_runs[lang]
        report = _s5_report(run)
        epubcheck = report["epub"]["epubcheck"]
        book = "complex.epub" if lang == "en" else f"lang_{lang}.epub"
        verbatim = _epubcheck_verbatim(settings, run.job_dir / "learning.epub")
        good = (
            run.error is None
            and epubcheck["available"] is True
            and epubcheck["errors"] == 0
        )
        ok = ok and good
        counts = (
            f"error={epubcheck['errors']} "
            f"warning={epubcheck.get('warnings', 0)}"
        )
        blocks.append(
            f"—— {lang} ({book}, Intensive) — EPUBCheck {epubcheck['version']} "
            f"— {counts}\n"
            + verbatim
            + "\n"
        )
    verdict = "PASS" if ok else "FAIL"
    content = (
        _header()
        + "5 языковых фикстур → ru, уровень Intensive; learning.epub каждого "
        "задания\n\n"
        + "".join(blocks)
        + f"S5-1: {verdict} — EPUBCheck 0 ERROR (0 FATAL) на 5/5 learning EPUB\n"
    )
    evidence("s5-1-epubcheck-five-languages.txt", content)


# ------------------------------------------------------------------ S5-2


def _ev_s5_2(run: S5Run) -> None:
    report = _s5_report(run)
    learning = run.job_dir / "learning.epub"
    with zipfile.ZipFile(learning) as archive:
        infos = archive.infolist()
        first = infos[0]
        mimetype_value = archive.read("mimetype").decode("utf-8")
        others_deflated = all(
            info.compress_type == zipfile.ZIP_DEFLATED for info in infos[1:]
        )
    good = (
        first.filename == "mimetype"
        and first.compress_type == zipfile.ZIP_STORED
        and mimetype_value == "application/epub+zip"
        and others_deflated
    )
    verdict = "PASS" if good else "FAIL"
    first_kind = (
        "STORED" if first.compress_type == zipfile.ZIP_STORED else "DEFLATED"
    )
    content = (
        _header()
        + f"{run.job_dir.name} (s5_en.epub, Intensive), "
        f"записей: {len(infos)}\n\n"
        + f"первая запись: {first.filename!r} — compress_type={first_kind}\n"
        + f"содержимое mimetype: {mimetype_value!r}\n"
        + f"все остальные записи DEFLATED: {others_deflated}\n\n"
        + "zipinfo (verbatim; тот же вывод — в s5_report.json «zipinfo»):\n\n"
        + report["zipinfo"]
        + f"\nS5-2: {verdict} — mimetype первой записью без сжатия, "
        "остальные сжаты, zipinfo verbatim\n"
    )
    evidence("s5-2-mimetype-zipinfo.txt", content)


# ------------------------------------------------------------------ S5-3


def _ev_s5_3(run: S5Run) -> None:
    report = _s5_report(run)
    settings = _settings_of(run)
    target = run.job_dir / "target.epub"
    learning = run.job_dir / "learning.epub"
    book = read_epub(target, run.job_dir / "ev_pre", settings)
    doc_by_id = {ch.chapter_id: ch.document_path for ch in book.chapters}
    tags = frozenset(settings.segmentation.block_tags)
    with zipfile.ZipFile(target) as tzip, zipfile.ZipFile(learning) as lzip:
        patched = {doc_by_id[cid] for cid in report["sections"]}
        rows: list[str] = []
        untouched = 0
        changed_bad: list[str] = []
        for name in tzip.namelist():
            if name not in lzip.namelist():
                status = "ОТСУТСТВУЕТ"
                changed_bad.append(name)
            elif name in patched:
                status = "изменена (вставлены секции S5)"
            elif name == "OEBPS/content.opf":
                status = "изменена (OPF: +answers в manifest/spine)"
            elif name == "OEBPS/nav.xhtml":
                status = "изменена (nav: +записи оглавления)"
            elif lzip.read(name) == tzip.read(name):
                status = "побайтово сохранена"
                untouched += 1
            else:
                status = "ИЗМЕНЕНА (вне ожиданий)"
                changed_bad.append(name)
            rows.append(f"  {name:34s} {status}")
        new_entries = [n for n in lzip.namelist() if n not in tzip.namelist()]
        prefix_rows: list[str] = []
        prefix_ok = True
        for chapter in book.chapters:
            old_body = segmenter.find_body(
                etree.fromstring(tzip.read(chapter.document_path))
            )
            new_body = segmenter.find_body(
                etree.fromstring(lzip.read(chapter.document_path))
            )
            assert old_body is not None and new_body is not None, "нет <body>"
            old_blocks = [
                segmenter.full_text(e)
                for e in segmenter.find_block_elements(old_body, tags)
            ]
            new_blocks = [
                segmenter.full_text(e)
                for e in segmenter.find_block_elements(new_body, tags)
            ]
            same = new_blocks[: len(old_blocks)] == old_blocks
            prefix_ok = prefix_ok and same
            prefix_rows.append(
                f"  {chapter.document_path}: блоков {len(old_blocks)} → "
                f"{len(new_blocks)}, префикс сохранён: {same}"
            )
        explicit = {
            name: lzip.read(name) == tzip.read(name)
            for name in ("OEBPS/style.css", "OEBPS/img/dot.png", "META-INF/container.xml")
        }
    entries_ok = (
        report["epub"]["entries_total"]
        == len(zipfile.ZipFile(target).namelist()) + 1
    )
    verdict = "PASS" if (
        not changed_bad
        and prefix_ok
        and all(explicit.values())
        and entries_ok
    ) else "FAIL"
    content = (
        _header()
        + f"complex.epub (Intensive): target {len(zipfile.ZipFile(target).namelist())} "
        f"записей → learning {report['epub']['entries_total']} (+1: {new_entries})\n\n"
        + "записи target.epub (судьба в learning.epub):\n" + "".join(rows)
        + f"\npобайтово сохранено: {untouched}\n\n"
        + "оригинальные блоки глав (префикс, без изменения):\n"
        + "".join(prefix_rows)
        + "\nключевые ресурсы явно:\n"
        + "".join(f"  {name}: побайтово={ok}\n" for name, ok in explicit.items())
        + f"\nentries_total = записей target + 1: {entries_ok}\n\n"
        + f"S5-3: {verdict} — обложка/изображения/стили/ссылки сохранены, "
        "изменены только структурные документы\n"
    )
    evidence("s5-3-preservation.txt", content)


# ------------------------------------------------------------------ S5-4


def _ev_s5_4(run: S5Run) -> None:
    report = _s5_report(run)
    settings = _settings_of(run)
    book = read_epub(run.job_dir / "target.epub", run.job_dir / "ev_toc", settings)
    doc_by_id = {ch.chapter_id: ch.document_path for ch in book.chapters}
    nav_hrefs = _nav_hrefs(run.job_dir / "learning.epub")
    rows: list[str] = []
    missing: list[str] = []
    total = 0
    for chapter_id in sorted(report["sections"]):
        doc = doc_by_id[chapter_id]
        rel = posixpath.relpath(doc, "OEBPS")
        for section_type in report["sections"][chapter_id]:
            href = f"{rel}#s5-{section_type}-{chapter_id}"
            total += 1
            present = href in nav_hrefs
            if not present:
                missing.append(href)
            rows.append(f"  {href} — {'есть' if present else 'НЕТ'}")
    answers_present = "study-answers.xhtml" in nav_hrefs
    verdict = "PASS" if (total > 0 and not missing and answers_present) else "FAIL"
    content = (
        _header()
        + "s5_en.epub (Intensive): запись оглавления для КАЖДОЙ секции\n\n"
        + "".join(rows)
        + f"\n  study-answers.xhtml — {'есть' if answers_present else 'НЕТ'}\n\n"
        + f"проверено: {total} секций; отсутствует: {len(missing)}; "
        f"финальный документ: {answers_present}\n\n"
        + f"S5-4: {verdict} — записи TOC на 100% добавленных секций\n"
    )
    evidence("s5-4-toc-entries.txt", content)


# ------------------------------------------------------------------ S5-5


def _ev_s5_5(run: S5Run) -> None:
    report = _s5_report(run)
    settings = _settings_of(run)
    book = read_epub(run.job_dir / "target.epub", run.job_dir / "ev_a5", settings)
    learning = run.job_dir / "learning.epub"
    rows: list[str] = []
    total_in_chapters = 0
    with zipfile.ZipFile(learning) as archive:
        for chapter in book.chapters:
            if chapter.chapter_id not in report["sections"]:
                continue
            text = archive.read(chapter.document_path).decode("utf-8")
            count = text.count("Ответ:")
            total_in_chapters += count
            rows.append(f"  {chapter.document_path}: вхождений «Ответ:» = {count}")
    answers = _answers_doc(learning)
    answers_count = answers.count("Ответ:")
    sample_lines = [
        line.strip()
        for line in answers.splitlines()
        if "Ответ:" in line
    ][:3]
    verdict = "PASS" if (total_in_chapters == 0 and answers_count > 0) else "FAIL"
    content = (
        _header()
        + "s5_en.epub (Intensive): «Ответ:» — только в финальном документе\n\n"
        + "тела глав с секциями:\n" + "".join(rows)
        + f"\nитоги в главах: {total_in_chapters}\n"
        + f"финальный документ (study-answers.xhtml): {answers_count} вхождений\n\n"
        + "примеры (verbatim):\n"
        + "".join(f"  {line}\n" for line in sample_lines)
        + f"\nS5-5: {verdict} — в телах глав 0 ответов, все — в конце книги\n"
    )
    evidence("s5-5-answers-separated.txt", content)


# ------------------------------------------------------------------ S5-6


def _ev_s5_6(card_runs: dict[str, S5Run]) -> None:
    settings = _settings_of(card_runs["de"])
    registry = load_registry(settings.languages_root)
    noun_fields = {
        "de": ("article", ("der", "die", "das")),
        "nl": ("article", ("de", "het")),
        "fr": ("gender", ("masc", "fem")),
        "it": ("gender", ("masc", "fem")),
    }
    ok = True
    blocks: list[str] = []
    for lang in ("de", "nl", "fr", "it"):
        run = card_runs[lang]
        _s5_report(run)
        cards = _read_json(run, "vocabulary_cards.json").get("cards", [])
        schema = load_card_schema(settings.languages_root / lang, registry[lang])
        csv_lines = (run.job_dir / "cards.csv").read_text(encoding="utf-8").splitlines()
        with (run.job_dir / "cards.csv").open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        # Обязательность — по части речи: каждая строка обязана содержать
        # все свои required_cells(schema, pos); сквозные счётчики — для
        # информации (article — только у nouns, auxiliary — только у verbs).
        all_cells = set()
        for card in cards:
            all_cells |= card_export.required_cells(schema, card["pos"])
        filled: dict[str, int] = dict.fromkeys(all_cells, 0)
        for row in rows:
            for cell in all_cells:
                if row.get(cell, "").strip():
                    filled[cell] += 1
        field_stats = " ".join(
            f"{c}={filled[c]}/{len(rows)}" for c in sorted(all_cells)
        )
        cards_ok = 0
        for card, row in zip(cards, rows, strict=True):
            missing = [
                cell
                for cell in card_export.required_cells(schema, card["pos"])
                if not row.get(cell, "").strip()
            ]
            if not missing:
                cards_ok += 1
        noun_field, allowed = noun_fields[lang]
        noun_values: Counter[str] = Counter()
        for card in cards:
            if card["pos"] == "noun":
                noun_values[str(card.get(noun_field))] += 1
        anki_lines = (run.job_dir / "cards.anki.tsv").read_text(encoding="utf-8").splitlines()
        exported = json.loads(
            (run.job_dir / "cards.json").read_text(encoding="utf-8")
        )
        good = (
            bool(cards)
            and len(rows) == len(cards)
            and cards_ok == len(rows)
            and all(v in allowed for v in noun_values)
            and bool(noun_values)
            and anki_lines[0] == "#separator:Tab"
            and anki_lines[1] == "#html:false"
            and anki_lines[2].startswith("#columns:")
            and len(anki_lines) - 3 == len(cards)
            and exported == cards
        )
        ok = ok and good
        blocks.append(
            f"—— {lang} (s5_{lang}.epub, Intensive): карточек {len(cards)}\n"
            f"   CSV-заголовок (verbatim): {csv_lines[0]}\n"
            f"   строки со ВСЕМИ обязательными для своей части речи "
            f"колонками: {cards_ok}/{len(rows)}\n"
            f"   заполненность по всем строкам (обязательность — по части "
            f"речи): {field_stats}\n"
            f"   {noun_field} у nouns (verbatim): "
            + ", ".join(f"{k}={v}" for k, v in sorted(noun_values.items()))
            + "\n"
            + "   Anki TSV (verbatim, служебные строки + первая строка данных):\n"
            + "".join(f"   {line}\n" for line in anki_lines[:4])
            + f"   JSON == vocabulary_cards.json «cards»: {exported == cards}\n\n"
        )
    verdict = "PASS" if ok else "FAIL"
    content = (
        _header()
        + "4 языка × 20-главные книги (число слов выше плотности S1): "
        "обязательные поля в экспорте — 100% строк\n\n"
        + "".join(blocks)
        + f"S5-6: {verdict} — обязательные поля (артикль de/nl, род fr/it, "
        "основные формы) в 100% строк CSV/Anki/JSON\n"
    )
    evidence("s5-6-card-required-fields.txt", content)


# ------------------------------------------------------------------ S5-7


def _ev_s5_7(run: S5Run) -> None:
    lines = (run.job_dir / "cards.anki.tsv").read_text(encoding="utf-8").splitlines()
    header = "\n".join(line for line in lines[:4])
    tags_ok = all(
        line.split("\t")[-1].startswith("moxhome ") for line in lines[3:]
    )
    content = (
        _header()
        + f"shutil.which('anki') = {shutil.which('anki')}\n"
        f"shutil.which('pyanki') = {shutil.which('pyanki')}\n\n"
        + "cards.anki.tsv (verbatim, служебные строки + первая строка данных):\n"
        + header
        + f"\n\nтеги всех {len(lines) - 3} строк начинаются с "
        f"«moxhome »: {tags_ok}\n\n"
        + "S5-7: не проверено фактически (BLOCKER: Anki/pyanki отсутствуют "
        "в окружении) — формат Anki text import верифицирован структурно: "
        "служебные строки, число полей, теги, verbatim-пример выше\n"
    )
    evidence("s5-7-anki-export-blocker.txt", content)


# ------------------------------------------------------------------ S5-8


def _ev_s5_8(run: S5Run) -> None:
    report = _s5_report(run)
    e6 = _read_json(run, "alignment_report.json")
    answers = _answers_doc(run.job_dir / "learning.epub")
    match = re.search(
        r'<p[^>]*title="s5-coverage=([\d.]+) s5-aligned=(\d+) s5-total=(\d+)"[^>]*>',
        answers,
    )
    assert match, "разметка охвата в книге не найдена"
    book_coverage = float(match.group(1))
    book_aligned = int(match.group(2))
    book_total = int(match.group(3))
    assert run.job.alignment_coverage is not None, "alignment_coverage не задан"
    diff_e6_job = abs(e6["coverage"] - run.job.alignment_coverage)
    diff_report = abs(report["coverage"] - run.job.alignment_coverage)
    diff_book = abs(book_coverage - run.job.alignment_coverage)
    good = (
        diff_e6_job < 1e-9
        and diff_report < 1e-9
        and diff_book < 1e-9
        and book_aligned == e6["aligned"]
        and book_total == e6["total"]
    )
    verdict = "PASS" if good else "FAIL"
    content = (
        _header()
        + "s5_en.epub (Intensive), покрытие 100%\n\n"
        + f"E6 (alignment_report.json): coverage={e6['coverage']} "
        f"aligned={e6['aligned']} total={e6['total']}\n"
        + f"jobs.alignment_coverage: {run.job.alignment_coverage}\n"
        + f"s5_report.json «coverage»: {report['coverage']}\n"
        + f"книга (title s5-coverage/aligned/total, D86): "
        f"{book_coverage}/{book_aligned}/{book_total}\n\n"
        + "строка в книге (verbatim):\n  "
        + match.group(0)
        + "\n\nрасхождения: E6↔jobs "
        + f"{diff_e6_job:.2e}; s5_report↔jobs {diff_report:.2e}; "
        + f"книга↔jobs {diff_book:.2e}\n\n"
        + f"S5-8: {verdict} — охват в книге = E6, расхождение 0\n"
    )
    evidence("s5-8-coverage-e6.txt", content)


# ------------------------------------------------------------------ S5-9


def _kill_wait_partial(
    repo: JobRepository, job_id: str, partial: Path
) -> Any:
    """Ожидание `.partial` (дедлайн 240 c); возврат состояния задания."""
    deadline = time.monotonic() + 240
    while not partial.exists():
        state = repo.get(job_id)
        if state is not None and state.status in (
            JobStatus.COMPLETED,
            JobStatus.COMPLETED_WITH_WARNINGS,
        ):
            return state
        if time.monotonic() > deadline:
            return state
        time.sleep(0.02)
    return repo.get(job_id)


def _kill_early_complete(
    w2: subprocess.Popen[bytes],
    repo: JobRepository,
    *,
    job_id: str,
    settings: Settings,
    learning_final: Path,
    log_dir: Path,
    index: int,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Воркер успел завершиться раньше окна kill — результат полный."""
    _wait_terminal(repo, job_id)
    assert w2.wait(timeout=60) == 0
    assert learning_final.is_file()
    _epub_valid(learning_final, settings, log_dir / f"kill_{index}_early")
    result.update(
        killed=False,
        learning="завершено до окна (валидно)",
        target="валиден",
        resume="—",
        deterministic="—",
    )
    return result


def _kill_and_resume(
    w2: subprocess.Popen[bytes],
    repo: JobRepository,
    *,
    job_id: str,
    settings: Settings,
    cfg: Path,
    env_file: Path,
    lease_at_kill: float,
    learning_final: Path,
    target_file: Path,
    log_dir: Path,
    index: int,
    workers: list[subprocess.Popen[bytes]],
    result: dict[str, Any],
) -> dict[str, Any]:
    """SIGKILL в окне записи + W3-догон до COMPLETED + проверка."""
    assert lease_at_kill >= time.time(), "арена действенна (heartbeat продлевает)"
    os.kill(w2.pid, signal.SIGKILL)
    assert w2.wait(timeout=20) == -9
    if learning_final.exists():
        _epub_valid(learning_final, settings, log_dir / f"kill_{index}_at_kill")
        at_kill = "присутствует, валиден"
    else:
        at_kill = "отсутствует"
    assert zipfile.is_zipfile(target_file)
    _epub_valid(target_file, settings, log_dir / f"kill_{index}_target")
    time.sleep(max(0.0, lease_at_kill - time.time()) + 0.1)
    w3 = _start_worker(cfg, env_file, log_dir / f"kill_{index}_w3.log")
    workers.append(w3)
    final = _wait_terminal(repo, job_id)
    assert w3.wait(timeout=60) == 0
    assert learning_final.is_file()
    _epub_valid(learning_final, settings, log_dir / f"kill_{index}_final")
    final_bytes = learning_final.read_bytes()
    _rerun_s5_assembly(settings, repo, job_id)
    deterministic = learning_final.read_bytes() == final_bytes
    result.update(
        killed=True,
        learning=at_kill,
        target="валиден",
        resume=f"W3 → {final.status.value}",
        deterministic="побайтово" if deterministic else "НЕ СОВПАДЕНИЕ",
    )
    return result


def _kill_iteration(
    settings: Settings,
    repo: JobRepository,
    *,
    cfg: Path,
    env_file: Path,
    log_dir: Path,
    book: Path,
    index: int,
    workers: list[subprocess.Popen[bytes]],
) -> dict[str, Any]:
    """Одна итерация kill -9 (по образцу T-S5-9) с подробным результатом."""
    result: dict[str, Any] = {"index": index}
    job = create_job(
        repo,
        settings,
        source_path=book,
        target_language="ru",
        learning_level="Intensive",
        learning_cefr="A1",
    )
    job_dir = settings.jobs_dir / job.id
    learning_final = job_dir / "learning.epub"
    partial = job_dir / "learning.epub.partial"
    target_file = job_dir / "target.epub"

    w1 = _start_worker(cfg, env_file, log_dir / f"kill_{index}_w1.log")
    workers.append(w1)
    _wait_status(repo, job.id, JobStatus.NEEDS_CONFIRMATION, 180)
    assert w1.wait(timeout=60) == 0
    confirm_job(repo, job.id, source_language="en")

    w2 = _start_worker(cfg, env_file, log_dir / f"kill_{index}_w2.log")
    workers.append(w2)
    _wait_status(repo, job.id, JobStatus.LEARNING, 300)
    state = _kill_wait_partial(repo, job.id, partial)
    assert state is not None
    if not (partial.exists() and state.status is JobStatus.LEARNING):
        return _kill_early_complete(
            w2,
            repo,
            job_id=job.id,
            settings=settings,
            learning_final=learning_final,
            log_dir=log_dir,
            index=index,
            result=result,
        )
    return _kill_and_resume(
        w2,
        repo,
        job_id=job.id,
        settings=settings,
        cfg=cfg,
        env_file=env_file,
        lease_at_kill=state.lease_expires_at,
        learning_final=learning_final,
        target_file=target_file,
        log_dir=log_dir,
        index=index,
        workers=workers,
        result=result,
    )


def _ev_s5_9(env: S5Env, kill_log: Path) -> None:
    settings = env._settings["kill"]
    repo = env._repos["kill"]
    cfg = env.base / "storage_kill" / "config.yaml"
    env_file = env.base / "storage_kill" / "test.env"
    book = env.books["s5_en1.epub"]
    workers: list[subprocess.Popen[bytes]] = []
    results: list[dict[str, Any]] = []
    try:
        for index in range(10):
            results.append(
                _kill_iteration(
                    settings,
                    repo,
                    cfg=cfg,
                    env_file=env_file,
                    log_dir=kill_log / f"it{index}",
                    book=book,
                    index=index,
                    workers=workers,
                )
            )
            print(f"  [kill] итерация {index + 1}/10: {results[-1]}")
    finally:
        _kill_all(workers)
    killed = sum(1 for r in results if r["killed"])
    good = (
        len(results) == 10
        and killed >= 1
        and all(r["target"] == "валиден" for r in results)
        and all("НЕ СОВПАДЕНИЕ" not in r["deterministic"] for r in results)
    )
    verdict = "PASS" if good else "FAIL"
    rows = "\n".join(
        f"  {r['index']:>2} | kill={'да' if r['killed'] else 'нет (досрочно)'} "
        f"| learning.epub: {r['learning']} | target.epub: {r['target']} "
        f"| resume: {r['resume']} | детерминизм: {r['deterministic']}"
        for r in results
    )
    content = (
        _header()
        + "s5_en1.epub (1 глава, Intensive), kill-профиль (heartbeat 0.5 c, "
        "lease 1.0 c); 10 итераций: W1 → confirm → W2 → SIGKILL в окне "
        "записи learning.epub.partial → проверка → W3 доводит до конца\n\n"
        + rows
        + f"\n\nпопало в окно kill: {killed}/10; "
        + "повреждённых принятых learning.epub: 0 (каждый принятый файл "
        "проверен read_epub + zip)\n"
        + "детерминизм: in-process повтор S5-сборки = финальный файл "
        "побайтово (все итерации с kill)\n\n"
        + f"S5-9: {verdict} — kill -9 ×10: 0 повреждённых принятых файлов, "
        "после resume — терминальное состояние с валидной книгой\n"
    )
    evidence("s5-9-kill9-no-corruption.txt", content)


# ------------------------------------------------------------------ T-S5-10


def _ev_t10(run: S5Run) -> None:
    report = _s5_report(run)
    target = run.job_dir / "target.epub"
    learning = run.job_dir / "learning.epub"
    with zipfile.ZipFile(target) as tzip, zipfile.ZipFile(learning) as lzip:
        untouched = 0
        changed: list[str] = []
        for name in tzip.namelist():
            if name in ("OEBPS/content.opf", "OEBPS/nav.xhtml"):
                continue
            if lzip.read(name) == tzip.read(name):
                untouched += 1
            else:
                changed.append(name)
    s5_hrefs = [h for h in _nav_hrefs(learning) if "#s5-" in h]
    answers = _answers_doc(learning)
    coverage_line = next(
        (line.strip() for line in answers.splitlines() if "s5-coverage=" in line),
        "(не найдена)",
    )
    good = (
        report["chapters_with_sections"] == 0
        and report["sections"] == {}
        and report["epub"]["epubcheck"]["errors"] == 0
        and not changed
        and not s5_hrefs
        and "Ответов нет" in answers
    )
    verdict = "PASS" if good else "FAIL"
    content = (
        _header()
        + "s5_en1.epub, уровень Minimal\n\n"
        + f"chapters_with_sections: {report['chapters_with_sections']}\n"
        + f"секции: {report['sections']}\n"
        + f"EPUBCheck errors: {report['epub']['epubcheck']['errors']}\n"
        + f"записи target (кроме opf/nav), побайтово сохранено: {untouched}; "
        f"изменено: {changed}\n"
        + f"записи оглавления с «#s5-»: {len(s5_hrefs)}\n"
        + f"ответы: «Ответов нет» = {'есть' if 'Ответов нет' in answers else 'НЕТ'}\n\n"
        + f"строка охвата (verbatim):\n  {coverage_line}\n\n"
        + f"T-S5-10: {verdict} — Minimal: без секций лексики/грамматики/вопросов, "
        "книга собрана (охват + финальный документ)\n"
    )
    evidence("t-s5-10-minimal-no-sections.txt", content)


# ------------------------------------------------------------------ T-S5-11


def _ev_t11(run: S5Run) -> None:
    report = _s5_report(run)
    expected = _artifact_chapters(run.job_dir)
    union: set[str] = set()
    for chapter_types in expected.values():
        union |= chapter_types
    rows: list[str] = []
    all_equal = True
    for chapter_id in sorted(report["sections"]):
        actual = set(report["sections"][chapter_id])
        exp = expected.get(chapter_id, set())
        same = actual == exp
        all_equal = all_equal and same
        rows.append(
            f"  {chapter_id}: секции={sorted(actual)} артефакты={sorted(exp)} "
            f"{'=' if same else '≠'}"
        )
    sets_equal = set(report["sections"]) == set(expected)
    learning = run.job_dir / "learning.epub"
    in_chapters = 0
    with zipfile.ZipFile(learning) as archive:
        for name in archive.namelist():
            if name.endswith(".xhtml") and "study-answers" not in name:
                in_chapters += archive.read(name).decode("utf-8").count("Ответ:")
    answers_count = _answers_doc(learning).count("Ответ:")
    good = (
        {"summary", "vocab", "questions", "exercises"} <= union
        and all_equal
        and sets_equal
        and in_chapters == 0
        and answers_count > 0
    )
    verdict = "PASS" if good else "FAIL"
    content = (
        _header()
        + "s5_en.epub (Intensive): эквивалентность «артефакт ⇔ секция» "
        "по каждой главе\n\n"
        + f"объединение типов по артефактам: {sorted(union)}\n\n"
        + "".join(rows)
        + f"\nнабор глав совпадает: {sets_equal}\n\n"
        + f"«Ответ:» в главах: {in_chapters}; в финальном документе: "
        f"{answers_count}\n\n"
        + f"T-S5-11: {verdict} — Intensive: все существующие секции "
        "присутствуют, ответы только в финальном документе\n"
    )
    evidence("t-s5-11-intensive-all-sections.txt", content)


# ------------------------------------------------------------------ T-S5-12


def _ev_t12(run: S5Run) -> None:
    report = _s5_report(run)
    e6 = _read_json(run, "alignment_report.json")
    answers = _answers_doc(run.job_dir / "learning.epub")
    match = re.search(r'<p[^>]*title="s5-coverage=0\.900000 [^"]*"[^>]*>', answers)
    skipped_lines = [
        line.strip()
        for line in answers.splitlines()
        if "S5 Ten — 3" in line
    ]
    target = run.job_dir / "target.epub"
    learning = run.job_dir / "learning.epub"
    with zipfile.ZipFile(target) as tzip, zipfile.ZipFile(learning) as lzip:
        ch3_untouched = (
            tzip.read("OEBPS/chapter3.xhtml") == lzip.read("OEBPS/chapter3.xhtml")
        )
        ch1_text = lzip.read("OEBPS/chapter1.xhtml").decode("utf-8")
        ch1_has_sections = 'class="s5-section"' in ch1_text
    cov_warnings = [w for w in run.warnings if "0.9" in w or "90" in w]
    skip_warnings = [w for w in run.warnings if "S5" in w and "глав" in w]
    assert run.job.alignment_coverage is not None, "alignment_coverage не задан"
    good = (
        abs(report["coverage"] - 0.9) < 1e-9
        and abs(run.job.alignment_coverage - 0.9) < 1e-9
        and e6["aligned"] == 90
        and e6["unaligned"] == 10
        and {s["chapter_id"] for s in report["skipped"]} == {"ch3"}
        and "ch3" not in report["sections"]
        and ch3_untouched
        and ch1_has_sections
        and bool(match)
        and bool(skipped_lines)
        and bool(cov_warnings)
        and bool(skip_warnings)
    )
    verdict = "PASS" if good else "FAIL"
    content = (
        _header()
        + "s5_10.epub (10 глав × 10 сегментов, Intensive); white-box: после "
        "run_validating_output глава ch3 → unaligned (xpath_mismatch), "
        "jobs.alignment_coverage = 0.9\n\n"
        + f"E6: aligned={e6['aligned']} unaligned={e6['unaligned']} "
        f"total={e6['total']} coverage={e6['coverage']}\n"
        + f"jobs.alignment_coverage: {run.job.alignment_coverage}\n"
        + f"s5_report.json: coverage={report['coverage']} "
        f"skipped={[s['chapter_id'] for s in report['skipped']]}\n"
        + f"chapter3.xhtml побайтово неизменён: {ch3_untouched}\n"
        + f"chapter1.xhtml содержит class=\"s5-section\": {ch1_has_sections}\n\n"
        + "книга (verbatim):\n  "
        + (match.group(0) if match else "(строка охвата не найдена)")
        + "\n  "
        + (skipped_lines[0] if skipped_lines else "(пропущенная глава не найдена)")
        + "\n\nпредупреждения (verbatim):\n"
        + "".join(f"  {w}\n" for w in (*cov_warnings, *skip_warnings))
        + f"\nT-S5-12: {verdict} — искусственный охват 90% отражён в книге, "
        "пропущенная глава перечислена, расхождение с E6 = 0\n"
    )
    evidence("t-s5-12-partial-coverage-90.txt", content)


def _write_evidence(
    env: S5Env,
    lang_runs: dict[str, S5Run],
    *,
    intensive: S5Run,
    card_runs: dict[str, S5Run],
    minimal: S5Run,
    cover90: S5Run,
    kill_log: Path,
) -> None:
    _ev_s5_1(env, lang_runs)
    _ev_s5_2(intensive)
    _ev_s5_3(lang_runs["en"])
    _ev_s5_4(intensive)
    _ev_s5_5(intensive)
    _ev_s5_6(card_runs)
    _ev_s5_7(card_runs["de"])
    _ev_s5_8(intensive)
    _ev_t10(minimal)
    _ev_t11(intensive)
    _ev_t12(cover90)
    print("S5: kill -9 ×10 (самое долгое) …")
    _ev_s5_9(env, kill_log)


# ------------------------------------------------------------------ main


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="s5_evidence_"))
    books_dir = work / "books"
    books_dir.mkdir()
    _synth_s4_en_book(books_dir / "s5_en.epub")
    _synth_s4_en1_book(books_dir / "s5_en1.epub")
    _synth_s5_10_book(books_dir / "s5_10.epub")
    for lang, uid in (
        ("de", "s5ev-de-uid-0020"),
        ("fr", "s5ev-fr-uid-0021"),
        ("it", "s5ev-it-uid-0022"),
        ("nl", "s5ev-nl-uid-0023"),
    ):
        _synth_s5_lang_book(books_dir / f"s5_{lang}.epub", lang, uid)
    server = MockLLMServer()
    server.start()
    env = S5Env(
        server=server,
        base=work,
        books={p.name: p for p in books_dir.glob("*.epub")},
    )
    main_settings, main_db = _make_storage(work, "storage_main", server)
    env.add_profile("main", main_settings, main_db)
    kill_settings, kill_db = _make_storage(
        work,
        "storage_kill",
        server,
        extra="server:\n  heartbeat_seconds: 0.5\n  lease_seconds: 1.0\n",
    )
    env.add_profile("kill", kill_settings, kill_db)
    _SETTINGS.clear()
    _SETTINGS.update(env._settings)
    kill_log = work / "kill_log"
    kill_log.mkdir()
    for index in range(10):
        (kill_log / f"it{index}").mkdir()
    try:
        print("S5: сквозные прогоны (5 языков) …")
        lang_runs = {}
        for lang, book in (
            ("en", "complex.epub"),
            ("de", "lang_de.epub"),
            ("fr", "lang_fr.epub"),
            ("it", "lang_it.epub"),
            ("nl", "lang_nl.epub"),
        ):
            name = "intensive_complex" if lang == "en" else f"intensive_{lang}"
            lang_runs[lang] = env.run(
                name, book, lang=lang, learning_level="Intensive"
            )
        print("S5: en20 (сборка/TOC/ответы/охват) …")
        intensive = env.run(
            "intensive_en20", "s5_en.epub", learning_level="Intensive"
        )
        print("S5: языковые книги для экспорта карточек …")
        card_runs = {}
        for lang in ("de", "nl", "fr", "it"):
            card_runs[lang] = env.run(
                f"cards_{lang}", f"s5_{lang}.epub", lang=lang, learning_level="Intensive"
            )
        print("S5: Minimal …")
        minimal = env.run("minimal_en1", "s5_en1.epub", learning_level="Minimal")
        print("S5: искусственный охват 90% …")
        cover90 = env.run_coverage90("cover90", "s5_10.epub")

        _write_evidence(
            env,
            lang_runs,
            intensive=intensive,
            card_runs=card_runs,
            minimal=minimal,
            cover90=cover90,
            kill_log=kill_log,
        )
    finally:
        env.close()
        server.stop()
    print("s5 evidence done")


if __name__ == "__main__":
    main()
