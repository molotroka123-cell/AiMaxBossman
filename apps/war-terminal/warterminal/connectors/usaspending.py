"""USAspending.gov API, no key. Awards = who already wins and for how much (competitor/supplier map). Not biddable tenders."""
from __future__ import annotations

from datetime import date, timedelta

URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"


def fetch(post, keyword: str, *, days: int = 90, limit: int = 25, today: date | None = None):
    today = today or date.today()
    body = {"filters": {"award_type_codes": ["A", "B", "C", "D"], "keywords": [keyword],
                        "time_period": [{"start_date": (today - timedelta(days=days)).isoformat(), "end_date": today.isoformat()}]},
            "fields": ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Description", "generated_internal_id"],
            "sort": "Award Amount", "order": "desc", "limit": limit}
    return body, post(URL, body)


def normalize_all(payload: dict, retrieved_at: str) -> list[dict]:
    out = []
    for r in payload.get("results", []):
        slug = r.get("generated_internal_id", "")
        out.append({"award_id": r.get("Award ID"), "recipient": r.get("Recipient Name"), "amount_usd": r.get("Award Amount"),
                    "agency": r.get("Awarding Agency"), "description": r.get("Description"),
                    "source_url": f"https://www.usaspending.gov/award/{slug}" if slug else "https://www.usaspending.gov/",
                    "source_id": "usaspending", "retrieved_at": retrieved_at, "demo": False})
    return out
