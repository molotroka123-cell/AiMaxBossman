"""Break a short-form edit (TikTok / Reels) into a reusable recipe: cuts, track tempo/beats, cut-on-beat sync, hook, pacing.

Local only (ffmpeg + numpy + librosa). The "hype" numbers are transparent HEURISTICS describing the edit's structure,
not a prediction of views — that needs labelled data (views per clip) we do not have yet.

  <gate-venv>/python tools/trend_edit/analyze_edit.py clip.mp4 --out recipe.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

W, H = 160, 90          # analysis resolution for cut detection


def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,avg_frame_rate:format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True)
    d = json.loads(r.stdout)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    num, _, den = v["avg_frame_rate"].partition("/")
    return {"width": v["width"], "height": v["height"], "fps": round(float(num) / float(den or 1), 3),
            "duration": float(d["format"]["duration"]), "has_audio": any(s["codec_type"] == "audio" for s in d["streams"])}


def frame_diffs(path: Path, fps: float) -> tuple[np.ndarray, np.ndarray]:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={W}:{H}", "-f", "rawvideo",
                          "-pix_fmt", "gray", "-"], capture_output=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)
    d = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))
    return d, np.arange(1, len(f)) / fps


def detect_cuts(d: np.ndarray, t: np.ndarray, min_gap: float = 0.15) -> list[float]:
    """A cut = a frame difference far above the local motion level (robust to camera shake)."""
    cuts, last = [], -1.0
    for i in range(len(d)):
        lo, hi = max(0, i - 12), min(len(d), i + 13)
        local = float(np.median(np.delete(d[lo:hi], i - lo))) if hi - lo > 1 else 0.0
        if d[i] > max(18.0, 3.5 * local) and t[i] - last >= min_gap:
            cuts.append(round(float(t[i]), 3))
            last = t[i]
    return cuts


def audio_beats(path: Path) -> dict:
    import librosa
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "22050", str(wav)], check=True)
        y, sr = librosa.load(str(wav), sr=22050)
    if len(y) < sr // 2:
        return {}
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    rms = librosa.feature.rms(y=y)[0]
    rt = librosa.times_like(rms, sr=sr)
    rise = np.diff(np.convolve(rms, np.ones(20) / 20, mode="same"))
    return {"tempo_bpm": round(float(np.atleast_1d(tempo)[0]), 1), "beats": [round(float(b), 3) for b in beats],
            "drop_s": round(float(rt[int(np.argmax(rise))]), 2), "loudness_rms_mean": round(float(rms.mean()), 4)}


def recipe(path: Path) -> dict:
    info = probe(path)
    d, t = frame_diffs(path, info["fps"])
    cuts = detect_cuts(d, t)
    shots = np.diff([0.0, *cuts, info["duration"]])
    audio = audio_beats(path) if info["has_audio"] else {}
    beats = np.array(audio.get("beats", []))
    sync = None
    if len(beats) and cuts:
        off = [float(np.min(np.abs(beats - c))) for c in cuts]
        sync = {"cuts_within_80ms_of_beat": round(sum(o <= 0.08 for o in off) / len(off), 3),
                "median_offset_ms": round(1000 * float(np.median(off)), 1)}
    first_cut = cuts[0] if cuts else None
    heur = {   # transparent structure checks, each 0/1; NOT a view prediction
        "hook_first_cut_le_1_5s": int(first_cut is not None and first_cut <= 1.5),
        "fast_pacing_ge_1_cut_per_s": int(len(cuts) / max(info["duration"], 1e-6) >= 1.0),
        "cuts_on_beat_ge_50pct": int(bool(sync) and sync["cuts_within_80ms_of_beat"] >= 0.5),
        "short_7_to_20s": int(7 <= info["duration"] <= 20),
        "vertical_9x16": int(info["height"] > info["width"]),
        "has_music": int(bool(audio)),
    }
    return {"file": path.name, **info, "cuts": cuts, "shot_count": len(shots),
            "shot_len_s": {"mean": round(float(shots.mean()), 3), "min": round(float(shots.min()), 3),
                           "max": round(float(shots.max()), 3)},
            "motion_mean": round(float(d.mean()), 2), "audio": audio, "cut_beat_sync": sync,
            "structure_checks": heur, "structure_score": f"{sum(heur.values())}/{len(heur)}",
            "note": "structure_score is a heuristic of edit structure, not a predicted view count"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip", nargs="+")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    res = [recipe(Path(p)) for p in a.clip]
    text = json.dumps(res if len(res) > 1 else res[0], ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
