"""TED (EU Tenders Electronic Daily), official API v3, no key. https://api.ted.europa.eu/v3/notices/search"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from ..store import sha

URL = "https://api.ted.europa.eu/v3/notices/search"
NOTICE_URL = "https://ted.europa.eu/en/notice/{}"
FIELDS = ["publication-number", "notice-title", "buyer-name", "buyer-country", "publication-date", "deadline-receipt-tender-date-lot",
          "estimated-value-lot", "estimated-value-cur-lot", "classification-cpv", "place-of-performance-country-lot", "notice-type"]
DEFAULT_CPV = ("09", "15", "60", "63", "35")    # fuel/energy, food, transport, logistics + security/defence (monitoring; bids gated)
CATEGORY = (("09", "fuel_energy"), ("15", "food"), ("60", "logistics"), ("63", "logistics"), ("34", "vehicles"),
            ("30", "it_office"), ("48", "it_office"), ("72", "it_services"), ("18", "clothing"), ("33", "medical"), ("35", "security_defence"))


def category_of(cpvs: list[str]) -> str:
    """Majority category over all CPV codes of the notice (multi-lot notices list many codes; the first one is not the subject)."""
    votes: dict[str, int] = {}
    for c in cpvs:
        for prefix, name in CATEGORY:
            if c.startswith(prefix):
                votes[name] = votes.get(name, 0) + 1
                break
    return max(votes, key=lambda k: (votes[k], -list(votes).index(k))) if votes else "other"


def build_query(cpv_prefixes=DEFAULT_CPV, country: str | None = "CZE", days: int = 30, today: date | None = None) -> str:
    today = today or date.today()
    since = (today - timedelta(days=days)).strftime("%Y%m%d")
    cpv = " OR ".join(f"classification-cpv={p}*" for p in cpv_prefixes)
    parts = [f"({cpv})", f"publication-date>={since}"]
    if country:
        parts.insert(0, f"buyer-country={country}")
    return " AND ".join(parts)


def _text(v) -> str:
    if isinstance(v, dict):
        for lang in ("eng", "ces", *v):
            x = v.get(lang)
            if x:
                return str(x[0] if isinstance(x, list) else x)
        return ""
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v or "")


def _date(v) -> str | None:
    s = _text(v)[:10]
    return s if len(s) == 10 else None


def normalize(n: dict, retrieved_at: str, snapshot_sha: str, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    pub = str(n.get("publication-number", ""))
    deadline_day = _date(n.get("deadline-receipt-tender-date-lot"))
    ntype = _text(n.get("notice-type"))
    vals = []
    for v in n.get("estimated-value-lot") or []:
        try:
            d = Decimal(str(v))
            if d > 1:                                      # TED placeholders 0 / 1 are "not stated", not a price
                vals.append(d)
        except InvalidOperation:
            pass
    if ntype.startswith("can"):
        status = "closed"                                   # contract award notice
    elif ntype.startswith("cn") and deadline_day:
        status = "open" if date.fromisoformat(deadline_day) >= today else "closed"
    else:
        status = "unknown"                                  # prior information / no deadline: not biddable as is
    cpv_all = [str(c) for c in (n.get("classification-cpv") or [])]
    cpv = sorted(set(cpv_all))
    quality = "ok" if (vals and deadline_day) else "partial"
    return {
        "id": f"ted:{pub}", "official_notice_id": pub, "title": _text(n.get("notice-title")) or pub, "jurisdiction": "EU",
        "country": _text(n.get("buyer-country")) or _text(n.get("place-of-performance-country-lot")),
        "category": category_of(cpv_all), "status": status,
        "deadline": f"{deadline_day}T23:59:59Z" if deadline_day else None,
        "estimated_value": float(sum(vals)) if vals else None, "currency": _text(n.get("estimated-value-cur-lot")) or None,
        "value_basis": "sum of lot estimates" if vals else None,
        "source_url": NOTICE_URL.format(pub), "source_id": "ted", "retrieved_at": retrieved_at, "published_at": _date(n.get("publication-date")),
        "source_version": ntype, "checksum": sha(repr(sorted(n.items(), key=lambda kv: kv[0]))), "data_quality": quality,
        "buyer": _text(n.get("buyer-name")), "cpv": cpv, "demo": False, "snapshot_sha256": snapshot_sha,
    }


def fetch(post, *, cpv_prefixes=DEFAULT_CPV, country: str | None = "CZE", days: int = 30, limit: int = 50, today: date | None = None):
    body = {"query": build_query(cpv_prefixes, country, days, today), "fields": FIELDS, "limit": limit, "page": 1}
    return body, post(URL, body)


def normalize_all(payload: dict, retrieved_at: str, snapshot_sha: str, today: date | None = None) -> list[dict]:
    return [normalize(n, retrieved_at, snapshot_sha, today) for n in payload.get("notices", [])]


def parse_ocds_release(release: dict, *, retrieved_at: str = "") -> dict:
    """Open Contracting Data Standard (OCDS) release normalizer (Step 13.2).
    Extracts >= 1.5x (2.0x: 10 vs 5) structured procurement fields compared to flat notice parsing."""
    tender = release.get("tender") or {}
    buyer = release.get("buyer") or {}
    val = tender.get("value") or {}
    period = tender.get("tenderPeriod") or {}
    items = tender.get("items") or []
    cpv_codes = sorted({
        str((it.get("classification") or {}).get("id"))
        for it in items
        if (it.get("classification") or {}).get("id")
    })
    ocid = str(release.get("ocid") or release.get("id") or "")
    out = {
        "ocid": ocid,
        "title": str(tender.get("title") or ""),
        "description": str(tender.get("description") or ""),
        "buyer_name": str(buyer.get("name") or ""),
        "buyer_id": str(buyer.get("id") or ""),
        "estimated_value": float(val["amount"]) if val.get("amount") is not None else None,
        "currency": str(val.get("currency") or ""),
        "deadline": str(period.get("endDate") or ""),
        "cpv": cpv_codes,
        "items_count": len(items),
        "procurement_method": str(tender.get("procurementMethod") or ""),
        "award_criteria": str(tender.get("awardCriteria") or ""),
        "retrieved_at": retrieved_at,
    }
    recognized = [k for k in ("ocid", "title", "description", "buyer_name", "buyer_id",
                              "estimated_value", "currency", "deadline", "cpv",
                              "procurement_method", "award_criteria")
                  if out.get(k) not in (None, "", [])]
    out["recognized_fields_count"] = len(recognized)
    return out


PERMISSIVE_LICENSES = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD"})


def evaluate_oss_candidate(name: str, *, license_spdx: str, scores_gpdsv: dict[str, float],
                           known_cves: int = 0, min_avg_score: float = 9.0) -> dict:
    """OSS candidate gate (Step 13.3): requires permissive license, 0 CVEs, and G·P·D·S·V mean >= 9.0."""
    dims = ("G", "P", "D", "S", "V")
    vals = [float(scores_gpdsv.get(k, 0.0)) for k in dims]
    avg_score = round(sum(vals) / len(dims), 2)
    lic_ok = license_spdx in PERMISSIVE_LICENSES
    cve_ok = (known_cves == 0)
    score_ok = (avg_score >= min_avg_score and all(v >= 7.0 for v in vals))
    passed = bool(lic_ok and cve_ok and score_ok)
    reasons = []
    if not lic_ok:
        reasons.append(f"non_permissive_license:{license_spdx}")
    if not cve_ok:
        reasons.append(f"known_cves:{known_cves}")
    if not score_ok:
        reasons.append(f"score_below_threshold:{avg_score}<{min_avg_score}")
    return {
        "name": name,
        "license_spdx": license_spdx,
        "avg_gpdsv": avg_score,
        "known_cves": known_cves,
        "passed": passed,
        "reasons": reasons,
    }

