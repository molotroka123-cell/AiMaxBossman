"""Read-only counters for local identity / LoRA datasets and promo video folders (MP21-E).

Counts files, byte-identical duplicates (SHA-256), caption sidecars and image sizes.
Writes counts only: no file names, no pixels, nothing leaves the machine. Never modifies the folders.

Usage:
    python tools/mp21_dataset_counters.py OUT.json
Folders that do not exist are reported as "MISSING" (never guessed).
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import sys

IMG = {".jpg", ".jpeg", ".png", ".webp"}
VID = {".mp4", ".mov", ".webm", ".mkv"}
B = r"C:\Users\asd\Bossman"
V = r"C:\Users\asd\Videos\Bossman-Genjutsu"

IMAGE_SETS = {
    "identity/gf-refs-all (originals: 16 photos + 116 video frames)": V + r"\gf-refs-all",
    "identity/gf-train-refs-20261010": V + r"\gf-train-refs-20261010",
    "identity/refs-kisliy": V + r"\refs-kisliy",
    "identity/man-refs-20261010": V + r"\man-refs-20261010",
    "identity/hair-refs-owner-20261011": V + r"\hair-refs-owner-20261011",
    "identity/gf-hair-clothes-20261010": V + r"\gf-hair-clothes-20261010",
    "hair/kisliy-hair-20261010 head crops": V + r"\kisliy-hair-20261010\head",
    "train/lora-work gfgirl ds (v1/v2 set)": B + r"\lora-work-20261010\ds\gfgirl",
    "train/lora-work kisliy ds": B + r"\lora-work-20261010\ds\kisliy",
    "train/gfgirl v3 dataset (88 images built 10.10)": B + r"\runpod-train-v3-20261010\dataset",
    "aug/gf-v3-approved (generated, owner approved)": B + r"\lora-datasets-20261010\gf-v3-approved",
    "aug/gf-synth out (generated swaps)": B + r"\lora-datasets-20261010\gf-synth\out",
    "aug/gf-aug-v2 synthetic_images (generated)": B + r"\lora-datasets-20261010\gf-aug-v2-20261010\synthetic_images",
    "aug/gf-aug-v2 real_photo_candidates": B + r"\lora-datasets-20261010\gf-aug-v2-20261010\real_photo_candidates",
    "aug/gf-aug-v2 video_frames_review_only": B + r"\lora-datasets-20261010\gf-aug-v2-20261010\video_frames_review_only",
    "voice+stage runpod-train-20261009 (images only)": B + r"\runpod-train-20261009",
}
VIDEO_SETS = sorted(
    d for d in (os.listdir(V) if os.path.isdir(V) else []) if os.path.isdir(os.path.join(V, d))
)


def sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_images(root: str) -> dict | str:
    if not os.path.isdir(root):
        return "MISSING"
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        Image = None
    n = 0
    hashes: dict[str, int] = collections.Counter()
    caps = 0
    sides: list[int] = []
    for dp, _, fn in os.walk(root):
        for f in fn:
            if os.path.splitext(f)[1].lower() not in IMG:
                continue
            p = os.path.join(dp, f)
            n += 1
            hashes[sha(p)] += 1
            if os.path.exists(os.path.splitext(p)[0] + ".txt"):
                caps += 1
            if Image is not None:
                try:
                    with Image.open(p) as im:
                        sides.append(min(im.size))
                except Exception:
                    pass
    sides.sort()
    return {
        "images": n,
        "unique_sha256": len(hashes),
        "byte_identical_extra": n - len(hashes),
        "with_caption_sidecar": caps,
        "min_side_min": sides[0] if sides else None,
        "min_side_median": sides[len(sides) // 2] if sides else None,
        "min_side_lt512": sum(1 for s in sides if s < 512),
    }


def scan_videos(root: str) -> dict | None:
    hashes: dict[str, int] = collections.Counter()
    n = 0
    for dp, _, fn in os.walk(root):
        for f in fn:
            if os.path.splitext(f)[1].lower() in VID:
                n += 1
                hashes[sha(os.path.join(dp, f))] += 1
    if not n:
        return None
    return {"videos": n, "unique_sha256": len(hashes), "byte_identical_extra": n - len(hashes)}


def main() -> int:
    out = {"image_sets": {}, "video_folders": {}}
    for label, root in IMAGE_SETS.items():
        out["image_sets"][label] = scan_images(root)
    for d in VIDEO_SETS:
        r = scan_videos(os.path.join(V, d))
        if r:
            out["video_folders"][d] = r
    out["video_total"] = {
        "videos": sum(v["videos"] for v in out["video_folders"].values()),
        "byte_identical_extra": sum(v["byte_identical_extra"] for v in out["video_folders"].values()),
    }
    path = sys.argv[1] if len(sys.argv) > 1 else "-"
    text = json.dumps(out, indent=1, ensure_ascii=False)
    if path == "-":
        print(text)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
