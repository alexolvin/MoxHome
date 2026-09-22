"""R8 (ТЗ §13/§15): «Настройки» — LLM-профили, проба, запись конфига, обслуживание.

- r8_1 корень /settings: 6 строк (href'ы), 0 полей ввода;
- r8_2 /settings/llm рендер: 3 <details> (первый open), поля, ключ
  «задан» (никогда не значение), data-saved у extra_body, toggle;
- r8_3 PUT /api/config/llm (валидный): config.yaml на диске (комментарии
  сохранены, extra_body — новый блок), .env, app.state подменён,
  секретов в ответе 0;
- r8_4 PUT невалидные: по одному полю на запрос — 400; файлы побайтово
  не тронуты (после КАЖДОГО случая);
- r8_5 api_key «» — сохранение текущего (.env не тронут, новый не создан);
- r8_6 toggle «один эндпоинт»: вкл — общий URL во все три профиля,
  выкл — по-профильные (checked из состояния);
- r8_7 POST /api/llm/probe: ok (mock-LLM: модель/токены), сохранённый
  extra_body в теле запроса (passthrough), 401/404/неверный JSON/
  закрытый порт — точные причины; невалидное тело — 400; конфиг не тронут;
- r8_8 GET /api/llm/models: список (mock), 400 (невалидный URL),
  502 (недоступен);
- r8_9 no-JS save: POST /settings/llm → 303 + конфиг обновлён; 400 —
  ввод пользователя сохранён в форме, файлы не тронуты;
- r8_10 no-JS probe: POST /settings/llm/probe → 200 + баннер
  (u3-ok / u3-warn); неизвестный профиль — 400;
- r8_11 appearance: cookies u3-theme/u3-density (Set-Cookie/delete),
  отмеченные радио из cookies;
- r8_12 purge «старше N дней» (API + no-JS-страница): days=0 — все
  терминальные, days=9999 — 404 no-match, невалидные — 400, по умолчанию —
  ui.purge_min_age_days; два шага (confirm_token), чужой токен — 409;
- r8_13 logs/clear: truncate(0) + строка факта, ответ {cleared, bytes};
- r8_14 about: версия из pyproject, коммит «неизвестно» (нет .git),
  /readme (нет файла → «не найден»; есть → содержимое), /logout — 303;
- r8_15 worker-релоад: новая конфигурация перед заданием; битый YAML —
  прежние настройки (не падает);
- r8_16 «Ещё» мастера: строка LLM-профиля (одна модель — её имя;
  разные — «разные»).
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from lxml import html as lhtml

from app.core.config import Settings, load_settings
from app.domain.job import JobStatus
from app.web import views
from app.web.auth import LOGIN_COOKIE, csrf_token
from app.web.strings import UI_STRINGS
from app.workers.worker import Worker

ROOT = Path(__file__).resolve().parents[1]
_TESTS_DIR = str(Path(__file__).resolve().parent)
_PROBE_DIR = str(Path(__file__).resolve().parent / "probe")
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

from mock_llm_server import MockLLMServer  # noqa: E402
from test_r3_web import _set_status  # noqa: E402

# Фикстуру u1_env предоставляет tests/conftest.py (тот же импорт, noqa F401).
from test_u1_web import (  # noqa: E402
    AUTH_SECRET,
    LLM_KEY,
    U1Env,
    U1Profile,
    _create_job,
    _upload_book,
)

#: Профили TЗ §13 (единое имя в API и формах).
_PROFILES = ("translation", "fill", "analysis")


# ------------------------------------------------------------------ окружение


@dataclass
class R8Ctx:
    """Основной профиль u1_env + снимок конфигов (restore для всех
    тестов, пишущих на диск)."""

    profile: U1Profile
    server: MockLLMServer
    orig_cfg: str
    orig_env: str
    orig_settings: Settings

    @property
    def client(self) -> TestClient:
        return self.profile.client

    @property
    def cfg(self) -> Path:
        return self.profile.cfg

    @property
    def env_file(self) -> Path:
        return self.profile.env_file

    def restore(self) -> None:
        self.cfg.write_text(self.orig_cfg, encoding="utf-8")
        self.env_file.write_text(self.orig_env, encoding="utf-8")
        self.profile.app.state.settings = self.orig_settings


@pytest.fixture(scope="module")
def r8(u1_env: U1Env) -> Iterator[R8Ctx]:
    """Профиль main + config.yaml с комментариями и явными полями
    (r8_2/r8_3: сохранение комментариев, границы полей, data-saved)."""
    profile = u1_env.profiles["main"]
    old: dict[str, Any] = yaml.safe_load(profile.cfg.read_text(encoding="utf-8"))
    url = profile.settings.llm.profile("translation").base_url
    text = (
        "# R8 тестовый конфиг: сохранение комментариев (r8_3)\n"
        f"storage:\n  root: {old['storage']['root']}\n"
        f"epubcheck:\n  jar_path: {old['epubcheck']['jar_path']}\n"
        f"languages:\n  root: {old['languages']['root']}\n"
        f"  matrix: {old['languages']['matrix']}\n"
        "llm:\n"
        "  # LLM-профили: одна настройка на все стадии\n"
        f"  prompts_root: {old['llm']['prompts_root']}\n"
        "  profiles:\n"
        "    translation:\n"
        f"      base_url: {url}   # основной эндпоинт\n"
        "      model: test-model\n"
        "      # температура\n"
        "      temperature: 0.7\n"
        "      retry_times: 2\n"
        "      concurrency: 3\n"
        "      extra_body:\n"
        "        chat_template_kwargs:\n"
        "          enable_thinking: false\n"
        "    fill:\n"
        f"      base_url: {url}\n"
        "      model: test-model\n"
        "    analysis:\n"
        f"      base_url: {url}\n"
        "      model: test-model\n"
        "glossary:\n"
        "  enabled: true\n"
        "  fix_enabled: true\n"
    )
    profile.cfg.write_text(text, encoding="utf-8")
    settings = load_settings(profile.cfg, profile.env_file)
    profile.settings = settings
    profile.app.state.settings = settings
    # R8: запись LLM-настроек (create_app принимает пути; __main__ — то же)
    profile.app.state.config_path = profile.cfg
    profile.app.state.env_path = profile.env_file
    ctx = R8Ctx(
        profile=profile,
        server=u1_env.server,
        orig_cfg=text,
        orig_env=profile.env_file.read_text(encoding="utf-8"),
        orig_settings=settings,
    )
    try:
        yield ctx
    finally:
        ctx.restore()  # страховка: модуль уходит с чистым конфигом


# ------------------------------------------------------------------ утилиты


def _baseline_body(r8: R8Ctx) -> dict[str, Any]:
    """Валидное тело PUT /api/config/llm из ТЕКУЩЕГО app.state."""
    st = r8.profile.app.state.settings
    return {
        "shared_endpoint": True,
        "base_url": st.llm.profile("translation").base_url,
        "profiles": {
            name: {
                "base_url": st.llm.profile(name).base_url,
                "model": st.llm.profile(name).model,
                "api_key": "",
                "extra_body": "",
                "temperature": st.llm.profile(name).temperature,
                "retry_times": st.llm.profile(name).retry_times,
                "concurrency": st.llm.profile(name).concurrency,
            }
            for name in _PROFILES
        },
    }


def _llm_form_data(r8: R8Ctx, *, model: str, temperature: str) -> dict[str, str]:
    """no-JS-форма /settings/llm (все 4 варианта URL + по-профильные)."""
    st = r8.profile.app.state.settings
    data: dict[str, str] = {
        "shared_endpoint": "1",
        "base_url": st.llm.profile("translation").base_url,
    }
    for name in _PROFILES:
        data[f"base_url_{name}"] = st.llm.profile(name).base_url
        data[f"model_{name}"] = model
        data[f"api_key_{name}"] = ""
        data[f"temperature_{name}"] = temperature
        data[f"retry_times_{name}"] = "1"
        data[f"concurrency_{name}"] = "1"
        data[f"extra_body_{name}"] = ""
    data["u3_csrf"] = csrf_token(st, "any-user")
    return data


def _fixed_server(status: int, body: bytes) -> ThreadingHTTPServer:
    """Мини-сервер с фиксированным ответом (проба: 401/404/неверный JSON)."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # тишина
            pass

        def _respond(self) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self._respond()

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self._respond()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _make_terminal_job(r8: R8Ctx, book: str, *, old_days: float | None) -> str:
    """Терминальное (CANCELLED) задание; ``old_days`` — подвинуть
    updated_at назад (проверка age-фильтра purge)."""
    profile = r8.profile
    job_id = _create_job(r8.client, book)
    _set_status(profile, job_id, JobStatus.CANCELLED)
    if old_days is not None:
        profile.db.conn.execute(
            "UPDATE jobs SET updated_at = ? WHERE id = ?",
            (time.time() - old_days * 86400, job_id),
        )
        profile.db.conn.commit()
    return job_id


