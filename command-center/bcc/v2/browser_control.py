"""BOSSMAN Browser & Computer Control runtime.

Drop-in module for command-center/bcc.

Goals:
- Playwright-based browser sessions controlled by BOSSMAN.
- DOM-first + screenshot/vision-friendly snapshots.
- Per-agent/browser permission policy: auto | ask | deny.
- Human takeover that blocks autonomous actions until Resume.
- Persistent browser profiles are optional.
- No browser dependency at import time: Playwright is loaded lazily.

This module intentionally does NOT execute payments, wallet actions or arbitrary
desktop mouse/keyboard actions. Those remain separate, approval-gated tools.
"""
from __future__ import annotations

import asyncio
import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

Decision = Literal["auto", "ask", "deny"]

READ_ACTIONS = {"snapshot", "screenshot", "read_dom"}
NAV_ACTIONS = {"navigate", "back", "reload"}
INTERACTION_ACTIONS = {"click", "type", "select", "download"}
SENSITIVE_ACTIONS = {"upload", "submit", "login"}
HARD_DENY_ACTIONS = {"purchase", "payment", "wallet", "bank_transfer"}

DEFAULT_RULES: dict[str, Decision] = {
    "navigate": "auto",
    "back": "auto",
    "reload": "auto",
    "read_dom": "auto",
    "snapshot": "auto",
    "screenshot": "auto",
    "click": "auto",
    "type": "auto",
    "select": "auto",
    "download": "ask",
    "upload": "ask",
    "submit": "ask",
    "login": "ask",
    "purchase": "deny",
    "payment": "deny",
    "wallet": "deny",
    "bank_transfer": "deny",
}


class BrowserUnavailable(RuntimeError):
    pass


class BrowserPolicyDenied(RuntimeError):
    def __init__(self, action: str, detail: str = ""):
        super().__init__(detail or f"browser action denied: {action}")
        self.action = action


class BrowserApprovalRequired(RuntimeError):
    def __init__(self, action: str, detail: str = ""):
        super().__init__(detail or f"browser action requires approval: {action}")
        self.action = action


class BrowserTakeoverActive(RuntimeError):
    pass


