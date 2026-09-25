from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from bcc.telegram_companion.adapters import IMAGE_MAX_BYTES, image_mime

from .collector import HighRecallCollector
from .models import ConsentState, EvidenceKind, MemoryCandidate, Sensitivity
from .vault import PersonaVault, _atomic_json


LAPTOP_PHOTO_REPLY_RU = (
    "Фото получил. Разбирать и редактировать изображения локально я начну после переезда на AI Max 😊"
)

_BLOCKED_VISUAL = re.compile(
    r"\b(?:race|ethnic|relig|politic|diagnos|disease|pregnan|sexual|address|"
    r"раса|этнич|религи|политич|диагноз|болезн|беремен|сексуал|адрес)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class PhotoAsset:
    person_key: str
    message_id: str
    path: Path
    sha256: str
    mime: str
    bytes: int


@dataclass(frozen=True, slots=True)
class PhotoReply:
    text: str
    asset: PhotoAsset | None
    background_memory_scheduled: bool


class VisionBackend(Protocol):
    async def analyze_fast(self, data: bytes, mime: str, user_prompt: str) -> str: ...
    async def analyze_for_memory(self, data: bytes, mime: str, caption: str) -> dict: ...


class PhotoStore:
    """Verified per-participant media storage; never a model-facing filesystem."""

    def __init__(self, vault: PersonaVault):
        self.vault = vault

    def _dir(self, person_key: str) -> Path:
        root = self.vault.ensure(person_key) / "media" / "inbox"
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _suffix(mime: str) -> str:
        return {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[mime]

    def ingest(self, person_key: str, message_id: str | int, data: bytes) -> PhotoAsset:
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError("empty photo")
        if len(data) > IMAGE_MAX_BYTES:
            raise ValueError("photo exceeds 10 MiB")
        raw = bytes(data)
        mime = image_mime(raw)
        if mime is None:
            raise ValueError("unsupported photo bytes")
        digest = hashlib.sha256(raw).hexdigest()
        safe_mid = re.sub(r"[^0-9A-Za-z_-]", "_", str(message_id))[:80] or "photo"
        path = self._dir(person_key) / f"{safe_mid}-{digest[:16]}{self._suffix(mime)}"
        if not path.exists():
            fd, tmp = tempfile.mkstemp(prefix=".photo.", suffix=".tmp", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, path)
            finally:
                Path(tmp).unlink(missing_ok=True)
        asset = PhotoAsset(person_key, str(message_id), path, digest, mime, len(raw))
        _atomic_json(self.vault.ensure(person_key) / "media" / "latest.json", {
            "message_id": asset.message_id,
            "path": asset.path.relative_to(self.vault.person_dir(person_key)).as_posix(),
            "sha256": asset.sha256,
            "mime": asset.mime,
            "bytes": asset.bytes,
            "schema": "bossman.pit.photo/1",
        })
        return asset

    def latest(self, person_key: str) -> PhotoAsset | None:
        path = self.vault.person_dir(person_key) / "media" / "latest.json"
        if not path.is_file():
            return None
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        rel = str(data.get("path", ""))
        candidate = (self.vault.person_dir(person_key) / rel).resolve()
        root = self.vault.person_dir(person_key).resolve()
        if root not in candidate.parents:
            raise ValueError("latest photo escaped participant root")
        return PhotoAsset(
            person_key=person_key,
            message_id=str(data.get("message_id", "")),
            path=candidate,
            sha256=str(data.get("sha256", "")),
            mime=str(data.get("mime", "")),
            bytes=int(data.get("bytes", 0)),
        )

    def read_verified(self, asset: PhotoAsset) -> bytes:
        root = self.vault.person_dir(asset.person_key).resolve()
        path = asset.path.resolve()
        if root not in path.parents:
            raise ValueError("photo escaped participant root")
        data = path.read_bytes()
        if len(data) != asset.bytes or hashlib.sha256(data).hexdigest() != asset.sha256:
            raise ValueError("photo bytes changed")
        if image_mime(data) != asset.mime:
            raise ValueError("photo type changed")
        return data


def visual_memory_candidates(
    *,
    analysis: dict,
    message_id: str,
    source_model: str,
) -> list[MemoryCandidate]:
    """Short-lived neutral visual context; repeated useful facts can be promoted later."""
    rows: list[MemoryCandidate] = []
    scene = str(analysis.get("scene", "")).strip()
    hints = analysis.get("memory_hints", [])
    values = ([scene] if scene else []) + ([str(v).strip() for v in hints] if isinstance(hints, list) else [])
    seen: set[str] = set()
    for index, value in enumerate(values[:8]):
        if not value or value.lower() in seen or _BLOCKED_VISUAL.search(value):
            continue
        seen.add(value.lower())
        rows.append(MemoryCandidate(
            id=f"photo:{message_id}:{index}",
            category="visual_context",
            key="shared_photo_context",
            value=value[:500],
            confidence=0.55,
            evidence_kind=EvidenceKind.INFERRED,
            sensitivity=Sensitivity.NORMAL,
            source_message_id=message_id,
            source_model=source_model,
            ttl_seconds=7 * 24 * 3600,
            utility_score=0.3,
            tags=["photo", "background-vision"],
        ))
    return rows


class PhotoPipeline:
    """Foreground vision + non-blocking background memory enrichment.

    The user's answer waits only for the short foreground vision call. Deeper
    memory analysis is scheduled after it and never blocks Telegram delivery.
    """

    def __init__(
        self,
        vault: PersonaVault,
        *,
        vision: VisionBackend | None,
        source_model: str = "qwen2.5-vl",
        ai_max_ready: bool = False,
        foreground_busy: Callable[[], bool] | None = None,
    ):
        self.vault = vault
        self.store = PhotoStore(vault)
        self.collector = HighRecallCollector(vault)
        self.vision = vision
        self.source_model = source_model
        self.ai_max_ready = bool(ai_max_ready)
        self.foreground_busy = foreground_busy or (lambda: False)
        self._background: set[asyncio.Task] = set()
        self._bg_slots = asyncio.Semaphore(1)

    async def answer_photo(
        self,
        *,
        person_key: str,
        message_id: str | int,
        data: bytes,
        prompt: str = "",
        consent: ConsentState | None = None,
    ) -> PhotoReply:
        asset = self.store.ingest(person_key, message_id, data)
        if not self.ai_max_ready or self.vision is None:
            return PhotoReply(LAPTOP_PHOTO_REPLY_RU, asset, False)

        raw = self.store.read_verified(asset)
        answer = await self.vision.analyze_fast(raw, asset.mime, prompt)
        state = consent if consent is not None else self.vault.consent(person_key)
        scheduled = False
        if state.memory_enabled:
            task = asyncio.create_task(
                self._background_memory(asset, caption=prompt),
                name=f"pit-photo-memory-{asset.message_id}",
            )
            self._background.add(task)
            task.add_done_callback(self._background.discard)
            scheduled = True
        return PhotoReply(answer, asset, scheduled)

    async def _wait_for_idle(self, *, max_wait_seconds: float = 30.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_wait_seconds
        while self.foreground_busy() and loop.time() < deadline:
            await asyncio.sleep(0.1)

    async def _background_memory(self, asset: PhotoAsset, *, caption: str) -> None:
        if self.vision is None:
            return
        async with self._bg_slots:
            await asyncio.sleep(0)
            await self._wait_for_idle()
            # If the foreground is still busy after the bounded wait, skip deep
            # enrichment rather than stealing compute from a live chat.
            if self.foreground_busy():
                return
            raw = await asyncio.to_thread(self.store.read_verified, asset)
            try:
                analysis = await self.vision.analyze_for_memory(raw, asset.mime, caption)
            except (OSError, ValueError, RuntimeError, TimeoutError):
                return
            candidates = visual_memory_candidates(
                analysis=analysis,
                message_id=asset.message_id,
                source_model=self.source_model,
            )
            if candidates:
                self.collector.ingest(asset.person_key, candidates)

    async def drain_background(self, *, timeout: float = 5.0) -> None:
        tasks = tuple(self._background)
        if not tasks:
            return
        try:
            async with asyncio.timeout(timeout):
                await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            for task in tasks:
                task.cancel()
