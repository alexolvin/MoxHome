"""Детерминированный OpenAI-совместимый mock-сервер (тестовый инструмент, D3).

Назначение: E1 (разведка движка без реального эндпоинта) и E1-тесты.
Сценарии (set_scenario):
  ok               — translate = code(text); fill = шаблон с code-преобразованием текстов;
  fill_garbage     — fill отвечает не-XML (триггер on_fill_failed);
  translate_empty  — translate отвечает пустой строкой;
  http500          — все запросы → HTTP 500;
  timeout          — сервер не отвечает дольше таймаута клиента (сценарий таймаута).

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
            kind = "translate" if system.startswith("You are a translator") else "fill"

            with state.lock:
                scenario = state.scenario

            if scenario == "http500":
                error_payload = {
                    "error": {"message": "mock internal error", "type": "server_error"}
                }
                self._send_json(500, json.dumps(error_payload))
                return
            if scenario == "timeout":
                time.sleep(30)

            if kind == "translate":
                response = "" if scenario == "translate_empty" else code(user)
            elif scenario == "fill_garbage":
                response = "Sorry, I cannot produce the requested XML structure."
            else:
                match = _FILL_TEMPLATE_RE.search(user)
                if match is None:
                    response = "I cannot produce the requested XML structure."
                else:
                    root = ET.fromstring(match.group(1))
                    _apply_code_to_element(root)
                    response = ET.tostring(root, encoding="unicode")

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
            self._send_sse(response, model_name, prompt_tokens, completion_tokens)

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

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)
