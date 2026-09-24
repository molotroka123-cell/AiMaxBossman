#!/usr/bin/env python3
"""Discover and locally ingest K1m6a public YouTube trading videos by date.

Default owner window: 2026-08-14..2026-08-27 inclusive. Discovery uses yt-dlp
metadata only. Each selected URL is then passed to the existing
youtube_trader_ingest_auto.py, which prefers captions, falls back to LOCAL ASR,
samples frames and uses LOCAL vision. Raw teacher material stays UNVERIFIED.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import shutil
import subprocess
import sys
import time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CHANNEL = "https://www.youtube.com/@k1m6a/videos"
DEFAULT_START = "2026-08-14"
DEFAULT_END = "2026-08-27"


def day(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc


def _run(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=False)


def discover(channel: str, start: dt.date, end: dt.date, *, limit: int = 0) -> list[dict[str, Any]]:
    if start > end:
        raise ValueError("start date is after end date")
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        raise RuntimeError("yt-dlp not found on PATH")
    cmd = [
        ytdlp, "--skip-download", "--no-warnings", "--ignore-errors", "--dump-json",
        "--dateafter", start.strftime("%Y%m%d"), "--datebefore", end.strftime("%Y%m%d"),
    ]
    if limit:
        cmd += ["--playlist-end", str(limit)]
    cmd.append(channel)
    proc = _run(cmd, timeout=1800)
    if proc.returncode not in (0, 1) and not proc.stdout.strip():
        raise RuntimeError(f"yt-dlp discovery failed ({proc.returncode}): {proc.stderr[-500:]}")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in proc.stdout.splitlines():
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        vid = str(item.get("id") or "")
        upload = str(item.get("upload_date") or "")
        if not vid or vid in seen or len(upload) != 8:
            continue
        try:
            d = dt.datetime.strptime(upload, "%Y%m%d").date()
        except ValueError:
            continue
        if not (start <= d <= end):
            continue
        seen.add(vid)
        rows.append({
            "video_id": vid,
            "url": item.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
            "upload_date": d.isoformat(),
            "title": str(item.get("title") or "")[:500],
            "duration": item.get("duration"),
            "channel": str(item.get("channel") or item.get("uploader") or ""),
            "channel_id": str(item.get("channel_id") or ""),
            "availability": item.get("availability"),
            "learning_status": "UNVERIFIED",
        })
    rows.sort(key=lambda x: (x["upload_date"], x["video_id"]))
    return rows


def _load(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def ingest_one(row: dict, *, inbox: pathlib.Path, frame_interval: int, max_frames: int) -> dict:
    script = ROOT / "tools" / "youtube_trader_ingest_auto.py"
    cmd = [
        sys.executable, str(script), row["url"],
        "--output-root", str(inbox),
        "--frame-interval", str(frame_interval),
        "--max-frames", str(max_frames),
    ]
    started = time.time()
    proc = _run(cmd, timeout=4 * 3600)
    result: dict[str, Any] = {
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "returncode": proc.returncode,
        "seconds": round(time.time() - started, 2),
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }
    for line in reversed(proc.stdout.splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            result["result"] = parsed
            break
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default=DEFAULT_CHANNEL)
    ap.add_argument("--start", type=day, default=day(DEFAULT_START))
    ap.add_argument("--end", type=day, default=day(DEFAULT_END))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--discover-only", action="store_true")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--frame-interval", type=int, default=30)
    ap.add_argument("--max-frames", type=int, default=360)
    ap.add_argument("--root", default="")
    ns = ap.parse_args(argv)
    if ns.frame_interval < 5 or ns.max_frames < 1 or ns.limit < 0:
        ap.error("invalid frame/limit settings")

    root = pathlib.Path(ns.root) if ns.root else (
        ROOT / "data" / "trading" / "youtube_batches" /
        f"k1m6a-{ns.start.isoformat()}-{ns.end.isoformat()}")
    root.mkdir(parents=True, exist_ok=True)
    inbox = root / "youtube_inbox"
    manifest_path = root / "batch-manifest.json"
    previous = _load(manifest_path)
    prior_by_id = {x.get("video_id"): x for x in previous.get("videos", []) if isinstance(x, dict)}

    try:
        videos = discover(ns.channel, ns.start, ns.end, limit=ns.limit)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    out = {
        "schema": "bossman.youtube-batch/1",
        "source": {"channel": ns.channel, "start": ns.start.isoformat(), "end": ns.end.isoformat()},
        "trust": "PUBLIC_UNTRUSTED_TEACHER",
        "promotion": "QUARANTINE_ONLY",
        "weights_changed": False,
        "videos": [],
    }
    failures = 0
    for row in videos:
        prior = prior_by_id.get(row["video_id"]) or {}
        if ns.discover_only:
            row["ingest"] = prior.get("ingest") or {"status": "NOT_RUN"}
        elif not ns.redo and (prior.get("ingest") or {}).get("status") == "PASS":
            row["ingest"] = prior["ingest"]
            row["ingest"]["reused"] = True
        else:
            row["ingest"] = ingest_one(row, inbox=inbox, frame_interval=ns.frame_interval,
                                       max_frames=ns.max_frames)
        failures += int((row["ingest"] or {}).get("status") == "FAIL")
        out["videos"].append(row)
        manifest_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    out["summary"] = {
        "discovered": len(videos),
        "ingested_pass": sum((v.get("ingest") or {}).get("status") == "PASS" for v in videos),
        "failed": failures,
        "not_run": sum((v.get("ingest") or {}).get("status") == "NOT_RUN" for v in videos),
    }
    manifest_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": failures == 0, "manifest": str(manifest_path), **out["summary"]},
                     ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
