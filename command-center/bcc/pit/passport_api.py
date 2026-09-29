"""Jeff window API for the participant passport (Jeff 1.5).

Registered by ``web.create_app`` with one call. Same commands and gates as Telegram, own
namespace only (key derived from the signed-in user), every action audited by the vault.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import Request

from . import passport_commands as pc

CONSENT_ACTIONS = ("revoke", "personalization_on", "personalization_off")


def register(app: Any, *, rt: Any, own_key: Callable[[int], str], user_of: Callable[..., Any],
             body_json: Callable[..., Any], error: Callable[[int, str], Any]) -> None:
    @app.get("/api/jeff/passport")
    async def passport_get(request: Request):
        uid = user_of(request)
        if uid is None:
            return error(401, "AUTH_REQUIRED")
        return pc.passport_view(rt.vault, own_key(uid), surface="web")

    @app.post("/api/jeff/passport/consent")
    async def passport_consent(request: Request):
        uid = user_of(request)
        if uid is None:
            return error(401, "AUTH_REQUIRED")
        data = await body_json(request)
        action = str(data.get("action", ""))
        if action not in CONSENT_ACTIONS:
            return error(400, "UNKNOWN_ACTION")
        key = own_key(uid)
        if action == "revoke":
            ok, note = pc.revoke_consent(rt.vault, key, surface="web")
            if ok:
                rt.invalidate_memory(key)
        else:
            ok, note = pc.set_personalization(rt.vault, key, action.endswith("_on"), surface="web")
        return {"ok": ok, "note": note, "consent": pc.consent_flags(rt.vault.consent(key))}
