#!/usr/bin/env python3
"""YouTube replay -> timestamped teacher claims -> independent exchange verification.

End-to-end (per public video, run dir prepared by yt-dlp with ``audio.*``,
``subs.<id>.en-orig.vtt`` and ``subs.<id>.live_chat.json``):

1. transcript: local ASR ``asr.segments.json`` (tools/youtube_trader_ingest_asr.py)
   or, only if missing, YouTube auto-captions — the source is labeled;
2. UTC alignment: median(chat wall-clock - chat video offset) from the public
   live-chat replay (only the derived number is kept; no chat text or ids);
3. claims: local Ollama model (``think: false``) over transcript windows, then
   deterministic validation (quote must be verbatim, number must be in the quote);
   every claim starts ``UNVERIFIED``;
4. independent verification: Binance USDT-M BTCUSDT 1m klines (price, taker-buy
   CVD proxy, developing dOpen/dPOC/dVAH/dVAL) and Bybit 5m open interest at
   horizons 1/5/15/30/60/240 min; liquidations -> ``UNKNOWN``;
5. optional frames: a few frames at claim times through the local vision model;
   unreadable fields stay ``UNKNOWN``.

No trading, no posting, no private data. Every model call checks the owner PAUSE file.

    python tools/youtube_trader_ingest_claims.py run <run_dir> --role known|held_out
    python tools/youtube_trader_ingest_claims.py frames <run_dir> --max-frames 4
    python tools/youtube_trader_ingest_claims.py report <evidence_root>
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from learning.claim_verification import (  # noqa: E402
    DIRECTIONS, HORIZONS_MIN, KINDS, METRICS, Claim, Market, align_from_chat, overall_status,
    summarize, value_supported_by_quote, verify_claim,
)
from tools.route_guard import assert_free_or_local  # noqa: E402
from tools.owner_journeys.runtime_guard import lower_priority, wait_if_paused  # noqa: E402

OLLAMA = "http://127.0.0.1:11434"
TEXT_MODEL = "bossman-fast-qwen36-35b-a3b-q5:latest"
VISION_MODEL = "bossman-fast-qwen36-vision:latest"
BINANCE_KLINES = "https://fapi.binance.com/fapi/v1/klines"
BYBIT_OI = "https://api.bybit.com/v5/market/open-interest"
WINDOW_S = 240.0

EXTRACT_SYSTEM = """You extract market claims from a crypto trading livestream transcript.
Return JSON only: {"claims": [ ... ]}. Return {"claims": []} when there are none.
A claim is something the speaker asserts about a market metric, now or in the future.
Allowed metric values: PRICE, CVD, OI, LIQUIDATIONS, dPOC, dVAH, dVAL, dOpen.
Mapping: open interest -> OI; cumulative volume delta / CVD / delta -> CVD;
liquidations / liqs / longs or shorts getting liquidated -> LIQUIDATIONS;
daily or developing POC / point of control -> dPOC; value area high / VAH -> dVAH;
value area low / VAL -> dVAL; daily open -> dOpen; price, bitcoin going up/down, targets -> PRICE.
Fields for each claim:
  seg: integer id of the transcript line where it is said
  quote: 3-25 words copied EXACTLY from that line (verbatim, no paraphrase)
  metric: one of the allowed values
  kind: STATE = describes the market right now (roughly the last 15 minutes);
        FORECAST = expects something within the next few hours;
        HISTORY = about an earlier period (last night, yesterday, last week, earlier today);
        LONG_TERM = expectation beyond one day (this week, this year, cycle targets)
  direction: UP, DOWN, ABOVE, BELOW, AT, TOUCH or null
     UP/DOWN = rising/falling or will rise/fall; ABOVE/BELOW = price relative to a level or number;
     AT = the speaker says price (or the named level) is currently at that number;
     TOUCH = price will reach (FORECAST) or just reached (STATE) a target number or level.
     A number merely "watched" or "of interest" without such a statement is not a claim.
  value: the number exactly as written in the quote (e.g. 72.5 or 72500), else null
  asset: BTC, ETH, SOL, ... when named or clearly the subject, else null
