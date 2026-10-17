"""World Bank procurement notices (projects financed by IBRD/IDA, worldwide), official search API v2, no key.
https://search.worldbank.org/api/v2/procnotices  (GET, format=json, rows, os, qterm, fl, <field>_exact filters)."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from ..store import sha

URL = "https://search.worldbank.org/api/v2/procnotices"
NOTICE_URL = "https://projects.worldbank.org/en/projects-operations/procurement-detail/{}"
FIELDS = ["id", "notice_type", "noticedate", "notice_status", "notice_version_no", "noticetitle", "bid_description", "bid_reference_no",
          "project_id", "project_name", "project_ctry_code", "project_ctry_name", "agency_name", "contact_organization",
          "bid_currency_code", "bid_estimate_amount", "procurement_group", "procurement_group_desc", "procurement_method_name",
          "submission_deadline_date", "submission_deadline_time", "unspsc_classification", "market_approach_name", "api_modified_date"]
NOTICE_TYPES = ("Invitation for Bids", "Request for Expression of Interest", "General Procurement Notice",
                "Invitation for Prequalification", "Contract Award")
AWARD_TYPES = ("Contract Award", "Goods and Works Award", "Small Contracts Award")
# UNSPSC segment (first 2 digits) -> same category names as ted.CATEGORY
UNSPSC_CATEGORY = (("15", "fuel_energy"), ("50", "food"), ("78", "logistics"), ("25", "vehicles"), ("43", "it_office"), ("44", "it_office"),
                   ("8111", "it_services"), ("53", "clothing"), ("42", "medical"), ("46", "security_defence"))
GROUP_CATEGORY = {"GO": "goods", "CW": "works", "CS": "consultancy", "NC": "services"}
# World Bank legacy country codes that differ from ISO 3166-1 alpha-2
WB_TO_ISO2 = {"ZR": "CD", "YF": "RS", "TP": "TL", "KV": "XK", "WG": "PS"}


def _s(v) -> str:
    if isinstance(v, list):
        v = v[0] if v else ""
    return str(v or "").strip()


def _day(v) -> str | None:
    s = _s(v)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", s):
        return s[:10]
    try:                                                    # noticedate is "16-Oct-2026"
        return datetime.strptime(s, "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def country_of(n: dict) -> str:
    code = _s(n.get("project_ctry_code")).upper()
    if re.fullmatch(r"[A-Z]{2}", code):
        return WB_TO_ISO2.get(code, code)
    return _s(n.get("project_ctry_name"))                   # regional codes like "3W", "7B" -> region/country name


def category_of(n: dict) -> str:
    for u in n.get("unspsc_classification") or []:
        code = _s(u.get("proc_voc_id") or u.get("seg_id")) if isinstance(u, dict) else ""
        for prefix, name in UNSPSC_CATEGORY:
            if code.startswith(prefix):
                return name
    return GROUP_CATEGORY.get(_s(n.get("procurement_group")).upper(), "other")


def _amount(v) -> float | None:
    try:
        x = float(_s(v))
    except ValueError:
        return None
    return x if x > 0 else None


def normalize(n: dict, retrieved_at: str, snapshot_sha: str, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    nid = _s(n.get("id"))
    ntype = _s(n.get("notice_type"))
    deadline_day = _day(n.get("submission_deadline_date"))
    if ntype in AWARD_TYPES or _s(n.get("notice_status")).lower() == "cancelled":
        status = "closed"
    elif deadline_day:
        status = "open" if date.fromisoformat(deadline_day) >= today else "closed"
    else:
        status = "unknown"                                  # e.g. General Procurement Notice: no deadline
    value = _amount(n.get("bid_estimate_amount"))
    currency = _s(n.get("bid_currency_code")) or None
    unspsc = sorted({_s(u.get("proc_voc_id")) for u in (n.get("unspsc_classification") or []) if isinstance(u, dict) and u.get("proc_voc_id")})
    quality = "ok" if (value is not None and currency and (deadline_day or ntype in AWARD_TYPES)) else "partial"
    return {
        "id": f"wb:{nid}", "official_notice_id": nid,
        "title": _s(n.get("noticetitle")) or _s(n.get("bid_description")) or _s(n.get("project_name")) or nid,
        "jurisdiction": "WB", "country": country_of(n), "category": category_of(n), "status": status,
        "deadline": f"{deadline_day}T23:59:59Z" if deadline_day else None,
        "estimated_value": value, "currency": currency if value is not None else None,
        "value_basis": "bid estimate" if value is not None else None,
        "source_url": NOTICE_URL.format(nid), "source_id": "worldbank", "retrieved_at": retrieved_at,
        "published_at": _day(n.get("noticedate")), "source_version": ntype,
        "checksum": sha(repr(sorted(n.items(), key=lambda kv: kv[0]))), "data_quality": quality,
        "buyer": _s(n.get("agency_name")) or _s(n.get("contact_organization")) or _s(n.get("project_name")),
        "cpv": [], "unspsc": unspsc, "project_id": _s(n.get("project_id")) or None,
        "procurement_method": _s(n.get("procurement_method_name")) or None,
        "demo": False, "snapshot_sha256": snapshot_sha,
    }


def build_params(*, query: str | None = None, rows: int = 50, notice_type: str | None = None, country: str | None = None,
                 offset: int = 0) -> dict:
    params = {"format": "json", "rows": max(1, min(int(rows), 1000)), "os": max(0, int(offset)), "fl": ",".join(FIELDS)}
    if query:
        params["qterm"] = query
    if notice_type:
        params["notice_type_exact"] = notice_type
    if country:                                             # 2-letter code -> project_ctry_code, otherwise exact country name
        c = country.strip()
        if re.fullmatch(r"[A-Za-z]{2}", c):
            params["project_ctry_code"] = c.upper()
        else:
            params["project_ctry_name_exact"] = c
    return params


def fetch(get, *, query: str | None = None, rows: int = 50, today: date | None = None, notice_type: str | None = None,
          country: str | None = None, offset: int = 0):
    """get(url, params) -> parsed JSON. Returns (request_description, raw_payload). `today` is accepted for interface parity."""
    params = build_params(query=query, rows=rows, notice_type=notice_type, country=country, offset=offset)
    return {"url": URL, "params": params}, get(URL, params)


def normalize_all(payload: dict, retrieved_at: str, snapshot_sha: str, today: date | None = None) -> list[dict]:
    return [normalize(n, retrieved_at, snapshot_sha, today) for n in (payload.get("procnotices") or []) if n.get("id")]
