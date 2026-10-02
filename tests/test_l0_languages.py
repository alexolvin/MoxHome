# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""T-L0-1…T-L0-8: языковой слой (L0) — детерминированное определение и резолвер пар.

TZ_EpubTranslator_02_LANGUAGES.md, раздел L0:
- T-L0-1 — определение языка на 5 языковых фикстурах: 5/5 верно, уверенность
  зафиксирована (L0-1);
- T-L0-2 — конфликт `dc:language` и детектора → NEEDS_CONFIRMATION в 100% случаев
  (L0-2): правила `decide()` перебраны + фикстура с конфликтом на уровне пайплайна;
- T-L0-3 — резолвер перечисляет недостающие ресурсы в 100% случаев
  `degraded`/`unsupported` (L0-3): supported ⟺ `missing` пусто;
- T-L0-4 — spaCy-модель «удалена» (шов `spacy_available`) → пары `unsupported`,
  приложение стартует, 0 падений (L0-4);
- T-L0-5 — статус пары в единой точке UI (services.languages) == резолвер,
  расхождение 0 (L0-5);
- T-L0-6 — число пар `supported` ≥ 5 (все исходные → `ru`) (L0-6);
- T-L0-7 — книга с намеренно неверным `dc:language` (`en` в OPF, немецкий текст):
  требуется подтверждение, авто-выбор запрещён (L0.5);
- T-L0-8 — двуязычная фикстура: факт L0.6 зафиксирован, сегменты помечены.

Единичные: детерминированность детектора (D7, фикс. сид), ошибки матрицы
(`load_matrix`, Rule 6), создание задания с unsupported-парой (L0.7, fail closed).
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from app.core.config import Settings, load_settings
from app.core.errors import ConfigurationError, InputValidationError
from app.domain.job import JobStatus
from app.epub.reader import read_dc_language, read_epub
from app.languages.detect import (
    Detection,
    LanguageDecision,
    decide,
    detect_book,
    detect_text,
)
from app.languages.registry import aliases_map, load_registry
from app.languages.resolver import PairResolver, PairStatus, load_matrix
from app.services.jobs import confirm_job, create_job
from app.services.languages import matrix_report, pair_status
from app.storage.database import Database
from app.storage.jobs_repo import JobRepository
from app.workers.pipeline import Pipeline
from app.workers.worker import Worker

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "epub"
ROOT = Path(__file__).resolve().parents[1]
_COMPLEX = FIXTURES / "complex.epub"
_CONFLICT = FIXTURES / "conflict.epub"
_BILINGUAL = FIXTURES / "bilingual.epub"

#: T-L0-1: пять языковых фикстур → ожидаемый язык (dc:language совпадает с текстом).
_LANGUAGE_FIXTURES = {
    "complex.epub": "en",
    "lang_de.epub": "de",
    "lang_fr.epub": "fr",
    "lang_it.epub": "it",
    "lang_nl.epub": "nl",
}

_SAMPLE_EN = "The quick brown fox jumps over the lazy dog near the river. " * 15


def _settings_for(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
) -> tuple[Settings, Database, JobRepository]:
    """Настройки + БД + репозиторий на изолированном storage.root (паттерн E3/E4).

    Реестр и матрица — данные из репозитория (дефолты make_yaml, L0).
    """
    cfg = make_yaml(str(tmp_path / "storage"))
    env = make_env(None)
    settings = load_settings(cfg, env)
    db = Database(settings.jobs_db_path, settings.storage.busy_timeout_ms)
    return settings, db, JobRepository(db)


# ------------------------------------------------------------------ T-L0-1


