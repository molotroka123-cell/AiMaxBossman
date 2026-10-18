"""Parsing tests for the browser-read NEN connector. Fixtures are saved portal pages; no live network."""
from datetime import datetime, timezone
from pathlib import Path

from warterminal.connectors import browser_nen as bn

FX = Path(__file__).parent / "fixtures_browser"
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def test_list_url_grammar():
    u = bn.list_url(buyer="Ministerstvo obrany", deadline_from="2026-10-18", page=2)
    assert u == "https://nen.nipez.cz/en/verejne-zakazky/p:vz:stavZP=neukoncena&zadavatelNazev=Ministerstvo%20obrany&podaniLhuta=2026-10-18%2C&page=2"
    assert bn.list_url(status=None) == "https://nen.nipez.cz/en/verejne-zakazky/"


def test_parse_list_rows_and_pages():
    r = bn.parse_list((FX / "nen_list_mo.html").read_text(encoding="utf-8"))
    assert len(r["rows"]) == 50 and r["last_page"] >= 2
    row = r["rows"][0]
    assert row["nen_number"].startswith("N006/") and row["detail_path"].startswith("/en/verejne-zakazky/detail-zakazky/")
    assert {x["buyer"] for x in r["rows"]} == {"Ministerstvo obrany"}


def test_parse_and_normalize_detail():
    d = bn.parse_detail((FX / "nen_detail_ammo.html").read_text(encoding="utf-8"))
    assert d["nen system number"] == "N006/26/V00033798" and "35331500-8" in d["_cpv"]
    url = "https://nen.nipez.cz/en/verejne-zakazky/detail-zakazky/N006-26-V00033798"
    t = bn.normalize(d, url, "2026-10-18T12:00:00Z", NOW)
    assert t["id"] == "cz:RVZ2600292570" and t["jurisdiction"] == "CZ" and t["source"] == "browser"
    assert t["estimated_value"] == 123966.94 and t["currency"] == "CZK"
    assert t["deadline"] == "2026-10-26T22:59:00Z"          # 23:59 Prague (CET after the 25 Oct DST change is +1; CEST +2 until then)
    assert t["status"] == "open" and t["category"] == "security_defence" and t["buyer"] == "Ministerstvo obrany"
    assert t["provenance"]["url"] == url and t["source_url"] == url
    assert "@" not in repr(t)                                # no contact personal data leaks


def test_missing_fields_stay_null_and_past_deadline_closes():
    t = bn.normalize({"nen system number": "N1", "current status of the procurement procedure": "Not terminated",
                      "deadline for submitting tenders": "01.01.2026, 10:00"}, "https://x/y", "2026-10-18T12:00:00Z", NOW)
    assert t["estimated_value"] is None and t["currency"] is None and t["cpv"] == [] and t["status"] == "closed"
    assert bn.normalize({}, "u", "t") is None


def test_money_and_dates():
    assert bn._money("1 234 567,89") == 1234567.89 and bn._money("0") is None and bn._money("abc") is None
    assert bn._dt("27.10.2026, 10:00") == "2026-10-27T09:00:00Z" and bn._dt("") is None


def test_relevance_prefers_ammo_over_chairs():
    ammo = {"buyer": "Ministerstvo obrany", "title": "Malorážkové náboje", "cpv": ["35331500-8"], "estimated_value": 123966.94}
    chair = {"buyer": "Obec X", "title": "Židle", "cpv": ["39100000-3"], "estimated_value": 1000}
    assert bn.relevance(ammo) >= 80 and bn.relevance(chair) == 0


def test_ingest_browser_endpoint_dedupes_and_rejects(tmp_path):
    from conftest import Env
    e = Env(tmp_path)
    d = bn.parse_detail((FX / "nen_detail_ammo.html").read_text(encoding="utf-8"))
    t = bn.normalize(d, "https://nen.nipez.cz/en/verejne-zakazky/detail-zakazky/N006-26-V00033798", "2026-10-18T12:00:00Z", NOW)
    bad = dict(t, source_url="http://insecure", official_notice_id="X1")
    r = e.req("POST", "/ingest/browser", "analyst", json={"portal": "nen", "tenders": [t, t, bad]}).json()
    assert (r["new"], r["duplicates"], r["rejected"]) == (1, 1, 1)
    assert e.req("POST", "/ingest/browser", "viewer", json={"portal": "nen", "tenders": []}).status_code == 403
    got = e.req("GET", "/tenders/CZ:RVZ2600292570", "viewer").json()
    assert got.get("source") == "browser" or got.get("tender", {}).get("source") == "browser"
