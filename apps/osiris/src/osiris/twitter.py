"""Twitter/X API slot. Frozen until the owner drops keys. No live calls."""
from __future__ import annotations

FROZEN = True
PROVIDER = "twitter"


def status() -> dict:
    return {
        "provider": PROVIDER,
        "status": "frozen",
        "ready": False,
        "live": False,
        "reason": "awaiting owner API keys; public-tweet read only when thawed",
        "when_thawed": ["public_tweet_read", "public_user_lookup"],
        "level0": "sealed",
        "notes": "no cookies, no login, no DMs, no closed profiles",
    }


def refuse(action: str = "any") -> dict:
    body = status()
    body["refused"] = action
    return body
