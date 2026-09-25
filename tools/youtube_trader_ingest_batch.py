#!/usr/bin/env python3
"""Discover and ingest public K1m6a YouTube videos for a bounded date window.

No cookies, auth bypass or private video access. Discovery uses yt-dlp metadata;
ingestion delegates to youtube_trader_ingest_auto.py. Teacher claims remain
UNVERIFIED until independent outcomes and verifiers promote them.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from datetime import date

def utf8_console() -> None:
    # Shipped runners start with `-I`, which ignores PYTHONUTF8/PYTHONIOENCODING:
    # without this the first Cyrillic line dies with cp1252 on Windows.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


utf8_console()



HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_SOURCE = "https://www.youtube.com/channel/UC2KGf4oWao2NMOnIA88ZwJQ/videos"


def _day(raw: str) -> date:
    return date.fromisoformat(raw)


def discover(source: str, start: str, end: str, limit: int = 200) -> list[dict]:
    if _day(start) > _day(end):
        raise ValueError("date_from is after date_to")
    cmd = [
        "yt-dlp", "--skip-download", "--dump-json", "--ignore-errors",
        "--dateafter", start.replace("-", ""), "--datebefore", end.replace("-", ""),
        "--playlist-end", str(limit), source,
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=1800, check=False,
                          encoding="utf-8", errors="replace")
    rows = []
    for line in proc.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = str(item.get("id") or "")
        up = str(item.get("upload_date") or "")
        if len(vid) != 11 or len(up) != 8:
            continue
        iso = f"{up[:4]}-{up[4:6]}-{up[6:]}"
        if start <= iso <= end:
            rows.append({
                "video_id": vid, "upload_date": iso, "title": item.get("title"),
                "channel": item.get("channel") or item.get("uploader"),
                "url": f"https://www.youtube.com/watch?v={vid}",
            })
    rows.sort(key=lambda x: (x["upload_date"], x["video_id"]))
    return rows


def _ingest_script() -> pathlib.Path:
    local = HERE / "youtube_trader_ingest_auto.py"
    return local if local.is_file() else ROOT / "tools" / "youtube_trader_ingest_auto.py"


def ingest(manifest: pathlib.Path, output_root: pathlib.Path, *, frame_interval: int = 30) -> dict:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    script = _ingest_script()
    if not script.is_file():
        raise RuntimeError(f"youtube ingest script not found: {script}")
    results = []
    for row in data.get("videos") or []:
        cmd = [sys.executable, str(script), row["url"], "--output-root", str(output_root),
               "--frame-interval", str(frame_interval)]
        proc = subprocess.run(cmd, cwd=ROOT if (ROOT / ".git").exists() else HERE,
                              text=True, capture_output=True, timeout=7200,
                              encoding="utf-8", errors="replace")
        results.append({"video_id": row["video_id"], "returncode": proc.returncode,
                        "stdout_tail": proc.stdout[-1000:], "stderr_tail": proc.stderr[-1000:]})
    return {"schema": "bossman.youtube-batch-result/1", "results": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--source-url", default=DEFAULT_SOURCE)
    d.add_argument("--from-date", default="2026-08-14")
    d.add_argument("--to-date", default="2026-08-27")
    d.add_argument("--limit", type=int, default=200)
    d.add_argument("--out", required=True)
    i = sub.add_parser("ingest")
    i.add_argument("--manifest", required=True)
    i.add_argument("--output-root", required=True)
    i.add_argument("--frame-interval", type=int, default=30)
    ns = ap.parse_args(argv)
    if ns.cmd == "discover":
        videos = discover(ns.source_url, ns.from_date, ns.to_date, ns.limit)
        payload = {"schema": "bossman.youtube-window/1", "source_url": ns.source_url,
                   "date_from": ns.from_date, "date_to": ns.to_date, "videos": videos}
        pathlib.Path(ns.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
        print(json.dumps({"videos": len(videos), "out": ns.out}, ensure_ascii=False))
        return 0 if videos else 3
    result = ingest(pathlib.Path(ns.manifest), pathlib.Path(ns.output_root),
                    frame_interval=ns.frame_interval)
    out = pathlib.Path(ns.output_root) / "batch-ingest-result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failed = sum(1 for row in result["results"] if row["returncode"] != 0)
    print(json.dumps({"videos": len(result["results"]), "failed": failed, "out": str(out)},
                     ensure_ascii=False))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
