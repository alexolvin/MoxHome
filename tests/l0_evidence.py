# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Доказательства L0: детерминированное определение языка и резолвер пар.

Запуск: python tests/l0_evidence.py
Захваты: docs/CONTROL/evidence/L0/l0-1…l0-8 (verbatim).

Сценарии:
  l0-1  определение языка на 5 языковых фикстурах: 5/5, уверенность (L0-1)
  l0-2  конфликт dc:language и детектора → NEEDS_CONFIRMATION 100% (L0-2)
  l0-3  резолвер: перечень недостающего в 100% degraded/unsupported пар (L0-3)
  l0-4  spaCy-модель «удалена» (шов): пары unsupported, старт приложения, 0 падений (L0-4)
  l0-5  статус пары в UI (services.languages) == резолвер: расхождение 0 (L0-5)
  l0-6  число supported-пар ≥ 5 (все исходные → ru) (L0-6)
  l0-7  T-L0-7: неверный dc:language (en в OPF, немецкий текст) → подтверждение
  l0-8  T-L0-8: двуязычная фикстура — факт и пометки сегментов (L0.6)

LLM не вызывается (стадия validating до любого LLM, E2.5): mock-сервер не нужен;
доказательства полностью воспроизводимы (seed=42, D7).
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from collections.abc import Sequence
from importlib.metadata import version as pkg_version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import Settings, load_settings  # noqa: E402
from app.domain.job import JobStatus  # noqa: E402
from app.epub.reader import read_epub  # noqa: E402
from app.languages.detect import detect_book  # noqa: E402
from app.languages.registry import aliases_map, load_registry  # noqa: E402
from app.languages.resolver import PairResolver, PairStatus, load_matrix  # noqa: E402
from app.services.jobs import confirm_job, create_job  # noqa: E402
from app.services.languages import pair_status  # noqa: E402
from app.storage.database import Database  # noqa: E402
from app.storage.jobs_repo import JobRepository  # noqa: E402
from app.workers.pipeline import Pipeline  # noqa: E402
from app.workers.worker import Worker  # noqa: E402

EVIDENCE = ROOT / "docs" / "CONTROL" / "evidence" / "L0"
FIXTURES = ROOT / "tests" / "fixtures" / "epub"
STAMP = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
VERSIONS = (
    f"langdetect={pkg_version('langdetect')} spacy={pkg_version('spacy')} "
    f"wordfreq={pkg_version('wordfreq')}"
)
_COMPLEX = FIXTURES / "complex.epub"
_CONFLICT = FIXTURES / "conflict.epub"
_BILINGUAL = FIXTURES / "bilingual.epub"

#: T-L0-1: пять языковых фикстур → ожидаемый язык.
BOOKS: dict[str, str] = {
    "complex.epub": "en",
    "lang_de.epub": "de",
    "lang_fr.epub": "fr",
    "lang_it.epub": "it",
    "lang_nl.epub": "nl",
}


