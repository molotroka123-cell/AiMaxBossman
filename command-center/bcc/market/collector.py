"""Twitch OI/CVD collector loop — READ-ONLY data collection.

    python -m bcc.market.collector run [--cadence 15] [--minutes 70]
    python -m bcc.market.collector calibrate --samples 20
    python -m bcc.market.collector status | export | stop

Every attempt becomes one ledger row with a timestamp and a status. A number is
recorded only from a frame proven fresh in THIS attempt (video time advanced
while sampling, playing, frame hash differs from the last recorded frame); a
stale or unreadable attempt records nulls — never the previous value.

STOP: create <root>/STOP (or `stop`). Checked every 0.5 s while waiting and
between capture and parse; the current attempt is finished or dropped, the
ledger write is atomic per row. Restart re-opens the channel and requires a
fresh frame before the first row with numbers.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from PIL import Image

from . import extract, routing, schema
from .ledger import Ledger, default_root

CHANNEL = "k1m6a"
OWNER_URL = "https://m.twitch.tv/k1m6a"          # the source the owner named
# The desktop player of the SAME channel: its quality menu lets us pin 1080p.
# On m.twitch.tv the menu is click-intercepted and adaptive bitrate drops the
# stream to 480p under host load, which makes the axis labels illegible
# (calibration v3, 2026-09-24: rows 12-19 at 852x480).
URL = "https://www.twitch.tv/k1m6a"
QUALITY_PREFERENCE = ("1080p", "Источник", "Source")
FRAME_SIZE = (1920, 1080)          # extract.DEFAULT_LAYOUT coordinate space
JUMP_FLAG = 0.15                  # relative change between consecutive verified samples that gets flagged

_FRAME_JS = """() => {
  const v = document.querySelector('video');
  const txt = (document.body && document.body.innerText || '').slice(0, 4000);
  const feats = {video: document.querySelectorAll('video').length,
                 canvas: document.querySelectorAll('canvas').length,
                 iframes: document.querySelectorAll('iframe').length, shadow_roots: 0,
                 file_inputs: document.querySelectorAll('input[type=file]').length,
                 nested_scroll: 0, popups: 0};
  if (!v) return {video: null, text: txt, url: location.href, features: feats};
  return {video: {w: v.videoWidth, h: v.videoHeight, t: v.currentTime, paused: v.paused,
                  ended: v.ended, rs: v.readyState, err: v.error ? v.error.code : null},
          text: txt, url: location.href, features: feats};
}"""
_GRAB_JS = """() => { const v = document.querySelector('video');
  const c = document.createElement('canvas'); c.width = v.videoWidth; c.height = v.videoHeight;
  c.getContext('2d').drawImage(v, 0, 0); return c.toDataURL('image/png'); }"""
_DOM_BUTTONS = ("Отклонить", "Reject", "Decline", "Начать просмотр", "Start Watching")


@dataclass
class Capture:
    stream_state: str
    resolved_url: str | None
    frame: Image.Image | None = None
    frame_sha256: str | None = None
    video_t0: float | None = None
    video_t1: float | None = None
    paused: bool | None = None
    detail: str = ""
    routing: dict | None = None
    native_size: tuple[int, int] | None = None


def classify_state(info: dict[str, Any]) -> tuple[str, str]:
    text = (info.get("text") or "").lower()
    v = info.get("video")
    if any(k in text for k in ("log in to watch", "войдите, чтобы", "sign up to watch")):
        return "LOGIN_REQUIRED", "login gate"
    if v is None:
        if any(k in text for k in ("offline", "не в сети", "оффлайн")):
            return "OFFLINE", "channel offline"
        return "OFFLINE", "no video element"
    if v.get("err"):
        return "PLAYER_ERROR", f"media error {v['err']}"
    if not v.get("w") or (v.get("rs") or 0) < 2:
        return "PLAYER_ERROR", f"video not ready rs={v.get('rs')}"
    return "LIVE", "video playing" if not v.get("paused") else "video paused"


class TwitchSource:
    """Playwright Chromium on the owner PC. Low-risk, reversible DOM actions only
    (decline cookies, start watching). No login, no chat, no clicks elsewhere."""

    def __init__(self, url: str = URL, headless: bool = True):
        self.url, self.headless = url, headless
        self._pw = self._browser = self._page = None
        self.opened_at: float | None = None
        self.dom_actions: list[str] = []
        self.quality: str | None = None

    async def open(self) -> None:
        from playwright.async_api import async_playwright
        await self.close()
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless, args=["--autoplay-policy=no-user-gesture-required", "--mute-audio"])
        # a large player + the player's own "source" quality preference: adaptive
        # bitrate otherwise drops to 720p and the axis labels become illegible
        ctx = await self._browser.new_context(viewport={"width": 1920, "height": 1200})
        await ctx.add_init_script(
            "try { localStorage.setItem('video-quality', JSON.stringify({default: 'chunked'})); } catch (e) {}")
        self._page = await ctx.new_page()
        await self._page.goto(self.url, wait_until="domcontentloaded", timeout=60_000)
        await self._page.wait_for_timeout(8_000)
        self.opened_at = time.time()
        await self._dom_housekeeping()
        self.quality = await self._pin_quality()

    async def _pin_quality(self) -> str | None:
        """Player Settings -> Quality -> 1080p (else Source): turns adaptive bitrate off.
        Observed-target DOM clicks on the player's own menu; reversible, no account."""
        page = self._page
        try:
            await page.mouse.move(600, 400)
            await page.wait_for_timeout(300)
            await page.mouse.move(640, 420)
            await page.click('[data-a-target="player-settings-button"]', timeout=5_000)
            await page.wait_for_timeout(600)
            await page.click('[data-a-target="player-settings-menu-item-quality"]', timeout=5_000)
            await page.wait_for_timeout(600)
            options = page.locator('[data-a-target="player-settings-submenu-quality-option"]')
            for want in QUALITY_PREFERENCE:
                opt = options.filter(has_text=want)
                if await opt.count():
                    label = " ".join((await opt.first.inner_text()).split())
                    await opt.first.click(timeout=5_000)
                    await page.keyboard.press("Escape")
                    self.dom_actions.append(f"quality:{label}")
                    return label
        except Exception as exc:                    # pinning is best effort; frames still get checked
            self.dom_actions.append(f"quality_pin_failed:{type(exc).__name__}")
        return None

    async def _dom_housekeeping(self) -> None:
        info = await self._page.evaluate(_FRAME_JS)
        decision = routing.route({"features": {**info["features"], "video": 0, "canvas": 0}}, target="dom")
        if decision["route"] != routing.JEV_DOM:
            return
        for label in _DOM_BUTTONS:
            btn = self._page.get_by_role("button", name=label, exact=True)
            try:
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click(timeout=3_000)
                    self.dom_actions.append(label)
                    await self._page.wait_for_timeout(1_000)
            except Exception:                       # a banner that vanished is not an error
                pass

    async def close(self) -> None:
        for obj, meth in ((self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    await getattr(obj, meth)()
                except Exception:
                    pass
        self._pw = self._browser = self._page = None

    async def capture(self) -> Capture:
        if self._page is None:
            await self.open()
        info0 = await self._page.evaluate(_FRAME_JS)
        state, detail = classify_state(info0)
        route = routing.route({"features": info0["features"]}, target="pixels")
        if state != "LIVE":
            return Capture(state, info0.get("url"), detail=detail, routing=route)
        await self._page.wait_for_timeout(400)
        data_url = await self._page.evaluate(_GRAB_JS)
        info1 = await self._page.evaluate(_FRAME_JS)
        raw = __import__("base64").b64decode(data_url.split(",", 1)[1])
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        native = img.size
        if native[1] < FRAME_SIZE[1]:               # adaptive bitrate slipped: pin again for the next sample
            self.quality = await self._pin_quality()
        if native != FRAME_SIZE:                  # layout is in 1920x1080 space; the hash stays the native frame's
            img = img.resize(FRAME_SIZE, Image.LANCZOS)
        return Capture("LIVE", info1.get("url"), frame=img, frame_sha256=hashlib.sha256(raw).hexdigest(),
                       video_t0=info0["video"]["t"], video_t1=(info1.get("video") or {}).get("t"),
                       paused=(info1.get("video") or {}).get("paused"),
                       detail=f"{detail}; quality={self.quality or 'auto'}", routing=route,
                       native_size=native)


def build_record(cap: Capture, reader: extract.Reader | None, *, last_frame_sha: str | None,
                 last_verified: dict[str, float] | None = None, crops_dir: Path | None = None,
                 stopping: Callable[[], bool] = lambda: False) -> dict[str, Any]:
    """One ledger row from one capture. Pure except for optional crop files."""
    rec = schema.new_observation(channel=CHANNEL, requested_url=URL, resolved_url=cap.resolved_url,
                                 stream_state=cap.stream_state)
    rec["evidence"]["routing"] = cap.routing
    rec["source"]["owner_url"] = OWNER_URL
    if cap.stream_state != "LIVE":
        rec["quality"]["status"] = {"OFFLINE": schema.STREAM_OFFLINE, "PLAYER_ERROR": schema.PLAYER_ERROR,
                                    "LOGIN_REQUIRED": schema.LOGIN_REQUIRED}[cap.stream_state]
        rec["evidence"]["detail"] = cap.detail
        return rec
    rec["evidence"]["frame_sha256"] = cap.frame_sha256
    rec["evidence"]["video_time"] = [cap.video_t0, cap.video_t1]
    rec["evidence"]["native_frame_size"] = list(cap.native_size) if cap.native_size else None
    rec["evidence"]["player"] = cap.detail
    fresh = (cap.frame is not None and not cap.paused and cap.video_t0 is not None and cap.video_t1 is not None
             and cap.video_t1 > cap.video_t0 and cap.frame_sha256 != last_frame_sha)
    rec["quality"]["fresh_frame"] = bool(fresh)
    if not fresh:
        rec["quality"]["status"] = schema.STALE_FRAME
        rec["evidence"]["detail"] = "frame not proven fresh (video time did not advance or same frame)"
        return rec
    if stopping():
        rec["quality"]["status"] = schema.UNREADABLE
        rec["evidence"]["detail"] = "STOP before parse: parse aborted"
        return rec
    out = extract.extract(reader, cap.frame)
    rec["evidence"]["extractor"] = f"{reader.identity} double-read"
    sym = out["symbol"]
    rec["evidence"]["ocr_raw"]["symbol"] = sym["raw"]
    rec["evidence"]["bbox"]["symbol"] = list(sym["bbox"])
    rec["evidence"]["crop_sha256"]["symbol"] = sym["crop_sha256"]
    rec["instrument"].update(symbol=sym["symbol"], timeframe=sym["timeframe"], exchange=sym["exchange"])
    per: dict[str, Any] = {}
    stamp = rec["captured_at_utc"].replace(":", "").replace("-", "").replace(".", "")
    for metric in ("cvd", "oi", "price"):
        r = out["readings"].get(metric)
        if r is None:
            per[metric] = {"status": schema.UNREADABLE, "confidence": 0.0, "reason": "badge not found"}
            continue
        per[metric] = {"status": r.status, "confidence": r.confidence}
        rec["evidence"]["ocr_raw"][metric] = r.raw
        rec["evidence"]["bbox"][metric] = list(r.bbox) if r.bbox else None
        if r.crop_sha256:
            rec["evidence"]["crop_sha256"][metric] = r.crop_sha256
        if crops_dir is not None and r.crop is not None:
            r.crop.save(crops_dir / f"{stamp}-{metric}.png")
        if r.status != schema.VERIFIED:
            continue
        if metric == "price":
            rec["metrics"]["price"] = r.value
        else:
            rec["metrics"][f"{metric}_value"], rec["metrics"][f"{metric}_unit"] = r.value, r.unit
    if crops_dir is not None and sym.get("crop") is not None:
        sym["crop"].save(crops_dir / f"{stamp}-symbol.png")
    if rec["metrics"]["cvd_value"] is not None:
        rec["metrics"]["cvd_type"] = "aggregated"          # pane title: "Coinwise - Aggregated CVD Pro"
    rec["quality"]["per_metric"] = per
    flags = []
    for key in ("oi", "cvd"):
        new = rec["metrics"][f"{key}_value"]
        unit = rec["metrics"][f"{key}_unit"]
        old = (last_verified or {}).get(key)
        if new is not None and old is not None:
            absolute = new * schema.UNITS[unit]
            if old and abs(absolute - old) / abs(old) > JUMP_FLAG:
                flags.append(f"{key}_implausible_jump")
    rec["quality"]["flags"] = flags
    verified = [m for m in ("oi", "cvd") if rec["metrics"][f"{m}_value"] is not None]
    # the record's status is about OI/CVD; a doubtful optional price stays per-metric
    doubtful = [m for m in ("oi", "cvd") if per.get(m, {}).get("status") in
                (schema.LOW_CONFIDENCE, schema.AMBIGUOUS_UNIT)]
    if not sym["symbol"]:
        rec["quality"]["status"] = schema.AMBIGUOUS_SYMBOL
        for k in ("price", "oi_value", "oi_unit", "cvd_value", "cvd_unit", "cvd_type"):
            rec["metrics"][k] = None                           # a number without its instrument is not data
        rec["quality"]["confidence"] = 0.0
        return rec
    if flags or (doubtful and not verified):
        rec["quality"]["status"] = schema.LOW_CONFIDENCE
    elif verified:
        rec["quality"]["status"] = schema.VERIFIED if not doubtful else schema.LOW_CONFIDENCE
    else:
        rec["quality"]["status"] = schema.UNREADABLE
    confs = [per[m]["confidence"] for m in ("oi", "cvd") if m in per]
    rec["quality"]["confidence"] = round(min(confs), 3) if confs and verified else 0.0
    return rec


class Collector:
    def __init__(self, root: Path, source: Any, reader: extract.Reader | None, *, cadence: float = 15.0,
                 offline_cadence: float = 90.0, keep_frames: int = 0):
        self.root, self.source, self.reader = Path(root), source, reader
        self.cadence, self.offline_cadence, self.keep_frames = cadence, offline_cadence, keep_frames
        self.ledger = Ledger(self.root)
        self.stop_file = self.root / "STOP"
        self.status_file = self.root / "reports" / "collector-status.json"
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        self.last_frame_sha: str | None = None
        self.last_verified: dict[str, float] = {}
        self.attempts = 0
        self.started = schema.utc_now()

    def stopping(self) -> bool:
        return self.stop_file.exists()

    async def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.stopping():
            await asyncio.sleep(min(0.5, end - time.monotonic()))

    def _write_status(self, rec: dict[str, Any] | None, state: str) -> None:
        body = {"pid": os.getpid(), "state": state, "started": self.started, "updated": schema.utc_now(),
                "attempts_this_run": self.attempts, "cadence_s": self.cadence,
                "last": {"at": rec["captured_at_utc"], "status": rec["quality"]["status"],
                         "stream_state": rec["source"]["stream_state"],
                         "oi": [rec["metrics"]["oi_value"], rec["metrics"]["oi_unit"]],
                         "cvd": [rec["metrics"]["cvd_value"], rec["metrics"]["cvd_unit"]],
                         "price": rec["metrics"]["price"]} if rec else None,
                "ledger": self.ledger.counts(), "trading": "NONE — data collection only",
                "dataset_status": "DATA_COLLECTION"}
        tmp = self.status_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.status_file)

    async def sample_once(self) -> dict[str, Any]:
        try:
            cap = await self.source.capture()
        except Exception as exc:                          # browser/player recovery: reopen next time
            cap = Capture("PLAYER_ERROR", None, detail=f"{type(exc).__name__}: {exc}"[:300])
            try:
                await self.source.close()
            except Exception:
                pass
        day = schema.utc_now()[:10]
        rec = build_record(cap, self.reader, last_frame_sha=self.last_frame_sha,
                           last_verified=self.last_verified, crops_dir=self.ledger.crops_dir(day),
                           stopping=self.stopping)
        if self.keep_frames and cap.frame is not None:
            fdir = self.root / "frames" / day
            fdir.mkdir(parents=True, exist_ok=True)
            cap.frame.save(fdir / f"{rec['captured_at_utc'].replace(':', '')}.png")
            self.keep_frames -= 1
        self.ledger.record(rec)
        self.attempts += 1
        if rec["quality"]["fresh_frame"]:
            self.last_frame_sha = cap.frame_sha256
        if rec["quality"]["status"] == schema.VERIFIED:
            for key in ("oi", "cvd"):
                if rec["metrics"][f"{key}_value"] is not None:
                    self.last_verified[key] = rec["metrics"][f"{key}_value"] * schema.UNITS[rec["metrics"][f"{key}_unit"]]
        self._write_status(rec, "running")
        return rec

    async def run(self, *, minutes: float | None = None, max_attempts: int | None = None) -> int:
        deadline = time.monotonic() + minutes * 60 if minutes else None
        rec = None
        try:
            while not self.stopping():
                if deadline and time.monotonic() >= deadline:
                    break
                if max_attempts is not None and self.attempts >= max_attempts:
                    break
                t0 = time.monotonic()
                rec = await self.sample_once()
                wait = self.cadence if rec["source"]["stream_state"] == "LIVE" else self.offline_cadence
                await self._sleep(max(0.0, wait - (time.monotonic() - t0)))
        finally:
            try:
                await self.source.close()
            finally:
                self._write_status(rec, "stopped" if self.stopping() else "finished")
                self.ledger.close()
        return self.attempts


# ------------------------------------------------------------------ CLI

def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bcc.market.collector", description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("run", "calibrate", "status", "export", "stop", "reindex"))
    ap.add_argument("--root", default=None)
    ap.add_argument("--cadence", type=float, default=15.0)
    ap.add_argument("--offline-cadence", type=float, default=90.0)
    ap.add_argument("--minutes", type=float, default=None)
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--keep-frames", type=int, default=0)
    ap.add_argument("--headed", action="store_true")
    ns = ap.parse_args(argv)
    root = Path(ns.root) if ns.root else default_root(CHANNEL)
    root.mkdir(parents=True, exist_ok=True)
    if ns.command == "stop":
        (root / "STOP").write_text(schema.utc_now(), encoding="utf-8")
        print(f"STOP written: {root / 'STOP'}")
        return 0
    if ns.command == "status":
        path = root / "reports" / "collector-status.json"
        print(path.read_text(encoding="utf-8") if path.exists() else json.dumps(Ledger(root).counts()))
        return 0
    if ns.command == "export":
        print(Ledger(root).export_csv())
        return 0
    if ns.command == "reindex":
        print(Ledger(root).reindex())
        return 0
    pid_file = root / "collector.pid"
    if pid_file.exists():
        try:
            other = int(pid_file.read_text().strip())
        except ValueError:
            other = 0
        if other and other != os.getpid() and _pid_alive(other):
            print(f"collector already running (pid {other}); refusing a second one", file=sys.stderr)
            return 3
    (root / "STOP").unlink(missing_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    reader = extract.CachedReader(extract.OllamaVisionReader())
    source = TwitchSource(headless=not ns.headed)
    keep = ns.samples if ns.command == "calibrate" else ns.keep_frames
    col = Collector(root, source, reader, cadence=ns.cadence, offline_cadence=ns.offline_cadence, keep_frames=keep)
    try:
        n = asyncio.run(col.run(minutes=ns.minutes,
                                max_attempts=ns.samples if ns.command == "calibrate" else None))
    finally:
        pid_file.unlink(missing_ok=True)
    print(json.dumps({"attempts": n, "ledger": Ledger(root).counts()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
