"""Детерминированный OpenAI-совместимый mock-сервер (тестовый инструмент, D3).

Назначение: E1 (разведка движка), E3 (воркер) и E4 (клиент анализа).
Сценарии (set_scenario):
  ok               — translate = code(text); fill = шаблон с code-преобразованием
                     текстов; analysis = валидный JSON {"items": []};
  fill_garbage     — fill отвечает не-XML (триггер on_fill_failed);
  translate_empty  — translate отвечает пустой строкой;
  http500          — все запросы → HTTP 500;
  timeout          — сервер не отвечает дольше таймаута клиента (сценарий таймаута);
  empty            — analysis отвечает пустой строкой (E4.6);
  bad_json         — analysis отвечает некорректным JSON (E4.6);
  refuse           — analysis отвечает повторяющимся отказом (E4.6);
  glossary_inconsistent — T-L2-3/T-L3-1: в ответе translate сентинел «Wexley»
                     (rot3) заменяется на «Xrongy» → несогласованный перевод
                     термина глоссария (analysis не затрагивается);
                     «Badparadigm» → «Pbadqw» (T-L3-9: unverified-термин с
                     расхождением — автоправки нет);
  glossary_inconsistent_triple — T-L3-11: «Wexley» заменяется на одну из трёх
                     неверных форм (детерминированно по слову из сегмента);
  glossary_fix_no_term — T-L3-14: как glossary_inconsistent, но ответ задачи
                     glossary_fix не содержит требуемого термина (валидация
                     п.1 срабатывает, правка отклоняется);
  vocab_ghost           — T-S1-8: ответ vocabulary_card содержит лишнюю
                     «призрачную» карточку (лемма не среди кандидатов);
  vocab_generated_example — S1-6: у первой карточки выдуманный пример с
                     example_generated=true (помеченные допускаются);
  vocab_schema_bad      — S1-4: у первой карточки translation="—" (плейсхолдер
                     → отклонение по card_schema.json, выпускается 0);
  mwe_bad_id            — S2-1: у первой записи mwe_note phenomenon_id вне
                     таксономии языка (ZZ-S2-FAKE) → детерминированная
                     валидация отклоняет, принятых 0 с этим id;
  grammar_bad_id        — S3-1: у первой заметки grammar_note phenomenon_id
                     вне таксономии языка (ZZ-S3-FAKE) → отклонение;
  grammar_bad_quote     — S3-2: у первой заметки surface не среди
                     детерминированных кандидатов («подброшенная» цитата) →
                     отклонение;
  grammar_note_too_long — T-S3-10: у первой заметки explanation длиннее
                     лимита → отклонение, а не тихая усечка;
  s4_entity_ghost     — S4-4: у первого вопроса entities содержит слово,
                     отсутствующее в главе → отклонение;
  s4_mc_two_correct   — S4-3: у первого mc-вопроса два правильных
                     варианта → отклонение;
  s4_mc_dup_option    — S4-3: у первого mc-вопроса дублирующиеся
                     варианты → отклонение;
  s4_gap_missing_word — S4-2: ответ gap-задания отсутствует в исходном
                     предложении → отклонение;
  s4_vocab_ghost      — S4-6: source_ref первого vocab-упражнения
                     указывает на карточку вне выпущенных → отклонение.

Сценарии, помеченные «analysis», действуют только на запросы вида analysis
(системный промпт начинается с "You are an analysis engine"); на translate/fill
они не влияют (эти запросы обрабатываются как в ok).

code() — по-символьный детерминированный сдвиг латиницы (rot3): перевод отличается от
оригинала, сохраняя структуру и длину; коммутирует с конкатенацией и нормализацией,
поэтому ответ fill (шаблон с code по всем текстовым узлам) консистентен с ответом translate.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.etree import ElementTree as ET

import tiktoken

from app.learning.study import blank_first_occurrence

# Явный re-export (mypy strict: no_implicit_reexport): v111_tailnet.py
# читает и заменяет mls.ThreadingHTTPServer.
__all__ = ["MockLLMServer", "ThreadingHTTPServer"]

_ROT = 3
_FILL_TEMPLATE_RE = re.compile(r"```XML\n(.*?)\n```", re.DOTALL)

# L3: сериализация графтованных блоков — в пространствах имён книги
# (без ns0-префиксов; семантически не критично, чисто для читаемости).
ET.register_namespace("", "http://www.w3.org/1999/xhtml")
ET.register_namespace("epub", "http://www.idpf.org/2007/ops")


def code(text: str) -> str:
    """По-символьный сдвиг латиницы (rot3), остальные символы не меняются."""
    out: list[str] = []
    for ch in text:
        if "a" <= ch <= "z":
            out.append(chr(ord("a") + (ord(ch) - ord("a") + _ROT) % 26))
        elif "A" <= ch <= "Z":
            out.append(chr(ord("A") + (ord(ch) - ord("A") + _ROT) % 26))
        else:
            out.append(ch)
    return "".join(out)


def _apply_code_to_element(element: ET.Element) -> None:
    if element.text:
        element.text = code(element.text)
    for child in element:
        _apply_code_to_element(child)
        if child.tail:
            child.tail = code(child.tail)


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.scenario = "ok"
        self.delay_seconds = 0.0
        self.requests: list[dict[str, object]] = []


class MockLLMServer:
    """Запускается в отдельном потоке; порт назначает ОС (127.0.0.1)."""

    def __init__(self) -> None:
        self._state = _State()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port = 0

    # -- управление --

    def start(self) -> int:
        state = self._state
        httpd = _build_server(state, lambda: self._httpd)
        self._httpd = httpd
        self._port = httpd.server_address[1]
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()
        return self._port

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def set_scenario(self, mode: str) -> None:
        with self._state.lock:
            self._state.scenario = mode

    def set_delay(self, seconds: float) -> None:
        """Имитация сетевой задержки ответа (для детерминированного kill в середине перевода)."""
        with self._state.lock:
            self._state.delay_seconds = max(0.0, float(seconds))

    def delay_seconds(self) -> float:
        with self._state.lock:
            return self._state.delay_seconds

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/v1"

    def requests_log(self) -> list[dict[str, object]]:
        with self._state.lock:
            return list(self._state.requests)


def _count_prompt_tokens(messages: list[dict[str, str]]) -> int:
    encoding = tiktoken.get_encoding("cl100k_base")
    total = 0
    for message in messages:
        total += 4 + len(encoding.encode(str(message.get("content", ""))))
    return total


_TASK_RE = re.compile(r"\. Task: (\w+)\.")

#: Стандартные поля OpenAI-совместимого запроса chat/completions:
#: «extra» в requests_log содержит только нестандартные (extra_body профилей, D80).
_STANDARD_REQUEST_KEYS = frozenset(
    {
        "model",
        "messages",
        "temperature",
        "top_p",
        "stream",
        "stream_options",
        "max_tokens",
        "stop",
        "seed",
        "n",
        "user",
        "logprobs",
        "top_logprobs",
        "presence_penalty",
        "frequency_penalty",
        "response_format",
    }
)

#: Стандартные поля тела запроса `completions` (MTPE M3): остальное — `extra`.
_STANDARD_COMPLETIONS_REQUEST_KEYS = frozenset(
    {
        "model",
        "prompt",
        "suffix",
        "max_tokens",
        "temperature",
        "top_k",
        "top_p",
        "stream",
        "stop",
        "seed",
        "n",
        "logprobs",
        "echo",
        "user",
    }
)

#: MT-черновик (MTPE M3): детерминированные ответы mock-мира. `ok` — на целевом
#: (ru) языке; `mt_wrong_lang` — на другом (en) → флаг `draft_bad`. `mt_echo` —
#: эхо промпта (содержит маркер шаблона) → `draft_bad`.
_MT_OK_RU = "Это пример перевода текста на русском языке для проверки черновика."
_MT_WRONG_EN = "This is a sample English sentence to trigger the wrong-language flag."


def _mt_response(scenario: str, prompt: str, model: str, max_tokens: int) -> tuple[str, str, str]:
    """(text, finish_reason, response_model) для транспорта `completions`.

    Прогрев (max_tokens == 1) — всегда корректный ответ (модель = запрошенная).
    Сценарии черновика: model_mismatch (модель ≠ запрошенной), mt_echo (эхо),
    mt_truncate (finish_reason=length), mt_wrong_lang (чужой язык), ok (ru).
    """
    if max_tokens == 1:
        return " ", "stop", model
    if scenario == "model_mismatch":
        return _MT_OK_RU, "stop", "mt-wrong-model"
    if scenario == "mt_echo":
        return prompt, "stop", model
    if scenario == "mt_truncate":
        return _MT_OK_RU[: len(_MT_OK_RU) // 2], "length", model
    if scenario == "mt_wrong_lang":
        return _MT_WRONG_EN, "stop", model
    return _MT_OK_RU, "stop", model


#: L2: mock-аннотация глоссария. Sentinel-термины (для T-L2-6/T-L2-11):
#: «badparadigm» (casefold) — 12 идентичных форм БЕЗ объяснений →
#: paradigm_unverified; «indeclinable» (casefold) — явная метка
#: несклоняемости (inflected=false, форм нет).
_GLOSSARY_SENTINEL_BAD = "badparadigm"
_GLOSSARY_SENTINEL_INDECLINABLE = "indeclinable"
#: Suffixes for the deterministic mock paradigm (12 positions, ru case names
#: order from pack.yaml); distinct suffixes → verified paradigm (L2-6).
_MOCK_PARADIGM_SUFFIXES = ("", "a", "u", "o2", "om", "e2", "i2", "ov", "am", "i3", "ami", "ax")

#: L2/T-L2-3: сценарий «glossary_inconsistent» — инъекция несогласованного
#: перевода: исходные термины-сентинелы в ответе translate/fill заменяются на
#: «неправильные» формы (в выходе нет НИ ОДНОЙ формы глоссарийной парадигмы —
#: расхождение по критерию L3.2). Анализ (аннотация) при этом не меняется.
_INCONSISTENT_TERM = "Wexley"
_INCONSISTENT_WRONG = "Xrongy"
#: T-L3-9: unverified-сентинел (badparadigm → 12 идентичных форм) с
#: расхождением в переводе — автоправка запрещена, только отчёт (L3-9).
_INCONSISTENT_MAP = {_INCONSISTENT_TERM: _INCONSISTENT_WRONG, "Badparadigm": "Pbadqw"}
#: T-L3-11: три разные неверные формы имени (инъекция — по слову сегмента,
#: детерминированно: одно и то же слово → одна и та же форма).
_TRIPLE_WRONG = ("Xrongy", "Yrrqjz", "Zssabw")
_TRIPLE_AROUND = code("around")
_TRIPLE_RE = re.compile(
    r"(\w+) " + re.escape(_TRIPLE_AROUND) + " " + re.escape(code(_INCONSISTENT_TERM))
)


def _triple_repl(match: re.Match[str]) -> str:
    """Каждое вхождение — своя форма по предшествующему слову (T-L3-11:
    «имя, переведённое тремя разными способами» — 20 слов → 3 формы)."""
    index = sum(ord(c) for c in match.group(1)) % len(_TRIPLE_WRONG)
    return f"{match.group(1)} {_TRIPLE_AROUND} {_TRIPLE_WRONG[index]}"


def _mock_paradigm(translation: str, names: list[str]) -> dict[str, object]:
    """Детерминированная mock-парадигма: каноническая форма + различимые
    суффиксы → проверка парадигмы проходит (различимы, общая основа =
    каноническая форма). Совпадений нет → coincidences пуст."""
    forms = []
    for i in range(len(names)):
        suffix = _MOCK_PARADIGM_SUFFIXES[i % len(_MOCK_PARADIGM_SUFFIXES)]
        forms.append(translation + ("_" + suffix if suffix else ""))
    return {"inflected": True, "forms": forms, "coincidences": {}}


def _glossary_annotation_response(user: str) -> str:
    """Ответ задачи glossary_annotation (L2): эхо кандидатов, перевод =
    rot3 (code) — согласован с движковым rot3-переводом (L2-4/T-L2-7:
    один и тот же термин → один и тот же перевод во всех вхождениях)."""
    marker = "Annotation payload (JSON):\n"
    start = user.find(marker)
    payload_raw = user[start + len(marker) :].strip() if start != -1 else ""
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return json.dumps({"items": []})
    paradigm_forms: dict[str, list[str]] = payload.get("paradigm_forms") or {}
    items: list[dict[str, object]] = []
    for cand in payload.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        term = str(cand.get("term", ""))
        if not term:
            continue
        key = term.casefold()
        pos = cand.get("pos_hint")
        if pos not in ("noun", "verb", "adjective", "proper_noun", "other"):
            pos = "other"
        names = paradigm_forms.get(pos) or []
        if key == _GLOSSARY_SENTINEL_INDECLINABLE:
            paradigm = {"inflected": False, "forms": [], "coincidences": {}}
        elif key == _GLOSSARY_SENTINEL_BAD and names:
            # 12 идентичных форм без объяснений → отклоняется (L2.4).
            paradigm = {
                "inflected": True,
                "forms": [code(term)] * len(names),
                "coincidences": {},
            }
        elif names:
            paradigm = _mock_paradigm(code(term), names)
        else:
            paradigm = {"inflected": False, "forms": [], "coincidences": {}}
        items.append(
            {
                "term": term,
                "translation": code(term),
                "pos": pos,
                "note": "mock annotation (L2)",
                "paradigm": paradigm,
            }
        )
    return json.dumps({"items": items}, ensure_ascii=False)


def _book_style_response() -> str:
    """Ответ задачи book_style (L2.7): детерминированный профиль."""
    return json.dumps(
        {"items": [{"style": "neutral", "region": None, "note": "mock style profile"}]},
        ensure_ascii=False,
    )


def _graft_text(source: ET.Element, trans: ET.Element) -> None:
    """Параллельная трансплантация текстовых узлов (L3, mock-модель):
    text/tail ИСХОДНОГО блока → транслационный блок с code() (rot3).
    Структура (теги, атрибуты, порядок) — транслационная; при несовпадении
    структуры графт обрывается (валидация п.3/п.1 в этом случае честно
    отклонит правку)."""
    if source.text:
        trans.text = code(source.text)
    if len(source) != len(trans):
        return
    for s_child, t_child in zip(source, trans, strict=True):
        _graft_text(s_child, t_child)
        if s_child.tail:
            t_child.tail = code(s_child.tail)


def _glossary_fix_response(user: str, scenario: str) -> str:
    """Ответ задачи glossary_fix (L3): графт исходных текстов в структуру
    текущего перевода. Результат содержит каноническую форму парадигмы
    (translation mock-аннотации = code(term)) и сохраняет внутреннюю
    разметку транслационного блока."""
    marker = "Fix payload (JSON):\n"
    start = user.find(marker)
    payload_raw = user[start + len(marker) :].strip() if start != -1 else ""
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return json.dumps({"items": []})
    segment_id = str(payload.get("segment_id", ""))
    term = str(payload.get("term", ""))
    try:
        source = ET.fromstring(str(payload.get("source_xml", "")))
        trans = ET.fromstring(str(payload.get("translation_xml", "")))
    except ET.ParseError:
        return json.dumps({"items": []})
    _graft_text(source, trans)
    html = ET.tostring(trans, encoding="unicode")
    if scenario == "glossary_fix_no_term":
        # T-L3-14: модель вернула версию без требуемого термина — п.1
        # валидации обязан сработать (правка отклоняется, без повтора).
        html = html.replace(code(term), "Qwzxw")
    return json.dumps(
        {"items": [{"segment_id": segment_id, "html": html}]}, ensure_ascii=False
    )


_VOCAB_PAYLOAD_MARKER = "Card payload (JSON):\n"


def _vocab_pos_fields(lemma: str, pos: str, lang: str) -> dict[str, object]:
    """POS-поля карточки (mock-модель): детерминированно по лемме
    (сдвиг rot3 согласован с переводом mock-глоссария)."""
    s = sum(ord(ch) for ch in lemma)
    fields: dict[str, object] = {}
    if pos == "noun":
        if lang == "de":
            fields["article"] = ("der", "die", "das")[s % 3]
            fields["plural"] = code(lemma) + "en"
        elif lang == "nl":
            fields["article"] = ("de", "het")[s % 2]
            fields["plural"] = code(lemma) + "s"
            fields["diminutive"] = code(lemma) + "je"
        elif lang == "fr":
            fields["gender"] = ("masc", "fem")[s % 2]
        elif lang == "it":
            fields["gender"] = ("masc", "fem")[s % 2]
            fields["plural"] = code(lemma) + "i"
    elif pos == "verb":
        if lang == "de":
            fields["auxiliary"] = ("haben", "sein")[s % 2]
            fields["main_forms"] = {
                "infinitive": code(lemma),
                "past": code(lemma) + "te",
                "participle": "ge" + code(lemma) + "t",
            }
            fields["separable"] = s % 2 == 0
        elif lang == "nl":
            fields["auxiliary"] = ("hebben", "zijn")[s % 2]
            fields["participle"] = "ge" + code(lemma) + "d"
            fields["separable"] = s % 2 == 0
        elif lang == "fr":
            fields["conjugation_group"] = ("1", "2", "3", "4", "irregular")[s % 5]
            fields["auxiliary"] = ("avoir", "etre")[s % 2]
            fields["irregular_forms"] = None
        elif lang == "it":
            fields["conjugation_group"] = ("1", "2", "3", "irregular")[s % 4]
            fields["auxiliary"] = ("avere", "essere")[s % 2]
            fields["irregular_forms"] = None
        elif lang == "en":
            fields["past_tense"] = code(lemma) + "d"
            fields["past_participle"] = code(lemma) + "ed"
    return fields


def _vocabulary_card_response(user: str, scenario: str) -> str:
    """Ответ задачи vocabulary_card (S1): детерминированно по кандидатам —
    по одному item на кандидата, эхо lemma, пример = source_sentence
    (пример ИЗ КНИГИ), usefulness = f(лемма). Сценарии:
    vocab_ghost — лишняя «призрачная» карточка (лемма не среди
    кандидатов → детерминированная валидация обязана отклонить, T-S1-8);
    vocab_generated_example — у первой карточки выдуманный пример с
    example_generated=true (S1-6: помеченные — допускаются);
    vocab_schema_bad — у первой карточки translation="—" (плейсхолдер →
    отклонение по card_schema.json, S1-4)."""
    start = user.find(_VOCAB_PAYLOAD_MARKER)
    if start == -1:
        return json.dumps({"items": []})
    try:
        payload = json.loads(user[start + len(_VOCAB_PAYLOAD_MARKER) :].strip())
    except json.JSONDecodeError:
        return json.dumps({"items": []})
    lang = str(payload.get("source_language", ""))
    items: list[dict[str, object]] = []
    for cand in payload.get("candidates", ()):
        if not isinstance(cand, dict):
            continue
        lemma = str(cand.get("lemma", ""))
        item: dict[str, object] = {
            "lemma": lemma,
            "meaning": code(lemma),
            "translation": code(lemma),
            "example": str(cand.get("source_sentence", "")),
            "example_generated": False,
            "usefulness": (sum(ord(ch) for ch in lemma) % 10) + 1,
        }
        item.update(_vocab_pos_fields(lemma, str(cand.get("pos", "")), lang))
        items.append(item)
    if scenario == "vocab_ghost":
        items.append(
            {
                "lemma": "Ghostlemma",
                "meaning": "ghost word",
                "translation": code("ghostlemma"),
                "example": "A ghost sentence that is not in the book.",
                "example_generated": True,
                "usefulness": 10,
            }
        )
    if scenario == "vocab_generated_example" and items:
        items[0] = dict(
            items[0],
            example="This example was generated by the model, not taken from the book.",
            example_generated=True,
        )
    if scenario == "vocab_schema_bad" and items:
        items[0] = dict(items[0], translation="—")
    return json.dumps({"items": items}, ensure_ascii=False)


_MWE_PAYLOAD_MARKER = "MWE payload (JSON):\n"


def _mwe_note_response(user: str, scenario: str) -> str:
    """Ответ задачи mwe_note (S2): детерминированно по кандидатам — по
    одному item на кандидата, эхо surface, эхо phenomenon_id; canonical —
    unit_lemma для отделяемых единиц, rot3 поверхности для прочих;
    meaning/translation — rot3. Сценарий mwe_bad_id: у первой записи
    phenomenon_id вне таксономии (ZZ-S2-FAKE) — детерминированная
    валидация обязана отклонить (S2-1)."""
    start = user.find(_MWE_PAYLOAD_MARKER)
    if start == -1:
        return json.dumps({"items": []})
    try:
        payload = json.loads(user[start + len(_MWE_PAYLOAD_MARKER) :].strip())
    except json.JSONDecodeError:
        return json.dumps({"items": []})
    items: list[dict[str, object]] = []
    for index, cand in enumerate(payload.get("candidates", ())):
        if not isinstance(cand, dict):
            continue
        surface = str(cand.get("surface", ""))
        unit_lemma = cand.get("unit_lemma")
        phenomenon_id = str(cand.get("phenomenon_id", ""))
        if scenario == "mwe_bad_id" and index == 0:
            phenomenon_id = "ZZ-S2-FAKE"
        items.append(
            {
                "surface": surface,
                "phenomenon_id": phenomenon_id,
                "canonical": str(unit_lemma) if unit_lemma else code(surface),
                "meaning": code(surface),
                "translation": code(surface),
            }
        )
    return json.dumps({"items": items}, ensure_ascii=False)


_GRAMMAR_PAYLOAD_MARKER = "Grammar payload (JSON):\n"
#: S3-5: пояснение mock-заметки — ТОЛЬКО русский текст (без латинских
#: цитат: langdetect с фикс. сидом чувствителен к длинным вкраплениям
#: латиницы в русском предложении — детерминированно, S3-5).
_GRAMMAR_EXPLANATION = (
    "Пояснение: эта грамматическая форма встречается в тексте книги и "
    "соответствует уровню пользователя."
)
#: T-S3-10: заведомо длиннее лимита grammar_note_max_chars (300).
_GRAMMAR_EXPLANATION_TOO_LONG = (
    "Пояснение: эта грамматическая форма встречается в тексте книги и важна "
    "для понимания структуры предложений. Она соответствует уровню "
    "пользователя, поэтому рекомендуется обратить внимание на порядок слов, "
    "наклонение глагола и падежные сочетания в подобных конструкциях. Форма "
    "повторяется в нескольких сегментах и стоит в начале списка приоритетов "
    "главы, потому что её понимание необходимо для прочтения следующих глав."
)


def _grammar_note_response(user: str, scenario: str) -> str:
    """Ответ задачи grammar_note (S3): детерминированно по кандидатам — по
    одному item на кандидата, эхо surface, эхо phenomenon_id; explanation —
    фиксированный русский шаблон. Сценарии: grammar_bad_id — у первой
    заметки phenomenon_id вне таксономии (ZZ-S3-FAKE, S3-1);
    grammar_bad_quote — у первой заметки surface не среди кандидатов
    (цитата «подброшена», S3-2); grammar_note_too_long — у первой заметки
    explanation длиннее лимита (T-S3-10: отклонение, не усечка)."""
    start = user.find(_GRAMMAR_PAYLOAD_MARKER)
    if start == -1:
        return json.dumps({"items": []})
    try:
        payload = json.loads(user[start + len(_GRAMMAR_PAYLOAD_MARKER) :].strip())
    except json.JSONDecodeError:
        return json.dumps({"items": []})
    items: list[dict[str, object]] = []
    for index, cand in enumerate(payload.get("candidates", ())):
        if not isinstance(cand, dict):
            continue
        surface = str(cand.get("surface", ""))
        phenomenon_id = str(cand.get("phenomenon_id", ""))
        explanation = _GRAMMAR_EXPLANATION
        if index == 0:
            if scenario == "grammar_bad_id":
                phenomenon_id = "ZZ-S3-FAKE"
            elif scenario == "grammar_bad_quote":
                surface = "Qwzxw"
            elif scenario == "grammar_note_too_long":
                explanation = _GRAMMAR_EXPLANATION_TOO_LONG
        items.append(
            {"surface": surface, "phenomenon_id": phenomenon_id, "explanation": explanation}
        )
    return json.dumps({"items": items}, ensure_ascii=False)


_S4_SUMMARY_MARKER = "Summary payload (JSON):\n"
_S4_QUESTIONS_MARKER = "Questions payload (JSON):\n"
_S4_EXERCISES_MARKER = "Exercises payload (JSON):\n"

_S4_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_S4_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _s4_sentences(text: str) -> list[str]:
    """Предложения исходного текста (детерминированный split по
    оконечной пунктуации)."""
    return [s for s in (part.strip() for part in _S4_SENTENCE_RE.split(text.strip())) if s]


def _s4_content_words(text: str, min_len: int = 4) -> list[str]:
    """Содержимые слова (буквенные, длина ≥ min_len) в порядке появления."""
    return [w for w in _S4_WORD_RE.findall(text) if len(w) >= min_len]


def _s4_payload_list(payload: dict[str, object], key: str) -> list[object]:
    """Массивное поле payload (пустой список, если отсутствует/не массив)."""
    value = payload.get(key)
    return value if isinstance(value, list) else []


def _s4_segment_sentences(payload: dict[str, object]) -> list[tuple[str, str]]:
    """(segment_id, sentence) — фактические предложения сегментов payload."""
    result: list[tuple[str, str]] = []
    for seg in _s4_payload_list(payload, "segments"):
        if not isinstance(seg, dict):
            continue
        segment_id = str(seg.get("segment_id", ""))
        if not segment_id:
            continue
        for sentence in _s4_sentences(str(seg.get("source", ""))):
            result.append((segment_id, sentence))
    return result


def _s4_distractors(correct: str, pool: list[str], count: int = 3) -> list[str]:
    """Дистракторы для mc (детерминированные): различные слова пула, затем
    варианты. Дистракторам НЕ нужно присутствовать в главе — только сущности."""
    seen = {correct.casefold()}
    out: list[str] = []
    for word in pool:
        if word.casefold() not in seen:
            seen.add(word.casefold())
            out.append(word)
        if len(out) == count:
            break
    i = 1
    while len(out) < count:
        variant = f"{correct} {i}"
        if variant.casefold() not in seen:
            seen.add(variant.casefold())
            out.append(variant)
        i += 1
    return out


def _s4_mc_options(
    correct: str, distractors: list[str], position: int
) -> list[dict[str, object]]:
    """4 варианта, ровно один correct (S4.3); позиция правильного ротируется
    (T-S4-9: правильный не систематически в одном месте/самый длинный)."""
    options: list[dict[str, object]] = [
        {"text": text, "correct": False} for text in distractors
    ]
    options.insert(position, {"text": correct, "correct": True})
    return options


def _s4_first_of_type(items: list[dict[str, object]], qtype: str) -> int | None:
    """Индекс первого item заданного типа (mock-инъекции сценариев)."""
    for idx, item in enumerate(items):
        if item["type"] == qtype:
            return idx
    return None


def _s4_gap_items(
    pick: Callable[[int], tuple[str, str]], chapter_index: int
) -> list[dict[str, object]]:
    """easy — пропуск (S4.2: пропущенное слово в исходном предложении)."""
    items: list[dict[str, object]] = []
    for k in range(5):
        segment_id, sentence = pick(k)
        words = _s4_content_words(sentence)
        if not words:
            continue
        word = words[(chapter_index + k) % len(words)]
        blanked = blank_first_occurrence(sentence, word)
        others = [w for w in words if w.casefold() != word.casefold()][:2]
        items.append(
            {
                "question": f"Вставьте пропущенное слово: {blanked}",
                "type": "gap",
                "level": "easy",
                "segment_id": segment_id,
                "entities": others or [word],
                "answer": word,
                "original_sentence": sentence,
                "sentence_with_blank": blanked,
            }
        )
    return items


def _s4_mc_items(
    pick: Callable[[int], tuple[str, str]], chapter_index: int
) -> list[dict[str, object]]:
    """standard — множественный выбор (S4.3: ровно один correct, позиция
    ротируется)."""
    items: list[dict[str, object]] = []
    for k in range(5):
        segment_id, sentence = pick(5 + k)
        words = _s4_content_words(sentence)
        if not words:
            continue
        correct = words[(chapter_index * 2 + k) % len(words)]
        items.append(
            {
                "question": f"Выберите верный вариант по фрагменту: {sentence}",
                "type": "mc",
                "level": "standard",
                "segment_id": segment_id,
                "entities": [correct],
                "answer": correct,
                "options": _s4_mc_options(
                    correct, _s4_distractors(correct, words), (chapter_index + k) % 4
                ),
            }
        )
    return items


def _s4_tf_items(
    pick: Callable[[int], tuple[str, str]], chapter_index: int
) -> list[dict[str, object]]:
    """standard — верно/неверно."""
    items: list[dict[str, object]] = []
    for k in range(2):
        segment_id, sentence = pick(10 + k)
        words = _s4_content_words(sentence)
        if not words:
            continue
        items.append(
            {
                "question": f"Верно ли утверждение: «{sentence}»?",
                "type": "tf",
                "level": "standard",
                "segment_id": segment_id,
                "entities": [words[0]],
                "answer": "true" if (chapter_index + k) % 2 == 0 else "false",
            }
        )
    return items


def _s4_free_items(
    pick: Callable[[int], tuple[str, str]], chapter_index: int
) -> list[dict[str, object]]:
    """advanced — свободные (эталонный ответ — русский, детерминированный)."""
    items: list[dict[str, object]] = []
    for k in range(3):
        segment_id, sentence = pick(12 + k)
        words = _s4_content_words(sentence)
        if not words:
            continue
        items.append(
            {
                "question": f"Что следует из фрагмента: {sentence}?",
                "type": "free",
                "level": "advanced",
                "segment_id": segment_id,
                "entities": words[:2],
                "answer": "Эталонный ответ: " + code(words[k % len(words)]),
            }
        )
    return items


def _s4_question_items(
    payload: dict[str, object], chapter_index: int, scenario: str
) -> list[dict[str, object]]:
    """S4: детерминированные mock-вопросы — 5 easy (gap), 5 standard (mc) +
    2 (tf), 3 advanced (free) — только по фактическим предложениям payload
    (сущности ∈ главе, gap-слово ∈ предложении, mc — ровно один correct)."""
    sentences = _s4_segment_sentences(payload)
    if not sentences:
        return []

    def pick(index: int) -> tuple[str, str]:
        return sentences[index % len(sentences)]

    items: list[dict[str, object]] = []
    for factory in (_s4_gap_items, _s4_mc_items, _s4_tf_items, _s4_free_items):
        items.extend(factory(pick, chapter_index))
    if scenario == "s4_entity_ghost" and items:
        items[0] = dict(items[0], entities=["QwzxwGhost"])
    if scenario == "s4_gap_missing_word":
        idx = _s4_first_of_type(items, "gap")
        if idx is not None:
            items[idx] = dict(items[idx], answer="ZzblqAbsentWord")
    if scenario in ("s4_mc_two_correct", "s4_mc_dup_option"):
        idx = _s4_first_of_type(items, "mc")
        if idx is not None:
            options = [dict(option) for option in items[idx]["options"]]  # type: ignore[attr-defined]
            if scenario == "s4_mc_two_correct":
                alt = next(i for i, option in enumerate(options) if not option["correct"])
                options[alt] = dict(options[alt], correct=True)
            else:
                options[0] = dict(options[0], text=str(options[1]["text"]))
            items[idx] = dict(items[idx], options=options)
    return items


def _chapter_questions_response(user: str, scenario: str) -> str:
    """Ответ задачи chapter_questions (S4)."""
    start = user.find(_S4_QUESTIONS_MARKER)
    if start == -1:
        return json.dumps({"items": []})
    try:
        payload = json.loads(user[start + len(_S4_QUESTIONS_MARKER) :].strip())
    except json.JSONDecodeError:
        return json.dumps({"items": []})
    chapter_index = sum(ord(ch) for ch in str(payload.get("chapter_id", "")))
    return json.dumps({"items": _s4_question_items(payload, chapter_index, scenario)})


def _chapter_summary_response(user: str) -> str:
    """Ответ задачи chapter_summary (S4): ОДИН объект (не items-массив),
    только по фактическому тексту payload; сущности — слова главы.
    Непарсится payload (прямой run_task с простым текстом, E4-2) — ответ
    остаётся схема-валидным (generic-сущности), пустого объекта нет."""
    start = user.find(_S4_SUMMARY_MARKER)
    payload: dict[str, object] = {}
    if start != -1:
        try:
            parsed: object = json.loads(user[start + len(_S4_SUMMARY_MARKER) :].strip())
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = {}
    words: list[str] = []
    for seg in _s4_payload_list(payload, "segments"):
        if isinstance(seg, dict):
            words.extend(_s4_content_words(str(seg.get("source", ""))))
    entities = list(dict.fromkeys(words))[:5] or ["текст"]
    summary = {
        "brief": "Глава о: " + ", ".join(entities[:3]) + " (mock).",
        "extended": (
            "Mock-суммаризация по фактическому тексту главы. Ключевые слова: "
            + ", ".join(entities)
            + "."
        ),
        "key_events": ["Событие mock: " + entity for entity in entities[:2]],
        "entities": entities,
        "themes": ["Тема mock: " + entities[0]],
        "concepts": [
            "Понятие mock: " + (entities[1] if len(entities) > 1 else entities[0])
        ],
    }
    return json.dumps(summary, ensure_ascii=False)


def _s4_vocab_items(
    cards: list[dict[str, object]], fallback_seg: str
) -> list[dict[str, object]]:
    """vocab — ТОЛЬКО из выпущенных S1-карточек payload (S4.6)."""
    items: list[dict[str, object]] = []
    for card in cards[:3]:
        lemma = str(card.get("id", ""))
        if not lemma:
            continue
        word_form = str(card.get("word_form") or lemma)
        items.append(
            {
                "type": "vocab",
                "prompt": f"Выберите перевод слова «{word_form}».",
                "answer": str(card.get("translation") or code(lemma)),
                "segment_id": str(card.get("segment_id") or fallback_seg),
                "source_ref": {"kind": "card", "id": lemma},
            }
        )
    return items


def _s4_grammar_items(
    notes: list[dict[str, object]], fallback_seg: str
) -> list[dict[str, object]]:
    """grammar — ТОЛЬКО из выпущенных S3-заметок payload (S4.6)."""
    items: list[dict[str, object]] = []
    for note in notes[:3]:
        surface = str(note.get("id", ""))
        if not surface:
            continue
        items.append(
            {
                "type": "grammar",
                "prompt": f"Объясните форму «{surface}».",
                "answer": str(note.get("explanation") or code(surface)),
                "segment_id": str(note.get("segment_id") or fallback_seg),
                "source_ref": {"kind": "note", "id": surface},
            }
        )
    return items


def _s4_gap_exercise(
    pick: Callable[[int], tuple[str, str]], chapter_index: int
) -> dict[str, object] | None:
    segment_id, sentence = pick(5)
    words = _s4_content_words(sentence)
    if not words:
        return None
    word = words[chapter_index % len(words)]
    blanked = blank_first_occurrence(sentence, word)
    return {
        "type": "gap",
        "prompt": f"Вставьте пропущенное слово: {blanked}",
        "answer": word,
        "segment_id": segment_id,
        "source_ref": None,
        "original_sentence": sentence,
        "sentence_with_blank": blanked,
    }


def _s4_mc_exercises(
    pick: Callable[[int], tuple[str, str]], chapter_index: int
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for k in range(2):
        segment_id, sentence = pick(6 + k)
        words = _s4_content_words(sentence)
        if not words:
            continue
        correct = words[(chapter_index + k) % len(words)]
        items.append(
            {
                "type": "mc",
                "prompt": f"Выберите верный вариант по фрагменту: {sentence}",
                "answer": correct,
                "segment_id": segment_id,
                "source_ref": None,
                "options": _s4_mc_options(
                    correct,
                    _s4_distractors(correct, words),
                    (chapter_index + k + 1) % 4,
                ),
            }
        )
    return items


def _s4_tf_exercise(
    pick: Callable[[int], tuple[str, str]], chapter_index: int
) -> dict[str, object] | None:
    segment_id, sentence = pick(8)
    words = _s4_content_words(sentence)
    if not words:
        return None
    return {
        "type": "tf",
        "prompt": f"Верно ли утверждение: «{sentence}»?",
        "answer": "true" if chapter_index % 2 == 0 else "false",
        "segment_id": segment_id,
        "source_ref": None,
        "entities": [words[0]],
    }


def _s4_free_exercise(
    pick: Callable[[int], tuple[str, str]], chapter_index: int
) -> dict[str, object] | None:
    segment_id, sentence = pick(9)
    words = _s4_content_words(sentence)
    if not words:
        return None
    return {
        "type": "free",
        "prompt": f"Что следует из фрагмента: {sentence}?",
        "answer": "Эталонный ответ: " + code(words[chapter_index % len(words)]),
        "segment_id": segment_id,
        "source_ref": None,
        "entities": words[:2],
    }


def _s4_sentence_exercises(
    allowed: set[str], pick: Callable[[int], tuple[str, str]], chapter_index: int
) -> list[dict[str, object]]:
    """Упражнения, строящиеся по предложениям главы (translation/gap/mc/tf/
    free): translation — предложение дословно из главы (S4)."""
    items: list[dict[str, object]] = []
    if "translation" in allowed:
        for k in range(2):
            segment_id, sentence = pick(k)
            items.append(
                {
                    "type": "translation",
                    "prompt": sentence,
                    "answer": code(sentence),
                    "segment_id": segment_id,
                    "source_ref": None,
                }
            )
    if "gap" in allowed:
        item = _s4_gap_exercise(pick, chapter_index)
        if item is not None:
            items.append(item)
    if "mc" in allowed:
        items.extend(_s4_mc_exercises(pick, chapter_index))
    if "tf" in allowed:
        item = _s4_tf_exercise(pick, chapter_index)
        if item is not None:
            items.append(item)
    if "free" in allowed:
        item = _s4_free_exercise(pick, chapter_index)
        if item is not None:
            items.append(item)
    return items


def _s4_exercise_items(
    payload: dict[str, object], chapter_index: int, scenario: str
) -> list[dict[str, object]]:
    """S4: детерминированные mock-упражнения — vocab только из карточек
    payload, grammar только из заметок (S4.6), translation дословно из
    главы, типы — из payload.exercise_types (конфиг limits)."""
    allowed = {str(t) for t in _s4_payload_list(payload, "exercise_types")}
    sentences = _s4_segment_sentences(payload)
    fallback_seg = sentences[0][0] if sentences else ""
    items: list[dict[str, object]] = []
    if "vocab" in allowed:
        cards = [c for c in _s4_payload_list(payload, "cards") if isinstance(c, dict)]
        items.extend(_s4_vocab_items(cards, fallback_seg))
    if "grammar" in allowed:
        notes = [n for n in _s4_payload_list(payload, "notes") if isinstance(n, dict)]
        items.extend(_s4_grammar_items(notes, fallback_seg))
    if sentences:
        def pick(index: int) -> tuple[str, str]:
            return sentences[index % len(sentences)]

        items.extend(_s4_sentence_exercises(allowed, pick, chapter_index))
    if scenario == "s4_vocab_ghost":
        idx = _s4_first_of_type(items, "vocab")
        if idx is not None:
            items[idx] = dict(items[idx], source_ref={"kind": "card", "id": "ghost_lemma_x"})
    return items


def _chapter_exercises_response(user: str, scenario: str) -> str:
    """Ответ задачи chapter_exercises (S4)."""
    start = user.find(_S4_EXERCISES_MARKER)
    if start == -1:
        return json.dumps({"items": []})
    try:
        payload = json.loads(user[start + len(_S4_EXERCISES_MARKER) :].strip())
    except json.JSONDecodeError:
        return json.dumps({"items": []})
    chapter_index = sum(ord(ch) for ch in str(payload.get("chapter_id", "")))
    return json.dumps({"items": _s4_exercise_items(payload, chapter_index, scenario)})


_CANNED_ANALYSIS = {
    "empty": "",
    "bad_json": '{"items": [unterminated — not valid JSON',
    "refuse": "I'm sorry, I cannot help you with that request.",
}


def _analysis_response(scenario: str, system: str, user: str) -> str:
    """Ответ для запросов вида analysis (E4.6: детерминированные сценарии;
    L2: task-aware ответы glossary_annotation / book_style; L3: glossary_fix;
    S1: vocabulary_card; S2: mwe_note)."""
    if scenario in _CANNED_ANALYSIS:
        return _CANNED_ANALYSIS[scenario]
    task_match = _TASK_RE.search(system)
    task = task_match.group(1) if task_match is not None else ""
    handlers: dict[str, Callable[[], str]] = {
        "glossary_annotation": lambda: _glossary_annotation_response(user),
        "book_style": _book_style_response,
        "glossary_fix": lambda: _glossary_fix_response(user, scenario),
        "vocabulary_card": lambda: _vocabulary_card_response(user, scenario),
        "mwe_note": lambda: _mwe_note_response(user, scenario),
        "grammar_note": lambda: _grammar_note_response(user, scenario),
        "chapter_summary": lambda: _chapter_summary_response(user),
        "chapter_questions": lambda: _chapter_questions_response(user, scenario),
        "chapter_exercises": lambda: _chapter_exercises_response(user, scenario),
    }
    handler = handlers.get(task)
    if handler is not None:
        return handler()
    return json.dumps({"items": []})


def _detect_kind(system: str) -> str:
    """Вид запроса по системному промпту (translate / analysis / fill / postedit)."""
    if system.startswith("You are a translator"):
        return "translate"
    if system.startswith("You are an analysis engine"):
        return "analysis"
    if system.startswith("You are a post-editing engine"):
        return "postedit"
    return "fill"


def _inject_inconsistent(text: str, scenario: str) -> str:
    """Инъекция несогласованных форм (fill — финальный текст в mock-мире:
    translate-ответ структурно невалиден — code() сдвигает и буквы тегов)."""
    if scenario == "glossary_inconsistent_triple":
        # T-L3-11: форма — детерминированно по слову из сегмента (одно слово —
        # одна форма; 20 слов → 3 разные формы вхождений). Каждое вхождение —
        # отдельный sub: fill-ответ — один XML на документ (все сегменты).
        return _TRIPLE_RE.sub(_triple_repl, text)
    if scenario in ("glossary_inconsistent", "glossary_fix_no_term"):
        # T-L2-3/T-L3: вхождение каждого сентинела — на «неправильную» форму
        # (остальные слова — обычный rot3, согласованный с глоссарием).
        for term, wrong in _INCONSISTENT_MAP.items():
            text = text.replace(code(term), wrong)
    return text


_BARE_AMP_RE = re.compile(r"&(?!(?:[a-zA-Z][a-zA-Z0-9]*|#\d+|#x[0-9a-fA-F]+);)")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _sanitize_xml_fragment(text: str) -> str:
    """Устойчивость mock (не приложения): `encode_friendly` движка пишет
    текстовые узлы без экранирования — сырой «&» из декодированного текста
    книги (напр. «R. Bemporad & Figlio») делает fill-промпт невалидным XML.
    Реальная LLM этим не ограничена; mock имитирует устойчивую модель:
    не-сущностные «&» экранируем, управляющие символы XML 1.0 вырезаем."""
    text = _BARE_AMP_RE.sub("&amp;", text)
    return _CTRL_CHARS_RE.sub("", text)


def _fill_response(user: str, scenario: str) -> str:
    if scenario == "fill_garbage":
        return "Sorry, I cannot produce the requested XML structure."
    match = _FILL_TEMPLATE_RE.search(user)
    if match is None:
        return "I cannot produce the requested XML structure."
    root = ET.fromstring(_sanitize_xml_fragment(match.group(1)))
    _apply_code_to_element(root)
    text = ET.tostring(root, encoding="unicode")
    return _inject_inconsistent(text, scenario)


# ------------------------------------------------------------------ postedit (MTPE M4)

#: Любая форма маркера (совпадает с app/mtpe/checks._MARKER_ANY): `⟦n⟧`, `⟦/n⟧`, `⟦n/⟧`.
_PE_MARKER_RE = re.compile(r"\u27e6\s*([/]?)(\d+)\s*([/]?)\u27e7")
_PE_ORIGINAL_MARKER = "Original (with markers):\n"
_PE_DRAFT_MARKER = "\n\nDraft:\n"
#: S2 (heavy_edit) — текст, далёкий от любого черновика (нормированное
#: расстояние Левенштейна > max_edit_ratio).
_PE_OVEREDIT_TEXT = (
    "Полностью переписанный текст без какого-либо отношения к исходному "
    "черновику для проверки порога массовой правки постредактурой."
)


def _extract_pe_blocks(user: str) -> tuple[str, str]:
    """(оригинал с маркерами, черновик) из 6-блочного user-сообщения §4.2."""
    o_start = user.find(_PE_ORIGINAL_MARKER)
    d_start = user.find(_PE_DRAFT_MARKER)
    if o_start == -1 or d_start == -1:
        return "", ""
    original = user[o_start + len(_PE_ORIGINAL_MARKER) : d_start].strip()
    draft = user[d_start + len(_PE_DRAFT_MARKER) :].strip()
    return original, draft


def _pe_marker_strings(original: str) -> list[str]:
    return [
        "\u27e6" + lead + num + trail + "\u27e7"
        for lead, num, trail in _PE_MARKER_RE.findall(original)
    ]


def _postedit_response(scenario: str, user: str) -> str:
    """Детерминированный ответ postedit (MTPE §4.8). `ok` — черновик с маркерами
    оригинала (H2/H6 проходят); сценарии инъекции ломают конкретную проверку."""
    original, draft = _extract_pe_blocks(user)
    markers = _pe_marker_strings(original)
    if scenario in ("pe_marker_loss", "pe_marker_loss_structural", "pe_reject_burst"):
        text = "".join(markers[:-1]) + draft  # без последнего маркера → H2
        return json.dumps({"text": text, "edits": []}, ensure_ascii=False)
    if scenario == "pe_bad_type":
        return json.dumps(
            {"text": "".join(markers) + draft, "edits": [{"type": "ZZ-FAKE-TYPE", "note": "mock"}]},
            ensure_ascii=False,
        )
    if scenario == "pe_silent_edit":
        changed = draft + " изменено"  # edits пуст, но text ≠ draft → H6
        return json.dumps({"text": "".join(markers) + changed, "edits": []}, ensure_ascii=False)
    if scenario == "pe_overedit":
        return json.dumps(
            {
                "text": "".join(markers) + _PE_OVEREDIT_TEXT,
                "edits": [{"type": "ACC-MISTRANSLATION", "note": "mock overedit"}],
            },
            ensure_ascii=False,
        )
    # ok: черновик + все маркеры оригинала по порядку (H2/H6/вложенность проходят).
    return json.dumps({"text": "".join(markers) + draft, "edits": []}, ensure_ascii=False)


def _build_response(kind: str, scenario: str, user: str, system: str) -> str:
    """Детерминированный ответ на запрос вида kind в сценарии scenario."""
    if kind == "translate":
        if scenario == "translate_empty":
            return ""
        # T-L2-3/T-L3: вхождение сентинела в переводе — на «неправильную»
        # форму (остальные слова — обычный rot3, согласованный с глоссарием).
        return _inject_inconsistent(code(user), scenario)
    if kind == "analysis":
        return _analysis_response(scenario, system, user)
    if kind == "postedit":
        return _postedit_response(scenario, user)
    return _fill_response(user, scenario)


def _build_server(
    state: _State, _httpd_ref: Callable[[], ThreadingHTTPServer | None]
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "MockLLM/1.0"

        def log_message(self, *args: object) -> None:  # тишина: наш лог — requests_log
            pass

        def do_GET(self) -> None:
            if self.path == "/_requests":
                with state.lock:
                    payload = json.dumps(state.requests, ensure_ascii=False)
                self._send_json(200, payload)
            elif self.path == "/v1/models":
                self._send_json(200, json.dumps({"data": [{"id": "probe-model"}]}))
            else:
                self._send_json(404, json.dumps({"error": "not found"}))

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/_control":
                with state.lock:
                    state.scenario = str(body.get("mode", "ok"))
                self._send_json(200, json.dumps({"ok": True, "mode": state.scenario}))
            elif self.path == "/v1/chat/completions":
                self._handle_chat(body)
            elif self.path == "/v1/completions":
                self._handle_completions(body)
            else:
                self._send_json(404, json.dumps({"error": "not found"}))

        def _send_json(self, status: int, payload: str) -> None:
            data = payload.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle_chat(self, body: dict[str, object]) -> None:
            raw_messages = body.get("messages", [])
            if not isinstance(raw_messages, list):
                raw_messages = []
            messages: list[dict[str, str]] = [
                {str(k): str(v) for k, v in item.items()}
                for item in raw_messages
                if isinstance(item, dict)
            ]
            system = str(messages[0].get("content", "")) if messages else ""
            user = str(messages[-1].get("content", "")) if messages else ""
            kind = _detect_kind(system)

            with state.lock:
                scenario = state.scenario
                delay = state.delay_seconds
            if delay > 0:
                time.sleep(delay)

            if scenario == "http500":
                error_payload = {
                    "error": {"message": "mock internal error", "type": "server_error"}
                }
                self._send_json(500, json.dumps(error_payload))
                return
            if scenario == "timeout":
                time.sleep(30)

            response = _build_response(kind, scenario, user, system)

            prompt_tokens = _count_prompt_tokens(messages)
            encoding = tiktoken.get_encoding("cl100k_base")
            completion_tokens = len(encoding.encode(response))

            with state.lock:
                state.requests.append(
                    {
                        "seq": len(state.requests),
                        "scenario": scenario,
                        "kind": kind,
                        "model": body.get("model"),
                        "user_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest()[:16],
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "system": system,
                        "user": user,
                        "response": response,
                        # D80: нестандартные поля тела запроса (extra_body
                        # профилей, напр. chat_template_kwargs) — для проверки
                        # passthrough; {} — если их не было.
                        "extra": {
                            key: value
                            for key, value in body.items()
                            if key not in _STANDARD_REQUEST_KEYS
                        },
                    }
                )

            model_name = str(body.get("model", "probe-model"))
            if body.get("stream"):
                self._send_sse(response, model_name, prompt_tokens, completion_tokens)
            else:
                # Не-streaming (OpenAI-совместимо): один JSON-ответ с usage.
                # Его использует клиент анализа (тракт A, E4.4).
                self._send_json(
                    200,
                    json.dumps(
                        {
                            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                            "object": "chat.completion",
                            "created": int(time.time()),
                            "model": model_name,
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {"role": "assistant", "content": response},
                                    "finish_reason": "stop",
                                }
                            ],
                            "usage": {
                                "prompt_tokens": prompt_tokens,
                                "completion_tokens": completion_tokens,
                                "total_tokens": prompt_tokens + completion_tokens,
                                "prompt_tokens_details": {"cached_tokens": 0},
                            },
                        },
                        ensure_ascii=False,
                    ),
                )

        def _handle_completions(self, body: dict[str, object]) -> None:
            """Транспорт `completions` (MTPE M3): профиль mt. `prompt` — заполненный
            шаблон `mt_draft`; детерминированный ответ по сценарию (mock-мир)."""
            prompt = str(body.get("prompt", ""))
            max_tokens = int(body.get("max_tokens", 16) or 16)
            model_name = str(body.get("model", "mt-model"))

            with state.lock:
                scenario = state.scenario
                delay = state.delay_seconds
            if delay > 0:
                time.sleep(delay)

            if scenario == "http500":
                error_payload = {"error": {"message": "mock internal error"}}
                self._send_json(500, json.dumps(error_payload))
                return

            text, finish_reason, response_model = _mt_response(
                scenario, prompt, model_name, max_tokens
            )
            encoding = tiktoken.get_encoding("cl100k_base")
            prompt_tokens = len(encoding.encode(prompt))
            completion_tokens = len(encoding.encode(text))

            with state.lock:
                state.requests.append(
                    {
                        "seq": len(state.requests),
                        "scenario": scenario,
                        "kind": "mt" if max_tokens > 1 else "mt_warmup",
                        "model": model_name,
                        "max_tokens": max_tokens,
                        "prompt": prompt,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "response": text,
                        "finish_reason": finish_reason,
                        "extra": {
                            key: value
                            for key, value in body.items()
                            if key not in _STANDARD_COMPLETIONS_REQUEST_KEYS
                        },
                    }
                )

            self._send_json(
                200,
                json.dumps(
                    {
                        "id": f"cmpl-{uuid.uuid4().hex[:12]}",
                        "object": "text_completion",
                        "created": int(time.time()),
                        "model": response_model,
                        "choices": [{"index": 0, "text": text, "finish_reason": finish_reason}],
                        "usage": {
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": prompt_tokens + completion_tokens,
                        },
                    },
                    ensure_ascii=False,
                ),
            )

        def _send_sse(
            self, response: str, model: str, prompt_tokens: int, completion_tokens: int
        ) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            created = int(time.time())

            def sse(payload: dict[str, object]) -> None:
                line = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                self.wfile.write(line.encode())

            base = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
            }
            role_chunk = {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
            sse({**base, "choices": [role_chunk]})
            for i in range(0, len(response), 32):
                piece = response[i : i + 32]
                content_chunk = {"index": 0, "delta": {"content": piece}, "finish_reason": None}
                sse({**base, "choices": [content_chunk]})
            sse(
                {
                    **base,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                        "prompt_tokens_details": {"cached_tokens": 0},
                    },
                }
            )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    # Потоки-хендлеры — демоны: сценарий timeout (сон 30 с) не держит выход процесса.
    httpd.daemon_threads = True
    return httpd
