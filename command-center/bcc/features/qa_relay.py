"""B2 — the owner-facing half of the cloud-QA bridge.

`bcc/qa_relay.py` holds the contract (signing, replay, allowlist, redaction).
This module is where the owner turns it on, sees what it can be asked for, and
watches what it was asked for.

The endpoints are behind the same local token auth as everything else, so the
"cloud side" in these routes means "whatever the owner pastes or their poller
delivers". Deciding where the queue lives is a deployment question and stays
out of the repository on purpose: the contract is a pure function of
(secret, job), so it is identical over an outbound poll, a file drop or a
manual paste, and nothing here has to change when that is chosen.

The bridge is OFF until the owner turns it on, and a shared secret only exists
once they create one. Both are deliberate: a signed-job bridge that is armed by
default is a remote execution surface nobody asked for.
"""
from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from .. import qa_relay as relay
from ..db import settings_kv
from . import Feature

router = APIRouter(tags=["qa"])

#: One bridge per process. The replay guard and rate limiter are in-process
#: state; a second instance would each hold half the history and neither would
#: catch a replay across them.
BRIDGE = relay.Bridge()


async def _read(svc, key: str) -> str | None:
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == key))).first()
    if row is None or row[0] is None:
        return None
    try:
        return str(svc.vault.decrypt(row[0]))
    except Exception:  # noqa: BLE001 — нечитаемая настройка = не настроено
        return None


async def _write(svc, key: str, value: str) -> None:
    enc = svc.vault.encrypt(value)
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == key))
        await s.execute(sa.insert(settings_kv).values(key=key, value_enc=enc))
        await s.commit()


async def secret_of(svc) -> str:
    return await _read(svc, relay.SECRET_SETTING_KEY) or ""


async def is_enabled(svc) -> bool:
    """Fail-closed: unset, unreadable or anything but an explicit yes is off."""
    value = await _read(svc, relay.ENABLED_SETTING_KEY)
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


async def state(svc) -> dict:
    secret = await secret_of(svc)
    return {"enabled": await is_enabled(svc),
            "configured": bool(secret),
            # The secret itself never appears here — only whether one exists.
            "capabilities": relay.REGISTRY.catalog(),
            "limits": {"max_ttl_seconds": relay.MAX_TTL_SECONDS,
                       "max_evidence_bytes": relay.MAX_EVIDENCE_BYTES,
                       "max_action_seconds": relay.MAX_ACTION_SECONDS,
                       "rate_limit_jobs": relay.RATE_LIMIT_JOBS,
                       "rate_limit_window_seconds": relay.RATE_LIMIT_WINDOW_SECONDS}}


@router.get("/qa-relay")
async def get_state(request: Request):
    return await state(request.app.state.svc)


@router.post("/qa-relay/enable")
async def enable(request: Request):
    """Turn the bridge on. Refuses while no secret exists: an enabled bridge
    with no secret would accept nothing, and reporting it as ready would be a
    lie the owner has to debug later."""
    svc = request.app.state.svc
    if not await secret_of(svc):
        raise HTTPException(409, {"code": "QA_RELAY_NO_SECRET",
                                  "message": "сначала создайте общий секрет моста",
                                  "hint": "POST /api/qa-relay/secret"})
    await _write(svc, relay.ENABLED_SETTING_KEY, "1")
    await svc.bus.emit("qa_relay.enabled", enabled=True)
    return await state(svc)


@router.post("/qa-relay/disable")
async def disable(request: Request):
    """The owner's kill switch. Takes effect on the very next job, including one
    already signed and in flight."""
    svc = request.app.state.svc
    await _write(svc, relay.ENABLED_SETTING_KEY, "0")
    await svc.bus.emit("qa_relay.enabled", enabled=False)
    return await state(svc)


@router.post("/qa-relay/secret")
async def rotate_secret(request: Request):
    """Create or rotate the shared secret.

    Returned exactly once, in the owner's own response to asking for it. It is
    never logged, never echoed in evidence, and never included in `GET
    /qa-relay`. Rotating invalidates every job signed with the old secret,
    which is the intended way to revoke a compromised cloud side."""
    svc = request.app.state.svc
    secret = relay.new_secret()
    await _write(svc, relay.SECRET_SETTING_KEY, secret)
    await svc.bus.emit("qa_relay.secret_rotated")
    return {"secret": secret,
            "note": "секрет показывается один раз; сохраните его на стороне QA"}


@router.post("/qa-relay/job")
async def submit_job(request: Request):
    """Execute one signed QA job and return sanitized evidence.

    Every refusal path returns 200 with a named status rather than an HTTP
    error: the cloud side needs to distinguish "your signature is wrong" from
    "the bridge is off" from "that capability does not exist", and collapsing
    them into 4xx loses exactly the distinction QA is trying to make."""
    svc = request.app.state.svc
    body = await request.json()
    outcome = await BRIDGE.handle(svc, body, secret=await secret_of(svc),
                                  enabled=await is_enabled(svc))
    return outcome.to_dict()


@router.get("/qa-relay/audit")
async def audit(request: Request, limit: int = 100):
    """What the bridge was asked for and what it answered. Accepted and refused
    alike: a bridge whose refusals are invisible cannot be audited."""
    rows = BRIDGE.audit[-max(1, min(int(limit), 500)):]
    return {"entries": list(reversed(rows)), "total": len(BRIDGE.audit)}


FEATURE = Feature(name="qa_relay", router=router)
