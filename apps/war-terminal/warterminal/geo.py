"""Country index from Natural Earth (public domain): names/ISO codes for joining UCDP, TED and World Bank data on the map."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

GEO_FILE = Path(__file__).parent / "ui" / "vendor" / "countries-110m.geojson"
# UCDP uses historical names in parentheses and a few spellings Natural Earth does not.
ALIASES = {"dr congo": "COD", "democratic republic of congo": "COD", "congo": "COG", "ivory coast": "CIV", "bosnia-herzegovina": "BIH",
           "kingdom of eswatini": "SWZ", "united states": "USA", "united states of america": "USA", "russia": "RUS", "myanmar": "MMR",
           "central african republic": "CAF", "south sudan": "SSD", "north macedonia": "MKD", "czechia": "CZE", "czech republic": "CZE",
           "cambodia": "KHM", "yemen": "YEM", "laos": "LAO", "syria": "SYR", "iran": "IRN", "turkey": "TUR", "turkiye": "TUR"}


def _key(name: str) -> str:
    return re.sub(r"\s*\(.*?\)\s*", " ", name or "").strip().lower()


@lru_cache(maxsize=1)
def index() -> dict:
    data = json.loads(GEO_FILE.read_text(encoding="utf-8"))
    by_name, by_iso2, iso3s = {}, {}, set()
    for f in data["features"]:
        p = f["properties"]
        iso3s.add(p["iso3"])
        if p.get("iso2"):
            by_iso2[p["iso2"].upper()] = p["iso3"]
        for k in ("name", "name_long", "admin", "formal"):
            if p.get(k):
                by_name[_key(p[k])] = p["iso3"]
    by_name.update(ALIASES)
    return {"by_name": by_name, "by_iso2": by_iso2, "iso3": iso3s, "source": data.get("source")}


def to_iso3(value: str | None) -> str | None:
    """ISO3 for a country given as ISO3, ISO2 or a (UCDP-style) name; None when unknown."""
    if not value:
        return None
    ix = index()
    v = value.strip()
    if len(v) == 3 and v.upper() in ix["iso3"]:
        return v.upper()
    if len(v) == 2 and v.upper() in ix["by_iso2"]:
        return ix["by_iso2"][v.upper()]
    return ix["by_name"].get(_key(v))
