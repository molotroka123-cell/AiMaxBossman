"""Jeff browser SEARCH fallback: a read-only, allowlisted, transparent browser behind an OFF-by-default flag.

What it is: when the normal search path (the configured SearXNG / keyless helper used by the Research Desk) returned
NOTHING, Jeff may open ONE public search-results page and read at most two result pages in a plain, headless browser
driven over MCP (the intended server is Microsoft's plain ``@playwright/mcp``, pinned and launched ``--isolated``).

What it is NOT (deliberate, see docs/owner/JEFF_BROWSER_SEARCH_AUDIT_20261011.md):
* no stealth / anti-bot / fingerprint-evasion / captcha solving; the browser identifies itself as what it is and
  ``robots.txt`` is honoured. The upstream "invisible" project was audited and NOT wired in.
* no click / type / evaluate / upload / download / login / form / payment tools: the allowlist is three tool names and
  anything else raises ``ForbiddenAction`` BEFORE it reaches the MCP client.
* Jeff (the model) never sees a browser tool. This module is deterministic code called by the Research Desk; the
  model only receives sanitised, redacted text as untrusted data. Shell / computer-use authority is untouched.

Safety envelope, all enforced here and covered by tests/test_jeff_browser_search.py:
flag OFF => no client is ever constructed (no process); <= 3 navigations per answer; <= 15 s per call (the client is
closed on timeout); global rate limit; STOP flag checked before and after every call; ``robots.txt`` per origin;
SSRF guard (public addresses only, http/https only, no credentials) before every navigation and on the landed URL;
ephemeral profile (``--isolated``) and a client that is closed after every answer; every result passes the injection
sanitiser, the secret filter and a PII filter before it can reach a participant.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol
from urllib import robotparser
from urllib.parse import parse_qs, quote_plus, urlsplit

from ..secret_filter import redact_secrets
from .research import Resolver, Search, guarded_target, safe_url, sanitize_fetched

log = logging.getLogger("bcc.pit.browser_search")

FLAG_ENV = "BOSSMAN_JEFF_BROWSER_SEARCH"          # "1"/"on"/"true"/"yes" turns it on; anything else is OFF
COMMAND_ENV = "BOSSMAN_JEFF_BROWSER_MCP_CMD"       # JSON list or shell-quoted string; NO default (nothing is installed)
ENGINE_ENV = "BOSSMAN_JEFF_BROWSER_SEARCH_URL"     # results-page template with {q}; default below
DEFAULT_ENGINE = "https://html.duckduckgo.com/html/?q={q}"
USER_AGENT_TOKEN = "BossmanJeff"                   # robots.txt group we identify as (the browser UA itself is untouched)

#: Exactly what the browser may be asked to do. Read-only. Everything else is refused before the client is touched.
TOOL_NAVIGATE = "browser_navigate"
TOOL_SNAPSHOT = "browser_snapshot"
TOOL_SCREENSHOT = "browser_take_screenshot"
TOOL_CLOSE = "browser_close"
ALLOWED_TOOLS = frozenset({TOOL_NAVIGATE, TOOL_SNAPSHOT, TOOL_SCREENSHOT, TOOL_CLOSE})

MAX_PAGES = 3                    # navigations per answer, search page included
CALL_TIMEOUT_S = 15.0
RATE_LIMIT = (4, 600.0)          # browser answers per window, whole process
ROBOTS_TTL_S = 3600.0
ROBOTS_TIMEOUT_S = 5.0
MAX_ROWS = 4
SNIPPET_CHARS = 400
PAGE_CHARS = 1200
TITLE_CHARS = 150


class BrowserSearchError(Exception):
    """Base class: every failure is turned into "no browser results", never raised into the chat."""


class ForbiddenAction(BrowserSearchError):
    """A tool outside ``ALLOWED_TOOLS`` (or a screenshot without the explicit opt-in) was requested."""


class BrowserStopped(BrowserSearchError):
    """The owner's STOP flag is set."""