# ------------------------------------------------------------------ корень


def test_r8_1_settings_root_six_rows_no_inputs(r8: R8Ctx) -> None:
    """ТЗ §13: корень «Настроек» — 6 строк, без единого поля ввода."""
    response = r8.client.get("/settings")
    assert response.status_code == 200, response.text
    root = lhtml.fromstring(response.text)
    links = [a.get("href") for a in root.xpath('//a[contains(@class, "u3-list-row")]')]
    assert links == [
        "/settings/llm",
        "/settings/languages",
        "/settings/prompts",
        "/settings/appearance",
        "/settings/maintenance",
        "/settings/about",
    ]
    # 0 input/select/textarea в контенте (no-JS — строки, нативные ссылки)
    assert not root.xpath("//input | //select | //textarea")


# ------------------------------------------------------------------ LLM-форма


def test_r8_2_llm_form_render(r8: R8Ctx) -> None:
    """ТЗ §13: 3 <details> (открыт ПЕРВЫЙ), поля профиля, ключ только
    «задан/не задан», границы из схемы, data-saved у extra_body."""
    response = r8.client.get("/settings/llm")
    assert response.status_code == 200, response.text
    root = lhtml.fromstring(response.text)
    details = root.xpath('//details[contains(@class, "u3-llm-profile")]')
    assert len(details) == 3
    assert "open" in details[0].attrib
    assert "open" not in details[1].attrib
    assert "open" not in details[2].attrib
    # переключатель: все три base_url равны — checked (вычисляет сервер)
    shared = root.xpath('//input[@name="shared_endpoint"]')[0]
    assert "checked" in shared.attrib
    # ключ: «задан» (LLM_API_KEY в .env), значение НИКОГДА не отображается
    key_input = root.xpath('//input[@name="api_key_translation"]')[0]
    assert key_input.get("placeholder") == UI_STRINGS["models_key_set"]
    assert LLM_KEY not in response.text
    # числовые поля: границы из схемы LLMProfile (pydantic ge/le)
    temp = root.xpath('//input[@name="temperature_translation"]')[0]
    assert (temp.get("min"), temp.get("max"), temp.get("step")) == ("0", "2.0", "0.1")
    assert root.xpath('//input[@name="retry_times_translation"]')[0].get("min") == "0"
    assert root.xpath('//input[@name="concurrency_translation"]')[0].get("min") == "1"
    # extra_body: сохранённое значение в поле + data-saved у «Сброс»
    textarea = root.xpath('//textarea[@name="extra_body_translation"]')[0]
    assert json.loads(textarea.text_content() or "{}") == {
        "chat_template_kwargs": {"enable_thinking": False}
    }
    reset = root.xpath('//button[@data-u3-llm-extra-reset="translation"]')[0]
    assert reset.get("data-saved") == json.dumps(
        {"chat_template_kwargs": {"enable_thinking": False}}, ensure_ascii=False
    )


