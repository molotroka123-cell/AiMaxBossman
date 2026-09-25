from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from bcc.telegram_companion.adapters import image_mime, json_request


@dataclass(frozen=True, slots=True)
class StudioImageEditConfig:
    core_url: str
    core_token: str
    model_id: str
    timeout_seconds: float = 900.0

    def __post_init__(self) -> None:
        parsed = urlsplit(self.core_url)
        try:
            loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
            _ = parsed.port
        except ValueError:
            loopback = False
        if parsed.scheme not in {"http", "https"} or not loopback or parsed.username or parsed.password:
            raise ValueError("Studio edit endpoint must be loopback")
        if not self.model_id.strip():
            raise ValueError("Studio image-edit model id required")
        if not 30 <= self.timeout_seconds <= 3600:
            raise ValueError("invalid Studio edit timeout")


@dataclass(frozen=True, slots=True)
class EditedImage:
    data: bytes
    mime: str
    sha256: str
    run_id: str
    job_id: int


class StudioImageEditBroker:
    """PIT image editing through the existing Bossman Studio API.

    This deliberately does not know how Qwen is hosted. Once the owner installs
    a local Qwen-Image-Edit model and registers it in Studio, PIT reuses the
    exact 1.6-compatible reference/job/run surface instead of creating a second
    media engine.
    """

    def __init__(self, config: StudioImageEditConfig, *, transport=None):
        self.config = config
        self.client = httpx.AsyncClient(
            timeout=config.timeout_seconds,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-BCC-Token": self.config.core_token} if self.config.core_token else {}

    def _url(self, path: str) -> str:
        return self.config.core_url.rstrip("/") + path

    async def available(self) -> bool:
        body = await json_request(self.client, "GET", self._url("/api/studio/models"), headers=self._headers)
        rows = body.get("items") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            return False
        return any(
            isinstance(row, dict)
            and row.get("id") == self.config.model_id
            and row.get("available") is True
            for row in rows
        )

    async def edit(
        self,
        *,
        image_bytes: bytes,
        mime: str,
        prompt: str,
        filename: str = "telegram-photo.png",
        settings: dict | None = None,
    ) -> EditedImage:
        if image_mime(image_bytes) != mime:
            raise ValueError("unverified source image")
        if not await self.available():
            raise RuntimeError("QWEN_IMAGE_EDIT_NOT_CONFIGURED")

        reference = await json_request(
            self.client,
            "POST",
            self._url("/api/studio/references"),
            headers=self._headers,
            payload={
                "filename": filename[:120],
                "data_base64": base64.b64encode(image_bytes).decode("ascii"),
            },
            timeout=60,
        )
        run_id = reference.get("id") if isinstance(reference, dict) else None
        if not isinstance(run_id, str) or not run_id:
            raise RuntimeError("Studio reference import failed")

        job = await json_request(
            self.client,
            "POST",
            self._url("/api/studio/jobs"),
            headers=self._headers,
            payload={
                "model": self.config.model_id,
                "prompt": str(prompt or "").strip()[:12000],
                "settings": dict(settings or {}),
                "media": [{"run_id": run_id, "role": "reference"}],
                "count": 1,
            },
            timeout=60,
        )
        job_id = job.get("id") if isinstance(job, dict) else None
        if type(job_id) is not int:
            raise RuntimeError("Studio edit job creation failed")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.timeout_seconds
        while True:
            current = await json_request(
                self.client,
                "GET",
                self._url(f"/api/studio/jobs/{job_id}"),
                headers=self._headers,
                timeout=30,
            )
            status = current.get("status") if isinstance(current, dict) else None
            if status == "completed":
                break
            if status in {"failed", "cancelled", "timeout"}:
                raise RuntimeError(f"Studio image edit ended as {status}")
            if loop.time() >= deadline:
                with __import__("contextlib").suppress(Exception):
                    await json_request(
                        self.client,
                        "POST",
                        self._url(f"/api/studio/jobs/{job_id}/cancel"),
                        headers=self._headers,
                        timeout=20,
                    )
                raise TimeoutError("Studio image edit timeout")
            await asyncio.sleep(0.5)

        runs = await json_request(
            self.client,
            "GET",
            self._url("/api/studio/runs"),
            headers=self._headers,
            params={"job_id": job_id, "surface": "image"},
            timeout=30,
        )
        items = runs.get("items") if isinstance(runs, dict) else None
        if not isinstance(items, list) or not items:
            raise RuntimeError("Studio image edit completed without verified run")
        output = items[0]
        out_id = output.get("id") if isinstance(output, dict) else None
        expected_sha = output.get("sha256") if isinstance(output, dict) else None
        if not isinstance(out_id, str) or not isinstance(expected_sha, str):
            raise RuntimeError("Studio image edit run identity invalid")

        try:
            response = await self.client.get(
                self._url(f"/api/studio/runs/{out_id}/file"),
                headers=self._headers,
                timeout=60,
            )
            response.raise_for_status()
            data = response.content
        except (httpx.HTTPError, OSError, TimeoutError):
            raise RuntimeError("Studio image edit output unavailable") from None

        observed_sha = hashlib.sha256(data).hexdigest()
        observed_mime = image_mime(data)
        if observed_sha != expected_sha or observed_mime not in {"image/png", "image/jpeg", "image/webp"}:
            raise RuntimeError("Studio image edit output verification failed")
        return EditedImage(data, observed_mime, observed_sha, out_id, job_id)
