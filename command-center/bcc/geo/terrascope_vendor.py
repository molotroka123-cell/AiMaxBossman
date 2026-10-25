# Vendored minimal pieces of Terrascope (https://github.com/a-shygun/Terrascope)
# pinned at commit f37163d8ecbc1a8550c293a773a5b41ec8919ab7 (2026-10-22).
#
# MIT License
#
# Copyright (c) 2026 Amir Shygun
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""Pure (no curses, no network, no config-file side effects) parsers and renderers copied from Terrascope.

Why vendored instead of imported: upstream imports `curses` at package level (``terrascope.core.ui``) — absent on
Windows — and writes ``~/.config/terrascope/default.yaml`` on first import. Only side-effect-free functions are
copied, verbatim in logic, with their upstream location noted; settings that upstream reads from
``assets/default.yaml`` are inlined as constants with the same values. ``tests/test_geo_terrascope.py`` checks
parity against the pinned clone when it is present (``apps-local/terrascope``).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

UPSTREAM_URL = "https://github.com/a-shygun/Terrascope"
UPSTREAM_SHA = "f37163d8ecbc1a8550c293a773a5b41ec8919ab7"
LICENSE = "MIT"

# ---------------------------------------------------------------- flights (layers/flights/api.py, formatting.py)

EMERGENCY_SQUAWKS = ("7500", "7600", "7700")
CATEGORY_BY_CODE = {
    2: "light", 12: "light", 3: "regional", 4: "airliner", 5: "heavy", 6: "heavy", 7: "fast",
    8: "helicopter", 9: "other", 10: "other", 11: "other", 14: "other",
}
LIGHT_MAX_SPEED = 95.0        # default.yaml layers.flights.light_max_speed_ms
REGIONAL_MAX_SPEED = 150.0    # default.yaml layers.flights.regional_max_speed_ms
HIGH_ALTITUDE_METERS = 7000.0  # default.yaml layers.flights.high_altitude_m


def finite_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def normalize_states(states: list) -> list[dict]:
    """OpenSky `states` rows -> airborne flight dicts (upstream layers/flights/api.py)."""
    flights = []
    for state in states:
        if not isinstance(state, list) or len(state) < 17:
            continue
        if state[8] is not False:
            continue
        try:
            longitude = float(state[5])
            latitude = float(state[6])
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(longitude) and math.isfinite(latitude)):
            continue
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            continue
        flights.append({
            "icao24": str(state[0] or "").strip().upper(),
            "callsign": str(state[1] or "").strip(),
            "origin_country": str(state[2] or "Unknown"),
            "time_position": state[3],
            "last_contact": state[4],
            "longitude": longitude,
            "latitude": latitude,
            "baro_altitude": state[7],
            "on_ground": state[8],
            "velocity": state[9],
            "true_track": state[10],
            "vertical_rate": state[11],
            "sensors": state[12],
            "geo_altitude": state[13],
            "squawk": state[14],
            "spi": state[15],
            "position_source": state[16],
            "category": state[17] if len(state) > 17 else None,
        })
    return flights


def categorize(flight: dict) -> str:
    """Upstream layers/flights/formatting.py: emergency squawk, category code, then a speed/altitude guess."""
    if str(flight.get("squawk") or "") in EMERGENCY_SQUAWKS:
        return "emergency"
    category = CATEGORY_BY_CODE.get(flight.get("category"))
    if category:
        return category
    speed = finite_float(flight.get("velocity"))
    if speed is None:
        return "other"
    altitude = finite_float(flight.get("baro_altitude"))
    if speed < LIGHT_MAX_SPEED:
        return "light"
    if speed < REGIONAL_MAX_SPEED and (altitude is None or altitude < HIGH_ALTITUDE_METERS):
        return "regional"
    return "airliner"


# ---------------------------------------------------------------- earthquakes (layers/weather/earthquakes.py)

