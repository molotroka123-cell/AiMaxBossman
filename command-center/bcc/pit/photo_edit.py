from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from .capabilities import LAPTOP_IMAGE_GENERATION_REPLY_RU
from .photo_pipeline import PhotoStore
from .studio_image_edit import EditedImage, StudioImageEditBroker
from .vault import PersonaVault


@dataclass(frozen=True, slots=True)
class PhotoEditReply:
    text: str
    image: EditedImage | None


class PhotoEditPipeline:
    """Edit the current participant's latest verified photo through Bossman Studio."""

    def __init__(
        self,
        vault: PersonaVault,
        *,
        broker: StudioImageEditBroker | None,
        ai_max_ready: bool,
        image_use_allowed: bool = True,
        vram_gate: Callable[[], Awaitable[bool]] | None = None,
    ):
        self.vault = vault
        self.store = PhotoStore(vault)
        self.broker = broker
        self.ai_max_ready = bool(ai_max_ready)
        self.image_use_allowed = bool(image_use_allowed)
        self.vram_gate = vram_gate

    async def edit_latest(self, person_key: str, prompt: str) -> PhotoEditReply:
        instruction = str(prompt or "").strip()
        if not instruction:
            return PhotoEditReply("Напиши, что именно изменить на последнем фото.", None)
        unavailable = await self._unavailable()
        if unavailable:
            return PhotoEditReply(unavailable, None)

        asset = self.store.latest(person_key)
        if asset is None:
            return PhotoEditReply("Сначала пришли фото, которое хочешь изменить.", None)

        source = self.store.read_verified(asset)
        references = tuple(
            (self.store.read_verified(ref), ref.mime, ref.path.name)
            for ref in self.store.references(person_key)
        )
        output = await self.broker.edit(
            image_bytes=source,
            mime=asset.mime,
            prompt=instruction,
            filename=asset.path.name,
            settings={},
            references=references,
        )
        return PhotoEditReply("Готово.", output)

    async def edit_current_bytes(self, data: bytes, prompt: str) -> PhotoEditReply:
        """Edit this upload without making it Jeff's durable latest photo."""
        instruction = str(prompt or "").strip()
        if not instruction:
            return PhotoEditReply("Напиши, что именно изменить на фото.", None)
        unavailable = await self._unavailable()
        if unavailable:
            return PhotoEditReply(unavailable, None)
        source, mime = PhotoStore.verify_input(data)
        output = await self.broker.edit(
            image_bytes=source,
            mime=mime,
            prompt=instruction,
            filename="telegram-photo" + PhotoStore._suffix(mime),
            settings={},
            references=(),
        )
        return PhotoEditReply("Готово.", output)

    async def _unavailable(self) -> str | None:
        if not self.ai_max_ready or self.broker is None:
            return LAPTOP_IMAGE_GENERATION_REPLY_RU
        if not self.image_use_allowed:
            return "Локальное редактирование пока не включено для этого режима использования."
        if self.vram_gate is not None and not await self.vram_gate():
            return "Редактирование временно отложено: ресурсы нужны Bossman 1.6."
        return None
