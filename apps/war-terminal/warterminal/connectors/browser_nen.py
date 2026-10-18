"""Browser-read connector for the Czech NEN portal (nen.nipez.cz, public pages, no API, no login).

Pure parsing/normalising code: HTML in, tender dicts out. No network here (the headless-browser driver lives in
tools/browser_contracts.py), so everything is unit-tested on saved page fixtures. Missing field = None, never invented.
Personal data of contact persons on detail pages is deliberately NOT extracted.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..store import sha
from .ted import category_of

BASE = "https://nen.nipez.cz"
LIST_PATH = "/en/verejne-zakazky/"
SOURCE_ID = "browser_nen"
CRAWL_DELAY_S = 10            # robots.txt of nen.nipez.cz: Crawl-delay: 10
CURRENCY = {"koruna česká": "CZK", "euro": "EUR", "americký dolar": "USD"}
PRAGUE = ZoneInfo("Europe/Prague")

# Buyers and subject words that define the "war procurement" domain on NEN (Czech, matched by the portal's own filters).
BUYERS = ["Ministerstvo obrany", "Vojenské lesy a statky", "Vojenský technický ústav", "Vojenský geografický a hydrometeorologický úřad",
          "Policie České republiky", "Hasičský záchranný sbor", "Vězeňská služba", "Generální ředitelství cel", "Ministerstvo vnitra"]
SUBJECTS = ["munice", "náboje", "zbraň", "dron", "bezpilot", "balistick", "neprůstřel", "kamufláž", "radar", "vojensk", "pancéř", "střelnice"]


def list_url(*, buyer: str | None = None, name: str | None = None, status: str | None = "neukoncena",
             deadline_from: str | None = None, page: int = 1) -> str:
    """NEN list URL grammar (read off the portal's own form submit): /p:vz:<k>=<v>&<k>=<v>&page=N, values single-encoded."""
    parts = []
    if status:
        parts.append(f"stavZP={status}")
    if buyer:
        parts.append(f"zadavatelNazev={quote(buyer)}")
    if name:
        parts.append(f"nazev={quote(name)}")
    if deadline_from:
        parts.append(f"podaniLhuta={deadline_from}%2C")
    if page > 1:
        parts.append(f"page={page}")
    return BASE + LIST_PATH + ("p:vz:" + "&".join(parts) if parts else "")


def _txt(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def parse_list(html: str) -> dict:
    """-> {rows:[{nen_number,title,status,buyer,deadline_text,detail_path}], last_page:int}"""
    s = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in s.select("tbody tr"):
        c = [_txt(td) for td in tr.select("td[data-title]")]
        a = tr.select_one("a[href*='detail-zakazky']")
        if len(c) < 5 or not a:
            continue
        rows.append({"nen_number": c[0], "title": c[1], "status": c[2], "buyer": c[3], "deadline_text": c[4] or None, "detail_path": a["href"]})
    pages = [int(m.group(1)) for a in s.select("a[href*='page=']") if (m := re.search(r"page=(\d+)$", a.get("href", "")))]
    return {"rows": rows, "last_page": max(pages) if pages else 1}


def parse_detail(html: str) -> dict:
    """Label -> value tiles (`.gov-grid-tile`: <h3> label + <p>/<a> value). Keys are lower-cased English labels; '_cpv' = CPV codes."""
    s = BeautifulSoup(html, "html.parser")
    out: dict = {}
    for tile in s.select(".gov-grid-tile"):
        h = tile.find("h3")
        if not h:
            continue
        label = _txt(h)
        out.setdefault(label.lower(), _txt(tile).removeprefix(label).strip())
    cpvs = set(re.findall(r"\b\d{8}-\d\b", out.get("code from the cpv code list", "")))
    for tr in s.select("table tbody tr"):                # item rows: ... | CPV code | CPV name | ...
        cpvs.update(re.findall(r"\b\d{8}-\d\b", _txt(tr)))
    out["_cpv"] = sorted(cpvs)
    return out


def _dt(text: str | None) -> str | None:
    """'26.10.2026, 23:59' (Prague local) -> ISO UTC; date-only also accepted. None when absent/unparseable."""
    if not text:
        return None
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:,?\s*(\d{1,2}):(\d{2}))?", text)
    if not m:
        return None
    d, mo, y, hh, mm = m.groups()
    try:
        local = datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), tzinfo=PRAGUE)
    except ValueError:
        return None
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(text: str | None) -> float | None:
    if not text:
        return None
    t = re.sub(r"[\s ]", "", text).replace(",", ".")
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None
    return float(v) if v > 0 else None


