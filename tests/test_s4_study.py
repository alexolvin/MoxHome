"""Тесты S4: summary главы, вопросы на понимание, упражнения (ТЗ_03 §S4,
CONTROL S4-1…S4-7).

Сквозной in-process прогон (Pipeline) до LEARNING — по образцу S3 (S1Env
переиспользуется); mock (D3) — детерминированный: вопросы/упражнения
строятся ТОЛЬКО по фактическим предложениям главы payload, ответы — в
answers.json (S4.4), в теле главы ответов НЕТ (S4-5). Lексические
упражнения — только из выпущенных S1-карточек, грамматические — только из
выпущенных S3-заметок (S4-6).

- T-S4-1 — глава без текстовых сегментов: summary НЕ создаётся, пропуск
  зафиксирован с причиной (S4-1).
- T-S4-2 — gap-слово не в исходном предложении: вопрос отклонён (S4-2).
- T-S4-3 — mc с двумя правильными / с дублем варианта: отклонён (S4-3).
- T-S4-4 — вопрос с сущностью, отсутствующей в главе: отклонён (S4-4).
- T-S4-5 — ответы отсутствуют в записях тела (questions/exercises.json),
  полностью присутствуют в answers.json; gap-ответ ∈ исходном предложении
  (S4-5).
- T-S4-6 — 100% vocab-упражнений из выпущенных карточек, grammar — из
  выпущенных заметок; «призрачный» источник отклонён (S4-6).
- T-S4-7 — 5 исходных языков + гейт уровней (Minimal/Balanced — нет S4;
  Learning — только вопросы; Intensive — summary + вопросы + упражнения).
- T-S4-8 — глава-изображение: summary НЕ создаётся, пропуск зафиксирован.
- T-S4-9 — 100 mc-вопросов: правильный вариант НЕ систематически самый
  длинный, позиции правильного распределены (защита от угадывания).
"""

from __future__ import annotations

import base64
import json
import sys
import zipfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.domain.job import JobStatus
from app.learning.study import (
    BLANK,
    blank_first_occurrence,
    split_words,
    validate_exercise,
    validate_question,
    validate_summary,
)

_TESTS_DIR = str(Path(__file__).resolve().parent)
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from mock_llm_server import MockLLMServer  # noqa: E402
from test_l2_glossary import _make_storage, _synth_book, _write_epub  # noqa: E402
from test_s1_vocabulary import S1Env, S1Run  # noqa: E402

#: 1×1 PNG (image-глава, T-S4-8).
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwACh"
    "wGA60e6kgAAAABJRU5ErkJggg=="
)

# ------------------------------------------------------------------ книги


#: 20 непересекающихся пулов по 5 существительных (en): каждое слово — ≥4
#: буквы, встречается в каждом предложении главы → детерминированный
#: mock-материал и 100 mc-вопросов (T-S4-9).
_S4_EN_POOLS: tuple[tuple[str, ...], ...] = (
    ("harbor", "anchor", "lighthouse", "buoy", "tidepool"),
    ("meadow", "willow", "heron", "fencepost", "dewdrop"),
    ("millrace", "grindstone", "barnyard", "threshing", "oatfield"),
    ("cliffside", "seagull", "crag", "headland", "rockweed"),
    ("forest", "birch", "squirrel", "hollow", "undergrowth"),
    ("valley", "orchard", "honeybee", "blossom", "creekbed"),
    ("desert", "cactus", "coyote", "mirage", "sandstone"),
    ("jungle", "parrot", "vineyard", "rainforest", "toucan"),
    ("port", "dockyard", "cargo", "stevedore", "billhook"),
    ("shore", "dune", "seashell", "sandpiper", "tideline"),
    ("cape", "lagoon", "mangrove", "pelican", "brine"),
    ("delta", "osprey", "marsh", "cattail", "reedbed"),
    ("isle", "ferryboat", "kelp", "seaweed", "cove"),
    ("glacier", "moraine", "icefall", "crevasse", "avalanche"),
    ("tundra", "caribou", "lichen", "permafrost", "muskox"),
    ("canyon", "juniper", "rattlesnake", "badland", "arroyo"),
    ("gulf", "barracuda", "coral", "sandbar", "estuary"),
    ("moor", "peatbog", "heather", "swan", "loch"),
    ("fen", "sedge", "dragonfly", "reed", "wetland"),
    ("bog", "moss", "firefly", "pondweed", "alder"),
)