@pytest.mark.parametrize(("name", "expected"), sorted(_LANGUAGE_FIXTURES.items()))
def test_l0_1_detection_on_five_language_fixtures(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
    name: str,
    expected: str,
) -> None:
    """T-L0-1 (L0-1): 5/5 фикстур — язык определён верно, уверенность зафиксирована."""
    settings, db, _ = _settings_for(tmp_path, make_env, make_yaml)
    try:
        book = read_epub(FIXTURES / name, tmp_path / "unpack", settings)
        aliases = aliases_map(load_registry(settings.languages_root))
        report = detect_book(book, settings, aliases=aliases)
        assert report.accepted_language == expected, (
            f"{name}: определён {report.accepted_language!r}, ожидается {expected!r}"
        )
        assert report.decision is LanguageDecision.ACCEPTED
        assert report.confidence is not None and report.confidence > 0.0, (
            "L0-1: уверенность должна быть зафиксирована"
        )
        assert report.probabilities, "L0-1: вероятности должны быть зафиксированы"
        assert report.needs_confirmation is False
    finally:
        db.close()


def test_l0_1_detection_is_deterministic() -> None:
    """D7: при фиксированном сите результат детектора воспроизводим."""
    first = detect_text(_SAMPLE_EN, 42)
    second = detect_text(_SAMPLE_EN, 42)
    assert first == second, "детектор с фикс. сидом обязан давать воспроизводимый результат"


# ------------------------------------------------------------------ T-L0-2


@pytest.mark.parametrize(
    ("detection", "dc", "aliases", "expected"),
    [
        # уверенный детектор совпал с dc:language → автопринято
        (
            Detection("en", 0.99, {"en": 0.99}),
            "en",
            None,
            (LanguageDecision.ACCEPTED, "en", 0),
        ),
        # уверенный детектор НЕ совпал с dc:language → подтверждение (авто-выбор запрещён)
        (
            Detection("de", 1.0, {"de": 1.0}),
            "en",
            None,
            (LanguageDecision.NEEDS_CONFIRMATION, None, 1),
        ),
        # детектор не уверен (dc:language есть) → подтверждение
        (
            Detection("en", 0.4, {"en": 0.4}),
            "en",
            None,
            (LanguageDecision.NEEDS_CONFIRMATION, None, 1),
        ),
        # dc:language отсутствует, детектор уверен → решение детектора
        (
            Detection("en", 0.9, {"en": 0.9}),
            None,
            None,
            (LanguageDecision.ACCEPTED_BY_DETECTOR, "en", 0),
        ),
        # dc:language отсутствует и детектор не уверен → подтверждение
        (
            Detection(None, None, {}),
            None,
            None,
            (LanguageDecision.NEEDS_CONFIRMATION, None, 1),
        ),
        # граничное: уверенность == порога считается уверенной (L0.5: ≥)
        (Detection("en", 0.6, {"en": 0.6}), "en", None, (LanguageDecision.ACCEPTED, "en", 0)),
        # алиасы реестра нормализуются (eng → en), конфликт после нормализации исчезает
        (
            Detection("eng", 0.9, {"eng": 0.9}),
            "en",
            {"eng": "en"},
            (LanguageDecision.ACCEPTED, "en", 0),
        ),
        # BCP-47 "und" (неопределённый, ставит конвертер) — как отсутствие dc:
        # уверенный детектор решает, ложного конфликта нет
        (
            Detection("en", 0.9, {"en": 0.9}),
            "und",
            None,
            (LanguageDecision.ACCEPTED_BY_DETECTOR, "en", 0),
        ),
    ],
    ids=[
        "dc_match_accepted",
        "conflict_needs_confirmation",
        "low_confidence_needs_confirmation",
        "no_dc_detector_decides",
        "no_dc_low_confidence_needs_confirmation",
        "boundary_confidence_accepted",
        "alias_normalization",
        "und_treated_as_absent",
    ],
)
def test_l0_2_decide_rules(
    detection: Detection,
    dc: str | None,
    aliases: dict[str, str] | None,
    expected: tuple[LanguageDecision, str | None, int],
) -> None:
    """T-L0-2 (L0-2): правила L0.5 перебраны — NEEDS_CONFIRMATION там, где положено."""
    decision, accepted, reasons_count = expected
    got_decision, got_accepted, reasons = decide(detection, dc, 0.6, aliases)
    assert got_decision is decision
    assert got_accepted == accepted
    assert isinstance(reasons, tuple) and all(isinstance(r, str) for r in reasons)
    assert len(reasons) == reasons_count, "причина для подтверждения — ровно одна (или ноль)"


