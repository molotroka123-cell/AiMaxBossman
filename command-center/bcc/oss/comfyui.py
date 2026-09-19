"""Bounded local ComfyUI adapter using upstream /prompt, /history and /view APIs.

No runtime/model download, custom nodes, cloud credentials or global interrupts.
A prompt receipt is never treated as evidence that an image exists.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import secrets
import struct
import time
import zlib
from typing import Any
from urllib.parse import urlsplit

import httpx

MAX_IMAGE_BYTES = 64 * 1024 * 1024
SAFE_NODES = frozenset({"CheckpointLoaderSimple", "CLIPTextEncode", "EmptyLatentImage",
                        "KSampler", "VAEDecode", "SaveImage"})


def local_url(value: str) -> str:
    """Pin localhost to an IP; never resolve a caller-supplied remote hostname."""
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.username or parsed.password or parsed.query
            or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("ComfyUI requires a plain local HTTP origin")
    host = parsed.hostname or ""
    if host == "localhost":
        host = "127.0.0.1"
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("not loopback")
        port = parsed.port or 8188
    except ValueError as exc:
        raise ValueError("ComfyUI must use a loopback IP or localhost") from exc
    return f"http://{'[' + host + ']' if ':' in host else host}:{port}"


def safe_relative(value: str, *, filename: bool = False) -> str:
    if not isinstance(value, str) or (filename and not value):
        raise ValueError("invalid ComfyUI output path")
    if (any(ord(c) < 32 for c in value) or any(c in value for c in "\\:%")
            or value.startswith("/") or any(p in (".", "..") for p in value.split("/"))
            or (filename and "/" in value)):
        raise ValueError("unsafe ComfyUI output path")
    return value


def validate_workflow(workflow: dict) -> None:
    if not isinstance(workflow, dict) or not 1 <= len(workflow) <= 64:
        raise ValueError("expected a bounded ComfyUI API workflow")
    if len(json.dumps(workflow)) > 128 * 1024:
        raise ValueError("workflow too large")
    for node_id, node in workflow.items():
        if not re.fullmatch(r"[0-9]+", str(node_id)) or not isinstance(node, dict):
            raise ValueError("invalid workflow node")
        if node.get("class_type") not in SAFE_NODES or not isinstance(node.get("inputs"), dict):
            raise ValueError("only supported native image nodes are allowed")
        inputs = node["inputs"]
        if node["class_type"] == "SaveImage":
            safe_relative(inputs.get("filename_prefix", "BOSSMAN"))
        if node["class_type"] == "CheckpointLoaderSimple":
            checkpoint = safe_relative(inputs.get("ckpt_name", ""))
            if not checkpoint.endswith(".safetensors"):
                raise ValueError("select an existing .safetensors checkpoint")
        if node["class_type"] == "EmptyLatentImage":
            if inputs.get("batch_size") != 1 or any(
                type(inputs.get(k)) is not int or not 256 <= inputs[k] <= 4096
                or inputs[k] % 8 for k in ("width", "height")
            ):
                raise ValueError("dimensions must be multiples of 8 in 256..4096; batch size is 1")
        if node["class_type"] == "KSampler":
            if type(inputs.get("steps")) is not int or not 1 <= inputs["steps"] <= 200:
                raise ValueError("steps must be 1..200")


class ComfyUIClient:
    def __init__(self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = local_url(base_url)
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, transport=self.transport,
                                 trust_env=False, follow_redirects=False, timeout=15)

    async def _json(self, method: str, path: str, **kwargs) -> dict:
        async with self._client() as client:
            async with client.stream(method, path, **kwargs) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise ValueError("ComfyUI response too large")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("invalid ComfyUI JSON response")
        return value

    async def health(self) -> dict:
        stats = await self._json("GET", "/system_stats")
        if not isinstance(stats.get("system"), dict) or not isinstance(stats.get("devices"), list):
            raise ValueError("endpoint does not report ComfyUI system stats")
        return {"available": True, "provider": "comfyui", "devices": stats["devices"],
                "generation_verified": False}

    async def submit(self, workflow: dict) -> str:
        validate_workflow(workflow)
        result = await self._json("POST", "/prompt", json={"prompt": workflow})
        prompt_id = result.get("prompt_id")
        if result.get("error") or result.get("node_errors") or not isinstance(prompt_id, str):
            raise ValueError("ComfyUI rejected workflow; check installed checkpoint and native nodes")
        self._prompt_id(prompt_id)
        return prompt_id

    @staticmethod
    def _prompt_id(value: str) -> None:
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
            raise ValueError("invalid ComfyUI prompt id")

    async def status(self, prompt_id: str) -> dict:
        self._prompt_id(prompt_id)
        result = await self._json("GET", f"/history/{prompt_id}")
        history = result.get(prompt_id)
        if history is None:
            return {"state": "pending", "outputs": []}
        if not isinstance(history, dict) or not isinstance(history.get("status"), dict):
            raise ValueError("invalid ComfyUI execution history")
        status = history["status"]
        if status.get("status_str") == "error":
            return {"state": "failed", "outputs": []}
        if status.get("completed") is not True or status.get("status_str") != "success":
            return {"state": "pending", "outputs": []}
        outputs = history.get("outputs")
        if not isinstance(outputs, dict):
            raise ValueError("ComfyUI completed without output evidence")
        descriptors = []
        for node in outputs.values():
            if not isinstance(node, dict):
                raise ValueError("invalid ComfyUI output node")
            for descriptor in node.get("images", []):
                if not isinstance(descriptor, dict) or descriptor.get("type") != "output":
                    continue
                safe_relative(descriptor.get("filename"), filename=True)
                safe_relative(descriptor.get("subfolder", ""))
                descriptors.append(descriptor)
        if not descriptors:
            raise ValueError("ComfyUI completed without saved image outputs")
        return {"state": "completed", "outputs": descriptors}

    async def download(self, descriptor: dict) -> tuple[bytes, str, dict]:
        if descriptor.get("type") != "output":
            raise ValueError("only saved ComfyUI output artifacts can be fetched")
        params = {"filename": safe_relative(descriptor.get("filename"), filename=True),
                  "subfolder": safe_relative(descriptor.get("subfolder", "")), "type": "output"}
        async with self._client() as client:
            async with client.stream("GET", "/view", params=params) as response:
                response.raise_for_status()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_IMAGE_BYTES:
                        raise ValueError("ComfyUI image exceeds 64 MiB limit")
        data = bytes(raw)
        width, height = verify_png(data)
        return data, "image/png", {"sha256": hashlib.sha256(data).hexdigest(),
                                   "width": width, "height": height, "file_bytes": len(data)}


def verify_png(data: bytes) -> tuple[int, int]:
    """Validate native SaveImage PNG structure, CRC and bounded decoded scanlines."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("ComfyUI output is not a PNG image")
    offset, compressed = 8, bytearray()
    dimensions = None
    ended = False
    while offset + 12 <= len(data):
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        end = offset + 12 + size
        if end > len(data):
            raise ValueError("truncated PNG")
        kind, payload = data[offset + 4:offset + 8], data[offset + 8:offset + 8 + size]
        crc = struct.unpack(">I", data[end - 4:end])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            raise ValueError("corrupt PNG checksum")
        if dimensions is None and kind != b"IHDR":
            raise ValueError("PNG must start with IHDR")
        if kind == b"IHDR":
            if dimensions is not None or size != 13:
                raise ValueError("invalid PNG header")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            if (not 1 <= width <= 4096 or not 1 <= height <= 4096
                    or depth != 8 or color not in (2, 6) or compression or filtering or interlace):
                raise ValueError("unsupported native SaveImage PNG format")
            dimensions = (width, height)
            stride = width * (3 if color == 2 else 4) + 1
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            if size or end != len(data):
                raise ValueError("invalid PNG end")
            ended = True
            break
        offset = end
    if dimensions is None or not ended or not compressed:
        raise ValueError("incomplete PNG")
    expected = stride * dimensions[1]
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(bytes(compressed), expected + 1)
    except zlib.error as exc:
        raise ValueError("invalid PNG image data") from exc
    if (len(pixels) != expected or not decoder.eof or decoder.unused_data
            or decoder.unconsumed_tail or any(pixels[i] > 4 for i in range(0, expected, stride))):
        raise ValueError("invalid PNG scanlines")
    return dimensions



