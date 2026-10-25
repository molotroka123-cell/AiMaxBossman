"""Geo data for agents, adopted from Terrascope (MIT): live aircraft, city weather, earthquakes, day/night, braille map.

Keyless public sources only (the same ones Terrascope uses):
  * OpenSky Network REST `states/all` with a bounding box (anonymous: ~400 credits/day, 10 s resolution;
    terms: non-commercial / research use, attribution «The OpenSky Network, https://opensky-network.org»);
  * Open-Meteo forecast API (no key; free tier for non-commercial use, <10 000 calls/day, CC BY 4.0 data);
  * USGS earthquake GeoJSON summary feeds (US public domain, updated every minute);
  * Natural Earth GeoJSON (public domain) from github.com/nvkelso/natural-earth-vector — downloaded once and cached;
  * the solar position maths is local (no network).

Hardening on top of upstream: every response is cached for ``CACHE_TTL_S`` (60 s) per URL; a per-host polite
interval (OpenSky 10 s, others 1 s); hard timeouts; result-size limits; and any network/format failure raises
``GeoUnavailable`` so the caller says «данные недоступны» instead of inventing values. No keys, no personal data.
"""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PIL import ImageDraw

from . import terrascope_vendor as ts

USER_AGENT = "Bossman-geo/1.0 (+terrascope adapter; keyless public APIs)"
CACHE_TTL_S = 60.0
OPENSKY_URL = "https://opensky-network.org/api/states/all"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
USGS_FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{level}_{period}.geojson"
NATURAL_EARTH_BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
COUNTRIES_FILE = "ne_50m_admin_0_countries.geojson"
PLACES_FILE = "ne_10m_populated_places_simple.geojson"
POLITE_INTERVAL_S = {"opensky-network.org": 10.0}
DEFAULT_INTERVAL_S = 1.0
TIMEOUT_S = {"opensky-network.org": 20.0, "raw.githubusercontent.com": 60.0}
DEFAULT_TIMEOUT_S = 15.0
MAX_LIST = 50
MAX_BBOX_AREA_DEG2 = 4000.0   # OpenSky bills 1-4 credits by area; larger than ~Europe is refused here (politeness)

UNAVAILABLE = "данные недоступны"

# A few presets that are not countries. Countries resolve from Natural Earth (English, local and Russian names).
REGIONS = {
    "world": (-180.0, -58.0, 180.0, 84.0),
    "europe": (-25.0, 34.0, 45.0, 72.0),
    "central europe": (5.0, 45.0, 25.0, 56.0),
    "north america": (-170.0, 7.0, -50.0, 75.0),
    "asia": (25.0, -11.0, 180.0, 78.0),
    "japan region": (122.0, 24.0, 150.0, 46.0),
}
REGION_ALIASES = {"мир": "world", "европа": "europe", "центральная европа": "central europe",
                  "северная америка": "north america", "азия": "asia"}
# Russian / local spellings of frequently asked cities whose Natural Earth `name` is the English exonym.
PLACE_ALIASES = {"прага": "prague", "praha": "prague", "москва": "moscow", "берлин": "berlin", "вена": "vienna",
                 "wien": "vienna", "варшава": "warsaw", "лондон": "london", "париж": "paris", "рим": "rome",
                 "брно": "brno", "киев": "kyiv", "kiev": "kyiv", "токио": "tokyo", "нью-йорк": "new york",
                 "мюнхен": "munich", "münchen": "munich", "милан": "milan", "мадрид": "madrid", "пекин": "beijing"}


class GeoUnavailable(Exception):
    """A source could not deliver data (network, HTTP, format). The message is safe to show the owner."""

    def __init__(self, source: str, reason: str) -> None:
        super().__init__(f"{UNAVAILABLE}: {source} ({reason})")
        self.source, self.reason = source, reason


class GeoInputError(ValueError):
    """Bad arguments (unknown place/region, bbox too large)."""


Fetcher = Callable[[str, float], bytes]


