"""Official keyless sources: OFAC/UN sanctions, CNB FX, UCDP conflicts. Payloads are tiny copies of the real formats."""
import io
import zipfile

from fastapi.testclient import TestClient

from conftest import Env, TOK, TODAY
from fixtures import fake_get, fake_post
from warterminal.api import create_app
from warterminal.connectors import cnb, sanctions, ucdp

OFAC = (b'36,"AEROCARIBBEAN AIRLINES",-0- ,"CUBA",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \r\n'
        b'9001,"MESTO BRNO TRADING LLC","entity","TEST",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \r\n')
UN = (b"<CONSOLIDATED_LIST><INDIVIDUALS><INDIVIDUAL><REFERENCE_NUMBER>CDi.001</REFERENCE_NUMBER><FIRST_NAME>ERIC</FIRST_NAME>"
      b"<SECOND_NAME>BADEGE</SECOND_NAME><INDIVIDUAL_ALIAS><ALIAS_NAME>E. Badege</ALIAS_NAME></INDIVIDUAL_ALIAS></INDIVIDUAL></INDIVIDUALS>"
      b"<ENTITIES><ENTITY><REFERENCE_NUMBER>QDe.001</REFERENCE_NUMBER><FIRST_NAME>SOME FRONT COMPANY</FIRST_NAME></ENTITY></ENTITIES></CONSOLIDATED_LIST>")
CNB = b"16 Oct 2026 #201\nCountry|Currency|Amount|Code|Rate\nEMU|euro|1|EUR|24.325\nUSA|dollar|1|USD|21.622\nJapan|yen|100|JPY|14.500\n"


