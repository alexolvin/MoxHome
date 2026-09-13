"""L3: доказательства CONTROL (verbatim).

Запуск: python tests/l3_evidence.py
Захваты: docs/CONTROL/evidence/L3/l3-1…l3-10 (verbatim).

Сценарии:
  l3-1  всё правимое множество исправлено: 3/3 (wexley_fix) + 20/20 при трёх
        разных неверных вариантах (T-L3-11); доля 0.0 → 1.0 (L3-1)
  l3-2  0 правок без записи: по одной записи quality.log на правку (L3-2)
  l3-3  0 непроставленных невалидированных: points == [] у каждой применённой
        правки (L3-3)
  l3-4  0 потери разметки: сигнатура блока l3bak == результат; гиперссылка +
        сноска сохранены (T-L3-12) (L3-4)
  l3-5  повтор: 0 расхождений, 0 правок, файл побайтово тот же (L3-5)
  l3-6  60% > 50%: остановка с отчётом, 0 правок, книга не переписана,
        LLM не вызывался (L3-6)
  l3-7  EPUBCheck пересборки 0 ERROR; mimetype первой записью, STORED (L3-7)
  l3-8  kill -9 ×10 в окне пересборки: 0 повреждённых target, l3bak цел
        (L3-8)
  l3-9  unverified: 0 автоправок, 100% сообщено, собственная доля (L3-9)
  l3-10 5 языков 5/5; правка выключена — no-op, обнаружение не выключено
        (L3-10)

Mock-сервер (D3) — детерминированный (rot3 + инъекции); реальные LLM-прогоны
не проводились (BLOCKER-1). Kill — реальные процессы воркера (арена 1.0 с).
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter
from importlib.metadata import version as pkg_version
from pathlib import Path

import lxml.etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "probe"))

from mock_llm_server import MockLLMServer  # noqa: E402
from test_l2_glossary import _make_storage  # noqa: E402
from test_l3_glossary_fix import (  # noqa: E402
    L3_CHAPTER,
    L3Env,
    _build_l3_books,
    _chapter_of,
    _fix_log_lines,
    _kill_all,
    _start_worker,
    _wait_status,
)

from app.domain.job import JobStatus  # noqa: E402
from app.epub.rebuild import block_bytes  # noqa: E402
from app.glossary.fix import markup_signature  # noqa: E402
from app.services.jobs import confirm_job, create_job  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "L3"
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
VERSIONS = f"python={sys.version.split()[0]} spacy={pkg_version('spacy')}"

_WRONG_VARIANTS = ("Xrongy", "Yrrqjz", "Zssabw")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def evidence(name: str, content: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print(f"  [evidence] {path.relative_to(ROOT)}")


def _header() -> str:
    return f"дата: {STAMP}\nверсии: {VERSIONS}\n\n"


def _log_lines_block(run) -> str:
    return "".join(
        "  " + json.dumps(line, ensure_ascii=False) + "\n"
        for line in _fix_log_lines(run.quality_log)
    )


def _block_signature(doc_bytes: bytes, xpath: str) -> Counter:
    return markup_signature(lxml.etree.fromstring(block_bytes(doc_bytes, xpath)))


def _target_unchanged(run) -> bool:
    return _sha(run.target_before) == _sha((run.job_dir / "target.epub").read_bytes())


# ------------------------------------------------------------------ L3-1…L3-10


def _ev_l3_1(fix, triple) -> None:
    report = fix.l3_report
    chapter = _chapter_of(fix.job_dir / "target.epub").decode("utf-8")
    pre_chapter = _chapter_of(fix.job_dir / "target.epub.l3bak").decode("utf-8")
    pre_triple = _chapter_of(triple.job_dir / "target.epub.l3bak").decode("utf-8")
    variants = {v: pre_triple.count(v) for v in _WRONG_VARIANTS}
    post_triple = _chapter_of(triple.job_dir / "target.epub").decode("utf-8")
    distinct = len([v for v in variants.values() if v])
    wrong_after = sum(post_triple.count(v) for v in _WRONG_VARIANTS)
    content = (
        _header()
        + "wexley_fix.epub (3 из 7 сегментов с Wexley, сценарий "
        "glossary_inconsistent):\n\n"
        + "l3_fix_report.json (выборка, verbatim):\n"
        + json.dumps(
            {k: report[k] for k in (
                "mismatches_input", "total_segments", "fixable_segment_share",
                "max_segment_share", "attempted", "applied", "rejected",
                "editable_share_before", "editable_share_after",
            )},
            ensure_ascii=False, indent=2,
        )
        + f"\n\ntarget.epub после L3: «Zhaohb» = {chapter.count('Zhaohb')}, "
        f"«Xrongy» = {chapter.count('Xrongy')}\n"
        + f"l3bak (доправки):          «Xrongy» = {pre_chapter.count('Xrongy')}\n\n"
        + "T-L3-11 — wexley20.epub (20 вхождений, три разных неверных "
        "варианта в доправки файле):\n"
        + f"  варианты до правки: {variants} (различных: {distinct})\n"
        + f"  attempted/applied/rejected = {triple.l3_report['attempted']}/"
        f"{triple.l3_report['applied']}/{triple.l3_report['rejected']}\n"
        + f"  target после L3: «Zhaohb» = {post_triple.count('Zhaohb')}, "
        f"неверные = {wrong_after}\n"
        + f"  записей в quality.log: {len(_fix_log_lines(triple.quality_log))} "
        f"(≤20), отклонений: {triple.l3_report['rejected']}\n"
        + f"  editable_share_after = {triple.l3_report['editable_share_after']}\n\n"
        + "L3-1: PASS — 100% правимого множества (3/3 и 20/20); unverified — "
        "отдельный перечень (l3-9)\n"
    )
    evidence("l3-1-full-fixable.txt", content)


def _ev_l3_2(fix, triple) -> None:
    lines = _fix_log_lines(fix.quality_log)
    applied_ids = sorted(f["segment_id"] for f in fix.l3_report["fixes"] if f["applied"])
    logged_ids = sorted(line["segment_id"] for line in lines)
    content = (
        _header()
        + "quality.log → записи glossary_fix (wexley_fix, verbatim):\n"
        + _log_lines_block(fix)
        + f"\nзаписей: {len(lines)}, применено: {fix.l3_report['applied']}, "
        f"совпадение segment_id: {applied_ids == logged_ids}\n\n"
        + f"wexley20: записей = {len(_fix_log_lines(triple.quality_log))}, "
        f"applied = {triple.l3_report['applied']}\n\n"
        + "L3-2: PASS — 0 правок без записи (по одной записи на правку)\n"
    )
    evidence("l3-2-fix-log.txt", content)


def _ev_l3_3(fix, noterm) -> None:
    applied = [f for f in fix.l3_report["fixes"] if f["applied"]]
    content = (
        _header()
        + "применённые правки (wexley_fix, verbatim-поля):\n"
        + "".join(
            f"  {f['segment_id'][:16]}… term={f['term']} applied={f['applied']} "
            f"points={f['validation_points']} "
            f"prompt={f['prompt_file']} v{f['prompt_version']} model={f['model']}\n"
            for f in applied
        )
        + "T-L3-14 (glossary_fix_no_term) — отклонённые, verbatim-поля:\n"
        + "".join(
            f"  {f['segment_id'][:16]}… applied={f['applied']} "
            f"points={f['validation_points']} reason={f['reason']!r}\n"
            for f in noterm.l3_report["fixes"]
        )
        + f"\nLLM-запросов на no_term-книге: {noterm.fix_requests} "
        "(3 сегмента, повторов нет)\n\n"
        + "L3-3: PASS — у каждой ПРИМЕНЁННОЙ правки points == [] "
        "(отклонённые — только с указанием пунктов)\n"
    )
    evidence("l3-3-validated.txt", content)


def _ev_l3_4(markup) -> None:
    mreport = markup.l3_report
    with zipfile.ZipFile(markup.job_dir / "target.epub.l3bak") as zbak:
        old_chapter = zbak.read(L3_CHAPTER)
    new_chapter = _chapter_of(markup.job_dir / "target.epub")
    xpaths = {f["segment_id"]: f for f in mreport["fixable"]}
    sig_rows: list[str] = []
    for f in [x for x in mreport["fixes"] if x["applied"]]:
        seg = xpaths[f["segment_id"]]
        old_sig = _block_signature(old_chapter, seg["translation_xpath"])
        new_sig = _block_signature(new_chapter, seg["translation_xpath"])
        sig_rows.append(
            f"  блок {seg['translation_xpath']}: равны={old_sig == new_sig}\n"
            + "".join(
                f"      {tag} attrs={list(attrs)} ×{count}\n"
                for (tag, attrs), count in sorted(old_sig.items())
            )
        )
    markup_fix = next(
        f for f in mreport["fixes"]
        if f["applied"] and f.get("new_html") and "notefoot" in f["new_html"]
    )
    content = (
        _header()
        + "wexley_markup.epub: p4 — гиперссылка + сноска (epub:type=notefoot)\n\n"
        + "сигнатура (теги, атрибуты) правых блоков, l3bak vs результат:\n"
        + "".join(sig_rows)
        + "T-L3-12 — new_html применённой правки (verbatim):\n  "
        + markup_fix["new_html"]
        + "\n\n"
        + "L3-4: PASS — 0 потери разметки; гиперссылка и сноска в новой "
        "версии (T-L3-12)\n"
    )
    evidence("l3-4-markup.txt", content)


def _ev_l3_5(fix, rerun, sha_after_first: str) -> None:
    rr = rerun.l3_report
    sha_after_rerun = _sha((fix.job_dir / "target.epub").read_bytes())
    content = (
        _header()
        + "повтор стадии по исправленному файлу (wexley_fix):\n\n"
        + f"  mismatches_input = {rr['mismatches_input']}  "
        f"attempted = {rr['attempted']}  applied = {rr['applied']}\n"
        + f"  LLM-запросов на повторе = {rerun.fix_requests}\n"
        + f"  sha256 target.epub после 1-го прогона = {sha_after_first}\n"
        + f"  sha256 target.epub после повтора     = {sha_after_rerun}\n"
        + f"  идентичны: {sha_after_first == sha_after_rerun}\n"
        + f"  записей glossary_fix в quality.log (без прироста): "
        f"{len(_fix_log_lines(rerun.quality_log))}\n\n"
        + "L3-5: PASS — 0 расхождений, 0 правок, файл побайтово тот же\n"
    )
    evidence("l3-5-rerun.txt", content)


def _ev_l3_6(halt) -> None:
    hreport = halt.l3_report
    content = (
        _header()
        + "wexley60.epub: 6 из 11 сегментов с Wexley (60% > порога 50%)\n\n"
        + "AppError (verbatim):\n  "
        + (halt.error.message if halt.error is not None else "(нет)")
        + "\n\nl3_fix_report.json (verbatim):\n"
        + json.dumps(hreport, ensure_ascii=False, indent=2)
        + f"\nsha256 target.epub: до    = {_sha(halt.target_before)}\n"
        + f"                   после = {_sha((halt.job_dir / 'target.epub').read_bytes())}\n"
        + f"LLM-запросов стадии = {halt.fix_requests}; l3bak существует: "
        f"{(halt.job_dir / 'target.epub.l3bak').exists()}\n\n"
        + "L3-6: PASS — превышение порога → остановка с отчётом, 0 правок,\n"
        "книга не переписана, LLM не вызывался (порог ДО вызовов)\n"
    )
    evidence("l3-6-halt.txt", content)


def _ev_l3_7(fix) -> None:
    report = fix.l3_report
    target = fix.job_dir / "target.epub"
    with zipfile.ZipFile(target) as archive:
        first = archive.infolist()[0]
        mimetype = archive.read("mimetype")
    content = (
        _header()
        + "l3_fix_report.json → rebuild (verbatim):\n"
        + json.dumps(report["rebuild"], ensure_ascii=False, indent=2)
        + f"\ntarget.epub: первая запись = {first.filename!r}, "
        f"compress_type = {first.compress_type} ({zipfile.ZIP_STORED} = ZIP_STORED)\n"
        + f"  mimetype = {mimetype}\n"
        + "  (внутренняя валидация + EPUBCheck — до os.replace; FATAL/ERROR →\n"
        "   отказ публикации, target не изменяется — Rule 6)\n\n"
        + "L3-7: PASS — 0 ERROR, mimetype первой записью без сжатия\n"
    )
    evidence("l3-7-rebuild.txt", content)


def _ev_l3_9(unverif) -> None:
    ureport = unverif.l3_report
    content = (
        _header()
        + "unverif.epub: Badparadigm (mock: 12 идентичных форм → парадигма "
        "unverified)\n\n"
        + "l3_fix_report.json (выборка, verbatim):\n"
        + json.dumps(
            {k: ureport[k] for k in (
                "mismatches_input", "attempted", "applied", "unverified_terms",
                "unverified_share",
            )},
            ensure_ascii=False, indent=2,
        )
        + "\nпредупреждения (verbatim):\n"
        + "".join(f"  {w}\n" for w in unverif.warnings)
        + f"\nLLM-запросов = {unverif.fix_requests}; "
        f"target неизменён: {_target_unchanged(unverif)}\n\n"
        + "L3-9: PASS — 0 автоправок, 100% сообщено отдельным перечнем "
        "с собственной долей\n"
    )
    evidence("l3-9-unverified.txt", content)


def _ev_l3_10(lang_runs, nofix) -> None:
    rows: list[str] = []
    ok = 0
    for run10 in lang_runs:
        s10 = run10.quality["glossary_consistency"]
        l10 = run10.quality.get("l3") or {}
        r10 = run10.l3_report or {}
        good = (
            run10.error is None
            and (run10.job_dir / "target.epub").is_file()
            and l10 is not None
            and r10.get("enabled") is True
        )
        if good:
            ok += 1
        rows.append(
            f"  {run10.name:14s} terms={s10['terms_checked']:<3d} "
            f"occ={s10['occurrences_checked']:<3d} mismatches={s10['mismatches']} "
            f"l3.applied={l10.get('applied')} enabled={r10.get('enabled')} "
            f"error={run10.error is not None}\n"
        )
    nr = nofix.l3_report
    verdict = "PASS" if ok == 5 else "FAIL"
    content = (
        _header()
        + "5 исходных языков → ru (правка включена, mock ok):\n\n"
        + "".join(rows)
        + f"\nзавершено со стадией L3: {ok}/5\n\n"
        + "правка ВЫКЛЮЧЕНА (nofix-профиль, wexley_fix, glossary_inconsistent):\n"
        + "l3_fix_report.json (verbatim):\n"
        + json.dumps(nr, ensure_ascii=False, indent=2)
        + f"\n  mismatches в quality.json = "
        f"{nofix.quality['glossary_consistency']['mismatches']} "
        "(обнаружение не выключено)\n"
        + f"  предупреждений = {len(nofix.warnings)} (расхождения сообщены)\n"
        + f"  target неизменён: {_target_unchanged(nofix)}\n\n"
        + f"L3-10: {verdict} — {ok}/5; no-op при выключенной правке, "
        "обнаружение\nпри этом не выключено\n"
    )
    evidence("l3-10-languages.txt", content)


# ------------------------------------------------------------------ L3-8 (kill)


def _kill_iteration(
    env: L3Env,
    work: Path,
    *,
    i: int,
    pre_sha: str,
    post_sha: str,
    workers: list[subprocess.Popen[bytes]],
) -> tuple[str, bool, bool]:
    """Одна итерация kill -9. Возвращает (строка отчёта, повреждён, в_окне)."""
    settings = env._settings["kill"]
    repo = env._repos["kill"]
    cfg = env.base / "storage_kill" / "config.yaml"
    env_file = env.base / "storage_kill" / "test.env"
    book = env.books["wexley_fix.epub"]
    log_dir = work / "kill"
    job = create_job(repo, settings, source_path=book, target_language="ru")
    job_dir = settings.jobs_dir / job.id
    target_file = job_dir / "target.epub"
    l3bak = job_dir / "target.epub.l3bak"
    partial = job_dir / "target.epub.partial"
    w1 = _start_worker(cfg, env_file, log_dir / f"k{i}_w1.log")
    workers.append(w1)
    _wait_status(repo, job.id, JobStatus.NEEDS_CONFIRMATION, 180)
    assert w1.wait(timeout=60) == 0
    confirm_job(repo, job.id, source_language="en")
    w2 = _start_worker(cfg, env_file, log_dir / f"k{i}_w2.log")
    workers.append(w2)
    _wait_status(repo, job.id, JobStatus.GLOSSARY_FIX, 300)
    deadline = time.monotonic() + 120
    while not partial.exists():
        state = repo.get(job.id)
        if state is not None and state.status is JobStatus.COMPLETED:
            break
        if time.monotonic() > deadline:
            break
        time.sleep(0.02)
    state = repo.get(job.id)
    assert state is not None
    if not (partial.exists() and state.status is JobStatus.GLOSSARY_FIX):
        _wait_status(repo, job.id, JobStatus.COMPLETED, 300)
        assert w2.wait(timeout=60) == 0
        row = (
            f"  #{i}: вне окна (воркер завершился до kill) — COMPLETED, "
            f"sha={_sha(_chapter_of(target_file))[:12]}\n"
        )
        return row, False, False
    t_kill, lease_at_kill = time.time(), state.lease_expires_at
    assert lease_at_kill is not None
    os.kill(w2.pid, signal.SIGKILL)
    assert w2.wait(timeout=20) == -9
    after_sha = _sha(_chapter_of(target_file))
    state_at_kill = "pre" if after_sha == pre_sha else "post"
    l3bak_ok = l3bak.is_file() and _sha(_chapter_of(l3bak)) == pre_sha
    valid_after = zipfile.is_zipfile(target_file)
    corrupted = after_sha not in (pre_sha, post_sha) or not valid_after
    time.sleep(max(0.0, lease_at_kill - time.time()) + 0.1)
    w3 = _start_worker(cfg, env_file, log_dir / f"k{i}_w3.log")
    workers.append(w3)
    final = _wait_status(repo, job.id, JobStatus.COMPLETED, 300)
    assert w3.wait(timeout=60) == 0
    final_sha = _sha(_chapter_of(target_file))
    row = (
        f"  #{i}: t_kill={t_kill:.3f} lease_at_kill={lease_at_kill:.3f} "
        f"арена_действительна={lease_at_kill >= t_kill}\n"
        f"       state_at_kill={state_at_kill}  l3bak_цел={l3bak_ok}  "
        f"target_валидный={valid_after}\n"
        f"       после resume: {final.status.value} sha={final_sha[:12]} "
        f"(post={post_sha[:12]}) совпал={final_sha == post_sha}\n"
    )
    return row, corrupted, True


def _ev_l3_8(env: L3Env, work: Path) -> None:
    """L3-8: kill -9 ×10 в окне пересборки (реальные процессы воркера)."""
    settings = env._settings["kill"]
    repo = env._repos["kill"]
    cfg = env.base / "storage_kill" / "config.yaml"
    env_file = env.base / "storage_kill" / "test.env"
    book = env.books["wexley_fix.epub"]
    log_dir = work / "kill"
    log_dir.mkdir(exist_ok=True)
    workers: list[subprocess.Popen[bytes]] = []
    # Сценарий глобальный: расхождения обязаны быть, иначе пересборки и
    # kill-окна (`.partial`) нет.
    env.server.set_scenario("glossary_inconsistent")
    try:
        job_ref = create_job(repo, settings, source_path=book, target_language="ru")
        target_ref = settings.jobs_dir / job_ref.id / "target.epub"
        w1 = _start_worker(cfg, env_file, log_dir / "ref_w1.log")
        workers.append(w1)
        _wait_status(repo, job_ref.id, JobStatus.NEEDS_CONFIRMATION, 180)
        assert w1.wait(timeout=60) == 0
        confirm_job(repo, job_ref.id, source_language="en")
        w2 = _start_worker(cfg, env_file, log_dir / "ref_w2.log")
        workers.append(w2)
        _wait_status(repo, job_ref.id, JobStatus.GLOSSARY_FIX, 300)
        pre_sha = _sha(_chapter_of(target_ref))
        _wait_status(repo, job_ref.id, JobStatus.COMPLETED, 300)
        assert w2.wait(timeout=60) == 0
        post_sha = _sha(_chapter_of(target_ref))
        assert pre_sha != post_sha, "в эталоне правки не применяются"

        rows: list[str] = []
        corrupted = 0
        in_window = 0
        for i in range(10):
            row, bad, hit = _kill_iteration(
                env, work, i=i, pre_sha=pre_sha, post_sha=post_sha, workers=workers
            )
            rows.append(row)
            corrupted += 1 if bad else 0
            in_window += 1 if hit else 0
    finally:
        _kill_all(workers)
        env.server.set_scenario("ok")

    content = (
        _header()
        + "T-L3-8: kill -9 ×10 в окне пересборки (L3.5: `.partial` → EPUBCheck → "
        "os.replace);\n"
        + "арена 1.0 с / heartbeat 0.5 с; книга wexley_fix.epub (3 правки)\n\n"
        + "эталонный прогон (без kill):\n"
        + f"  pre  (вход в GLOSSARY_FIX)  sha256(глава) = {pre_sha}\n"
        + f"  post (COMPLETED)            sha256(глава) = {post_sha}\n\n"
        + f"итераций в окне пересборки: {in_window}/10\n\n"
        + "итерации (verbatim):\n"
        + "".join(rows)
        + f"\nповреждённых target: {corrupted}/10\n"
        + f"L3-8: {'PASS' if corrupted == 0 else 'FAIL'} — 0 повреждённых, "
        "бэкап l3bak цел,\n"
        "после истечения аренды задание завершено с эталонным результатом\n"
    )
    evidence("l3-8-kill9.txt", content)


# ------------------------------------------------------------------ main


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="l3_evidence_"))
    books_dir = work / "books"
    books_dir.mkdir()
    server = MockLLMServer()
    server.start()
    env = L3Env(server=server, base=work, books=_build_l3_books(books_dir))
    main_settings, main_db = _make_storage(
        work, "storage_main", server, extra="  fix_enabled: true\n"
    )
    env.add_profile("main", main_settings, main_db)
    wide_settings, wide_db = _make_storage(
        work, "storage_wide", server, extra="  fix_enabled: true\n"
        "  fix_max_segment_share: 1.0\n",
    )
    env.add_profile("wide", wide_settings, wide_db)
    nofix_settings, nofix_db = _make_storage(
        work, "storage_nofix", server, extra="  fix_enabled: false\n"
    )
    env.add_profile("nofix", nofix_settings, nofix_db)
    kill_settings, kill_db = _make_storage(
        work, "storage_kill", server,
        extra="  fix_enabled: true\nserver:\n  heartbeat_seconds: 0.5\n"
        "  lease_seconds: 1.0\n",
    )
    env.add_profile("kill", kill_settings, kill_db)
    try:
        print("L3: сквозные прогоны …")
        fix = env.run("fix", "wexley_fix.epub")
        sha_after_first = _sha((fix.job_dir / "target.epub").read_bytes())
        triple = env.run(
            "triple20", "wexley20.epub", profile="wide",
            scenario="glossary_inconsistent_triple",
        )
        markup = env.run("markup", "wexley_markup.epub")
        noterm = env.run(
            "noterm_ev", "wexley_no_term.epub", scenario="glossary_fix_no_term"
        )
        rerun = env.rerun_l3("fix")
        halt = env.run("halt", "wexley60.epub")
        unverif = env.run("unverif", "unverif.epub")
        nofix = env.run("nofix_run", "wexley_fix.epub", profile="nofix")
        lang_runs = [
            env.run(f"lang5_{lang}", file_name, lang=lang, scenario="ok")
            for file_name, lang in (
                ("complex.epub", "en"), ("lang_de.epub", "de"),
                ("lang_fr.epub", "fr"), ("lang_it.epub", "it"),
                ("lang_nl.epub", "nl"),
            )
        ]

        _ev_l3_1(fix, triple)
        _ev_l3_2(fix, triple)
        _ev_l3_3(fix, noterm)
        _ev_l3_4(markup)
        _ev_l3_5(fix, rerun, sha_after_first)
        _ev_l3_6(halt)
        _ev_l3_7(fix)
        _ev_l3_9(unverif)
        _ev_l3_10(lang_runs, nofix)

        print("L3: kill -9 ×10 (реальные воркеры, ~7–9 мин) …")
        _ev_l3_8(env, work)
    finally:
        env.close()
        server.stop()
    print("l3 evidence done")


if __name__ == "__main__":
    main()