def normalize_features(features: list) -> list[dict]:
    """USGS GeoJSON features -> quake dicts (upstream layers/weather/earthquakes.py)."""
    quakes = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            continue
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            continue
        try:
            longitude = float(coordinates[0])
            latitude = float(coordinates[1])
            depth_km = float(coordinates[2]) if len(coordinates) > 2 else None
        except (TypeError, ValueError):
            continue
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            continue
        magnitude = properties.get("mag")
        try:
            magnitude = float(magnitude) if magnitude is not None else None
        except (TypeError, ValueError):
            magnitude = None
        quakes.append({
            "id": feature.get("id") or "",
            "place": properties.get("place") or "Unknown location",
            "magnitude": magnitude,
            "depth_km": depth_km,
            "time": properties.get("time"),
            "updated": properties.get("updated"),
            "tsunami": bool(properties.get("tsunami")),
            "alert": properties.get("alert"),
            "felt": properties.get("felt"),
            "status": properties.get("status"),
            "event_type": properties.get("type") or "earthquake",
            "url": properties.get("url") or "",
            "longitude": longitude,
            "latitude": latitude,
        })
    return quakes


# ---------------------------------------------------------------- weather (layers/weather/config.py, city_weather.py)

WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Slight rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Slight snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Rain showers", 81: "Rain showers",
    82: "Violent rain showers", 85: "Snow showers", 86: "Snow showers", 95: "Thunderstorm",
    96: "Thunderstorm, hail", 99: "Thunderstorm, heavy hail",
}
UNKNOWN_WEATHER = "Unknown"


def describe_weather_code(code) -> str:
    try:
        return WEATHER_CODES.get(int(code), UNKNOWN_WEATHER)
    except (TypeError, ValueError):
        return UNKNOWN_WEATHER


def parse_current_weather(payload) -> dict:
    """Open-Meteo forecast response -> normalized current block (upstream fetch_weather_snapshot, parse part).

    Raises ValueError when the response has no current block (upstream raises WeatherFetchError)."""
    if not isinstance(payload, dict):
        raise ValueError("Weather response is not an object")
    current = payload.get("current")
    if not isinstance(current, dict):
        current = payload.get("current_weather")
    if not isinstance(current, dict):
        raise ValueError("Weather response missing current_weather")
    return {
        "temperature": current.get("temperature_2m", current.get("temperature")),
        "windspeed": current.get("wind_speed_10m", current.get("windspeed")),
        "winddirection": current.get("wind_direction_10m", current.get("winddirection")),
        "weathercode": current.get("weather_code", current.get("weathercode")),
        "time": current.get("time"),
    }


# ---------------------------------------------------------------- day / night (layers/daynight/astronomy.py)

SUN_PHASES = ((0.0, "Day"), (-6.0, "Civil twilight"), (-12.0, "Nautical twilight"), (-18.0, "Astronomical twilight"))
SUN_NIGHT = "Night"
SUNRISE_ELEVATION = -0.833
SYNODIC_MONTH_DAYS = 29.530588853
MOON_EPOCH = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
MOON_PHASE_NAMES = ("New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous",
                    "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent")


def julian_day(now: datetime) -> float:
    year, month = now.year, now.month
    day = now.day + (now.hour + now.minute / 60 + now.second / 3600) / 24
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return int(365.25 * (year + 4716)) + int(30.6001 * (month + 1)) + day + b - 1524.5


