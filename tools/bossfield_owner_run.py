#!/usr/bin/env python3
"""Bossfield media preflight and edit companion for Bossman's existing Video Studio.

This tool does not submit paid generation requests. Submit via Bossman's
existing governed Studio/CLI, then bring verified clips into one project. Four
clips: Seedance 15 seconds, then 3 local Wan 2.2 takes of 5 seconds each.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bossman-core"))
from bossman.video_factory.ffmpeg import ffmpeg_bin, ffprobe_bin

FRAME_RATE = 24
SLOTS = (("cloud", 15), ("local_01", 5), ("local_02", 5), ("local_03", 5))
MAX_INPUT_BYTES = 1_000_000_000


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def checked(path: Path, root: Path, *, size_limit: int = MAX_INPUT_BYTES) -> Path:
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Expected regular media inside the project directory")
    if path.stat().st_size == 0 or path.stat().st_size > size_limit:
        raise ValueError("Empty or oversized project input")
    return path


def run_command(argv: list[str], *, timeout: int = 300) -> str:
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Media tool unavailable or timed out") from exc
    if result.returncode:
        raise ValueError(f"Media verification/render failed (exit {result.returncode})")
    if len(result.stdout) > 1_000_000:
        raise ValueError("Oversized media metadata")
    return result.stdout.decode("utf-8", errors="replace")


def inspect(path: Path) -> dict:
    probe = ffprobe_bin()
    if not probe:
        raise ValueError("ffprobe is required to verify exact duration, codecs and dimensions")
    data = json.loads(run_command([probe, "-v", "error", "-show_streams", "-show_format",
                                   "-print_format", "json", str(path)], timeout=40))
    stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if stream is None:
        raise ValueError("No video stream")
    duration = float(data.get("format", {}).get("duration") or 0)
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if not 0 < duration <= 120 or width < 64 or height < 64:
        raise ValueError("Invalid duration or frame dimensions")
    return {"duration_s": duration, "width": width, "height": height,
            "video_codec": stream.get("codec_name"),
            "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", []))}


def init_project(project: Path, story: Path, photos: list[Path], aspect: str) -> dict:
    project = project.resolve()
    if project.exists() and any(project.iterdir()):
        raise ValueError("Use a new empty project directory; previous owner media is immutable")
    if not photos or len(photos) > 12 or aspect not in {"9:16", "16:9"}:
        raise ValueError("Supply 1–12 owner photos and a supported aspect ratio")
    if not story.is_file() or story.is_symlink() or not 1 <= story.stat().st_size <= 20_000:
        raise ValueError("Provide an existing bounded story file")
    body = story.read_text(encoding="utf-8")
    if not body.strip():
        raise ValueError("Owner story is empty")
    checked_photos = []
    for src in photos:
        if src.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError("Only owner image files are supported")
        if src.is_symlink() or not src.is_file() or not 1 <= src.stat().st_size <= 25_000_000:
            raise ValueError("Photo must be an existing regular file <=25 MB")
        checked_photos.append(src)
    (project / "references").mkdir(parents=True)
    (project / "raw").mkdir()
    (project / "audit").mkdir()
    (project / "story.txt").write_text(body, encoding="utf-8")
    reference_rows = []
    for i, src in enumerate(checked_photos, 1):
        target = project / "references" / f"owner-{i:02d}{src.suffix.lower()}"
        shutil.copyfile(src, target)
        reference_rows.append({"path": str(target.relative_to(project)), "sha256": digest_file(target)})
    manifest = {
        "schema_version": 1, "brand": "Bossfield", "project": "bossman-video-studio",
        "aspect_ratio": aspect, "fps": FRAME_RATE,
        "story": {"path": "story.txt", "sha256": digest_file(project / "story.txt")},
        "owner_photos": reference_rows,
        "shots": [{"id": name, "duration_s": duration,
                   "path": f"raw/{name}.mp4",
                   "provider": "openrouter:bytedance/seedance-2.5" if name == "cloud" else "sdcpp:wan2.2-ti2v-5b"}
                  for name, duration in SLOTS],
        "generation": "NOT_RUN", "continuity_review": "PENDING", "owner_approval": "PENDING",
        "music": None,
    }
    atomic_json(project / "bossfield.json", manifest)
    return manifest


def load_project(project: Path) -> dict:
    project = project.resolve()
    state = json.loads((project / "bossfield.json").read_text(encoding="utf-8"))
    if (state.get("schema_version") != 1 or state.get("brand") != "Bossfield"
            or state.get("project") != "bossman-video-studio"
            or state.get("aspect_ratio") not in {"9:16", "16:9"}
            or state.get("fps") != FRAME_RATE
            or [(s.get("id"), s.get("duration_s"), s.get("path")) for s in state.get("shots", [])]
            != [(name, duration, f"raw/{name}.mp4") for name, duration in SLOTS]):
        raise ValueError("Unexpected project contract")
    for item in [state["story"], *state["owner_photos"]]:
        file = checked(project / item["path"], project, size_limit=25_000_000)
        if digest_file(file) != item["sha256"]:
            raise ValueError("Owner story/reference changed since plan was frozen")
    if (project / "STOP").exists():
        raise ValueError("Owner STOP is active")
    return state


def audit_shots(project: Path, state: dict) -> list[dict]:
    shots = []
    target_ratio = 9 / 16 if state["aspect_ratio"] == "9:16" else 16 / 9
    for shot in state["shots"]:
        path = checked(project / shot["path"], project)
        measured = inspect(path)
        if measured["duration_s"] < shot["duration_s"] - 0.005:
            raise ValueError("Short provider output: regenerate the clip, never pad fake seconds")
        if abs(measured["width"] / measured["height"] - target_ratio) > 0.05:
            raise ValueError("Clip aspect ratio differs from storyboard")
        shots.append({"id": shot["id"], "path": shot["path"], "sha256": digest_file(path),
                      "provider_declared": shot["provider"], "measured": measured,
                      "provenance_verified": False})
    return shots


def extract_edge(video: Path, out: Path, *, at_end: bool) -> None:
    binary = ffmpeg_bin()
    if not binary:
        raise ValueError("ffmpeg is required")
    run_command([binary, "-v", "error", "-nostdin", "-y",
                 "-sseof", "-0.07", "-i", str(video), "-frames:v", "1", str(out)]
                if at_end else [binary, "-v", "error", "-nostdin", "-y", "-i", str(video),
                                "-frames:v", "1", str(out)], timeout=60)


def assemble(project: Path, music: Path | None = None) -> dict:
    project = project.resolve()
    state = load_project(project)
    report_path = project / "audit" / "assembly.json"
    if report_path.exists() or (project / "bossfield-30s.mp4").exists():
        raise ValueError("Previous assembly exists; preserve original evidence and use a new project")
    clips = audit_shots(project, state)
    srcs = [project / row["path"] for row in clips]
    binary = ffmpeg_bin()
    if not binary:
        raise ValueError("ffmpeg is required")
    width, height = (720, 1280) if state["aspect_ratio"] == "9:16" else (1280, 720)
    clip_filters = []
    for i, (_, seconds) in enumerate(SLOTS):
        clip_filters.append(f"[{i}:v]trim=duration={seconds},setpts=PTS-STARTPTS,"
                            f"fps={FRAME_RATE},scale={width}:{height}:force_original_aspect_ratio=decrease,"
                            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}]")
    clip_filters.append("".join(f"[v{i}]" for i in range(4)) + "concat=n=4:v=1:a=0[v]")
    tmp = project / ("bossfield-30s." + uuid.uuid4().hex + ".tmp.mp4")
    argv = [binary, "-v", "error", "-nostdin", "-y", *sum((["-i", str(p)] for p in srcs), []),
            "-filter_complex", ";".join(clip_filters), "-map", "[v]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-r", "24",
            "-frames:v", "720", "-movflags", "+faststart"]
    music_data = None
    if music is not None:
        track = checked(music.resolve(), project)
        if track.suffix.lower() not in {".wav", ".mp3", ".m4a", ".flac"}:
            raise ValueError("Unsupported soundtrack file")
        music_data = {"path": str(track.relative_to(project)), "sha256": digest_file(track)}
        # Add soundtrack after the four video inputs and use a dedicated audio map.
        pos = argv.index("-filter_complex")
        argv[pos:pos] = ["-stream_loop", "-1", "-i", str(track)]
        argv += ["-map", "4:a:0", "-c:a", "aac", "-b:a", "192k",
                 "-af", "afade=t=out:st=29:d=1"]
    argv += ["-t", "30", str(tmp)]
    try:
        run_command(argv, timeout=600)
        media = inspect(tmp)
        if (not 29.95 <= media["duration_s"] <= 30.05 or media["width"] != width
                or media["height"] != height or (music_data is not None and not media["has_audio"])):
            raise ValueError("Rendered movie failed the exact 30s media contract")
        # Full decode catches corrupt video bodies that ffprobe alone misses.
        run_command([binary, "-v", "error", "-nostdin", "-i", str(tmp),
                     "-f", "null", "-"], timeout=300)
        dest = project / "bossfield-30s.mp4"
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    seams = []
    for i in range(3):
        left = project / "audit" / f"seam-{i + 1}-last.png"
        right = project / "audit" / f"seam-{i + 1}-first.png"
        extract_edge(srcs[i], left, at_end=True)
        extract_edge(srcs[i + 1], right, at_end=False)
        seams.append({"left": str(left.relative_to(project)), "left_sha256": digest_file(left),
                      "right": str(right.relative_to(project)), "right_sha256": digest_file(right),
                      "visual_review": "PENDING"})
    report = {"schema_version": 1, "status": "ASSEMBLED_PENDING_REVIEW", "movie": "bossfield-30s.mp4",
              "movie_sha256": digest_file(dest), "measured": media, "source_shots": clips,
              "source_story_sha256": state["story"]["sha256"], "music": music_data,
              "seams": seams, "continuity_review": "PENDING",
              "owner_approval": "PENDING", "note": "Check original identities and 3 adjacent seam pairs; receipt is not an independent review"}
    atomic_json(report_path, report)
    return report


def verify(project: Path) -> dict:
    project = project.resolve()
    state = load_project(project)
    receipt = json.loads((project / "audit" / "assembly.json").read_text(encoding="utf-8"))
    clips = audit_shots(project, state)
    if [(r["id"], r["sha256"]) for r in clips] != [(r["id"], r["sha256"]) for r in receipt["source_shots"]]:
        raise ValueError("Source clips changed after assembly")
    movie = checked(project / "bossfield-30s.mp4", project)
    if digest_file(movie) != receipt["movie_sha256"]:
        raise ValueError("Rendered movie changed after assembly")
    if inspect(movie) != receipt["measured"]:
        raise ValueError("Media metadata changed after assembly")
    for pair in receipt["seams"]:
        for kind in ("left", "right"):
            path = checked(project / pair[kind], project, size_limit=25_000_000)
            if digest_file(path) != pair[kind + "_sha256"]:
                raise ValueError("Seam evidence changed")
    return {"status": "EVIDENCE_INTACT_PENDING_VISUAL_REVIEW", "movie": str(movie),
            "sha256": receipt["movie_sha256"], "duration_s": receipt["measured"]["duration_s"]}


def anchor(project: Path, after: str) -> dict:
    """Freeze the preceding clip's last frame before generating the next take."""
    project = project.resolve()
    state = load_project(project)
    eligible = {"cloud": "local_01", "local_01": "local_02", "local_02": "local_03"}
    if after not in eligible:
        raise ValueError("Anchor source must be cloud, local_01 or local_02")
    src = checked(project / "raw" / (after + ".mp4"), project)
    metadata = inspect(src)
    expected = next(shot["duration_s"] for shot in state["shots"] if shot["id"] == after)
    if metadata["duration_s"] < expected - 0.005:
        raise ValueError("Source clip too short for a continuity anchor")
    dest = project / "references" / ("anchor-after-" + after + ".png")
    if dest.exists():
        raise ValueError("Anchor exists; retain the frozen attempt and use a new project")
    extract_edge(src, dest, at_end=True)
    return {"status": "ANCHOR_READY", "for_shot": eligible[after],
            "path": str(dest), "sha256": digest_file(dest),
            "source_sha256": digest_file(src), "continuity_verified": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "preflight", "anchor", "assemble", "verify"))
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--story", type=Path)
    parser.add_argument("--photo", action="append", type=Path, default=[])
    parser.add_argument("--aspect", choices=("9:16", "16:9"), default="9:16")
    parser.add_argument("--music", type=Path)
    parser.add_argument("--after", choices=("cloud", "local_01", "local_02"))
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            if args.story is None:
                raise ValueError("--story is required for init")
            result = init_project(args.project, args.story, args.photo, args.aspect)
        elif args.command == "preflight":
            state = load_project(args.project)
            result = {"status": "INPUTS_READY", "shot_plan": state["shots"],
                      "media_ready": False, "cloud_key_present": bool(os.getenv("OPENROUTER_API_KEY")),
                      "ffmpeg": bool(ffmpeg_bin()), "ffprobe": bool(ffprobe_bin())}
        elif args.command == "anchor":
            if not args.after:
                raise ValueError("--after required for continuity anchor")
            result = anchor(args.project, args.after)
        elif args.command == "assemble":
            result = assemble(args.project, args.music)
        else:
            result = verify(args.project)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print("BOSSFIELD_BLOCKED: " + str(exc)[:500], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
