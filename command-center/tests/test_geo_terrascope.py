"""geo.* tools adopted from Terrascope (MIT, pinned f37163d8) — no network: recorded fixture responses + fakes.

Covers: parsers per source (OpenSky, USGS, Open-Meteo, Natural Earth), country border / bbox filtering, size limits,
timeout / HTTP error / bad JSON -> honest «данные недоступны» (never a number), 60 s cache + polite interval,
day/night maths, braille render width-bounded and ANSI-free for chat, tool registry permission class, chat-only
agent gets nothing, and parity of the vendored code with the pinned upstream clone (skipped when it is absent).
Live proof: tools/prove_geo_terrascope.py -> docs/audits/2026-10-25-terrascope/receipt.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc.features import tools_geo
from bcc.geo import service as gs
from bcc.geo import terrascope_vendor as ts
from bcc.permissions import is_dangerous
from bcc.tools import REGISTRY, allowed_tools_for, decide_effect

FIX = Path(__file__).parent / "fixtures" / "geo"

# Synthetic Natural Earth: a pentagon «Czechia» (bbox 12..19 x 48.5..51) whose south edge is slanted, so two of
# the fixture aircraft are inside the bbox but outside the borders; Prague twice (a small US namesake).
COUNTRIES = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"ADMIN": "Czechia", "NAME": "Czechia", "NAME_RU": "Чехия", "ISO_A2": "CZ",
                                       "ISO_A3": "CZE"},
     "geometry": {"type": "Polygon", "coordinates": [[[12, 49], [15, 48.5], [19, 49], [19, 51], [12, 51], [12, 49]]]}},
    {"type": "Feature", "properties": {"ADMIN": "Antarctica", "NAME": "Antarctica", "CONTINENT": "Antarctica"},
     "geometry": {"type": "Polygon", "coordinates": [[[0, -80], [10, -80], [10, -70], [0, -80]]]}},
]}
PLACES = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"NAME": "Prague", "ADM0NAME": "Czechia", "POP_MAX": 1264000,
                                       "FEATURECLA": "Admin-0 capital"},
     "geometry": {"type": "Point", "coordinates": [14.4208, 50.088]}},
    {"type": "Feature", "properties": {"NAME": "Prague", "ADM0NAME": "United States of America", "POP_MAX": 2400},
     "geometry": {"type": "Point", "coordinates": [-96.68, 35.48]}},
]}


def _fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


class FakeNet:
    """Routes URLs to recorded bodies; records every call. A value that is an Exception is raised."""

    def __init__(self, routes: dict):
        self.routes, self.urls = routes, []

    def __call__(self, url: str, timeout: float) -> bytes:
        self.urls.append((url, timeout))
        for key, body in self.routes.items():
            if key in url:
                if isinstance(body, BaseException):
                    raise body
                return body
        raise urllib.error.URLError("no route in test")


class Clock:
    def __init__(self):
        self.t, self.slept = 1000.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


def _svc(tmp_path, extra: dict | None = None, clock: Clock | None = None):
    routes = {gs.COUNTRIES_FILE: json.dumps(COUNTRIES).encode(), gs.PLACES_FILE: json.dumps(PLACES).encode(),
              "opensky-network.org": _fx("opensky_states_bbox.json"), "earthquake.usgs.gov": _fx("usgs_4.5_day.json"),
              "api.open-meteo.com": _fx("open_meteo_current.json")}
    routes.update(extra or {})
    net = FakeNet(routes)
    clock = clock or Clock()
    http = gs.Http(net, clock=clock, sleep=clock.sleep)
    return gs.GeoService(tmp_path / "geo", http=http), net


# ------------------------------------------------------------------ parsers on recorded responses

def test_opensky_parser_keeps_only_airborne_valid_rows():
    states = json.loads(_fx("opensky_states_bbox.json"))["states"]
    flights = ts.normalize_states(states)
    assert len(states) == 8 and len(flights) == 6                    # on-ground + malformed row dropped
    assert all(f["on_ground"] is False for f in flights)
    assert flights[0]["callsign"] == "PGT98WZ" and flights[0]["icao24"] == flights[0]["icao24"].upper()


def test_usgs_parser_and_open_meteo_parser():
    quakes = ts.normalize_features(json.loads(_fx("usgs_4.5_day.json"))["features"])
    assert len(quakes) == 8 and quakes[0]["magnitude"] == 5.9 and "Nagqu" in quakes[0]["place"]
    cur = ts.parse_current_weather(json.loads(_fx("open_meteo_current.json")))
    assert cur["temperature"] == 10.5 and ts.describe_weather_code(cur["weathercode"]) == "Slight rain"
    with pytest.raises(ValueError):
        ts.parse_current_weather({"hourly": {}})


def test_natural_earth_parsers_skip_antarctica_and_pick_capital():
    rings = ts.load_polygons(COUNTRIES["features"])
    assert len(rings) == 1 and rings[0][1:] == (12.0, 19.0, 48.5, 51.0)
    places = ts.parse_places(PLACES["features"])
    assert places[0][2:5] == ("Prague", True, 1264000)


# ------------------------------------------------------------------ aircraft: borders, bbox, limits

def test_aircraft_over_country_filters_by_borders_and_limits(tmp_path):
    svc, net = _svc(tmp_path)
    r = svc.aircraft(region="Чехия", limit=2)
    assert r["area"] == "Czechia" and r["within_borders"] is True
    assert r["bbox_count"] == 6 and r["count"] == 4                  # 2 below the slanted south border
    assert len(r["items"]) == 2 and r["truncated"] is True
    assert [i["callsign"] for i in r["items"]] == sorted(i["callsign"] for i in r["items"])
    url = next(u for u, _ in net.urls if "opensky" in u)
    assert "lamin=48.5000" in url and "lomax=19.0000" in url        # bbox query, not the global 4-credit feed
    assert r["data_time_utc"].startswith("20")


def test_aircraft_bbox_mode_and_too_large_area(tmp_path):
    svc, _ = _svc(tmp_path)
    r = svc.aircraft(bbox=[15.0, 48.0, 16.0, 50.0], limit=50)
    assert r["within_borders"] is False and r["count"] == r["bbox_count"] == 3
    with pytest.raises(gs.GeoInputError):
        svc.aircraft(region="world")
    with pytest.raises(gs.GeoInputError):
        svc.aircraft(bbox=[20, 50, 10, 40])                          # inverted
    with pytest.raises(gs.GeoInputError):
        svc.aircraft(region="Atlantis")


# ------------------------------------------------------------------ weather / earthquakes / day-night

def test_weather_for_russian_city_name(tmp_path):
    svc, net = _svc(tmp_path)
    w = svc.weather(city="Прага")
    assert (w["place"], w["country"], w["temperature_c"], w["conditions"]) == ("Prague", "Czechia", 10.5,
                                                                             "Slight rain")
    assert "latitude=50.0880" in next(u for u, _ in net.urls if "open-meteo" in u)   # the capital, not Oklahoma


def test_earthquakes_threshold_window_feed_and_strongest(tmp_path):
    svc, net = _svc(tmp_path)
    feats = json.loads(_fx("usgs_4.5_day.json"))["features"]
    newest = max(f["properties"]["time"] for f in feats) / 1000
    r = svc.earthquakes(min_magnitude=5.0, hours=24, now=newest + 60)
    assert r["feed"] == "4.5_day" and r["count"] == 4
    assert r["strongest"]["magnitude"] == 5.9 and "Nagqu" in r["strongest"]["place"]
    assert [i["magnitude"] for i in r["items"]] == sorted((i["magnitude"] for i in r["items"]), reverse=True)
    r1 = svc.earthquakes(min_magnitude=4.5, hours=1, now=newest + 60)
    assert r1["count"] == sum(1 for f in feats if f["properties"]["time"] / 1000 >= newest + 60 - 3600)
    svc.earthquakes(min_magnitude=2.5, hours=48, now=newest)
    assert any("2.5_week" in u for u, _ in net.urls)
    r3 = svc.earthquakes(min_magnitude=4.5, hours=24, limit=3, now=newest + 60)
    assert len(r3["items"]) == 3 and r3["truncated"] is True


def test_daynight_prague_noon_vs_midnight():
    svc = gs.GeoService(Path("unused"), http=gs.Http(FakeNet({})))
    noon = svc.daynight(lat=50.088, lon=14.4208, city="Prague", at=datetime(2026, 6, 21, 11, 0, tzinfo=timezone.utc))
    night = svc.daynight(lat=50.088, lon=14.4208, city="Prague", at=datetime(2026, 6, 21, 23, 0, tzinfo=timezone.utc))
    assert noon["is_day"] is True and noon["phase"] == "Day" and 60 < noon["sun_elevation_deg"] < 64
    assert night["is_day"] is False and night["phase"] in ("Night", "Astronomical twilight", "Nautical twilight")
    assert noon["sunrise_utc"].startswith("02:") and noon["sunset_utc"].startswith("19:")


# ------------------------------------------------------------------ failures -> honest unavailable

@pytest.mark.parametrize("exc, reason", [(TimeoutError("timed out"), "TimeoutError"),
                                         (urllib.error.URLError("dns"), "URLError"),
                                         (urllib.error.HTTPError("u", 429, "Too Many", {}, None), "HTTP 429")])
def test_network_failure_is_unavailable_not_invented(tmp_path, exc, reason):
    svc, _ = _svc(tmp_path, {"opensky-network.org": exc, "api.open-meteo.com": exc, "earthquake.usgs.gov": exc})
    for call in (lambda: svc.aircraft(region="CZ"), lambda: svc.weather(city="Prague"),
                 lambda: svc.earthquakes()):
        with pytest.raises(gs.GeoUnavailable) as e:
            call()
        assert gs.UNAVAILABLE in str(e.value) and reason in str(e.value)


def test_bad_json_is_unavailable_and_not_cached(tmp_path):
    svc, net = _svc(tmp_path, {"earthquake.usgs.gov": b"<html>oops</html>"})
    for _ in range(2):
        with pytest.raises(gs.GeoUnavailable):
            svc.earthquakes()
    assert sum("usgs" in u for u, _ in net.urls) == 2                # not served from cache the second time


def test_cache_60s_and_polite_interval(tmp_path):
    clock = Clock()
    svc, net = _svc(tmp_path, clock=clock)
    svc.aircraft(region="CZ")
    svc.aircraft(region="CZ")
    assert sum("opensky" in u for u, _ in net.urls) == 1             # cached
    clock.t += 61
    svc.aircraft(region="CZ")
    assert sum("opensky" in u for u, _ in net.urls) == 2             # TTL expired -> refetch
    svc.aircraft(bbox=[13, 49, 14, 50])                              # different URL right after: polite wait
    assert clock.slept and clock.slept[-1] == pytest.approx(gs.POLITE_INTERVAL_S["opensky-network.org"])
    timeouts = {u.split("/")[2]: t for u, t in net.urls}
    assert timeouts["opensky-network.org"] == gs.TIMEOUT_S["opensky-network.org"]


# ------------------------------------------------------------------ braille map

def test_map_render_bounded_plain_and_ansi(tmp_path):
    svc, _ = _svc(tmp_path)
    m = svc.map_render(region="Czechia", width=60, height=16, ansi=True)
    lines = m["text"].split("\n")
    assert len(lines) == 16 and all(len(line) <= 60 for line in lines)
    assert "\x1b" not in m["text"] and "\x1b[" in m["ansi"]
    assert sum(ch not in (ts.BLANK_BRAILLE, " ") for ch in m["text"]) > 20     # the border is drawn
    big = svc.map_render(region="world", width=999, height=999)
    assert big["width"] == 160 and big["height"] == 60
    over = svc.map_render(region="Czechia", width=60, height=16, layers=["aircraft"])
    assert "✈" in over["text"]


# ------------------------------------------------------------------ registry, permissions, chat-only

async def test_tools_registered_as_network_read(tmp_path):
    await tools_geo._setup(None)
    for name in ("geo.aircraft", "geo.weather", "geo.earthquakes", "geo.daynight", "geo.map_render"):
        spec = REGISTRY.get(name)
        assert spec is not None, name
        assert (spec.category, spec.permission, spec.default_effect) == ("read", "browser.read", "auto")
        assert not is_dangerous(spec.permission) and spec.external_output
        local = {"id": 41, "tools": ["geo.*"], "permissions": {"browser.read": True}}
        assert decide_effect(spec, {}, local)[0] == "auto"


async def test_chat_only_agent_gets_no_geo_tools():
    await tools_geo._setup(None)
    local = {"id": 41, "tools": ["geo.*"], "permissions": {"browser.read": True}}
    cloud = {"id": 50, "tools": ["geo.*"], "permissions": {"cloud_pc_access": False}}
    assert {s.name for s in REGISTRY.resolve(allowed_tools_for({"meta": {}}, local))} >= set(tools_geo.TOOL_NAMES)
    assert allowed_tools_for({"meta": {}}, cloud) == []
    assert REGISTRY.resolve(allowed_tools_for({"meta": {"allowed_tools": ["geo.*"]}}, cloud)) == []


async def test_tool_handler_unavailable_says_so(tmp_path, monkeypatch):
    svc, _ = _svc(tmp_path, {"opensky-network.org": TimeoutError("t")})
    monkeypatch.setattr(tools_geo, "service_for", lambda _d: svc)
    ctx = SimpleNamespace(svc=SimpleNamespace(settings=SimpleNamespace(data_dir=str(tmp_path))))
    res = await tools_geo._t_aircraft({"region": "Czechia"}, ctx)
    assert res.error and "данные недоступны" in res.content and "OpenSky" in res.content
    assert not any(ch.isdigit() for ch in res.content.split("(")[0])   # no count invented
    ok = await tools_geo._t_weather({"city": "Prague"}, ctx)
    assert not ok.error and "10.5 °C" in ok.content and ok.data["temperature_c"] == 10.5
    bad = await tools_geo._t_weather({}, ctx)
    assert bad.error and "ошибка аргументов" in bad.content


async def test_tool_handler_aircraft_text_matches_data(tmp_path, monkeypatch):
    svc, _ = _svc(tmp_path)
    monkeypatch.setattr(tools_geo, "service_for", lambda _d: svc)
    ctx = SimpleNamespace(svc=SimpleNamespace(settings=SimpleNamespace(data_dir=str(tmp_path))))
    res = await tools_geo._t_aircraft({"region": "CZ", "limit": 3}, ctx)
    assert f": {res.data['count']} (в bbox {res.data['bbox_count']})" in res.content
    for it in res.data["items"]:
        assert it["callsign"] in res.content
    m = await tools_geo._t_map({"region": "CZ", "width": 40, "height": 10}, ctx)
    assert not m.error and "\x1b" not in m.content and len(m.content) <= tools_geo.MAX_CONTENT


# ------------------------------------------------------------------ parity with the pinned upstream clone

def _clone_src() -> Path:
    env = os.environ.get("BCC_TERRASCOPE_SRC")
    return Path(env) if env else Path(__file__).resolve().parents[2].parent / "apps-local" / "terrascope" / "src"


PARITY = r"""
import json, sys, types
from datetime import datetime, timezone
sys.path.insert(0, sys.argv[1])
try:
    import curses  # noqa: F401
