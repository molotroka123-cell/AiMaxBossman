"""browser.active_tab_info (master prompt 28.10, task 4): real URL/title/build_sha of the active tab over CDP, read-only.

A fake CDP + Pult server runs on loopback; no real Chrome, no owner data.
"""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from bcc import browser_tab_info as bti
from bcc.features import tools_browser as tb

SHA = "a0d8150f52c0efd6e5c75c9a5c257afd819fb64e"


def _serve(tabs):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/json/list":
                body = json.dumps(tabs(self.server.server_port)).encode()
            elif self.path == "/health/live":
                body = json.dumps({"app": "bossman-command-center", "build_sha": SHA, "liveness": True}).encode()
            elif self.path == "/health":                       # like the live server: 503 while optional parts are down
                self.send_response(503); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(json.dumps({"build_sha": SHA, "status": "degraded"}).encode()); return
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def cdp(monkeypatch):
    made = []

    def start(tabs):
        srv = _serve(tabs); made.append(srv)
        monkeypatch.setenv("BOSSMAN_CDP_URL", f"http://127.0.0.1:{srv.server_port}")
        return srv
    yield start
    for s in made:
        s.shutdown()


def test_picks_the_first_real_page_and_strips_secrets(cdp):
    cdp(lambda port: [
        {"type": "service_worker", "url": f"http://127.0.0.1:{port}/sw.js", "title": "sw", "id": "0"},
        {"type": "page", "url": "devtools://devtools/bundled/inspector.html", "title": "DevTools", "id": "1"},
        {"type": "page", "url": "chrome-extension://abc/popup.html", "title": "ext", "id": "2"},
        {"type": "page", "url": f"http://user:pw@127.0.0.1:{port}/m/chat?token=SECRET#frag-secret", "title": "Пульт\x00\x1b[31m" + "x" * 500, "id": "3"},
        {"type": "page", "url": "https://example.com/other", "title": "second", "id": "4"},
    ])
    info = bti.active_tab_info()
    assert info["url"].endswith("/m/chat") and "SECRET" not in info["url"] and "pw" not in info["url"] and "#" not in info["url"]
    assert "\x00" not in info["title"] and "\x1b" not in info["title"] and len(info["title"]) <= 200
    assert info["build_sha"] == SHA
    assert info["tabs_open"] == 4          # 2 pages + devtools + extension counted as pages, service_worker not


def test_build_sha_is_only_read_from_loopback_origins(cdp, monkeypatch):
    cdp(lambda port: [{"type": "page", "url": "https://example.com/health", "title": "ext", "id": "1"}])
    assert bti.active_tab_info()["build_sha"] is None
    assert bti.build_sha_of("http://192.168.1.5:8801/") is None


def test_non_loopback_cdp_endpoint_is_refused(monkeypatch):
    monkeypatch.setenv("BOSSMAN_CDP_URL", "http://169.254.169.254:9222")
    with pytest.raises(bti.CdpUnavailable, match="loopback"):
        bti.active_tab_info()


def test_cdp_down_says_how_to_enable_it_and_never_guesses(monkeypatch):
    monkeypatch.setenv("BOSSMAN_CDP_URL", "http://127.0.0.1:9")       # nothing listens there
    with pytest.raises(bti.CdpUnavailable, match="remote-debugging-port"):
        bti.active_tab_info()


def test_no_http_tab_open(cdp):
    cdp(lambda port: [{"type": "page", "url": "about:blank", "title": "", "id": "1"}])
    with pytest.raises(bti.CdpUnavailable, match="вкладки"):
        bti.active_tab_info()


def test_tool_is_registered_read_only_and_external():
    spec = next(s for s in tb.SPECS if s.name == "browser.active_tab_info")
    assert spec.category == "read" and spec.permission == "browser.read" and spec.external_output is True
    assert spec.input_schema == {} and spec.default_effect == "auto"


def test_tool_handler_returns_data_and_a_clean_error(cdp, monkeypatch):
    cdp(lambda port: [{"type": "page", "url": f"http://127.0.0.1:{port}/", "title": "Пульт", "id": "1"}])
    ok = asyncio.run(tb._active_tab_info({}, SimpleNamespace()))
    assert not ok.error and ok.external and ok.data["build_sha"] == SHA and SHA[:12] in ok.content
    monkeypatch.setenv("BOSSMAN_CDP_URL", "http://127.0.0.1:9")
    bad = asyncio.run(tb._active_tab_info({}, SimpleNamespace()))
    assert bad.error and "remote-debugging-port" in bad.content


def test_build_sha_falls_back_to_a_503_health_body_when_live_is_missing(monkeypatch):
    calls = []

    def fake(url, timeout):
        calls.append(url)
        if url.endswith("/health/live"):
            raise OSError("no live route")
        import urllib.error, io
        raise urllib.error.HTTPError(url, 503, "down", {}, io.BytesIO(json.dumps({"build_sha": SHA}).encode()))

    monkeypatch.setattr(bti, "_get_json", fake)
    assert bti.build_sha_of("http://127.0.0.1:8801/") == SHA and calls[-1].endswith("/health")
