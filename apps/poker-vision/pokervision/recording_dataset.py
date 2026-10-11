"""Local owner recordings -> reviewed perception examples, never strategy labels.

Run with ``python -m pokervision.recording_dataset``. This is an offline data
utility for the existing Poker Vision calibrator, not a second task backend.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import shutil

import cv2

SPLITS = {"train", "dev", "test"}
STREETS = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _private_root(root: Path) -> Path:
    root = root.resolve()
    if any((p / ".git").exists() for p in (root, *root.parents)):
        raise ValueError("Private recording datasets must be outside Git checkouts")
    return root


def prepare(source: Path, root: Path, *, game: str, split: str,
            session_group: str, sample_fps: float = 1, max_frames: int = 1800) -> dict:
    """Sample at most max_frames. All clips of one session must share group/split.

    Explicit session groups are owner labels, not inferred from video content.
    Separate recordings of the same hand must not be assigned different groups.
    """
    source = source.resolve(strict=True)
    root = _private_root(root)
    if game not in {"cash", "tournament", "unknown"} or split not in SPLITS:
        raise ValueError("game must be cash/tournament/unknown; split train/dev/test")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_group):
        raise ValueError("session_group must be a short non-personal identifier")
    if not math.isfinite(sample_fps) or not 0.1 <= sample_fps <= 5:
        raise ValueError("sample_fps must be 0.1..5")
    if isinstance(max_frames, bool) or not isinstance(max_frames, int) or not 1 <= max_frames <= 10000:
        raise ValueError("max_frames must be 1..10000")
    sha = digest(source)
    root.mkdir(parents=True, exist_ok=True)
    for p in root.glob("*/manifest.json"):
        other = json.loads(p.read_text(encoding="utf-8"))
        if other["source_sha256"] == sha:
            raise ValueError("Recording already imported; retain existing annotations")
        if other["session_group"] == session_group and other["split"] != split:
            raise ValueError("Session leakage: same session_group in different splits")
    dest = root / (game + "-" + sha[:16])
    if dest.exists():
        raise FileExistsError(dest)
    cap = cv2.VideoCapture(str(source))
    staging = None
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not cap.isOpened() or not math.isfinite(fps) or fps <= 0:
            raise ValueError("Recording cannot be decoded with a valid frame rate")
        staging = Path(tempfile.mkdtemp(prefix=".preparing-", dir=root))
        (staging / "frames").mkdir()
        rows = []
        index, next_sample, exhausted = 0, 0.0, False
        # Sequential decode preserves ordering; this does not infer actions between samples.
        while len(rows) < max_frames:
            ok, image = cap.read()
            if not ok:
                exhausted = True
                break
            t_ms = round(index * 1000 / fps)
            index += 1
            if t_ms + 0.001 < next_sample:
                continue
            name = f"frames/{len(rows):06d}.png"
            if not cv2.imwrite(str(staging / name), image):
                raise ValueError("Frame could not be written")
            rows.append({"frame": name, "t_ms": t_ms,
                         "sha256": digest(staging / name), "hand_idx": None,
                         "reviewed": False, "truth": None})
            next_sample += 1000 / sample_fps
        if not rows:
            raise ValueError("Recording contains no decoded frames")
        if digest(source) != sha:
            raise ValueError("Source changed during preparation")
        manifest = {"schema_version": 1, "source_sha256": sha,
                    "session_group": session_group, "game": game, "split": split,
                    "frame_count": len(rows), "source_fps": fps,
                    "sample_fps": sample_fps, "last_sample_ms": rows[-1]["t_ms"],
                    "sampling_stopped_at_limit": not exhausted,
                    "status": "NEEDS_HUMAN_LABELS", "strategy_training_ready": False,
                    "metadata": {"stakes": None, "rake": None, "ante": None,
                                 "payouts": None, "bounty_type": None},
                    "limitations": ["Sampled frames are not a complete action history",
                                     "Decode failure and EOF are not distinguished",
                                     "No automatic card labels or solver targets"]}
        _json(staging / "manifest.json", manifest)
        with (staging / "annotations.jsonl").open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        staging.rename(dest)
        staging = None
        return {"directory": str(dest), **manifest}
    finally:
        cap.release()
        if staging is not None:
            # Only our unique temporary directory, never an existing dataset.
            shutil.rmtree(staging)


def validate_truth(truth: dict) -> None:
    if not isinstance(truth, dict):
        raise ValueError("Reviewed frame needs truth object")
    hero, board = truth.get("hero_cards"), truth.get("board")
    if not isinstance(hero, list) or len(hero) != 2 or not isinstance(board, list):
        raise ValueError("Need two visible hero cards and a board list")
    cards = hero + board
    if any(not isinstance(c, dict) or not re.fullmatch(r"[2-9TJQKA][shdc]", str(c.get("card", ""))) for c in cards):
        raise ValueError("Cards must be objects such as {card: As}")
    values = [c["card"] for c in cards]
    if len(set(values)) != len(values):
        raise ValueError("Duplicate cards")
    street = truth.get("street")
    if street not in STREETS or len(board) != STREETS[street]:
        raise ValueError("Street and board length disagree")
    for key in ("pot", "hero_stack"):
        value = truth.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{key} must be a finite nonnegative number")


def export_reviewed(dataset: Path) -> dict:
    """Write labels.jsonl compatible with eval.dataset.load_session.

    Refuse overwrites and validate everything before creating the output.
    No solver target is inferred from a player's action or the outcome.
    """
    dataset = _private_root(dataset)
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    selected, seen = [], set()
    rows = [json.loads(line) for line in (dataset / "annotations.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != manifest["frame_count"]:
        raise ValueError("Annotation count does not match manifest")
    for row in rows:
        name = row.get("frame", "")
        if not re.fullmatch(r"frames/[0-9]{6}\.png", name) or name in seen:
            raise ValueError("Invalid or repeated frame path")
        seen.add(name)
        p = (dataset / name).resolve(strict=True)
        if not p.is_relative_to(dataset) or digest(p) != row["sha256"]:
            raise ValueError("Frame identity mismatch")
        if row.get("reviewed") is not True:
            continue
        if type(row.get("hand_idx")) is not int or row["hand_idx"] < 0:
            raise ValueError("Reviewed frame needs nonnegative hand_idx")
        validate_truth(row.get("truth"))
        selected.append({"frame": name, "t_ms": row["t_ms"],
                         "hand_idx": row["hand_idx"], "truth": row["truth"]})
    if not selected:
        raise ValueError("No human-reviewed examples; training not started")
    output = dataset / "labels.jsonl"
    with output.open("x", encoding="utf-8") as f:
        f.write("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected))
    return {"labels": str(output), "reviewed_frames": len(selected),
            "split": manifest["split"], "session_group": manifest["session_group"],
            "labels_sha256": digest(output), "strategy_training_ready": False}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("source", type=Path)
    prep.add_argument("--output", type=Path, required=True)
    prep.add_argument("--game", choices=["cash", "tournament", "unknown"], required=True)
    prep.add_argument("--split", choices=sorted(SPLITS), required=True)
    prep.add_argument("--session-group", required=True)
    prep.add_argument("--sample-fps", type=float, default=1)
    prep.add_argument("--max-frames", type=int, default=1800)
    exp = sub.add_parser("export-reviewed")
    exp.add_argument("dataset", type=Path)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "prepare":
            result = prepare(args.source, args.output, game=args.game, split=args.split,
                             session_group=args.session_group, sample_fps=args.sample_fps,
                             max_frames=args.max_frames)
        else:
            result = export_reviewed(args.dataset)
    except (ValueError, OSError, KeyError) as exc:
        ap.exit(1, f"Dataset refused: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