def _s4_en_paras(pool: tuple[str, ...]) -> tuple[str, ...]:
    """8 предложений главы: все 5 слов пула в каждом (дет. mock-материал)."""
    a, b, c, d, e = pool
    return (
        f"The {a} near the {b} kept the {c} steady while the {d} rose over the {e}.",
        f"Every {a} passed the {b} and the {c} guided the {d} toward the {e}.",
        f"Workers at the {a} mended the {d} before the {c} closed the {b}.",
        f"The {e} drifted past the {a}, past the {b}, and the {c} rang out.",
        f"Storms bent the {a}, the {b} widened, and the {d} held against the {e}.",
        f"Old maps marked the {c} beside the {a}, a quiet {b} above the {e}.",
        f"Traders counted the {d} at the {a} while the {c} watched the {b}.",
        f"The {a} echoed at dusk, the {b} glistened, and the {e} lay far away.",
    )


def _chapter_xhtml(lang: str, body: str, title: str) -> str:
    return (
        f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" lang="{lang}" xml:lang="{lang}">\n'
        f"<head>\n  <title>{title}</title>\n</head>\n<body>\n  {body}\n</body>\n"
        "</html>\n"
    )


def _synth_book_multi(
    out_path: Path,
    title: str,
    lang: str,
    uid: str,
    bodies: tuple[str, ...],
    *,
    extra: dict[str, str | bytes] | None = None,
    extra_manifest: str = "",
) -> None:
    """Мультиглавный EPUB: bodies — готовые HTML-тела глав (p/li/...)."""
    files: dict[str, str | bytes] = dict(extra or {})
    manifest_items: list[str] = []
    spine: list[str] = []
    nav_entries: list[str] = []
    for index, body in enumerate(bodies, start=1):
        doc = f"chapter{index}.xhtml"
        files[f"OEBPS/{doc}"] = _chapter_xhtml(lang, body, f"{title} — {index}")
        manifest_items.append(
            f'    <item id="ch{index}" href="{doc}" '
            'media-type="application/xhtml+xml"/>\n'
        )
        spine.append(f'    <itemref idref="ch{index}"/>\n')
        nav_entries.append(f'      <li><a href="{doc}">{title} — {index}</a></li>\n')
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="s4-id">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'\n    <dc:identifier id="s4-id">{uid}</dc:identifier>'
        f'\n    <dc:title>{title}</dc:title>\n    <dc:creator>S4 Probe</dc:creator>'
        f'\n    <dc:language>{lang}</dc:language>\n    <dc:date>2026-01-01</dc:date>'
        '\n    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>\n'
        "  </metadata>\n  <manifest>\n"
        + "".join(manifest_items)
        + extra_manifest
        + '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
        'properties="nav"/>\n  </manifest>\n  <spine>\n'
        + "".join(spine)
        + "  </spine>\n</package>\n"
    )
    nav = (
        f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" '
        f'xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">\n'
        "<head>\n  <title>Contents</title>\n</head>\n<body>\n  "
        '<nav epub:type="toc" id="toc">\n    <h1>Contents</h1>\n    <ol>\n'
        + "".join(nav_entries)
        + "    </ol>\n  </nav>\n</body>\n</html>\n"
    )
    files["META-INF/container.xml"] = (
        '<?xml version="1.0"?>\n<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n  <rootfiles>\n'
        '    <rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/>\n  </rootfiles></container>\n'
    )
    files["OEBPS/content.opf"] = opf
    files["OEBPS/nav.xhtml"] = nav
    _write_epub(out_path, files)


def _synth_s4_en_book(out_path: Path) -> None:
    """20 глав × 8 предложений (T-S4-9: 100 mc-вопросов)."""
    bodies = tuple(
        "\n".join(
            f'    <p id="p{i}">{p}</p>'
            for i, p in enumerate(_s4_en_paras(pool), start=1)
        )
        for pool in _S4_EN_POOLS
    )
    _synth_book_multi(out_path, "S4 Probe EN", "en", "s4-en-uid-0001", bodies)


def _synth_s4_en1_book(out_path: Path) -> None:
    """Одна en-глава (сценарные тесты + гейт уровней)."""
    body = "\n".join(
        f'    <p id="p{i}">{p}</p>' for i, p in enumerate(_s4_en_paras(_S4_EN_POOLS[0]), start=1)
    )
    _synth_book_multi(out_path, "S4 Probe EN1", "en", "s4-en1-uid-0002", (body,))