def _zip(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


GED = _zip("GEDEvent_v25_1.csv", "id,year,country,region,latitude,longitude,best\n1,2024,Ukraine,Europe,48.0,37.0,1200\n"
                                "2,2024,Ukraine,Europe,47.0,35.0,300\n3,2024,Mali,Africa,14.0,-4.0,50\n4,2023,Mali,Africa,14.0,-4.0,999\n")
ACD = _zip("UcdpPrioConflict_v25_1.csv", "conflict_id,location,side_a,side_b,intensity_level,type_of_conflict,year\n"
                                         "11347,Mali,Government of Mali,JNIM,1,4,2024\n"
                                         "200,Russia (Soviet Union),Government of Russia,Government of Ukraine,2,2,2024\n13,Old,Gov,Rebels,1,3,2023\n")
FILES = {sanctions.OFAC_URL: OFAC, sanctions.UN_URL: UN, cnb.URL: CNB, ucdp.GED_URL: GED, ucdp.ACD_URL: ACD}


def env_live(tmp_path):
    e = Env.__new__(Env)
    e.app = create_app(tmp_path, live=True, tokens=TOK, post=fake_post(), get=fake_get, today=TODAY, download=lambda url: FILES[url])
    e.c, e.store = TestClient(e.app), e.app.state.store
    return e


def test_parsers_on_real_formats():
    o = sanctions.parse_ofac(OFAC)
    assert [r["name"] for r in o] == ["AEROCARIBBEAN AIRLINES", "MESTO BRNO TRADING LLC"] and o[0]["type"] == "entity"
    u = sanctions.parse_un(UN)
    assert {r["name"] for r in u} == {"ERIC BADEGE", "E. Badege", "SOME FRONT COMPANY"}
    fx = cnb.parse(CNB.decode())
    assert fx["as_of"] == "2026-10-16" and fx["rates"]["JPYCZK"] == 0.145 and fx["rates"]["USDCZK"] == 21.622
    g = ucdp.parse_ged(GED, "now")
    assert g["year"] == 2024 and [(i["country"], i["deaths"], i["severity"]) for i in g["items"]] == [("Ukraine", 1500, "high"), ("Mali", 50, "low")]
    assert g["items"][0]["lat"] == 47.5 and g["items"][0]["demo"] is False and "не координаты событий" in g["items"][0]["precision"]
    a = ucdp.parse_acd(ACD)
    assert a["year"] == 2024 and len(a["items"]) == 2 and {i["intensity"] for i in a["items"]} == {"war", "minor"}


def test_live_fetches_are_refused_when_live_is_off(tmp_path):
    e = Env(tmp_path, live=False)
    for path, role in (("/sanctions/fetch", "compliance"), ("/fx/fetch", "analyst"), ("/conflicts/fetch", "compliance")):
        r = e.req("POST", path, role, json={})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "LIVE_DISABLED"


def test_sanctions_fetch_screens_buyers_and_suppliers(tmp_path):
    e = env_live(tmp_path)
    assert e.req("POST", "/sanctions/fetch", "analyst", json={}).status_code == 403          # compliance+
    r = e.req("POST", "/sanctions/fetch", "compliance", json={"sources": ["ofac", "un"]}).json()
    assert r["by_source"] == {"ofac": 2, "un": 3} and r["stored"] == 5 and len(r["sha256"]["ofac"]) == 64
    st = e.req("GET", "/sanctions/status", "viewer").json()
    assert st["count"] == 5 and st["as_of"] == "2026-10-17"
    e.ingest_ted()
    a = e.req("POST", "/assessments", "analyst", json={"tender_key": "EU:100002-2026"}).json()          # buyer "Social services"
    assert next(g for g in a["gates"] if g["id"] == "counterparty_screening")["status"] == "PASS"
    sup = e.req("POST", "/suppliers", "analyst", json={"name": "Mesto Brno Trading LLC", "country": "CZ"}).json()
    assert sup["screening"] == "POSSIBLE_MATCH"
    assert e.req("POST", "/suppliers", "analyst", json={"name": "Honest Fuel sro", "country": "CZ"}).json()["screening"] == "CLEAR"


def test_cnb_rates_make_cross_currency_values_known(tmp_path):
    e = env_live(tmp_path)
    e.ingest_ted()
    before = e.req("POST", "/assessments", "analyst", json={"tender_key": "EU:100002-2026"}).json()      # 52000 EUR
    assert before["usd_value"] is None
    assert e.req("POST", "/fx/fetch", "analyst", json={}).json() == {"as_of": "2026-10-16", "pairs": 3, "source": "ČNB daily fixing"}
    after = e.req("POST", "/assessments", "analyst", json={"tender_key": "EU:100002-2026"}).json()
    assert after["usd_value"] == "58500.60"                                # 52000 * 24.325 / 21.622 via CZK
    czk = e.req("POST", "/assessments", "analyst", json={"tender_key": "EU:100001-2026"}).json()
    assert czk["usd_value"] == "11562.30"                                   # 250000 CZK / 21.622


def test_conflicts_fetch_gives_a_real_sourced_layer(tmp_path):
    e = env_live(tmp_path)
    r = e.req("POST", "/conflicts/fetch", "compliance", json={}).json()
    assert r == {"year": 2024, "countries": 2, "events": 3, "acd_conflicts": 2}
    c = e.req("GET", "/conflicts?include_demo=false", "viewer").json()
    assert c["status"] == "OK" and c["real_count"] == 2 and all(not i["demo"] and i["source_url"].startswith("https://ucdp") for i in c["items"])
    assert c["acd"]["year"] == 2024 and len(c["acd"]["items"]) == 2
    rows = {s["id"]: s for s in e.req("GET", "/sources/status", "viewer").json()["sources"]}
    assert rows["ucdp"]["status"] == "LIVE" and rows["ofac"]["status"] == "NOT_RUN"


def test_map_layers_join_tenders_and_ucdp_conflicts_by_iso3(tmp_path):
    e = env_live(tmp_path)
    e.ingest_ted()
    e.req("POST", "/conflicts/fetch", "compliance", json={})
    m = e.req("GET", "/map/layers", "viewer").json()
    c = m["countries"]
    assert c["CZE"]["tenders"] >= 1 and c["UKR"]["conflict_deaths"] == 1500 and c["UKR"]["severity"] == "high"
    assert c["MLI"]["conflict_deaths"] == 50 and c["RUS"]["tenders"] == 1          # "Russia (Soviet Union)" style names map too
    assert m["unmapped_conflicts"] == [] and m["geometry"].endswith("countries-110m.geojson") and "Natural Earth" in m["geometry_source"]
