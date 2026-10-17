"""GDELT DOC 2.0: global news about a topic/country. Media attention, NOT verified conflict events and NOT geospatial intelligence."""
from __future__ import annotations

URL = "https://api.gdeltproject.org/api/v2/doc/doc"


def fetch(get, query: str, *, timespan: str = "7d", limit: int = 20):
    params = {"query": query, "mode": "artlist", "maxrecords": limit, "format": "json", "timespan": timespan, "sort": "datedesc"}
    return params, get(URL, params)


def normalize_all(payload: dict, retrieved_at: str) -> list[dict]:
    return [{"title": a.get("title"), "url": a.get("url"), "domain": a.get("domain"), "seen": a.get("seendate"),
             "source_country": a.get("sourcecountry"), "language": a.get("language"), "source_id": "gdelt", "retrieved_at": retrieved_at,
             "kind": "news_mention", "demo": False} for a in payload.get("articles", [])]