@dataclass(slots=True)
class BrowserPolicy:
    enabled: bool = True
    mode: str = "dom_vision"  # dom_vision | dom_only | vision_only
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    rules: dict[str, Decision] = field(default_factory=lambda: dict(DEFAULT_RULES))
    max_tabs: int = 5
    persistent_profile: bool = False
    screenshots: str = "errors"  # never | errors | each_step
    max_runtime_minutes: int = 480

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "BrowserPolicy":
        raw = dict(raw or {})
        rules = dict(DEFAULT_RULES)
        rules.update({k: v for k, v in (raw.get("rules") or {}).items()
                      if v in ("auto", "ask", "deny")})
        return cls(
            enabled=bool(raw.get("enabled", True)),
            mode=str(raw.get("mode") or "dom_vision"),
            allowed_domains=_patterns(raw.get("allowed_domains")),
            blocked_domains=_patterns(raw.get("blocked_domains")),
            rules=rules,
            max_tabs=max(1, min(int(raw.get("max_tabs") or 5), 25)),
            persistent_profile=bool(raw.get("persistent_profile", False)),
            screenshots=str(raw.get("screenshots") or "errors"),
            max_runtime_minutes=max(1, min(int(raw.get("max_runtime_minutes") or 480), 10080)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "allowed_domains": self.allowed_domains,
            "blocked_domains": self.blocked_domains,
            "rules": self.rules,
            "max_tabs": self.max_tabs,
            "persistent_profile": self.persistent_profile,
            "screenshots": self.screenshots,
            "max_runtime_minutes": self.max_runtime_minutes,
        }

    def domain_allowed(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return False
        if any(_host_match(host, p) for p in self.blocked_domains):
            return False
        if not self.allowed_domains:
            return True
        return any(_host_match(host, p) for p in self.allowed_domains)

    def decision(self, action: str, *, url: str = "") -> Decision:
        if not self.enabled:
            return "deny"
        action = action.strip().lower()
        if action in HARD_DENY_ACTIONS:
            return "deny"
        if url and action in NAV_ACTIONS | INTERACTION_ACTIONS | SENSITIVE_ACTIONS:
            if not self.domain_allowed(url):
                return "deny"
        return self.rules.get(action, "ask")


@dataclass
class BrowserRuntimeSession:
    id: int
    policy: BrowserPolicy
    context: Any
    page: Any
    browser: Any = None
    takeover: bool = False
    paused: bool = False
    profile_name: str = "default"


def _patterns(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = re.split(r"[\s,]+", value)
    return [str(v).strip().lower() for v in value if str(v).strip()]


def _host_match(host: str, pattern: str) -> bool:
    pattern = pattern.strip().lower()
    if not pattern:
        return False
    if pattern.startswith("*."):
        root = pattern[2:]
        return host == root or host.endswith("." + root)
    return fnmatch.fnmatch(host, pattern)


class BrowserManager:
    """In-process Playwright manager.

    Persistence of profile/session metadata belongs to the Control API/DB layer.
    A process restart closes live Chromium contexts; the DB should mark such
    sessions interrupted and let the user/mission reopen from a checkpoint.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.profile_dir = self.data_dir / "profiles"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw: Any = None
        self._sessions: dict[int, BrowserRuntimeSession] = {}
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        try:
            import playwright.async_api  # noqa: F401
            return True
        except Exception:
            return False

    async def _playwright(self):
        if self._pw is not None:
            return self._pw
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise BrowserUnavailable(
                "Playwright не установлен. Установите dependency и Chromium: "
                "`pip install playwright && playwright install chromium`"
            ) from exc
        self._pw = await async_playwright().start()
        return self._pw

    async def start(self, session_id: int, policy: BrowserPolicy,
                    *, profile_name: str = "default", headless: bool = True) -> dict[str, Any]:
        if not policy.enabled:
            raise BrowserPolicyDenied("start", "Browser access выключен политикой")
        async with self._lock:
            if session_id in self._sessions:
                return await self.status(session_id)
            pw = await self._playwright()
            browser = None
            if policy.persistent_profile:
                safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", profile_name)[:80] or "default"
                user_data = self.profile_dir / safe_name
                context = await pw.chromium.launch_persistent_context(
                    str(user_data), headless=headless, viewport={"width": 1440, "height": 900}
                )
                pages = context.pages
                page = pages[0] if pages else await context.new_page()
            else:
                browser = await pw.chromium.launch(headless=headless)
                context = await browser.new_context(viewport={"width": 1440, "height": 900})
                page = await context.new_page()
            self._sessions[session_id] = BrowserRuntimeSession(
                id=session_id, policy=policy, context=context, page=page,
                browser=browser, profile_name=profile_name
            )
        return await self.status(session_id)

    async def stop(self, session_id: int) -> None:
        sess = self._sessions.pop(session_id, None)
        if not sess:
            return
        try:
            await sess.context.close()
        finally:
            if sess.browser is not None:
                try:
                    await sess.browser.close()
                except Exception:
                    pass

    async def close(self) -> None:
        for sid in list(self._sessions):
            await self.stop(sid)
        if self._pw is not None:
            try:
                await self._pw.stop()
            finally:
                self._pw = None

    def _session(self, session_id: int) -> BrowserRuntimeSession:
        sess = self._sessions.get(session_id)
        if not sess:
            raise LookupError(f"browser session {session_id} is not running")
        return sess

    def _guard(self, sess: BrowserRuntimeSession, action: str,
               *, url: str = "", actor: str = "agent", approved: bool = False) -> None:
        if sess.paused:
            raise BrowserPolicyDenied(action, "Browser session paused")
        if sess.takeover and actor != "human":
            raise BrowserTakeoverActive("Human takeover active")
        decision = sess.policy.decision(action, url=url or sess.page.url)
        if decision == "deny":
            raise BrowserPolicyDenied(action)
        if decision == "ask" and actor != "human" and not approved:
            raise BrowserApprovalRequired(action)

    async def status(self, session_id: int) -> dict[str, Any]:
        sess = self._session(session_id)
        return {
            "id": session_id,
            "url": sess.page.url,
            "title": await sess.page.title(),
            "takeover": sess.takeover,
            "paused": sess.paused,
            "profile_name": sess.profile_name,
            "mode": sess.policy.mode,
            "pages": len(sess.context.pages),
        }

    async def pause(self, session_id: int) -> dict[str, Any]:
        sess = self._session(session_id)
        sess.paused = True
        return await self.status(session_id)

    async def resume(self, session_id: int) -> dict[str, Any]:
        sess = self._session(session_id)
        sess.paused = False
        sess.takeover = False
        return await self.status(session_id)

    async def takeover(self, session_id: int) -> dict[str, Any]:
        sess = self._session(session_id)
        sess.takeover = True
        return await self.status(session_id)

    async def navigate(self, session_id: int, url: str, *,
                       actor: str = "agent", approved: bool = False) -> dict[str, Any]:
        sess = self._session(session_id)
        self._guard(sess, "navigate", url=url, actor=actor, approved=approved)
        await sess.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        return await self.snapshot(session_id, actor=actor, approved=True)

    async def click(self, session_id: int, selector: str, *,
                    actor: str = "agent", approved: bool = False) -> dict[str, Any]:
        sess = self._session(session_id)
        self._guard(sess, "click", actor=actor, approved=approved)
        await sess.page.locator(selector).first.click(timeout=30000)
        return await self.status(session_id)

    async def type_text(self, session_id: int, selector: str, text: str, *,
                        actor: str = "agent", approved: bool = False) -> dict[str, Any]:
        sess = self._session(session_id)
        self._guard(sess, "type", actor=actor, approved=approved)
        loc = sess.page.locator(selector).first
        await loc.fill(text, timeout=30000)
        return await self.status(session_id)

    async def select(self, session_id: int, selector: str, value: str, *,
                     actor: str = "agent", approved: bool = False) -> dict[str, Any]:
        sess = self._session(session_id)
        self._guard(sess, "select", actor=actor, approved=approved)
        await sess.page.locator(selector).first.select_option(value, timeout=30000)
        return await self.status(session_id)

    async def back(self, session_id: int, *, actor: str = "agent") -> dict[str, Any]:
        sess = self._session(session_id)
        self._guard(sess, "back", actor=actor)
        await sess.page.go_back(wait_until="domcontentloaded", timeout=30000)
        return await self.status(session_id)

    async def reload(self, session_id: int, *, actor: str = "agent") -> dict[str, Any]:
        sess = self._session(session_id)
        self._guard(sess, "reload", actor=actor)
        await sess.page.reload(wait_until="domcontentloaded", timeout=30000)
        return await self.status(session_id)

    async def screenshot(self, session_id: int, *,
                         actor: str = "agent", approved: bool = False) -> bytes:
        sess = self._session(session_id)
        self._guard(sess, "screenshot", actor=actor, approved=approved)
        return await sess.page.screenshot(type="png", full_page=False)

    async def snapshot(self, session_id: int, *,
                       actor: str = "agent", approved: bool = False,
                       max_text: int = 20000, max_interactive: int = 200) -> dict[str, Any]:
        sess = self._session(session_id)
        self._guard(sess, "snapshot", actor=actor, approved=approved)
        page = sess.page
        # DOM-first snapshot: cheap and deterministic. Vision only receives screenshot when needed.
        data = await page.evaluate(
            """(limits) => {
              const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
              const text = clean(document.body ? document.body.innerText : '').slice(0, limits.maxText);
              const selectors = 'a,button,input,textarea,select,[role="button"],[role="link"],[tabindex]';
              const nodes = Array.from(document.querySelectorAll(selectors)).slice(0, limits.maxInteractive);
              const interactive = nodes.map((el, i) => ({
                i,
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role') || '',
                text: clean(el.innerText || el.value || '').slice(0, 220),
                aria: clean(el.getAttribute('aria-label') || '').slice(0, 220),
                name: el.getAttribute('name') || '',
                type: el.getAttribute('type') || '',
                placeholder: clean(el.getAttribute('placeholder') || '').slice(0, 220),
                href: el.href || '',
                disabled: !!el.disabled,
              }));
              return { text, interactive };
            }""",
            {"maxText": max_text, "maxInteractive": max_interactive},
        )
        return {
            "session_id": session_id,
            "url": page.url,
            "title": await page.title(),
            "text": data.get("text") or "",
            "interactive": data.get("interactive") or [],
            "takeover": sess.takeover,
            "paused": sess.paused,
            "mode": sess.policy.mode,
        }