# ------------------------------------------------------------------ T-L0-7


def test_l0_7_wrong_dc_language_requires_confirmation(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
) -> None:
    """T-L0-7 (L0.2/L0.5): `en` в OPF, немецкий текст → подтверждение, без авто-выбора.

    Полный поток как у воркера: VALIDATING → (True) → NEEDS_CONFIRMATION →
    confirm с выбором языка пользователя → PLANNING → план по подтверждённому языку.
    """
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml)
    try:
        job = create_job(repo, settings, source_path=_CONFLICT, target_language="de")
        pipeline = Pipeline(settings)
        repo.transition(job.id, JobStatus.VALIDATING, reason="тест: старт этапа")
        assert pipeline.run_validating(repo, repo.require(job.id)) is True
        # авто-выбора нет (L0.5): язык в задании не появляется
        assert repo.require(job.id).source_language is None

        det = pipeline.validation(job.id)["language_detection"]
        assert det["dc_language"] == "en"
        assert det["detected"] == "de"
        assert det["decision"] == LanguageDecision.NEEDS_CONFIRMATION.value
        assert det["needs_confirmation"] is True
        assert det["accepted_language"] is None
        assert det["reasons"], "конфликт обязан быть объяснён в отчёте"

        repo.transition(job.id, JobStatus.NEEDS_CONFIRMATION, reason="тест: как воркер")
        confirmed = confirm_job(repo, job.id, source_language="de")
        assert confirmed.status is JobStatus.PLANNING
        assert repo.require(job.id).source_language == "de"
        pipeline.run_planning(repo, repo.require(job.id))
        assert (settings.jobs_dir / job.id / "plan.json").is_file()
    finally:
        db.close()


def _make_und_epub(path: Path) -> Path:
    """Минимальный EPUB с ``dc:language=und`` (как ставит конвертер, IMPORT_FLAGS)."""
    container = (
        '<?xml version="1.0"?>'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="uid"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="uid">und</dc:identifier><dc:title>Und</dc:title>'
        "<dc:language>und</dc:language></metadata><manifest>"
        '<item id="ch1" href="chapter1.xhtml" '
        'media-type="application/xhtml+xml"/></manifest>'
        "<spine><itemref idref=\"ch1\"/></spine></package>"
    )
    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>ch</title></head>'
        "<body><p>The quick brown fox jumps over the lazy dog by the river.</p></body></html>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/chapter1.xhtml", xhtml)
    return path


def test_l0_7_und_dc_skips_pair_precheck(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
) -> None:
    """L0.7/L0.5: ``dc:language=und`` (неопределённый, ставит конвертер) — не язык.

    Создание задания НЕ отклоняет пару (und → ru) как unsupported: исходный язык
    определит детектор на validating. Ретрегрессия: до фикса — InputValidationError
    «языковая пара und → ru не поддерживается (L0.2)»."""
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml)
    try:
        epub = _make_und_epub(tmp_path / "und.epub")
        assert read_dc_language(epub) == "und", "фикстура: dc:language должен быть und"
        job = create_job(repo, settings, source_path=epub, target_language="ru")
        assert job.status is JobStatus.CREATED
    finally:
        db.close()


# ------------------------------------------------------------------ T-L0-8


