"""Prozorro (Ukraine public procurement), official public read-only OpenProcurement API 2.5, no key.

Feed:    GET https://public.api.openprocurement.org/api/2.5/tenders?offset=<cursor>&limit=<n>&opt_fields=<a,b,...>
         -> {"data": [{id, dateModified, <opt_fields>}...], "next_page": {"offset": "<cursor>", ...}, "prev_page": {...}}
         Ascending order by dateModified (descending=1 reverses it). `offset` accepts an epoch timestamp for the first call;
         every answer hands back the opaque cursor of the next page. An empty `data` = caught up (cursor stays usable).
Details: GET https://public.api.openprocurement.org/api/2.5/tenders/<id> -> {"data": {...full tender...}}
Verified live 2026-10-21 (shape above). Public page of a tender: https://prozorro.gov.ua/tender/<tenderID>.

Incremental sync: the feed cursor is stored by the caller after each page, so an interrupted run resumes at the first
unfinished page; a tender met twice is fetched once per run and the store deduplicates by tenderID (changed => new version).
The feed has no title/CPV, so biddable candidates (and tenders already stored) get one detail request each; only
relevant ones (defence/security/dual-use CPV or keyword, same word lists as the gates) are kept.
Politeness: identified User-Agent (http.UA), <= 2 requests/s, exponential backoff on 429/5xx/timeouts (Retry-After honoured).
Privacy: contact persons, e-mails, phones and documents are dropped before anything is stored; buyer = public entity + EDRPOU.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta, timezone

from .. import rules
from ..store import sha
from .ted import category_of

URL = "https://public.api.openprocurement.org/api/2.5/tenders"
TENDER_URL = "https://prozorro.gov.ua/tender/{}"
API_VERSION = "2.5"
# The feed honours only a whitelist of opt_fields (verified 2026-10-21: title, items, value, mainProcurementCategory are
# silently dropped), so relevance is decided on the detail record; the feed is used to skip what cannot be bid on.
FEED_FIELDS = ("tenderID", "status", "procurementMethodType", "tenderPeriod")
NOT_BIDDABLE_METHODS = {"reporting"}                         # direct-award reports: nothing to bid on
PAGE_SIZE = 100
MAX_PAGES = 20                     # per run: <= 2000 feed entries
MAX_DETAILS = 300                  # per run: detail requests (checked between pages; ~2.5 min at 2 req/s)
DEFAULT_DAYS = 1                   # first run without a cursor: one day back (Prozorro has tens of thousands of changes a day)

# CPV division 35 = security, fire-fighting, police and defence equipment: the prefix the terminal already uses for
# security_defence (ted.CATEGORY / ted.DEFAULT_CPV); 353..357 (rules.CONTROLLED_CPV_PREFIXES) are inside it.
RELEVANT_CPV_PREFIXES = ("35",)
UA_WORDS = ("дрон", "бпла", "безпілотн", "радар", "рлс", "реб", "радіоелектронн", "засоби захисту", "засобів захисту", "бронежилет",
            "бронеплит", "балістичн", "тепловіз", "нічного бачення", "боєприпас", "зброя", "зброї", "вибухов", "подвійного призначення",
            "протидрон", "антидрон", "fpv")
WHOLE_WORDS = frozenset({"реб", "рлс", "uav", "fpv"})        # short tokens: whole word only («ребро», «рлса» are not hits)
KEYWORDS = tuple(dict.fromkeys(w.lower() for w in (*UA_WORDS, *rules.DUAL_USE_WORDS, *rules.CONTROLLED_WORDS)))
_KW_RX = [(w, re.compile(r"(?<!\w)" + re.escape(w) + (r"(?!\w)" if w in WHOLE_WORDS else ""), re.IGNORECASE)) for w in KEYWORDS]

OPEN_STATES = {"active.enquiries", "active.tendering"}
CLOSED_STATES = {"active.pre-qualification", "active.pre-qualification.stand-still", "active.auction", "active.qualification",
                 "active.qualification.stand-still", "active.awarded", "active", "complete", "unsuccessful"}
RETRY_STATUS = {429, 500, 502, 503, 504}
KEEP_TOP = ("id", "tenderID", "title", "title_en", "description", "description_en", "status", "procurementMethod", "procurementMethodType",
            "mainProcurementCategory", "value", "procuringEntity", "items", "lots", "tenderPeriod", "enquiryPeriod", "auctionPeriod",
            "date", "dateCreated", "dateModified")
KEEP_ENTITY = ("name", "name_en", "identifier", "kind")
KEEP_ITEM = ("id", "description", "description_en", "classification", "quantity", "unit", "relatedLot", "deliveryDate")
KEEP_LOT = ("id", "title", "title_en", "value", "status")


class TransportError(RuntimeError):
    """Raised by a transport (or the polite wrapper after its retries) with the HTTP status when there is one."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


