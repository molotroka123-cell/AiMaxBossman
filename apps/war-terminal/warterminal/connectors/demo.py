"""Synthetic fixtures for UI/dev. Every record is demo=True and carries an unmistakable id; never counted as real."""
from __future__ import annotations

from datetime import date, timedelta


def tenders(retrieved_at: str, today: date | None = None) -> list[dict]:
    today = today or date.today()
    rows = [
        ("DEMO-001", "CZ", "Dodávka motorové nafty pro obecní vozidla (DEMO)", "fuel_energy", ["09134200"], 38000, "EUR", 40),
        ("DEMO-002", "CZ", "Potravinové balíčky pro sociální služby (DEMO)", "food", ["15800000"], 52000, "EUR", 21),
        ("DEMO-003", "CZ", "Silniční nákladní doprava, rámcová smlouva (DEMO)", "logistics", ["60100000"], 240000, "EUR", 33),
        ("DEMO-004", "DE", "Werkskantine Lebensmittel (DEMO)", "food", ["15000000"], 95000, "EUR", 12),
        ("DEMO-005", "PL", "Usługi spedycyjne (DEMO)", "logistics", ["63520000"], None, None, 28),
        ("DEMO-006", "EU", "Specialised electronics, dual-use review (DEMO)", "security_defence", ["35000000"], 500000, "EUR", 45),
        ("DEMO-007", "CZ", "Expired example (DEMO)", "logistics", ["60000000"], 30000, "EUR", -3),
    ]
    out = []
    for nid, ctry, title, cat, cpv, val, cur, days in rows:
        out.append({"id": f"demo:{nid}", "official_notice_id": nid, "title": title, "jurisdiction": "EU" if ctry in ("CZ", "DE", "PL", "EU") else ctry,
                    "country": ctry if ctry != "EU" else "", "category": cat, "status": "open" if days >= 0 else "closed",
                    "deadline": f"{(today + timedelta(days=days)).isoformat()}T23:59:59Z", "estimated_value": val, "currency": cur,
                    "source_url": f"https://example.invalid/demo/{nid}", "source_id": "demo", "retrieved_at": retrieved_at, "published_at": today.isoformat(),
                    "source_version": "demo", "checksum": nid, "data_quality": "synthetic", "buyer": "DEMO BUYER", "cpv": cpv, "demo": True})
    return out
