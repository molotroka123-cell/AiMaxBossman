"""Real engines for a call, built on the EXISTING Jeff/Bossman voice tract and local model routes (placeholder until wired)."""
from __future__ import annotations

from ..settings import CallSettings
from ..types import CallError


async def build_jeff_engines(settings: CallSettings):
    raise CallError("STT_UNAVAILABLE", detail="jeff_engines_not_wired")
