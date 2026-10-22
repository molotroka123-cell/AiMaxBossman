"""obs-lite v0.3.0 hardware H.264 screen recorder & edge-crop integration for Bossman.

Upstream: https://github.com/25Dark25/obs-lite (Rust, Windows Graphics Capture + Media Foundation H.264).
Installed binary: %LOCALAPPDATA%/Programs/obs-lite/obs-lite.exe

Experiment rule (Owner 22.10):
- Bossman records the screen / Telegram Desktop post and crops edges deterministically via ffmpeg,
  then sends the MP4 directly to the owner's Pult without inspecting frames via vision/view_file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature

router = APIRouter(prefix="/obs-lite", tags=["obs-lite"])

UPSTREAM_URL = "https://github.com/25Dark25/obs-lite"
PINNED_VERSION = "v0.3.0"
ZIP_SHA256 = "593100A30EA0BC7BDC45B4B67E6D934B412E9FD7E52E536D43123245950241FE"
MSI_SHA256 = "4F0F78CF057BDCCF2A64BC77E13CA1FEBA634EA0B1CD6344BEBC987F9843FD81"


def find_obs_lite_exe() -> Path | None:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local")) / "Programs/obs-lite/obs-lite.exe",
        Path.home() / "Bossman/obs-lite/bin/obs-lite.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    which = shutil.which("obs-lite")
    return Path(which) if which else None


def find_ffmpeg_exe() -> Path | None:
    which = shutil.which("ffmpeg")
    if which:
        return Path(which)
    winget_glob = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local")) / "Microsoft/WinGet/Packages"
    if winget_glob.is_dir():
        for p in winget_glob.rglob("ffmpeg.exe"):
            return p
    return None


def status() -> dict[str, Any]:
    exe = find_obs_lite_exe()
    ff = find_ffmpeg_exe()
    return {
        "id": "obs_lite",
        "name": "obs-lite (Rust H.264 Screen Recorder)",
        "upstream": UPSTREAM_URL,
        "version": PINNED_VERSION,
        "license": "MIT",
        "status": "installed" if exe is not None else "needs_setup",
        "binary_found": exe is not None,
        "ffmpeg_found": ff is not None,
        "privacy_egress": "loopback_only",
        "sha256_zip": ZIP_SHA256,
        "sha256_msi": MSI_SHA256,
    }


def parse_selftest_stdout(stdout: str) -> dict[str, Any]:
    """Parse obs-lite --selftest output without touching or viewing video frames."""
    info: dict[str, Any] = {
        "source": None,
        "encoder": None,
        "captured_frames": 0,
        "encoded_frames": 0,
        "lagged_frames": 0,
        "output_mp4": None,
    }
    for raw_line in (stdout or "").splitlines():
        line = raw_line.strip()
        if line.startswith("source:"):
            info["source"] = line.split("source:", 1)[1].strip()
        elif line.startswith("encoder:"):
            info["encoder"] = line.split("encoder:", 1)[1].strip()
        elif line.startswith("captured frames:"):
            try:
                info["captured_frames"] = int(line.split(":", 1)[1].strip().split()[0])
            except ValueError:
                pass
        elif line.startswith("encoded:"):
            parts = line.replace("encoded:", "").replace("lagged:", "").split()
            if len(parts) >= 1:
                try:
                    info["encoded_frames"] = int(parts[0])
                except ValueError:
                    pass
            if len(parts) >= 2:
                try:
                    info["lagged_frames"] = int(parts[1])
                except ValueError:
                    pass
        elif line.startswith("file:"):
            info["output_mp4"] = line.split("file:", 1)[1].strip()
        elif line.endswith(".mp4") and (":\\" in line or "/" in line):
            info["output_mp4"] = line.split()[-1].strip()
    return info


def crop_video_edges(
    input_mp4: Path,
    output_mp4: Path,
    *,
    crop_left: int = 80,
    crop_right: int = 80,
    crop_top: int = 56,
    crop_bottom: int = 64,
    in_width: int = 1920,
    in_height: int = 1080,
) -> dict[str, Any]:
    """Crop screen edges (window borders, title bar, taskbar) using ffmpeg without decoding frames into LLM context."""
    ff = find_ffmpeg_exe()
    if ff is None:
        raise RuntimeError("ffmpeg not found on system")
    if not input_mp4.is_file():
        raise FileNotFoundError(f"Input video not found: {input_mp4}")

    out_w = max(320, (in_width - crop_left - crop_right) // 2 * 2)
    out_h = max(240, (in_height - crop_top - crop_bottom) // 2 * 2)
    crop_filter = f"crop={out_w}:{out_h}:{crop_left}:{crop_top}"

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(ff),
        "-y",
        "-i", str(input_mp4),
        "-vf", crop_filter,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_mp4),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0 or not output_mp4.is_file():
        raise RuntimeError(f"ffmpeg crop failed (rc={res.returncode}): {res.stderr[-400:]}")
    return {
        "input_mp4": str(input_mp4),
        "output_mp4": str(output_mp4),
        "crop_filter": crop_filter,
        "width": out_w,
        "height": out_h,
        "size_bytes": output_mp4.stat().st_size,
    }


def record_screen(duration_sec: int = 8, output_dir: Path | None = None) -> dict[str, Any]:
    """Run obs-lite --selftest <duration_sec> and return telemetry + recorded file path."""
    exe = find_obs_lite_exe()
    if exe is None:
        raise RuntimeError("obs-lite.exe is not installed")
    dur = max(2, min(60, int(duration_sec)))
    videos_dir = Path(output_dir) if output_dir else (Path.home() / "Videos")
    videos_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    proc = subprocess.run(
        [str(exe), "--selftest", str(dur)],
        cwd=str(videos_dir),
        capture_output=True,
        text=True,
        timeout=dur + 30,
        encoding="utf-8",
        errors="replace",
    )
    elapsed_s = round(time.monotonic() - t0, 2)
    parsed = parse_selftest_stdout(proc.stdout)

    mp4_path: Path | None = None
    if parsed.get("output_mp4"):
        raw_p = Path(parsed["output_mp4"])
        mp4_path = raw_p if raw_p.is_absolute() else (videos_dir / raw_p)
    if (mp4_path is None or not mp4_path.is_file()) and (videos_dir / "selftest.mp4").is_file():
        mp4_path = videos_dir / "selftest.mp4"

    if mp4_path is None or not mp4_path.is_file():
        raise RuntimeError(f"obs-lite exited rc={proc.returncode} without producing MP4: {proc.stdout} {proc.stderr}")

    stamped_path = videos_dir / f"obs-lite-{int(time.time())}.mp4"
    shutil.copy2(mp4_path, stamped_path)
    mp4_path = stamped_path

    parsed.update({
        "returncode": proc.returncode,
        "duration_requested_s": dur,
        "elapsed_s": elapsed_s,
        "output_mp4": str(mp4_path),
        "size_bytes": mp4_path.stat().st_size,
        "lag_ratio": round(parsed["lagged_frames"] / max(1, parsed["encoded_frames"]), 4),
    })
    return parsed


@router.get("/status")
async def get_status() -> dict[str, Any]:
    return status()


@router.post("/record")
async def post_record(body: dict = Body(default_factory=dict)) -> dict[str, Any]:
    dur = int(body.get("duration_sec") or 8)
    do_crop = bool(body.get("crop_edges", True))
    try:
        rec = record_screen(dur)
        if do_crop:
            src = Path(rec["output_mp4"])
            cropped_path = src.with_name(src.stem + "-cropped.mp4")
            crop_res = crop_video_edges(
                src,
                cropped_path,
                crop_left=int(body.get("crop_left", 80)),
                crop_right=int(body.get("crop_right", 80)),
                crop_top=int(body.get("crop_top", 56)),
                crop_bottom=int(body.get("crop_bottom", 64)),
            )
            rec["cropped"] = crop_res
        return rec
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


async def _tool_record_screen(args: dict, ctx: ToolContext) -> ToolResult:
    dur = int(args.get("duration_sec") or 8)
    do_crop = bool(args.get("crop_edges", True))
    rec = record_screen(dur)
    if do_crop:
        src = Path(rec["output_mp4"])
        cropped_path = src.with_name(src.stem + "-cropped.mp4")
        rec["cropped"] = crop_video_edges(
            src,
            cropped_path,
            crop_left=int(args.get("crop_left", 80)),
            crop_right=int(args.get("crop_right", 80)),
            crop_top=int(args.get("crop_top", 56)),
            crop_bottom=int(args.get("crop_bottom", 64)),
        )
    return ToolResult(ok=True, output=rec, preview=f"obs-lite recorded {rec['output_mp4']} (lagged={rec['lagged_frames']})")


SPECS = [
    ToolSpec(
        name="obs_lite.record",
        description="Записать экран через obs-lite (H.264 GPU) и опционально обрезать края через ffmpeg без просмотра кадров моделью.",
        handler=_tool_record_screen,
        input_schema={
            "duration_sec": {"type": "integer", "description": "длительность записи в секундах (2..60)"},
            "crop_edges": {"type": "boolean", "description": "обрезать края через ffmpeg"},
            "crop_left": {"type": "integer", "description": "отступ слева (px)"},
            "crop_right": {"type": "integer", "description": "отступ справа (px)"},
            "crop_top": {"type": "integer", "description": "отступ сверху (px)"},
            "crop_bottom": {"type": "integer", "description": "отступ снизу (px)"},
        },
        required=[],
        category="exec",
        permission="desktop.control",
        source="obs_lite",
        default_effect="ask",
        timeout_seconds=120.0,
        idempotent=False,
        external_output=True,
    ),
]


async def _setup(svc: Any) -> None:
    for _s in SPECS:
        REGISTRY.register(_s)


FEATURE = Feature(name="obs_lite_recorder", router=router, setup=_setup)