def solar_position(now: datetime) -> tuple[float, float]:
    """(declination in radians, equation of time in minutes), ~0.01 deg (Meeus)."""
    T = (julian_day(now) - 2451545.0) / 36525.0
    L0 = math.radians((280.46646 + T * (36000.76983 + T * 0.0003032)) % 360)
    M = math.radians(357.52911 + T * (35999.05029 - 0.0001537 * T))
    e = 0.016708634 - T * (0.000042037 + 0.0000001267 * T)
    center = (math.sin(M) * (1.914602 - T * (0.004817 + 0.000014 * T))
              + math.sin(2 * M) * (0.019993 - 0.000101 * T) + math.sin(3 * M) * 0.000289)
    omega = math.radians(125.04 - 1934.136 * T)
    apparent = math.radians(math.degrees(L0) + center - 0.00569 - 0.00478 * math.sin(omega))
    obliquity = math.radians(23 + (26 + (21.448 - T * (46.815 + T * (0.00059 - T * 0.001813))) / 60) / 60
                             + 0.00256 * math.cos(omega))
    declination = math.asin(math.sin(obliquity) * math.sin(apparent))
    y = math.tan(obliquity / 2) ** 2
    eq_time = 4 * math.degrees(y * math.sin(2 * L0) - 2 * e * math.sin(M) + 4 * e * y * math.sin(M) * math.cos(2 * L0)
                               - 0.5 * y * y * math.sin(4 * L0) - 1.25 * e * e * math.sin(2 * M))
    return declination, eq_time


def sun_elevation(lat: float, lon: float, now: datetime, declination: float, eq_time: float) -> float:
    minutes = now.hour * 60 + now.minute + now.second / 60
    solar_minutes = (minutes + eq_time + 4 * lon) % 1440
    hour_angle = math.radians(solar_minutes / 4 - 180)
    phi = math.radians(lat)
    cosine = math.sin(phi) * math.sin(declination) + math.cos(phi) * math.cos(declination) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, cosine))))


def sunrise_sunset(lat: float, lon: float, declination: float, eq_time: float):
    """(sunrise, sunset) as minutes after 00:00 UTC, or "polar day" / "polar night"."""
    phi = math.radians(lat)
    cos_h = ((math.sin(math.radians(SUNRISE_ELEVATION)) - math.sin(phi) * math.sin(declination))
             / (math.cos(phi) * math.cos(declination) or 1e-9))
    if cos_h <= -1:
        return "polar day"
    if cos_h >= 1:
        return "polar night"
    half_day = math.degrees(math.acos(cos_h))
    noon = 720 - 4 * lon - eq_time
    return (noon - 4 * half_day) % 1440, (noon + 4 * half_day) % 1440


def sun_phase_name(elevation: float) -> str:
    for threshold, name in SUN_PHASES:
        if elevation >= threshold:
            return name
    return SUN_NIGHT


def moon_info(now: datetime) -> dict:
    age = ((now - MOON_EPOCH).total_seconds() / 86400) % SYNODIC_MONTH_DAYS
    fraction = age / SYNODIC_MONTH_DAYS
    return {"name": MOON_PHASE_NAMES[int(fraction * 8 + 0.5) % 8],
            "illumination": (1 - math.cos(2 * math.pi * fraction)) / 2, "age": age}


def hhmm(minutes: float) -> str:
    minutes = round(minutes) % 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# ---------------------------------------------------------------- map data (core/mapdata.py)

Ring = tuple  # (points, min_lon, max_lon, min_lat, max_lat)