Not claims (skip them): conditionals ("if it closes above 78 ...") unless the speaker says the condition
is true now; questions; quoting or reading chat; other people's positions or entries; jokes; music.
Never invent numbers: value must appear in the quote."""

_WORD_RE = re.compile(r"[a-z0-9]+")


def _norm_words(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


# ------------------------------------------------------------------ inputs

def load_transcript(run: Path) -> tuple[list[dict[str, Any]], str]:
    asr = run / "asr.segments.json"
    if asr.is_file():
        meta = json.loads((run / "asr.meta.json").read_text(encoding="utf-8")) if (run / "asr.meta.json").is_file() else {}
        return json.loads(asr.read_text(encoding="utf-8")), str(meta.get("source") or "local_asr")
    vtt = next(iter(sorted(run.glob("subs.*.en-orig.vtt"))), None)
    if vtt is None:
        return [], "NONE"
    from tools.youtube_trader_ingest import parse_vtt

    cues = parse_vtt(vtt.read_text(encoding="utf-8", errors="replace"))
    return [{"start": c.start, "end": c.end, "text": c.text} for c in cues], "youtube_auto_captions:en-orig"


def chat_pairs(run: Path) -> list[tuple[float, float]]:
    """(video_offset_s, wall_epoch_s) of in-stream text messages; text/ids are discarded."""
    path = next(iter(sorted(run.glob("subs.*.live_chat.json"))), None)
    if path is None:
        return []
    pairs = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        replay = row.get("replayChatItemAction") or {}
        off = replay.get("videoOffsetTimeMsec")
        for action in replay.get("actions") or []:
            item = (action.get("addChatItemAction") or {}).get("item") or {}
            msg = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
            if msg and msg.get("timestampUsec") and off is not None:
                pairs.append((int(off) / 1000.0, int(msg["timestampUsec"]) / 1e6))
    return pairs


def windows(segments: list[dict[str, Any]], size_s: float = WINDOW_S) -> list[list[tuple[int, dict]]]:
    out: list[list[tuple[int, dict]]] = []
    cur: list[tuple[int, dict]] = []
    start = None
    for i, seg in enumerate(segments):
        if start is None:
            start = float(seg["start"])
        if float(seg["start"]) - start >= size_s and cur:
            out.append(cur)
            cur, start = [], float(seg["start"])
        cur.append((i, seg))
    if cur:
        out.append(cur)
    return out


# ------------------------------------------------------------------ model

def ollama_chat(model: str, messages: list[dict], *, fmt: Optional[str] = "json",
                timeout: int = 300) -> tuple[str, float]:
    # num_ctx is deliberately NOT overridden: a different context size forces Ollama
    # to reload the shared model and evicts it for the other workstreams.
    assert_free_or_local(OLLAMA, model)   # local only; refused before any I/O
    wait_if_paused(log=lambda s: print(s, flush=True))
    body: dict[str, Any] = {"model": model, "messages": messages, "stream": False, "think": False,
                            "keep_alive": "10m", "options": {"temperature": 0}}
    if fmt:
        body["format"] = fmt
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - loopback Ollama
        data = json.loads(resp.read().decode("utf-8"))
    return str((data.get("message") or {}).get("content") or ""), time.time() - t


def _json_obj(text: str) -> dict[str, Any]:
    raw = text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        a, b = raw.find("{"), raw.rfind("}")
        if a >= 0 and b > a:
            return json.loads(raw[a:b + 1])
        raise


def validate_claims(raw: dict[str, Any], window: list[tuple[int, dict]], *, video_id: str,
                    source: str, epoch0: Optional[float]) -> tuple[list[Claim], dict[str, int]]:
    """Deterministic gate on model output: verbatim quote, schema, number-in-quote."""
    by_id = {i: seg for i, seg in window}
    stats = {"proposed": 0, "rejected_quote": 0, "rejected_schema": 0, "value_dropped": 0}
    out: list[Claim] = []
    for item in (raw.get("claims") or []) if isinstance(raw, dict) else []:
        stats["proposed"] += 1
        if not isinstance(item, dict):
            stats["rejected_schema"] += 1
            continue
        try:
            seg_id = int(item.get("seg"))
        except (TypeError, ValueError):
            stats["rejected_schema"] += 1
            continue
        metric, kind = item.get("metric"), item.get("kind")
        direction = item.get("direction")
        if seg_id not in by_id or metric not in METRICS or kind not in KINDS or (
                direction is not None and direction not in DIRECTIONS):
            stats["rejected_schema"] += 1
            continue
        quote = str(item.get("quote") or "").strip()
        context = " ".join(str(by_id[j]["text"]) for j in (seg_id - 1, seg_id, seg_id + 1) if j in by_id)
        if len(_norm_words(quote).split()) < 3 or _norm_words(quote) not in _norm_words(context):
            stats["rejected_quote"] += 1
            continue
        value = item.get("value")
        notes: list[str] = []
        try:
            value = None if value is None else float(str(value).replace(",", ""))
        except ValueError:
            value = None
        if value is not None and not value_supported_by_quote(value, quote):
            value, _ = None, notes.append("value_not_in_quote_dropped")
            stats["value_dropped"] += 1
        asset = str(item.get("asset") or "").strip().upper()
        instrument = "BTC" if asset in ("BTC", "BITCOIN", "XBT") else (asset or "UNSTATED")
        t_video = float(by_id[seg_id]["start"])
        out.append(Claim(
            claim_id=f"{video_id}-{int(t_video):06d}-{len(out):02d}", video_id=video_id, t_video_s=t_video,
            quote=quote, metric=metric, kind=kind, direction=direction, value=value,
            instrument=instrument, t_utc=None if epoch0 is None else epoch0 + t_video,
            transcript_source=source, notes=notes,
        ))
    return out, stats


# ------------------------------------------------------------------ market data

def _get_json(url: str, params: dict[str, Any]) -> Any:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"User-Agent": "bossman-rc19-verifier"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - public market data
        return json.loads(resp.read().decode("utf-8"))


def fetch_market(start_s: float, end_s: float, cache: Path) -> dict[str, Any]:
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    klines: list[dict[str, Any]] = []
    t = int(start_s) * 1000
    while t < end_s * 1000:
        rows = _get_json(BINANCE_KLINES, {"symbol": "BTCUSDT", "interval": "1m", "startTime": t, "limit": 1500})
        if not rows:
            break
        for r in rows:
            klines.append({"t": r[0] // 1000, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
                           "c": float(r[4]), "v": float(r[5]), "tb": float(r[9])})
        t = rows[-1][0] + 60_000
        time.sleep(0.2)
    oi: list[dict[str, Any]] = []
    step = 200 * 300
    t = int(start_s)
    end_s = int(end_s)
    while t < end_s:
        data = _get_json(BYBIT_OI, {"category": "linear", "symbol": "BTCUSDT", "intervalTime": "5min",
                                    "startTime": t * 1000, "endTime": int(min(end_s, t + step)) * 1000,
                                    "limit": 200})
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit OI error {data.get('retCode')}: {data.get('retMsg')}")
        for r in (data.get("result") or {}).get("list") or []:
            oi.append({"t": int(r["timestamp"]) // 1000, "oi": float(r["openInterest"])})
        t += step
        time.sleep(0.2)
    uniq = {r["t"]: r for r in oi}
    out = {"source": {"price_cvd_levels": "binance-usdm BTCUSDT 1m klines (fapi/v1/klines)",
                      "open_interest": "bybit v5 linear BTCUSDT open-interest 5min",
                      "liquidations": "NONE (no public historical feed) -> UNKNOWN"},
           "start": start_s, "end": end_s, "klines": klines, "oi": [uniq[k] for k in sorted(uniq)]}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


# ------------------------------------------------------------------ pipeline

def run_video(run: Path, *, role: str, model: str = TEXT_MODEL, window_s: float = WINDOW_S,
              chat: Callable[..., tuple[str, float]] = ollama_chat, max_windows: int = 0) -> dict[str, Any]:
    started = time.time()
    video_id = run.name
    meta = json.loads((run.parent.parent / f"meta-{video_id}.json").read_text(encoding="utf-8"))
    duration = float(meta.get("duration") or 0)
    segments, source = load_transcript(run)
    align = align_from_chat(chat_pairs(run))
    release = meta.get("release_timestamp")
    if align.get("status") == "ALIGNED" and release:
        align["delta_vs_release_timestamp_s"] = round(align["epoch_at_offset0"] - float(release), 3)
    epoch0 = align.get("epoch_at_offset0") if align.get("status") == "ALIGNED" else None

    claims: list[Claim] = []
    gate = {"proposed": 0, "rejected_quote": 0, "rejected_schema": 0, "value_dropped": 0, "model_errors": 0}
    latencies: list[float] = []
    wins = windows(segments, window_s)
    if max_windows:
        wins = wins[:max_windows]
    for w in wins:
        lines = "\n".join(f"[{i}] {seg['text']}" for i, seg in w)
        msgs = [{"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": f"Transcript lines (id, text):\n{lines}"}]
        try:
            text, dt = chat(model, msgs)
            latencies.append(dt)
            found, st = validate_claims(_json_obj(text), w, video_id=video_id, source=source, epoch0=epoch0)
        except Exception as exc:  # noqa: BLE001
            gate["model_errors"] += 1
            print(json.dumps({"video": video_id, "window_start": w[0][1]["start"], "error": type(exc).__name__}),
                  flush=True)
            continue
        for k, v in st.items():
            gate[k] += v
        claims.extend(found)
        print(json.dumps({"video": video_id, "t": w[0][1]["start"], "claims": len(found), "s": round(dt, 1)}),
              flush=True)

    results: list[dict[str, Any]] = []
    market_meta: dict[str, Any] = {"status": "NOT_FETCHED"}
    if epoch0 is not None:
        session = math.floor(epoch0 / 86400) * 86400
        mk = fetch_market(session, epoch0 + duration + 5 * 3600, run / "market.json")
        market = Market(mk["klines"], mk["oi"])
        market_meta = {"status": "FETCHED", "source": mk["source"], "klines": len(mk["klines"]), "oi": len(mk["oi"])}
        results = [verify_claim(c, market) for c in claims]
    else:
        results = [verify_claim(c, Market([])) for c in claims]

    rows = []
    for c, ev in zip(claims, results):
        d = c.to_dict()
        d["verification"] = ev
        d["verification_status"] = overall_status(ev)
        rows.append(d)
    (run / "claims.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                      encoding="utf-8")
    summary = summarize(claims, results)
    summary["instrument"] = {k: sum(1 for c in claims if c.instrument == k) for k in {c.instrument for c in claims}}
    lat = sorted(latencies)
    report = {
        "video_id": video_id, "role": role, "title": meta.get("title"), "upload_date": meta.get("upload_date"),
        "duration_s": duration, "transcript_source": source, "transcript_segments": len(segments),
        "alignment": align, "model_route": {"claims": f"ollama:{model} think=false temperature=0",
                                            "asr": source},
        "windows": len(wins), "gate": gate, "market": market_meta, "summary": summary,
        "latency_s": {"n": len(lat), "p50": round(statistics.median(lat), 2) if lat else None,
                      "p95": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 2) if lat else None},
        "runtime_s": round(time.time() - started, 1),
        "horizons_min": list(HORIZONS_MIN),
        "learning_status": "UNVERIFIED_TEACHER_CLAIMS_SCORED; nothing promoted",
    }
    (run / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


# ------------------------------------------------------------------ frames

FRAME_PROMPT = """Read this trading-stream frame. Return JSON only with keys:
chart_price, instrument, venue, timeframe, cvd, open_interest, dPOC, dVAH, dVAL, dOpen, notes.
Use null for anything not clearly readable. Never guess digits. Numbers as plain numbers."""


def grab_frame(url: str, t: float, out: Path, local_video: Optional[Path] = None) -> bool:
    """First frame at t from the locally downloaded public stream (yt-dlp native download).

    Remote seeking with ffmpeg / --download-sections hung on YouTube's throttled ranges
    (measured 2026-09-28), so the frame pass needs the video file in the run dir.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or local_video is None or not local_video.is_file():
        return False
    try:
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", f"{t:.1f}", "-i", str(local_video),
                        "-frames:v", "1", "-q:v", "2", str(out)], capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False
    return out.is_file()


