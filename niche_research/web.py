"""Polite reader of PUBLIC web pages + a search interface.

Rules enforced here (owner spec 24.10):
  * http/https only; private/loopback hosts refused unless ``allow_private`` (tests only);
  * robots.txt is fetched and obeyed for our User-Agent; robots 401/403 or unreachable = do not fetch;
  * >= 5 s between requests to the same host, persisted in the DB so it holds across ticks/processes;
  * clear User-Agent ``BossmanNicheResearch/1.0``; no cookies, no login, no CAPTCHA/paywall bypass — a 401/403/
    429 or a page that looks like a CAPTCHA/login/paywall is recorded as blocked and NOT retried;
  * nothing personal is stored: only short quotes, passed through ``strip_pii``;
  * every fetch (ok or not) writes a learning record (URL, date, kind, task) to ``research/learning.jsonl``.
Search failures are returned as an empty result with a reason; the caller records «неизвестно», never invents.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Protocol

from niche_research import USER_AGENT
from niche_research.pii import strip_pii

UA_TOKEN = "BossmanNicheResearch"
MIN_HOST_INTERVAL = 5.0
MAX_BYTES = 1_500_000
# Visible-text markers count only on short pages (a contact form with reCAPTCHA on a normal page is not a wall).
_TEXT_BLOCK_MARKERS = ("captcha", "are you a robot", "не робот", "subscribe to continue", "войдите, чтобы",
                       "log in to continue", "paywall")
_RAW_BLOCK_MARKERS = ("anomaly-modal", "cf-chl-", "cf_chl_")
_TITLE_BLOCK = ("just a moment", "attention required", "access denied", "captcha")


def iso_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


@dataclass
class FetchResult:
    url: str
    ok: bool
    status: int = 0
    title: str = ""
    text: str = ""
    raw: str = ""
    error: str = ""
    fetched_at: float = 0.0


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""


@dataclass
class SearchResult:
    query: str
    hits: list[SearchHit] = field(default_factory=list)
    error: str = ""


class _TextExtractor(HTMLParser):
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
        if tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "section", "article"):
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
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML still yields partial text
        pass
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return p.title.strip(), text, p.has_password


Transport = Callable[[str, dict, float], tuple[int, dict, bytes]]


MAX_REDIRECTS = 3
_REDIRECTS = (301, 302, 303, 307, 308)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Security audit 24.10: urlopen followed redirects silently, so a public page could 302 the fetcher to
    127.0.0.1 / 169.254.169.254 past the public-host and robots checks. 3xx now comes back to PoliteFetcher,
    which validates every hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urllib_transport(url: str, headers: dict, timeout: float) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), b""


class ResearchLog:
    """Learning records: DB row + one JSON line in ``research/learning.jsonl`` per record."""

    def __init__(self, store, run_dir: Path, clock: Callable[[], float] = time.time):
        self.store = store
        self.path = Path(run_dir) / "research" / "learning.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock

    def record(self, *, url: str, kind: str, text: str, task_id: int | None) -> None:
        now = self.clock()
        text = strip_pii(text)[:1000]
        self.store.add_learning(url=url, kind=kind, text=text, task_id=task_id, now=now)
        line = {"date": iso_date(now), "ts": now, "task_id": task_id, "url": url, "kind": kind, "text": text}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")


class PoliteFetcher:
    def __init__(self, store, research_log: ResearchLog | None = None, *, clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep, transport: Transport | None = None,
                 min_interval: float = MIN_HOST_INTERVAL, allow_private: bool = False, timeout: float = 20):
        self.store = store
        self.log = research_log
        self.clock = clock
        self.sleeper = sleeper
        self.transport = transport or urllib_transport
        self.min_interval = max(MIN_HOST_INTERVAL, float(min_interval))
        self.allow_private = allow_private
        self.timeout = timeout
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.requests: list[str] = []

    # ---- guards ----------------------------------------------------------------------------------------------
    def _host_is_public(self, host: str) -> bool:
        if self.allow_private:
            return True
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True

    def _wait_for_host(self, netloc: str) -> None:
        last = self.store.host_last_fetch(netloc)
        now = self.clock()
        if last is not None and now - last < self.min_interval:
            self.sleeper(self.min_interval - (now - last))
        self.store.set_host_last_fetch(netloc, self.clock())

    def _request(self, url: str) -> tuple[int, dict, bytes]:
        netloc = urllib.parse.urlsplit(url).netloc.lower()
        self._wait_for_host(netloc)
        self.requests.append(url)
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9", "Accept-Language": "ru,en;q=0.8"}
        return self.transport(url, headers, self.timeout)

    def robots_allows(self, url: str) -> tuple[bool, str]:
        parts = urllib.parse.urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc.lower()}"
        if base not in self._robots:
            target = base + "/robots.txt"
            try:
                for _ in range(MAX_REDIRECTS + 1):
                    status, headers, body = self._request(target)
                    if status not in _REDIRECTS:
                        break
                    nxt = urllib.parse.urljoin(target, _location(headers))
                    if not _location(headers) or self._url_refusal(nxt):
                        status = 0                    # redirect to nowhere / a non-public host = unreachable
                        break
                    target = nxt
                else:
                    status = 0
            except OSError as exc:
                self._robots[base] = None
                return False, f"robots.txt unreachable ({type(exc).__name__})"
            if status == 0 or status in _REDIRECTS:
                self._robots[base] = None
                return False, "robots.txt unreachable (redirect refused)"
            rp = urllib.robotparser.RobotFileParser()
            if status in (401, 403):
                rp.disallow_all = True
            elif status >= 400:
                rp.allow_all = True
            else:
                rp.parse(body.decode("utf-8", "replace").splitlines())
            self._robots[base] = rp
        rp = self._robots[base]
        if rp is None:
            return False, "robots.txt unreachable"
        if not rp.can_fetch(UA_TOKEN, url):
            return False, "disallowed by robots.txt"
        return True, ""

    # ---- fetch -----------------------------------------------------------------------------------------------
    def fetch(self, url: str, task_id: int | None = None) -> FetchResult:
        res = self._fetch(url)
        if self.log is not None:
            if res.ok:
                self.log.record(url=url, kind="observation", task_id=task_id,
                                text=f"страница прочитана {iso_date(res.fetched_at)}: {res.title or '(без заголовка)'}")
            else:
                self.log.record(url=url, kind="unknown", task_id=task_id,
                                text=f"не прочитано: {res.error}")
        return res

    def _url_refusal(self, url: str) -> str:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return "only public http(s) URLs"
        if parts.username or parts.password:
            return "credentials in URL refused"
        if not self._host_is_public(parts.hostname):
            return "non-public host refused"
        return ""

    def _fetch(self, url: str) -> FetchResult:
        now = self.clock()
        why = self._url_refusal(url)
        if why:
            return FetchResult(url, False, error=why, fetched_at=now)
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            allowed, why = self.robots_allows(current)          # robots for EVERY hop, not only the first URL
            if not allowed:
                return FetchResult(url, False, error=why, fetched_at=self.clock())
            try:
                status, headers, body = self._request(current)
            except OSError as exc:
                return FetchResult(url, False, error=f"network error {type(exc).__name__}", fetched_at=self.clock())
            if status not in _REDIRECTS:
                break
            nxt = urllib.parse.urljoin(current, _location(headers))
            why = self._url_refusal(nxt) if _location(headers) else "redirect without Location"
            if why:
                return FetchResult(url, False, status, error=f"redirect refused: {why}", fetched_at=self.clock())
            current = nxt
        else:
            return FetchResult(url, False, error=f"more than {MAX_REDIRECTS} redirects", fetched_at=self.clock())
        fetched_at = self.clock()
        if status in (401, 402, 403, 407, 429, 451):
            return FetchResult(url, False, status, error=f"HTTP {status}: access restricted, not bypassed",
                               fetched_at=fetched_at)
        if status >= 400:
            return FetchResult(url, False, status, error=f"HTTP {status}", fetched_at=fetched_at)
        ctype = {k.lower(): v for k, v in headers.items()}.get("content-type", "text/html").lower()
        if not ("html" in ctype or "text/plain" in ctype):
            return FetchResult(url, False, status, error=f"unsupported content-type {ctype}", fetched_at=fetched_at)
        if len(body) > MAX_BYTES:
            body = body[:MAX_BYTES]
        charset = "utf-8"
        m = re.search(r"charset=([\w\-]+)", ctype)
        if m:
            charset = m.group(1)
        try:
            raw = body.decode(charset, "replace")
        except LookupError:
            raw = body.decode("utf-8", "replace")
        title, text, has_password = html_to_text(raw) if "html" in ctype else ("", raw, False)
        low_raw, low_text, low_title = raw[:200_000].lower(), text.lower(), title.lower()
        walled = (has_password or any(m in low_raw for m in _RAW_BLOCK_MARKERS)
                  or any(m in low_title for m in _TITLE_BLOCK)
                  or (len(text) < 2000 and any(m in low_text for m in _TEXT_BLOCK_MARKERS)))
        if walled:
            return FetchResult(url, False, status, title=title,
                               error="captcha/login/paywall detected: not bypassed", fetched_at=fetched_at)
        return FetchResult(url, True, status, title=strip_pii(title), text=text[:60_000], raw=raw,
                           fetched_at=fetched_at)


def _location(headers: dict) -> str:
    return {k.lower(): v for k, v in (headers or {}).items()}.get("location", "")


class SearchProvider(Protocol):
    def search(self, query: str, task_id: int | None = None) -> SearchResult: ...


class DuckDuckGoHtmlSearch:
    """Login-free search via html.duckduckgo.com through the polite fetcher (robots + rate limit apply)."""

    ENDPOINT = "https://html.duckduckgo.com/html/?q="

    def __init__(self, fetcher: PoliteFetcher, max_results: int = 5):
        self.fetcher = fetcher
        self.max_results = max_results

    def search(self, query: str, task_id: int | None = None) -> SearchResult:
        res = self.fetcher.fetch(self.ENDPOINT + urllib.parse.quote_plus(query), task_id=task_id)
        if not res.ok:
            return SearchResult(query, [], error=res.error or "search failed")
        hits = parse_ddg_html(res.raw)[: self.max_results]
        if not hits:
            return SearchResult(query, [], error="no results parsed")
        return SearchResult(query, hits)


_DDG_LINK = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DDG_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)


def parse_ddg_html(html: str) -> list[SearchHit]:
    hits: list[SearchHit] = []
    snippets = [_strip_tags(s) for s in _DDG_SNIPPET.findall(html)]
    for i, (href, title) in enumerate(_DDG_LINK.findall(html)):
        href = href.replace("&amp;", "&")
        if href.startswith("//"):
            href = "https:" + href
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
        target = q.get("uddg", [href])[0]
        if "duckduckgo.com/y.js" in target or not target.startswith("http"):
            continue  # ads / internal links
        hits.append(SearchHit(_strip_tags(title), target, snippets[i] if i < len(snippets) else ""))
    return hits


def _strip_tags(s: str) -> str:
    _, text, _ = html_to_text(s)
    return strip_pii(text.strip())


class NullSearch:
    """Used when search is disabled: always an honest failure."""

    def search(self, query: str, task_id: int | None = None) -> SearchResult:
        return SearchResult(query, [], error="search disabled")
