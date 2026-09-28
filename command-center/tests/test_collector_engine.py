"""End-to-end collection loop (bcc/collector/engine.py) against a local fake
HTTP server: robots.txt is fetched for real (over loopback), page content
comes from a fake browser session so the test does not need a real Chromium
install to exercise STOP/PAUSE/cap/provenance/conflict logic. The one test
that must use the real Playwright browser lives in
test_collector_browser_real.py.
"""
from __future__ import annotations

import time as time_module

import pytest

from bcc.collector import config, control as control_mod
from bcc.collector.browser_fetch import RenderedPage
from bcc.collector.engine import CollectorConfig, run_collection

from .collector_server import FakeServer


@pytest.fixture
def server():
    srv = FakeServer()
    srv.set_route("/robots.txt", body="User-agent: *\nAllow: /\n")
    yield srv
    srv.close()


@pytest.fixture(autouse=True)
def _fast_waits(monkeypatch):
    """Politeness delays are real (>=5s) by design; monkeypatch the sleep the
    control loop uses so tests stay fast while still exercising the real
    wait-time arithmetic (DomainState computes a real ~5s remaining, the
    loop just does not really block for it)."""
    monkeypatch.setattr(control_mod.time, "sleep", lambda _s: None)


class FakeSession:
    """Stands in for HumanBrowserSession: same async shape, no real browser.
    ``pages``/``fail`` are keyed by URL exactly as the engine will call
    ``read()`` — i.e. by the fake server's own URLs, so robots.txt handling
    (which IS real, against the fake server) still lines up with the host."""

    def __init__(self, data_dir=None, *, allowed_hosts=None, headless=True,
                pages=None, fail=None):
        self.pages = pages or {}
        self.fail = fail or {}
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True

    async def read(self, url: str, human_pace: bool = True) -> RenderedPage:
        if url in self.fail:
            raise self.fail[url]
        title, text = self.pages.get(url, ("", ""))
        return RenderedPage(url=url, title=title, text=text)

    async def close(self):
        self.closed = True


def _sources_file(tmp_path, urls):
    path = tmp_path / "sources.txt"
    path.write_text("\n".join(urls) + "\n", encoding="utf-8")
    return path


BANDWIDTH_TEXT_A = "AMD Ryzen AI Max+ 395 memory bandwidth is 256 GB/s according to this page."
BANDWIDTH_TEXT_B = "AMD Ryzen AI Max+ 395 memory bandwidth is 273 GB/s per this other source."
TOPIC = "AMD Ryzen AI Max+ 395 memory bandwidth"


async def test_facts_all_carry_full_provenance(tmp_path, server):
    url = server.url("/p1.html")
    session = FakeSession(pages={url: ("Page 1", BANDWIDTH_TEXT_A)})
    cfg = CollectorConfig(topic=TOPIC, sources_path=_sources_file(tmp_path, [url]),
                          data_dir=tmp_path / "data")
    summary = await run_collection(cfg, session_factory=lambda: session)
    assert summary.facts, "expected at least one fact"
    for fact in summary.facts:
        d = fact.as_dict()["provenance"]
        assert d["url"] == url
        assert d["retrieved_at_utc"]
        assert d["quote"] in BANDWIDTH_TEXT_A
        assert d["selector"].startswith("innerText#s")
        assert len(d["page_hash"]) == 64
        assert d["robots_txt_allowed"] is True
    assert session.started and session.closed


async def test_conflicting_facts_from_different_sources_are_both_kept(tmp_path, server):
    url_a, url_b = server.url("/a.html"), server.url("/b.html")
    session = FakeSession(pages={url_a: ("A", BANDWIDTH_TEXT_A), url_b: ("B", BANDWIDTH_TEXT_B)})
    cfg = CollectorConfig(topic=TOPIC, sources_path=_sources_file(tmp_path, [url_a, url_b]),
                          data_dir=tmp_path / "data", min_delay_s=5.0)
    summary = await run_collection(cfg, session_factory=lambda: session)
    values = {f.value for f in summary.facts if f.predicate == "bandwidth"}
    assert "256 GB/s" in " ".join(values) or any("256" in v for v in values)
    assert any("256" in v for v in values) and any("273" in v for v in values), (
        "both conflicting bandwidth figures must be preserved, not merged")


async def test_robots_disallow_skips_the_page(tmp_path, server):
    server.set_route("/robots.txt", body="User-agent: *\nDisallow: /blocked\n")
    url = server.url("/blocked/page.html")
    session = FakeSession(pages={url: ("Blocked", BANDWIDTH_TEXT_A)})
    cfg = CollectorConfig(topic=TOPIC, sources_path=_sources_file(tmp_path, [url]),
                          data_dir=tmp_path / "data")
    summary = await run_collection(cfg, session_factory=lambda: session)
    assert len(summary.pages) == 1
    assert summary.pages[0].status == "skipped_robots"
    assert summary.facts == []


