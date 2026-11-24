"""Source signals for video learning, ported as IDEAS from Stream Tube (native Rust YouTube client, 2026-11).

Stream Tube ships SponsorBlock and Return YouTube Dislike (RYD) for a viewer. For learning they mean two things:

* SponsorBlock -> clean transcripts. Sponsor reads, self-promo, "like and subscribe", intros/outros are not
  knowledge; they are removed from the cues BEFORE chunking, so they never become claims.
* RYD -> a source prior. Likes vs (estimated) dislikes give a Wilson lower bound used to rank which videos to
  learn from first. It is advisory only: it never rejects a transcript and never promotes a claim.

Privacy: SponsorBlock is queried by a 4-char SHA-256 prefix of the video id (k-anonymity, the API's own
recommendation), so the exact video is not disclosed. RYD needs the exact id (public metadata, no personal data).
Network is injected (`http_get(url, timeout) -> (status, body_bytes)`); nothing here raises into the pipeline:
every outcome is a status (OK / NONE / NOT_FOUND / RATE_LIMITED / UNAVAILABLE / BAD_RESPONSE / INSUFFICIENT_EVIDENCE).
No code from Stream Tube is copied; only the public APIs are called.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
SPONSORBLOCK_BASE = "https://sponsor.ajay.app"
RYD_BASE = "https://returnyoutubedislikeapi.com"

# Not knowledge. "filler" (tangents/jokes) is opt-in: in lessons a tangent can still carry a fact.
DEFAULT_STRIP_CATEGORIES = ("sponsor", "selfpromo", "interaction", "intro", "outro", "preview", "music_offtopic")
ALL_KNOWN_CATEGORIES = DEFAULT_STRIP_CATEGORIES + ("filler", "exclusive_access", "poi_highlight", "chapter")


def _check_id(video_id: str) -> str:
    if not isinstance(video_id, str) or not VIDEO_ID_RE.match(video_id):
        raise ValueError("invalid YouTube video id")
    return video_id


def default_http_get(url: str, timeout: float = 8.0):
    """Plain HTTPS GET. Returns (status, body). Network failure -> (0, b'')."""
    req = urllib.request.Request(url, headers={"User-Agent": "Bossman-learning/1 (+local)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https hosts above
            return resp.status, resp.read(2_000_000)
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except (urllib.error.URLError, OSError, TimeoutError):
        return 0, b""


def _status_for_http(code: int) -> str:
    if code == 404:
        return "NOT_FOUND"
    if code == 429:
        return "RATE_LIMITED"
    return "UNAVAILABLE"


# ---------------------------------------------------------------- SponsorBlock

@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    category: str
    votes: int = 0
    locked: bool = False


def fetch_sponsor_segments(video_id: str, *, http_get=default_http_get, categories=DEFAULT_STRIP_CATEGORIES,
                           base: str = SPONSORBLOCK_BASE) -> dict:
    """k-anonymous lookup. Returns {"status", "segments": [Segment], "prefix"}.
    NONE = the API answered and this video has no segments (404 on the prefix endpoint is the API's 'nothing')."""
    vid = _check_id(video_id)
    cats = [c for c in categories if c in ALL_KNOWN_CATEGORIES]
    prefix = hashlib.sha256(vid.encode("ascii")).hexdigest()[:4]
    url = "%s/api/skipSegments/%s?categories=%s&actionTypes=%s" % (
        base, prefix, urllib.request.quote(json.dumps(cats, separators=(",", ":"))),
        urllib.request.quote('["skip"]'))
    code, body = http_get(url, 8.0)
    out = {"status": "UNAVAILABLE", "segments": [], "prefix": prefix}
    if code == 404:
        out["status"] = "NONE"
        return out
    if code != 200:
        out["status"] = _status_for_http(code)
        return out
    try:
        rows = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        out["status"] = "BAD_RESPONSE"
        return out
    if not isinstance(rows, list):
        out["status"] = "BAD_RESPONSE"
        return out
    segs = []
    for row in rows:
        if not isinstance(row, dict) or row.get("videoID") != vid:  # other videos sharing the prefix
            continue
        for s in row.get("segments") or []:
            try:
                a, b = (float(x) for x in s["segment"])
                cat = str(s["category"])
            except (KeyError, TypeError, ValueError):
                continue
            if s.get("actionType", "skip") != "skip" or cat not in cats or not (0 <= a < b):
                continue
            segs.append(Segment(a, b, cat, int(s.get("votes") or 0), bool(s.get("locked"))))
    out["segments"] = sorted(segs, key=lambda x: x.start)
    out["status"] = "OK" if segs else "NONE"
    return out


def strip_segments(cues: list, segments: list, *, min_votes: int = 0, overlap_drop: float = 0.5,
                   max_removed_share: float = 0.6) -> dict:
    """Drop cues that are mostly inside a trusted skip segment.

    Trust: a segment counts when it is locked by a VIP or has votes >= min_votes (downvoted junk is ignored).
    Guard: if stripping would remove more than max_removed_share of the spoken time the segments are treated as
    suspicious and NOTHING is removed (status SUSPICIOUS) -- a troll submission must not delete a lesson.
    """
    trusted = [s for s in segments if s.locked or s.votes >= min_votes]
    total = sum(max(0.0, c.end - c.start) for c in cues)
    kept, removed, removed_s = [], [], 0.0
    for c in cues:
        dur = max(1e-6, c.end - c.start)
        inside = sum(max(0.0, min(c.end, s.end) - max(c.start, s.start)) for s in trusted)
        if inside / dur >= overlap_drop:
            removed.append(c)
            removed_s += c.end - c.start
        else:
            kept.append(c)
    share = removed_s / total if total > 0 else 0.0
    if share > max_removed_share:
        return {"status": "SUSPICIOUS", "cues": list(cues), "removed_cues": 0, "removed_s": 0.0,
                "would_remove_share": round(share, 3), "trusted_segments": len(trusted)}
    return {"status": "STRIPPED" if removed else "UNCHANGED", "cues": kept, "removed_cues": len(removed),
            "removed_s": round(removed_s, 2), "removed_share": round(share, 3), "trusted_segments": len(trusted)}


# ---------------------------------------------------------------- Return YouTube Dislike

def fetch_votes(video_id: str, *, http_get=default_http_get, base: str = RYD_BASE) -> dict:
    """RYD votes. Dislikes are ESTIMATES (extrapolated from extension users), labelled as such."""
    vid = _check_id(video_id)
    code, body = http_get("%s/votes?videoId=%s" % (base, vid), 8.0)
    if code != 200:
        return {"status": _status_for_http(code)}
    try:
        d = json.loads(body.decode("utf-8"))
        likes, dislikes = int(d["likes"]), int(d["dislikes"])
        views = int(d.get("viewCount") or 0)
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return {"status": "BAD_RESPONSE"}
    if likes < 0 or dislikes < 0 or d.get("id") not in (None, vid):
        return {"status": "BAD_RESPONSE"}
    return {"status": "OK", "likes": likes, "dislikes": dislikes, "views": views, "deleted": bool(d.get("deleted")),
            "source": "ryd_estimate"}


def wilson_lower(pos: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = pos / total
    den = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - margin) / den)