def unwrap_ring(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not points:
        return points
    result = [points[0]]
    previous_lon = points[0][0]
    offset = 0.0
    for lon, lat in points[1:]:
        delta = lon - previous_lon
        if delta > 180:
            offset -= 360.0
        elif delta < -180:
            offset += 360.0
        result.append((lon + offset, lat))
        previous_lon = lon
    return result


def make_ring(points) -> Ring | None:
    if not points:
        return None
    converted = []
    for point in points:
        if len(point) < 2:
            continue
        lon = float(point[0])
        lat = float(point[1])
        if not math.isfinite(lon) or not math.isfinite(lat):
            continue
        converted.append((lon, lat))
    if len(converted) < 2:
        return None
    converted = unwrap_ring(converted)
    lons = [p[0] for p in converted]
    lats = [p[1] for p in converted]
    return (converted, min(lons), max(lons), min(lats), max(lats))


def is_antarctica(properties: object) -> bool:
    if not isinstance(properties, dict):
        return False
    props = {str(key).upper(): value for key, value in properties.items()}
    if props.get("CONTINENT") == "Antarctica":
        return True
    if props.get("ADM0_A3") == "ATA" or props.get("ISO_A3") == "ATA":
        return True
    return "Antarctica" in (props.get("ADMIN"), props.get("NAME"))


def load_geojson_features(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        raise ValueError(f"{Path(path).name}: unexpected GeoJSON format")
    return data["features"]


def feature_rings(feature: dict) -> list[Ring]:
    """Outer rings of one Polygon/MultiPolygon feature (upstream load_polygons, per feature)."""
    rings: list[Ring] = []
    geometry = feature.get("geometry") if isinstance(feature, dict) else None
    if not isinstance(geometry, dict):
        return rings
    kind, coordinates = geometry.get("type"), geometry.get("coordinates")
    if kind == "Polygon" and coordinates:
        ring = make_ring(coordinates[0])
        if ring:
            rings.append(ring)
    elif kind == "MultiPolygon" and coordinates:
        for polygon in coordinates:
            if polygon:
                ring = make_ring(polygon[0])
                if ring:
                    rings.append(ring)
    return rings


def load_polygons(features: list[dict]) -> list[Ring]:
    rings: list[Ring] = []
    for feature in features:
        if not isinstance(feature, dict) or is_antarctica(feature.get("properties")):
            continue
        rings.extend(feature_rings(feature))
    return rings


def parse_places(features: list[dict]) -> list[tuple]:
    """(lon, lat, name, is_capital, population, country) per populated place (upstream load_places_file)."""
    places = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties, geometry = feature.get("properties"), feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            continue
        coordinates = geometry.get("coordinates")
        if geometry.get("type") != "Point" or not isinstance(coordinates, list) or len(coordinates) < 2:
            continue
        try:
            lon, lat = float(coordinates[0]), float(coordinates[1])
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(lon) and math.isfinite(lat)):
            continue
        props = {str(key).lower(): value for key, value in properties.items()}
        name = props.get("name") or props.get("nameascii")
        if not name:
            continue
        is_capital = str(props.get("featurecla", "")).lower().startswith("admin-0 capital")
        if str(props.get("adm0cap", "")).strip() in ("1", "1.0"):
            is_capital = True
        population = 0
        for key in ("pop_max", "pop_min"):
            try:
                population = max(population, int(float(props.get(key) or 0)))
            except (TypeError, ValueError):
                pass
        country = str(props.get("adm0name") or props.get("sov0name") or "")
        places.append((lon, lat, str(name), is_capital, population, country))
    return places


# ---------------------------------------------------------------- view + braille (core/view.py, core/world.py)

CELL_ASPECT = 2.0   # default.yaml map.cell_aspect
LAT_MIN = -58.0     # default.yaml map.lat_min
LAT_MAX = 84.0      # default.yaml map.lat_max
LAT_RANGE = LAT_MAX - LAT_MIN
LAT_MID = (LAT_MAX + LAT_MIN) / 2
ZOOM_MIN, ZOOM_MAX = 1.0, 64.0
WRAP_SHIFTS = (-360.0, 0.0, 360.0)
BRAILLE_BIT_ORDER = np.array([[0, 3], [1, 4], [2, 5], [6, 7]], dtype=np.uint8)
BRAILLE_BIT_WEIGHTS = (1 << BRAILLE_BIT_ORDER).astype(np.uint8)
BLANK_BRAILLE = "⠀"


def calculate_viewport(zoom: float, aspect: float = 2.0) -> tuple[float, float]:
    lat_span = LAT_RANGE / zoom
    return lat_span * aspect, lat_span


def viewport_aspect(width: int, height: int, cell_aspect: float = CELL_ASPECT) -> float:
    if height <= 0:
        return 2.0
    return width / (height * cell_aspect)


def normalize_longitude(longitude: float) -> float:
    return ((longitude + 180.0) % 360.0) - 180.0


def clamp_center(center_lon: float, center_lat: float, lon_span: float, lat_span: float) -> tuple[float, float]:
    center_lon = normalize_longitude(center_lon)
    if lat_span >= LAT_RANGE - 1e-9:
        center_lat = LAT_MID
    else:
        half_lat = lat_span / 2
        center_lat = max(LAT_MIN + half_lat, min(LAT_MAX - half_lat, center_lat))
    return center_lon, center_lat


def visible(polygon: Ring, center_lon: float, center_lat: float, lon_span: float, lat_span: float) -> list[float]:
    _, min_lon, max_lon, min_lat, max_lat = polygon
    if max_lat < center_lat - lat_span / 2 or min_lat > center_lat + lat_span / 2:
        return []
    view_min_lon, view_max_lon = center_lon - lon_span / 2, center_lon + lon_span / 2
    return [s for s in WRAP_SHIFTS if not (max_lon + s < view_min_lon or min_lon + s > view_max_lon)]


def project_location(longitude, latitude, center_lon, center_lat, lon_span, lat_span, width, height):
    relative_lon = (longitude - (center_lon - lon_span / 2)) % 360.0
    if relative_lon > lon_span:
        return None
    x = relative_lon / lon_span * width
    y = (center_lat + lat_span / 2 - latitude) / lat_span * height
    if x < 0 or x >= width or y < 0 or y >= height:
        return None
    return x, y


class View:
    """Viewport -> pixels / braille cells (upstream core/view.py View, drawing subset)."""

    def __init__(self, center_lon: float, center_lat: float, zoom: float, width: int, height: int) -> None:
        self.width, self.height, self.zoom = width, height, zoom
        self.aspect = viewport_aspect(width, height, CELL_ASPECT)
        self.lon_span, self.lat_span = calculate_viewport(zoom, self.aspect)
        self.center_lon, self.center_lat = clamp_center(center_lon, center_lat, self.lon_span, self.lat_span)
        self.pixel_width, self.pixel_height = width * 2, height * 4
        self.scale_x = self.pixel_width / self.lon_span
        self.scale_y = self.pixel_height / self.lat_span
        self.origin_lon = self.center_lon - self.lon_span / 2
        self.origin_lat = self.center_lat + self.lat_span / 2

    def new_image(self) -> Image.Image:
        return Image.new("L", (self.pixel_width, self.pixel_height), 0)

    def to_lines(self, image: Image.Image) -> list[str]:
        array = np.asarray(image.convert("1"), dtype=np.uint8).reshape(self.height, 4, self.width, 2)
        values = (array * BRAILLE_BIT_WEIGHTS[np.newaxis, :, np.newaxis, :]).sum(axis=(1, 3))
        codes = np.ascontiguousarray(values + 0x2800, dtype="<u2")
        return [row.tobytes().decode("utf-16-le") for row in codes]

    def to_cell(self, longitude: float, latitude: float) -> tuple[int, int] | None:
        pos = project_location(longitude, latitude, self.center_lon, self.center_lat, self.lon_span,
                               self.lat_span, self.width, self.height)
        if pos is None:
            return None
        x, y = int(pos[0]), int(pos[1])
        if 0 <= x < self.width and 0 <= y < self.height:
            return x, y
        return None


def draw_rings(rings: list[Ring], view: View, outline: ImageDraw.ImageDraw) -> None:
    """Project each ring (and its antimeridian copies) and draw its outline (upstream world._draw_rings)."""
    for ring in rings:
        shifts = visible(ring, view.center_lon, view.center_lat, view.lon_span, view.lat_span)
        if not shifts:
            continue
        points = np.asarray(ring[0], dtype=np.float64)
        if len(points) < 2:
            continue
        xy = np.empty((len(points), 2), dtype=np.int64)
        xy[:, 1] = np.rint((view.origin_lat - points[:, 1]) * view.scale_y)
        for shift in shifts:
            xy[:, 0] = np.rint((points[:, 0] + shift - view.origin_lon) * view.scale_x)
            outline.line(xy.ravel().tolist(), fill=255, width=1)
