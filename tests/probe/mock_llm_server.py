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
  glossary_inconsistent — T-L2-3: в ответе translate сентинел «Wexley»
                     (rot3) заменяется на «Xrongy» → несогласованный перевод
                     термина глоссария (analysis не затрагивается).

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

_ROT = 3
_FILL_TEMPLATE_RE = re.compile(r"```XML\n(.*?)\n```", re.DOTALL)


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
#: перевода: исходный термин-сентинел в ответе translate заменяется на
#: «неправильную» форму (в выходе нет НИ ОДНОЙ формы глоссарийной парадигмы —
#: расхождение по критерию L3.2). Анализ (аннотация) при этом не меняется.
_INCONSISTENT_TERM = "Wexley"
_INCONSISTENT_WRONG = "Xrongy"


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


def _analysis_response(scenario: str, system: str, user: str) -> str:
    """Ответ для запросов вида analysis (E4.6: детерминированные сценарии;
    L2: task-aware ответы glossary_annotation / book_style)."""
    if scenario == "empty":
        return ""
    if scenario == "bad_json":
        return '{"items": [unterminated — not valid JSON'
    if scenario == "refuse":
        return "I'm sorry, I cannot help you with that request."
    task_match = _TASK_RE.search(system)
    if task_match is not None:
        if task_match.group(1) == "glossary_annotation":
            return _glossary_annotation_response(user)
        if task_match.group(1) == "book_style":
            return _book_style_response()
    return json.dumps({"items": []})


def _detect_kind(system: str) -> str:
    """Вид запроса по системному промпту (translate / analysis / fill)."""
    if system.startswith("You are a translator"):
        return "translate"
    if system.startswith("You are an analysis engine"):
        return "analysis"
    return "fill"


def _fill_response(user: str, scenario: str) -> str:
    if scenario == "fill_garbage":
        return "Sorry, I cannot produce the requested XML structure."
    match = _FILL_TEMPLATE_RE.search(user)
    if match is None:
        return "I cannot produce the requested XML structure."
    root = ET.fromstring(match.group(1))
    _apply_code_to_element(root)
    text = ET.tostring(root, encoding="unicode")
    if scenario == "glossary_inconsistent":
        # T-L2-3: финальный текст в mock-мире приходит из fill-пути (translate-
        # ответ структурно невалиден — code() сдвигает и буквы тегов), поэтому
        # инъекция применяется здесь же.
        text = text.replace(code(_INCONSISTENT_TERM), _INCONSISTENT_WRONG)
    return text


def _build_response(kind: str, scenario: str, user: str, system: str) -> str:
    """Детерминированный ответ на запрос вида kind в сценарии scenario."""
    if kind == "translate":
        if scenario == "translate_empty":
            return ""
        text = code(user)
        if scenario == "glossary_inconsistent":
            # T-L2-3: вхождение сентинела в переводе заменяется на «неправильную»
            # форму (все остальные слова — обычный rot3, согласованный с глоссарием).
            text = text.replace(code(_INCONSISTENT_TERM), _INCONSISTENT_WRONG)
        return text
    if kind == "analysis":
        return _analysis_response(scenario, system, user)
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