def _synth_s4_sparse_book(out_path: Path) -> None:
    """3 главы: без блоков (T-S4-1), только изображение (T-S4-8), текст."""
    text_body = "\n".join(
        f'    <p id="p{i}">{p}</p>' for i, p in enumerate(_s4_en_paras(_S4_EN_POOLS[1]), start=1)
    )
    image_body = '    <img src="image1.png" alt="S4 image chapter"/>'
    _synth_book_multi(
        out_path,
        "S4 Sparse",
        "en",
        "s4-sparse-uid-0003",
        ("", image_body, text_body),
        extra={"OEBPS/image1.png": _PNG_1PX},
        extra_manifest=(
            '    <item id="img1" href="image1.png" media-type="image/png"/>\n'
        ),
    )


#: T-S4-7: 12 предложений на язык (слова ≥4 буквы в каждом предложении).
_S4_DE_PARAS: tuple[str, ...] = (
    "Ich warte auf den Zug am Bahnhof.",
    "Die Brücke über den Fluss stand fest.",
    "Wir gehen durch den alten Park.",
    "Das Fenster im Zimmer war offen.",
    "Er liest das Buch jeden Abend.",
    "Sie malt ein großes Bild.",
    "Der Zug fährt früh nach Wien.",
    "Die Mühle am Fluss war alt.",
    "Hans kauft Brot am Markt.",
    "Die Kinder spielen im Garten.",
    "Ein neuer Tag beginnt im Dorf.",
    "Die Straße führte durch den Wald.",
)
_S4_FR_PARAS: tuple[str, ...] = (
    "Le pont sur la rivière était solide.",
    "Nous marchons dans le vieux parc.",
    "Elle lit un long roman.",
    "Le bateau part pour le port.",
    "Il peint une grande image.",
    "Les enfants jouent dans le jardin.",
    "Le train arrive tôt demain.",
    "La rivière coule vers la mer.",
    "Nous visitons la vieille ville.",
    "Le marché est très animé.",
    "Une nouvelle route ouvre devant nous.",
    "Le jardin fleuri attire les abeilles.",
)
_S4_IT_PARAS: tuple[str, ...] = (
    "Il ponte sul fiume era solido.",
    "Noi camminiamo nel vecchio parco.",
    "Lei legge un lungo romanzo.",
    "La nave parte per il porto.",
    "Egli dipinge un grande quadro.",
    "I bambini giocano nel giardino.",
    "Il treno arriva presto domani.",
    "Il fiume scorre verso il mare.",
    "Noi visitiamo la vecchia città.",
    "Il mercato è molto animato.",
    "Una nuova strada si apre davanti a noi.",
    "Il giardino fiorito attira le api.",
)
_S4_NL_PARAS: tuple[str, ...] = (
    "De brug over de rivier was sterk.",
    "Wij lopen door de oude tuin.",
    "Zij leest een lang verhaal.",
    "De boot vaart naar de haven.",
    "Hij schildert een groot schilderij.",
    "De kinderen spelen in de tuin.",
    "De trein arriveert vroeg morgen.",
    "De rivier stroomt naar zee.",
    "Wij bezichtigen de oude stad.",
    "De markt is druk en levendig.",
    "Een nieuwe weg opent zich voor ons.",
    "De bloeiende tuin trekt bijen.",
)


# ------------------------------------------------------------------ окружение


