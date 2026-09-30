"""/chat.html в настоящем Chromium против настоящего сервера и поддельной потоковой модели (RUN_20261001_CHAT).

Регрессии, которые нельзя поймать чистой логикой: консоль без 404, отключённый облачный агент с причиной,
заметка STOP снимается, `<think>` из итога не виден и ответ не дублируется, значок LOCAL после перезагрузки.
Поддельная модель — локальный OpenAI-совместимый сервер в этом же процессе (не выдаётся за модель).
CHAT_UX_UI_DIR — каталог другой версии `ui/` (доказательство «красный на старом коде»).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from bcc.app import create_app
from bcc.auth import HEADER
from bcc.config import Settings

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import LiveServer, _free_port, _launch

UI_DIR = Path(os.environ.get("CHAT_UX_UI_DIR") or Path(__file__).resolve().parents[1] / "ui")
pytestmark = [pytest.mark.timeout(240), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


# --------------------------------------------------------------- поддельная потоковая модель

def _parts(prompt: str) -> list[tuple[str, str, float]]:
    if "THINK" in prompt:
        return ([("reasoning_content", f"СЕКРЕТНОЕ-РАССУЖДЕНИЕ {i}. ", 0.02) for i in range(4)]
                + [("content", "<think>скрытая цепочка думай-тег</think>", 0.02),
                   ("content", "Открытый ответ без рассуждений. " * 2, 0.02)])
    m = re.search(r"SLOW(\d+)", prompt)
    if m:
        return [("content", f"слово{i} ", 0.1) for i in range(int(m.group(1)))]
    return [("content", "Привет из заглушки. ", 0.02)]


class _Fake(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a) -> None:  # noqa: D102
        pass

    def _send_json(self, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._send_json({"object": "list", "data": [{"id": "fake-stream", "object": "model"}]})

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        user = ""
        for m in reversed(body.get("messages") or []):
            if m.get("role") == "user":
                c = m.get("content")
                user = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                break
        user = user.split("Новое сообщение владельца:")[-1]
        parts = _parts(user)
        if not body.get("stream"):
            text = "".join(t for k, t, _ in parts if k == "content")
            self._send_json({"id": "x", "object": "chat.completion", "model": "fake-stream",
                             "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def write(obj) -> None:
            data = ("data: " + (obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)) + "\n\n").encode()
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()

        try:
            for key, text, delay in parts:
                write({"id": "x", "object": "chat.completion.chunk", "model": "fake-stream",
                       "choices": [{"index": 0, "delta": {key: text}, "finish_reason": None}]})
                time.sleep(delay)
            write({"id": "x", "object": "chat.completion.chunk", "model": "fake-stream",
                   "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            write({"id": "x", "object": "chat.completion.chunk", "model": "fake-stream", "choices": [],
                   "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}})
            write("[DONE]")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except OSError:
            pass


@pytest.fixture
def fake_llm():
    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Fake)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        srv.shutdown()


class ChatServer(LiveServer):
    """LiveServer с живыми worker-циклами (ходы чата должны выполняться) и выбранным каталогом ui."""

    def __init__(self, tmp_path: Path) -> None:
        import uvicorn
        data = tmp_path / "data"
        self.settings = Settings(data_dir=data, database_url=f"sqlite+aiosqlite:///{data / 'bcc.db'}", ui_dir=UI_DIR)
        self.app = create_app(self.settings, announce_token=False)
        self.svc = self.app.state.svc
        self.port = _free_port()
        self.loop = asyncio.new_event_loop()
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning",
                                                    loop="none", timeout_graceful_shutdown=1))
        self.thread = threading.Thread(target=self._run, daemon=True)


@pytest.fixture
def chat(tmp_path, fake_llm):
    srv = ChatServer(tmp_path).start()
    with httpx.Client(base_url=srv.url, headers={HEADER: srv.svc.auth.token}, trust_env=False, timeout=30) as api:
        provider = api.post("/api/providers", json={"name": "заглушка", "kind": "openai_compat", "base_url": fake_llm}).json()
        model = api.post("/api/models", json={"provider_id": provider["id"], "name": "fake-stream", "alias": "fake-stream",
                                              "kind": "local", "context_window": 8192}).json()
        agent = api.post("/api/agents", json={"name": "Заглушка", "model_id": model["id"], "tools": [], "max_steps": 3,
                                              "max_tokens": 2048}).json()
        cloud_provider = api.post("/api/providers", json={"name": "облако", "kind": "openai_compat",
                                                          "base_url": "https://api.cloud-provider.example/v1"}).json()
        cloud_model = api.post("/api/models", json={"provider_id": cloud_provider["id"], "name": "cloud-x", "alias": "cloud-x",
                                                    "kind": "cloud"}).json()
        api.post("/api/agents", json={"name": "Облако без цены", "model_id": cloud_model["id"], "tools": []})
        yield srv, agent
    srv.stop()


def _open(pw, srv, errors: list[str]):
    browser = _launch(pw)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: errors.append(f"{m.type}: {m.text} @ {(m.location or {}).get('url', '')}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto(srv.url + "/chat.html", wait_until="domcontentloaded")
    page.fill("#chat-login-token", srv.svc.auth.token)
    page.click("#chat-login-submit")
    page.wait_for_selector("#chat-app:not([hidden])", timeout=15000)
    page.wait_for_timeout(800)
    return browser, page


def _pick(page, name: str) -> None:
    page.click("#chat-picker")
    page.wait_for_selector("#chat-picker-menu")
    page.click(f"#chat-picker-menu [role=option]:has-text('{name}')")


def _send(page, text: str) -> None:
    page.fill("#chat-input", text)
    page.press("#chat-input", "Enter")


def _wait_finished(page, timeout_s: float = 60) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if page.evaluate("(() => { const b = [...document.querySelectorAll('.msg-bot')].pop(); return !!b && b.dataset.live === '0'; })()"):
            page.wait_for_timeout(2200)        # строка состояния и подвал доживают до «Готово»
            return
        page.wait_for_timeout(200)
    raise AssertionError("the turn did not finish")


def test_chat_console_is_clean_and_an_unpriced_cloud_agent_is_disabled_with_a_reason(chat):
    from playwright.sync_api import sync_playwright
    srv, _agent = chat
    errors: list[str] = []
    with sync_playwright() as pw:
        browser, page = _open(pw, srv, errors)
        page.click("#chat-picker")
        page.wait_for_selector("#chat-picker-menu")
        opt = page.locator("#chat-picker-menu [role=option]:has-text('Облако без цены')")
        assert opt.get_attribute("aria-disabled") == "true", "an unpriced cloud agent must not be selectable"
        assert "цен" in (opt.get_attribute("title") or "").lower(), opt.get_attribute("title")
        ok = page.locator("#chat-picker-menu [role=option]:has-text('Заглушка')")
        assert ok.get_attribute("aria-disabled") == "false"
        browser.close()
    assert errors == [], errors


def test_stop_note_is_cleared_when_the_engine_confirms_the_stop(chat):
    from playwright.sync_api import sync_playwright
    srv, _agent = chat
    errors: list[str] = []
    with sync_playwright() as pw:
        browser, page = _open(pw, srv, errors)
        _pick(page, "Заглушка")
        _send(page, "SLOW200 длинный ответ")
        # строковые предикаты page.wait_for_function запрещены CSP страницы — ждём опросом
        deadline = time.time() + 30
        while time.time() < deadline and page.evaluate("(document.querySelector('.msg-bot .md') || {innerText: ''}).innerText.length") < 40:
            page.wait_for_timeout(200)
        page.focus("#chat-input")
        page.keyboard.press("Escape")
        deadline = time.time() + 30
        while time.time() < deadline and "Остановлено владельцем" not in page.evaluate("(document.querySelector('.msg-bot .bot-status') || {innerText: ''}).innerText"):
            page.wait_for_timeout(200)
        page.wait_for_timeout(3500)
        assert "Остановлено" in page.evaluate("document.querySelector('#chat-statusbar').innerText")
        assert page.evaluate("document.querySelector('.cmp-note').innerText") == "", \
            "the «STOP отправлен — жду подтверждения» note must not outlive «Остановлено»"
        browser.close()
    assert errors == [], errors


def test_think_block_is_hidden_answer_is_not_duplicated_and_local_badge_survives_reload(chat):
    from playwright.sync_api import sync_playwright
    srv, _agent = chat
    errors: list[str] = []
    with sync_playwright() as pw:
        browser, page = _open(pw, srv, errors)
        _pick(page, "Заглушка")
        _send(page, "THINK расскажи что-нибудь")
        _wait_finished(page)
        segs = page.evaluate("[...document.querySelectorAll('.msg-bot .bot-segs > *')].map(e => e.innerText)")
        body = page.evaluate("document.body.innerText")
        assert len(segs) == 1, f"the answer must appear once, got {segs}"
        assert "думай-тег" not in body and "<think" not in body and "СЕКРЕТНОЕ" not in body, body[:500]
        page.reload()
        page.wait_for_selector("#chat-app:not([hidden])", timeout=15000)
        page.wait_for_timeout(2500)
        foot = page.evaluate("(document.querySelector('.msg-bot .bot-foot') || {innerText: ''}).innerText")
        assert "LOCAL" in foot and "$0.00 local" in foot, f"a reopened local turn must keep its place: {foot!r}"
        assert "думай-тег" not in page.evaluate("document.body.innerText")
        browser.close()
    assert errors == [], errors