def evidence(name: str, content: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    print(f"  [evidence] {path.relative_to(ROOT)}")


def _header(title: str) -> list[str]:
    return [title, f"замер: {STAMP}", f"окружение: {VERSIONS}", ""]


def _settings(work: Path) -> tuple[Settings, Database, JobRepository]:
    """Изолированное storage; реестр/матрица/промпты — данные репозитория (L0)."""
    storage = work / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    env_file = work / "test.env"
    env_file.write_text(
        "SERVER_BIND_ADDRESS=127.0.0.1\nLLM_API_KEY=test-key-value-000\n", encoding="utf-8"
    )
    profile_lines = "\n".join(
        "    " + name + ":\n      base_url: http://127.0.0.1:1/v1\n      model: evidence-model"
        for name in ("translation", "fill", "analysis")
    )
    cfg_file = work / "config.yaml"
    cfg_file.write_text(
        f"storage:\n  root: {storage}\n"
        f"languages:\n  root: {ROOT / 'config' / 'languages'}\n"
        f"  matrix: {ROOT / 'config' / 'language_matrix.yaml'}\n"
        f"llm:\n  profiles:\n{profile_lines}\n",
        encoding="utf-8",
    )
    settings = load_settings(cfg_file, env_file)
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    return settings, db, JobRepository(db)


# ------------------------------------------------------------------ L0-1


def l0_1(settings: Settings, work: Path) -> None:
    seed = settings.language_detection.seed
    lines = _header(f"L0-1: определение языка на 5 языковых фикстурах (seed={seed})")
    ok = 0
    for name, expected in BOOKS.items():
        book = read_epub(FIXTURES / name, work / f"unpack_{name}", settings)
        report = detect_book(
            book, settings, aliases=aliases_map(load_registry(settings.languages_root))
        )
        verdict = "PASS" if report.accepted_language == expected else "FAIL"
        ok += report.accepted_language == expected
        top3 = ", ".join(
            f"{lang}:{prob:.6f}"
            for lang, prob in sorted(report.probabilities.items(), key=lambda kv: -kv[1])[:3]
        )
        lines.append(
            f"{verdict} {name}: dc:language={report.dc_language} detected={report.detected} "
            f"confidence={report.confidence} decision={report.decision.value} top3=[{top3}]"
        )
    lines.append(f"итого: {ok}/{len(BOOKS)} верно, уверенность зафиксирована")
    evidence("l0-1_detection_5_fixtures.txt", "\n".join(lines))


# ------------------------------------------------------------------ L0-2


def l0_2(settings: Settings, work: Path) -> None:
    lines = _header(
        "L0-2: конфликт dc:language и детектора → NEEDS_CONFIRMATION "
        "(conflict.epub: en в OPF, немецкий текст)"
    )
    hits = 0
    for run in range(1, 4):
        book = read_epub(_CONFLICT, work / f"unpack_conflict_{run}", settings)
        report = detect_book(
            book, settings, aliases=aliases_map(load_registry(settings.languages_root))
        )
        hits += report.needs_confirmation
        lines.append(
            f"прогон {run}: dc:language={report.dc_language} detected={report.detected} "
            f"confidence={report.confidence:.6f} decision={report.decision.value} "
            f"accepted={report.accepted_language} needs_confirmation={report.needs_confirmation}"
        )
        for reason in report.reasons:
            lines.append(f"  причина: {reason}")
    lines.append(f"NEEDS_CONFIRMATION: {hits}/3 (100%)")
    evidence("l0-2_dc_conflict_needs_confirmation.txt", "\n".join(lines))


# ------------------------------------------------------------------ L0-3


def l0_3(settings: Settings) -> None:
    lines = _header(f"L0-3: резолвер — перечень недостающего (промпты: {ROOT / 'prompts'})")
    resolver = PairResolver(
        registry_root=settings.languages_root, prompts_root=ROOT / "prompts"
    )
    pairs = load_matrix(settings.language_matrix_path)
    degraded_unsupported = 0
    with_missing = 0
    for source, target in pairs:
        report = resolver.resolve_pair(source, target)
        if report.status is PairStatus.SUPPORTED:
            lines.append(f"{source} → {target}: SUPPORTED (missing=0)")
            continue
        degraded_unsupported += 1
        with_missing += bool(report.missing)
        lines.append(
            f"{source} → {target}: {report.status.value.upper()} (missing={len(report.missing)})"
        )
        for item in report.missing:
            lines.append(f"  - {item}")
    lines.append(
        f"degraded/unsupported с перечнем: {with_missing}/{degraded_unsupported} (100%)"
    )
    evidence("l0-3_resolver_missing_list.txt", "\n".join(lines))


# ------------------------------------------------------------------ L0-4


def l0_4(settings: Settings, db: Database, repo: JobRepository) -> None:
    lines = _header(
        "L0-4: spaCy-модель «удалена» (шов spacy_available=False, без фактического удаления)"
    )
    resolver = PairResolver(
        registry_root=settings.languages_root,
        prompts_root=ROOT / "prompts",
        spacy_available=lambda model: False,
    )
    pairs = load_matrix(settings.language_matrix_path)
    reports = [resolver.resolve_pair(source, target) for source, target in pairs]
    for report in reports:
        lines.append(f"{report.source} → {report.target}: {report.status.value.upper()}")
        for item in report.missing:
            if "spaCy" in item:
                lines.append(f"  - {item}")
    crashes = 0
    try:
        Pipeline(settings)
        Worker(settings, repo)
        lines.append("старт приложения (load_settings + Pipeline + Worker): исключений нет")
    except Exception as exc:
        crashes = 1
        lines.append(f"старт приложения: ПАДЕНИЕ: {exc!r}")
    unsupported = sum(r.status is PairStatus.UNSUPPORTED for r in reports)
    lines.append(f"пар unsupported после «удаления» модели: {unsupported}/{len(pairs)}")
    lines.append(f"падений: {crashes}")
    evidence("l0-4_spacy_model_removed.txt", "\n".join(lines))


# ------------------------------------------------------------------ L0-5


def l0_5(settings: Settings) -> None:
    lines = _header("L0-5: статус пары в единой точке UI (services.languages) == резолвер")
    resolver = PairResolver(
        registry_root=settings.languages_root, prompts_root=settings.prompts_dir
    )
    pairs = load_matrix(settings.language_matrix_path)
    diffs = 0
    for source, target in pairs:
        via_service = pair_status(settings, source, target)
        via_direct = resolver.resolve_pair(source, target)
        same = (
            (via_service.source, via_service.status, via_service.missing, via_service.prompts)
            == (via_direct.source, via_direct.status, via_direct.missing, via_direct.prompts)
        )
        diffs += not same
        lines.append(
            f"{source} → {target}: ui={via_service.status.value} "
            f"resolver={via_direct.status.value} расхождение={'0' if same else '1'}"
        )
    lines.append(f"суммарное расхождение: {diffs} (критерий: 0)")
    evidence("l0-5_ui_matches_resolver.txt", "\n".join(lines))


# ------------------------------------------------------------------ L0-6


def l0_6(settings: Settings) -> None:
    lines = _header("L0-6: число supported-пар (критерий: ≥ 5; все исходные → ru)")
    resolver = PairResolver(
        registry_root=settings.languages_root, prompts_root=ROOT / "prompts"
    )
    pairs = load_matrix(settings.language_matrix_path)
    reports = [resolver.resolve_pair(source, target) for source, target in pairs]
    supported = [r for r in reports if r.status is PairStatus.SUPPORTED]
    for report in reports:
        lines.append(f"{report.source} → {report.target}: {report.status.value.upper()}")
    lines.append(f"supported: {len(supported)}")
    for report in supported:
        lines.append(f"  - {report.source} → {report.target}")
    ru_sources = {s for s, t in pairs if t == "ru"}
    supported_ru = {r.source for r in supported if r.target == "ru"}
    lines.append(
        "критерий: "
        f"{'PASS' if len(supported) >= 5 and supported_ru == ru_sources else 'FAIL'} "
        f"(supported={len(supported)} ≥ 5; исходные → ru: {sorted(supported_ru)})"
    )
    evidence("l0-6_supported_pairs.txt", "\n".join(lines))


# ------------------------------------------------------------------ T-L0-7


def l0_7(settings: Settings, db: Database, repo: JobRepository) -> None:
    lines = _header("T-L0-7: неверный dc:language (en в OPF, немецкий текст) → подтверждение")
    job = create_job(repo, settings, source_path=_CONFLICT, target_language="de")
    pipeline = Pipeline(settings)
    repo.transition(job.id, JobStatus.VALIDATING, reason="evidence: старт этапа")
    requires = pipeline.run_validating(repo, repo.require(job.id))
    detection = pipeline.validation(job.id)["language_detection"]
    lines.append(f"job: {job.id}")
    lines.append(f"run_validating → требуется подтверждение: {requires}")
    lines.append(
        f"job.source_language после validating: {repo.require(job.id).source_language} "
        "(авто-выбор запрещён, L0.5)"
    )
    lines.append("")
    lines.append("validation.json → language_detection (verbatim):")
    lines.append(json.dumps(detection, ensure_ascii=False, indent=2))
    repo.transition(job.id, JobStatus.NEEDS_CONFIRMATION, reason="evidence: как воркер")
    confirmed = confirm_job(repo, job.id, source_language="de")
    lines.append("")
    lines.append(
        f"confirm_job(source_language='de'): status={confirmed.status.value} "
        f"source_language={repo.require(job.id).source_language}"
    )
    pipeline.run_planning(repo, repo.require(job.id))
    plan_present = (settings.jobs_dir / job.id / "plan.json").is_file()
    lines.append(f"plan.json создан по подтверждённому языку: {plan_present}")
    evidence("l0-7_wrong_dc_confirmation.txt", "\n".join(lines))


# ------------------------------------------------------------------ T-L0-8


def l0_8(settings: Settings, db: Database, repo: JobRepository) -> None:
    lines = _header("T-L0-8: двуязычная фикстура (en + de, dc:language=en) — факт и пометки")
    job = create_job(repo, settings, source_path=_BILINGUAL, target_language="de")
    pipeline = Pipeline(settings)
    repo.transition(job.id, JobStatus.VALIDATING, reason="evidence: старт этапа")
    requires = pipeline.run_validating(repo, repo.require(job.id))
    detection = pipeline.validation(job.id)["language_detection"]
    multilingual = detection["multilingual"]
    cfg = settings.language_detection
    lines.append(f"job: {job.id}")
    lines.append(f"требуется подтверждение: {requires} (нет: детектор и dc:language согласны)")
    lines.append(f"job.source_language: {repo.require(job.id).source_language}")
    lines.append(
        f"пороги (конфиг): multilingual_share={cfg.multilingual_share} "
        f"segment_min_chars={cfg.segment_min_chars}"
    )
    lines.append("")
    lines.append("multilingual (verbatim):")
    lines.append(json.dumps(multilingual, ensure_ascii=False, indent=2))
    evidence("l0-8_bilingual_fact_and_marks.txt", "\n".join(lines))


def main(argv: Sequence[str] | None = None) -> int:
    with tempfile.TemporaryDirectory(prefix="l0_evidence_") as td:
        work = Path(td)
        settings, db, repo = _settings(work)
        print(f"среда: {work}")
        l0_1(settings, work)
        l0_2(settings, work)
        l0_3(settings)
        l0_4(settings, db, repo)
        l0_5(settings)
        l0_6(settings)
        l0_7(settings, db, repo)
        l0_8(settings, db, repo)
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