@pytest.fixture(scope="module")
def s4_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[S1Env]:
    """Модульное окружение S4: S1Env (сквозной прогон до LEARNING) +
    S4-книги (20-главная en, 1-главная en, разреженная, 5 языков)."""
    base = tmp_path_factory.mktemp("s4_module")
    books_dir = base / "books"
    books_dir.mkdir()
    books: dict[str, Path] = {}
    _synth_s4_en_book(books_dir / "s4_en.epub")
    books["s4_en.epub"] = books_dir / "s4_en.epub"
    _synth_s4_en1_book(books_dir / "s4_en1.epub")
    books["s4_en1.epub"] = books_dir / "s4_en1.epub"
    _synth_s4_sparse_book(books_dir / "s4_sparse.epub")
    books["s4_sparse.epub"] = books_dir / "s4_sparse.epub"
    for name, title, lang, uid, paras in (
        ("s4_de.epub", "S4 Probe DE", "de", "s4-de-uid-0004", _S4_DE_PARAS),
        ("s4_fr.epub", "S4 Probe FR", "fr", "s4-fr-uid-0005", _S4_FR_PARAS),
        ("s4_it.epub", "S4 Probe IT", "it", "s4-it-uid-0006", _S4_IT_PARAS),
        ("s4_nl.epub", "S4 Probe NL", "nl", "s4-nl-uid-0007", _S4_NL_PARAS),
    ):
        path = books_dir / name
        _synth_book(path, title, lang, uid, paras)
        books[name] = path
    server = MockLLMServer()
    server.start()
    env = S1Env(server=server, base=base, books=books)
    main_settings, main_db = _make_storage(base, "storage_main", server)
    env.add_profile("main", main_settings, main_db)
    try:
        yield env
    finally:
        env.close()
        server.stop()


# ------------------------------------------------------------------ служебное


def _read_json(run: S1Run, name: str) -> dict[str, Any]:
    path = run.job_dir / name
    assert path.is_file(), f"нет {name}: {run.error}"
    return json.loads(path.read_text(encoding="utf-8"))


def _summaries(run: S1Run) -> dict[str, Any]:
    return _read_json(run, "summaries.json")


def _questions(run: S1Run) -> dict[str, Any]:
    return _read_json(run, "questions.json")


def _exercises(run: S1Run) -> dict[str, Any]:
    return _read_json(run, "exercises.json")


def _answers(run: S1Run) -> dict[str, Any]:
    return _read_json(run, "answers.json")


def _mc_answers(report: dict[str, Any], answers: dict[str, Any]) -> list[dict[str, Any]]:
    """mc-вопросы с ответом (индекс правильного варианта)."""
    result: list[dict[str, Any]] = []
    for chapter_id, chapter in answers.get("chapters", {}).items():
        for qid, answer in chapter.get("questions", {}).items():
            record = next(q for q in report["questions"] if q["id"] == qid)
            if record["type"] == "mc" and record["chapter_id"] == chapter_id:
                result.append({"record": record, "answer": int(answer)})
    return result


# ------------------------------------------------------------------ T-S4-1


def test_s4_1_summary_empty_fields_unit() -> None:
    # S4-1 (unit): пустые обязательные поля summary — отклонение (пустой
    # summary не выпускается; генерация по названию исключена payload-ом).
    reasons = validate_summary({"brief": "", "extended": "Текст есть."})
    assert any("brief" in reason for reason in reasons)
    ok = {
        "brief": "Кратко.",
        "extended": "Развёрнуто.",
        "key_events": ["Событие."],
        "entities": ["Слово"],
        "themes": ["Тема."],
        "concepts": [],
    }
    assert validate_summary(ok) == []


def test_s4_1_empty_chapter_no_summary(s4_env: S1Env) -> None:
    # S4-1: глава без выровненных текстовых сегментов — summary НЕ создаётся;
    # пропуск зафиксирован с причиной (отчёт + warning quality).
    run = s4_env.run("t1", "s4_sparse.epub", lang="en", learning_level="Intensive")
    assert run.error is None
    report = _summaries(run)
    assert report["chapters_total"] == 3
    assert report["chapters_skipped"] == 2
    assert report["chapters_issued"] == 1
    skipped_ids = {s["chapter_id"] for s in report["skipped"]}
    issued_ids = {c["chapter_id"] for c in report["chapters"]}
    assert not (skipped_ids & issued_ids)
    for entry in report["skipped"]:
        assert "нет выровненных текстовых сегментов" in entry["reason"]
    assert any("S4 summary" in w and "нет выровненных" in w for w in run.quality["warnings"])
    # Вопросы/упражнения пустых глав тоже отсутствуют.
    q_report = _questions(run)
    e_report = _exercises(run)
    assert {q["chapter_id"] for q in q_report["questions"]} == issued_ids
    assert {e["chapter_id"] for e in e_report["exercises"]} == issued_ids


# ------------------------------------------------------------------ T-S4-2