def _status_of(exc: Exception) -> tuple[int | None, float | None]:
    status = getattr(exc, "status", None)
    retry_after = getattr(exc, "retry_after", None)
    resp = getattr(exc, "response", None)                   # httpx.HTTPStatusError
    if status is None and resp is not None:
        status = getattr(resp, "status_code", None)
        try:
            retry_after = float(resp.headers.get("Retry-After"))
        except (TypeError, ValueError, AttributeError):
            retry_after = None
    return status, retry_after


def _retryable(exc: Exception, status: int | None) -> bool:
    if status is not None:
        return status in RETRY_STATUS
    return "Timeout" in type(exc).__name__ or isinstance(exc, (TimeoutError, ConnectionError))


def polite(get, *, min_interval: float = 0.5, retries: int = 4, base_delay: float = 1.0, max_delay: float = 60.0,
           sleep=time.sleep, clock=time.monotonic):
    """Wrap get(url, params) -> JSON: at most 1/min_interval requests per second, backoff on 429/5xx/timeouts.
    Any other error (e.g. 404, 400) is raised at once. After `retries` failed retries the last error is raised."""
    last = {"t": None}

    def call(url: str, params: dict | None = None):
        attempt = 0
        while True:
            if last["t"] is not None:
                wait = min_interval - (clock() - last["t"])
                if wait > 0:
                    sleep(wait)
            last["t"] = clock()
            try:
                return get(url, params)
            except Exception as exc:                        # noqa: BLE001 - classified below, re-raised when not retryable
                status, retry_after = _status_of(exc)
                if not _retryable(exc, status) or attempt >= retries:
                    raise
                delay = retry_after if retry_after is not None else base_delay * (2 ** attempt)
                sleep(min(max(delay, 0.0), max_delay))
                attempt += 1
    return call


# ---------------------------------------------------------------- relevance
def _cpv8(code) -> str:
    return str(code or "").split("-")[0].strip()


def cpv_codes(t: dict) -> list[str]:
    codes = [_cpv8((it.get("classification") or {}).get("id")) for it in t.get("items") or []]
    return sorted({c for c in codes if c.isdigit()})


def _texts(t: dict) -> str:
    parts = [t.get("title"), t.get("title_en"), t.get("description"), t.get("description_en")]
    for it in t.get("items") or []:
        parts += [it.get("description"), it.get("description_en"), (it.get("classification") or {}).get("description")]
    for lot in t.get("lots") or []:
        parts += [lot.get("title"), lot.get("title_en")]
    return " \n ".join(str(p) for p in parts if p)


def relevance(t: dict) -> dict | None:
    """Why a tender is relevant (CPV prefixes and keyword hits) or None. The buyer name is NOT used: a military unit
    buying bread is not a defence tender."""
    cpv = [c for c in cpv_codes(t) if c.startswith(RELEVANT_CPV_PREFIXES)]
    text = _texts(t)
    words = [w for w, rx in _KW_RX if rx.search(text)]
    return {"cpv": cpv, "keywords": words} if (cpv or words) else None


# ---------------------------------------------------------------- normalization
def _utc(s) -> str | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _period(p) -> dict:
    p = p or {}
    return {"start": _utc(p.get("startDate")), "end": _utc(p.get("endDate"))}


def status_of(t: dict, today: date) -> str:
    st = str(t.get("status") or "")
    if st == "cancelled":
        return "cancelled"
    if st in OPEN_STATES:
        end = _utc((t.get("tenderPeriod") or {}).get("endDate"))
        if end:
            return "open" if date.fromisoformat(end[:10]) >= today else "closed"
        return "unknown"
    if st in CLOSED_STATES:
        return "closed"
    return "unknown"                                        # draft, unknown future states


def scrub(t: dict) -> dict:
    """Keep the public tender facts only: no contact persons, e-mails, phones, addresses of people, documents, bids."""
    out = {k: t[k] for k in KEEP_TOP if k in t}
    pe = t.get("procuringEntity") or {}
    if pe:
        ent = {k: pe[k] for k in KEEP_ENTITY if k in pe}
        addr = pe.get("address") or {}
        ent["address"] = {k: addr[k] for k in ("locality", "region", "countryName") if k in addr}
        out["procuringEntity"] = ent
    if "items" in t:
        out["items"] = [{k: it[k] for k in KEEP_ITEM if k in it} for it in t.get("items") or []]
    if "lots" in t:
        out["lots"] = [{k: lot[k] for k in KEEP_LOT if k in lot} for lot in t.get("lots") or []]
    return out


