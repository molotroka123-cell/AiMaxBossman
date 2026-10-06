"""Browser smoke: the real Jeff window against the real Jeff web server (fake model)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _free_port, _launch, loopback_get

pytestmark = [
    pytest.mark.timeout(180),
    pytest.mark.skipif(not chromium_available(), reason=browser_reason()),
]


@pytest.fixture
def jeff_server(tmp_path):
    import uvicorn

    from bcc.pit import web

    from .test_pit_runtime import make_settings
    from .test_pit_web import RecordingAdapter

    settings = make_settings(tmp_path)
    adapter = RecordingAdapter("<think>скрытое</think>Привет! Я на связи.")

    def factory():
        runtime = web.WebParticipantRuntime(settings, Path(tmp_path) / "pit-v1.7" / "web")
        runtime.adapter = adapter
        return runtime

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(
        web.create_app(settings, port=port, runtime_factory=factory),
        host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if loopback_get(url + "/api/jeff/identity").status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.1)
    yield url
    server.should_exit = True
    thread.join(timeout=10)


def test_jeff_window_chats_for_real_and_stays_participant_safe(jeff_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            requests: list[tuple[str, str]] = []
            page.on("request", lambda r: requests.append((r.method, r.url)))
            page.goto(f"{jeff_server}/jeff.html", wait_until="domcontentloaded")
            page.wait_for_selector("#login:not([hidden])")
            assert "Создать" in page.locator("#login-submit").inner_text()
            page.fill("#login-username", "alice")
            page.fill("#login-password", "correct horse 1")
            page.click("#login-submit")
            page.wait_for_selector("#app:not([hidden])")
            page.wait_for_selector(".bubble.assistant")
            assert "Привет, я Джефф" in page.locator(".bubble.assistant").first.inner_text()

            assert page.locator(".capability.locked").is_disabled()
            # the voice confirmation bar is hidden until a doubtful transcript
            # (live finding: CSS display:flex overrode the hidden attribute)
            assert not page.is_visible("#voice-confirm")
            assert not page.is_visible("#login")
            # J4: Shift+Enter is a newline, Enter sends
            page.click("#message")
            page.keyboard.type("Как")
            page.keyboard.press("Shift+Enter")
            assert page.input_value("#message") == "Как\n"
            assert page.locator(".bubble.user").count() == 0
            page.fill("#message", "Как дела?")
            page.press("#message", "Enter")
            # the page CSP forbids eval, so waits use locators, not wait_for_function
            page.locator(".bubble.assistant:not(.pending)").nth(1).wait_for()
            last = page.locator(".bubble.assistant").last
            assert "Привет! Я на связи." in last.inner_text()
            assert "скрытое" not in page.content()
            assert last.locator("details.disclosure").count() == 1

            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector(".bubble.user")
            assert "Как дела?" in page.locator(".bubble.user").last.inner_text()

            foreign = [u for _, u in requests if not u.startswith(jeff_server)]
            assert foreign == [], foreign
            api = {u.split(jeff_server, 1)[1].split("?")[0] for _, u in requests
                   if "/api/" in u}
            assert all(path.startswith("/api/jeff/") for path in api), api
        finally:
            browser.close()


def test_jeff_window_keeps_the_whole_chat_after_f5_and_across_a_reconnect(jeff_server):
    """Owner bug (c): 25 bubbles became 6 after F5. The chat is now served from the display transcript, and a
    reconnect never redraws it from a shorter list (turns the server did not keep, e.g. while memory is paused)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"{jeff_server}/jeff.html", wait_until="domcontentloaded")
            page.wait_for_selector("#login:not([hidden])")
            page.fill("#login-username", "alice")
            page.fill("#login-password", "correct horse 1")
            page.click("#login-submit")
            page.wait_for_selector("#app:not([hidden])")
            page.locator(".bubble.assistant").first.wait_for()
            for i in range(6):
                page.fill("#message", f"вопрос номер {i}")
                page.press("#message", "Enter")
                page.locator(".bubble.assistant:not(.pending)").nth(i + 1).wait_for()
            assert page.locator(".bubble").count() == 13                   # greeting + 6 turns x 2
            page.reload(wait_until="domcontentloaded")
            page.locator(".bubble").nth(12).wait_for()
            assert page.locator(".bubble").count() == 13                   # was 12: no greeting, context window only

            # two more turns after the participant paused memory: the server keeps nothing of them
            for text in ("/pause_memory", "во время паузы А", "во время паузы Б"):
                before = page.locator(".bubble").count()
                page.fill("#message", text)
                page.press("#message", "Enter")
                page.locator(".bubble").nth(before + 1).wait_for()
                page.locator(".bubble.assistant.pending").first.wait_for(state="detached")
            shown = page.locator(".bubble").count()
            assert shown == 19
            page.context.set_offline(True)
            page.locator("#connection-status[data-tone='bad']").wait_for(timeout=20000)
            page.context.set_offline(False)
            page.locator("#connection-status[data-tone='ok']").wait_for(timeout=20000)
            page.wait_for_timeout(1500)                                    # the reconnect reload has run
            assert page.locator(".bubble").count() == shown                # not wiped down to the stored 15
        finally:
            browser.close()