def normalize(detail: dict, url: str, fetched_at: str, now: datetime | None = None) -> dict | None:
    """Detail dict -> terminal tender doc (schemas/tender.schema.json). None if the notice has no identity."""
    now = now or datetime.now(timezone.utc)
    nen = detail.get("nen system number")
    nipez = detail.get("identifier of nipez") or nen
    if not nipez:
        return None
    deadline = _dt(detail.get("deadline for submitting tenders"))
    status_raw = (detail.get("current status of the procurement procedure") or "").lower()
    if status_raw.startswith("not terminated") and deadline:
        status = "open" if deadline >= now.strftime("%Y-%m-%dT%H:%M:%SZ") else "closed"
    elif status_raw.startswith("cancel"):
        status = "cancelled"
    elif status_raw.startswith(("awarded", "termination", "unsuccessful")):
        status = "closed"
    else:
        status = "unknown"
    cpv = detail.get("_cpv") or []
    cur_raw = (detail.get("currency") or "").lower()
    value = _money(detail.get("estimated value (excl. vat)"))
    return {
        "id": f"cz:{nipez}", "official_notice_id": nipez, "title": detail.get("procurement procedure name") or nen or nipez,
        "jurisdiction": "CZ", "country": "CZ", "category": category_of(list(cpv)), "status": status, "deadline": deadline,
        "estimated_value": value, "currency": (CURRENCY.get(cur_raw) or cur_raw.upper() or None) if value is not None else None,
        "value_basis": "estimated value excl. VAT" if value is not None else None,
        "source_url": url, "source_id": SOURCE_ID, "source": "browser", "fetched_at": fetched_at, "retrieved_at": fetched_at,
        "published_at": (_dt(detail.get("date of publication on profile")) or "")[:10] or None,
        "source_version": f"NEN browser read; nen_number={nen}; status={detail.get('current status of the procurement procedure')}",
        "checksum": sha(repr(sorted((k, str(v)) for k, v in detail.items()))),
        "data_quality": "ok" if (value is not None and deadline) else "partial",
        "buyer": detail.get("contracting authority") or "", "cpv": cpv, "demo": False, "snapshot_sha256": sha(url + fetched_at),
        "provenance": {"method": "headless browser page read (no API)", "url": url, "fetched_at": fetched_at, "portal": "nen.nipez.cz"},
    }


DEFENCE_CPV = ("18", "31", "32", "33", "34", "37", "38")           # protective clothing, electrical, comms, medical, vehicles, optics
DEFENCE_WORDS = ("munice", "náboj", "zbra", "dron", "bezpilot", "balistick", "neprůstřel", "kamufl", "radar", "vojensk", "taktick",
                 "ochrann", "polní", "střel", "výstroj", "uniform", "pancéř", "ostraha", "kybernet")


def relevance(doc: dict) -> int:
    """Cheap, explainable 0-100 relevance for the war-procurement domain: buyer, CPV prefix, subject words, value."""
    s = 0
    buyer, title, cpv = (doc.get("buyer") or "").lower(), (doc.get("title") or "").lower(), doc.get("cpv") or []
    if any(b.lower() in buyer for b in BUYERS[:5]):
        s += 30
    s += 25 if any(c.startswith("35") for c in cpv) else (10 if any(c.startswith(DEFENCE_CPV) for c in cpv) else 0)
    s += 25 if any(w in title for w in DEFENCE_WORDS) else 0
    v = doc.get("estimated_value") or 0
    s += 20 if v >= 5_000_000 else 10 if v >= 1_000_000 else 0
    return min(s, 100)
