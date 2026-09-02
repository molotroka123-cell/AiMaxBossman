"""Trusted owner approval issuer (PASS 3, OWNER-AUTH-*).

A model can produce the string ``approver="human:owner"``; it cannot produce an
owner-issued approval. Flow:

    challenge(task, digest, scope)  -> server-side challenge (stored durably)
    redeem(challenge_id, credential) -> credential verified by the EXISTING Bossman
        perimeter (remote_client Principal with the ``approve`` scope), challenge
        consumed once, nonce minted server-side, issued row persisted in the
        DurableSafetyStore -> ApprovalDecision bound to owner + task + digest +
        scope + expiry + nonce.

``ApprovalRegistry(live=True)`` accepts only nonces that exist in the issued table
with identical digest/scope/owner/expiry, and consumes them once (persisted), so
replay after a process restart is refused as well.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

from bossman.company.model import ApprovalDecision

try:  # reuse the Stage 6 perimeter vocabulary; never a parallel auth system
    from ..remote_client.auth import SCOPE_APPROVE
except Exception:  # noqa: BLE001
    SCOPE_APPROVE = "approve"

CHALLENGE_TTL_S = 600.0


class OwnerAuthRefused(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class Challenge:
    challenge_id: str
    task_id: str
    digest: str
    scope: str
    expires_at: float


class OwnerApprovalIssuer:
    """`authenticate(credential)` must return a perimeter Principal (device_id, scopes,
    has_scope) or None. The issuer never sees raw model output; it binds only what
    the server computed (digest/scope) to what the authenticated owner confirmed."""

    def __init__(self, store: Any, *, authenticate: Callable[[str], Any], clock: Callable[[], float] = time.time,
                 approval_ttl_s: float = 900.0, challenge_ttl_s: float = CHALLENGE_TTL_S) -> None:
        if store is None or not hasattr(store, "record_issued_approval"):
            raise OwnerAuthRefused("owner approvals need a DurableSafetyStore")
        self.store, self.authenticate, self.clock = store, authenticate, clock
        self.approval_ttl_s, self.challenge_ttl_s = approval_ttl_s, challenge_ttl_s

    def challenge(self, *, task_id: str, digest: str, scope: str) -> Challenge:
        cid = secrets.token_urlsafe(18)
        exp = self.clock() + self.challenge_ttl_s
        self.store.save_pending_approval(f"challenge:{cid}", {"task_id": task_id, "digest": digest, "scope": scope, "expires_at": exp})
        return Challenge(cid, task_id, digest, scope, exp)

    def redeem(self, challenge_id: str, credential: str) -> ApprovalDecision:
        principal = self.authenticate(credential)
        if principal is None or not getattr(principal, "has_scope", lambda s: False)(SCOPE_APPROVE):
            raise OwnerAuthRefused("credential is not an authenticated owner device with the approve scope")
        payload = self.store.resume_pending_approval(f"challenge:{challenge_id}", consume=True)
        if not payload:
            raise OwnerAuthRefused("unknown or already used challenge")
        if self.clock() >= float(payload["expires_at"]):
            raise OwnerAuthRefused("challenge expired")
        owner = f"human:{getattr(principal, 'device_id', 'owner')}"
        nonce = secrets.token_urlsafe(24)
        expires_at = self.clock() + self.approval_ttl_s
        self.store.record_issued_approval(nonce=nonce, digest=payload["digest"], scope=payload["scope"], owner=owner,
                                          task_id=payload["task_id"], expires_at=expires_at)
        return ApprovalDecision(True, owner, "owner approved via authenticated challenge", digest=payload["digest"],
                                scope=payload["scope"], expires_at=expires_at, nonce=nonce)