def _num(v: Any) -> Optional[float]:
    try:
        return None if v in (None, "", "null") else float(str(v).replace(",", ""))
    except ValueError:
        return None


def frame_checks(fields: dict[str, Any], instrument: Any, t_utc: Optional[float], market: Market) -> dict[str, Any]:
    """Independent checks of what the vision model read on a frame (BTC charts only)."""
    inst = str(instrument or "").lower()
    if not t_utc or not any(k in inst for k in ("btc", "bitcoin", "xbt")):
        why = "frame instrument is not BTC" if t_utc else "frame not aligned"
        return {k: {"status": "UNKNOWN", "reason": why} for k in ("chart_price", *LEVELS_FOR_FRAMES)}
    out: dict[str, Any] = {}
    px = market.price(t_utc)
    cp = _num(fields.get("chart_price"))
    if cp is None or px is None:
        out["chart_price"] = {"status": "UNKNOWN", "reason": "not readable" if cp is None else "no market price"}
    else:
        err = abs(cp - px) / px
        out["chart_price"] = {"status": "VERIFIED" if err <= 0.01 else "REFUTED", "frame": cp, "binance": px,
                              "rel_err": round(err, 5)}
    levels = market.developing_levels(t_utc)
    for k in LEVELS_FOR_FRAMES:
        v, ref = _num(fields.get(k)), levels.get(k)
        if v is None or ref is None:
            out[k] = {"status": "UNKNOWN", "reason": "not readable" if v is None else "level not computable"}
            continue
        err = abs(v - ref) / ref
        out[k] = {"status": "VERIFIED" if err <= 0.0035 else "REFUTED", "frame": v, "computed": ref,
                  "rel_err": round(err, 5)}
    return out