def source_prior(votes: dict, *, min_votes: int = 50) -> dict:
    """Advisory ranking prior 0..1. Too few votes or no data -> INSUFFICIENT_EVIDENCE, never a fake 0 or 0.5."""
    if not votes or votes.get("status") != "OK":
        return {"status": "INSUFFICIENT_EVIDENCE", "prior": None, "reason": (votes or {}).get("status", "NO_DATA")}
    if votes.get("deleted"):
        return {"status": "INSUFFICIENT_EVIDENCE", "prior": None, "reason": "DELETED"}
    total = votes["likes"] + votes["dislikes"]
    if total < min_votes:
        return {"status": "INSUFFICIENT_EVIDENCE", "prior": None, "reason": "FEW_VOTES", "votes": total}
    return {"status": "OK", "prior": round(wilson_lower(votes["likes"], total), 4), "votes": total,
            "basis": "wilson_lb95(likes, likes+ryd_estimated_dislikes)"}


def rank_sources(candidates: list) -> list:
    """candidates: [{"video_id", "prior": source_prior(...)}]. Known priors first (desc), unknown after, stable."""
    known = [c for c in candidates if (c.get("prior") or {}).get("status") == "OK"]
    unknown = [c for c in candidates if c not in known]
    return sorted(known, key=lambda c: -c["prior"]["prior"]) + unknown
