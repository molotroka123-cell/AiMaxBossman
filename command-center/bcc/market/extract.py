"""Frame -> OI/CVD/price/symbol readings, with evidence. LOCAL vision only.

The streamer's chart (TradingView layout on k1m6a) draws the last value of each
pane as a badge on the right price axis; the badge MOVES vertically with the
value, so crops are located per frame, not fixed:

* OI/CVD badges are saturated (red/green) rectangles whose text carries the
  metric label ("CVD 74.44B", "Open Interest 19.57B") — the label, not the
  position, decides which metric a number is;
* the current-price badge is a neutral dark two-line label (price + bar
  countdown) inside the price pane;
* the symbol line ("BTC1! · 30 · CME") sits at the chart's top-left.

Every value is read THREE times from decorrelated renderings (colour and
grayscale). Unanimous agreement of the parsed value AND unit -> verified; disagreement -> low confidence;
nothing parseable -> unreadable. A number is never guessed or carried forward.
"""
from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import json
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from PIL import Image

from . import schema

# Region config for 1920x1080 frames of this layout (config, not truth: every
# read is validated by its label/regex, a moved layout yields UNREADABLE).
DEFAULT_LAYOUT: dict[str, Any] = {
    "frame_size": [1920, 1080],
    "axis_column": [1150, 1306],          # x range of the right price axis badges
    "axis_rows": [600, 784],             # CVD + OI panes only (labels still decide the metric)
    "price_pane_rows": [90, 600],
    "price_column": [1254, 1300],
    "symbol_box": [325, 93, 470, 111],
    "clock_box": [1548, 22, 1690, 72],
}

VISION_MODEL = os.environ.get("BOSSMAN_MARKET_VISION_MODEL", "bossman-fast-qwen36-vision:latest")
VISION_URL = os.environ.get("BOSSMAN_MARKET_VISION_URL", "http://127.0.0.1:11434")
PROMPT = ("Transcribe the text in this image exactly. Numbers on this chart end with a unit suffix letter "
          "(K, M, B or T) when abbreviated. Output only the text. If it is not legible, output UNREADABLE.")
EXTRACTOR_VERSION = "triple-read-unanimous/v4"
ACCEPTED_EXTRACTORS = ("triple-read-unanimous/v3", EXTRACTOR_VERSION)


def calibrated_extractor(identity: str | None) -> bool:
    return any(version in (identity or "") for version in ACCEPTED_EXTRACTORS)


class Reader(Protocol):
    identity: str

    def read(self, image: Image.Image) -> str: ...


