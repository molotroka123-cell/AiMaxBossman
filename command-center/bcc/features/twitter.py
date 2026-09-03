"""Twitter/X API slot. Frozen until the owner drops keys. No live calls."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import Feature

router = APIRouter()

STATUS = {
    "provider": "twitter",
    "status": "frozen",
    "ready": False,
    "live": False,
    "reason": "awaiting owner API keys; public-tweet read only when thawed",
    "when_thawed": ["public_tweet_read", "public_user_lookup"],
    "level0": "sealed",
    "notes": "no cookies, no login, no DMs, no closed profiles",
}


@router.get("/twitter/status")
async def twitter_status():
    return dict(STATUS)


@router.post("/twitter/lookup")
async def twitter_lookup():
    raise HTTPException(status_code=423, detail=dict(STATUS))


FEATURE = Feature(name="twitter", router=router)
