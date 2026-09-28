"""robots.txt handling: disallow honoured, absent robots.txt = allowed (the
standard 4xx convention), server errors fail-closed, and the fetch itself
identifies with the one honest product User-Agent — never a fake one.

All against a local fake HTTP server (collector_server.FakeServer) — no real
site is ever contacted.
"""
from __future__ import annotations

import pytest

from bcc.collector import config
from bcc.collector.robots import RobotsChecker

from .collector_server import FakeServer


@pytest.fixture
def server():
    srv = FakeServer()
    yield srv
    srv.close()


def test_disallowed_path_is_refused(server):
    server.set_route("/robots.txt", body="User-agent: *\nDisallow: /secret\n")
    server.set_route("/secret/page.html", body="<html>nope</html>")
    checker = RobotsChecker()
    verdict = checker.allows(server.url("/secret/page.html"))
    assert verdict.allowed is False
    assert "disallow" in verdict.reason.lower()


def test_allowed_path_is_allowed(server):
    server.set_route("/robots.txt", body="User-agent: *\nDisallow: /secret\n")
    checker = RobotsChecker()
    verdict = checker.allows(server.url("/public/page.html"))
    assert verdict.allowed is True


def test_missing_robots_txt_means_allowed(server):
    # No /robots.txt route registered at all -> the fake server answers 404,
    # which is the standard "nothing published" signal, not a refusal.
    checker = RobotsChecker()
    verdict = checker.allows(server.url("/anything.html"))
    assert verdict.allowed is True
    assert "4xx" in verdict.reason or "allowed" in verdict.reason


def test_robots_server_error_fails_closed(server):
    server.set_route("/robots.txt", status=500, body="boom")
    checker = RobotsChecker()
    verdict = checker.allows(server.url("/page.html"))
    assert verdict.allowed is False


def test_robots_fetch_unreachable_fails_closed():
    # Nothing is listening on this port: a genuine network failure.
    checker = RobotsChecker(timeout=1.0)
    verdict = checker.allows("http://127.0.0.1:1/page.html")
    assert verdict.allowed is False


def test_robots_txt_fetch_uses_the_one_honest_user_agent(server):
    server.set_route("/robots.txt", body="User-agent: *\nAllow: /\n")
    checker = RobotsChecker()
    checker.allows(server.url("/page.html"))
    headers = server.headers_for("/robots.txt")
    assert headers is not None
    ua = headers.get("User-Agent", "")
    assert ua == config.USER_AGENT
    # Never rotated: two requests (robots.txt fetched once, memoised) or two
    # different hosts must both show the exact same identity string.
    assert "BossmanOsiris" in ua


def test_robots_verdict_is_memoised_per_host(server):
    server.set_route("/robots.txt", body="User-agent: *\nDisallow: /x\n")
    checker = RobotsChecker()
    checker.allows(server.url("/x/1.html"))
    checker.allows(server.url("/x/2.html"))
    robots_requests = [r for r in server.received if r["path"] == "/robots.txt"]
    assert len(robots_requests) == 1