def _is_loopback(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class CachedReader:
    """Same pixels -> same transcription. Keyed by the SHA-256 of the exact image
    sent, so a cache hit is a read of THIS frame's pixels, never a carried value."""

    def __init__(self, inner: Reader, limit: int = 4096):
        self.inner, self.limit = inner, limit
        self.identity = inner.identity
        self.cache: dict[str, str] = {}
        self.hits = 0

    def read(self, image: Image.Image) -> str:
        key = sha256_png(image)
        if key in self.cache:
            self.hits += 1
            return self.cache[key]
        text = self.inner.read(image)
        if len(self.cache) >= self.limit:
            self.cache.pop(next(iter(self.cache)))
        self.cache[key] = text
        return text


class OllamaVisionReader:
    """Local Ollama vision model. Frames never leave the machine: a non-loopback
    endpoint is refused (a cloud-vision route needs a separate owner decision)."""

    def __init__(self, url: str = VISION_URL, model: str = VISION_MODEL, timeout: float = 120.0):
        if not _is_loopback(url):
            raise ValueError(f"vision endpoint must be local (loopback), got {url!r}")
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout
        self.identity = f"ollama:{model}@{urllib.parse.urlparse(url).port}"
        self.calls = 0
        self.seconds = 0.0

    def read(self, image: Image.Image) -> str:
        buf = io.BytesIO()
        image.save(buf, "PNG")
        body = {"model": self.model, "stream": False, "think": False, "keep_alive": "60m",
                "options": {"temperature": 0, "num_predict": 60},
                "messages": [{"role": "user", "content": PROMPT,
                              "images": [base64.b64encode(buf.getvalue()).decode()]}]}
        req = urllib.request.Request(self.url + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t = time.monotonic()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.load(r)
        self.calls += 1
        self.seconds += time.monotonic() - t
        return str((data.get("message") or {}).get("content") or "").strip()


# ------------------------------------------------------------------ location

def _saturated(p: tuple[int, int, int]) -> bool:
    return max(p) - min(p) > 90 and max(p) > 140


def locate_badges(img: Image.Image, layout: dict[str, Any] = DEFAULT_LAYOUT) -> list[tuple[int, int, int, int]]:
    """Bounding boxes of saturated badges on the right axis (top to bottom)."""
    x0, x1 = layout["axis_column"]
    y0, y1 = layout["axis_rows"]
    px = img.load()
    rows: list[tuple[int, int, int]] = []          # (y, minx, maxx) of rows with >= 25 saturated px
    for y in range(y0, min(y1, img.height)):
        xs = [x for x in range(x0, min(x1, img.width)) if _saturated(px[x, y])]
        if len(xs) >= 25:
            rows.append((y, xs[0], xs[-1]))
    boxes: list[list[int]] = []
    for y, a, b in rows:
        if boxes and y == boxes[-1][3] + 1:
            box = boxes[-1]
            box[0], box[2], box[3] = min(box[0], a), max(box[2], b), y
        else:
            boxes.append([a, y, b, y])
    return [(a, t, b + 1, btm + 1) for a, t, b, btm in boxes if 8 <= btm - t + 1 <= 16]


def locate_price_badge(img: Image.Image, layout: dict[str, Any] = DEFAULT_LAYOUT) -> tuple[int, int, int, int] | None:
    """The neutral-dark two-line last-price badge in the price pane (price line only)."""
    x0, x1 = layout["price_column"]
    y0, y1 = layout["price_pane_rows"]
    px = img.load()
    band: list[int] = []
    best: list[int] | None = None
    gap = 0
    for y in range(y0, min(y1, img.height)):
        # badge background just right of the axis line: darkest of 5 px (text is bright)
        edge = min((px[x, y] for x in range(x0 + 4, x0 + 9)), key=sum)
        neutral = edge[2] - edge[0] < 16 and 15 <= sum(edge) / 3 <= 45
        if neutral:
            band.append(y)
            gap = 0
            continue
        if band and gap < 2:                      # one or two compression-dark rows inside a badge
            gap += 1
            continue
        if 16 <= len(band) <= 34 and (best is None or len(band) > len(best)):
            best = band
        band, gap = [], 0
    if 16 <= len(band) <= 34 and (best is None or len(band) > len(best)):
        best = band
    if best is None:
        return None
    top = best[0]
    return (x0 - 2, top, x1 + 2, top + len(best) // 2 + 1)


# ------------------------------------------------------------------ reading

def _variants(crop: Image.Image) -> tuple[Image.Image, ...]:
    """Three decorrelated renderings of the same crop -> three reads, all must agree.

    Live run B (2026-09-24 10:02:21Z) recorded a FALSE VERIFIED: a 3x bicubic and a
    6x nearest-neighbour read of a teal badge showing 19.55B both returned 19.65B.
    Measured on the failing crops: a grayscale autocontrast rendering was right on
    every one; each colour rendering erred on some. Unanimity across colour and
    grayscale renderings removed every false value on those crops."""
    # v3 (calibration v5): at 3-5x the model read 83,595 as 83,695 unanimously (3/3);
    # at 8x every rendering read the 5/6 glyphs right. Small renderings are the bias.
    from PIL import ImageOps
    gray = ImageOps.autocontrast(ImageOps.grayscale(crop))
    k = 8
    # Owner golden JPEG: inverted gray interpreted the final B in 75.32B as 8.
    # Gray bicubic kept the unit legible; 40 archived calibration badges also
    # matched their verified values. Keep unanimity: one uncertain read is UNKNOWN.
    return (crop.resize((crop.width * k, crop.height * k), Image.LANCZOS),
            gray.resize((crop.width * k, crop.height * k), Image.LANCZOS).convert("RGB"),
            gray.resize((crop.width * k, crop.height * k), Image.BICUBIC).convert("RGB"))


def sha256_png(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return hashlib.sha256(buf.getvalue()).hexdigest()


@dataclass
class Reading:
    metric: str
    status: str                      # VERIFIED | LOW_CONFIDENCE | UNREADABLE | AMBIGUOUS_UNIT
    value: float | None = None
    unit: str | None = None
    raw: list[str] = field(default_factory=list)
    bbox: tuple[int, int, int, int] | None = None
    crop_sha256: str | None = None
    crop: Image.Image | None = None

    @property
    def confidence(self) -> float:
        return {"VERIFIED": 0.95, "LOW_CONFIDENCE": 0.5, "AMBIGUOUS_UNIT": 0.3}.get(self.status, 0.0)


def _agree(parsed: list[Any]) -> tuple[str, Any]:
    """VERIFIED only when EVERY read parsed and all are identical."""
    good = [p for p in parsed if p is not None]
    if len(good) == len(parsed) >= 2 and all(g == good[0] for g in good):
        return schema.VERIFIED, good[0]
    if (len(good) == len(parsed) >= 2 and isinstance(good[0], tuple)
            and all(g[0] == good[0][0] for g in good) and len({g[1] for g in good}) > 1):
        return schema.AMBIGUOUS_UNIT, None
    if good:
        return schema.LOW_CONFIDENCE, None
    return schema.UNREADABLE, None


def read_badge(reader: Reader, img: Image.Image, box: tuple[int, int, int, int]) -> Reading | None:
    """Read one axis badge; None when it is not an OI/CVD badge."""
    crop = img.crop((box[0] - 2, box[1] - 2, box[2] + 2, box[3] + 2))
    raws = [reader.read(v) for v in _variants(crop)]
    labelled = [schema.classify_badge(r) for r in raws]
    metrics = {lab[0] for lab in labelled if lab}
    if not metrics:
        return None
    if len(metrics) > 1:
        return Reading(metric=sorted(metrics)[0], status=schema.LOW_CONFIDENCE, raw=raws, bbox=box,
                       crop_sha256=sha256_png(crop), crop=crop)
    metric = metrics.pop()
    parsed = [schema.parse_abbrev(lab[1]) if lab else None for lab in labelled]
    status, value = _agree(parsed)
    return Reading(metric=metric, status=status, value=value[0] if value else None,
                   unit=value[1] if value else None, raw=raws, bbox=box,
                   crop_sha256=sha256_png(crop), crop=crop)


def read_price(reader: Reader, img: Image.Image, layout: dict[str, Any] = DEFAULT_LAYOUT) -> Reading:
    box = locate_price_badge(img, layout)
    if box is None:
        return Reading(metric="price", status=schema.UNREADABLE)
    crop = img.crop(box)
    raws = [reader.read(v) for v in _variants(crop)]
    parsed = [schema.parse_price(r) for r in raws]
    status, value = _agree(parsed)
    if value is not None and not 1_000 <= value <= 10_000_000:
        status, value = schema.LOW_CONFIDENCE, None
    return Reading(metric="price", status=status, value=value, raw=raws, bbox=box,
                   crop_sha256=sha256_png(crop), crop=crop)


_SEP = r"\s*[·•.,\-–—]\s*"
_SYMBOL = re.compile(r"^\s*([A-Z0-9!_:]{2,20})" + _SEP + r"(\d{1,4}[smhdDWM]?|[DWM])" + _SEP
                     + r"([A-Z][A-Z0-9 ]{1,20}?)\s*$")


def read_symbol(reader: Reader, img: Image.Image, layout: dict[str, Any] = DEFAULT_LAYOUT) -> dict[str, Any]:
    box = tuple(layout["symbol_box"])
    crop = img.crop(box)
    raw = reader.read(crop.resize((crop.width * 3, crop.height * 3), Image.BICUBIC))
    m = _SYMBOL.match(raw.replace("·", "·"))
    out = {"raw": raw, "bbox": box, "crop_sha256": sha256_png(crop), "symbol": None, "timeframe": None,
           "exchange": None, "crop": crop}
    if m:
        out.update(symbol=m.group(1), timeframe=m.group(2), exchange=m.group(3).strip())
    return out


def extract(reader: Reader, img: Image.Image, layout: dict[str, Any] = DEFAULT_LAYOUT) -> dict[str, Any]:
    """All readings of one fresh frame."""
    readings: dict[str, Reading] = {}
    for box in locate_badges(img, layout):
        r = read_badge(reader, img, box)
        if r is None:
            continue
        prev = readings.get(r.metric)
        if prev is not None and prev.status == schema.VERIFIED and r.status == schema.VERIFIED \
                and (prev.value, prev.unit) != (r.value, r.unit):
            r = Reading(metric=r.metric, status=schema.LOW_CONFIDENCE, raw=prev.raw + r.raw, bbox=r.bbox,
                        crop_sha256=r.crop_sha256, crop=r.crop)       # two different badges claim one metric
        if prev is None or prev.status != schema.VERIFIED or r.status == schema.LOW_CONFIDENCE:
            readings[r.metric] = r
    readings["price"] = read_price(reader, img, layout)
    return {"symbol": read_symbol(reader, img, layout), "readings": readings}