class BudgetExceeded(BrowserSearchError):
    """More than ``MAX_PAGES`` navigations in one answer."""


class RobotsDisallowed(BrowserSearchError):
    """robots.txt (or an unreadable robots.txt) refuses this URL."""


class McpToolClient(Protocol):
    """The minimum the guard needs from an MCP session; the real one is ``StdioMcpClient``, tests use a fake."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str: ...

    async def close(self) -> None: ...


def enabled(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(FLAG_ENV, "")).strip().lower() in {"1", "on", "true", "yes"}


# --------------------------------------------------------------------------- output filters
_EMAIL = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+", re.UNICODE)
_PHONE = re.compile(r"(?<![\w/.-])\+?\d[\d\s().-]{8,16}\d(?![\w/-])")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_SECRET_QUERY = re.compile(r"[?&](?:[\w.-]*(?:token|key|secret|passw\w*|pwd|sig|signature|auth|session|sid|code)[\w.-]*)=",
                           re.I)


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if alt:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
        alt = not alt
    return total % 10 == 0


def redact_pii(text: str) -> tuple[str, int]:
    """Secrets (existing filter) + e-mails, phone-like numbers, Luhn-valid card numbers and IBANs. -> (text, hits)."""
    value, changed = redact_secrets(str(text or ""))
    hits = 1 if changed else 0

    def sub(pattern: re.Pattern, repl: str, value: str, check: Callable[[str], bool] | None = None) -> str:
        nonlocal hits

        def one(match: re.Match) -> str:
            nonlocal hits
            if check is not None and not check(match.group(0)):
                return match.group(0)
            hits += 1
            return repl
        return pattern.sub(one, value)

    value = sub(_EMAIL, "[REDACTED_EMAIL]", value)
    value = sub(_IBAN, "[REDACTED_IBAN]", value)
    value = sub(_CARD, "[REDACTED_CARD]", value, lambda m: _luhn(re.sub(r"\D", "", m)) and 13 <= len(re.sub(r"\D", "", m)) <= 19)
    value = sub(_PHONE, "[REDACTED_PHONE]", value)
    return value, hits


def clean_text(raw: str, *, limit: int) -> str:
    """Untrusted page text -> injection-sanitised, secret/PII-redacted, capped."""
    text, _ = sanitize_fetched(raw, max_chars=limit * 4)
    text, _ = redact_pii(text)
    return text[:limit]


def clean_url(url: str) -> str | None:
    """Public http(s) URL with no credentials and no secret-looking query parameter; else None."""
    ok = safe_url(url)
    if ok is None or _SECRET_QUERY.search(ok):
        return None
    return ok


# --------------------------------------------------------------------------- robots.txt
RobotsFetch = Callable[[str], Awaitable[tuple[int, str]]]      # origin -> (http status, body)


async def _http_robots_fetch(origin: str) -> tuple[int, str]:
    import httpx
    async with httpx.AsyncClient(follow_redirects=False, timeout=ROBOTS_TIMEOUT_S,
                                 headers={"User-Agent": USER_AGENT_TOKEN}) as client:
        response = await client.get(origin + "/robots.txt")
        return response.status_code, response.text[:200_000]


class RobotsPolicy:
    """robots.txt per origin, cached. 4xx (no file) = allowed, 5xx / network error / timeout = refused (conservative)."""

    def __init__(self, fetch: RobotsFetch | None = None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._fetch = fetch or _http_robots_fetch
        self._clock = clock
        self._cache: dict[str, tuple[float, robotparser.RobotFileParser | None]] = {}

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        now = self._clock()
        row = self._cache.get(origin)
        if row is None or row[0] <= now:
            parser: robotparser.RobotFileParser | None
            try:
                status, body = await asyncio.wait_for(self._fetch(origin), timeout=ROBOTS_TIMEOUT_S + 1)
            except Exception:  # noqa: BLE001 - unreadable robots.txt: do not guess, refuse
                status, body = 599, ""
            if 400 <= status < 500:
                parser = robotparser.RobotFileParser()
                parser.parse([])                      # no robots.txt: everything allowed
            elif 200 <= status < 300:
                parser = robotparser.RobotFileParser()
                parser.parse(body.splitlines())
            else:
                parser = None                         # refuse
            self._cache[origin] = row = (now + ROBOTS_TTL_S, parser)
        parser = row[1]
        if parser is None:
            return False
        return parser.can_fetch(USER_AGENT_TOKEN, url) and parser.can_fetch("*", url)


# --------------------------------------------------------------------------- the guarded session
class GuardedBrowser:
    """One answer's worth of browser access. Every tool call goes through ``_call``; nothing else touches the client."""

    def __init__(self, client: McpToolClient, *, stopped: Callable[[], bool], robots: RobotsPolicy,
                 resolver: Resolver | None = None, allow_screenshot: bool = False,
                 max_pages: int = MAX_PAGES, call_timeout: float = CALL_TIMEOUT_S) -> None:
        self._client, self._stopped, self._robots = client, stopped, robots
        self._resolver, self._allow_screenshot = resolver, allow_screenshot
        self._max_pages, self._timeout = max_pages, call_timeout
        self.pages = 0
        self.calls: list[str] = []

    async def _call(self, name: str, arguments: dict[str, Any]) -> str:
        if name not in ALLOWED_TOOLS or (name == TOOL_SCREENSHOT and not self._allow_screenshot):
            raise ForbiddenAction(name)
        if self._stopped():
            raise BrowserStopped()
        self.calls.append(name)
        try:
            out = await asyncio.wait_for(self._client.call_tool(name, arguments), timeout=self._timeout)
        except asyncio.TimeoutError:
            await self.close()                        # a hung page must not keep a process alive
            raise
        if self._stopped():
            await self.close()
            raise BrowserStopped()
        return str(out or "")

    async def open_page(self, url: str) -> str:
        """Navigate to a PUBLIC page and return its raw visible text (still untrusted; callers clean it)."""
        if self.pages >= self._max_pages:
            raise BudgetExceeded()
        target = await guarded_target(url, resolver=self._resolver)
        if target is None:
            raise ForbiddenAction("non-public url")
        if not await self._robots.allowed(target):
            raise RobotsDisallowed(urlsplit(target).hostname or "")
        self.pages += 1
        landed = await self._call(TOOL_NAVIGATE, {"url": target})
        final = re.search(r"Page URL:\s*(\S+)", landed)
        if final and await guarded_target(final.group(1), resolver=self._resolver) is None:
            raise ForbiddenAction("landed on non-public url")      # a redirect into the LAN / metadata: content dropped
        return await self._call(TOOL_SNAPSHOT, {})

    async def screenshot(self, url: str) -> str:
        """Opt-in (allow_screenshot): returns what the server says (a file path / reference), never page text."""
        if not self._allow_screenshot:
            raise ForbiddenAction(TOOL_SCREENSHOT)             # refuse BEFORE navigating anywhere
        await self.open_page(url)
        return await self._call(TOOL_SCREENSHOT, {})

    async def close(self) -> None:
        try:
            await asyncio.wait_for(self._client.close(), timeout=5.0)
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- result parsing
_LINK = re.compile(r'-\s*link\s+"(?P<title>[^"]{1,300})"[^\n]*\n(?:\s*-\s*/url:\s*(?P<url>\S+))', re.I)
_MD_LINK = re.compile(r"\[(?P<title>[^\]]{1,300})\]\((?P<url>https?://[^)\s]+)\)")