def test_s4_2_gap_word_not_in_sentence_unit() -> None:
    # S4-2 (unit): пропущенное слово не в исходном предложении — отклонение.
    item = {
        "question": "Вставьте пропущенное слово: The house sat on the ____.",
        "type": "gap",
        "level": "easy",
        "segment_id": "seg-0",
        "entities": ["house"],
        "answer": "ZzblqAbsentWord",
        "original_sentence": "The house sat on the hill.",
        "sentence_with_blank": "The house sat on the ____.",
    }
    reasons = validate_question(
        item, chapter_seg_ids=frozenset({"seg-0"}), chapter_text="The house sat on the hill.",
        normalize=str.casefold,
    )
    assert reasons and any("S4.2" in reason for reason in reasons)


def test_s4_2_gap_missing_word_scenario(s4_env: S1Env) -> None:
    # S4-2 (сценарий s4_gap_missing_word): gap-вопрос с отсутствующим словом
    # отклонён, не выпущен.
    run = s4_env.run(
        "t2", "s4_en1.epub", lang="en", learning_level="Learning",
        scenario="s4_gap_missing_word",
    )
    assert run.error is None
    report = _questions(run)
    bad = [
        r for r in report["rejected"]
        if any("отсутствует в исходном предложении (S4.2)" in reason for reason in r["reasons"])
    ]
    assert bad, "gap-вопрос с чужим словом отклонён с причиной"
    assert all(q["type"] != "gap" or "ZzblqAbsentWord" not in q["question"]
               for q in report["questions"])


# ------------------------------------------------------------------ T-S4-3


def test_s4_3_mc_two_correct_unit() -> None:
    # S4-3 (unit): два правильных варианта — отклонение; дубль — отклонение.
    item = {
        "question": "Выберите верный вариант по фрагменту: The fox runs.",
        "type": "mc",
        "level": "standard",
        "segment_id": "seg-0",
        "entities": ["fox"],
        "answer": "fox",
        "options": [
            {"text": "fox", "correct": True},
            {"text": "wolf", "correct": True},
            {"text": "bear", "correct": False},
            {"text": "deer", "correct": False},
        ],
    }
    reasons = validate_question(
        item, chapter_seg_ids=frozenset({"seg-0"}), chapter_text="The fox runs fast.",
        normalize=str.casefold,
    )
    assert any("число правильных вариантов" in reason for reason in reasons)
    item["options"][1] = {"text": "fox", "correct": False}  # дубль + один correct
    reasons = validate_question(
        item, chapter_seg_ids=frozenset({"seg-0"}), chapter_text="The fox runs fast.",
        normalize=str.casefold,
    )
    assert any("дублирующиеся варианты" in reason for reason in reasons)


def test_s4_3_mc_two_correct_scenario(s4_env: S1Env) -> None:
    # S4-3 (сценарий): mc с двумя правильными — отклонён, не выпущен.
    run = s4_env.run(
        "t3a", "s4_en1.epub", lang="en", learning_level="Learning",
        scenario="s4_mc_two_correct",
    )
    assert run.error is None
    report = _questions(run)
    bad = [
        r for r in report["rejected"]
        if any("число правильных вариантов" in reason for reason in r["reasons"])
    ]
    assert bad, "mc с двумя правильными отклонён с причиной"


def test_s4_3_mc_dup_option_scenario(s4_env: S1Env) -> None:
    # S4-3 (сценарий): mc с дублирующимися вариантами — отклонён.
    run = s4_env.run(
        "t3b", "s4_en1.epub", lang="en", learning_level="Learning",
        scenario="s4_mc_dup_option",
    )
    assert run.error is None
    report = _questions(run)
    bad = [
        r for r in report["rejected"]
        if any("дублирующиеся варианты" in reason for reason in r["reasons"])
    ]
    assert bad, "mc с дублем варианта отклонён с причиной"


# ------------------------------------------------------------------ T-S4-4


def test_s4_4_entity_not_in_chapter_unit() -> None:
    # S4-4 (unit): сущность, отсутствующая в тексте главы — отклонение.
    item = {
        "question": "Кто строил мост?",
        "type": "free",
        "level": "advanced",
        "segment_id": "seg-0",
        "entities": ["QwzxwGhost"],
        "answer": "Рабочие.",
    }
    reasons = validate_question(
        item, chapter_seg_ids=frozenset({"seg-0"}), chapter_text="Workers built the bridge.",
        normalize=str.casefold,
    )
    assert any("S4.4" in reason for reason in reasons)


