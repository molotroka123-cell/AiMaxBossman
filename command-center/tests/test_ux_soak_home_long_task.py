"""RC 1.9 soak (workstream F): a long task could not be launched from Главная.

Repro (soak, owner-style): paste a long brief (> 12 000 characters) into the home composer
and press Ctrl+Enter. The composer first asks the Video Studio router whether this is a
video request; that endpoint accepts at most 12 000 characters, answered 422 and the
owner saw «неверный запрос: text — String should have at most 12000 characters». The
task was never created, although /api/tasks accepts the prompt.

A text the video router cannot take can never be a video request: it goes straight to
the ordinary task path. Short texts still consult the router exactly as before.
"""
from __future__ import annotations

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

LONG = "Подробное ТЗ. " + "Ж" * 13_000


def _agent(srv) -> int:
    """One agent (the composer then needs no picker), created through the public API."""
    import httpx

    with httpx.Client(base_url=srv.url, trust_env=False, timeout=10,
                      headers={"X-BCC-Token": srv.svc.auth.token}) as c:
        prov = c.post("/api/providers", json={"name": "ux", "kind": "openai_compat",
                                              "base_url": "http://127.0.0.1:9/v1"}).json()
        model = c.post("/api/models", json={"provider_id": prov["id"], "name": "ux-model"}).json()
        return int(c.post("/api/agents", json={"name": "ux-agent", "model_id": model["id"]}).json()["id"])


def test_long_brief_from_home_becomes_a_task(live):  # noqa: F811
    from playwright.sync_api import sync_playwright

    _agent(live)
    seen: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("request", lambda r: seen.append(r.url) if "/api/video-studio/chat" in r.url else None)
        _login(page, live)
        page.wait_for_selector("#view[data-rendered='home-v3']", timeout=20000)
        box = page.locator("#view textarea").first
        box.fill(LONG)
        box.press("Control+Enter")
        page.wait_for_selector("#toast-root .toast", timeout=10000)
        toasts = page.evaluate("() => [...document.querySelectorAll('#toast-root .toast')].map(e => e.innerText).join(' | ')")
        browser.close()

    assert "поставлена" in toasts, toasts
    assert "12000" not in toasts and "неверный запрос" not in toasts
    import asyncio
    import sqlalchemy as sa
    from bcc.db import tasks as tasks_t

    async def prompts():
        async with live.svc.db.session() as s:
            return [r[0] for r in (await s.execute(sa.select(tasks_t.c.prompt))).fetchall()]

    stored = asyncio.run_coroutine_threadsafe(prompts(), live.loop).result(timeout=10)
    assert stored == [LONG]
    # the video router was not asked about a text it cannot accept
    assert seen == []


def test_long_brief_with_media_is_refused_readably_not_dropped(live, tmp_path):  # noqa: F811
    """With media attached the text is meant for Video Studio: refuse with a readable
    limit instead of the raw 422, and never create a task that silently drops the media."""
    from playwright.sync_api import sync_playwright

    _agent(live)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    seen: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("request", lambda r: seen.append(r.url) if "/api/video-studio/" in r.url else None)
        _login(page, live)
        page.wait_for_selector("#view[data-rendered='home-v3']", timeout=20000)
        page.set_input_files("#view input[type=file]", str(clip))
        box = page.locator("#view textarea").first
        box.fill(LONG)
        box.press("Control+Enter")
        page.wait_for_selector("#toast-root .toast", timeout=10000)
        toasts = page.evaluate("() => [...document.querySelectorAll('#toast-root .toast')].map(e => e.innerText).join(' | ')")
        page.set_input_files("#view input[type=file]", [])
        browser.close()

    assert "длиннее 12000" in toasts and "неверный запрос" not in toasts, toasts
    assert seen == []
    import asyncio
    import sqlalchemy as sa
    from bcc.db import tasks as tasks_t

    async def count():
        async with live.svc.db.session() as s:
            return (await s.execute(sa.select(sa.func.count()).select_from(tasks_t))).scalar()

    assert asyncio.run_coroutine_threadsafe(count(), live.loop).result(timeout=10) == 0
