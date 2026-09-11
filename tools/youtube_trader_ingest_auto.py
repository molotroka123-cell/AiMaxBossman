#!/usr/bin/env python3
"""URL-only YouTube Trader ingest with automatic local ASR fallback.

This wrapper first asks YouTube for normal/automatic captions via the canonical
`youtube_trader_ingest` pipeline. If no captions are available it downloads the
public audio track and asks a configured local OpenAI-compatible transcription
endpoint for timestamped segments. If ASR is unavailable, ingestion still
continues visually; speech is never fabricated.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from tools import youtube_trader_ingest as base

_ORIGINAL_DOWNLOAD_SUBTITLES = base.download_subtitles
ASR_MODEL = os.getenv("BOSSMAN_LOCAL_ASR_MODEL", "whisper-1").strip() or "whisper-1"


def _download_audio(url: str, workdir: Path) -> Path:
    ytdlp = base.require_binary("yt-dlp")
    template = str(workdir / "asr_audio.%(ext)s")
    base._run([
        ytdlp,
        "--no-playlist",
        "-f",
        "ba/bestaudio",
        "-x",
        "--audio-format",
        "wav",
        "--audio-quality",
        "5",
        "-o",
        template,
        url,
    ], timeout=1800)
    rows = sorted(workdir.glob("asr_audio.*"))
    if not rows:
        raise RuntimeError("audio extraction produced no file")
    return max(rows, key=lambda p: p.stat().st_size)


def _multipart(fields: dict[str, str], file_field: str, path: Path) -> tuple[bytes, str]:
    boundary = "----BossmanBoundary" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{file_field}"; filename="{path.name}"\r\n'.encode(),
        b"Content-Type: audio/wav\r\n\r\n",
        path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return b"".join(chunks), boundary


def _transcribe(audio: Path, *, api_base: str) -> dict[str, Any]:
    body, boundary = _multipart(
        {
            "model": ASR_MODEL,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "segment",
        },
        "file",
        audio,
    )
    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/audio/transcriptions",
        data=body,
        method="POST",
    )
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Accept", "application/json")
    token = os.getenv("BOSSMAN_LOCAL_API_KEY", "").strip()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=1800) as resp:  # noqa: S310 - configured local endpoint
        return json.loads(resp.read().decode("utf-8"))


def _vtt_ts(seconds: float) -> str:
    ms_total = max(0, int(round(float(seconds) * 1000)))
    hours, rem = divmod(ms_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _segments_to_vtt(data: dict[str, Any]) -> str:
    segments = data.get("segments") or []
    out = ["WEBVTT", ""]
    for i, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            continue
        start = segment.get("start")
        end = segment.get("end")
        text = str(segment.get("text") or "").strip()
        if start is None or end is None or not text:
            continue
        out.extend([str(i), f"{_vtt_ts(float(start))} --> {_vtt_ts(float(end))}", text, ""])
    return "\n".join(out)


def download_subtitles_with_asr(url: str, workdir: Path) -> list[Path]:
    paths = _ORIGINAL_DOWNLOAD_SUBTITLES(url, workdir)
    if paths:
        return paths
    try:
        audio = _download_audio(url, workdir)
        data = _transcribe(audio, api_base=base.DEFAULT_API_BASE)
        vtt = _segments_to_vtt(data)
        if "-->" not in vtt:
            return []
        path = workdir / "subs.local-asr.vtt"
        path.write_text(vtt, encoding="utf-8")
        return [path]
    except Exception:  # noqa: BLE001 - visual ingestion remains usable without ASR
        return []


def main() -> int:
    base.download_subtitles = download_subtitles_with_asr
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