def test_s4_4_entity_ghost_scenario(s4_env: S1Env) -> None:
    # S4-4 (сценарий): вопрос с «призрачной» сущностью отклонён, не выпущен.
    run = s4_env.run(
        "t4", "s4_en1.epub", lang="en", learning_level="Learning",
        scenario="s4_entity_ghost",
    )
    assert run.error is None
    report = _questions(run)
    bad = [
        r for r in report["rejected"]
        if any("сущность отсутствует в главе (S4.4)" in reason for reason in r["reasons"])
    ]
    assert bad, "вопрос с отсутствующей сущностью отклонён с причиной"
    for question in report["questions"]:
        assert "QwzxwGhost" not in question["entities"]


# ------------------------------------------------------------------ T-S4-5


def test_s4_5_answers_separated_from_body(s4_env: S1Env) -> None:
    # S4-5: в записях тела (questions.json/exercises.json) НЕТ полей ответа;
    # answers.json содержит все ответы; gap-ответ ∈ исходном предложении,
    # предложение с пропуском — по конвенции BLANK.
    run = s4_env.run("t5", "s4_en.epub", lang="en", learning_level="Intensive")
    assert run.error is None
    q_report = _questions(run)
    e_report = _exercises(run)
    answers = _answers(run)
    assert q_report["questions"], "вопросы выпущены"
    assert e_report["exercises"], "упражнения выпущены"
    for record in q_report["questions"]:
        assert "answer" not in record, f"ответ в теле вопроса: {record['id']}"
        for option in record.get("options") or []:
            assert isinstance(option, str), "варианты в теле — только текст"
    for record in e_report["exercises"]:
        assert "answer" not in record, f"ответ в теле упражнения: {record['id']}"
    chapters = answers["chapters"]
    for chapter_id, chapter in chapters.items():
        for kind, ids in (("questions", chapter.get("questions", {})),
                          ("exercises", chapter.get("exercises", {}))):
            assert ids, f"{chapter_id}/{kind}: пустые ответы"
            for item_id, answer in ids.items():
                assert str(answer).strip(), f"пустой ответ: {chapter_id}/{kind}/{item_id}"
    # Каждый выпущенный вопрос/упражнение — с ответом.
    all_qids = {q["id"] for q in q_report["questions"]}
    all_eids = {e["id"] for e in e_report["exercises"]}
    answered_qids = {qid for ch in chapters.values() for qid in ch.get("questions", {})}
    answered_eids = {eid for ch in chapters.values() for eid in ch.get("exercises", {})}
    assert all_qids == answered_qids
    assert all_eids == answered_eids
    # gap: ответ — слово исходного предложения; blank — по конвенции.
    gaps = [q for q in q_report["questions"] if q["type"] == "gap"]
    assert gaps, "gap-вопросы выпущены"
    for record in gaps:
        answer = chapters[record["chapter_id"]]["questions"][record["id"]]
        assert BLANK in record["sentence_with_blank"]
        assert (
            answer.casefold()
            in {w.casefold() for w in split_words(record["original_sentence"])}
        )
        assert record["sentence_with_blank"] == blank_first_occurrence(
            record["original_sentence"], answer
        )
    # mc: ответ — индекс варианта 0..N-1.
    for record in q_report["questions"]:
        if record["type"] != "mc":
            continue
        answer = chapters[record["chapter_id"]]["questions"][record["id"]]
        assert 0 <= int(answer) < len(record["options"])


# ------------------------------------------------------------------ T-S4-6


def test_s4_6_vocabulary_only_from_issued_cards_unit() -> None:
    # S4-6 (unit): vocab с чужим id карточки / grammar с чужим id заметки —
    # отклонение (новый материал не порождаётся).
    common: dict[str, Any] = {
        "chapter_seg_ids": frozenset({"seg-0"}),
        "chapter_text": "The harbor kept the anchor.",
        "allowed_types": frozenset({"vocab", "grammar"}),
        "normalize": str.casefold,
    }
    vocab = {
        "type": "vocab",
        "prompt": "Выберите перевод слова «harbor».",
        "answer": "гавань",
        "segment_id": "seg-0",
        "source_ref": {"kind": "card", "id": "ghost_lemma_x"},
    }
    reasons = validate_exercise(vocab, card_ids=frozenset({"harbor"}), note_ids=frozenset(),
                                **common)
    assert any("S4.6" in reason for reason in reasons)
    vocab_ok = dict(vocab, source_ref={"kind": "card", "id": "harbor"})
    assert validate_exercise(vocab_ok, card_ids=frozenset({"harbor"}), note_ids=frozenset(),
                             **common) == []
    grammar = {
        "type": "grammar",
        "prompt": "Объясните форму «was built».",
        "answer": "Пассивный залог.",
        "segment_id": "seg-0",
        "source_ref": {"kind": "note", "id": "ghost_note_x"},
    }
    reasons = validate_exercise(grammar, card_ids=frozenset(), note_ids=frozenset({"was built"}),
                                **common)
    assert any("S4.6" in reason for reason in reasons)


