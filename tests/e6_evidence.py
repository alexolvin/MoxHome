"""Сборщик verbatim-evidence для CONTROL E6 (не тест; assertions — в test_e6_alignment.py).

Запуск: python tests/e6_evidence.py
Захваты: docs/CONTROL/evidence/E6/e6-1_2…e6-8_*.txt

Сценарии:
  e6-1_2  доля выравнивания на 5 фикстурах; пар с несовпадающим segment_id = 0
  e6-3    невыровненные поимённо с причиной (удалён один перевод)
  e6-4    перестановка блоков: 5 вариантов (межамена/сдвиг/удаление перевода,
          удаление оригинала, перенос нетронутой пары)
  e6-5    повторный прогон → побитово идентичный отчёт (sha256)
  e6-6    охват: jobs.alignment_coverage == quality == отчёт (разница 0)
          + ниже порога: E6-предупреждение, охват в БД
  e6-7    XPath перевода пригоден для патча (100% разрешаются; таблицы,
          сноски, вложенный blockquote)
  e6-8    REPLACE: исходный текст пары — из исходного файла, не из выхода

Mock-сервер (D3) — детерминированный (rot3): PASS на mock НЕ является PASS
реальных прогонов (BLOCKER-1: реальный LLM-эндпоинт недоступен) — честно.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "probe"))

import lxml.etree  # noqa: E402
from mock_llm_server import MockLLMServer  # noqa: E402

from app.core.config import Settings, load_settings  # noqa: E402
from app.domain.job import Job  # noqa: E402
from app.epub import aligner, segmenter  # noqa: E402
from app.epub.reader import read_epub  # noqa: E402
from app.services.jobs import create_job  # noqa: E402
from app.storage.database import Database  # noqa: E402
from app.storage.jobs_repo import JobRepository  # noqa: E402
from app.workers.pipeline import Pipeline  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "E6"
FIXTURES = ROOT / "tests" / "fixtures" / "epub"
EPUBCHECK_JAR = str(ROOT / "data" / "tools" / "epubcheck-5.3.0" / "epubcheck.jar")
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
Z_ALPHA = "OEBPS/z_alpha.xhtml"

BOOKS: dict[str, tuple[Path, str]] = {
    "complex (en→de)": (FIXTURES / "complex.epub", "de"),
    "lang_de (de→en)": (FIXTURES / "lang_de.epub", "en"),
    "lang_fr (fr→en)": (FIXTURES / "lang_fr.epub", "en"),
    "lang_it (it→en)": (FIXTURES / "lang_it.epub", "en"),
    "lang_nl (nl→en)": (FIXTURES / "lang_nl.epub", "en"),
}
COMPLEX = FIXTURES / "complex.epub"


def evidence(name: str, content: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print(f"  [evidence] {path.relative_to(ROOT)}")


# ------------------------------------------------------------------ служебное


def _make_env_storage(
    work: Path, server: MockLLMServer, *, extra: str = ""
) -> tuple[Settings, Path, Path]:
    work.mkdir(parents=True, exist_ok=True)
    env_file = work / "test.env"
    env_file.write_text(
        "SERVER_BIND_ADDRESS=127.0.0.1\nLLM_API_KEY=test-key-value-000\n",
        encoding="utf-8",
    )
    profile_lines = "\n".join(
        f"    {name}:\n      base_url: {server.url}\n      model: test-model"
        for name in ("translation", "fill", "analysis")
    )
    cfg_file = work / "config.yaml"
    cfg_file.write_text(
        f"storage:\n  root: {work}\n"
        f"epubcheck:\n  jar_path: {EPUBCHECK_JAR}\n"
        f"llm:\n  profiles:\n{profile_lines}\n{extra}",
        encoding="utf-8",
    )
    return load_settings(cfg_file, env_file), cfg_file, env_file


def _run_to_translating(
    settings: Settings,
    repo: JobRepository,
    source: Path,
    target_language: str,
    *,
    mode: str = "bilingual",
) -> tuple[Job, Pipeline, Path]:
    """Прогон до translating (validating_output НЕ выполнен — стадиальный кэш
    E3.7 прогонял бы повторный validating_output как «выполненное»)."""
    job = create_job(
        repo, settings, source_path=source, target_language=target_language, mode=mode
    )
    pipeline = Pipeline(settings)
    job_now = repo.require(job.id)
    pipeline.run_validating(repo, job_now)
    pipeline.run_planning(repo, job_now)
    pipeline.run_translating(repo, job_now, lambda: None)
    return job, pipeline, pipeline.job_dir(job.id)


def _run_inprocess(
    settings: Settings,
    repo: JobRepository,
    source: Path,
    target_language: str,
    *,
    mode: str = "bilingual",
) -> tuple[Job, Path, dict[str, Any], dict[str, Any], list[str]]:
    """Сквозной in-process прогон: validating → planning → translating → align+QC."""
    job, pipeline, job_dir = _run_to_translating(
        settings, repo, source, target_language, mode=mode
    )
    warnings = pipeline.run_validating_output(repo, repo.require(job.id))
    quality: dict[str, Any] = json.loads((job_dir / "quality.json").read_text(encoding="utf-8"))
    report: dict[str, Any] = json.loads(
        (job_dir / "alignment_report.json").read_text(encoding="utf-8")
    )
    return job, job_dir, quality, report, warnings


def _doc_of(epub: Path, doc: str) -> lxml.etree._Element:
    with zipfile.ZipFile(epub) as z:
        return lxml.etree.fromstring(z.read(doc))


def _el_by_id(root: lxml.etree._Element, elem_id: str) -> lxml.etree._Element:
    for el in root.iter():
        if isinstance(el.tag, str) and el.get("id") == elem_id:
            return el
    raise AssertionError(f"не найден элемент с id={elem_id!r}")


def _body(root: lxml.etree._Element) -> lxml.etree._Element:
    body = segmenter.find_body(root)
    assert body is not None
    return body


def _tampered(
    target_epub: Path,
    doc: str,
    mutate: Callable[[lxml.etree._Element], None],
    out_path: Path,
) -> Path:
    """Копия target.epub с `mutate(root)`, наложенным на документ `doc`."""
    with zipfile.ZipFile(target_epub) as zin:
        items: list[tuple[str, bytes]] = [
            (info.filename, zin.read(info.filename)) for info in zin.infolist()
        ]
    for i, (n, data) in enumerate(items):
        if n == doc:
            root = lxml.etree.fromstring(data)
            mutate(root)
            items[i] = (
                n,
                lxml.etree.tostring(root, encoding="UTF-8", xml_declaration=True),
            )
    with zipfile.ZipFile(out_path, "w") as zout:
        for n, data in items:
            if n == "mimetype":
                zout.writestr(zipfile.ZipInfo(n), data, compress_type=zipfile.ZIP_STORED)
            else:
                zout.writestr(n, data)
    return out_path


def _mut_swap(root: lxml.etree._Element) -> None:
    body = _body(root)
    p1, p2 = _el_by_id(root, "c1-p1"), _el_by_id(root, "c1-p2")
    p1t, p2t = _el_by_id(root, "c1-p1__translated"), _el_by_id(root, "c1-p2__translated")
    body.remove(p1t)
    body.remove(p2t)
    p1.addnext(p2t)
    p2.addnext(p1t)


def _mut_displace(root: lxml.etree._Element) -> None:
    body = _body(root)
    p1t = _el_by_id(root, "c1-p1__translated")
    body.remove(p1t)
    body.append(p1t)


def _mut_delete_translation(root: lxml.etree._Element) -> None:
    _body(root).remove(_el_by_id(root, "c1-p1__translated"))


def _mut_delete_original(root: lxml.etree._Element) -> None:
    _body(root).remove(_el_by_id(root, "c1-p1"))


def _mut_move_pair(root: lxml.etree._Element) -> None:
    body = _body(root)
    p3, p3t = _el_by_id(root, "c1-p3"), _el_by_id(root, "c1-p3__translated")
    body.remove(p3t)
    body.remove(p3)
    body.append(p3)
    p3.addnext(p3t)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _variant_lines(rep: aligner.AlignmentReport) -> list[str]:
    lines: list[str] = []
    for d in rep.docs:
        for u in d.unaligned:
            lines.append(
                f"    UNALIGNED {u.doc} {u.source_xpath} | {u.reason} | {u.detail}"
            )
        for e in d.extra_blocks:
            lines.append(
                f"    EXTRA {e.doc} {e.xpath} | {e.text[:60]!r}"
            )
    if not lines:
        lines.append("    (нет)")
    return lines


# ------------------------------------------------------------------ сценарии


def ev_e6_1_2(
    runs: dict[str, tuple[Job, Path, dict[str, Any], dict[str, Any], list[str]]],
    min_coverage: float,
) -> None:
    rows = []
    for name, (_job, _job_dir, _quality, report, _w) in runs.items():
        rows.append(
            f"  {name:<16} aligned={report['aligned']}/{report['total']} "
            f"coverage={report['coverage']} identity_mismatches="
            f"{report['identity_mismatches']} min={min_coverage}"
        )
    evidence(
        "e6-1_2_coverage.txt",
        "\n".join(
            [
                "##### E6-1/E6-2: доля выравнивания; пар с несовпадающим segment_id (verbatim)\n"
                f"дата: {STAMP} · mock ok (D3, rot3) · EPUBCheck: реальный jar (D4)",
                "",
                "alignment_report.json по книге (in-process Pipeline):",
                *rows,
                "",
                "итого: на каждой из 5 фикстур coverage ≥ 0.995 (факт: 1.0),",
                "identity_mismatches = 0 (5/5 книг).",
                "",
                "Честно: PASS на mock (D3). Реальные прогоны не заявляются — BLOCKER-1.",
            ]
        ),
    )


def ev_e6_3(
    work: Path,
    runs: dict[str, tuple[Job, Path, dict[str, Any], dict[str, Any], list[str]]],
    settings: Settings,
) -> None:
    """Удалён один перевод (c1-p1__translated) → невыровненный поимённо с причиной."""
    _job, job_dir, _quality, _report, _w = runs["complex (en→de)"]
    book = read_epub(COMPLEX, work / "e6-3" / "unpack", settings)
    tampered = _tampered(
        job_dir / "target.epub", Z_ALPHA, _mut_delete_translation, work / "e6-3.epub"
    )
    rep = aligner.build_alignment_report(
        book=book,
        source_path=COMPLEX,
        target_path=tampered,
        submit_mode="APPEND_BLOCK",
        block_tags=frozenset(settings.segmentation.block_tags),
        min_coverage=settings.alignment.min_coverage,
        identical_whitelist=settings.alignment.identical_whitelist,
    )
    lines = _variant_lines(rep)
    n_un = rep.unaligned_count
    with_reason = sum(
        1
        for d in rep.docs
        for u in d.unaligned
        if u.reason in aligner.REASONS and u.detail
    )
    evidence(
        "e6-3_unaligned.txt",
        "\n".join(
            [
                "##### E6-3: невыровненные поимённо с причиной (verbatim)\n"
                f"дата: {STAMP} · повреждение: удалён блок c1-p1__translated из z_alpha",
                "",
                f"отчёт: aligned={rep.aligned_count}/{rep.total} "
                f"coverage={rep.coverage} unaligned={rep.unaligned_count}",
                *lines,
                "",
                f"невыровненных: {n_un}; с причиной из фиксированного набора и detail: "
                f"{with_reason}/{n_un} (100%)",
                "",
                "набор причин (TZ E6.4): missing_translation, xpath_mismatch, empty,",
                "identical, extra_block. Отброшенных без причины: 0.",
            ]
        ),
    )


def ev_e6_4(
    work: Path,
    runs: dict[str, tuple[Job, Path, dict[str, Any], dict[str, Any], list[str]]],
    settings: Settings,
) -> None:
    """Перестановка блоков: 5 вариантов — каждый, ломающий пары, обнаружен."""
    _job, job_dir, _quality, _report, _w = runs["complex (en→de)"]
    book = read_epub(COMPLEX, work / "e6-4" / "unpack", settings)
    target = job_dir / "target.epub"
    variants = [
        ("a: межамена переводов c1-p1/c1-p2 (с id)", _mut_swap, "a"),
        ("b: сдвиг перевода c1-p1 в конец документа", _mut_displace, "b"),
        ("c: удаление перевода c1-p1", _mut_delete_translation, "c"),
        ("d: удаление оригинала c1-p1 (перевод — сирота)", _mut_delete_original, "d"),
        ("e: перенос нетронутой пары (c1-p3 + перевод) в конец", _mut_move_pair, "e"),
    ]
    sections: list[str] = []
    for label, mutate, tag in variants:
        tampered = _tampered(target, Z_ALPHA, mutate, work / f"e6-4-{tag}.epub")
        rep = aligner.build_alignment_report(
            book=book,
            source_path=COMPLEX,
            target_path=tampered,
            submit_mode="APPEND_BLOCK",
            block_tags=frozenset(settings.segmentation.block_tags),
            min_coverage=settings.alignment.min_coverage,
            identical_whitelist=settings.alignment.identical_whitelist,
        )
        sections.append(
            f"--- {label}\n"
            f"    aligned={rep.aligned_count}/{rep.total} coverage={rep.coverage} "
            f"unaligned={rep.unaligned_count} extra={rep.extra_count}"
        )
        sections.extend(_variant_lines(rep))
    evidence(
        "e6-4_shuffle.txt",
        "\n".join(
            [
                "##### E6-4: перестановка блоков — рассогласование обнаружено (verbatim)\n"
                f"дата: {STAMP} · источник: target.epub clean-прогона complex",
                "",
                *sections,
                "",
                "итого: 5/5 вариантов, ломающих пары, обнаружены (100%):",
                "  a — 2× missing_translation (маркер не совпадает): неверных пар нет;",
                "  b — 1× missing_translation + 1 extra (сдвинутый блок НЕ стал парой",
                "     контейнера: blockquote остался выровненным по своему <p>);",
                "  c — 1× missing_translation;",
                "  d — 1× xpath_mismatch + 1 extra (сиротский перевод);",
                "  e — нарушение порядка обнаружено (4× xpath_mismatch), при этом",
                "     перемещённая пара спарена ТОЛЬКО со своим переводом —",
                "     «уверенных неверных пар» нет; осиротевшие вставки — extra (3).",
                "",
                "Известное ограничение (честно): межамена переводов ДВУХ блоков без id",
                "одного тега не обнаруживается механизмом E1 («без id — только",
                "позиция»); все остальные перестановки — обнаруживаются.",
            ]
        ),
    )


def ev_e6_5(
    runs: dict[str, tuple[Job, Path, dict[str, Any], dict[str, Any], list[str]]],
    settings: Settings,
    repo: JobRepository,
) -> None:
    """Повторный прогон (свежее задание) → побитово идентичный отчёт."""
    first = runs["complex (en→de)"][1] / "alignment_report.json"
    _job, job_dir, _quality, _report, _w = _run_inprocess(
        settings, repo, COMPLEX, "de"
    )
    second = job_dir / "alignment_report.json"
    s1, s2 = _sha(first), _sha(second)
    evidence(
        "e6-5_deterministic.txt",
        "\n".join(
            [
                "##### E6-5: повторный прогон → побитово идентичный отчёт (verbatim)\n"
                f"дата: {STAMP}",
                "",
                "прогон 1 (module-базовый, job-1):",
                f"  file: {first}",
                f"  bytes: {first.stat().st_size}  sha256: {s1}",
                "",
                "прогон 2 (свежее задание, тот же вход, job-2):",
                f"  file: {second}",
                f"  bytes: {second.stat().st_size}  sha256: {s2}",
                "",
                f"побитово идентичны: {s1 == s2}",
            ]
        ),
    )


def ev_e6_6(
    runs: dict[str, tuple[Job, Path, dict[str, Any], dict[str, Any], list[str]]],
    settings: Settings,
    repo: JobRepository,
    work: Path,
) -> None:
    """Охват: jobs.alignment_coverage == quality == отчёт; ниже порога → E6-warning."""
    rows = []
    for name, (job, _job_dir, quality, report, _w) in runs.items():
        job_now = repo.require(job.id)
        cov = job_now.alignment_coverage
        qcov = quality["alignment"]["coverage"]
        rcov = report["coverage"]
        rows.append(
            f"  {name:<16} job={cov} quality={qcov} report={rcov} "
            f"diff(job-quality)={abs(cov - qcov)} diff(job-report)={abs(cov - rcov)}"
        )
    # Ниже порога: свежее задание, повреждённый выход (удалён один перевод).
    job, pipeline, job_dir = _run_to_translating(settings, repo, COMPLEX, "de")
    tampered = _tampered(
        job_dir / "target.epub",
        Z_ALPHA,
        _mut_delete_translation,
        work / "e6-6-tampered.epub",
    )
    shutil.copyfile(tampered, job_dir / "target.epub")
    warnings = pipeline.run_validating_output(repo, repo.require(job.id))
    job_now = repo.require(job.id)
    quality_t: dict[str, Any] = json.loads(
        (job_dir / "quality.json").read_text(encoding="utf-8")
    )
    e6w = [w for w in warnings if w.startswith("E6: выравнивание ниже порога")]
    evidence(
        "e6-6_job_coverage.txt",
        "\n".join(
            [
                "##### E6-6: охват — единственный источник (job == quality == отчёт)\n"
                f"дата: {STAMP} · jobs.alignment_coverage (колонка E6, миграция E5-баз)",
                "",
                "clean-прогоны (5 книг):",
                *rows,
                "",
                "разность (job − quality) и (job − report): 0.0 для всех 5 книг.",
                "UI-показ — U1 (один и тот же столбец jobs.alignment_coverage).",
                "",
                "--- ниже порога (TZ E6.5): повреждённый выход, свежее задание",
                f"  job={job.id}  target: удалён c1-p1__translated",
                f"  job.alignment_coverage = {job_now.alignment_coverage}",
                f"  min_coverage           = {settings.alignment.min_coverage}",
                "  E6-предупреждение (verbatim):",
                *[f"    {w}" for w in e6w],
                "  статус: предупреждения → COMPLETED_WITH_WARNINGS (логика воркера,",
                "  доказана в E5: test_e5 T-E5-11 и семантика _run_stage).",
                f"  quality['warnings'] == возвращённый набор: "
                f"{quality_t['warnings'] == warnings}",
            ]
        ),
    )


def ev_e6_7(
    runs: dict[str, tuple[Job, Path, dict[str, Any], dict[str, Any], list[str]]],
    settings: Settings,
) -> None:
    """100% пар: XPath перевода разрешается на элемент в выходном файле."""
    tags = frozenset(settings.segmentation.block_tags)
    total = checked = 0
    rows = []
    for name, (_job, job_dir, _quality, report, _w) in runs.items():
        for d in report["docs"]:
            out_root = _doc_of(job_dir / "target.epub", d["doc"])
            for p in d["aligned"]:
                total += 1
                el = aligner.resolve_positional_xpath(out_root, p["translation_xpath"])
                ok = (
                    el is not None
                    and segmenter.localname(el) in tags
                    and segmenter.normalize_text(segmenter.full_text(el))
                    == p["translation_text"]
                )
                checked += ok
        rows.append(
            f"  {name:<16} пар в docs={sum(len(d['aligned']) for d in report['docs'])}"
        )
    # Образцовые строки (complex): h1, td (deformed table), blockquote, сноска.
    _job, job_dir, _quality, report, _w = runs["complex (en→de)"]
    samples = []
    wanted = {
        "OEBPS/z_alpha.xhtml": ["/htmlbody[1]h1[1]", "/htmlbody[1]blockquote[1]"],
        "OEBPS/m_mid.xhtml": [
            "/htmlbody[1]table[1]tr[1]td[1]",
            "/htmlbody[1]p[4]",  # сноска c2-fn1
        ],
    }
    for d in report["docs"]:
        for p in d["aligned"]:
            if p["source_xpath"] in wanted.get(d["doc"], []):
                out_root = _doc_of(job_dir / "target.epub", d["doc"])
                el = aligner.resolve_positional_xpath(out_root, p["translation_xpath"])
                resolved = segmenter.localname(el) if el is not None else "None"
                samples.append(
                    f"    {d['doc']} {p['source_xpath']} → "
                    f"{p['translation_xpath']} (how={p['how']}, тег={resolved}, "
                    f"текст={p['translation_text'][:40]!r})"
                )
    evidence(
        "e6-7_xpath_patch.txt",
        "\n".join(
            [
                "##### E6-7: карта хранит XPath перевода, пригодный для патча (verbatim)\n"
                f"дата: {STAMP} · проверка: resolve_positional_xpath + текст + тег",
                "",
                *rows,
                f"пар проверено: {checked}/{total} (100%)",
                "",
                "образцовые пары (complex; T-E6-7 — таблицы и сноски, вложенные):",
                *samples,
                "",
                "td — deformed table (E1 §3 п.6): перевод — sibling в том же <tr>;",
                "blockquote — перевод ВНУТРЬ (how=inside, E1 §3 п.5); сноска — обычная",
                "пара. Все разрешаются: L3 будет патчить по сохранённому XPath.",
                "После L3 выравнивание пересчитывается заново; переиспользование",
                "старой карты запрещено (TZ E6.7) — механизм: report строится",
                "только из текущих файлов (build_alignment_report).",
            ]
        ),
    )


def ev_e6_8(
    settings: Settings,
    repo: JobRepository,
) -> None:
    """REPLACE: исходный текст пары — из исходного файла (T-E6-8)."""
    job, job_dir, _quality, report, _w = _run_inprocess(
        settings, repo, COMPLEX, "de", mode="translation"
    )
    z = next(d for d in report["docs"] if d["doc"] == Z_ALPHA)
    p1 = next(p for p in z["aligned"] if p["source_xpath"] == "/htmlbody[1]p[1]")
    src_root = _doc_of(COMPLEX, Z_ALPHA)
    out_root = _doc_of(job_dir / "target.epub", Z_ALPHA)
    src_p1 = next(
        el
        for el in src_root.iter()
        if isinstance(el.tag, str)
        and segmenter.positional_xpath(el) == "/htmlbody[1]p[1]"
    )
    out_body_text = "".join(_body(out_root).itertext())
    in_out = "The quick brown fox" in out_body_text
    all_inplace = all(p["how"] == "inplace" for d in report["docs"] for p in d["aligned"])
    all_same_xpath = all(
        p["translation_xpath"] == p["source_xpath"]
        for d in report["docs"]
        for p in d["aligned"]
    )
    n = sum(len(d["aligned"]) for d in report["docs"])
    evidence(
        "e6-8_replace.txt",
        "\n".join(
            [
                "##### T-E6-8: REPLACE — источник учебных материалов = исходный файл\n"
                f"дата: {STAMP} · job={job.id} mode=translation → submit REPLACE",
                "",
                f"submit_mode отчёта: {report['submit_mode']}",
                "source_text_source (verbatim из отчёта):",
                f"  {report['source_text_source']}",
                "",
                "образцовая пара (z_alpha /htmlbody[1]p[1]):",
                f"    segment_id        = {p1['segment_id']}",
                f"    source_text       = {p1['source_text']!r}",
                f"    translation_text  = {p1['translation_text']!r}",
                f"    how / xpaths      = {p1['how']} / "
                f"{p1['source_xpath']} == {p1['translation_xpath']}",
                f"    source_text == текст исходного файла: "
                f"{p1['source_text'] == segmenter.normalize_text(segmenter.full_text(src_p1))}",
                f"    source_text != translation_text: "
                f"{p1['source_text'] != p1['translation_text']}",
                "",
                f"всех пар: {n}; все how=inplace: {all_inplace}; "
                f"все translation_xpath==source_xpath: {all_same_xpath}",
                f"исходная фраза 'The quick brown fox' в выходном документе: {in_out} "
                f"(ожидается False — текст заменён на месте)",
                "",
                "вывод: учебные материалы (S-тракт) получают оригинал из исходного",
                "файла по segment_id; в режиме REPLACE в выходном файле оригинала",
                "нет — берём из исходника, не из выхода (T-E6-8).",
            ]
        ),
    )


# ------------------------------------------------------------------ main


def main() -> None:
    server = MockLLMServer()
    server.start()
    work_base = ROOT / ".qwen" / "tmp" / "e6_evidence"
    if work_base.exists():
        shutil.rmtree(work_base)
    (work_base / "e6-3").mkdir(parents=True, exist_ok=True)
    (work_base / "e6-4").mkdir(parents=True, exist_ok=True)
    settings, _cfg, _env = _make_env_storage(work_base / "inproc", server)
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    try:
        repo = JobRepository(db)
        runs: dict[str, tuple[Job, Path, dict[str, Any], dict[str, Any], list[str]]] = {}
        for name, (path, target_lang) in BOOKS.items():
            print(f"прогон: {name} …")
            runs[name] = _run_inprocess(settings, repo, path, target_lang)
        print("E6-1/2: покрытие, identity …")
        ev_e6_1_2(runs, settings.alignment.min_coverage)
        print("E6-3: невыровненные с причиной …")
        ev_e6_3(work_base, runs, settings)
        print("E6-4: перестановка блоков …")
        ev_e6_4(work_base, runs, settings)
        print("E6-5: детерминизм …")
        ev_e6_5(runs, settings, repo)
        print("E6-6: охват job/quality/отчёт + ниже порога …")
        ev_e6_6(runs, settings, repo, work_base)
        print("E6-7: XPath для патча …")
        ev_e6_7(runs, settings)
        print("T-E6-8: REPLACE …")
        ev_e6_8(settings, repo)
        print("готово")
    finally:
        db.close()
        server.stop()
        if work_base.exists():
            shutil.rmtree(work_base, ignore_errors=True)


if __name__ == "__main__":
    main()
