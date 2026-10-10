"""Run FaceFusion with two Bossman fixes, patched in THIS process only (the FaceFusion checkout is not modified).

1. Steady face (Gate 1, 10.10, kisliy clip: landmark jitter 0.027 > 0.02 IOD). FaceFusion hands every processor the
   frame and +-2 neighbours (`--target-frame-amount 2`); with `--face-tracker-score > 0` it tracks the face through them
   but aligns on the middle frame's RAW landmarks, so the pasted face shakes and pulses in size. Here the tracked face's
   5/68 points and box are a weighted average over the window (weights 1-2-3-2-1: symmetric, no lag).
2. No colour re-ranging on merge: FaceFusion appends `scale=out_color_matrix=bt709:out_range=tv` to every merge (measured
   10.10: mean 45.39 -> 42.93); Bossman restores everything outside the face from the source, so a darker face area
   would show against it.

Run with FaceFusion's interpreter, cwd = the FaceFusion checkout, the same arguments as facefusion.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

WEIGHTS = (1.0, 2.0, 3.0, 2.0, 1.0)


def smooth_points(points: list, weights=WEIGHTS):
    """Weighted mean of equally shaped point arrays centred on the middle one; missing (None) entries are skipped."""
    import numpy as np
    mid = len(points) // 2
    half = len(weights) // 2
    acc, total = None, 0.0
    for offset, w in zip(range(-half, half + 1), weights):
        i = mid + offset
        if 0 <= i < len(points) and points[i] is not None:
            p = np.asarray(points[i], np.float64)
            acc = p * w if acc is None else acc + p * w
            total += w
    return None if acc is None else (acc / total).astype(np.asarray(points[mid]).dtype)


def _install() -> None:
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    sys.path.insert(0, str(Path.cwd()))
    from facefusion import face_selector, face_tracker, ffmpeg_builder

    ffmpeg_builder.convert_color_space = lambda color_space: []

    def smoothed_track_faces(vision_frames, score):
        target_index = len(vision_frames) // 2
        out = []
        for track in face_tracker.create_face_tracks(vision_frames, score):
            idx = sorted(track)
            if not idx[0] <= target_index <= idx[-1]:
                continue
            filled = face_tracker.refill_faces([track.get(i) for i in range(idx[0], idx[-1] + 1)])
            centre = target_index - idx[0]
            window = [filled[centre + k] if 0 <= centre + k < len(filled) else None for k in range(-2, 3)]
            face = filled[centre]
            marks = {key: smooth_points([f.landmark_set.get(key) if f is not None else None for f in window])
                     for key in face.landmark_set}
            box = smooth_points([f.bounding_box if f is not None else None for f in window])
            out.append(face._replace(landmark_set={**face.landmark_set, **{k: v for k, v in marks.items() if v is not None}},
                                     bounding_box=box if box is not None else face.bounding_box))
        return out

    face_selector.track_faces = smoothed_track_faces


if __name__ == "__main__":
    _install()
    from facefusion import conda, core
    conda.setup()
    core.cli()
