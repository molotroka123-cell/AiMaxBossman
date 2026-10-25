"""Polite public-web reader + learning journal for the main Bossman (security/internet audit 24.10).

Before this module the main Bossman read the web only through the String MCP service (a remote fetcher whose
politeness we cannot check, and whose audit line keeps the domain only) or the disabled OSIRIS web_research. There
was no durable record of WHAT was read and WHAT was concluded from it. The pattern is ported from
`niche_research/web.py` (PoliteFetcher + ResearchLog), with the redirect hole of that fetcher closed:

  * http/https only, `policy.check_public_url` (no credentials, no bare IPs, no local names, deny-list) and every
    resolved address must be public — on EVERY redirect hop (max 3), robots.txt is checked for every hop too;
  * robots.txt obeyed for our User-Agent; robots 401/403 or unreachable = do not fetch (fail-closed); 404 = allowed;
  * >= 5 s between requests to the same host; clear User-Agent; no cookies, no login, no CAPTCHA/paywall bypass —
    401/402/403/407/429/451 or a page that looks like a login/CAPTCHA/paywall is recorded as blocked, not retried;
  * every read (ok or not) writes a learning record; conclusions/assumptions are recorded against a URL that was
    actually read. Records keep the URL without query/fragment, a UTC date, a kind
    (fact | conclusion | assumption | unknown) and a short PII-stripped text. Page bodies are never stored.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

from . import policy
from .policy import PolicyRefusal

USER_AGENT = "BossmanWebReader/1.0 (+owner PC; polite; obeys robots.txt)"
UA_TOKEN = "BossmanWebReader"
MIN_HOST_INTERVAL = 5.0
MAX_BYTES = 1_500_000
MAX_REDIRECTS = 3
KINDS = ("fact", "conclusion", "assumption", "unknown")
_BLOCKED_STATUS = (401, 402, 403, 407, 429, 451)
_TEXT_BLOCK = ("captcha", "are you a robot", "не робот", "subscribe to continue", "войдите, чтобы",
               "log in to continue", "paywall")
_RAW_BLOCK = ("anomaly-modal", "cf-chl-", "cf_chl_")
_TITLE_BLOCK = ("just a moment", "attention required", "access denied", "captcha")

# --- PII strip: same rules as niche_research/pii.py (emails, phone runs of >= 9 digits, Telegram handles) ---
REDACTED = "[скрыто]"
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w])\+?\d[\d\s\-().]{7,}\d(?![\w])")
_TG = re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{4,31}\b")


def strip_pii(text: str) -> str:
    if not text:
        return text or ""
    text = _EMAIL.sub(REDACTED, text)
    text = _PHONE.sub(lambda m: REDACTED if sum(c.isdigit() for c in m.group(0)) >= 9 else m.group(0), text)
    return _TG.sub(REDACTED, text)


def clean_url(url: str) -> str:
    """URL as stored in a record: no userinfo, query or fragment (they can carry tokens or personal data)."""
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    netloc = host + (f":{p.port}" if p.port else "")
    return urllib.parse.urlunsplit((p.scheme, netloc, p.path or "/", "", ""))


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LearningJournal:
    """One JSON line per learning record in ``<data_dir>/web_access/learning.jsonl``."""

    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.Lock()

    def record(self, *, url: str, kind: str, text: str, source: str, status: str = "ok",
               title: str = "", task_id: int | None = None) -> dict:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        now = self.clock()
        rec = {"id": uuid.uuid4().hex[:12], "ts": round(now, 3), "date": _iso(now), "url": clean_url(url),
               "domain": (urllib.parse.urlsplit(url).hostname or "-").lower(), "kind": kind, "status": status,
               "source": source, "title": strip_pii(title)[:200], "text": strip_pii(policy.redact(text))[:1000],
               "task_id": task_id}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def records(self, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out[-max(1, min(int(limit), 500)):]

    def was_read(self, url: str) -> bool:
        target = clean_url(url)
        return any(r.get("url") == target and r.get("kind") == "fact" and r.get("status") == "ok"
                   for r in self.records(500))


class _Text(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self.has_password = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "input" and any(k == "type" and (v or "").lower() == "password" for k, v in attrs):
            self.has_password = True
        if tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "section", "article"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str, bool]:
    p = _Text()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML still yields partial text
        pass
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    return p.title.strip(), re.sub(r"\n\s*\n+", "\n", text).strip(), p.has_password


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        return None                       # 3xx is returned to us; every hop is validated by the reader


_OPENER = urllib.request.build_opener(_NoRedirect)
Transport = Callable[[str, dict, float], tuple[int, dict, bytes]]


def urllib_transport(url: str, headers: dict, timeout: float) -> tuple[int, dict, bytes]:
    try:
        with _OPENER.open(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:          # 3xx (no redirect followed) and 4xx/5xx land here
        return exc.code, dict(exc.headers or {}), b""


def _public_dns(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified or (ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"))):
            return False
    return bool(infos)


@dataclass
class ReadResult:
    url: str
    ok: bool
    status: int = 0
    final_url: str = ""
    title: str = ""
    text: str = ""
    error: str = ""
    record_id: str = ""


class PoliteReader:
    def __init__(self, journal: LearningJournal | None, *, transport: Transport | None = None,
                 resolver: Callable[[str], bool] = _public_dns, clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep, min_interval: float = MIN_HOST_INTERVAL,
                 timeout: float = 20.0):
        self.journal = journal
        self.transport = transport or urllib_transport
        self.resolver = resolver
        self.clock = clock
        self.sleeper = sleeper
        self.min_interval = max(MIN_HOST_INTERVAL, float(min_interval))
        self.timeout = timeout
        self._last: dict[str, float] = {}
        self._robots: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}
        self._lock = threading.Lock()
        self.requests: list[str] = []

    # ---- guards ------------------------------------------------------------------------------------------
    def refusal(self, url: str) -> str:
        try:
            policy.check_public_url(url)
        except PolicyRefusal as exc:
            return str(exc)
        if not self.resolver(urllib.parse.urlsplit(url).hostname or ""):
            return "host does not resolve to a public address"
        return ""

    def _request(self, url: str) -> tuple[int, dict, bytes]:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        with self._lock:
            last = self._last.get(host)
            now = self.clock()
            if last is not None and now - last < self.min_interval:
                self.sleeper(self.min_interval - (now - last))
            self._last[host] = self.clock()
        self.requests.append(url)
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9", "Accept-Language": "ru,en;q=0.8"}
        return self.transport(url, headers, self.timeout)

    def robots_allows(self, url: str) -> tuple[bool, str]:
        parts = urllib.parse.urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc.lower()}"
        cached = self._robots.get(base)
        if cached is None or self.clock() - cached[0] > 1800:
            rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            target, status, body = base + "/robots.txt", 0, b""
            try:
                for _ in range(MAX_REDIRECTS + 1):
                    status, headers, body = self._request(target)
                    if status not in (301, 302, 303, 307, 308):
                        break
                    loc = {k.lower(): v for k, v in headers.items()}.get("location", "")
                    target = urllib.parse.urljoin(target, loc)
                    if not loc or self.refusal(target):
                        status = 0              # redirect to nowhere / to a non-public host: treat as unreachable
                        break
                else:
                    status = 0
            except OSError:
                status = 0
            if status == 0:
                rp = None
            elif status in (401, 403):
                rp.disallow_all = True
            elif status >= 400:
                rp.allow_all = True
            elif 300 <= status < 400:
                rp = None
            else:
                rp.parse(body.decode("utf-8", "replace").splitlines())
            self._robots[base] = (self.clock(), rp)
        rp = self._robots[base][1]
        if rp is None:
            return False, "robots.txt unreachable: not fetched (fail-closed)"
        if not rp.can_fetch(UA_TOKEN, url):
            return False, "disallowed by robots.txt"
        return True, ""

    # ---- read --------------------------------------------------------------------------------------------
    def read(self, url: str, *, source: str = "web.read", task_id: int | None = None) -> ReadResult:
        res = self._read(str(url or "").strip())
        if self.journal is not None:
            if res.ok:
                rec = self.journal.record(url=res.final_url or res.url, kind="fact", status="ok", source=source,
                                          title=res.title, task_id=task_id,
                                          text=f"страница прочитана: {res.title or '(без заголовка)'}; "
                                               f"{len(res.text)} симв. текста; начало: {res.text[:200]}")
            else:
                rec = self.journal.record(url=res.url, kind="unknown", status="not_read", source=source,
                                          task_id=task_id, text=f"не прочитано: {res.error}")
            res.record_id = rec["id"]
        return res

    def _read(self, url: str) -> ReadResult:
        why = self.refusal(url)
        if why:
            return ReadResult(url, False, error=f"refused: {why}")
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            allowed, why = self.robots_allows(current)
            if not allowed:
                return ReadResult(url, False, final_url=current, error=why)
            try:
                status, headers, body = self._request(current)
            except OSError as exc:
                return ReadResult(url, False, final_url=current, error=f"network error {type(exc).__name__}")
            if status in (301, 302, 303, 307, 308):
                loc = {k.lower(): v for k, v in headers.items()}.get("location", "")
                nxt = urllib.parse.urljoin(current, loc)
                why = self.refusal(nxt) if loc else "redirect without Location"
                if why:
                    return ReadResult(url, False, status, final_url=current, error=f"redirect refused: {why}")
                current = nxt
                continue
            return self._page(url, current, status, headers, body)
        return ReadResult(url, False, final_url=current, error=f"more than {MAX_REDIRECTS} redirects")

    def _page(self, url: str, final: str, status: int, headers: dict, body: bytes) -> ReadResult:
        if status in _BLOCKED_STATUS:
            return ReadResult(url, False, status, final, error=f"HTTP {status}: access restricted, not bypassed")
        if status >= 400 or status < 200:
            return ReadResult(url, False, status, final, error=f"HTTP {status}")
        ctype = {k.lower(): v for k, v in headers.items()}.get("content-type", "text/html").lower()
        if not ("html" in ctype or "text/plain" in ctype):
            return ReadResult(url, False, status, final, error=f"unsupported content-type {ctype[:60]}")
        m = re.search(r"charset=([\w\-]+)", ctype)
        try:
            raw = body[:MAX_BYTES].decode(m.group(1) if m else "utf-8", "replace")
        except LookupError:
            raw = body[:MAX_BYTES].decode("utf-8", "replace")
        title, text, has_password = html_to_text(raw) if "html" in ctype else ("", raw, False)
        low_raw, low_text, low_title = raw[:200_000].lower(), text.lower(), title.lower()
        if (has_password or any(x in low_raw for x in _RAW_BLOCK) or any(x in low_title for x in _TITLE_BLOCK)
                or (len(text) < 2000 and any(x in low_text for x in _TEXT_BLOCK))):
            return ReadResult(url, False, status, final, title=strip_pii(title),
                              error="captcha/login/paywall detected: not bypassed")
        return ReadResult(url, True, status, final, title=strip_pii(title), text=text[:60_000])
