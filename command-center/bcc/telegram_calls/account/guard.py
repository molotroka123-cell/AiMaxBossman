"""The one place that decides whether a dial may happen. Evaluated by the manager AND again by the worker."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..settings import CallSettings
from ..types import AccountState, CallError, PeerRef


@dataclass(frozen=True)
class DialContext:
    account: AccountState
    me_id: int | None
    active_call: bool
    stop_active: bool           # call-module STOP file OR the global Bossman STOP
    uncertain_previous: bool
    deps_ok: bool = True


def check_dial(settings: CallSettings, ctx: DialContext, *, confirm_unknown: bool = False) -> PeerRef:
    """Return the ONLY peer that may be called, or raise a stable ``CallError``. No peer argument exists on purpose."""
    if not ctx.deps_ok:
        raise CallError("DEPENDENCIES_MISSING")
    if not settings.enabled:
        raise CallError("NOT_ENABLED")
    if ctx.account == AccountState.NO_CREDENTIALS:
        raise CallError("NO_CREDENTIALS")
    if ctx.account != AccountState.READY:
        raise CallError("NOT_LOGGED_IN")
    peer = settings.peer
    if peer is None:
        raise CallError("PEER_NOT_SELECTED")
    if ctx.me_id is not None and peer.user_id == ctx.me_id:
        raise CallError("PEER_IS_SELF")
    if ctx.stop_active:
        raise CallError("STOP_ACTIVE")
    if ctx.active_call:
        raise CallError("CALL_IN_PROGRESS")
    if ctx.uncertain_previous and not confirm_unknown:
        raise CallError("UNCERTAIN_PREVIOUS_CALL")
    return peer


def check_peer_candidate(user: dict[str, Any], me_id: int | None) -> PeerRef:
    """Validate a contact the owner picked as the test peer (from the Telegram dialog/contact list)."""
    uid = user.get("id")
    if type(uid) is not int or uid <= 0:
        raise CallError("PEER_INVALID")
    if me_id is not None and uid == me_id:
        raise CallError("PEER_IS_SELF")
    if user.get("bot") or user.get("deleted") or user.get("is_self") or not user.get("is_user", True):
        raise CallError("PEER_INVALID")
    label = " ".join(str(user.get("label") or "").split())[:120]
    return PeerRef(uid, label)