def test_s4_6_vocabulary_only_from_issued_cards(s4_env: S1Env) -> None:
    # S4-6 (сквозной): 100% vocab-упражнений из выпущенных S1-карточек главы,
    # grammar — из выпущенных S3-заметок главы; метрика vocab_from_cards = 1.0.
    run = s4_env.run("t5", "s4_en.epub", lang="en", learning_level="Intensive")
    cards = _read_json(run, "vocabulary_cards.json")
    cards_by_chapter: dict[str, set[str]] = {}
    for card in cards["cards"]:
        cards_by_chapter.setdefault(str(card.get("chapter_id", "")), set()).add(
            str(card.get("lemma", ""))
        )
    report = _exercises(run)
    vocab = [e for e in report["exercises"] if e["type"] == "vocab"]
    grammar = [e for e in report["exercises"] if e["type"] == "grammar"]
    assert vocab, "vocab-упражнения выпущены (карточки S1 есть)"
    if any(cards_by_chapter.values()):
        assert report["vocab_from_cards"] == 1.0
    for exercise in vocab:
        ref = exercise["source_ref"]
        assert ref and ref.get("kind") == "card"
        assert ref["id"] in cards_by_chapter.get(exercise["chapter_id"], set()), (
            f"vocab-упражнение вне выпущенных карточек: {ref}"
        )
    notes = _read_json(run, "grammar_notes.json")
    notes_by_chapter: dict[str, set[str]] = {}
    for note in notes["notes"]:
        notes_by_chapter.setdefault(str(note.get("chapter_id", "")), set()).add(
            str(note.get("surface", ""))
        )
    for exercise in grammar:
        ref = exercise["source_ref"]
        assert ref and ref.get("kind") == "note"
        assert ref["id"] in notes_by_chapter.get(exercise["chapter_id"], set())


def test_s4_6_vocab_ghost_scenario(s4_env: S1Env) -> None:
    # S4-6 (сценарий s4_vocab_ghost): source_ref на карточку вне выпущенных —
    # упражнение отклонено, не выпущено.
    run = s4_env.run(
        "t6", "s4_en.epub", lang="en", learning_level="Intensive",
        scenario="s4_vocab_ghost",
    )
    assert run.error is None
    report = _exercises(run)
    bad = [
        r for r in report["rejected"]
        if any("S4.6" in reason for reason in r["reasons"])
    ]
    assert bad, "упражнение с призрачной карточкой отклонено с причиной"
    for exercise in report["exercises"]:
        ref = exercise.get("source_ref") or {}
        assert ref.get("id") != "ghost_lemma_x"


# ------------------------------------------------------------------ T-S4-7


@pytest.mark.parametrize(
    ("lang", "book"),
    [
        ("en", "s4_en1.epub"),
        ("de", "s4_de.epub"),
        ("fr", "s4_fr.epub"),
        ("it", "s4_it.epub"),
        ("nl", "s4_nl.epub"),
    ],
)
def test_s4_7_five_source_languages(s4_env: S1Env, lang: str, book: str) -> None:
    # S4-7: 5 исходных языков (Intensive) — summary + вопросы + упражнения
    # выпущены, секция s4 в quality, артефакты на месте.
    run = s4_env.run(f"t7_{lang}", book, lang=lang, learning_level="Intensive")
    assert run.error is None, f"{lang}: {run.error}"
    assert run.job.status is JobStatus.LEARNING
    assert run.quality["s4"]["summary"]["enabled"] is True
    assert run.quality["s4"]["questions"]["enabled"] is True
    assert run.quality["s4"]["exercises"]["enabled"] is True
    assert _summaries(run)["chapters_issued"] >= 1, f"{lang}: summary выпущен"
    assert _questions(run)["questions_issued"] >= 1, f"{lang}: вопросы выпущены"
    assert _exercises(run)["exercises_issued"] >= 1, f"{lang}: упражнения выпущены"
    for report in (_summaries(run), _questions(run), _exercises(run)):
        assert report["prompt_file"], f"{lang}: промпт записан (E4-2)"
        assert report["prompt_version"] >= 1


