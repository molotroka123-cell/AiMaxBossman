"""The one test that drives the REAL Playwright/Chromium browser the product
already ships (``bcc.v2.browser_control.BrowserManager``), against a local
fake HTTP server — no real site is contacted, but the render itself is
genuine: JS on the page actually executes.

Also the test for "no fingerprint/UA spoofing": the request the real browser
makes carries Chromium's own, unmodified User-Agent — never a string this
codebase invented, and never rotated between requests.
"""
from __future__ import annotations

import pytest

from bcc.collector.browser_fetch import HumanBrowserSession
from bcc.collector.engine import CollectorConfig, run_collection

from .browser_support import chromium_available
from .collector_server import FakeServer


@pytest.fixture(autouse=True)
def _allow_private_browser_targets(monkeypatch):
    # Same override the rest of the browser test suite uses (test_feat_browser.py):
    # the product refuses loopback targets by default (SSRF hardening); our
    # test server IS loopback, on purpose, so we opt in for this test only.
    monkeypatch.setenv("BCC_BROWSER_ALLOW_PRIVATE", "1")


@pytest.fixture
def server():
    srv = FakeServer()
    srv.set_route("/robots.txt", body="User-agent: *\nAllow: /\n")
    srv.set_route("/page.html", body=(
        "<html><body><h1>AMD Ryzen AI Max+ 395</h1>"
        "<p id='out'>loading…</p>"
        "<script>document.getElementById('out').innerText="
        "'AMD Ryzen AI Max+ 395 memory bandwidth is 256 GB/s (rendered by JS).';"
        "</script></body></html>"))
    yield srv
    srv.close()


pytestmark = pytest.mark.skipif(not chromium_available(), reason="Chromium не предустановлен")


async def test_real_browser_renders_js_and_identifies_honestly(tmp_path, server):
    url = server.url("/page.html")
    session = HumanBrowserSession(tmp_path / "browser", allowed_hosts=["127.0.0.1"])
    await session.start()
    try:
        page = await session.read(url, human_pace=False)
    finally:
        await session.close()
    # The JS-set text is present: this is real rendering, not a raw-HTML parse.
    assert "256 GB/s" in page.text
    assert "rendered by JS" in page.text

    headers = server.headers_for("/page.html")
    assert headers is not None
    ua = headers.get("User-Agent", "")
    # Genuinely Playwright/Chromium's own identity: never empty, never a
    # string this codebase invented, and unmistakably a real browser UA.
    assert ua
    assert "Bossman" not in ua
    assert any(marker in ua for marker in ("Chrome", "Chromium", "HeadlessChrome"))


async def test_full_run_through_the_real_browser_produces_sourced_facts(tmp_path, server):
    sources = tmp_path / "sources.txt"
    sources.write_text(server.url("/page.html") + "\n", encoding="utf-8")
    cfg = CollectorConfig(topic="AMD Ryzen AI Max+ 395 memory bandwidth",
                          sources_path=sources, data_dir=tmp_path / "data",
                          human_pace=False)
    summary = await run_collection(cfg)
    assert summary.pages and summary.pages[0].status == "ok"
    assert any("256 GB/s" in f.value for f in summary.facts)
    for fact in summary.facts:
        assert fact.provenance.url == server.url("/page.html")
        assert len(fact.provenance.page_hash) == 64
