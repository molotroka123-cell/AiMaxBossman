"""Verified structural-level extraction/tracking for the k1m6a chart.

This module is intentionally separate from Price/CVD/OI extraction. A level is
usable only when its label and numeric value are read unanimously from the same
fresh frame. Temporal events (reclaim/loss/retest/rejection) are derived from
verified observations; the vision model never declares them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from PIL import Image

from . import extract, schema

LEVEL_NAMES = ("dPOC", "dVAH", "dVAL", "dOpen")
_LEVEL = re.compile(
    r"^\s*(dPOC|dVAH|dVAL|dOpen)\s*[:|\-]?\s*(\d{1,3}(?:[, ]\d{3})+|\d{3,7}(?:\.\d{1,2})?)\s*$",
    re.I,
)

# Whole price pane, including left-side study labels and right axis. We prefer
# false UNKNOWN to a guessed level. Calibration may narrow this on owner PC.
DEFAULT_LEVEL_BOX = (300, 90, 1306, 600)


@dataclass(frozen=True)
class LevelReading:
    name: str
    value: float
    status: str
    raw: tuple[str, ...]
    crop_sha256: str


def parse_level(text: str) -> tuple[str, float] | None:
    t = " ".join((text or "").replace("·", " ").split())
    m = _LEVEL.match(t)
    if not m:
        return None
    canonical = {x.lower(): x for x in LEVEL_NAMES}[m.group(1).lower()]
    value = schema.parse_price(m.group(2))
    if value is None:
        return None
    return canonical, value


def read_levels(reader: extract.Reader, frame: Image.Image,
                box: tuple[int, int, int, int] = DEFAULT_LEVEL_BOX) -> dict[str, Any]:
    """Read explicit named levels. Never infer a numeric level from geometry alone."""
    crop = frame.crop(box)
    raws = [reader.read(v) for v in extract._variants(crop)]
    per_read: list[dict[str, float]] = []
    for raw in raws:
        found: dict[str, float] = {}
        # The generic reader may return several lines from this larger crop.
        for line in re.split(r"[\n;]+", raw):
            parsed = parse_level(line)
            if parsed:
                found[parsed[0]] = parsed[1]
        per_read.append(found)
    out: dict[str, Any] = {"status": schema.UNREADABLE, "levels": {}, "raw": raws,
                           "bbox": list(box), "crop_sha256": extract.sha256_png(crop)}
    agreed = {}
    for name in LEVEL_NAMES:
        values = [r.get(name) for r in per_read]
        if all(v is not None for v in values) and len(set(values)) == 1:
            agreed[name] = values[0]
    if agreed:
        out["status"] = schema.VERIFIED
        out["levels"] = agreed
    elif any(per_read):
        out["status"] = schema.LOW_CONFIDENCE
    return out


def relation(price: float | None, level: float | None, *, tolerance_bps: float = 5.0) -> str:
    if price is None or level is None:
        return "UNKNOWN"
    eps = abs(level) * tolerance_bps / 10_000
    if abs(price - level) <= eps:
        return "AT"
    return "ABOVE" if price > level else "BELOW"


def track(previous: dict[str, Any] | None, current: dict[str, Any],
          *, tolerance_bps: float = 5.0) -> dict[str, str]:
    """Derive level events from two verified states; no single-frame 'reclaim'."""
    events: dict[str, str] = {}
    if not previous:
        return events
    p0, p1 = previous.get("price"), current.get("price")
    for name, level in current.get("levels", {}).items():
        old_level = previous.get("levels", {}).get(name)
        if old_level is None or abs(old_level - level) > max(1.0, abs(level) * 0.001):
            continue
        before = relation(p0, old_level, tolerance_bps=tolerance_bps)
        now = relation(p1, level, tolerance_bps=tolerance_bps)
        if before == "BELOW" and now == "ABOVE":
            events[name] = "RECLAIM"
        elif before == "ABOVE" and now == "BELOW":
            events[name] = "LOSS"
        elif before != "AT" and now == "AT":
            events[name] = "RETEST"
    return events
