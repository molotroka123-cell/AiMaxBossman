"""Bounded open-news skill: reviewed upstream processing, explicit RSS acquisition.

This is a supported subset, not the upstream crawler/CLI/streaming runtime.
No import-time I/O, package installation, model request, or background worker.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from ._vendor.summarizer import summarize_text
from ._vendor.token_filter import filter_articles

UPSTREAM_SHA = "ebb0e9b4deb0bf8fa8983e6324276a51f091ab43"
ENDPOINT = "https://news.google.com/rss/search"
MAX_FEED_BYTES = 1_048_576
MAX_ARTICLES = 40
MAX_TEXT_CHARS = 100_000
MAX_RESULT_CHARS = 30_000
TIMEOUT_SECONDS = 20.0
TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "igshid", "ref", "ref_src", "ito", "smid",
})
MODES = ("any", "all", "exact_phrase")
FIELDS = {"url": 2048, "title": 500, "description": 4000, "text": 8000,
          "source": 200, "published_at": 100}


class NewsInputError(ValueError):
    """Invalid caller or feed input; never an implicit network permission."""


class NewsFetchError(RuntimeError):
    """A named acquisition failure, not an empty successful news result."""


def network_blocked() -> bool:
    return os.environ.get("BOSSMAN_OFFLINE_MODE_ENABLED", "").strip().lower() in {
        "1", "true", "yes",
    }


def _integer(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise NewsInputError(f"{name}: integer in [{low}, {high}] required")
    return value


def _text(value: object, name: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise NewsInputError(f"{name}: text of at most {maximum} characters required")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise NewsInputError(f"{name}: control characters are not accepted")
    result = value.strip()
    if required and not result:
        raise NewsInputError(f"{name}: non-empty text required")
    return result


def _keys(args: dict, allowed: set[str]) -> None:
    if not isinstance(args, dict) or set(args) - allowed:
        raise NewsInputError("unsupported arguments; approval, headers, URLs and streaming cannot be injected")


def public_reference(url: str) -> str:
    """Validate a displayed reference, without DNS lookup or opening the URL."""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        if (parsed.scheme not in {"http", "https"} or not host or parsed.username
                or parsed.password or parsed.port not in (None, 80, 443)
                or "\\" in url or any(c.isspace() for c in url)):
            raise ValueError
        if host.endswith((".localhost", ".local", ".internal", ".test", ".invalid")) or host == "localhost":
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or re.fullmatch(r"[a-z0-9.-]+", host) is None:
                raise ValueError
        else:
            if not address.is_global:
                raise ValueError
    except (ValueError, UnicodeError):
        raise NewsInputError("article URL must be a public HTTP(S) reference without credentials") from None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _url_key(url: str) -> str:
    """Offline exact dedupe, based on upstream tracking-parameter normalization.

    Deliberately does NOT import upstream dedupe: its Google News resolver
    performs network requests. Do not merge same-title stories across outlets.
    """
    parsed = urlsplit(url)
    host = parsed.netloc.lower().removeprefix("www.")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parsed.query)
                             if k.lower() not in TRACKING_PARAMS))
    return urlunsplit(("", host, parsed.path.rstrip("/"), query, ""))


class _PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain(value: str) -> str:
    parser = _PlainHTML()
    parser.feed(value)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def process_articles(args: dict) -> dict:
    """Run actual upstream token filter and extractive summarizer, entirely offline."""
    _keys(args, {"articles", "query", "query_mode", "exclude_terms", "limit", "sentence_count"})
    articles = args.get("articles")
    if not isinstance(articles, list) or len(articles) > MAX_ARTICLES:
        raise NewsInputError(f"articles: list with at most {MAX_ARTICLES} records required")
    query = _text(args.get("query", ""), "query", 240)
    mode = args.get("query_mode", "any")
    if mode not in MODES:
        raise NewsInputError("query_mode: any, all or exact_phrase required")
    exclusions = args.get("exclude_terms", [])
    if not isinstance(exclusions, list) or len(exclusions) > 10:
        raise NewsInputError("exclude_terms: at most 10 terms required")
    exclusions = [_text(v, "exclude_terms", 80, required=True) for v in exclusions]
    limit = _integer(args.get("limit", 10), "limit", 1, 20)
    sentences = _integer(args.get("sentence_count", 3), "sentence_count", 1, 5)
    clean: list[dict] = []
    total = 0
    for record in articles:
        if not isinstance(record, dict) or set(record) - set(FIELDS):
            raise NewsInputError("article: only url, title, description, text, source, published_at accepted")
        row = {key: _text(record.get(key, ""), key, cap, required=key == "url")
               for key, cap in FIELDS.items()}
        row["url"] = public_reference(row["url"])
        if not (row["title"] or row["description"] or row["text"]):
            raise NewsInputError("article: missing all searchable text")
        total += sum(len(v) for v in row.values())
        if total > MAX_TEXT_CHARS:
            raise NewsInputError("article batch exceeds total text budget")
        clean.append(row)
    filtered = filter_articles(clean, query=query, query_mode=mode,
                               exclude_terms=exclusions, search_in=["title", "description", "body"])
    seen: set[str] = set()
    unique = []
    for row in filtered:
        key = _url_key(row["url"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    out = []
    clipped = False
    for row in unique[:limit]:
        text = row["text"] or row["description"] or row["title"]
        summary = summarize_text(text, sentences)
        short = summary[:1200]
        clipped |= len(summary) > len(short)
        out.append({"url": row["url"], "title": row["title"], "source": row["source"],
                    "published_at": row["published_at"] or None, "summary": short,
                    "summary_truncated": len(summary) > len(short),
                    "input_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "article_fetched": False, "original_url_resolved": False})
    while out and len(json.dumps(out, ensure_ascii=False)) > MAX_RESULT_CHARS:
        out.pop()
        clipped = True
    return {"status": "PROCESSED_INPUT", "upstream_sha": UPSTREAM_SHA,
            "processed_at": datetime.now(timezone.utc).isoformat(), "network_requests": 0,
            "input_count": len(articles), "filtered_count": len(filtered),
            "unique_count": len(unique), "results": out,
            "truncated": clipped or len(unique) > limit,
            "freshness_verified": False, "source_kind": "supplied_text",
            "summary_method": "open-news extractive; Latin scoring, other scripts use leading sentences",
            "untrusted_content": True}


def search_parameters(args: dict) -> dict[str, str]:
    _keys(args, {"query", "query_mode", "language", "country", "time_limit", "limit"})
    query = _text(args.get("query"), "query", 240, required=True)
    if "\n" in query or "\r" in query or "\t" in query:
        raise NewsInputError("query must be a single line")
    mode = args.get("query_mode", "any")
    if mode not in MODES:
        raise NewsInputError("invalid query_mode")
    language = args.get("language", "en")
    country = args.get("country", "US")
    if not isinstance(language, str) or re.fullmatch(r"[a-z]{2}", language) is None:
        raise NewsInputError("language: two lowercase letters required")
    if not isinstance(country, str) or re.fullmatch(r"[A-Z]{2}", country) is None:
        raise NewsInputError("country: two uppercase letters required")
    recency = args.get("time_limit", "d")
    if recency not in ("d", "w", "m"):
        raise NewsInputError("time_limit: d, w or m required")
    _integer(args.get("limit", 10), "limit", 1, 20)
    if mode == "exact_phrase":
        query = '"' + query.replace('"', '') + '"'
    elif mode == "all":
        query = " AND ".join(query.split())
    # Adapted from the pinned upstream _build_search_query / _locale_params.
    query += " when:" + {"d": "1d", "w": "7d", "m": "30d"}[recency]
    return {"q": query, "hl": f"{language}-{country}", "gl": country,
            "ceid": f"{country}:{language}"}


def parse_rss(payload: bytes, limit: int) -> tuple[list[dict], int, bool]:
    _integer(limit, "limit", 1, 20)
    if len(payload) > MAX_FEED_BYTES:
        raise NewsFetchError("rss_body_limit")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeError:
        raise NewsFetchError("rss_requires_utf8") from None
    # Only UTF-8, no XML declarations which can reinterpret bytes, entities,
    # local files, external DTDs or expansion attacks. No URL goes to a parser.
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.IGNORECASE) or "\x00" in text:
        raise NewsFetchError("rss_unsafe_xml")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise NewsFetchError("rss_invalid_xml") from None
    if root.tag != "rss" or root.find("channel") is None:
        raise NewsFetchError("rss_wrong_shape")
    items = root.findall("./channel/item")
    records = []
    discarded = 0
    total = 0
    input_clipped = False
    for item in items[:MAX_ARTICLES]:
        def field(key, maximum):
            return _plain(item.findtext(key) or "")[:maximum]
        try:
            url = _text(item.findtext("link") or "", "link", FIELDS["url"], required=True)
            url = public_reference(url)
        except NewsInputError:
            discarded += 1
            continue
        row = {"url": url, "title": field("title", 500),
               "description": field("description", 3000), "source": field("source", 200),
               "published_at": field("pubDate", 100)}
        if not (row["title"] or row["description"]):
            discarded += 1
            continue
        size = sum(len(value) for value in row.values())
        if total + size > MAX_TEXT_CHARS:
            input_clipped = True
            break
        total += size
        records.append(row)
    return records, discarded, input_clipped or len(items) > MAX_ARTICLES


async def search_news(args: dict) -> dict:
    """One RSS GET, after canonical tool approval; never fetch returned article URLs.

    No transport/endpoint/approved input is exposed to the model. Tests patch
    httpx.AsyncClient with MockTransport, not a live web or model invocation.
    """
    params = search_parameters(args)
    if network_blocked():
        raise NewsFetchError("offline_mode_blocks_news_search")
    import httpx
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=10.0, trust_env=False, follow_redirects=False,
                                         headers={"Accept": "application/rss+xml, application/xml",
                                                  "Accept-Encoding": "identity",
                                                  "User-Agent": "Bossman-OpenNews/1.0"}) as client:
                if network_blocked():
                    raise NewsFetchError("offline_mode_blocks_news_search")
                async with client.stream("GET", ENDPOINT, params=params) as response:
                    if response.status_code != 200:
                        raise NewsFetchError(f"rss_http_{response.status_code}")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise NewsFetchError("rss_compressed_response_refused")
                    mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if mime not in {"application/rss+xml", "application/xml", "text/xml"}:
                        raise NewsFetchError("rss_wrong_content_type")
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=16384):
                        if network_blocked():
                            raise NewsFetchError("offline_mode_revoked_during_fetch")
                        if len(chunks) + len(chunk) > MAX_FEED_BYTES:
                            raise NewsFetchError("rss_body_limit")
                        chunks.extend(chunk)
    except (httpx.HTTPError, TimeoutError):
        raise NewsFetchError("rss_transport_failed_or_timed_out") from None
    records, discarded, feed_truncated = parse_rss(bytes(chunks), args.get("limit", 10))
    report = process_articles({"articles": records, "limit": args.get("limit", 10)})
    # The endpoint has executed, but returned dates and claims remain unverified.
    report.update(status=("FETCHED_RSS" if report["results"] else
                          "NO_USABLE_RESULTS" if discarded else "NO_RESULTS"),
                  source_kind="google_news_rss", network_requests=1,
                  fetched_at=datetime.now(timezone.utc).isoformat(),
                  discarded_entries=discarded, feed_truncated=feed_truncated,
                  feed_sha256=hashlib.sha256(chunks).hexdigest())
    report["truncated"] = report["truncated"] or feed_truncated
    return report
