#!/usr/bin/env python3
"""Bossman YouTube -> Trader Apprentice ingestion pipeline.

Input: one public YouTube URL.
Output: auditable UNVERIFIED candidate episodes built from transcript + sampled
video frames + local multimodal model extraction + deterministic Trader
Apprentice classification.

The tool deliberately does NOT auto-promote teacher claims to verified trading
knowledge and never places orders. It only uses ordinary public YouTube access;
no DRM/auth bypass or cookie theft is attempted.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from learning.trader_apprentice import LevelMap, Snapshot, analyze  # noqa: E402

YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"
}
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
TS_RE = re.compile(r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})[.,](?P<ms>\d{3})")
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")

DEFAULT_API_BASE = os.getenv("BOSSMAN_LOCAL_API_BASE", "http://127.0.0.1:8000/v1").rstrip("/")
DEFAULT_VISION_MODEL = os.getenv("BOSSMAN_LOCAL_VLM_MODEL", "").strip()

FRAME_SYSTEM = """You extract factual trading-screen observations from one video frame.
Return JSON only. Never infer a number that is unreadable. Use null for unknown.
Do not give hidden reasoning. Separate chart/reference price from execution tape
price if both exist. CVD and OI are observation values, not directional labels.
A teacher/transcript statement is a claim, not ground truth."""

FRAME_USER_TEMPLATE = """Video timestamp: {timestamp_seconds:.1f}s
Nearby transcript (may be empty or inaccurate):
{transcript}

Extract this exact JSON object:
{{
  "chart_price": number|null,
  "execution_price": number|null,
  "source_label": string|null,
  "instrument": string|null,
  "cvd": number|null,
  "open_interest": number|null,
  "long_liquidations": number|null,
  "short_liquidations": number|null,
  "buy_volume": number|null,
  "sell_volume": number|null,
  "levels": {{
    "dVAL": number|null, "dPOC": number|null, "dVAH": number|null,
    "dOpen": number|null, "pDayLow": number|null, "pDayHigh": number|null,
    "weekOpen": number|null, "monthOpen": number|null, "weekEq": number|null,
    "yearEq": number|null, "settlementD": number|null,
    "settlementW": number|null, "ntPOC": number|null
  }},
  "teacher_claim": string|null,
  "teacher_trigger": string|null,
  "teacher_invalidation": string|null,
  "vision_confidence": number,
  "notes": string|null
}}

