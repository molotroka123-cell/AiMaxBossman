"""Security/internet audit 24.10: the main Bossman reads public pages politely and records what it learned.

Synthetic pages only (fake transport, fake DNS); no network. Each check has a negative control.
"""
from __future__ import annotations

import json

from bcc.web_access.polite_reader import LearningJournal, PoliteReader

PUBLIC = {"example.org", "example.net", "www.example.org"}


def _site(pages: dict[str, tuple[int, dict, bytes]]):
    seen: list[str] = []

    def transport(url, headers, timeout):
        seen.append(url)
        assert "Cookie" not in headers and "Authorization" not in headers
        assert headers["User-Agent"].startswith("BossmanWebReader/")
        if url in pages:
            v = pages[url]
            if isinstance(v, Exception):
                raise v
            return v
        return 404, {}, b""
    return transport, seen


def _reader(tmp_path, pages, *, sleeps=None, public=PUBLIC):
    transport, seen = _site(pages)
    journal = LearningJournal(tmp_path / "learning.jsonl", clock=lambda: 1_790_000_000.0)
    reader = PoliteReader(journal, transport=transport, resolver=lambda h: h in public,
                          sleeper=(sleeps.append if sleeps is not None else (lambda s: None)))
    return reader, journal, seen


HTML = (200, {"Content-Type": "text/html; charset=utf-8"},
        "<html><title>Тест mail me@corp.example</title><body><p>Звоните +7 999 123-45-67. Цена 15 000 ₽.</p>"
        "<p>Публичный факт: релиз 2.1 вышел 24.10.2026.</p></body></html>".encode())


def test_ok_read_writes_a_fact_record_with_url_date_kind_and_strips_pii(tmp_path):
    reader, journal, seen = _reader(tmp_path, {"https://example.org/robots.txt": (200, {}, b"User-agent: *\nAllow: /\n"),
                                               "https://example.org/news?utm=x&ref=feed": HTML})
    res = reader.read("https://example.org/news?utm=x&ref=feed")
    assert res.ok and "релиз 2.1" in res.text
    rec = journal.records()[-1]
    assert rec["id"] == res.record_id and len(rec["id"]) == 12
    assert rec["url"] == "https://example.org/news"            # no query string (tokens / personal data)
    assert rec["kind"] == "fact" and rec["status"] == "ok" and rec["date"].startswith("2026-")
    raw = json.dumps(rec, ensure_ascii=False)
    assert "me@corp.example" not in raw and "999 123-45-67" not in raw and "[скрыто]" in raw
    assert "15 000" in raw, "prices/dates survive the PII strip"
    assert not reader.read("https://example.org/p?email=me@corp.example").ok, "a URL carrying an email is refused"


def test_robots_disallow_is_respected_and_recorded_negative_control_allowed_path(tmp_path):
    robots = (200, {}, b"User-agent: *\nDisallow: /private\n")
    reader, journal, seen = _reader(tmp_path, {"https://example.org/robots.txt": robots,
                                               "https://example.org/private/x": HTML, "https://example.org/pub": HTML})
    res = reader.read("https://example.org/private/x")
    assert not res.ok and "robots" in res.error
    assert "https://example.org/private/x" not in seen, "a disallowed page must never be requested"
    assert journal.records()[-1]["kind"] == "unknown"
    assert reader.read("https://example.org/pub").ok          # control: the same site's allowed page is read


def test_robots_unreachable_or_forbidden_fails_closed(tmp_path):
    reader, _, seen = _reader(tmp_path, {"https://example.org/robots.txt": OSError("down"),
                                         "https://example.org/a": HTML})
    assert not reader.read("https://example.org/a").ok and "https://example.org/a" not in seen
    reader, _, seen = _reader(tmp_path, {"https://example.org/robots.txt": (403, {}, b""),
                                         "https://example.org/a": HTML})
    assert not reader.read("https://example.org/a").ok and "https://example.org/a" not in seen


def test_login_captcha_and_403_are_not_bypassed_or_retried(tmp_path):
    login = (200, {"Content-Type": "text/html"}, b"<title>Sign in</title><form><input type='password'></form>")
    captcha = (200, {"Content-Type": "text/html"}, b"<title>Just a moment...</title>cf-chl-bypass")
    pages = {"https://example.org/robots.txt": (404, {}, b""), "https://example.org/login": login,
             "https://example.org/c": captcha, "https://example.org/f": (403, {}, b"")}
    reader, journal, seen = _reader(tmp_path, pages)
    for path in ("login", "c", "f"):
        res = reader.read(f"https://example.org/{path}")
        assert not res.ok and "not bypassed" in res.error, (path, res.error)
        assert seen.count(f"https://example.org/{path}") == 1, "no retry, no bypass attempt"
    assert [r["kind"] for r in journal.records()] == ["unknown"] * 3