def _unwrap_redirect(url: str) -> str:
    """Result links of HTML search pages are often ``//engine/l/?uddg=<target>``: use the target, not the tracker."""
    raw = url.strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        parts = urlsplit(raw)
        target = parse_qs(parts.query).get("uddg")
    except ValueError:
        return url
    return target[0] if target else raw


def parse_results(snapshot: str, *, skip_host: str = "", limit: int = MAX_ROWS) -> list[dict]:
    """Links out of an accessibility snapshot (``- link "T"`` + ``/url:``) or markdown; titles are cleaned and URLs vetted."""
    rows: list[dict] = []
    seen: set[str] = set()
    for match in list(_LINK.finditer(snapshot)) + list(_MD_LINK.finditer(snapshot)):
        url = clean_url(_unwrap_redirect(match.group("url")))
        if url is None:
            continue
        host = (urlsplit(url).hostname or "").lower()
        if skip_host and (host == skip_host or host.endswith("." + skip_host)):
            continue                                   # links back to the search engine itself
        if url in seen:
            continue
        seen.add(url)
        title = clean_text(match.group("title"), limit=TITLE_CHARS)
        rows.append({"title": title or host, "url": url, "snippet": ""})
        if len(rows) >= limit:
            break
    return rows


# --------------------------------------------------------------------------- the search fallback
ClientFactory = Callable[[], Awaitable[McpToolClient]]


