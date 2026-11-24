#!/usr/bin/env python3
"""YouTube -> Bossman learning, through the fetch control plane (ideas from Stream Tube, 2026-11).

    python tools/youtube_learning_fetch.py URL [URL ...] --out D:/Bossman/learning/yt [--lang ru] [--no-signals]

Per URL: SponsorBlock segments + RYD prior (unless --no-signals) -> FetchController (player-client fallback, breaker,
optional BOSSMAN_YTDLP_SHA256 pin) -> run_pipeline(skip_segments=..., prior=...) with the rule extractor.
With several URLs, videos are processed in source-prior order (best-rated first, unknown last).
Prints one JSON report per run; exit 0 if at least one video produced a transcript, 2 if none, 3 on usage error.
Claims stay UNVERIFIED. No cookies, no sign-in, no download of the video itself.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "command-center"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from bcc.learning_video import run_pipeline  # noqa: E402
from bcc.learning_video.fetch_control import Breaker, FetchController  # noqa: E402
from bcc.learning_video.stream_signals import (  # noqa: E402
    fetch_sponsor_segments, fetch_votes, rank_sources, source_prior,
)
from tools.youtube_trader_ingest import extract_video_id  # noqa: E402  (one URL validator, not a second one)


def _pick_vtt(files: list, lang: str):
    rows = sorted(files)
    for f in rows:
        if (".%s." % lang) in Path(f).name:
            return f
    return rows[0] if rows else None


def run(urls: list, out: Path, *, lang: str, signals: bool, controller: FetchController,
        http_get=None) -> dict:
    kw = {"http_get": http_get} if http_get else {}
    items = []
    for url in urls:
        vid = extract_video_id(url)
        item = {"url": url, "video_id": vid}
        if signals:
            item["sponsor"] = fetch_sponsor_segments(vid, **kw)
            item["prior"] = source_prior(fetch_votes(vid, **kw))
        items.append(item)
    if signals:
        items = rank_sources(items)
    results = []
    for item in items:
        wd = out / item["video_id"]
        fetch = controller.fetch_subtitles(item["url"], wd)
        row = {"video_id": item["video_id"], "fetch": fetch, "prior": item.get("prior"),
               "sponsor_status": (item.get("sponsor") or {}).get("status", "NOT_REQUESTED")}
        vtt = _pick_vtt(fetch["files"], lang) if fetch["verdict"] == "OK" else None
        if vtt:
            text = Path(vtt).read_text(encoding="utf-8", errors="replace")
            # yt-dlp names manual and automatic captions the same way: we cannot prove "manual", so trust stays low.
            kind = "auto_subs"
            rep = run_pipeline(text, url=item["url"], kind=kind, lang=lang, store_path=out / "claims.jsonl",
                               skip_segments=(item.get("sponsor") or {}).get("segments"), prior=item.get("prior"))
            rep["claims"] = len(rep["claims"])
            rep["rejects"] = len(rep["rejects"])
            row["pipeline"] = rep
        results.append(row)
    ok = sum(1 for r in results if "pipeline" in r)
    return {"videos": len(results), "with_transcript": ok, "results": results,
            "verdict": "PARTIAL" if 0 < ok < len(results) else ("OK" if ok else "NO_TRANSCRIPTS")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--lang", default="ru", choices=("ru", "en"))
    ap.add_argument("--no-signals", action="store_true", help="skip SponsorBlock/RYD (fully offline except yt-dlp)")
    args = ap.parse_args(argv)
    try:
        for u in args.urls:
            extract_video_id(u)  # refuse non-YouTube input before touching any binary or network
    except ValueError as exc:
        print(json.dumps({"verdict": "BLOCKED", "reason": str(exc)}))
        return 3
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        print(json.dumps({"verdict": "BLOCKED", "reason": "yt-dlp not found"}))
        return 3
    out = Path(args.out)
    ctrl = FetchController(ytdlp=ytdlp, breaker=Breaker(path=out / "fetch_breaker.json"),
                           expected_sha256=os.getenv("BOSSMAN_YTDLP_SHA256"))
    try:
        rep = run(args.urls, out, lang=args.lang, signals=not args.no_signals, controller=ctrl)
    except ValueError as exc:
        print(json.dumps({"verdict": "BLOCKED", "reason": str(exc)}))
        return 3
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    return 0 if rep["with_transcript"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
