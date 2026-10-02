# Copyright (c) 2026 Alex Olvin. All rights reserved.
# Proprietary. Unauthorized use, copying, modification, or distribution
# is prohibited without prior written permission. See LICENSE.
"""Детерминированное определение исходного языка (L0.4, D7): langdetect с фикс. сидом.

НЕ через LLM (L0.4). langdetect 1.x: `DetectorFactory` с фиксированным сидом —
результат воспроизводим для одного и того же текста (D7).

Правила решения (L0.5):
- детектор уверен (≥ min_confidence) и совпадает с dc:language → язык автопринят;
- детектор уверен, но НЕ совпадает с dc:language → NEEDS_CONFIRMATION
  (показываются оба значения, авто-выбор запрещён);
- детектор неуверен → NEEDS_CONFIRMATION (dc:language сам по себе — недостаточное
  основание: в реальных EPUB он часто ошибочен, L0.5);
- dc:language отсутствует → детектор решает, если уверенность выше порога.

Многоязычная книга (L0.6): доля сегментов с языком, отличным от принятого;
выше порога (config) — факт в отчёте и сегменты помечены.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from langdetect import LangDetectException
from langdetect.detector_factory import PROFILES_DIRECTORY, DetectorFactory

from app.core.config import LanguageDetectionSection, Settings
from app.domain.book import Book


class _FactoryHolder:
    """Единый фабрик-детекторов процесса: профили загружаются один раз (аналог L1.2).

    Сид выставляется на каждом вызове. Воркер выполняет одно задание за раз
    (E3.5) — гонки по общему сиду нет.
    """

    _factory: DetectorFactory | None = None

    @classmethod
    def get(cls, seed: int) -> DetectorFactory:
        factory = cls._factory
        if factory is None:
            factory = DetectorFactory()
            factory.load_profile(PROFILES_DIRECTORY)
            cls._factory = factory
        factory.set_seed(seed)
        return factory


@dataclass(frozen=True)
class Detection:
    """Результат детектора: топ-1 язык, его уверенность, все вероятности."""

    language: str | None
    confidence: float | None
    probabilities: dict[str, float]


def detect_text(text: str, seed: int) -> Detection:
    """Детерминированное определение языка текста (L0.4).

    Пустой/неопределяемый текст — None (Rule 6: честный результат, не галлюцинация).
    """
    if not text.strip():
        return Detection(None, None, {})
    detector = _FactoryHolder.get(seed).create()
    detector.append(text)
    try:
        raw = detector.get_probabilities()  # list[Language]: .lang, .prob (по убыванию)
    except LangDetectException:
        return Detection(None, None, {})
    total = sum(item.prob for item in raw)
    if total <= 0:
        return Detection(None, None, {})
    probabilities = {item.lang: round(item.prob / total, 6) for item in raw}
    language, confidence = max(probabilities.items(), key=lambda kv: kv[1])
    return Detection(language, confidence, probabilities)


class LanguageDecision(StrEnum):
    """Исход сопоставления детектора и dc:language (L0.5)."""

    #: Уверенный детектор совпал с dc:language — язык автопринят.
    ACCEPTED = "accepted"
    #: dc:language отсутствует, детектор уверен — язык принят решением детектора.
    ACCEPTED_BY_DETECTOR = "accepted_by_detector"
    #: Конфликт/неуверенность — требуется подтверждение (оба значения в отчёте).
    NEEDS_CONFIRMATION = "needs_confirmation"


@dataclass(frozen=True)
class SegmentMark:
    """Пометка сегмента с языком, отличным от основного (L0.6)."""

    segment_id: str
    detected: str
    confidence: float


@dataclass(frozen=True)
class MultilingualFact:
    """Факт о многоязычности книги (L0.6): доля и (при превышении порога) пометки."""

    main_language: str
    marked: tuple[SegmentMark, ...]
    segment_count: int
    share: float
    exceeds_threshold: bool


@dataclass(frozen=True)
class LanguageReport:
    """Решение по языку книги (стадия validating, L0)."""

    dc_language: str | None
    detected: str | None
    confidence: float | None
    probabilities: dict[str, float] = field(default_factory=dict)
    decision: LanguageDecision = LanguageDecision.NEEDS_CONFIRMATION
    accepted_language: str | None = None
    needs_confirmation: bool = True
    reasons: tuple[str, ...] = ()
    multilingual: MultilingualFact | None = None


def sample_text(book: Book, sample_segments: int) -> str:
    """Выборка текста для детектора: первые N сегментов в порядке документов.

    Объём и метод выборки — из конфига (L0.4: `language_detection.sample_segments`).
    """
    segments = [s for chapter in book.chapters for s in chapter.segments]
    sample = segments[:sample_segments]
    return "\n".join(s.plain_text for s in sample if s.plain_text.strip())


def normalize_code(code: str | None, aliases: dict[str, str] | None = None) -> str | None:
    """Канонический код: нижний регистр, основной под-тег, алиасы реестра (eng→en)."""
    if code is None:
        return None
    primary = code.strip().lower().split("-")[0]
    if aliases is not None:
        return aliases.get(primary, primary)
    return primary


def decide(
    detection: Detection,
    dc_language: str | None,
    min_confidence: float,
    aliases: dict[str, str] | None = None,
) -> tuple[LanguageDecision, str | None, tuple[str, ...]]:
    """Правила L0.5. Возврат: (решение, принятый язык, причины для отчёта/подтверждения)."""
    det_code = normalize_code(detection.language, aliases)
    dc_code = normalize_code(dc_language, aliases)
    # BCP-47 "und" (неопределённый) — отсутствие языка, а не отдельный язык:
    # конвертер ставит --language und (adapters.convert.IMPORT_FLAGS), и
    # «конфликт» уверенного детектора с und — ложный (L0.5) → считаем dc нет.
    if dc_code == "und":
        dc_code = None
    confident = (
        det_code is not None
        and detection.confidence is not None
        and detection.confidence >= min_confidence
    )
    det_desc = (
        f"{detection.confidence:.4f}" if detection.confidence is not None else "нет результата"
    )
    if dc_code is None:
        if confident:
            return LanguageDecision.ACCEPTED_BY_DETECTOR, det_code, ()
        return (
            LanguageDecision.NEEDS_CONFIRMATION,
            None,
            (
                f"в OPF нет dc:language, и детектор не уверен ({det_desc} < {min_confidence})",
            ),
        )
    if not confident:
        return (
            LanguageDecision.NEEDS_CONFIRMATION,
            None,
            (
                f"детектор не уверен ({det_desc} < {min_confidence}); "
                f"dc:language={dc_language} сам по себе — недостаточное основание (L0.5)",
            ),
        )
    if det_code == dc_code:
        return LanguageDecision.ACCEPTED, det_code, ()
    return (
        LanguageDecision.NEEDS_CONFIRMATION,
        None,
        (
            f"конфликт языков: детектор={det_code} (p={detection.confidence:.4f}) "
            f"против dc:language={dc_language} — авто-выбор запрещён (L0.5), показаны оба значения",
        ),
    )


def detect_book(
    book: Book,
    settings: Settings,
    *,
    aliases: dict[str, str] | None = None,
) -> LanguageReport:
    """Определение исходного языка книги (L0.4/L0.5/L0.6)."""
    cfg = settings.language_detection
    detection = detect_text(sample_text(book, cfg.sample_segments), cfg.seed)
    decision, accepted, reasons = decide(detection, book.language, cfg.min_confidence, aliases)
    multilingual: MultilingualFact | None = None
    if accepted is not None:
        multilingual = _multilingual_fact(book, accepted, cfg, aliases)
    return LanguageReport(
        dc_language=book.language,
        detected=normalize_code(detection.language, aliases),
        confidence=detection.confidence,
        probabilities=detection.probabilities,
        decision=decision,
        accepted_language=accepted,
        needs_confirmation=decision is LanguageDecision.NEEDS_CONFIRMATION,
        reasons=reasons,
        multilingual=multilingual,
    )


def _multilingual_fact(
    book: Book,
    main_language: str,
    cfg: LanguageDetectionSection,
    aliases: dict[str, str] | None,
) -> MultilingualFact:
    """Факт L0.6: доля сегментов с языком, отличным от основного.

    Пометки сегментов заполняются только при превышении порога (L0.6); сам факт
    (доля, превышен ли порог) всегда в отчёте. Очень короткие сегменты (<
    segment_min_chars) не помечаются — на них детектор ненадёжен.
    """
    segments = [s for chapter in book.chapters for s in chapter.segments]
    if not segments:
        return MultilingualFact(main_language, (), 0, 0.0, False)
    marks: list[SegmentMark] = []
    for segment in segments:
        if len(segment.plain_text.strip()) < cfg.segment_min_chars:
            continue
        d = detect_text(segment.plain_text, cfg.seed)
        code = normalize_code(d.language, aliases)
        if code is not None and code != main_language:
            marks.append(SegmentMark(segment.segment_id, code, d.confidence or 0.0))
    share = round(len(marks) / len(segments), 6)
    exceeds = share > cfg.multilingual_share
    return MultilingualFact(
        main_language=main_language,
        marked=tuple(marks) if exceeds else (),
        segment_count=len(segments),
        share=share,
        exceeds_threshold=exceeds,
    )
