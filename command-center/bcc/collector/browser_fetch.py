"""One real browser tab, reused across a run — the product's own Playwright
runtime (``bcc.v2.browser_control.BrowserManager``), not a second browser
stack. JS-heavy pages render exactly as a human would see them; the
collector never falls back to a raw-HTTP GET to "save time".

The session's own Chromium user agent is never touched — Playwright's
genuine default identifies the real browser and version, which is the
opposite of spoofing.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass

from ..v2.browser_control import (
    BrowserManager,
    BrowserPolicy,
    BrowserPolicyDenied,
    BrowserTakeoverActive,
    BrowserUnavailable,
    CaptchaBlocked,
)
from . import config


class PageBlocked(Exception):
    """The browser itself refused this page (owner takeover/pause, captcha,
    policy). Distinct from a network failure so the caller can decide
    whether to treat it as a pause-equivalent or a hard skip."""

    def __init__(self, reason: str, *, pause_equivalent: bool):
        super().__init__(reason)
        self.pause_equivalent = pause_equivalent


@dataclass(frozen=True, slots=True)
class RenderedPage:
    url: str
    title: str
    text: str


class HumanBrowserSession:
    """A single real browser session, visited one page at a time."""

    SESSION_ID = 1

    def __init__(self, data_dir, *, allowed_hosts: list[str], headless: bool = True):
        self._manager = BrowserManager(data_dir)
        self._policy = BrowserPolicy(enabled=True, allowed_domains=list(allowed_hosts),
                                     persistent_profile=False, screenshots="never")
        self._headless = headless
        self._started = False

    async def start(self) -> None:
        if not self._manager.available:
            raise BrowserUnavailable(
                "Playwright/Chromium недоступен для human-like коллектора: "
                "`pip install playwright && playwright install chromium`.")
        await self._manager.start(self.SESSION_ID, self._policy, headless=self._headless)
        self._started = True

    async def read(self, url: str, *, human_pace: bool = True) -> RenderedPage:
        """Navigates like a considerate reader: goes to the page, gives it a
        moment to settle, then (best effort) scrolls a little before
        reading — the way a human skims before deciding a page is relevant."""
        try:
            snap = await asyncio.wait_for(
                self._manager.navigate(self.SESSION_ID, url, actor="collector", approved=True),
                timeout=config.PAGE_TIMEOUT_S,
            )
        except BrowserTakeoverActive as exc:
            raise PageBlocked(str(exc), pause_equivalent=True) from exc
        except BrowserPolicyDenied as exc:
            paused = "paused" in str(exc).lower()
            raise PageBlocked(str(exc), pause_equivalent=paused) from exc
        except CaptchaBlocked as exc:
            # Reading is still allowed by the guard, but a CAPTCHA on the
            # page means the site does not want automated visitors reading
            # it either — a considerate reader leaves, they do not solve it.
            raise PageBlocked(str(exc), pause_equivalent=False) from exc
        if human_pace:
            await self._skim(url)
            snap = await self._manager.snapshot(self.SESSION_ID, actor="collector", approved=True)
        return RenderedPage(url=str(snap.get("url") or url), title=str(snap.get("title") or ""),
                            text=str(snap.get("text") or ""))

    async def _skim(self, url: str) -> None:
        """Best-effort human pacing: a short settle-in wait, then a couple of
        gentle scrolls with pauses in between, mirroring how a person reads
        a page instead of grabbing the DOM the instant it loads. Never
        fatal — a page that cannot be scrolled (e.g. no scrollable body) is
        still read via the ordinary snapshot."""
        await asyncio.sleep(random.uniform(1.0, 2.0))
        try:
            sess = self._manager._session(self.SESSION_ID)  # noqa: SLF001 -- same package, read-only scroll
            for _ in range(2):
                await sess.page.mouse.wheel(0, 900)
                await asyncio.sleep(random.uniform(0.6, 1.4))
        except Exception:  # noqa: BLE001 -- scrolling is a courtesy, not a requirement
            pass

    async def close(self) -> None:
        if self._started:
            await self._manager.close()
            self._started = False
