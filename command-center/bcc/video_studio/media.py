"""Owned immutable media and bounded, shell-free local FFmpeg primitives.

Only self-contained, magic-recognised media containers are accepted. Playlists,
URLs, devices, scripts and demuxer-selected external resources are not inputs.
"""
from __future__ import annotations

import asyncio
from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
from fractions import Fraction

TICKS = 1_000_000


async def blocking(function, *args):
    """Do not abandon a file worker while its caller removes temporary files."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def binary(name: str) -> str:
    value = shutil.which(name)
    if not value:
        raise ValueError(f"{name} is unavailable; install a local FFmpeg build")
    return value


async def process(argv, *, progress=None, diagnostic=None, stage="render", timeout=3600, max_output=8_388_608):
    """Bound captures, propagate failure, and terminate AND reap on cancellation."""
    proc = await asyncio.create_subprocess_exec(*map(str, argv), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    tail = deque(maxlen=32)
    result = bytearray()

    async def stdout():
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            if not progress and "-progress" not in argv:
                if len(result) + len(line) > max_output:
                    raise ValueError("media process output exceeded its capture limit")
                result.extend(line)
            if progress and b"=" in line:
                key, value = line.decode("utf-8", "replace").strip().split("=", 1)
                if key in {"out_time_us", "progress", "frame"}:
                    await progress(stage, {key: value})

    async def stderr():
        while True:
            block = await proc.stderr.readline()
            if not block:
                return
            tail.append(block)
            if diagnostic:
                await diagnostic(block.decode("utf-8", "replace"))

    readers = [asyncio.create_task(stdout()), asyncio.create_task(stderr())]
    try:
        async with asyncio.timeout(timeout):
            await asyncio.gather(*readers)
            await proc.wait()
        if proc.returncode:
            message = b"".join(tail).decode("utf-8", "replace")[-3000:]
            raise ValueError(f"media process failed ({proc.returncode}): {message}")
        return bytes(result), b"".join(tail)
    except BaseException:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.wait()
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        raise


def file_kind(path: Path) -> tuple[str, str]:
    with path.open("rb") as stream:
        head = stream.read(64)
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "matroska", ".mkv"
    if len(head) >= 12 and head[4:8] in {b"ftyp", b"moov", b"mdat", b"free", b"wide"}:
        return "mov", ".mp4"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "wav", ".wav"
    if head.startswith(b"RIFF") and head[8:12] == b"AVI ":
        return "avi", ".avi"
    if head.startswith(b"fLaC"):
        return "flac", ".flac"
    if head.startswith(b"OggS"):
        return "ogg", ".ogg"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 255 and head[1] & 224 == 224):
        if not head.startswith(b"\xff\xd8"):
            return "mp3", ".mp3"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png_pipe", ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg_pipe", ".jpg"
    raise ValueError("unsupported or unsafe media container; playlists and external references are forbidden")


def input_args(path: Path) -> list[str]:
    kind, _ = file_kind(path)
    args = ["-protocol_whitelist", "file", "-f", kind]
    if kind == "mov":
        args += ["-enable_drefs", "0", "-use_absolute_path", "0"]
    return args + ["-i", str(path)]


async def probe(path: Path) -> dict:
    out, _ = await process([binary("ffprobe"), "-v", "error", *input_args(path),
        "-show_format", "-show_streams", "-of", "json"], timeout=60)
    data = json.loads(out)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    if not video and not audio:
        raise ValueError("media has no usable video or audio stream")
    raw_duration = data.get("format", {}).get("duration") or video.get("duration") or audio.get("duration") or 0
    duration = float(raw_duration)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("invalid media duration")
    try:
        fps = Fraction(video.get("avg_frame_rate", "0/1"))
    except (ValueError, ZeroDivisionError):
        fps = Fraction(0)
    return {"duration_ticks": round(duration * TICKS), "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)), "fps": {"num": fps.numerator, "den": fps.denominator},
        "has_video": bool(video), "has_audio": bool(audio), "sample_rate": int(audio.get("sample_rate", 0)),
        "channels": int(audio.get("channels", 0)), "metadata": {"format": data.get("format", {}).get("format_name"),
            "video_codec": video.get("codec_name"), "audio_codec": audio.get("codec_name"),
            "pixel_format": video.get("pix_fmt"), "sample_aspect_ratio": video.get("sample_aspect_ratio"),
            "color_space": video.get("color_space"), "color_transfer": video.get("color_transfer"),
            "avg_frame_rate": video.get("avg_frame_rate"), "r_frame_rate": video.get("r_frame_rate"),
            "video_start": video.get("start_time"), "audio_start": audio.get("start_time"),
            "video_duration": video.get("duration"), "audio_duration": audio.get("duration")}}


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class MediaLibrary:
    def __init__(self, root):
        self.root = Path(root).resolve()

    async def import_file(self, path, name=None):
        """Copy a HOST-APPROVED attachment in bounded chunks; this is not authorization."""
        source = Path(path)
        if not source.is_file():
            raise ValueError("approved media attachment must be a regular file")
        directory = self.root / "media"
        directory.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix="ingest-", suffix=".part", dir=directory)
        temporary = Path(temporary)
        try:
            def copy():
                with os.fdopen(handle, "wb") as target, source.open("rb") as original:
                    shutil.copyfileobj(original, target, length=1024 * 1024)
                    target.flush()
                    os.fsync(target.fileno())
            await blocking(copy)
            _, ext = file_kind(temporary)
            info = await probe(temporary)
            digest = await blocking(digest_file, temporary)
            destination = directory / (digest + ext)
            if destination.exists():
                if await blocking(digest_file, destination) != digest:
                    raise ValueError("existing content-addressed media is corrupt")
            else:
                os.replace(temporary, destination)
            return {"id": "m_" + digest[:24], "name": str(name or source.name)[:255],
                "relative_path": destination.relative_to(self.root).as_posix(), "sha256": digest,
                "bytes": destination.stat().st_size, **info, "folder": "", "tags": []}
        finally:
            temporary.unlink(missing_ok=True)

    def resolve(self, media):
        digest = media.get("sha256", "")
        relative = media.get("relative_path", "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or not isinstance(relative, str):
            raise ValueError("invalid owned media reference")
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root / "media") or path.stem != digest or not path.is_file():
            raise ValueError("media path escaped owned storage or is missing")
        if digest_file(path) != digest:
            raise ValueError("media content hash mismatch; relink required")
        file_kind(path)
        return path

    async def prepare(self, media):
        path = await blocking(self.resolve, media)
        cache = self.root / "cache" / media["sha256"] / "v1"
        cache.mkdir(parents=True, exist_ok=True)
        artifacts = {}
        ff = [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        async def output(key, name, args):
            target = cache / name
            if not target.exists():
                temp = cache / (uuid.uuid4().hex + "-" + name)
                try:
                    await process([*ff, *input_args(path), *args, str(temp)])
                    os.replace(temp, target)
                finally:
                    temp.unlink(missing_ok=True)
            artifacts[key] = target.relative_to(self.root).as_posix()
        if media["has_video"]:
            await output("thumbnail", "thumb.jpg", ["-frames:v", "1", "-vf", "scale=320:-2", "-an"])
            await output("proxy", "proxy.mp4", ["-vf", "scale=640:-2,fps=25,setsar=1", "-c:v", "libx264",
                "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart"])
        if media["has_audio"]:
            await output("waveform", "wave.png", ["-filter_complex", "[0:a]showwavespic=s=1200x160:colors=5b9dff[v]", "-map", "[v]", "-frames:v", "1", "-an"])
        return artifacts

    @staticmethod
    def capabilities():
        return capabilities()


def capabilities():
    """Build availability, not a claim that every encoder works on this host."""
    import subprocess
    result = {"ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"), "filters": [], "encoders": [],
        "hardware_status": "requires profile-specific actual encode probe", "cloud": False,
        "asr": "BLOCKED: configure and validate a local whisper.cpp model", "generation": "BLOCKED: no verified generation provider connected"}
    if result["ffmpeg"]:
        for option, key in [("-filters", "filters"), ("-encoders", "encoders")]:
            run = subprocess.run([result["ffmpeg"], "-hide_banner", option], capture_output=True, text=True, timeout=15)
            result[key] = [m.group(1) for line in run.stdout.splitlines() if (m := re.match(r"^\s*[A-Z.]{2,6}\s+(\w+)\s", line))]
    model=os.getenv("BOSSMAN_VIDEO_ASR_MODEL")
    if model and Path(model).is_file() and "whisper" in result["filters"]:
        result["asr"]="AVAILABLE: host-configured local whisper.cpp model; CPU execution"
    result["asr_model_configured"]=bool(model and Path(model).is_file())
    result["tracking"]="requires host-configured OpenCV runtime; translation features only"
    return result
