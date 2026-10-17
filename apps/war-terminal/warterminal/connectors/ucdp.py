"""UCDP (Uppsala Conflict Data Program) public downloads, no key: GED events with coordinates and the UCDP/PRIO conflict list.

Used for COMMERCIAL CONTEXT only: country-level aggregates of the latest year (events, deaths, mean point). No tactical use,
no event-level positions are exposed by the API. Data is annual and verified, so it lags reality (latest year in the release)."""
from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict

GED_URL = "https://ucdp.uu.se/downloads/ged/ged251-csv.zip"
ACD_URL = "https://ucdp.uu.se/downloads/ucdpprio/ucdp-prio-acd-251-csv.zip"
PAGE = "https://ucdp.uu.se/downloads/"
LICENCE = "CC BY 4.0 (по сведениям UCDP; проверить перед коммерческим использованием)"


def _csv_from_zip(data: bytes):
    z = zipfile.ZipFile(io.BytesIO(data))
    name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    return csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8", errors="replace"))


def parse_ged(data: bytes, retrieved_at: str, version: str = "25.1") -> dict:
    rows = list(_csv_from_zip(data))
    year = max(int(r["year"]) for r in rows if r.get("year", "").isdigit())
    agg = defaultdict(lambda: {"events": 0, "deaths": 0, "lat": 0.0, "lon": 0.0, "region": ""})
    for r in rows:
        if r.get("year") != str(year):
            continue
        try:
            lat, lon, best = float(r["latitude"]), float(r["longitude"]), int(float(r.get("best") or 0))
        except (KeyError, ValueError):
            continue
        a = agg[r.get("country", "?")]
        a["events"] += 1
        a["deaths"] += best
        a["lat"] += lat
        a["lon"] += lon
        a["region"] = r.get("region", "")
    items = []
    for country, a in agg.items():
        sev = "high" if a["deaths"] >= 1000 else "medium" if a["deaths"] >= 100 else "low"
        items.append({"id": f"ucdp-ged-{version}:{country}:{year}", "name": f"{country}: {a['events']} событий, {a['deaths']} погибших ({year})",
                      "country": country, "lat": round(a["lat"] / a["events"], 3), "lon": round(a["lon"] / a["events"], 3), "severity": sev,
                      "since": str(year), "events": a["events"], "deaths": a["deaths"], "region": a["region"], "source_id": f"ucdp_ged_{version}",
                      "source_url": PAGE, "licence": LICENCE, "retrieved_at": retrieved_at, "demo": False,
                      "precision": "средняя точка событий страны за год; не координаты событий"})
    items.sort(key=lambda i: -i["deaths"])
    return {"year": year, "events_total": sum(i["events"] for i in items), "items": items}


def parse_acd(data: bytes) -> dict:
    rows = list(_csv_from_zip(data))
    year = max(int(r["year"]) for r in rows if r.get("year", "").isdigit())
    items = [{"conflict_id": r.get("conflict_id"), "location": r.get("location"), "side_a": r.get("side_a"), "side_b": r.get("side_b"),
              "intensity": "war" if r.get("intensity_level") == "2" else "minor", "type": r.get("type_of_conflict"), "year": year}
             for r in rows if r.get("year") == str(year)]
    return {"year": year, "items": items}
