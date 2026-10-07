"""ComfyUI video client: reuses bcc.oss.comfyui (loopback pin, safe paths, bounded JSON).

Differences from the image client, all deliberate:
  * the workflow is a template the owner installed (video custom nodes), so the
    image-only SAFE_NODES list is not applied; size/shape are still bounded;
  * STOP interrupts THIS prompt only (/interrupt with prompt_id; queued prompts
    are removed via /queue delete), never a blind global interrupt;
  * progress comes only from ComfyUI's own websocket `progress` messages.
    If websockets is missing or the socket fails, progress is None (honest).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

import httpx

from ..oss.comfyui import ComfyUIClient, safe_relative

MAX_VIDEO_BYTES = 4 * 1024 * 1024 * 1024
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
VIDEO_KEYS = ("videos", "gifs", "images")
VIDEO_SUFFIX = (".mp4", ".webm", ".mkv", ".mov", ".webp", ".gif")


def validate_template(workflow: Any) -> None:
    if not isinstance(workflow, dict) or not 1 <= len(workflow) <= 256:
        raise ValueError("workflow template must be a bounded ComfyUI API graph")
    if len(json.dumps(workflow)) > 512 * 1024:
        raise ValueError("workflow template too large")
    for node_id, node in workflow.items():
        if not re.fullmatch(r"[0-9]+", str(node_id)) or not isinstance(node, dict) \
                or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict):
            raise ValueError("invalid workflow node")


def apply_ws_message(state: dict[str, dict], message: dict) -> None:
    """Fold one ComfyUI websocket message into per-prompt progress state."""
    data = message.get("data") if isinstance(message, dict) else None
    if not isinstance(data, dict):
        return
    pid = data.get("prompt_id")
    if not isinstance(pid, str):
        return
    if message.get("type") == "progress":
        value, maximum = data.get("value"), data.get("max")
        if isinstance(value, int) and isinstance(maximum, int) and maximum > 0 and 0 <= value <= maximum:
            state[pid] = {"value": value, "max": maximum}


def classify_error(text: str) -> str:
    low = (text or "").lower()
    if "out of memory" in low or "oom" in low.split():
        return "out_of_memory"
    return "runtime_error"


class ComfyUIVideoClient(ComfyUIClient):
    def __init__(self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(base_url, transport=transport)
        self._ws_state: dict[str, dict] = {}
        self._ws_task: asyncio.Task | None = None
        self._ws_client_id = uuid.uuid4().hex

    async def upload(self, name: str, data: bytes) -> str:
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("input file exceeds 64 MiB")
        safe_relative(name, filename=True)
        async with self._client() as client:
            r = await client.post("/upload/image", files={"image": (name, data)},
                                  data={"overwrite": "true", "type": "input"})
            r.raise_for_status()
            stored = r.json().get("name")
        return safe_relative(stored if isinstance(stored, str) else name, filename=True)

    async def submit_video(self, workflow: dict, client_id: str) -> str:
        validate_template(workflow)
        await self._ensure_ws()
        result = await self._json("POST", "/prompt", json={"prompt": workflow, "client_id": self._ws_client_id})
        prompt_id = result.get("prompt_id")
        if result.get("error") or result.get("node_errors") or not isinstance(prompt_id, str):
            detail = json.dumps(result.get("node_errors") or result.get("error") or result)[:600]
            raise ValueError(f"ComfyUI rejected the workflow: {detail}")
        self._prompt_id(prompt_id)
        return prompt_id

    async def poll(self, prompt_id: str) -> dict:
        self._prompt_id(prompt_id)
        history = (await self._json("GET", f"/history/{prompt_id}")).get(prompt_id)
        if history is None:
            return {"state": "running" if await self._is_running(prompt_id) else "pending",
                    "outputs": [], "error": None}
        status = history.get("status") if isinstance(history, dict) else None
        if not isinstance(status, dict):
            raise ValueError("invalid ComfyUI execution history")
        if status.get("status_str") == "error":
            return {"state": "failed", "outputs": [], "error": _history_error(status)}
        if status.get("completed") is not True:
            return {"state": "running", "outputs": [], "error": None}
        outputs = []
        for node in (history.get("outputs") or {}).values():
            for key in VIDEO_KEYS:
                for d in node.get(key, []) if isinstance(node, dict) else []:
                    if isinstance(d, dict) and d.get("type") == "output" \
                            and str(d.get("filename", "")).lower().endswith(VIDEO_SUFFIX):
                        safe_relative(d["filename"], filename=True)
                        safe_relative(d.get("subfolder", ""))
                        outputs.append(d)
        if not outputs:
            return {"state": "failed", "outputs": [], "error": "ComfyUI finished but saved no video file"}
        return {"state": "completed", "outputs": outputs, "error": None}

    async def _is_running(self, prompt_id: str) -> bool:
        queue = await self._json("GET", "/queue")
        return any(isinstance(item, list) and len(item) > 1 and item[1] == prompt_id
                   for item in queue.get("queue_running", []))

    async def progress(self, prompt_id: str) -> dict | None:
        return self._ws_state.get(prompt_id)

    async def download(self, descriptor: dict, dest: Path) -> int:
        params = {"filename": safe_relative(descriptor.get("filename"), filename=True),
                  "subfolder": safe_relative(descriptor.get("subfolder", "")), "type": "output"}
        total = 0
        tmp = dest.with_suffix(dest.suffix + ".part")
        async with self._client() as client:
            async with client.stream("GET", "/view", params=params, timeout=None) as response:
                response.raise_for_status()
                with tmp.open("wb") as fh:
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_VIDEO_BYTES:
                            raise ValueError("video exceeds 4 GiB limit")
                        fh.write(chunk)
        tmp.replace(dest)
        return total

    async def interrupt(self, prompt_id: str) -> None:
        self._prompt_id(prompt_id)
        async with self._client() as client:
            # remove it if it has not started; otherwise interrupt only this prompt
            await client.post("/queue", json={"delete": [prompt_id]})
            r = await client.post("/interrupt", json={"prompt_id": prompt_id})
            r.raise_for_status()

    async def _ensure_ws(self) -> None:
        if self._ws_task and not self._ws_task.done():
            return
        try:
            import websockets  # optional dependency: absent means no step progress
        except ImportError:
            return
        url = self.base_url.replace("http://", "ws://") + f"/ws?clientId={self._ws_client_id}"

        async def pump():
            with contextlib.suppress(Exception):
                async with websockets.connect(url, max_size=4 * 1024 * 1024) as ws:
                    async for raw in ws:
                        if isinstance(raw, str):
                            with contextlib.suppress(ValueError):
                                apply_ws_message(self._ws_state, json.loads(raw))
        self._ws_task = asyncio.create_task(pump(), name="direct-gen-ws")

    async def close(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._ws_task


def _history_error(status: dict) -> str:
    for item in status.get("messages", []) or []:
        if isinstance(item, list) and len(item) == 2 and item[0] == "execution_error" and isinstance(item[1], dict):
            data = item[1]
            return str(data.get("exception_message") or data.get("exception_type") or "execution error")[:800]
    return "ComfyUI reported an execution error"
