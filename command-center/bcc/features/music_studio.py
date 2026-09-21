"""Local Music Studio — ACE-Step 1.5 adapter.

Owner-facing product contract:
prompt -> local ACE-Step task -> poll -> download -> verify bytes -> persist.
No cloud fallback and no mock is reported as generation success.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import Feature

router = APIRouter(prefix="/music", tags=["music-studio"])


class MusicIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    lyrics: str = Field(default="[inst]", max_length=12000)
    duration: int = Field(default=90, ge=10, le=600)
    bpm: int | None = Field(default=None, ge=40, le=240)
    key_scale: str = Field(default="", max_length=40)
    time_signature: str = Field(default="4", pattern=r"^(2|3|4|6)$")
    thinking: bool = True
    model: str = Field(default="acestep-v15-turbo", max_length=120)


def _base() -> str:
    return os.environ.get("BOSSMAN_ACESTEP_URL", "http://127.0.0.1:8001").rstrip("/")


def _headers() -> dict[str, str]:
    key = os.environ.get("BOSSMAN_ACESTEP_API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _loopback_only(url: str) -> None:
    """Local generation must not silently become an arbitrary network egress."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("ACE-Step URL must be http(s)")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 80, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("ACE-Step host does not resolve") from exc
    if not infos:
        raise ValueError("ACE-Step host does not resolve")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_loopback:
            raise ValueError("ACE-Step must be a local loopback service")


async def _json(client: httpx.AsyncClient, method: str, path: str, **kwargs):
    response = await client.request(method, _base() + path, headers=_headers(), **kwargs)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict) or body.get("code") != 200 or body.get("error"):
        raise RuntimeError("ACE-Step returned a non-success envelope")
    return body.get("data")


@router.get("/health")
async def health():
    try:
        _loopback_only(_base())
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            response = await client.get(_base() + "/health", headers=_headers())
            response.raise_for_status()
        return {"status": "READY", "provider": "ACE-Step 1.5", "local": True,
                "base_url": _base()}
    except Exception as exc:  # health endpoint must explain, not crash the UI
        return {"status": "NEEDS_ATTENTION", "provider": "ACE-Step 1.5",
                "local": True, "reason": f"{type(exc).__name__}: {exc}"}


@router.post("/generate")
async def generate(body: MusicIn, request: Request):
    try:
        _loopback_only(_base())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    payload = {
        "prompt": body.prompt,
        "lyrics": body.lyrics,
        "audio_duration": body.duration,
        "time_signature": body.time_signature,
        "thinking": body.thinking,
        "model": body.model,
    }
    if body.bpm is not None:
        payload["bpm"] = body.bpm
    if body.key_scale:
        payload["key_scale"] = body.key_scale
    try:
        async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
            data = await _json(client, "POST", "/release_task", json=payload)
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(503, f"ACE-Step unavailable: {type(exc).__name__}") from None
    task_id = data.get("task_id") if isinstance(data, dict) else None
    if not isinstance(task_id, str) or not task_id:
        raise HTTPException(502, "ACE-Step did not return task_id")
    await request.app.state.svc.bus.emit("music.generation.queued", task_id=task_id,
                                         model=body.model, duration=body.duration)
    return {"task_id": task_id, "status": "queued", "provider": "ACE-Step 1.5"}


@router.get("/tasks/{task_id}")
async def task(task_id: str, request: Request):
    try:
        _loopback_only(_base())
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            data = await _json(client, "POST", "/query_result",
                               json={"task_id_list": [task_id]})
    except (ValueError, httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(503, f"ACE-Step unavailable: {type(exc).__name__}") from None
    if not isinstance(data, list) or not data:
        raise HTTPException(502, "ACE-Step returned no task state")
    row = data[0]
    state = row.get("status")
    if state == 0:
        return {"task_id": task_id, "status": "running"}
    if state == 2:
        return {"task_id": task_id, "status": "failed"}
    if state != 1:
        raise HTTPException(502, "ACE-Step returned unknown task state")
    try:
        outputs = json.loads(row.get("result") or "[]")
    except json.JSONDecodeError:
        raise HTTPException(502, "ACE-Step result is malformed") from None
    refs = [x.get("file") for x in outputs if isinstance(x, dict) and x.get("file")]
    if not refs:
        raise HTTPException(502, "ACE-Step succeeded without audio output")
    return {"task_id": task_id, "status": "completed", "outputs": refs,
            "metadata": [x.get("metas") or {} for x in outputs if isinstance(x, dict)]}


@router.post("/tasks/{task_id}/save")
async def save(task_id: str, request: Request):
    state = await task(task_id, request)
    if state.get("status") != "completed":
        raise HTTPException(409, "music task is not completed")
    ref = state["outputs"][0]
    target = urljoin(_base() + "/", ref.lstrip("/"))
    if urlsplit(target).netloc != urlsplit(_base()).netloc:
        raise HTTPException(409, "ACE-Step output escaped configured local provider")
    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            response = await client.get(target, headers=_headers())
            response.raise_for_status()
            payload = response.content
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"audio download failed: {type(exc).__name__}") from None
    if len(payload) < 1024:
        raise HTTPException(502, "generated audio is unexpectedly small")
    # Minimal magic verification; full ffprobe happens in installed/hardware acceptance.
    if not (payload.startswith(b"ID3") or payload[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
            or payload.startswith(b"RIFF") or payload.startswith(b"fLaC") or payload.startswith(b"OggS")):
        raise HTTPException(502, "provider output is not a recognized audio container")
    digest = hashlib.sha256(payload).hexdigest()
    root = Path(request.app.state.svc.settings.data_dir) / "music"
    root.mkdir(parents=True, exist_ok=True)
    suffix = ".wav" if payload.startswith(b"RIFF") else ".flac" if payload.startswith(b"fLaC") else ".ogg" if payload.startswith(b"OggS") else ".mp3"
    path = root / f"{task_id}-{digest[:12]}{suffix}"
    path.write_bytes(payload)
    await request.app.state.svc.bus.emit("music.generation.saved", task_id=task_id,
                                         sha256=digest, bytes=len(payload))
    return {"task_id": task_id, "status": "saved", "path": str(path),
            "sha256": digest, "bytes": len(payload), "provider": "ACE-Step 1.5"}


@router.get("/presets")
async def presets():
    return {"items": [
        {"id": "phonk", "label": "Phonk", "prompt": "dark aggressive phonk, distorted cowbell melody, punchy 808 bass, Memphis-inspired drums, instrumental", "bpm": 130},
        {"id": "drift-phonk", "label": "Drift Phonk", "prompt": "high-energy drift phonk, saturated cowbells, hard clipped 808, fast driving drums, instrumental", "bpm": 150},
        {"id": "ultrafunk", "label": "Ultra Funk", "prompt": "ultrafunk, heavy club bass, chopped rhythmic vocal textures, aggressive electronic percussion", "bpm": 130},
        {"id": "nightcore", "label": "Nightcore", "prompt": "fast bright nightcore-inspired electronic pop, energetic drums, high-register vocal treatment", "bpm": 170},
        {"id": "electro", "label": "Electro", "prompt": "energetic electronic dance track, driving kick, bright synth hook, club arrangement", "bpm": 128},
    ]}


FEATURE = Feature(name="music_studio", router=router)
