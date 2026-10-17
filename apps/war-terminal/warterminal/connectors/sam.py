"""SAM.gov Opportunities API v2. Needs a free personal API key (https://sam.gov -> Account Details). Without a key: NOT_CONNECTED.
Written to the published API shape; NOT validated live (no key on this machine) => reported as NOT_RUN, never as LIVE."""
from __future__ import annotations

from datetime import date, timedelta

from ..store import sha

URL = "https://api.sam.gov/opportunities/v2/search"


def fetch(get, api_key: str, *, days: int = 14, limit: int = 50, today: date | None = None):
    today = today or date.today()
    params = {"api_key": api_key, "limit": limit, "postedFrom": (today - timedelta(days=days)).strftime("%m/%d/%Y"),
              "postedTo": today.strftime("%m/%d/%Y"), "ptype": "o"}
    return {"url": URL, "days": days, "limit": limit}, get(URL, params)


def normalize_all(payload: dict, retrieved_at: str, snapshot_sha: str) -> list[dict]:
    out = []
    for r in payload.get("opportunitiesData", []):
        nid = str(r.get("noticeId") or r.get("solicitationNumber") or "")
        award = r.get("award") or {}
        out.append({
            "id": f"sam:{nid}", "official_notice_id": nid, "title": str(r.get("title") or nid), "jurisdiction": "US", "country": "US",
            "category": "other", "status": "open" if str(r.get("active", "")).lower() == "yes" else "unknown",
            "deadline": r.get("responseDeadLine"), "estimated_value": _num(award.get("amount")), "currency": "USD" if award.get("amount") else None,
            "source_url": str(r.get("uiLink") or f"https://sam.gov/opp/{nid}/view"), "source_id": "sam", "retrieved_at": retrieved_at,
            "published_at": r.get("postedDate"), "source_version": str(r.get("type") or ""), "checksum": sha(repr(sorted(r.items(), key=lambda kv: kv[0]))),
            "data_quality": "partial", "buyer": str(r.get("fullParentPathName") or ""), "cpv": [], "naics": str(r.get("naicsCode") or ""),
            "demo": False, "snapshot_sha256": snapshot_sha,
        })
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
