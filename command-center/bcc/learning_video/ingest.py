"""Product-side YouTube learning ingest: rank by source prior -> FetchController -> SponsorBlock strip -> claims.

One orchestration used by both surfaces of the same system: the agent tool `learning.youtube`
(bcc.features.tools_learning_video, pult/chat path) and the CLI `tools/youtube_learning_fetch.py`.
Network and the yt-dlp runner are injected through the FetchController / http_get; claims stay UNVERIFIED.
"""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

from .fetch_control import FetchController
from .pipeline import run_pipeline
from .stream_signals import VIDEO_ID_RE, fetch_sponsor_segments, fetch_votes, rank_sources, source_prior

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
MAX_URLS = 10
CLAIMS_FILE = "claims.jsonl"
BREAKER_FILE = "fetch_breaker.json"


def video_id_from_url(url: str) -> str:
    """Only public youtube.com / youtu.be watch, shorts, live, embed URLs. Anything else -> ValueError."""
    p = urllib.parse.urlparse(str(url or "").strip())
    host = (p.hostname or "").lower()
    if p.scheme not in ("https", "http") or host not in YOUTUBE_HOSTS:
        raise ValueError("only public youtube.com / youtu.be URLs are accepted")
    if host == "youtu.be":
        vid = p.path.strip("/").split("/")[0]
    else:
        parts = [x for x in p.path.split("/") if x]
        if parts[:1] == ["watch"]:
            vid = (urllib.parse.parse_qs(p.query).get("v") or [""])[0]
        elif len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
            vid = parts[1]
        else:
            vid = ""
    if not VIDEO_ID_RE.match(vid or ""):
        raise ValueError("could not extract a valid 11-character YouTube video id")
    return vid


def _pick_vtt(files: list, lang: str):
    rows = sorted(files)
    for f in rows:
        if (".%s." % lang) in Path(f).name:
            return f
    return rows[0] if rows else None


def run(urls: list, out: Path, *, lang: str, signals: bool, controller: FetchController, http_get=None) -> dict:
    if not urls or len(urls) > MAX_URLS:
        raise ValueError("1..%d URLs per run" % MAX_URLS)
    out = Path(out)
    kw = {"http_get": http_get} if http_get else {}
    items, seen = [], set()
    for url in urls:
        vid = video_id_from_url(url)
        if vid in seen:
            continue
        seen.add(vid)
        item = {"url": "https://www.youtube.com/watch?v=" + vid, "video_id": vid}  # canonical: no tracking params
        if signals:
            item["sponsor"] = fetch_sponsor_segments(vid, **kw)
            item["prior"] = source_prior(fetch_votes(vid, **kw))
        items.append(item)
    if signals:
        items = rank_sources(items)
    results = []
    for item in items:
        if controller.breaker.is_open():
            results.append({"video_id": item["video_id"], "fetch": {"verdict": "BLOCKED_BREAKER_OPEN", "files": [],
                                                                    "attempts": []}, "prior": item.get("prior")})
            continue
        fetch = controller.fetch_subtitles(item["url"], out / item["video_id"])
        row = {"video_id": item["video_id"], "fetch": fetch, "prior": item.get("prior"),
               "sponsor_status": (item.get("sponsor") or {}).get("status", "NOT_REQUESTED")}
        vtt = _pick_vtt(fetch["files"], lang) if fetch["verdict"] == "OK" else None
        if vtt:
            text = Path(vtt).read_text(encoding="utf-8", errors="replace")
            # yt-dlp names manual and automatic captions alike: "manual" cannot be proven, so trust stays low.
            rep = run_pipeline(text, url=item["url"], kind="auto_subs", lang=lang, store_path=out / CLAIMS_FILE,
                               skip_segments=(item.get("sponsor") or {}).get("segments"), prior=item.get("prior"))
            rep["claims"] = len(rep["claims"])
            rep["rejects"] = len(rep["rejects"])
            row["pipeline"] = rep
        results.append(row)
    ok = sum(1 for r in results if "pipeline" in r)
    return {"videos": len(results), "with_transcript": ok, "results": results,
            "verdict": "PARTIAL" if 0 < ok < len(results) else ("OK" if ok else "NO_TRANSCRIPTS")}


def store_status(out: Path, *, clock=None) -> dict:
    """Read-only: breaker state and claim counts per source. No transcript text."""
    import time
    out = Path(out)
    now = (clock or time.time)()
    breaker = {"strikes": 0, "open": False, "open_for_s": 0}
    bp = out / BREAKER_FILE
    if bp.exists():
        try:
            d = json.loads(bp.read_text(encoding="utf-8"))
            left = float(d.get("open_until", 0)) - now
            breaker = {"strikes": int(d.get("strikes", 0)), "open": left > 0, "open_for_s": max(0, int(left))}
        except (ValueError, OSError):
            breaker = {"status": "CORRUPT"}
    per, total, bad = {}, 0, 0
    cp = out / CLAIMS_FILE
    if cp.exists():
        for ln in cp.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                c = json.loads(ln)
                sid = str(c["source_id"]).rsplit("-", 1)[0]  # video ids may contain "-"
            except (ValueError, KeyError, TypeError):
                bad += 1
                continue
            per[sid] = per.get(sid, 0) + 1
            total += 1
    return {"breaker": breaker, "claims_total": total, "claims_by_video": per, "corrupt_lines": bad,
            "status": "UNVERIFIED claims only"}