def test_l0_8_bilingual_fact_recorded_and_segments_marked(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
) -> None:
    """T-L0-8 (L0.6): двуязычная книга — факт зафиксирован, сегменты с другим
    языком помечены; подтверждение не требуется (dc и детектор согласны)."""
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml)
    try:
        job = create_job(repo, settings, source_path=_BILINGUAL, target_language="de")
        pipeline = Pipeline(settings)
        repo.transition(job.id, JobStatus.VALIDATING, reason="тест: старт этапа")
        assert pipeline.run_validating(repo, repo.require(job.id)) is False
        assert repo.require(job.id).source_language == "en"

        ml = pipeline.validation(job.id)["language_detection"]["multilingual"]
        assert ml is not None, "факт многоязычности обязан быть в отчёте"
        assert ml["main_language"] == "en"
        assert ml["segment_count"] == 7  # en-глава: h1+3p; de-глава: h1+2p
        assert ml["share"] > 0.2, "доля de-сегментов превышает порог (L0.6)"
        assert ml["exceeds_threshold"] is True
        assert len(ml["marked"]) == 2, "помечены только de-абзацы (h1 короче порога)"
        assert all(m["detected"] == "de" for m in ml["marked"])

        book = read_epub(_BILINGUAL, settings.jobs_dir / job.id / "unpack", settings)
        segment_ids = {s.segment_id for chapter in book.chapters for s in chapter.segments}
        assert {m["segment_id"] for m in ml["marked"]} <= segment_ids, (
            "пометки должны указывать на существующие сегменты книги"
        )
    finally:
        db.close()


# ------------------------------------------------------------------ T-L0-3/5/6


def test_l0_3_resolver_lists_missing_for_all_degraded_and_unsupported(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
) -> None:
    """T-L0-3 (L0.3): supported ⟺ missing пусто; каждый degraded/unsupported —
    с точным перечнем недостающего (100% пар матрицы)."""
    settings, db, _ = _settings_for(tmp_path, make_env, make_yaml)
    try:
        pairs = load_matrix(settings.language_matrix_path)
        assert pairs, "матрица не должна быть пустой"
        for source, target in pairs:
            report = pair_status(settings, source, target)
            if report.status is PairStatus.SUPPORTED:
                assert report.missing == (), f"supported с недостающими: {report.missing}"
            else:
                assert report.missing, (
                    f"{source} → {target}: статус {report.status} без перечня недостающего (L0.3)"
                )
    finally:
        db.close()


def test_l0_5_ui_pair_status_equals_resolver(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
) -> None:
    """T-L0-5 (L0-5): единая точка UI (services.languages) == резолвер, расхождение 0."""
    settings, db, _ = _settings_for(tmp_path, make_env, make_yaml)
    try:
        pairs = load_matrix(settings.language_matrix_path)
        direct = PairResolver(
            registry_root=settings.languages_root, prompts_root=settings.prompts_dir
        )
        for source, target in pairs:
            via_service = pair_status(settings, source, target)
            via_direct = direct.resolve_pair(source, target)
            assert (
                via_service.source,
                via_service.target,
                via_service.status,
                via_service.missing,
                via_service.prompts,
            ) == (
                via_direct.source,
                via_direct.target,
                via_direct.status,
                via_direct.missing,
                via_direct.prompts,
            ), f"расхождение UI-статуса и резолвера для {source} → {target}"
        via_service_matrix = matrix_report(settings)
        via_direct_matrix = tuple(direct.resolve_pair(s, t) for s, t in pairs)
        assert via_service_matrix == via_direct_matrix
    finally:
        db.close()


def test_l0_6_supported_pairs_at_least_five(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
) -> None:
    """T-L0-6 (L0-6): supported ≥ 5; все исходные → `ru` supported.

    Промпты — реальные (из репозитория): тестовый tmp-storage их не содержит,
    а статус пары обязан отражать фактические ресурсы (L0.2).
    """
    settings, db, _ = _settings_for(tmp_path, make_env, make_yaml)
    try:
        resolver = PairResolver(
            registry_root=settings.languages_root, prompts_root=ROOT / "prompts"
        )
        pairs = load_matrix(settings.language_matrix_path)
        reports = [resolver.resolve_pair(source, target) for source, target in pairs]
        supported = [r for r in reports if r.status is PairStatus.SUPPORTED]
        assert len(supported) >= 5, f"supported: {len(supported)} < 5: {reports}"
        ru_sources = {source for source, target in pairs if target == "ru"}
        supported_ru = {r.source for r in supported if r.target == "ru"}
        assert supported_ru == ru_sources, "все исходные → ru обязаны быть supported"
    finally:
        db.close()