LEVELS_FOR_FRAMES = ("dPOC", "dVAH", "dVAL", "dOpen")


def recheck_frames(run: Path) -> dict[str, Any]:
    """Recompute frame checks from stored vision readings (no model call)."""
    res = json.loads((run / "frames.json").read_text(encoding="utf-8"))
    rep_ = json.loads((run / "report.json").read_text(encoding="utf-8"))
    epoch0 = (rep_.get("alignment") or {}).get("epoch_at_offset0")
    mk = json.loads((run / "market.json").read_text(encoding="utf-8"))
    market = Market(mk["klines"], mk["oi"])
    for f in res["frames"]:
        if "fields" not in f:
            continue
        t_utc = epoch0 + f["t_video_s"] if epoch0 else None
        f["t_utc"] = t_utc
        f["checks"] = frame_checks(f["fields"], f.get("instrument"), t_utc, market)
    (run / "frames.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    return res


def run_frames(run: Path, *, max_frames: int = 4, model: str = VISION_MODEL) -> dict[str, Any]:
    import base64

    video_id = run.name
    url = f"https://www.youtube.com/watch?v={video_id}"
    rows = [json.loads(x) for x in (run / "claims.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    mk = json.loads((run / "market.json").read_text(encoding="utf-8")) if (run / "market.json").is_file() else None
    market = Market(mk["klines"], mk["oi"]) if mk else Market([])
    # prefer level/price claims with explicit values, spread over the video
    pick = sorted([r for r in rows if r["metric"] in ("PRICE", "dPOC", "dVAH", "dVAL", "dOpen")],
                  key=lambda r: (r["value"] is None, r["t_video_s"]))
    chosen, seen = [], []
    for r in pick:
        if all(abs(r["t_video_s"] - s) > 600 for s in seen):
            chosen.append(r)
            seen.append(r["t_video_s"])
        if len(chosen) >= max_frames:
            break
    fdir = run / "frames"
    fdir.mkdir(exist_ok=True)
    local_video = next((p for p in sorted(run.glob("video.*")) if p.suffix in (".mp4", ".webm", ".mkv")), None)
    out = []
    for r in chosen:
        f = fdir / f"t{int(r['t_video_s']):06d}.jpg"
        if not f.is_file() and not grab_frame(url, r["t_video_s"], f, local_video):
            out.append({"t_video_s": r["t_video_s"], "status": "UNKNOWN", "reason": "frame not retrievable"})
            continue
        img = base64.b64encode(f.read_bytes()).decode()
        try:
            text, dt = ollama_chat(model, [{"role": "user", "content": FRAME_PROMPT, "images": [img]}],
                                   timeout=600)
            obs = _json_obj(text)
        except Exception as exc:  # noqa: BLE001
            out.append({"t_video_s": r["t_video_s"], "status": "UNKNOWN", "reason": type(exc).__name__})
            continue
        fields = {k: obs.get(k) for k in ("chart_price", "cvd", "open_interest", "dPOC", "dVAH", "dVAL", "dOpen")}
        unknown = [k for k, v in fields.items() if v in (None, "", "null")]
        t_utc = r.get("t_utc")
        check = frame_checks(fields, obs.get("instrument"), t_utc, market)
        out.append({"t_video_s": r["t_video_s"], "frame": f.name, "latency_s": round(dt, 1),
                    "instrument": obs.get("instrument"), "venue": obs.get("venue"),
                    "timeframe": obs.get("timeframe"), "fields": fields, "unknown_fields": unknown,
                    "checks": check})
        print(json.dumps({"video": video_id, "frame": f.name, "unknown": unknown, "s": round(dt, 1)}), flush=True)
    res = {"video_id": video_id, "model": model, "frames": out}
    (run / "frames.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    return res


def aggregate(evidence_root: Path) -> dict[str, Any]:
    """Collect per-video reports (+ frames and manual precision audits when present)."""
    videos = []
    for rep_path in sorted((evidence_root / "raw").glob("*/report.json")):
        rep = json.loads(rep_path.read_text(encoding="utf-8"))
        row = {k: rep[k] for k in ("video_id", "role", "title", "duration_s", "transcript_source", "alignment",
                                   "model_route", "gate", "latency_s", "runtime_s")}
        row["summary"] = rep["summary"]
        frames = rep_path.parent / "frames.json"
        if frames.is_file():
            fr = json.loads(frames.read_text(encoding="utf-8"))["frames"]
            row["frames"] = {"n": len(fr), "fields_unknown": sum(len(f.get("unknown_fields", [])) for f in fr),
                             "fields_total": sum(len(f.get("fields", {})) for f in fr),
                             "checks": {k: [f.get("checks", {}).get(k, {}).get("status") for f in fr]
                                        for k in ("chart_price", *LEVELS_FOR_FRAMES)},
                             "instruments": [f.get("instrument") for f in fr]}
        audit = evidence_root / "audit" / f"{rep['video_id']}-precision-audit.json"
        if audit.is_file():
            a = json.loads(audit.read_text(encoding="utf-8"))
            row["manual_precision_audit"] = {k: a.get(k) for k in ("decided_claims", "valid", "precision_estimate",
                                                                   "valid_verified", "valid_refuted")}
        videos.append(row)
    roles = {v["role"] for v in videos}
    complete = all(v["alignment"].get("status") == "ALIGNED" and v["gate"]["model_errors"] == 0 for v in videos)
    held_out = [v for v in videos if v["role"] == "held_out"]
    marker = ("VERIFIED" if "known" in roles and len(held_out) >= 2 and complete else "PARTIAL")
    return {"videos": videos, "suggested_marker_pipeline": marker}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("report")
    g.add_argument("evidence_root")
    r = sub.add_parser("run")
    r.add_argument("run_dir")
    r.add_argument("--role", choices=("known", "held_out"), required=True)
    r.add_argument("--model", default=TEXT_MODEL)
    r.add_argument("--max-windows", type=int, default=0)
    f = sub.add_parser("frames")
    f.add_argument("run_dir")
    f.add_argument("--max-frames", type=int, default=4)
    f.add_argument("--model", default=VISION_MODEL)
    f.add_argument("--recheck", action="store_true", help="recompute checks from stored readings, no model")
    args = ap.parse_args(argv)
    if args.cmd == "report":
        agg = aggregate(Path(args.evidence_root))
        out = Path(args.evidence_root) / "k1m6a-summary.json"
        out.write_text(json.dumps(agg, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"out": str(out), "videos": len(agg["videos"]),
                          "marker": agg["suggested_marker_pipeline"]}))
        return 0
    print(json.dumps({"priority": lower_priority()}), flush=True)
    if args.cmd == "run":
        rep = run_video(Path(args.run_dir), role=args.role, model=args.model, max_windows=args.max_windows)
        print(json.dumps({k: rep[k] for k in ("video_id", "role", "summary", "runtime_s")}, ensure_ascii=False))
    else:
        rep = (recheck_frames(Path(args.run_dir)) if args.recheck
               else run_frames(Path(args.run_dir), max_frames=args.max_frames, model=args.model))
        rep.setdefault("video_id", Path(args.run_dir).name)
        print(json.dumps({"video_id": rep["video_id"], "frames": len(rep["frames"])}))
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