async def test_daily_cap_skips_pages_beyond_the_cap(tmp_path, server):
    urls = [server.url(f"/p{i}.html") for i in range(3)]
    session = FakeSession(pages={u: ("T", BANDWIDTH_TEXT_A) for u in urls})
    cfg = CollectorConfig(topic=TOPIC, sources_path=_sources_file(tmp_path, urls),
                          data_dir=tmp_path / "data", daily_cap=2, min_delay_s=5.0)
    summary = await run_collection(cfg, session_factory=lambda: session)
    statuses = [p.status for p in summary.pages]
    assert statuses.count("ok") == 2
    assert statuses.count("skipped_cap") == 1


async def test_min_delay_is_recorded_for_the_second_page_same_domain(tmp_path, server):
    urls = [server.url("/p1.html"), server.url("/p2.html")]
    session = FakeSession(pages={u: ("T", BANDWIDTH_TEXT_A) for u in urls})
    cfg = CollectorConfig(topic=TOPIC, sources_path=_sources_file(tmp_path, urls),
                          data_dir=tmp_path / "data", min_delay_s=8.0)
    summary = await run_collection(cfg, session_factory=lambda: session)
    ok_pages = [p for p in summary.pages if p.status == "ok"]
    assert len(ok_pages) == 2
    assert ok_pages[0].delay_before_s == 0.0
    assert ok_pages[1].delay_before_s > 7.0     # ~8s floor minus a few ms


async def test_stop_mid_crawl_saves_partial_results(tmp_path, server):
    urls = [server.url("/p1.html"), server.url("/p2.html"), server.url("/p3.html")]
    run_id = "stop-test-run"
    run_dir = tmp_path / "data" / "runs" / run_id

    class StoppingSession(FakeSession):
        async def read(self, url, human_pace=True):
            if url == urls[1]:
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / config.STOP_FILE_NAME).write_text("owner said stop", encoding="utf-8")
            return await super().read(url, human_pace=human_pace)

    session = StoppingSession(pages={u: ("T", BANDWIDTH_TEXT_A) for u in urls})
    cfg = CollectorConfig(topic=TOPIC, sources_path=_sources_file(tmp_path, urls),
                          data_dir=tmp_path / "data", min_delay_s=5.0, run_id=run_id)
    summary = await run_collection(cfg, session_factory=lambda: session)
    urls_seen = [p.url for p in summary.pages]
    assert urls[2] not in urls_seen, "STOP must prevent the third page from being visited"
    assert (run_dir / "facts.json").is_file()
    assert (run_dir / "summary.md").is_file()
    assert session.closed, "the browser session must still be torn down on STOP"


async def test_pause_is_honoured_then_resumes(tmp_path, server):
    urls = [server.url("/p1.html"), server.url("/p2.html")]
    pause_path = tmp_path / "PAUSE"
    pause_path.write_text("owner paused", encoding="utf-8")
    resumed = {"done": False}
    original_sleep = control_mod.time.sleep

    def sleep_and_resume(_seconds: float) -> None:
        if not resumed["done"]:
            resumed["done"] = True
            pause_path.unlink(missing_ok=True)

    import bcc.collector.control as control_module
    control_module.time.sleep = sleep_and_resume  # noqa: SLF001 -- test-only patch, restored below
    try:
        session = FakeSession(pages={u: ("T", BANDWIDTH_TEXT_A) for u in urls})
        cfg = CollectorConfig(topic=TOPIC, sources_path=_sources_file(tmp_path, urls),
                              data_dir=tmp_path / "data", min_delay_s=5.0,
                              pause_file=pause_path)
        summary = await run_collection(cfg, session_factory=lambda: session)
    finally:
        control_module.time.sleep = original_sleep
    assert resumed["done"] is True
    assert len(summary.pages) == 2
    assert all(p.status == "ok" for p in summary.pages)


async def test_network_error_is_handled_not_fatal(tmp_path, server):
    url_ok, url_fail = server.url("/ok.html"), server.url("/fail.html")
    session = FakeSession(pages={url_ok: ("OK", BANDWIDTH_TEXT_A)},
                          fail={url_fail: ConnectionRefusedError("no one home")})
    cfg = CollectorConfig(topic=TOPIC, sources_path=_sources_file(tmp_path, [url_fail, url_ok]),
                          data_dir=tmp_path / "data", min_delay_s=5.0)
    summary = await run_collection(cfg, session_factory=lambda: session)
    statuses = {p.url: p.status for p in summary.pages}
    assert statuses[url_fail] == "skipped_error"
    assert statuses[url_ok] == "ok"


async def test_unknown_attribute_is_recorded_explicitly(tmp_path, server):
    url = server.url("/p1.html")
    session = FakeSession(pages={url: ("T", BANDWIDTH_TEXT_A)})
    cfg = CollectorConfig(topic=TOPIC, sources_path=_sources_file(tmp_path, [url]),
                          data_dir=tmp_path / "data",
                          attributes=("bandwidth", "tdp_watts_not_mentioned_anywhere"))
    summary = await run_collection(cfg, session_factory=lambda: session)
    unknowns = [f for f in summary.facts if f.unknown]
    assert any(f.predicate == "tdp_watts_not_mentioned_anywhere" for f in unknowns)
    assert all(f.value == "UNKNOWN" for f in unknowns)
    assert not any(f.predicate == "bandwidth" and f.unknown for f in summary.facts)