def image_configuration() -> tuple[str, str] | None:
    url = os.getenv("BOSSMAN_COMFYUI_URL", "").strip()
    checkpoint = os.getenv("BOSSMAN_COMFYUI_CHECKPOINT", "").strip()
    if not url or not checkpoint:
        return None
    local_url(url)
    safe_relative(checkpoint)
    if not checkpoint.endswith(".safetensors"):
        raise ValueError("ComfyUI checkpoint must be an existing .safetensors file")
    return url, checkpoint


def validate_image_spec(spec: dict) -> None:
    """Fail before queueing an image operation the local adapter cannot honor."""
    if spec.get("source_asset_id") is not None or spec.get("reference_asset_ids") or spec.get("kind", "generate") != "generate":
        raise ValueError("ComfyUI adapter currently supports text-to-image; references/editing are not supported")
    for key in ("width", "height"):
        value = spec.get(key, 1024)
        if type(value) is not int or not 256 <= value <= 4096 or value % 8:
            raise ValueError("ComfyUI dimensions must be multiples of 8 in 256..4096")
    steps = spec.get("steps", 30)
    if type(steps) is not int or not 1 <= steps <= 200:
        raise ValueError("ComfyUI steps must be 1..200")


class ComfyUIImageProvider:
    name = "comfyui"

    def __init__(self, base_url: str, checkpoint: str, *, client: ComfyUIClient | None = None,
                 timeout_seconds: float = 600, poll_seconds: float = 1):
        self.client = client or ComfyUIClient(base_url)
        self.checkpoint = checkpoint
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds

    def workflow(self, spec: dict, index: int) -> dict:
        validate_image_spec(spec)
        seed = (int(spec["seed"] if spec.get("seed") is not None else 1) + index) % (2**63)
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": self.checkpoint}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": str(spec.get("prompt", "")), "clip": ["1", 1]}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": str(spec.get("negative_prompt", "")), "clip": ["1", 1]}},
            "4": {"class_type": "EmptyLatentImage", "inputs": {"width": int(spec.get("width", 1024)), "height": int(spec.get("height", 1024)), "batch_size": 1}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0], "seed": seed, "steps": int(spec.get("steps", 30)), "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "BOSSMAN/" + secrets.token_hex(12)}},
        }
        validate_workflow(workflow)
        return workflow

    async def render(self, spec: dict[str, Any], index: int) -> tuple[bytes, str, dict[str, Any]]:
        workflow = self.workflow(spec, index)
        prompt_id = await self.client.submit(workflow)
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            status = await self.client.status(prompt_id)
            if status["state"] == "failed":
                raise RuntimeError(f"ComfyUI execution failed for {prompt_id}")
            if status["state"] == "completed":
                if len(status["outputs"]) != 1:
                    raise ValueError("expected one saved image per Bossman render")
                data, mime, meta = await self.client.download(status["outputs"][0])
                expected = workflow["4"]["inputs"]
                if (meta["width"], meta["height"]) != (expected["width"], expected["height"]):
                    raise ValueError("ComfyUI image dimensions do not match requested output")
                return data, mime, {**meta, "provider": self.name, "mock": False,
                    "prompt_id": prompt_id, "seed": workflow["5"]["inputs"]["seed"],
                    "checkpoint": self.checkpoint, "evidence": "history_success_and_downloaded_png"}
            await asyncio.sleep(self.poll_seconds)
        raise TimeoutError(f"ComfyUI prompt {prompt_id} still pending; it may still run on the local service")
