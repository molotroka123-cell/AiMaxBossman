"""geo.* agent tools — live map data adopted from Terrascope (MIT) through `bcc.geo` (owner 25.10).

  geo.aircraft     aircraft in the air over a country/region/bbox right now (OpenSky, keyless)
  geo.weather      current weather in a city or at lat/lon (Open-Meteo, keyless)
  geo.earthquakes  earthquakes >= M over the last N hours, optionally in a region (USGS feeds)
  geo.daynight     day/twilight/night, sun elevation, sunrise/sunset for a place (local solar maths)
  geo.map_render   braille map of a region as plain text (optionally aircraft/earthquake markers)

Permission class: network READ of public data (`browser.read`, not dangerous, default AUTO) like web.search;
no keys, no PC changes. Cloud chat-only agents get no tools at all (bcc.tools.allowed_tools_for). A failed source
returns an error result «данные недоступны: <источник> (<причина>)» — the model must say so, never invent values.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..geo.service import GeoInputError, GeoUnavailable, service_for
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature

MAX_CONTENT = 6000
NO_INVENT = "Не выдумывай значения: если данных нет — так и скажи владельцу."


def _svc(ctx: ToolContext):
    return service_for(Path(ctx.svc.settings.data_dir))


def _f(v, nd=1, unit=""):
    if v is None:
        return "н/д"
    return f"{v:.{nd}f}{unit}" if isinstance(v, float) else f"{v}{unit}"


def summarize(kind: str, r: dict) -> str:
    """Compact Russian text the model reads; every number comes from `r` (the tool data)."""
    if kind == "aircraft":
        where = "внутри границ" if r.get("within_borders") else "в прямоугольнике"
        lines = [f"Самолёты в воздухе — {r['area']} ({where}): {r['count']} (в bbox {r['bbox_count']}); "
                 f"снимок OpenSky {r.get('data_time_utc') or 'н/д'} UTC."]
        for it in r["items"]:
            lines.append(f"- {it['callsign'] or '(без позывного)'} icao24={it['icao24']} {it['origin_country']} "
                         f"высота {_f(it['altitude_m'], 0, ' м')} скорость {_f(it['speed_kmh'], 0, ' км/ч')} "
                         f"[{it['category']}] ({it['lat']}, {it['lon']})")
        if r.get("truncated"):
            lines.append(f"(показаны первые {len(r['items'])} из {r['count']})")
        return "\n".join(lines)
    if kind == "weather":
        return (f"Погода сейчас — {r['place']}{', ' + r['country'] if r.get('country') else ''} "
                f"(местное время {r.get('local_time')}, {r.get('timezone')}): {_f(r['temperature_c'])} °C, "
                f"ощущается {_f(r.get('apparent_c'))} °C, {r['conditions']} (код {r['weather_code']}), "
                f"ветер {_f(r.get('wind_kmh'))} км/ч, направление {_f(r.get('wind_dir_deg'), 0)}°, "
                f"влажность {_f(r.get('humidity_pct'), 0)} %, осадки {_f(r.get('precipitation_mm'))} мм. "
                f"Источник: {r['source']}.")
    if kind in ("earthquakes", "quakes"):
        lines = [f"Землетрясения M≥{r['min_magnitude']} за {r['hours']:g} ч ({r['area']}): {r['count']}. "
                 f"Лента USGS {r['feed']}, обновлена {r.get('feed_generated_utc') or 'н/д'} UTC."]
        for it in r["items"]:
            lines.append(f"- M{it['magnitude']:.1f} {it['place']} — {it['time_utc']} UTC, глубина "
                         f"{_f(it['depth_km'], 1, ' км')}{', ЦУНАМИ-флаг' if it['tsunami'] else ''}")
        if r.get("truncated"):
            lines.append(f"(показаны {len(r['items'])} сильнейших из {r['count']})")
        return "\n".join(lines)
    if kind == "daynight":
        ph = {"Day": "день", "Civil twilight": "гражданские сумерки", "Nautical twilight": "навигационные сумерки",
              "Astronomical twilight": "астрономические сумерки", "Night": "ночь"}.get(r["phase"], r["phase"])
        sun = (f"восход {r['sunrise_utc']} UTC, закат {r['sunset_utc']} UTC" if "sunrise_utc" in r
               else str(r.get("polar")))
        return (f"{r['place']} на {r['at_utc']}: {ph} (высота Солнца {r['sun_elevation_deg']}°), {sun}; "
                f"Луна: {r['moon_phase']}, освещённость {r['moon_illumination_pct']} %.")
    if kind in ("map", "map_render"):
        return (f"Карта {r['area']} {r['width']}x{r['height']} (центр {r['center']}, zoom {r['zoom']}):\n"
                + r["text"] + ("\n" + "; ".join(r["legend"]) if r["legend"] else ""))
    return str(r)


async def _run(kind: str, fn, args: dict, ctx: ToolContext) -> ToolResult:
    name = f"geo.{kind}"
    try:
        res = await asyncio.to_thread(fn, _svc(ctx), args)
    except GeoUnavailable as exc:
        return ToolResult(content=f"{name}: {exc}. {NO_INVENT}", one_line=f"{name}: unavailable ({exc.source})",
                          error=True, data={"unavailable": True, "source": exc.source})
    except (GeoInputError, TypeError, ValueError) as exc:
        return ToolResult(content=f"{name}: ошибка аргументов: {exc}", one_line=f"{name}: bad args", error=True)
    text = summarize(kind, res)
    truncated = len(text) > MAX_CONTENT
    if truncated:
        text = text[:MAX_CONTENT]
    data = {k: v for k, v in res.items() if k not in ("text", "ansi")}
    return ToolResult(content=text, one_line=f"{name}: ok", external=True, truncated=truncated,
                      more="уменьшите width/height или limit" if truncated else "", data=data)


def _list(v):
    if v is None or v == "":
        return None
    if isinstance(v, str):
        return [x.strip() for x in v.replace(";", ",").split(",") if x.strip()]
    return list(v)


async def _t_aircraft(args, ctx):
    return await _run("aircraft", lambda s, a: s.aircraft(a.get("region"), _list(a.get("bbox")),
                                                          a.get("limit") or 10), args, ctx)


async def _t_weather(args, ctx):
    return await _run("weather", lambda s, a: s.weather(a.get("city"), a.get("lat"), a.get("lon")), args, ctx)


async def _t_quakes(args, ctx):
    return await _run("earthquakes", lambda s, a: s.earthquakes(
        a.get("min_magnitude", 4.5) if a.get("min_magnitude") is not None else 4.5, a.get("hours") or 24,
        a.get("region"), _list(a.get("bbox")), a.get("limit") or 10), args, ctx)


async def _t_daynight(args, ctx):
    return await _run("daynight", lambda s, a: s.daynight(a.get("city"), a.get("lat"), a.get("lon")), args, ctx)


async def _t_map(args, ctx):
    return await _run("map_render", lambda s, a: s.map_render(
        a.get("region"), _list(a.get("bbox")), a.get("width") or 80, a.get("height") or 24,
        _list(a.get("layers")), ansi=False), args, ctx)


_BBOX = {"type": "array", "items": {"type": "number"}, "description": "[min_lon, min_lat, max_lon, max_lat]"}
_REGION = {"type": "string", "description": "страна (Czechia / Чехия / CZ) или регион: europe, world, central europe"}
_CITY = {"type": "string", "description": "город латиницей или по-русски (Prague / Прага), либо lat+lon"}
_COMMON = dict(source="builtin", category="read", permission="browser.read", default_effect="auto",
               external_output=True)

SPECS = [
    ToolSpec(name="geo.aircraft", handler=_t_aircraft, timeout_seconds=60.0,
             description=("Самолёты в воздухе СЕЙЧАС над страной/регионом (живые данные OpenSky, без ключа): "
                          "количество внутри границ страны + список рейсов (позывной, страна, высота, скорость). "
                          "Live aircraft over a country/region/bbox."),
             input_schema={"region": _REGION, "bbox": _BBOX,
                           "limit": {"type": "integer", "description": "сколько рейсов перечислить, 1..50"}},
             **_COMMON),
    ToolSpec(name="geo.weather", handler=_t_weather, timeout_seconds=45.0,
             description=("Текущая погода в городе или точке (Open-Meteo, без ключа): температура, ощущается, "
                          "условия, ветер, влажность, осадки. Current weather for a city or lat/lon."),
             input_schema={"city": _CITY, "lat": {"type": "number"}, "lon": {"type": "number"}}, **_COMMON),
    ToolSpec(name="geo.earthquakes", handler=_t_quakes, timeout_seconds=45.0,
             description=("Землетрясения за последние N часов с магнитудой не ниже min_magnitude (ленты USGS): "
                          "количество, сильнейшее, список (магнитуда, место, время UTC, глубина). "
                          "Recent earthquakes from USGS."),
             input_schema={"min_magnitude": {"type": "number", "description": "по умолчанию 4.5"},
                           "hours": {"type": "number", "description": "окно, ч (по умолчанию 24, до 720)"},
                           "region": _REGION, "bbox": _BBOX, "limit": {"type": "integer"}}, **_COMMON),
    ToolSpec(name="geo.daynight", handler=_t_daynight, timeout_seconds=30.0,
             description=("День или ночь сейчас в городе/точке: фаза (день/сумерки/ночь), высота Солнца, восход и "
                          "закат (UTC), фаза Луны. Local solar maths, no network except city lookup."),
             input_schema={"city": _CITY, "lat": {"type": "number"}, "lon": {"type": "number"}}, **_COMMON),
    ToolSpec(name="geo.map_render", handler=_t_map, timeout_seconds=90.0,
             description=("Нарисовать карту региона шрифтом Брайля (текст, границы стран Natural Earth); "
                          "layers: aircraft, earthquakes — отметки поверх. Braille text map of a region."),
             input_schema={"region": _REGION, "bbox": _BBOX,
                           "width": {"type": "integer", "description": "20..160 символов"},
                           "height": {"type": "integer", "description": "8..60 строк"},
                           "layers": {"type": "array", "items": {"type": "string", "enum": ["aircraft",
                                                                                             "earthquakes"]}}},
             **_COMMON),
]

TOOL_NAMES = tuple(s.name for s in SPECS)


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_geo", router=None, setup=_setup)
