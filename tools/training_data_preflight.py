"""Free, offline check of the owner's training data BEFORE any training run (photos + captions, voice).

    python tools/training_data_preflight.py --photos <dir> --voice <dir> --out manifest.json \
        [--expect-photos 45] [--min-voice-minutes 10]

Reads local files only: no network, no model, no GPU, no upload, nothing is rented or spent. The manifest keeps counts, durations,
sizes and sha256 of the files, and caption LENGTHS - never caption text, never audio. Exit 0 = READY, 1 = NEEDS_FIX.
Photos: ``<stem>.jpg|jpeg|png|webp`` paired with ``<stem>.txt``. HEIC is reported (convert first), not read.
Voice: wav is measured with the stdlib; mp3/m4a/ogg/flac need ffprobe on PATH, otherwise they are listed as UNMEASURED.
READY says the data is well-formed; it does not say the training will work or that the result will be good.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

IMAGES = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}
MIN_SIDE = 512
MIN_CLIP_SECONDS = 3.0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def image_size(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:  # noqa: BLE001 - an unreadable image is a finding, not a crash
        return None


def audio_seconds(path: Path) -> float | None:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path)) as w:
                return w.getnframes() / float(w.getframerate())
        except Exception:  # noqa: BLE001
            pass
    probe = shutil.which("ffprobe")
    if not probe:
        return None
    r = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, timeout=60)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def check_photos(folder: Path, expect: int | None) -> dict:
    files = sorted(p for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []
    imgs = [p for p in files if p.suffix.lower() in IMAGES]
    heic = [p.name for p in files if p.suffix.lower() in {".heic", ".heif"}]
    rows, problems, seen = [], [], {}
    if not folder.is_dir():
        problems.append(f"photo folder does not exist: {folder.name}")
    for p in imgs:
        cap = p.with_suffix(".txt")
        size = image_size(p)
        digest = sha256(p)
        text_len = len(cap.read_text(encoding="utf-8", errors="replace").strip()) if cap.is_file() else None
        if size is None:
            problems.append(f"{p.name}: unreadable image")
        elif min(size) < MIN_SIDE:
            problems.append(f"{p.name}: shorter side {min(size)} < {MIN_SIDE}")
        if text_len is None:
            problems.append(f"{p.name}: no caption file {cap.name}")
        elif text_len == 0:
            problems.append(f"{p.name}: empty caption")
        if digest in seen:
            problems.append(f"{p.name}: byte-identical to {seen[digest]}")
        seen.setdefault(digest, p.name)
        rows.append({"file": p.name, "sha256": digest, "size": list(size) if size else None, "caption_chars": text_len})
    orphans = sorted(c.name for c in files if c.suffix.lower() == ".txt" and not any(c.stem == i.stem for i in imgs))
    problems += [f"{n}: caption without an image" for n in orphans]
    problems += [f"{n}: HEIC/HEIF - convert to jpg/png first" for n in heic]
    if expect is not None and len(imgs) != expect:
        problems.append(f"expected {expect} photos, found {len(imgs)}")
    return {"folder_exists": folder.is_dir(), "count": len(imgs), "files": rows, "problems": problems}


def check_voice(folder: Path, min_minutes: float | None) -> dict:
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO) if folder.is_dir() else []
    rows, problems, total = [], [], 0.0
    if not folder.is_dir():
        problems.append(f"voice folder does not exist: {folder.name}")
    elif not files:
        problems.append("voice folder has no audio files")
    for p in files:
        sec = audio_seconds(p)
        if sec is None:
            problems.append(f"{p.name}: duration UNMEASURED (not wav and no ffprobe, or unreadable)")
        else:
            total += sec
            if sec < MIN_CLIP_SECONDS:
                problems.append(f"{p.name}: clip {sec:.1f}s shorter than {MIN_CLIP_SECONDS}s")
        rows.append({"file": p.name, "sha256": sha256(p), "seconds": None if sec is None else round(sec, 2)})
    if min_minutes is not None and total / 60 < min_minutes:
        problems.append(f"total speech {total / 60:.1f} min < required {min_minutes} min")
    return {"folder_exists": folder.is_dir(), "files_count": len(files), "total_seconds": round(total, 2),
            "total_minutes": round(total / 60, 2), "files": rows, "problems": problems}


def run(photos: Path | None, voice: Path | None, expect_photos: int | None, min_voice_minutes: float | None) -> dict:
    out: dict = {"network": "none", "gpu": "none", "cost_usd": 0, "contains_captions_text": False, "contains_audio": False}
    if photos is not None:
        out["photos"] = check_photos(photos, expect_photos)
    if voice is not None:
        out["voice"] = check_voice(voice, min_voice_minutes)
    bad = [p for part in ("photos", "voice") if part in out for p in out[part]["problems"]]
    out["verdict"] = "NEEDS_FIX" if bad or not (photos or voice) else "READY"
    out["problem_count"] = len(bad)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--photos", type=Path)
    ap.add_argument("--voice", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--expect-photos", type=int)
    ap.add_argument("--min-voice-minutes", type=float)
    a = ap.parse_args(argv)
    res = run(a.photos, a.voice, a.expect_photos, a.min_voice_minutes)
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: res[k] for k in res if k not in ("photos", "voice")}, ensure_ascii=False))
    for part in ("photos", "voice"):
        for p in res.get(part, {}).get("problems", [])[:20]:
            print(f"  {part}: {p}")
    return 0 if res["verdict"] == "READY" else 1


if __name__ == "__main__":
    sys.exit(main())