def test_s4_7_level_gating(s4_env: S1Env) -> None:
    # Таблица уровней ТЗ_03: Minimal/Balanced — S4 не исполняется;
    # Learning — только вопросы; Intensive — summary + вопросы + упражнения.
    minimal = s4_env.run("t7b_min", "s4_en1.epub", lang="en", learning_level="Minimal")
    assert minimal.error is None
    assert "s4" not in minimal.quality, "Minimal: S4 не должен исполняться"
    assert not (minimal.job_dir / "summaries.json").is_file()
    assert not (minimal.job_dir / "questions.json").is_file()
    assert not (minimal.job_dir / "exercises.json").is_file()
    balanced = s4_env.run("t7b_bal", "s4_en1.epub", lang="en", learning_level="Balanced")
    assert balanced.error is None
    assert "s1" in balanced.quality, "Balanced: S1 исполняется"
    assert "s4" not in balanced.quality, "Balanced: S4 не входит в уровень"
    assert not (balanced.job_dir / "questions.json").is_file()
    learning = s4_env.run("t7b_learn", "s4_en1.epub", lang="en", learning_level="Learning")
    assert learning.error is None
    assert learning.quality["s4"]["questions"]["enabled"] is True
    assert "summary" not in learning.quality["s4"], "Learning: summary вне уровня"
    assert "exercises" not in learning.quality["s4"], "Learning: упражнения вне уровня"
    assert (learning.job_dir / "questions.json").is_file()
    assert not (learning.job_dir / "summaries.json").is_file()
    assert not (learning.job_dir / "exercises.json").is_file()
    intensive = s4_env.run("t7b_int", "s4_en1.epub", lang="en", learning_level="Intensive")
    assert intensive.error is None
    for part in ("summary", "questions", "exercises"):
        assert intensive.quality["s4"][part]["enabled"] is True


# ------------------------------------------------------------------ T-S4-8


def test_s4_8_image_chapter_no_summary(s4_env: S1Env) -> None:
    # T-S4-8: глава-изображение (текстовых сегментов нет) — summary не
    # создаётся, пропуск зафиксирован; книга не падает.
    run = s4_env.run("t1", "s4_sparse.epub", lang="en", learning_level="Intensive")
    assert run.error is None
    assert run.job.status is JobStatus.LEARNING
    report = _summaries(run)
    skipped = {s["chapter_id"]: s["reason"] for s in report["skipped"]}
    issued = [c["chapter_id"] for c in report["chapters"]]
    assert len(skipped) == 2, "обе нетекстовые главы пропущены"
    assert len(issued) == 1, "текстовая глава суммаризирована"
    for chapter_id in skipped:
        assert chapter_id not in issued
    # В EPUB главы-изображения есть (проверка целостности пакета).
    with zipfile.ZipFile(run.job_dir / "target.epub") as archive:
        names = archive.namelist()
    assert "OEBPS/image1.png" in names


# ------------------------------------------------------------------ T-S4-9


def test_s4_9_mc_not_systematic(s4_env: S1Env) -> None:
    # T-S4-9: 100 mc-вопросов — правильный вариант НЕ систематически самый
    # длинный; позиции правильного распределены (защита от угадывания).
    run = s4_env.run("t5", "s4_en.epub", lang="en", learning_level="Intensive")
    report = _questions(run)
    answers = _answers(run)
    mc = _mc_answers(report, answers)
    assert len(mc) >= 100, f"нужно ≥100 mc-вопросов, выпущено {len(mc)}"
    positions = Counter(m["answer"] for m in mc)
    assert len(positions) >= 3, f"позиции правильного почти не меняются: {positions}"
    top_share = max(positions.values()) / len(mc)
    assert top_share < 0.6, f"правильный систематически на одной позиции: {positions}"
    longest_hits = 0
    for m in mc:
        options = m["record"]["options"]
        correct_len = len(options[m["answer"]])
        longest_hits += 1 if correct_len == max(len(o) for o in options) else 0
    longest_share = longest_hits / len(mc)
    assert longest_share < 0.6, (
        f"правильный систематически самый длинный: {longest_share:.0%} из {len(mc)}"
    )
