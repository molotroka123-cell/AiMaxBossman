"""One-shot geo CLI (non-interactive, prints once; the mobile Web Terminal can run it).

    python -m bcc.geo map --region europe [--width 100 --height 30 --layers aircraft,earthquakes --ansi]
    python -m bcc.geo aircraft --region Czechia
    python -m bcc.geo weather --city Prague
    python -m bcc.geo quakes --min 4.5 --hours 24
    python -m bcc.geo daynight --city Prague
Add --json for the raw result. Exit 2 when the data source is unavailable (nothing is invented).
"""
from __future__ import annotations

import argparse
import json
import sys

from .service import GeoInputError, GeoUnavailable, service_for


def _data_dir():
    from ..config import _data_dir as dd
    return dd()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bcc.geo", description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["map", "aircraft", "weather", "quakes", "daynight"])
    ap.add_argument("--region")
    ap.add_argument("--bbox", help="min_lon,min_lat,max_lon,max_lat")
    ap.add_argument("--city")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--min", type=float, default=4.5, dest="min_magnitude")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--width", type=int, default=80)
    ap.add_argument("--height", type=int, default=24)
    ap.add_argument("--layers", default="")
    ap.add_argument("--ansi", action="store_true", help="colour output for a real terminal")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    bbox = [float(v) for v in a.bbox.split(",")] if a.bbox else None
    svc = service_for(_data_dir())
    try:
        if a.command == "map":
            res = svc.map_render(a.region, bbox, a.width, a.height,
                                 [x for x in a.layers.split(",") if x], ansi=a.ansi)
        elif a.command == "aircraft":
            res = svc.aircraft(a.region, bbox, a.limit)
        elif a.command == "weather":
            res = svc.weather(a.city, a.lat, a.lon)
        elif a.command == "quakes":
            res = svc.earthquakes(a.min_magnitude, a.hours, a.region, bbox, a.limit)
        else:
            res = svc.daynight(a.city, a.lat, a.lon)
    except GeoUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except GeoInputError as exc:
        print(f"ошибка ввода: {exc}", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    elif a.command == "map":
        print(res["ansi"] if a.ansi else res["text"])
        for note in res["legend"]:
            print(note)
    else:
        from ..features.tools_geo import summarize
        print(summarize(a.command, res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
