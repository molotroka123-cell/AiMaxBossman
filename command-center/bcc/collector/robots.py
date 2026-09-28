"""robots.txt: fetched honestly, parsed with the standard library, fail-closed
on ambiguity.

Uses ``urllib.robotparser`` (stdlib — the task explicitly allows it) instead
of pulling in a third-party parser: one fewer dependency to license-check and
to keep working on Windows with Smart App Control on.

The fetch itself goes through a plain, synchronous HTTP GET with the
product's one honest User-Agent (``config.USER_AGENT``) — never through the
browser (robots.txt is a machine contract, not a page a human reads) and
never with a spoofed identity.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from . import config


@dataclass(frozen=True, slots=True)
class RobotsVerdict:
    allowed: bool
    reason: str


def _robots_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/robots.txt"


def _fetch(url: str, *, timeout: float) -> tuple[int, list[str]]:
    """Returns (http_status, body_lines). Raises only on a genuine network
    failure (no response at all) — an HTTP error status is a valid,
    informative response and must reach the caller as one, not as an
    exception: ``urlopen`` raises ``HTTPError`` (a ``URLError`` subclass) for
    4xx/5xx, which would otherwise be indistinguishable from "unreachable"
    and turn a plain 404 (no robots.txt published) into fail-closed."""
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — http(s) only, see caller
            status = int(getattr(resp, "status", 200) or 200)
            body = resp.read(2_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        try:
            body = exc.read(2_000_000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
    return status, body.splitlines()


class RobotsChecker:
    """One instance per run: memoizes a verdict per (host, path) so a page
    visited twice in one run does not refetch robots.txt."""

    def __init__(self, *, timeout: float = config.ROBOTS_FETCH_TIMEOUT_S,
                 fetch=_fetch, clock=time.time):
        self._timeout = timeout
        self._fetch = fetch
        self._clock = clock
        self._parsers: dict[str, RobotFileParser | None] = {}
        self._cache: dict[tuple[str, str], RobotsVerdict] = {}

    def _parser_for(self, url: str) -> RobotFileParser | None:
        """None means "robots.txt does not restrict this host" (absent /
        4xx). A parser with rules means restrictions apply."""
        parsed = urlparse(url)
        host_key = f"{parsed.scheme}://{parsed.netloc}"
        if host_key in self._parsers:
            return self._parsers[host_key]
        robots_url = _robots_url(url)
        parser: RobotFileParser | None
        try:
            status, lines = self._fetch(robots_url, timeout=self._timeout)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            # Fail-closed: we could not learn the rules, so we assume the
            # strictest possible one (nothing allowed) rather than guess.
            parser = RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
            self._parsers[host_key] = parser
            return parser
        if status in (401, 403):
            # Explicit refusal to even show us the rules: fail-closed.
            parser = RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
        elif 400 <= status < 500:
            # No robots.txt published (404/410/…): the standard convention
            # is "everything allowed" — matches RobotFileParser.read()'s own
            # behaviour for 4xx, so we mirror it explicitly here.
            parser = None
        elif 200 <= status < 300:
            parser = RobotFileParser()
            parser.parse(lines)
        else:
            # 5xx (or any other unexpected status): we cannot tell what the
            # rules are — fail-closed, the same as an unreachable server.
            parser = RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
        self._parsers[host_key] = parser
        return parser

    def allows(self, url: str) -> RobotsVerdict:
        parsed = urlparse(url)
        key = (f"{parsed.scheme}://{parsed.netloc}", parsed.path or "/")
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        parser = self._parser_for(url)
        if parser is None:
            verdict = RobotsVerdict(True, "no robots.txt published (4xx) — allowed by convention")
        else:
            # RobotFileParser.can_fetch already falls back to the "*" record
            # when no rule names our own user agent; querying it twice with
            # "*" and OR-ing the results would let a targeted disallow for
            # our name be overridden by a permissive wildcard, which is
            # backwards — the more specific rule must win.
            ok = parser.can_fetch(config.USER_AGENT, url)
            verdict = RobotsVerdict(
                ok, "allowed by robots.txt" if ok else "disallowed by robots.txt")
        self._cache[key] = verdict
        return verdict
