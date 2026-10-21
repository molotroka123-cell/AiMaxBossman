"""Source registry with honest status. LIVE only after a successful fetch recorded by this app; everything else says why not."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# kind: procurement | conflict | sanctions | market | humanitarian; adapter: True when this repo ships a connector
REGISTRY = [
    dict(id="ted", name="TED (EU Tenders Electronic Daily)", kind="procurement", url="https://ted.europa.eu/", api="https://api.ted.europa.eu/v3/notices/search", auth="none", adapter=True,
         note="official API v3; ToS: reuse allowed with attribution"),
    dict(id="sam", name="SAM.gov Opportunities", kind="procurement", url="https://sam.gov/", api="https://api.sam.gov/opportunities/v2/search", auth="free personal API key", adapter=True,
         note="adapter written to published API shape, not validated live without a key"),
    dict(id="usaspending", name="USAspending.gov (awards)", kind="procurement", url="https://www.usaspending.gov/", api="https://api.usaspending.gov/", auth="none", adapter=True,
         note="historical awards: competitor and supplier map, not biddable"),
    dict(id="eu_ft", name="EU Funding & Tenders Portal", kind="procurement", url="https://ec.europa.eu/info/funding-tenders/opportunities/portal/", auth="none", adapter=False, note="to validate"),
    dict(id="ungm", name="UN Global Marketplace (UNGM)", kind="procurement", url="https://www.ungm.org/", auth="registration", adapter=False,
         note="fuel/food/logistics for UN agencies, relevant for Guinea; ToS to check"),
    dict(id="worldbank", name="World Bank procurement notices (worldwide)", kind="procurement", url="https://projects.worldbank.org/en/projects-operations/procurement",
         api="https://search.worldbank.org/api/v2/procnotices", auth="none", adapter=True, note="worldwide notices; terms: attribution (CC BY 4.0 on Finances One copies), API terms page not found"),
    dict(id="prozorro", name="Prozorro (Ukraine public procurement) 🇺🇦", kind="procurement", url="https://prozorro.gov.ua/",
         api="https://public.api.openprocurement.org/api/2.5/tenders", auth="none", adapter=True,
         note="official public read-only OpenProcurement API 2.5 (open data under the Law of Ukraine «On Public Procurement»); incremental feed cursor; "
              "only defence/security/dual-use tenders (CPV 35*, keywords) are kept; contact persons are not stored"),
    dict(id="ukfts",name="UK Find a Tender", kind="procurement", url="https://www.find-tender.service.gov.uk/", auth="none", adapter=False, note="to validate"),
    dict(id="cz_nen", name="Czech ISVZ open data (NEN + VVZ, monthly export)", kind="procurement", url="https://isvz.nipez.cz/opendata/nova", auth="none", adapter=True,
         note="ENABLED by owner decision 17.10.2026 despite robots.txt Disallow: only the documented monthly ZIP is downloaded; data lags ~1 month; MMR contact ospevz@mmr.gov.cz"),
    dict(id="cz_vvz", name="Czech Vestnik verejnych zakazek", kind="procurement", url="https://vvz.nipez.cz/", auth="none", adapter=False, note="to validate"),
    dict(id="gdelt", name="GDELT DOC 2.0 (news)", kind="conflict", url="https://www.gdeltproject.org/", api="https://api.gdeltproject.org/api/v2/doc/doc", auth="none", adapter=True,
         note="media attention, not verified events"),
    dict(id="ucdp", name="UCDP GED 25.1 + UCDP/PRIO ACD (downloads)", kind="conflict", url="https://ucdp.uu.se/downloads/", auth="none (public downloads)", adapter=True,
         note="annual verified data (latest year 2024); the API needs a token, the downloads do not; licence per UCDP"),
    dict(id="acled", name="ACLED", kind="conflict", url="https://acleddata.com/", auth="key + licence", adapter=False, note="commercial use licence must be checked"),
    dict(id="reliefweb", name="ReliefWeb / OCHA HDX", kind="humanitarian", url="https://reliefweb.int/", auth="appname", adapter=False, note="per-dataset licences"),
    dict(id="icrc", name="ICRC", kind="conflict", url="https://www.icrc.org/", auth="none", adapter=False, note="reports, not an API"),
    dict(id="eu_sanctions", name="EU consolidated financial sanctions list", kind="sanctions", url="https://www.sanctionsmap.eu/", auth="none", adapter=False,
         note="import via /sanctions/import (CSV/JSON) with version and date"),
    dict(id="ofac", name="OFAC SDN list", kind="sanctions", url="https://ofac.treasury.gov/", api="https://www.treasury.gov/ofac/downloads/sdn.csv", auth="none", adapter=True,
         note="POST /sanctions/fetch"),
    dict(id="un_sanctions", name="UN Security Council consolidated list", kind="sanctions", url="https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list",
         api="https://scsanctions.un.org/resources/xml/en/consolidated.xml", auth="none", adapter=True, note="POST /sanctions/fetch"),
    dict(id="cnb", name="Czech National Bank FX fixing", kind="market", url="https://www.cnb.cz/", api="https://www.cnb.cz/en/financial-markets/foreign-exchange-market/central-bank-exchange-rate-fixing/central-bank-exchange-rate-fixing/daily.txt",
         auth="none", adapter=True, note="official daily rates; POST /fx/fetch"),
    dict(id="sipri", name="SIPRI military expenditure", kind="market", url="https://www.sipri.org/", auth="none", adapter=False, note="used via World Bank indicator below"),
    dict(id="worldbank_ind", name="World Bank Indicators: military expenditure (SIPRI)", kind="market", url="https://data.worldbank.org/indicator/MS.MIL.XPND.CD",
         api="https://api.worldbank.org/v2/", auth="none", adapter=True, note="POST /indicators/fetch; CC BY 4.0"),
    dict(id="unhcr", name="UNHCR refugee statistics", kind="humanitarian", url="https://www.unhcr.org/refugee-statistics/", api="https://api.unhcr.org/population/v1/",
         auth="none", adapter=True, note="POST /indicators/fetch; annual"),
    dict(id="ecb", name="ECB reference rates (Frankfurter)", kind="market", url="https://www.frankfurter.app/", api="https://api.frankfurter.app/latest", auth="none",
         adapter=True, note="cross-check of CNB rates: GET /fx/crosscheck"),
    dict(id="nominatim", name="Nominatim (OpenStreetMap geocoding)", kind="geo", url="https://nominatim.org/", api="https://nominatim.openstreetmap.org/search",
         auth="none (1 req/s, cache)", adapter=True, note="GET /geocode; ODbL attribution"),
    dict(id="comtrade", name="UN Comtrade (preview API)", kind="market", url="https://comtradeapi.un.org/", api="https://comtradeapi.un.org/public/v1/preview/",
         auth="none (preview)", adapter=True, note="GET /trade: trade flows by HS code"),
]


def status_for(entry: dict, *, live_enabled: bool, last: dict | None, has_key: bool = False, error: dict | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out = dict(entry)
    if error and (not last or error["at"] > last["at"]):
        out["status"] = "ERROR"                                   # the newest attempt failed: say so instead of showing old data as current
        out["reason"] = error["message"]
        out["error_at"] = error["at"]
        if last:
            out["last_success"], out["last_count"] = last["at"], last.get("count")
        return out
    if not entry["adapter"]:
        out["status"] = "NOT_CONNECTED"
        out["reason"] = "адаптер не реализован: " + entry.get("note", "")
    elif entry["id"] == "sam" and not has_key:
        out["status"] = "NOT_CONNECTED"
        out["reason"] = "нужен бесплатный API-ключ SAM.gov"
    elif not live_enabled:
        out["status"] = "DISABLED"
        out["reason"] = "внешние источники выключены переменной WAR_TERMINAL_LIVE=0 (аварийный выключатель сети)"
    elif not last:
        out["status"] = "NOT_RUN"
        out["reason"] = "ещё ни одного успешного запроса"
    else:
        age = now - datetime.fromisoformat(last["at"])
        out["status"] = "LIVE" if entry["id"] != "sam" else "NOT_RUN"
        out["last_success"] = last["at"]
        out["last_count"] = last.get("count")
        out["stale"] = age > timedelta(hours=48)
        if entry["id"] == "sam":
            out["reason"] = "ключ есть, но адаптер ни разу не проверен на реальных данных этой машины"
    return out