except ImportError:              # Windows: upstream subpackage __init__s import the curses UI; stub it for parity only
    class _Curses(types.ModuleType):
        def __getattr__(self, name):
            return 0
    sys.modules["curses"] = _Curses("curses")
from terrascope.layers.flights.api import normalize_states
from terrascope.layers.flights.formatting import categorize
from terrascope.layers.daynight import astronomy as a
states = json.load(open(sys.argv[2], encoding="utf-8"))["states"]
fl = normalize_states(states)
now = datetime(2026, 10, 25, 14, 0, tzinfo=timezone.utc)
d, e = a.solar_position(now)
print(json.dumps({"flights": fl, "cats": [categorize(f) for f in fl], "dec": d, "eq": e,
                  "elev": a.sun_elevation(50.088, 14.42, now, d, e), "rs": a.sunrise_sunset(50.088, 14.42, d, e)}))
"""


def test_vendored_code_matches_pinned_upstream(tmp_path):
    src = _clone_src()
    if not (src / "terrascope" / "layers" / "flights" / "api.py").is_file():
        pytest.skip("pinned Terrascope clone not present (apps-local/terrascope)")
    env = dict(os.environ, XDG_CONFIG_HOME=str(tmp_path / "cfg"), XDG_CACHE_HOME=str(tmp_path / "cache"),
               terrascope_OFFLINE="1", PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, "-c", PARITY, str(src), str(FIX / "opensky_states_bbox.json")],
                         capture_output=True, text=True, env=env, timeout=120)
    if out.returncode != 0 and "No module named" in out.stderr:
        pytest.skip(f"upstream deps missing: {out.stderr.strip().splitlines()[-1]}")
    assert out.returncode == 0, out.stderr
    up = json.loads(out.stdout)
    fl = ts.normalize_states(json.loads(_fx("opensky_states_bbox.json"))["states"])
    assert up["flights"] == json.loads(json.dumps(fl)) and up["cats"] == [ts.categorize(f) for f in fl]
    now = datetime(2026, 10, 25, 14, 0, tzinfo=timezone.utc)
    d, e = ts.solar_position(now)
    assert (up["dec"], up["eq"]) == pytest.approx((d, e))
    assert up["elev"] == pytest.approx(ts.sun_elevation(50.088, 14.42, now, d, e))
    assert up["rs"] == pytest.approx(list(ts.sunrise_sunset(50.088, 14.42, d, e)))
