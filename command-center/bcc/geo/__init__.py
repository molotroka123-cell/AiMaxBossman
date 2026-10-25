"""Geo data adapter adopted from Terrascope (MIT, https://github.com/a-shygun/Terrascope @ f37163d8).

`service.GeoService` — keyless live aircraft (OpenSky), weather (Open-Meteo), earthquakes (USGS), day/night
(local solar maths) and a braille map (Natural Earth). Agent tools: `bcc.features.tools_geo` (geo.*).
CLI (prints once, non-interactive — usable from the mobile Web Terminal): ``python -m bcc.geo map --region europe``.
"""
from .service import GeoInputError, GeoService, GeoUnavailable, service_for  # noqa: F401