class BrowserSearch:
    """``async search(query) -> rows`` with the Research Desk's ``Search`` signature. Flag OFF => returns [] and never
    calls ``client_factory``."""

    def __init__(self, client_factory: ClientFactory, *, stopped: Callable[[], bool] = lambda: False,
                 is_enabled: Callable[[], bool] = enabled, robots: RobotsPolicy | None = None,
                 resolver: Resolver | None = None, engine_url: str = "", clock: Callable[[], float] = time.monotonic,
                 enrich_pages: int = MAX_PAGES - 1, call_timeout: float = CALL_TIMEOUT_S) -> None:
        self._factory, self._stopped, self._enabled = client_factory, stopped, is_enabled
        self._robots = robots or RobotsPolicy()
        self._resolver = resolver
        self._engine = engine_url or os.environ.get(ENGINE_ENV, "").strip() or DEFAULT_ENGINE
        self._clock = clock
        self._enrich = max(0, min(int(enrich_pages), MAX_PAGES - 1))
        self._call_timeout = call_timeout
        self._stamps: list[float] = []
        self._lock = asyncio.Lock()
        self.counters = {"runs": 0, "disabled": 0, "rate_limited": 0, "stopped": 0, "forbidden": 0, "robots": 0,
                         "timeouts": 0, "errors": 0, "pii_hits": 0, "rows": 0}

    def _rate_ok(self) -> bool:
        limit, window = RATE_LIMIT
        now = self._clock()
        self._stamps = [t for t in self._stamps if now - t < window]
        if len(self._stamps) >= limit:
            return False
        self._stamps.append(now)
        return True

    async def search(self, query: str) -> list[dict]:
        query = " ".join(str(query or "").split())[:200]
        if not query:
            return []
        if not self._enabled():
            self.counters["disabled"] += 1
            return []                                       # OFF: not even a factory call
        # The query leaves the machine to a search engine: it must not carry a secret / PII.
        if redact_pii(query)[1]:
            self.counters["pii_hits"] += 1
            return []
        if self._stopped():
            self.counters["stopped"] += 1
            return []
        if not self._rate_ok():
            self.counters["rate_limited"] += 1
            return []
        async with self._lock:                              # one browser at a time
            self.counters["runs"] += 1
            guard: GuardedBrowser | None = None
            try:
                client = await asyncio.wait_for(self._factory(), timeout=self._call_timeout)
                guard = GuardedBrowser(client, stopped=self._stopped, robots=self._robots, resolver=self._resolver,
                                       call_timeout=self._call_timeout)
                serp = self._engine.replace("{q}", quote_plus(query))
                snapshot = await guard.open_page(serp)
                rows = parse_results(snapshot, skip_host=(urlsplit(serp).hostname or "").lower())
                for row in rows[: self._enrich]:
                    try:
                        text = await guard.open_page(row["url"])
                    except BudgetExceeded:
                        break
                    except (ForbiddenAction, RobotsDisallowed):
                        continue                            # that one result only; the rest are still usable
                    cleaned = clean_text(text, limit=PAGE_CHARS)
                    row["snippet"] = cleaned[:SNIPPET_CHARS]
                self.counters["rows"] += len(rows)
                return rows
            except BrowserStopped:
                self.counters["stopped"] += 1
            except ForbiddenAction:
                self.counters["forbidden"] += 1
            except RobotsDisallowed:
                self.counters["robots"] += 1
            except asyncio.TimeoutError:
                self.counters["timeouts"] += 1
            except Exception as exc:  # noqa: BLE001 - never raised into the chat
                self.counters["errors"] += 1
                log.warning("browser search failed: %s", type(exc).__name__)
            finally:
                if guard is not None:
                    await guard.close()
            return []

    def status(self) -> dict[str, Any]:
        return {"enabled": bool(self._enabled()), "counters": dict(self.counters), "max_pages": MAX_PAGES,
                "call_timeout_s": self._call_timeout}