def test_non_public_targets_are_refused_before_any_request(tmp_path):
    reader, _, seen = _reader(tmp_path, {})
    for url in ("http://127.0.0.1/", "http://localhost/", "https://u:p@example.org/", "file:///C:/Windows/win.ini",
                "https://internal.corp.example/", "http://169.254.169.254/latest/meta-data/"):
        assert not reader.read(url).ok, url
    assert seen == []


def test_redirect_to_a_private_host_is_refused_negative_control_public_redirect_followed(tmp_path):
    pages = {"https://example.org/robots.txt": (404, {}, b""),
             "https://example.org/go": (302, {"Location": "http://intranet.example/admin"}, b""),
             "https://example.org/move": (301, {"Location": "https://example.net/new"}, b""),
             "https://example.net/robots.txt": (200, {}, b"User-agent: *\nDisallow: /secret\n"),
             "https://example.net/new": HTML,
             "https://example.org/sneak": (302, {"Location": "https://example.net/secret"}, b"")}
    reader, _, seen = _reader(tmp_path, pages)
    res = reader.read("https://example.org/go")
    assert not res.ok and "redirect refused" in res.error and not any("intranet" in u for u in seen)
    ok = reader.read("https://example.org/move")
    assert ok.ok and ok.final_url == "https://example.net/new"
    sneak = reader.read("https://example.org/sneak")
    assert not sneak.ok and "robots" in sneak.error, "robots is checked on every redirect hop"


def test_same_host_is_read_at_most_once_per_5_seconds(tmp_path):
    sleeps: list[float] = []
    pages = {"https://example.org/robots.txt": (404, {}, b""), "https://example.org/a": HTML,
             "https://example.org/b": HTML}
    reader, _, _ = _reader(tmp_path, pages, sleeps=sleeps)
    reader.read("https://example.org/a")
    reader.read("https://example.org/b")
    assert sleeps and all(4.0 < s <= 5.0 for s in sleeps), sleeps


# ------------------------------------------------------------------ API / tools on the real app

async def test_api_read_note_and_learning_list(env):
    from bcc.features.web_access import reader_of
    reader = reader_of(env.svc)
    transport, seen = _site({"https://example.org/robots.txt": (404, {}, b""), "https://example.org/doc": HTML})
    reader.transport, reader.resolver = transport, (lambda h: h in PUBLIC)
    r = await env.client.post("/api/web-access/note", json={"url": "https://example.org/doc", "kind": "conclusion",
                                                             "text": "релиз вышел"})
    assert r.status_code == 422, "a conclusion needs a source that was really read"
    r = await env.client.post("/api/web-access/read", json={"url": "https://example.org/doc"})
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["record_id"] and "_text" not in body and "text" not in body
    r = await env.client.post("/api/web-access/note", json={"url": "https://example.org/doc", "kind": "conclusion",
                                                             "text": "релиз 2.1 вышел 24.10.2026 (по странице)"})
    assert r.status_code == 200 and r.json()["kind"] == "conclusion"
    r = await env.client.post("/api/web-access/note", json={"url": "https://example.org/doc", "kind": "fact",
                                                             "text": "x"})
    assert r.status_code == 422, "facts come only from a real read"
    recs = (await env.client.get("/api/web-access/learning")).json()["records"]
    assert [x["kind"] for x in recs] == ["fact", "conclusion"]
    assert all(x["url"] == "https://example.org/doc" for x in recs)


async def test_web_read_tool_needs_the_owner_flag_and_marks_output_external(env, monkeypatch):
    from bcc.features import web_access
    from bcc.features.web_access import reader_of
    from bcc.tools import REGISTRY
    assert REGISTRY.get("web.read") is None, "negative control: no flag, no tool (a '*' grant gains nothing)"
    monkeypatch.setenv(web_access.READER_FLAG, "1")
    await web_access.setup(env.svc)
    spec = REGISTRY.get("web.read")
    assert spec is not None and spec.permission == "browser.read" and spec.external_output
    reader = reader_of(env.svc)
    transport, _ = _site({"https://example.org/robots.txt": (404, {}, b""), "https://example.org/doc": HTML})
    reader.transport, reader.resolver = transport, (lambda h: h in PUBLIC)
    ctx = type("Ctx", (), {"svc": env.svc, "task": {"id": 7}})()
    res = await spec.handler({"url": "https://example.org/doc"}, ctx)
    assert not res.error and res.external and res.data["record_id"]
    assert reader.journal.records()[-1]["task_id"] == 7
    for name in ("web.read", "web.note"):
        REGISTRY.unregister(name)
