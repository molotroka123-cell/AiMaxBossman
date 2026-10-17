"""Synthetic conflict markers for the UI. They are NOT real conflicts: names say DEMO and the API marks demo=True."""
from __future__ import annotations


def conflicts(retrieved_at: str) -> list[dict]:
    rows = [("DEMO-ZONE-A", "XX", 12.0, 2.0, "high"), ("DEMO-ZONE-B", "XX", 31.0, 35.0, "medium"), ("DEMO-ZONE-C", "XX", -4.0, 24.0, "high"),
            ("DEMO-ZONE-D", "XX", 48.0, 31.0, "medium"), ("DEMO-ZONE-E", "XX", 20.0, 96.0, "low")]
    return [{"id": f"demo:{n}", "name": n, "country": c, "lat": la, "lon": lo, "severity": s, "since": None, "source_id": "demo",
             "source_url": "https://example.invalid/demo", "retrieved_at": retrieved_at, "demo": True} for n, c, la, lo, s in rows]