def test_r8_3_put_valid_writes_disk_and_state(r8: R8Ctx) -> None:
    """Р8-решение 5: запись — диск (комментарии сохранены, extra_body —
    новый блок), .env, подмена app.state; секретов в ответе 0."""
    client = r8.client
    body = _baseline_body(r8)
    for name in _PROFILES:
        body["profiles"][name]["model"] = "new-model-1"
    body["profiles"]["translation"].update(
        temperature=0.3,
        retry_times=5,
        concurrency=2,
        extra_body=json.dumps({"nested": {"deep": True}}),
    )
    env_before = r8.env_file.read_text(encoding="utf-8")
    try:
        response = client.put("/api/config/llm", json=body)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["saved"] is True
        assert [p["model"] for p in data["profiles"]] == ["new-model-1"] * 3
        cfg_text = r8.cfg.read_text(encoding="utf-8")
        assert cfg_text.count("model: new-model-1") == 3
        # комментарии (inline и строки) — на месте
        assert "# основной эндпоинт" in cfg_text
        assert "# LLM-профили: одна настройка на все стадии" in cfg_text
        assert "# температура" in cfg_text
        # скаляры заменены на месте
        assert "temperature: 0.3" in cfg_text
        assert "retry_times: 5" in cfg_text
        assert "concurrency: 2" in cfg_text
        # extra_body — замена целого вложенного блока
        assert "deep: true" in cfg_text
        assert "enable_thinking" not in cfg_text
        # ключ «» = сохранение текущего: .env побайтово не тронут
        assert r8.env_file.read_text(encoding="utf-8") == env_before
        # U1-8: секретов в ответе 0
        assert LLM_KEY not in response.text
        assert AUTH_SECRET not in response.text
        # app.state подменён: следующий рендер — новые значения
        page = lhtml.fromstring(client.get("/settings/llm").text)
        value = page.xpath('//input[@id="u3-llm-model-translation"]')[0].get("value")
        assert value == "new-model-1"
    finally:
        r8.restore()