# ------------------------------------------------------------------ T-L0-4


def test_l0_4_spacy_model_removed_pair_unsupported_app_starts(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
) -> None:
    """T-L0-4 (L0-4): spaCy-модель «удалена» (шов, без фактического удаления) →
    пары unsupported, приложение стартует, 0 падений."""
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml)
    try:
        resolver = PairResolver(
            registry_root=settings.languages_root,
            prompts_root=ROOT / "prompts",
            spacy_available=lambda model: False,
        )
        pairs = load_matrix(settings.language_matrix_path)
        reports = [resolver.resolve_pair(source, target) for source, target in pairs]
        assert reports, "матрица не должна быть пустой"
        for report in reports:
            assert report.status is PairStatus.UNSUPPORTED, (
                f"{report.source} → {report.target}: без spaCy-модели пары обязаны быть "
                f"unsupported, получено {report.status}"
            )
            assert any("spaCy-модель не установлена" in m for m in report.missing), (
                f"причина (нет spaCy-модели) не в перечне: {report.missing}"
            )
        # «приложение стартует, 0 падений»: пайплайн и воркер строятся без исключений
        Pipeline(settings)
        Worker(settings, repo)
    finally:
        db.close()


# ------------------------------------------------------------------ единичные


def test_l0_create_job_unsupported_pair_rejected(
    tmp_path: Path,
    make_env: Callable[[Mapping[str, str | None] | None], Path],
    make_yaml: Callable[..., Path],
) -> None:
    """L0.7: unsupported-пара (dc:language → target) — задание НЕ создаётся (fail closed),
    с точным перечнем недостающего (L0.3)."""
    settings, db, repo = _settings_for(tmp_path, make_env, make_yaml)
    try:
        with pytest.raises(InputValidationError) as excinfo:
            create_job(repo, settings, source_path=_COMPLEX, target_language="ja")
        # задание с unsupported-парой не должно попасть в БД (fail closed, L0.7)
        count = db.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
        assert count is not None and int(count[0]) == 0
    finally:
        db.close()
    message = str(excinfo.value)
    assert "не поддерживается" in message
    assert "ja/pack.yaml" in message, f"перечень недостающего отсутствует: {message}"


def test_l0_load_matrix_malformed(tmp_path: Path) -> None:
    """Rule 6: неверная матрица — ConfigurationError, а не молчаливое продолжение."""

    def write(text: str) -> Path:
        path = tmp_path / "matrix.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    with pytest.raises(ConfigurationError):
        load_matrix(tmp_path / "absent.yaml")
    with pytest.raises(ConfigurationError):
        load_matrix(write("pairs: []\n"))
    with pytest.raises(ConfigurationError):
        load_matrix(
            write("pairs:\n  - {source: en, target: ru}\n  - {source: en, target: ru}\n")
        )
    with pytest.raises(ConfigurationError):
        load_matrix(write("pairs:\n  - [en, ru]\n"))
    with pytest.raises(ConfigurationError):
        load_matrix(write('pairs:\n  - {source: "", target: ru}\n'))
    with pytest.raises(ConfigurationError):
        load_matrix(write("pairs: {source: en, target: ru}\n"))

    pairs = load_matrix(
        write("pairs:\n  - {source: EN, target: RU}\n  - {source: de, target: ru}\n")
    )
    assert pairs == (("en", "ru"), ("de", "ru"))
