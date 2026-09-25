from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CapabilityState(StrEnum):
    REQUIRED = "required"
    AI_MAX_ONLY = "ai_max_only"
    NEVER_PARTICIPANT = "never_participant"


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    state: CapabilityState
    description: str


JEFF_CAPABILITIES = (
    Capability("general_chat", CapabilityState.REQUIRED, "general multilingual assistant chat"),
    Capability("web_search", CapabilityState.REQUIRED, "fresh public web search/read with sources"),
    Capability("vision", CapabilityState.REQUIRED, "understand participant-uploaded images"),
    Capability("file_understanding", CapabilityState.REQUIRED, "analyze participant-uploaded safe files"),
    Capability("code_reasoning", CapabilityState.REQUIRED, "explain/review/write code without host execution authority"),
    Capability("calculator", CapabilityState.REQUIRED, "deterministic calculations"),
    Capability("summarization", CapabilityState.REQUIRED, "summaries and transformations"),
    Capability("translation", CapabilityState.REQUIRED, "multilingual translation"),
    Capability("personal_memory", CapabilityState.REQUIRED, "Bossman-managed per-ID personalization"),
    Capability("web_citations", CapabilityState.REQUIRED, "surface sources for web-derived answers"),
    Capability("image_generation", CapabilityState.AI_MAX_ONLY, "local image generation after AI Max migration"),
    Capability("computer_control", CapabilityState.NEVER_PARTICIPANT, "intentionally unavailable to PIT participants"),
    Capability("shell", CapabilityState.NEVER_PARTICIPANT, "intentionally unavailable to PIT participants"),
)


LAPTOP_IMAGE_GENERATION_REPLY_RU = "Скоро научусь, малышка 😊"


def image_generation_reply(*, ai_max_image_generation_ready: bool) -> str | None:
    """Return a laptop placeholder; None means the real image path may proceed."""
    return None if ai_max_image_generation_ready else LAPTOP_IMAGE_GENERATION_REPLY_RU