def test_r8_4_put_invalid_400_nothing_written(r8: R8Ctx) -> None:
    """Fail-closed: каждое несоответствие — 400, на диске ничего не
    поменялось (проверяется после КАЖДОГО случая)."""
    client = r8.client
    cfg_before = r8.cfg.read_bytes()
    env_before = r8.env_file.read_bytes()
    cases: list[tuple[str, str, Callable[[dict[str, Any]], None]]] = [
        ("ftp-схема", "некорректный эндпоинт",
         lambda b: b.update(base_url="ftp://x/v1")),
        ("путь-не-/v1", "некорректный эндпоинт",
         lambda b: b.update(base_url="http://127.0.0.1:1/v2")),
        ("температура-3", "температура: число 0–2",
         lambda b: b["profiles"]["translation"].update(temperature=3.0)),
        ("температура-str", "температура: число 0–2",
         lambda b: b["profiles"]["translation"].update(temperature="abc")),
        ("повторы--1", "повторы: целое ≥ 0",
         lambda b: b["profiles"]["fill"].update(retry_times=-1)),
        ("параллелизм-0", "параллелизм: целое ≥ 1",
         lambda b: b["profiles"]["analysis"].update(concurrency=0)),
        ("extra-не-json", "extra_body: JSON-объект",
         lambda b: b["profiles"]["translation"].update(extra_body="не-json")),
        ("extra-список", "extra_body: JSON-объект",
         lambda b: b["profiles"]["translation"].update(extra_body="[1,2]")),
        ("профиль-отсутствует", "неизвестный профиль: analysis",
         lambda b: b["profiles"].pop("analysis")),
    ]

    def key_conflict(b: dict[str, Any]) -> None:
        b["profiles"]["translation"]["api_key"] = "key-a"
        b["profiles"]["fill"]["api_key"] = "key-b"

    cases.append(("ключи-различаются", "один ключ API", key_conflict))

    try:
        for name, expect, mutate in cases:
            body = _baseline_body(r8)
            mutate(body)
            response = client.put("/api/config/llm", json=body)
            assert response.status_code == 400, (name, response.status_code, response.text)
            assert expect in response.json()["detail"], (name, response.text)
            assert r8.cfg.read_bytes() == cfg_before, f"{name}: config.yaml тронут"
            assert r8.env_file.read_bytes() == env_before, f"{name}: .env тронут"
    finally:
        r8.restore()


def test_r8_5_put_empty_key_kept(r8: R8Ctx) -> None:
    """api_key «» = сохранить текущий: .env — только заданный ключ,
    LLM_ANALYSIS_API_KEY при «» не создаётся."""
    client = r8.client
    body = _baseline_body(r8)
    body["profiles"]["translation"]["api_key"] = "brand-new-key-123"
    for name in _PROFILES:
        body["profiles"][name]["model"] = "kept-model"
    try:
        response = client.put("/api/config/llm", json=body)
        assert response.status_code == 200, response.text
        env_text = r8.env_file.read_text(encoding="utf-8")
        assert "LLM_API_KEY=brand-new-key-123" in env_text
        assert "LLM_ANALYSIS_API_KEY" not in env_text
        cfg_text = r8.cfg.read_text(encoding="utf-8")
        assert cfg_text.count("model: kept-model") == 3
    finally:
        r8.restore()


def test_r8_6_shared_toggle(r8: R8Ctx) -> None:
    """ТЗ §13: вкл — одно поле base_url пишется во все три профиля;
    выкл — по-профильные. checked — вычисляет сервер из состояния."""
    client = r8.client
    try:
        # вкл: общий эндпоинт
        body = _baseline_body(r8)
        body["base_url"] = "http://127.0.0.1:9999/v1"
        response = client.put("/api/config/llm", json=body)
        assert response.status_code == 200, response.text
        cfg_text = r8.cfg.read_text(encoding="utf-8")
        assert cfg_text.count("base_url: http://127.0.0.1:9999/v1") == 3
        page = lhtml.fromstring(client.get("/settings/llm").text)
        assert "checked" in page.xpath('//input[@name="shared_endpoint"]')[0].attrib
        # выкл: по-профильные
        body = _baseline_body(r8)
        body["shared_endpoint"] = False
        urls = {
            "translation": "http://127.0.0.1:10001/v1",
            "fill": "http://127.0.0.1:10002/v1",
            "analysis": "http://127.0.0.1:10003/v1",
        }
        for name, url in urls.items():
            body["profiles"][name]["base_url"] = url
        response = client.put("/api/config/llm", json=body)
        assert response.status_code == 200, response.text
        cfg_text = r8.cfg.read_text(encoding="utf-8")
        for url in urls.values():
            assert url in cfg_text, url
        page = lhtml.fromstring(client.get("/settings/llm").text)
        assert "checked" not in page.xpath('//input[@name="shared_endpoint"]')[0].attrib
    finally:
        r8.restore()


