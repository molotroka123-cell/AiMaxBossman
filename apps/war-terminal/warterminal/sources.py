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
    dict(id="ukfts", name="UK Find a Tender", kind="procurement", url="https://www.find-tender.service.gov.uk/", auth="none", adapter=False, note="to validate"),
    dict(id="cz_nen", name="Czech ISVZ open data (NEN + VVZ, monthly export)", kind="procurement", url="https://isvz.nipez.cz/opendata/nova", auth="none", adapter=False,
         note="connector written and tested (connectors/cz.py) but NOT enabled: isvz.nipez.cz robots.txt says Disallow: / -> owner decision or MMR permission (ospevz@mmr.gov.cz); data lags ~1 month"),
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
    dict(id="sipri", name="SIPRI military expenditure", kind="market", url="https://www.sipri.org/", auth="none", adapter=False, note="annual context numbers"),
    dict(id="comtrade", name="UN Comtrade (preview API)", kind="market", url="https://comtradeapi.un.org/", auth="none (preview)", adapter=False, note="trade flows by HS code"),
]


def status_for(entry: dict, *, live_enabled: bool, last: dict | None, has_key: bool = False, demo_present: bool = False, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out = dict(entry)
    if entry["id"] == "demo":
        out["status"] = "DEMO"
    elif not entry["adapter"]:
        out["status"] = "NOT_CONNECTED"
        out["reason"] = "адаптер не реализован: " + entry.get("note", "")
    elif entry["id"] == "sam" and not has_key:
        out["status"] = "NOT_CONNECTED"
        out["reason"] = "нужен бесплатный API-ключ SAM.gov"
    elif not live_enabled:
        out["status"] = "DISABLED"
        out["reason"] = "WAR_TERMINAL_LIVE=false (по умолчанию)"
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