def _urllib_fetch(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310 - fixed https hosts only
        return resp.read()


class Http:
    """GET with a 60 s TTL cache per URL, a polite per-host interval and hard timeouts. Thread-safe."""

    def __init__(self, fetch: Fetcher | None = None, *, ttl: float = CACHE_TTL_S,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self._fetch = fetch or _urllib_fetch
        self.ttl, self._clock, self._sleep = ttl, clock, sleep
        self._cache: dict[str, tuple[float, bytes]] = {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.calls = 0      # real network calls (tests/receipts)

    def get(self, url: str, source: str, *, cache: bool = True) -> bytes:
        host = urllib.parse.urlsplit(url).hostname or ""
        with self._lock:
            now = self._clock()
            hit = self._cache.get(url)
            if cache and hit and now - hit[0] < self.ttl:
                return hit[1]
            wait = POLITE_INTERVAL_S.get(host, DEFAULT_INTERVAL_S) - (now - self._last.get(host, -1e9))
            if wait > 0:
                self._sleep(wait)
            self._last[host] = self._clock()
            self.calls += 1
            try:
                body = self._fetch(url, TIMEOUT_S.get(host, DEFAULT_TIMEOUT_S))
            except urllib.error.HTTPError as exc:
                raise GeoUnavailable(source, f"HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise GeoUnavailable(source, type(exc).__name__ + (f": {exc.reason}" if hasattr(exc, "reason") else
                                                                   "")) from None
            if cache:
                self._cache[url] = (self._clock(), body)
            return body

    def get_json(self, url: str, source: str):
        body = self.get(url, source)
        try:
            return json.loads(body)
        except (ValueError, UnicodeDecodeError):
            with self._lock:
                self._cache.pop(url, None)
            raise GeoUnavailable(source, "ответ не JSON") from None


@dataclass
class Area:
    name: str
    bbox: tuple[float, float, float, float]          # min_lon, min_lat, max_lon, max_lat
    rings: list | None = None                         # country outline rings for point-in-polygon, None = bbox only


def _norm(s: str) -> str:
    return " ".join(str(s or "").strip().lower().replace("ё", "е").split())


def point_in_ring(lon: float, lat: float, points: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(points)
    j = n - 1
    for i in range(n):
        xi, yi = points[i]
        xj, yj = points[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _ring_area(points) -> float:
    return abs(sum(points[i][0] * points[i - 1][1] - points[i - 1][0] * points[i][1] for i in range(len(points)))) / 2


class GeoService:
    def __init__(self, cache_dir: Path, *, http: Http | None = None) -> None:
        self.cache_dir = Path(cache_dir)
        self.http = http or Http()
        self._ne: dict[str, list] = {}
        self._ne_lock = threading.Lock()

    # ------------------------------------------------------------ Natural Earth (downloaded once, cached on disk)

    def _ne_features(self, filename: str) -> list[dict]:
        with self._ne_lock:
            if filename in self._ne:
                return self._ne[filename]
            path = self.cache_dir / "naturalearth" / filename
            if not path.is_file():
                body = self.http.get(NATURAL_EARTH_BASE + filename, "Natural Earth", cache=False)
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_bytes(body)
                tmp.replace(path)
            try:
                features = ts.load_geojson_features(path)
            except (ValueError, OSError, UnicodeDecodeError):
                path.unlink(missing_ok=True)          # corrupt cache: drop it, next call downloads again
                raise GeoUnavailable("Natural Earth", "файл карты повреждён") from None
            self._ne[filename] = features
            return features

    def country_rings(self) -> list:
        return ts.load_polygons(self._ne_features(COUNTRIES_FILE))

    def places(self) -> list[tuple]:
        key = "_places"
        if key not in self._ne:
            self._ne[key] = ts.parse_places(self._ne_features(PLACES_FILE))
        return self._ne[key]

    def find_country(self, name: str) -> Area | None:
        want = _norm(name)
        if not want:
            return None
        for feature in self._ne_features(COUNTRIES_FILE):
            props = {str(k).upper(): v for k, v in (feature.get("properties") or {}).items()}
            names = {_norm(props.get(k) or "") for k in ("ADMIN", "NAME", "NAME_LONG", "NAME_EN", "NAME_RU",
                                                       "NAME_CS", "NAME_DE", "FORMAL_EN", "SOVEREIGNT", "GEOUNIT")}
            codes = {_norm(props.get(k) or "") for k in ("ISO_A2", "ISO_A3", "ADM0_A3")}
            if want in names or want in codes:
                rings = ts.feature_rings(feature)
                if not rings:
                    return None
                main = max(rings, key=lambda r: _ring_area(r[0]))
                return Area(str(props.get("NAME") or name), (main[1], main[3], main[2], main[4]), rings)
        return None

    def find_place(self, name: str) -> tuple[float, float, str, str]:
        want = _norm(name)
        want = PLACE_ALIASES.get(want, want)
        if not want:
            raise GeoInputError("не указан город")
        best = None
        for lon, lat, pname, _cap, pop, country in self.places():
            if _norm(pname) == want and (best is None or pop > best[4]):
                best = (lon, lat, pname, country, pop)
        if best is None:
            raise GeoInputError(f"город «{name}» не найден в Natural Earth (укажите латиницей или lat/lon)")
        return best[0], best[1], best[2], best[3]

    def resolve_area(self, region: str | None = None, bbox: list | None = None) -> Area:
        if bbox:
            try:
                lo1, la1, lo2, la2 = (float(v) for v in bbox)
            except (TypeError, ValueError):
                raise GeoInputError("bbox = [min_lon, min_lat, max_lon, max_lat]") from None
            if not (-180 <= lo1 < lo2 <= 180 and -90 <= la1 < la2 <= 90):
                raise GeoInputError("bbox вне диапазона или перевёрнут")
            return Area("bbox", (lo1, la1, lo2, la2))
        key = _norm(region or "")
        key = REGION_ALIASES.get(key, key)
        if not key:
            raise GeoInputError("укажите region (страна/регион) или bbox")
        if key in REGIONS:
            return Area(key, REGIONS[key])
        area = self.find_country(key)
        if area is None:
            raise GeoInputError(f"регион «{region}» не найден (страна по-английски/по-русски, ISO-код или bbox)")
        return area

    def _point(self, city: str | None, lat, lon) -> tuple[float, float, str, str]:
        if lat is not None and lon is not None:
            try:
                la, lo = float(lat), float(lon)
            except (TypeError, ValueError):
                raise GeoInputError("lat/lon должны быть числами") from None
            if not (-90 <= la <= 90 and -180 <= lo <= 180):
                raise GeoInputError("lat/lon вне диапазона")
            return lo, la, city or f"{la:.3f},{lo:.3f}", ""
        if city:
            return self.find_place(city)
        raise GeoInputError("укажите city или lat+lon")

    # ------------------------------------------------------------ aircraft (OpenSky)

    def aircraft(self, region: str | None = None, bbox: list | None = None, limit: int = 10) -> dict:
        area = self.resolve_area(region, bbox)
        lo1, la1, lo2, la2 = area.bbox
        if (lo2 - lo1) * (la2 - la1) > MAX_BBOX_AREA_DEG2:
            raise GeoInputError("область слишком большая для OpenSky (макс. ~размер Европы): сузьте регион")
        url = f"{OPENSKY_URL}?lamin={la1:.4f}&lomin={lo1:.4f}&lamax={la2:.4f}&lomax={lo2:.4f}"
        payload = self.http.get_json(url, "OpenSky")
        if not isinstance(payload, dict) or not isinstance(payload.get("states") or [], list):
            raise GeoUnavailable("OpenSky", "неожиданный формат ответа")
        flights = ts.normalize_states(payload.get("states") or [])
        in_bbox = [f for f in flights if lo1 <= f["longitude"] <= lo2 and la1 <= f["latitude"] <= la2]
        if area.rings:
            inside = [f for f in in_bbox
                      if any(point_in_ring(f["longitude"], f["latitude"], r[0]) for r in area.rings)]
        else:
            inside = in_bbox
        inside.sort(key=lambda f: (f["callsign"] == "", f["callsign"]))
        limit = max(1, min(int(limit or 10), MAX_LIST))
        items = [{"callsign": f["callsign"] or None, "icao24": f["icao24"], "origin_country": f["origin_country"],
                  "lat": round(f["latitude"], 3), "lon": round(f["longitude"], 3),
                  "altitude_m": None if ts.finite_float(f["baro_altitude"]) is None else round(f["baro_altitude"]),
                  "speed_kmh": None if ts.finite_float(f["velocity"]) is None else round(f["velocity"] * 3.6),
                  "category": ts.categorize(f)} for f in inside[:limit]]
        snap = payload.get("time")
        return {"source": "OpenSky Network (opensky-network.org)", "area": area.name, "bbox": list(area.bbox),
                "within_borders": bool(area.rings), "count": len(inside), "bbox_count": len(in_bbox),
                "data_time_utc": _iso(snap), "items": items, "truncated": len(inside) > limit}

    # ------------------------------------------------------------ weather (Open-Meteo)

    def weather(self, city: str | None = None, lat=None, lon=None) -> dict:
        lo, la, name, country = self._point(city, lat, lon)
        url = (f"{OPEN_METEO_URL}?latitude={la:.4f}&longitude={lo:.4f}"
               "&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,"
               "wind_speed_10m,wind_direction_10m,weather_code&timezone=auto")
        payload = self.http.get_json(url, "Open-Meteo")
        try:
            cur = ts.parse_current_weather(payload)
        except ValueError as exc:
            raise GeoUnavailable("Open-Meteo", str(exc)) from None
        raw = payload.get("current") or {}
        temp = ts.finite_float(cur["temperature"])
        if temp is None:
            raise GeoUnavailable("Open-Meteo", "нет температуры в ответе")
        return {"source": "Open-Meteo (open-meteo.com)", "place": name, "country": country,
                "lat": round(la, 4), "lon": round(lo, 4), "local_time": cur["time"],
                "timezone": payload.get("timezone"), "temperature_c": temp,
                "apparent_c": ts.finite_float(raw.get("apparent_temperature")),
                "humidity_pct": ts.finite_float(raw.get("relative_humidity_2m")),
                "precipitation_mm": ts.finite_float(raw.get("precipitation")),
                "wind_kmh": ts.finite_float(cur["windspeed"]), "wind_dir_deg": ts.finite_float(cur["winddirection"]),
                "weather_code": cur["weathercode"], "conditions": ts.describe_weather_code(cur["weathercode"])}

    # ------------------------------------------------------------ earthquakes (USGS)

    def earthquakes(self, min_magnitude: float = 4.5, hours: float = 24, region: str | None = None,
                    bbox: list | None = None, limit: int = 10, now: float | None = None) -> dict:
        try:
            mmin, hrs = float(min_magnitude), float(hours)
        except (TypeError, ValueError):
            raise GeoInputError("min_magnitude/hours должны быть числами") from None
        if not (0 < hrs <= 24 * 30):
            raise GeoInputError("hours: 1..720")
        period = "day" if hrs <= 24 else ("week" if hrs <= 24 * 7 else "month")
        level = "4.5" if mmin >= 4.5 else ("2.5" if mmin >= 2.5 else ("1.0" if mmin >= 1.0 else "all"))
        url = USGS_FEED.format(level=level, period=period)
        payload = self.http.get_json(url, "USGS")
        if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
            raise GeoUnavailable("USGS", "неожиданный формат ответа")
        quakes = ts.normalize_features(payload["features"])
        area = self.resolve_area(region, bbox) if (region or bbox) else None
        cutoff_ms = ((now if now is not None else time.time()) - hrs * 3600) * 1000
        sel = [q for q in quakes if q["magnitude"] is not None and q["magnitude"] >= mmin
               and isinstance(q["time"], (int, float)) and q["time"] >= cutoff_ms]
        if area is not None:
            lo1, la1, lo2, la2 = area.bbox
            sel = [q for q in sel if lo1 <= q["longitude"] <= lo2 and la1 <= q["latitude"] <= la2]
        sel.sort(key=lambda q: q["magnitude"], reverse=True)
        limit = max(1, min(int(limit or 10), MAX_LIST))
        items = [{"magnitude": q["magnitude"], "place": q["place"], "time_utc": _iso(q["time"] / 1000),
                  "depth_km": None if q["depth_km"] is None else round(q["depth_km"], 1),
                  "lat": round(q["latitude"], 3), "lon": round(q["longitude"], 3), "tsunami": q["tsunami"],
                  "id": q["id"]} for q in sel[:limit]]
        meta = payload.get("metadata") or {}
        return {"source": "USGS Earthquake Hazards Program (earthquake.usgs.gov)", "feed": f"{level}_{period}",
                "min_magnitude": mmin, "hours": hrs, "area": area.name if area else "world", "count": len(sel),
                "strongest": items[0] if items else None, "items": items, "truncated": len(sel) > limit,
                "feed_generated_utc": _iso((meta.get("generated") or 0) / 1000) if meta.get("generated") else None}

    # ------------------------------------------------------------ day / night (local astronomy, no network)

    def daynight(self, city: str | None = None, lat=None, lon=None, at: datetime | None = None) -> dict:
        lo, la, name, country = self._point(city, lat, lon)
        now = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        dec, eqt = ts.solar_position(now)
        elev = ts.sun_elevation(la, lo, now, dec, eqt)
        rs = ts.sunrise_sunset(la, lo, dec, eqt)
        moon = ts.moon_info(now)
        out = {"source": "Terrascope solar maths (NOAA/Meeus), local", "place": name, "country": country,
               "lat": round(la, 4), "lon": round(lo, 4), "at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "sun_elevation_deg": round(elev, 2), "phase": ts.sun_phase_name(elev), "is_day": elev >= 0,
               "moon_phase": moon["name"], "moon_illumination_pct": round(moon["illumination"] * 100)}
        if isinstance(rs, str):
            out["polar"] = rs
        else:
            out["sunrise_utc"], out["sunset_utc"] = ts.hhmm(rs[0]), ts.hhmm(rs[1])
        return out

    # ------------------------------------------------------------ braille map (Terrascope renderer)

    def map_render(self, region: str | None = None, bbox: list | None = None, width: int = 80, height: int = 24,
                   layers: list | None = None, ansi: bool = False) -> dict:
        width = max(20, min(int(width or 80), 160))
        height = max(8, min(int(height or 24), 60))
        area = self.resolve_area(region or ("world" if not bbox else None), bbox)
        lo1, la1, lo2, la2 = area.bbox
        aspect = ts.viewport_aspect(width, height)
        lat_need = max(la2 - la1, 0.5) * 1.08
        lon_need = max(lo2 - lo1, 0.5) * 1.08
        zoom = min(ts.LAT_RANGE / lat_need, ts.LAT_RANGE * aspect / lon_need)
        zoom = max(ts.ZOOM_MIN, min(ts.ZOOM_MAX, zoom))
        view = ts.View((lo1 + lo2) / 2, (la1 + la2) / 2, zoom, width, height)
        image = view.new_image()
        ts.draw_rings(self.country_rings(), view, ImageDraw.Draw(image))
        grid = [list(row) for row in view.to_lines(image)]
        overlay: dict[tuple[int, int], tuple[str, str]] = {}
        notes: list[str] = []
        wanted = {str(x).strip().lower() for x in (layers or [])}
        if "aircraft" in wanted:
            try:
                for it in self.aircraft(bbox=list(view_bbox(view)), limit=MAX_LIST)["items"]:
                    cell = view.to_cell(it["lon"], it["lat"])
                    if cell:
                        overlay[cell] = ("✈", "\x1b[33m")
                notes.append("✈ = самолёт (OpenSky)")
            except (GeoUnavailable, GeoInputError) as exc:
                notes.append(f"самолёты: {exc}")
        if "earthquakes" in wanted:
            try:
                eq = self.earthquakes(min_magnitude=2.5, hours=24 * 7, limit=MAX_LIST)
                for it in eq["items"]:
                    cell = view.to_cell(it["lon"], it["lat"])
                    if cell:
                        overlay[cell] = ("*", "\x1b[31m")
                notes.append("* = землетрясение M2.5+ за 7 дней (USGS)")
            except GeoUnavailable as exc:
                notes.append(f"землетрясения: {exc}")
        plain_rows, ansi_rows = [], []
        for y, row in enumerate(grid):
            prow = list(row)
            arow = []
            for x, ch in enumerate(row):
                mark = overlay.get((x, y))
                if mark:
                    prow[x] = mark[0]
                    arow.append(f"{mark[1]}{mark[0]}\x1b[0m")
                else:
                    arow.append(ch)
            plain_rows.append("".join(prow).rstrip(ts.BLANK_BRAILLE) or ts.BLANK_BRAILLE)
            ansi_rows.append("\x1b[37m" + "".join(arow) + "\x1b[0m")
        out = {"source": "Terrascope braille renderer + Natural Earth", "area": area.name,
               "center": [round(view.center_lon, 3), round(view.center_lat, 3)], "zoom": round(zoom, 2),
               "width": width, "height": height, "text": "\n".join(plain_rows), "legend": notes}
        if ansi:
            out["ansi"] = "\n".join(ansi_rows)
        return out


def view_bbox(view: ts.View) -> tuple[float, float, float, float]:
    lo1 = max(-180.0, view.center_lon - view.lon_span / 2)
    lo2 = min(180.0, view.center_lon + view.lon_span / 2)
    la1 = max(-90.0, view.center_lat - view.lat_span / 2)
    la2 = min(90.0, view.center_lat + view.lat_span / 2)
    return lo1, la1, lo2, la2


def _iso(epoch) -> str | None:
    try:
        v = float(epoch)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return datetime.fromtimestamp(v, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_SERVICES: dict[str, GeoService] = {}
_SERVICES_LOCK = threading.Lock()


def service_for(data_dir: Path | str) -> GeoService:
    """One GeoService (and one cache/rate limiter) per data dir in the process."""
    key = str(Path(data_dir))
    with _SERVICES_LOCK:
        if key not in _SERVICES:
            _SERVICES[key] = GeoService(Path(data_dir) / "geo")
        return _SERVICES[key]
