"""Stable prefix for cloud prompt-cache. Local runtime does not use this."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

PROMPT_PATH = Path(__file__).resolve().parents[2] / "OSIRIS_DATA_ACQUISITION_PROMPT.md"


def stable_prefix() -> str:
    if PROMPT_PATH.is_file():
        return PROMPT_PATH.read_text(encoding="utf-8")
    return "OSIRIS: public sources only. Level 0 sealed. Fact without passport is discarded.\n"


def cache_key(prefix: str | None = None) -> str:
    body = prefix if prefix is not None else stable_prefix()
    return sha256(body.encode("utf-8")).hexdigest()


def wrap_cloud(user: str) -> dict:
    prefix = stable_prefix()
    return {
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
        "stable_prefix": prefix,
        "cache_key": cache_key(prefix),
        "user": user,
        "applied": True,
    }