Rules: vision_confidence is 0..1. Keep chart and execution prices separate.
If transcript disagrees with the visible chart, preserve visible values and put
that disagreement in notes. Do not guess obscured digits."""


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def extract_video_id(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        raise ValueError("only public youtube.com / youtu.be URLs are accepted")
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    else:
        qs = urllib.parse.parse_qs(parsed.query)
        video_id = (qs.get("v") or [""])[0]
        if not video_id and parsed.path.startswith("/shorts/"):
            video_id = parsed.path.split("/")[2]
        if not video_id and parsed.path.startswith("/live/"):
            video_id = parsed.path.split("/")[2]
    if not VIDEO_ID_RE.match(video_id):
        raise ValueError("could not extract a valid 11-character YouTube video id")
    return video_id


def _run(argv: list[str], *, timeout: int = 900, cwd: Optional[Path] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=True,
    )


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"required binary not found: {name}")
    return path


def yt_metadata(url: str) -> dict[str, Any]:
    ytdlp = require_binary("yt-dlp")
    proc = _run([ytdlp, "--no-playlist", "--dump-single-json", "--skip-download", url], timeout=120)
    return json.loads(proc.stdout)


def download_subtitles(url: str, workdir: Path) -> list[Path]:
    ytdlp = require_binary("yt-dlp")
    template = str(workdir / "subs.%(id)s.%(language)s.%(ext)s")
    cmd = [
        ytdlp, "--no-playlist", "--skip-download", "--write-subs", "--write-auto-subs",
        "--sub-langs", "en.*,ru.*,en,ru", "--sub-format", "vtt", "-o", template, url,
    ]
    try:
        _run(cmd, timeout=180)
    except subprocess.CalledProcessError:
        return []
    return sorted(workdir.glob("subs.*.vtt"))


def _vtt_seconds(raw: str) -> float:
    match = TS_RE.search(raw.strip())
    if not match:
        raise ValueError(f"bad VTT timestamp: {raw!r}")
    return (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + int(match.group("s"))
        + int(match.group("ms")) / 1000.0
    )


def parse_vtt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        left, right = lines[timing_index].split("-->", 1)
        try:
            start = _vtt_seconds(left)
            end = _vtt_seconds(right)
        except ValueError:
            continue
        body = SPACE_RE.sub(" ", TAG_RE.sub("", " ".join(lines[timing_index + 1 :]))).strip()
        if not body:
            continue
        if cues and body == cues[-1].text and start <= cues[-1].end + 0.5:
            prev = cues[-1]
            cues[-1] = Cue(prev.start, max(prev.end, end), prev.text)
        else:
            cues.append(Cue(start, end, body))
    return cues


def choose_subtitle(paths: Iterable[Path]) -> Optional[Path]:
    rows = list(paths)
    if not rows:
        return None
    def rank(path: Path) -> tuple[int, str]:
        name = path.name.lower()
        if ".en" in name:
            return (0, name)
        if ".ru" in name:
            return (1, name)
        return (2, name)
    return sorted(rows, key=rank)[0]


def transcript_near(cues: list[Cue], timestamp: float, radius: float = 35.0, max_chars: int = 3500) -> str:
    selected = [c.text for c in cues if c.end >= timestamp - radius and c.start <= timestamp + radius]
    return SPACE_RE.sub(" ", " ".join(selected)).strip()[:max_chars]


def download_video(url: str, workdir: Path, *, max_filesize_mb: int = 750) -> Path:
    ytdlp = require_binary("yt-dlp")
    template = str(workdir / "video.%(ext)s")
    cmd = [
        ytdlp, "--no-playlist", "--max-filesize", f"{max_filesize_mb}M",
        "-f", "b[height<=720]/best[height<=720]/best", "-o", template, url,
    ]
    _run(cmd, timeout=1800)
    candidates = sorted(workdir.glob("video.*"))
    if not candidates:
        raise RuntimeError("yt-dlp finished without a video file")
    return max(candidates, key=lambda p: p.stat().st_size)


def extract_frames(video: Path, frames_dir: Path, *, interval_seconds: int = 30, max_frames: int = 360) -> list[Path]:
    ffmpeg = require_binary("ffmpeg")
    frames_dir.mkdir(parents=True, exist_ok=True)
    output = frames_dir / "frame_%06d.jpg"
    vf = f"fps=1/{interval_seconds},scale=min(1280\\,iw):-2"
    _run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(video),
        "-vf", vf, "-frames:v", str(max_frames), "-q:v", "3", str(output),
    ], timeout=1800)
    return sorted(frames_dir.glob("frame_*.jpg"))


def _api_json(method: str, url: str, payload: Optional[dict] = None, *, timeout: int = 120) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    token = os.getenv("BOSSMAN_LOCAL_API_KEY", "").strip()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def discover_model(api_base: str, explicit: str = "") -> str:
    if explicit:
        return explicit
    try:
        data = _api_json("GET", f"{api_base.rstrip('/')}/models", timeout=15)
        rows = data.get("data") or []
        if rows and rows[0].get("id"):
            return str(rows[0]["id"])
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("local model is not reachable and no BOSSMAN_LOCAL_VLM_MODEL was set") from exc
    raise RuntimeError("local model endpoint returned no model ids")


def _extract_json_text(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        left, right = raw.find("{"), raw.rfind("}")
        if left >= 0 and right > left:
            return json.loads(raw[left : right + 1])
        raise


def vision_extract(frame: Path, timestamp_seconds: float, transcript: str, *, api_base: str, model: str) -> dict[str, Any]:
    image_b64 = base64.b64encode(frame.read_bytes()).decode("ascii")
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": FRAME_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": FRAME_USER_TEMPLATE.format(timestamp_seconds=timestamp_seconds, transcript=transcript)},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ]},
        ],
    }
    data = _api_json("POST", f"{api_base.rstrip('/')}/chat/completions", payload, timeout=180)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("local VLM returned no choices")
    content = choices[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        content = "\n".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in content)
    return _extract_json_text(str(content))


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("$", "")
        scale = 1.0
        if cleaned.lower().endswith("b"):
            scale, cleaned = 1_000_000_000.0, cleaned[:-1]
        elif cleaned.lower().endswith("m"):
            scale, cleaned = 1_000_000.0, cleaned[:-1]
        elif cleaned.lower().endswith("k"):
            scale, cleaned = 1_000.0, cleaned[:-1]
        try:
            return float(cleaned) * scale
        except ValueError:
            return None
    return None


def _level_map(raw: dict[str, Any]) -> LevelMap:
    levels = raw.get("levels") if isinstance(raw.get("levels"), dict) else {}
    known = {
        "dval": _number(levels.get("dVAL")), "dpoc": _number(levels.get("dPOC")),
        "dvah": _number(levels.get("dVAH")), "dopen": _number(levels.get("dOpen")),
        "pday_low": _number(levels.get("pDayLow")), "pday_high": _number(levels.get("pDayHigh")),
        "week_open": _number(levels.get("weekOpen")), "month_open": _number(levels.get("monthOpen")),
        "week_eq": _number(levels.get("weekEq")), "year_eq": _number(levels.get("yearEq")),
        "settlement_d": _number(levels.get("settlementD")), "settlement_w": _number(levels.get("settlementW")),
    }
    extra = {}
    ntpoc = _number(levels.get("ntPOC"))
    if ntpoc is not None:
        extra["ntPOC"] = ntpoc
    return LevelMap(**known, extra=extra)


def observation_to_snapshot(obs: dict[str, Any], timestamp: float, video_id: str) -> Optional[Snapshot]:
    price = _number(obs.get("chart_price"))
    if price is None:
        return None
    return Snapshot(
        price=price,
        cvd=_number(obs.get("cvd")),
        open_interest=_number(obs.get("open_interest")),
        long_liquidations=_number(obs.get("long_liquidations")),
        short_liquidations=_number(obs.get("short_liquidations")),
        buy_volume=_number(obs.get("buy_volume")),
        sell_volume=_number(obs.get("sell_volume")),
        source=f"youtube:{video_id}",
        instrument=str(obs.get("instrument") or "unknown"),
        timestamp=f"video+{timestamp:.1f}s",
    )


def _future_price(rows: list[dict[str, Any]], index: int, horizon_seconds: float) -> Optional[tuple[float, float]]:
    start_t = float(rows[index]["timestamp_seconds"])
    for row in rows[index + 1 :]:
        if float(row["timestamp_seconds"]) - start_t >= horizon_seconds:
            p = _number(row.get("observation", {}).get("chart_price"))
            if p is not None:
                return float(row["timestamp_seconds"]), p
    return None


def attach_future_outcomes(rows: list[dict[str, Any]], horizons: Iterable[int] = (300, 900, 3600)) -> None:
    for i, row in enumerate(rows):
        start = _number(row.get("observation", {}).get("chart_price"))
        outcomes: dict[str, Any] = {}
        if start is None or start <= 0:
            row["future_outcomes"] = outcomes
            continue
        for horizon in horizons:
            future = _future_price(rows, i, horizon)
            if future is None:
                continue
            ts, price = future
            outcomes[f"{horizon}s"] = {
                "timestamp_seconds": ts,
                "chart_price": price,
                "return_pct": round((price - start) / start * 100.0, 4),
            }
        row["future_outcomes"] = outcomes


def build_cases(observations: list[dict[str, Any]], video_id: str, title: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    previous_snapshot: Optional[Snapshot] = None
    for row in observations:
        obs = row.get("observation") or {}
        ts = float(row["timestamp_seconds"])
        snapshot = observation_to_snapshot(obs, ts, video_id)
        deterministic = None
        if snapshot is not None and previous_snapshot is not None:
            deterministic = analyze(previous_snapshot, snapshot, _level_map(obs)).to_dict()
        if snapshot is not None:
            previous_snapshot = snapshot
        cases.append({
            "case_id": f"yt-{video_id}-{int(ts):06d}",
            "learning_status": "UNVERIFIED",
            "source": "youtube",
            "video_id": video_id,
            "video_title": title,
            "timestamp_seconds": ts,
            "frame": row.get("frame"),
            "transcript_excerpt": row.get("transcript_excerpt", ""),
            "observation": obs,
            "deterministic_analysis": deterministic,
            "teacher_claim_status": "UNVERIFIED",
            "future_outcomes": {},
        })
    attach_future_outcomes(cases)
    return cases


def ingest(
    url: str,
    *,
    output_root: Path,
    api_base: str = DEFAULT_API_BASE,
    model: str = DEFAULT_VISION_MODEL,
    frame_interval: int = 30,
    max_frames: int = 360,
    max_duration_seconds: int = 4 * 3600,
) -> dict[str, Any]:
    video_id = extract_video_id(url)
    metadata = yt_metadata(url)
    duration = int(metadata.get("duration") or 0)
    if duration and duration > max_duration_seconds:
        raise RuntimeError(f"video duration {duration}s exceeds limit {max_duration_seconds}s")

    resolved_model = discover_model(api_base, model)
    run_dir = output_root / video_id
    run_dir.mkdir(parents=True, exist_ok=True)

    subtitle_paths = download_subtitles(url, run_dir)
    subtitle_path = choose_subtitle(subtitle_paths)
    cues: list[Cue] = []
    if subtitle_path:
        cues = parse_vtt(subtitle_path.read_text(encoding="utf-8", errors="replace"))

    video_path = download_video(url, run_dir)
    frames = extract_frames(video_path, run_dir / "frames", interval_seconds=frame_interval, max_frames=max_frames)

    observations: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for idx, frame in enumerate(frames):
        timestamp = float(idx * frame_interval)
        transcript = transcript_near(cues, timestamp)
        try:
            obs = vision_extract(frame, timestamp, transcript, api_base=api_base, model=resolved_model)
        except Exception as exc:  # noqa: BLE001
            failures.append({"timestamp_seconds": timestamp, "frame": str(frame), "error": type(exc).__name__})
            continue
        observations.append({
            "timestamp_seconds": timestamp,
            "frame": str(frame.relative_to(run_dir)),
            "transcript_excerpt": transcript,
            "observation": obs,
        })

    cases = build_cases(observations, video_id, str(metadata.get("title") or ""))
    cases_path = run_dir / "candidate_cases.jsonl"
    cases_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in cases) + ("\n" if cases else ""), encoding="utf-8")

    manifest = {
        "version": "1.0.0",
        "status": "UNVERIFIED_INGEST",
        "url": url,
        "video_id": video_id,
        "title": metadata.get("title"),
        "channel": metadata.get("channel") or metadata.get("uploader"),
        "duration_seconds": duration,
        "local_model": resolved_model,
        "api_base": api_base,
        "frame_interval_seconds": frame_interval,
        "frames_extracted": len(frames),
        "observations_parsed": len(observations),
        "parse_failures": failures,
        "subtitle_file": str(subtitle_path.relative_to(run_dir)) if subtitle_path else None,
        "candidate_cases": str(cases_path.relative_to(run_dir)),
        "promotion_policy": "Teacher claims remain UNVERIFIED until independent outcome/verification gates promote them.",
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest one public YouTube trading video into Trader Apprentice candidates")
    parser.add_argument("url", help="public youtube.com / youtu.be URL")
    parser.add_argument("--output-root", default=str(ROOT / "data" / "trading" / "youtube_inbox"))
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="OpenAI-compatible local /v1 API base")
    parser.add_argument("--model", default=DEFAULT_VISION_MODEL, help="local multimodal model id; auto-discovered when omitted")
    parser.add_argument("--frame-interval", type=int, default=30, help="seconds between sampled frames")
    parser.add_argument("--max-frames", type=int, default=360)
    parser.add_argument("--max-duration", type=int, default=4 * 3600, help="maximum accepted video duration in seconds")
    args = parser.parse_args(argv)
    if args.frame_interval < 5:
        parser.error("--frame-interval must be >= 5 seconds")
    if args.max_frames < 1:
        parser.error("--max-frames must be >= 1")
    try:
        manifest = ingest(
            args.url,
            output_root=Path(args.output_root),
            api_base=args.api_base,
            model=args.model,
            frame_interval=args.frame_interval,
            max_frames=args.max_frames,
            max_duration_seconds=args.max_duration,
        )
    except (ValueError, RuntimeError, subprocess.SubprocessError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "manifest": manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
