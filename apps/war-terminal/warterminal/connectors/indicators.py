"""Five keyless public APIs (from the public-apis list) for country context, verified live 17.10.2026:

- World Bank Indicators: military expenditure, current USD (MS.MIL.XPND.CD, sourced from SIPRI) - "defense budgets" with a real source;
- UNHCR population statistics: refugees by country of origin;
- Frankfurter (ECB reference rates): second FX source to cross-check the CNB fixing;
- Nominatim (OpenStreetMap): geocoding of buyers/suppliers - usage policy: max 1 request/second, identified User-Agent, cache results;
- UN Comtrade public preview: annual trade flows by HS code (e.g. what a country imports and from whom).
"""
from __future__ import annotations

WB_MIL = "https://api.worldbank.org/v2/country/all/indicator/MS.MIL.XPND.CD"
UNHCR = "https://api.unhcr.org/population/v1/population/"
FRANKFURTER = "https://api.frankfurter.dev/v1/latest"            # moved from api.frankfurter.app (301, seen live 17.10)
NOMINATIM = "https://nominatim.openstreetmap.org/search"
COMTRADE = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"


def military_expenditure(get) -> dict:
    """Latest non-empty value per country (mrnev=1). Aggregates (regions, income groups) have no ISO3 and are dropped."""
    raw = get(WB_MIL, {"format": "json", "mrnev": 1, "per_page": 400})
    meta, rows = (raw[0], raw[1] or []) if isinstance(raw, list) and len(raw) == 2 else ({}, [])
    out = {}
    for r in rows:
        iso3, val = r.get("countryiso3code"), r.get("value")
        if iso3 and len(iso3) == 3 and val is not None:
            out[iso3] = {"usd": float(val), "year": r.get("date"), "name": (r.get("country") or {}).get("value")}
    return {"items": out, "source": "World Bank WDI MS.MIL.XPND.CD (SIPRI)", "last_updated": meta.get("lastupdated"), "url": WB_MIL}


def refugees_by_origin(get, year: int, max_pages: int = 60) -> dict:
    out, page = {}, 1
    while page <= max_pages:
        raw = get(UNHCR, {"limit": 100, "page": page, "yearFrom": year, "yearTo": year, "coo_all": "true"})
        for r in raw.get("items", []):
            iso = r.get("coo_iso")
            if iso and iso != "-":
                out[iso] = {"refugees": int(r.get("refugees") or 0), "asylum_seekers": int(r.get("asylum_seekers") or 0),
                            "idps": int(r.get("idps") or 0), "year": r.get("year"), "name": r.get("coo_name")}
        if page >= int(raw.get("maxPages") or 1):
            break
        page += 1
    return {"items": out, "year": year, "source": "UNHCR Refugee Population Statistics", "url": UNHCR}


def ecb_rates(get, base: str = "EUR") -> dict:
    raw = get(FRANKFURTER, {"from": base})
    return {"base": raw.get("base"), "as_of": raw.get("date"), "rates": raw.get("rates", {}), "source": "ECB reference rates via Frankfurter"}


def geocode(get, query: str) -> dict | None:
    raw = get(NOMINATIM, {"q": query, "format": "json", "limit": 1})
    if not raw:
        return None
    r = raw[0]
    return {"query": query, "lat": float(r["lat"]), "lon": float(r["lon"]), "display_name": r.get("display_name"),
            "licence": r.get("licence") or "© OpenStreetMap contributors, ODbL 1.0"}


def trade_flows(get, reporter_code: int, hs: str, flow: str, year: int, top: int = 10) -> dict:
    """flow M=imports, X=exports. Returns top partners by value (USD). Partner code 0 = World total."""
    raw = get(COMTRADE, {"reporterCode": reporter_code, "period": year, "cmdCode": hs, "flowCode": flow})
    rows = [r for r in raw.get("data", []) if r.get("partnerCode") not in (0, None)]
    total = next((r.get("primaryValue") for r in raw.get("data", []) if r.get("partnerCode") == 0), None)
    rows.sort(key=lambda r: -(r.get("primaryValue") or 0))
    return {"reporter_code": reporter_code, "hs": hs, "flow": flow, "year": year, "world_total_usd": total,
            "partners": [{"partner_code": r.get("partnerCode"), "value_usd": r.get("primaryValue")} for r in rows[:top]],
            "source": "UN Comtrade public preview", "note": "partner_code = UN M49 numeric code"}
