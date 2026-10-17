"""Five public APIs for country context: World Bank military expenditure, UNHCR, ECB (Frankfurter), Nominatim, UN Comtrade."""
from fastapi.testclient import TestClient

from conftest import Env, TOK, TODAY
from fixtures import fake_post, gdelt_payload
from test_live_sources import FILES
from warterminal.api import create_app
from warterminal.connectors import cnb, indicators

WB = [{"page": 1, "lastupdated": "2026-10-08"}, [
    {"country": {"id": "CZ", "value": "Czechia"}, "countryiso3code": "CZE", "date": "2025", "value": 7100000000.0},
    {"country": {"id": "UA", "value": "Ukraine"}, "countryiso3code": "UKR", "date": "2025", "value": 64700000000.0},
    {"country": {"id": "ZH", "value": "Africa Eastern and Southern"}, "countryiso3code": "AFE", "date": "2025", "value": 1.0},
    {"country": {"id": "XX", "value": "Nothing"}, "countryiso3code": "", "date": "2025", "value": 5.0}]]
UNHCR = {"maxPages": 1, "items": [{"year": 2024, "coo_iso": "UKR", "coo_name": "Ukraine", "refugees": 5900000, "asylum_seekers": 10, "idps": 0},
                                  {"year": 2024, "coo_iso": "-", "coo_name": "-", "refugees": 1}]}
ECB = {"base": "EUR", "date": "2026-10-16", "rates": {"CZK": 24.323, "USD": 1.125}}
NOM = [{"lat": "50.0874654", "lon": "14.4212535", "display_name": "Praha, Česko", "licence": "Data © OpenStreetMap contributors, ODbL 1.0."}]
CT = {"data": [{"partnerCode": 0, "primaryValue": 9000.0}, {"partnerCode": 276, "primaryValue": 5000.0}, {"partnerCode": 703, "primaryValue": 3000.0}]}
calls = []


def fake_get(url, params=None, **kw):
    calls.append(url)
    if url == indicators.WB_MIL:
        return WB
    if url == indicators.UNHCR:
        return UNHCR
    if url == indicators.FRANKFURTER:
        return ECB
    if url == indicators.NOMINATIM:
        return NOM
    if url == indicators.COMTRADE:
        return CT
    if "gdeltproject" in url:
        return gdelt_payload()
    raise AssertionError(url)


def env(tmp_path, live=True):
    e = Env.__new__(Env)
    e.app = create_app(tmp_path, live=live, tokens=TOK, post=fake_post(), get=fake_get, today=TODAY, download=lambda u: FILES[u])
    e.c, e.store = TestClient(e.app), e.app.state.store
    return e


def test_parsers_drop_aggregates_and_blank_codes():
    m = indicators.military_expenditure(fake_get)
    assert set(m["items"]) == {"CZE", "UKR", "AFE"} and m["items"]["CZE"]["usd"] == 7.1e9 and m["last_updated"] == "2026-10-08"
    r = indicators.refugees_by_origin(fake_get, 2024)
    assert r["items"] == {"UKR": {"refugees": 5900000, "asylum_seekers": 10, "idps": 0, "year": 2024, "name": "Ukraine"}}
    t = indicators.trade_flows(fake_get, 203, "27", "M", 2024)
    assert t["world_total_usd"] == 9000.0 and [p["partner_code"] for p in t["partners"]] == [276, 703]


def test_indicators_fetch_feeds_country_view_and_map(tmp_path):
    e = env(tmp_path)
    r = e.req("POST", "/indicators/fetch", "analyst", json={}).json()
    assert r["military_countries"] == 3 and r["refugee_countries"] == 1 and r["ecb_as_of"] == "2026-10-16" and r["errors"] == {}
    ua = e.req("GET", "/indicators/country/ukr", "viewer").json()
    assert ua["military_expenditure"]["usd"] == 6.47e10 and ua["refugees"]["refugees"] == 5900000
    layers = e.req("GET", "/map/layers", "viewer").json()["countries"]
    assert layers["UKR"]["military_usd"] == 6.47e10 and layers["UKR"]["refugees"] == 5900000
    assert "AFE" not in layers                                    # regional aggregate is not a country on the map


def test_fx_crosscheck_compares_cnb_with_ecb(tmp_path):
    e = env(tmp_path)
    assert e.req("GET", "/fx/crosscheck", "viewer").json()["status"] == "NOT_RUN"
    e.req("POST", "/fx/fetch", "analyst", json={})
    e.req("POST", "/indicators/fetch", "analyst", json={})
    x = e.req("GET", "/fx/crosscheck", "viewer").json()
    assert x["status"] == "OK" and x["eur_czk_cnb"] == 24.325 and x["eur_czk_ecb"] == 24.323 and x["diff_pct"] < 0.1


def test_geocode_is_cached_and_trade_is_validated(tmp_path):
    e = env(tmp_path)
    calls.clear()
    a = e.req("GET", "/geocode?q=Prague", "analyst").json()
    b = e.req("GET", "/geocode?q=prague", "analyst").json()
    assert a["cached"] is False and b["cached"] is True and calls.count(indicators.NOMINATIM) == 1 and "OpenStreetMap" in a["result"]["licence"]
    assert e.req("GET", "/geocode?q=Prague", "viewer").status_code == 403
    t = e.req("GET", "/trade?reporter=203&hs=27&flow=M&year=2024", "viewer").json()
    assert t["partners"][0] == {"partner_code": 276, "value_usd": 5000.0}
    assert e.req("GET", "/trade?flow=Z", "viewer").status_code == 422


def test_live_off_blocks_the_new_external_calls(tmp_path):
    e = env(tmp_path, live=False)
    assert e.req("POST", "/indicators/fetch", "analyst", json={}).status_code == 403
    assert e.req("GET", "/geocode?q=Prague", "analyst").status_code == 403
    assert e.req("GET", "/trade", "viewer").status_code == 403


def test_one_failing_source_is_reported_not_fatal(tmp_path):
    def flaky(url, params=None, **kw):
        if url == indicators.UNHCR:
            raise RuntimeError("upstream 503")
        return fake_get(url, params)
    e = Env.__new__(Env)
    e.app = create_app(tmp_path, live=True, tokens=TOK, post=fake_post(), get=flaky, today=TODAY, download=lambda u: FILES[u])
    e.c, e.store = TestClient(e.app), e.app.state.store
    r = e.req("POST", "/indicators/fetch", "analyst", json={}).json()
    assert r["military_countries"] == 3 and r["refugee_countries"] == 0 and "upstream 503" in r["errors"]["unhcr"]