# ------------------------------------------------------------------ проба


def test_r8_7_probe(r8: R8Ctx) -> None:
    """ТЗ §13: два шага (GET /models + chat на 8 токенов); результат —
    одна строка; проба НЕ пишет конфиг, не создаёт задание."""
    client = r8.client
    cfg_before = r8.cfg.read_bytes()
    body = {
        "profile": "translation",
        "base_url": r8.server.url,
        "model": "test-model",
        "temperature": 0.7,
    }
    # ok: mock-LLM (модель эхо, токены из usage)
    response = client.post("/api/llm/probe", json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert "модель test-model" in data["message"]
    assert "токенов" in data["message"]
    # пустой extra_body в теле = СОХРАНЁННЫЙ (passthrough в запрос)
    last = r8.server.requests_log()[-1]
    assert last["extra"] == {"chat_template_kwargs": {"enable_thinking": False}}
    # фиксированные отказы: 401, 404, неверный JSON
    for status, expect in (
        (401, UI_STRINGS["llm_probe_http_401"]),
        (404, UI_STRINGS["llm_probe_http_404"]),
    ):
        httpd = _fixed_server(status, b'{"error": "denied"}')
        try:
            bad_url = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
            response = client.post("/api/llm/probe", json={**body, "base_url": bad_url})
            assert response.status_code == 200
            assert response.json()["ok"] is False
            assert response.json()["message"] == expect
        finally:
            httpd.shutdown()
    httpd = _fixed_server(200, b"not-json")
    try:
        bad_url = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
        response = client.post("/api/llm/probe", json={**body, "base_url": bad_url})
        assert response.json()["ok"] is False
        assert response.json()["message"] == UI_STRINGS["llm_probe_bad_json"]
    finally:
        httpd.shutdown()
    # закрытый порт: недоступно (ConnectionError)
    response = client.post("/api/llm/probe", json={**body, "base_url": "http://127.0.0.1:1/v1"})
    data = response.json()
    assert data["ok"] is False
    assert "недоступно" in data["message"]
    # невалидное тело — 400
    for bad, expect in (
        ({**body, "profile": "bogus"}, "неизвестный профиль: bogus"),
        ({**body, "base_url": "ftp://x/v1"}, "некорректный эндпоинт"),
        ({**body, "model": "  "}, "модель: непустая строка"),
        ({**body, "temperature": 5.0}, "температура: число 0–2"),
    ):
        response = client.post("/api/llm/probe", json=bad)
        assert response.status_code == 400, (expect, response.text)
        assert expect in response.json()["detail"]
    # конфиг не тронут (проба не пишет)
    assert r8.cfg.read_bytes() == cfg_before


def test_r8_8_models_endpoint(r8: R8Ctx) -> None:
    """Р8-решение 4: серверный прокси GET {base_url}/models для datalist.
    400 — невалидный URL; 502 — недоступен."""
    client = r8.client
    response = client.get("/api/llm/models", params={"base_url": r8.server.url})
    assert response.status_code == 200, response.text
    assert response.json()["models"] == ["probe-model"]
    response = client.get("/api/llm/models", params={"base_url": "ftp://x/v1"})
    assert response.status_code == 400
    assert "некорректный эндпоинт" in response.json()["detail"]
    response = client.get("/api/llm/models", params={"base_url": "http://127.0.0.1:1/v1"})
    assert response.status_code == 502
    assert "недоступно" in response.json()["detail"]


# ------------------------------------------------------------------ no-JS


def test_r8_9_nojs_save(r8: R8Ctx) -> None:
    """no-JS: нативная POST-форма → 303 + конфиг обновлён; 400 — ввод
    сохранён в форме, файлы не тронуты."""
    client = r8.client
    try:
        response = client.post(
            "/settings/llm",
            data=_llm_form_data(r8, model="nojs-model", temperature="0.5"),
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text
        assert response.headers["location"] == "/settings/llm"
        cfg_text = r8.cfg.read_text(encoding="utf-8")
        assert cfg_text.count("model: nojs-model") == 3
        page = lhtml.fromstring(client.get("/settings/llm").text)
        value = page.xpath('//input[@name="model_translation"]')[0].get("value")
        assert value == "nojs-model"
        # 400: температура «abc» — форма с вводом пользователя
        cfg_before = r8.cfg.read_bytes()
        bad = _llm_form_data(r8, model="kept-model-2", temperature="0.5")
        bad["temperature_translation"] = "abc"
        response = client.post("/settings/llm", data=bad)
        assert response.status_code == 400
        assert UI_STRINGS["err_llm_bad_temperature"] in response.text
        page = lhtml.fromstring(response.text)
        value = page.xpath('//input[@name="model_translation"]')[0].get("value")
        assert value == "kept-model-2"
        assert r8.cfg.read_bytes() == cfg_before
    finally:
        r8.restore()


def test_r8_10_nojs_probe(r8: R8Ctx) -> None:
    """no-JS: submit-кнопка формы (formaction) → 200 + баннер результата
    (u3-ok / u3-warn); неизвестный профиль — 400."""
    client = r8.client
    data = _llm_form_data(r8, model="test-model", temperature="0.7")
    data["profile"] = "translation"
    response = client.post("/settings/llm/probe", data=data)
    assert response.status_code == 200, response.text
    banner = lhtml.fromstring(response.text).xpath('//p[@id="u3-llm-probe-msg"]')[0]
    assert "hidden" not in banner.attrib
    assert "u3-ok" in (banner.get("class") or "")
    assert "модель test-model" in banner.text_content()
    assert "токенов" in banner.text_content()
    # отказ: закрытый порт
    bad = _llm_form_data(r8, model="test-model", temperature="0.7")
    bad["profile"] = "translation"
    bad["base_url"] = "http://127.0.0.1:1/v1"
    response = client.post("/settings/llm/probe", data=bad)
    assert response.status_code == 200
    banner = lhtml.fromstring(response.text).xpath('//p[@id="u3-llm-probe-msg"]')[0]
    assert "u3-warn" in (banner.get("class") or "")
    assert "недоступно" in banner.text_content()
    # неизвестный профиль — 400
    bad["profile"] = "bogus"
    response = client.post("/settings/llm/probe", data=bad)
    assert response.status_code == 400
    assert "неизвестный профиль: bogus" in response.text


def test_r8_11_appearance_cookies(r8: R8Ctx) -> None:
    """Р8-решение 10: тема/плотность — cookies (отсутствуют = системная/
    обычная); отметка радио — из cookies."""
    client = r8.client
    csrf = csrf_token(r8.profile.app.state.settings, "any-user")
    response = client.post(
        "/settings/appearance",
        data={"theme": "dark", "density": "compact", "u3_csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/settings/appearance"
    page = lhtml.fromstring(client.get("/settings/appearance").text)
    assert {i.get("value") for i in page.xpath('//input[@name="theme"][@checked]')} == {"dark"}
    assert {i.get("value") for i in page.xpath('//input[@name="density"][@checked]')} == {
        "compact"
    }
    # обратно к умолчаниям — cookies удалены
    response = client.post(
        "/settings/appearance",
        data={"theme": "system", "density": "normal", "u3_csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = lhtml.fromstring(client.get("/settings/appearance").text)
    assert {i.get("value") for i in page.xpath('//input[@name="theme"][@checked]')} == {
        "system"
    }
    assert {i.get("value") for i in page.xpath('//input[@name="density"][@checked]')} == {
        "normal"
    }
    assert client.cookies.get("u3-theme") is None
    assert client.cookies.get("u3-density") is None


def _purge_nojs(r8: R8Ctx, csrf: str, book: str) -> None:
    """r8_12: no-JS-страница — два шага, чужой токен — 409."""
    client = r8.client
    profile = r8.profile
    now2 = _make_terminal_job(r8, book, old_days=None)
    old2 = _make_terminal_job(r8, book, old_days=2.0)
    response = client.post(
        "/settings/maintenance/purge", data={"days": "0", "u3_csrf": csrf}
    )
    assert response.status_code == 200, response.text
    assert now2 in response.text and old2 in response.text
    page = lhtml.fromstring(response.text)
    token = page.xpath('//input[@name="confirm_token"]')[0].get("value") or ""
    assert token
    # чужой токен — 409, ничего не удалено
    response = client.post(
        "/settings/maintenance/purge",
        data={"days": "0", "confirm_token": "bogus", "u3_csrf": csrf},
    )
    assert response.status_code == 409
    assert UI_STRINGS["err_purge_token"] in response.text
    assert profile.repo.get(now2) is not None
    # верный токен — 303, удалены
    response = client.post(
        "/settings/maintenance/purge",
        data={"days": "0", "confirm_token": token, "u3_csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert profile.repo.get(now2) is None and profile.repo.get(old2) is None
    # невалидные days на странице — 400
    response = client.post(
        "/settings/maintenance/purge", data={"days": "abc", "u3_csrf": csrf}
    )
    assert response.status_code == 400
    assert UI_STRINGS["err_purge_bad_days"] in response.text
    # по умолчанию без кандидатов — 409 no-match
    response = client.post(
        "/settings/maintenance/purge", data={"days": "", "u3_csrf": csrf}
    )
    assert response.status_code == 409
    assert "нет заданий, соответствующих фильтру" in response.text


def test_r8_12_purge_days(r8: R8Ctx, u1_env: U1Env) -> None:
    """R8 (ТЗ §13 «Обслуживание»): days — свой N (API + no-JS-страница);
    два шага (confirm_token); только терминальные старше N."""
    client = r8.client
    profile = r8.profile
    csrf = csrf_token(profile.app.state.settings, "any-user")
    book = _upload_book(client, u1_env.books["tiny"])
    now_id = _make_terminal_job(r8, book, old_days=None)
    old_id = _make_terminal_job(r8, book, old_days=2.0)
    ids = sorted([now_id, old_id])
    # days=9999 — нет совпадений
    response = client.post("/api/jobs/purge", json={"days": 9999})
    assert response.status_code == 404
    assert "нет заданий, соответствующих фильтру" in response.json()["detail"]
    # невалидные days — 400
    for bad_days in (-1, True, "abc"):
        response = client.post("/api/jobs/purge", json={"days": bad_days})
        assert response.status_code == 400
        assert UI_STRINGS["err_purge_bad_days"] in response.json()["detail"]
    # days=0 — все терминальные
    response = client.post("/api/jobs/purge", json={"days": 0})
    assert response.status_code == 200, response.text
    dry = response.json()
    assert dry["dry_run"] is True
    assert dry["job_ids"] == ids
    assert dry["min_age_days"] == 0
    token0 = dry["confirm_token"]
    # без days — конфиг-дефолт (ui.purge_min_age_days = 1): только старое
    response = client.post("/api/jobs/purge", json={})
    assert response.status_code == 200, response.text
    dry = response.json()
    assert dry["job_ids"] == [old_id]
    assert dry["min_age_days"] == 1.0
    # шаг 2 (токен состава days=0) — удалены ровно оба
    response = client.post("/api/jobs/purge", json={"days": 0, "confirm_token": token0})
    assert response.status_code == 200, response.text
    done = response.json()
    assert done["dry_run"] is False
    assert done["purged"] == ids
    for job_id in ids:
        assert profile.repo.get(job_id) is None
    # ---- no-JS-страница (два шага с серверным подтверждением) ----
    _purge_nojs(r8, csrf, book)


# ------------------------------------------------------------------ обслуживание


def test_r8_13_logs_clear(r8: R8Ctx) -> None:
    """R8-решение 8б: тот же механизм, что per-job — truncate(0) +
    строка факта; ответ {cleared, bytes}."""
    st = r8.profile.app.state.settings
    log_path = st.logs_dir / "app.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_bytes(b"x" * 512)
    response = r8.client.post("/api/logs/clear")
    assert response.status_code == 200, response.text
    assert response.json() == {"cleared": True, "bytes": 512}
    content = log_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    assert len(lines) == 1
    assert "логи очищены" in lines[0]
    assert "512 Б" in lines[0]
    # повторный вызов — bytes = размер строки факта
    response = r8.client.post("/api/logs/clear")
    assert response.json()["bytes"] == len(content.encode("utf-8"))


def test_r8_14_about_readme_logout(r8: R8Ctx) -> None:
    """Р8-решение 9: версия из pyproject, коммит git (fail-safe),
    /readme, /logout (303 + cookie удалён)."""
    client = r8.client
    st = r8.profile.app.state.settings
    pyproject = st.storage.root / "pyproject.toml"
    readme = st.storage.root / "README.md"
    pyproject.write_text('version = "0.9.9-test"\n', encoding="utf-8")
    try:
        page = client.get("/settings/about")
        assert page.status_code == 200, page.text
        assert "0.9.9-test" in page.text
        # в tmp-корне нет .git — fail-safe «неизвестно»
        assert UI_STRINGS["about_unknown"] in page.text
        # readme: нет файла → «не найден»
        page = client.get("/readme")
        assert page.status_code == 200
        assert UI_STRINGS["about_readme_missing"] in page.text
        readme.write_text("# R8 readme\n", encoding="utf-8")
        try:
            page = client.get("/readme")
            assert page.status_code == 200
            assert "R8 readme" in page.text
        finally:
            readme.unlink()
    finally:
        pyproject.unlink()
    # выход: 303 /login + удаление cookie-логина (Set-Cookie, Max-Age=0).
    # Клиент u1 аутентифицирован хедерами (Basic), поэтому эффект — в
    # заголовке Set-Cookie, а не в jar.
    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    set_cookie = response.headers.get_list("set-cookie")
    assert any(
        c.startswith(LOGIN_COOKIE + "=") and "Max-Age=0" in c for c in set_cookie
    ), set_cookie


# ------------------------------------------------------------------ воркер


def test_r8_15_worker_reload(r8: R8Ctx) -> None:
    """Р8-решение 6: воркер релоадит конфиг перед каждым новым заданием;
    битый YAML — прежняя конфигурация (не падает)."""
    profile = r8.profile
    worker = Worker(
        profile.app.state.settings,
        profile.repo,
        config_path=r8.cfg,
        env_path=r8.env_file,
    )
    try:
        before = worker._settings
        text = r8.cfg.read_text(encoding="utf-8")
        r8.cfg.write_text(
            text.replace("model: test-model", "model: reloaded-model", 1),
            encoding="utf-8",
        )
        worker._reload_config()
        assert worker._settings is not before
        assert worker._settings.llm.profile("translation").model == "reloaded-model"
        # битый YAML: сохранена последняя валидная
        good = worker._settings
        r8.cfg.write_text("storage: [незакрытый\n  root: x\n", encoding="utf-8")
        worker._reload_config()
        assert worker._settings is good
    finally:
        r8.restore()


# ------------------------------------------------------------------ «Ещё» мастера


def test_r8_16_create_sheet_llm_row(r8: R8Ctx) -> None:
    """R4-плейсхолдер (решение 12): строка LLM-профиля — одна модель,
    если у всех профилей одна, иначе «разные»."""
    client = r8.client
    page = client.get("/jobs?sheet=create")
    assert page.status_code == 200, page.text
    assert UI_STRINGS["create_more_llm"] in page.text
    assert "test-model" in page.text
    # разные модели — смешанный маркер
    body = _baseline_body(r8)
    body["shared_endpoint"] = False
    models = {"translation": "model-a", "fill": "model-b", "analysis": "model-c"}
    for name, model in models.items():
        body["profiles"][name]["model"] = model
    try:
        response = client.put("/api/config/llm", json=body)
        assert response.status_code == 200, response.text
        page = client.get("/jobs?sheet=create")
        assert UI_STRINGS["create_llm_mixed"] in page.text
    finally:
        r8.restore()


# ------------------------------------------------------------------ обслуживание


def test_r8_17_maintenance_worker_status_class(
    r8: R8Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Найдено замером T11: worker_alive отдаёт «ok (pid …)» — строка
    здорового воркера рендерилась в красном u3-err (строгое
    ``== "ok"``). startswith("ok") — u3-ok, текст остаётся
    информативным."""
    report = {
        "app": "ok",
        "database": "ok",
        "storage": "ok",
        "worker": "ok (pid 4242)",
        "llm": dict.fromkeys(_PROFILES, "ok"),
        "epubcheck": "ok",
        "converter": "ok",
        "nlp": {},
    }
    monkeypatch.setattr(views, "health_report", lambda _s, _r: report)
    page = r8.client.get("/settings/maintenance")
    assert page.status_code == 200, page.text
    tree = lhtml.fromstring(page.text)
    by_name: dict[str, Any] = {}
    for row in tree.xpath(".//div[contains(@class, 'u3-maint-row')]"):
        names = row.xpath(".//span[contains(@class, 'u3-maint-name')]")
        if names:
            by_name[names[0].text_content().strip()] = row
    worker = by_name.get(UI_STRINGS["maint_status_worker"])
    assert worker is not None, "строка «Воркер» не найдена"
    ok_spans = worker.xpath(".//span[contains(@class, 'u3-ok')]")
    assert ok_spans, f"«ok (pid …)» должно рендериться u3-ok: {worker}"
    assert "4242" in ok_spans[0].text_content()
    llm_row = by_name.get(UI_STRINGS["maint_status_llm"])
    assert llm_row is not None, "строка «LLM» не найдена"
    assert llm_row.xpath(".//span[contains(@class, 'u3-ok')]"), "LLM ok — u3-ok"
