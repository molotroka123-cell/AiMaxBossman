#!/usr/bin/env python3
"""Select chart screenshots at meaningful transcript moments, not fixed intervals.

Uses local captions/claims/lesson candidates as evidence anchors and optionally
adds nearby FFmpeg scene cuts. Scene cuts alone never receive a screenshot slot.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

STRONG = re.compile(
    r"\b(entry|enter|entered|trigger|setup|stop loss|invalidation|break even|\bbe\b|"
    r"take profit|exit|scale out|retest|reclaim|sweep|failed auction|single print|"
    r"absorption|delta|cvd|open interest|\boi\b|value area|\bvah\b|\bval\b|"
    r"point of control|\bpoc\b|liquidat|footprint|risk|position size|trade review|"
    r"went long|going long|went short|going short|long here|short here)\b", re.I)
MEDIUM = re.compile(
    r"\b(chart|level|support|resistance|acceptance|rejection|volume profile|"
    r"higher timeframe|lower timeframe|confluence|bullish|bearish|range|breakout)\b|"
    r"\$\s?\d[\d,.]*|\b\d[\d,.]*\s?(?:k|thousand)\b", re.I)
NOISE = re.compile(
    r"\b(giveaway|give away|subscribe|like and subscribe|sponsor|discord|nft|"
    r"link in bio|comment below|merch|promo code)\b", re.I)
TIMECODE = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})")
TAG = re.compile(r"<[^>]*>")


@dataclass
class Moment:
    time_s: float
    score: float
    reasons: list[str]
    evidence: list[str]


def parse_vtt(text: str) -> list[tuple[float, float, str]]:
    rows: list[tuple[float, float, str]] = []
    lines = text.replace("\ufeff", "").splitlines()
    i = 0
    while i < len(lines):
        match = TIMECODE.search(lines[i])
        if not match:
            i += 1
            continue
        vals = match.groups()
        h, m, s, ms = vals
        start = (int(h or 0) * 3600) + int(m) * 60 + int(s) + int(ms) / 1000
        end = start
        tail = lines[i][match.end():]
        end_match = TIMECODE.search(tail)
        if end_match:
            eh, em, es, ems = end_match.groups()
            end = int(eh or 0) * 3600 + int(em) * 60 + int(es) + int(ems) / 1000
        i += 1
        body: list[str] = []
        while i < len(lines) and lines[i].strip():
            if not TIMECODE.search(lines[i]) and lines[i].strip() != "WEBVTT":
                body.append(lines[i])
            i += 1
        clean = " ".join(html.unescape(TAG.sub(" ", x)) for x in body)
        clean = " ".join(clean.split())
        if clean:
            rows.append((start, end, clean))
    return rows


def parse_asr_segments(text: str) -> list[tuple[float, float, str]]:
    """Read local-ASR segments as separate, visibly labeled evidence cues."""
    payload = json.loads(text)
    if isinstance(payload, dict):
        payload = payload.get("segments", [])
    if not isinstance(payload, list):
        return []
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            start, end = float(item["start"]), float(item["end"])
            phrase = " ".join(str(item.get("text") or "").split())
        except (KeyError, TypeError, ValueError):
            continue
        if phrase and start >= 0 and end >= start:
            rows.append((start, end, f"[LOCAL ASR] {phrase}"))
    return rows


def score_moments(cues: list[tuple[float, float, str]], *, claims: list[dict] | None = None,
                  lessons: list[dict] | None = None, scene_times: list[float] | None = None,
                  max_frames: int = 16, min_gap_s: float = 35) -> list[Moment]:
    """Return nonuniform evidence moments; repeated nearby cues are merged."""
    candidates: list[Moment] = []
    for start, end, text in cues:
        strong = STRONG.findall(text)
        medium = MEDIUM.findall(text)
        noise = NOISE.findall(text)
        score = 5 * len(strong) + 2 * len(medium) - 6 * len(noise)
        if score >= 5:
            candidates.append(Moment(start, float(score), ["transcript_cue"], [text[:300]]))
    for row in claims or []:
        quote = str(row.get("quote") or "")
        if STRONG.search(quote) or MEDIUM.search(quote):
            candidates.append(Moment(float(row.get("t_video_s") or 0), 5.0,
                                     ["claim_anchor"], [quote[:300]]))
    for row in lessons or []:
        quote = str(row.get("quote") or "")
        cat = str(row.get("class") or "")
        if cat in {"transferable_principle", "teaching_example", "dated_view"} and quote:
            t = row.get("timestamp_seconds")
            if t is not None:
                candidates.append(Moment(float(t), 6.0 + float(row.get("learning_value") or 0),
                                         ["lesson_candidate", cat], [quote[:300]]))
    # A cut is useful only if it is near a meaningful transcript moment; it
    # nudges the screenshot a few seconds toward the changed chart state.
    scenes = sorted(float(x) for x in (scene_times or []))
    for item in candidates:
        nearby = [s for s in scenes if abs(s - item.time_s) <= 25]
        if nearby:
            best = min(nearby, key=lambda s: abs(s - item.time_s))
            item.time_s = best
            item.score += 1.5
            item.reasons.append("near_scene_change")
    candidates.sort(key=lambda x: (-x.score, x.time_s))
    selected: list[Moment] = []
    for item in candidates:
        if any(abs(item.time_s - old.time_s) < min_gap_s for old in selected):
            old = min(selected, key=lambda x: abs(item.time_s - x.time_s))
            old.evidence.extend(x for x in item.evidence if x not in old.evidence)
            old.reasons.extend(x for x in item.reasons if x not in old.reasons)
            continue
        selected.append(item)
        if len(selected) >= max_frames:
            break
    return sorted(selected, key=lambda x: x.time_s)


def detect_scenes(video: Path, threshold: float = .035) -> list[float]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    cmd = [ffmpeg, "-hide_banner", "-i", str(video), "-vf",
           f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=1800)
    return [float(x) for x in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]


def extract(video: Path, moments: list[Moment], out_dir: Path) -> list[dict]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg unavailable")
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for idx, moment in enumerate(moments, 1):
        path = out_dir / f"smart_{idx:03d}_{int(moment.time_s):05d}.jpg"
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss",
                        f"{moment.time_s:.2f}", "-i", str(video), "-frames:v", "1",
                        "-q:v", "2", str(path)], check=True, timeout=120)
        results.append({"time_s": round(moment.time_s, 2), "score": moment.score,
                        "reasons": moment.reasons, "evidence": moment.evidence,
                        "frame": path.name})
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--max-frames", type=int, default=16)
    ap.add_argument("--min-gap", type=float, default=35)
    args = ap.parse_args()
    vtt = next(iter(sorted(args.run_dir.glob("subs.*.vtt"))), None)
    asr_path = args.run_dir / "asr.segments.json"
    video = next((p for p in args.run_dir.glob("video.*") if p.suffix.lower() in {".mp4", ".mkv", ".webm"}), None)
    if not video or (not vtt and not asr_path.is_file()):
        ap.error("run_dir must contain a local video and subtitles or local ASR")
    claims = [json.loads(x) for x in (args.run_dir / "claims.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()] if (args.run_dir / "claims.jsonl").is_file() else []
    notes = json.loads((args.run_dir / "study_notes.json").read_text(encoding="utf-8")) if (args.run_dir / "study_notes.json").is_file() else {}
    cues = parse_vtt(vtt.read_text(encoding="utf-8", errors="replace")) if vtt else []
    asr_cues = parse_asr_segments(asr_path.read_text(encoding="utf-8")) if asr_path.is_file() else []
    cues.extend(asr_cues)
    scenes = detect_scenes(video)
    moments = score_moments(cues, claims=claims, lessons=notes.get("items", []), scene_times=scenes,
                            max_frames=args.max_frames, min_gap_s=args.min_gap)
    rows = extract(video, moments, args.run_dir / "frames")
    payload = {"video_id": args.run_dir.name,
               "selection": "subtitle_and_local_asr_semantic_anchors_plus_claims_lessons_and_nearby_scene_changes; no uniform sampling",
               "subtitle_cues": len(parse_vtt(vtt.read_text(encoding="utf-8", errors="replace"))) if vtt else 0,
               "local_asr_cues": len(asr_cues), "ffmpeg_scene_changes": len(scenes), "frames": rows}
    (args.run_dir / "smart_frames.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"video_id": args.run_dir.name, "scene_changes": len(scenes), "selected": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