def with_browser_fallback(primary: Search, browser: BrowserSearch) -> Search:
    """The existing search first; the browser ONLY when it returned nothing (or raised)."""
    async def search(query: str) -> list[dict]:
        try:
            rows = list(await primary(query))
        except Exception:  # noqa: BLE001 - primary failure is the same "nothing" signal
            rows = []
        if rows:
            return rows
        return await browser.search(query)
    return search


# --------------------------------------------------------------------------- real transport (lazy, optional)
def configured_command(environ: dict[str, str] | None = None) -> list[str]:
    raw = str((os.environ if environ is None else environ).get(COMMAND_ENV, "")).strip()
    if not raw:
        return []
    try:
        value = json.loads(raw) if raw.startswith("[") else shlex.split(raw, posix=(os.name != "nt"))
    except ValueError:
        return []
    return [str(part) for part in value] if isinstance(value, list) and value else []


class StdioMcpClient:
    """Launches the configured MCP server over stdio with the python ``mcp`` SDK. Created ONLY when the flag is on and a
    command is configured; ``close()`` ends the process (the server runs ``--isolated``: its profile is in-memory)."""

    def __init__(self, command: list[str]) -> None:
        self._command = command
        self._stack: Any = None
        self._session: Any = None

    async def start(self) -> "StdioMcpClient":
        from contextlib import AsyncExitStack
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(
                stdio_client(StdioServerParameters(command=self._command[0], args=self._command[1:],
                                                   env={k: v for k, v in os.environ.items()
                                                        if k in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE"}})))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except BaseException:
            await stack.aclose()
            raise
        self._stack, self._session = stack, session
        return self

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = await self._session.call_tool(name, arguments)
        return "\n".join(getattr(part, "text", "") for part in (result.content or []) if getattr(part, "text", ""))

    async def close(self) -> None:
        stack, self._stack, self._session = self._stack, None, None
        if stack is not None:
            await stack.aclose()


def default_factory(environ: dict[str, str] | None = None) -> ClientFactory | None:
    command = configured_command(environ)
    if not command:
        return None

    async def factory() -> McpToolClient:
        return await StdioMcpClient(command).start()
    return factory


def maybe_wrap(primary: Search, *, stopped: Callable[[], bool], environ: dict[str, str] | None = None) -> tuple[Search, BrowserSearch | None]:
    """Used by ``research.create``: returns ``primary`` untouched unless the flag is ON and a command is configured."""
    if not enabled(environ):
        return primary, None
    factory = default_factory(environ)
    if factory is None:
        log.warning("%s is on but %s is not set: browser search stays off", FLAG_ENV, COMMAND_ENV)
        return primary, None
    browser = BrowserSearch(factory, stopped=stopped, is_enabled=lambda: enabled(environ))
    return with_browser_fallback(primary, browser), browser