def normalize(t: dict, retrieved_at: str, snapshot_sha: str, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    tid = str(t.get("tenderID") or "")
    val = t.get("value") or {}
    try:
        amount = float(val["amount"]) if val.get("amount") is not None else None
    except (TypeError, ValueError):
        amount = None
    if amount is not None and amount <= 0:
        amount = None                                       # 0 = not stated, never a real price
    pe = t.get("procuringEntity") or {}
    ident = pe.get("identifier") or {}
    cpv = cpv_codes(t)
    tp, ep = _period(t.get("tenderPeriod")), _period(t.get("enquiryPeriod"))
    title = str(t.get("title") or "").strip()
    rel = relevance(t) or {"cpv": [], "keywords": []}
    quality = "ok" if (amount is not None and tp["end"]) else "partial"
    return {
        "id": f"prozorro:{tid}", "official_notice_id": tid, "prozorro_id": str(t.get("id") or "") or None,
        "title": title or tid, "title_uk": title or None, "title_en": (str(t.get("title_en") or "").strip() or None), "title_lang": "uk",
        "jurisdiction": "UA", "country": "UA", "category": category_of(cpv), "status": status_of(t, today),
        "deadline": tp["end"], "tender_period": tp, "enquiry_period": ep,
        "estimated_value": amount, "currency": (val.get("currency") or None) if amount is not None else None,
        "value_basis": ("expected value incl. VAT" if val.get("valueAddedTaxIncluded") else "expected value excl. VAT") if amount is not None else None,
        "source_url": TENDER_URL.format(tid), "source_id": "prozorro", "retrieved_at": retrieved_at,
        "published_at": (_utc(t.get("date") or t.get("dateCreated")) or "")[:10] or None,
        "source_version": f"openprocurement {API_VERSION}; dateModified={_utc(t.get('dateModified')) or '-'}; status={t.get('status')}",
        "checksum": sha(json.dumps(scrub(t), sort_keys=True, ensure_ascii=False)), "data_quality": quality,
        "buyer": str(pe.get("name") or ""), "buyer_en": pe.get("name_en") or None,
        "buyer_id": str(ident.get("id") or "") or None, "buyer_id_scheme": ident.get("scheme") or None,
        "cpv": cpv, "procurement_method": t.get("procurementMethodType") or t.get("procurementMethod") or None,
        "procurement_category": t.get("mainProcurementCategory") or None,
        "relevance": rel, "demo": False, "snapshot_sha256": snapshot_sha,
    }


def normalize_all(details: list[dict], retrieved_at: str, snapshot_sha: str, today: date | None = None) -> list[dict]:
    return [normalize(t, retrieved_at, snapshot_sha, today) for t in details if t.get("tenderID")]


# ---------------------------------------------------------------- feed paging
def start_offset(days: int, today: date) -> str:
    since = datetime.combine(today - timedelta(days=days), datetime.min.time(), tzinfo=timezone.utc)
    return str(int(since.timestamp()))


def candidate(entry: dict, known: set | frozenset = frozenset()) -> bool:
    """Feed-level prefilter (the feed has no title/items/CPV, see FEED_FIELDS): a tender already in the store is always
    refreshed (status/deadline changes); a new one only when it is still biddable and not a direct-award report."""
    if entry.get("id") in known:
        return True
    return entry.get("status") in OPEN_STATES and entry.get("procurementMethodType") not in NOT_BIDDABLE_METHODS


def iter_pages(get, *, cursor: str | None, days: int = DEFAULT_DAYS, today: date | None = None, page_size: int = PAGE_SIZE,
               max_pages: int = MAX_PAGES, max_details: int = MAX_DETAILS, known: set | frozenset = frozenset(), seen: set | None = None):
    """Yield one dict per feed page: offset used, next cursor, entries scanned, scrubbed details of relevant (or already
    stored) tenders. The caller stores `next_offset` only after it has stored that page's tenders, so a run that dies
    mid-page resumes at that page. A page is always finished; the detail budget is checked between pages."""
    today = today or datetime.now(timezone.utc).date()
    offset = cursor or start_offset(days, today)
    seen = set() if seen is None else seen
    fetched = 0
    for _ in range(max_pages):
        params = {"offset": offset, "limit": page_size, "opt_fields": ",".join(FEED_FIELDS)}
        feed = get(URL, params) or {}
        entries = feed.get("data") or []
        nxt = str(((feed.get("next_page") or {}).get("offset")) or offset)
        details, skipped, duplicates = [], 0, 0
        for e in entries:
            tid = e.get("id")
            if tid and tid in seen:
                duplicates += 1                              # modified twice during the run: one detail request is enough
                continue
            if not tid or not candidate(e, known):
                skipped += 1
                continue
            seen.add(tid)
            data = (get(f"{URL}/{tid}", None) or {}).get("data") or {}
            fetched += 1
            if data.get("tenderID") and (relevance(data) or tid in known):
                details.append(scrub(data))
        caught_up = not entries or nxt == offset
        yield {"offset": offset, "next_offset": nxt, "scanned": len(entries), "skipped": skipped, "duplicates": duplicates,
               "details_fetched": fetched, "details": details, "caught_up": caught_up}
        if caught_up or fetched >= max_details:
            return
        offset = nxt
