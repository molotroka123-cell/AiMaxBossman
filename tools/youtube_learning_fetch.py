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

from bcc.learning_video.fetch_control import Breaker, FetchController  # noqa: E402
from bcc.learning_video.ingest import BREAKER_FILE, run, video_id_from_url  # noqa: E402,F401  (run: re-export)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--lang", default="ru", choices=("ru", "en"))
    ap.add_argument("--no-signals", action="store_true", help="skip SponsorBlock/RYD (fully offline except yt-dlp)")
    args = ap.parse_args(argv)
    try:
        for u in args.urls:
            video_id_from_url(u)  # refuse non-YouTube input before touching any binary or network
    except ValueError as exc:
        print(json.dumps({"verdict": "BLOCKED", "reason": str(exc)}))
        return 3
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        print(json.dumps({"verdict": "BLOCKED", "reason": "yt-dlp not found"}))
        return 3
    out = Path(args.out)
    ctrl = FetchController(ytdlp=ytdlp, breaker=Breaker(path=out / BREAKER_FILE),
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
