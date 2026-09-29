"""The dial guard. Enforced twice (CallsManager and worker, each with a FRESH settings read).

``check_dial`` raises ``CallError`` (codes from ``types.ERRORS``) or returns the one allowed ``PeerRef``.
There is deliberately no ``peer`` argument: the target always comes from the owner's confirmed settings.
"""
from __future__ import annotations

from typing import Any

from .settings import CallsSettings
from .types import MAX_ALLOWED_PEERS, UNCERTAIN_OUTCOMES, CallError, Outcome, PeerRef


def _is_uncertain(last_outcome: Any) -> bool:
    if last_outcome is None:
        return False
    try:
        return Outcome(getattr(last_outcome, "value", last_outcome)) in UNCERTAIN_OUTCOMES
    except ValueError:
        return True          # an outcome we cannot parse is uncertain by definition


def _stopped(stopflag: Any) -> bool:
    if stopflag is None:
        return False
    return bool(stopflag.is_set() if callable(getattr(stopflag, "is_set", None)) else stopflag)


def check_dial(settings: CallsSettings, account_self_id: int | None, stopflag: Any, last_outcome: Any,
               in_call: bool, confirm_unknown: bool = False) -> PeerRef:
    if not settings.enabled:
        raise CallError("NOT_ENABLED")
    if _stopped(stopflag):
        raise CallError("STOP_ACTIVE")
    if in_call:
        raise CallError("CALL_IN_PROGRESS")
    if account_self_id is None:
        raise CallError("NOT_LOGGED_IN")       # without our own id we cannot prove the peer is not us
    peers = settings.allowed_peers
    if len(peers) > MAX_ALLOWED_PEERS:
        raise CallError("PEER_NOT_ALLOWED")
    if not peers or not settings.peer_confirmed:
        raise CallError("PEER_NOT_SELECTED")
    peer = peers[0]
    if peer.user_id == account_self_id:
        raise CallError("PEER_IS_SELF")
    if _is_uncertain(last_outcome) and confirm_unknown is not True:
        raise CallError("UNCERTAIN_PREVIOUS_CALL")
    return peer
