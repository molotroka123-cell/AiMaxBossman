"""Named Level-1 grants. No global 'allow all'. No silent renewal."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from .policy import ActionClass, LEVEL0, LEVEL1, PolicyDenied, decide, level_of, Level

if TYPE_CHECKING:
    from .store import Store

ISO = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class GrantError(ValueError):
    pass


class GrantBook:
    def __init__(self, store: Store):
        self.store = store

    def issue(
        self,
        *,
        author: str,
        source_or_subject: str,
        reason: str,
        clause: str,
        ttl_hours: int,
    ) -> dict:
        author = (author or "").strip()
        source_or_subject = (source_or_subject or "").strip()
        reason = (reason or "").strip()
        if not author or not source_or_subject or not reason:
            raise GrantError("author, source_or_subject and reason are required")
        try:
            action = ActionClass(clause)
        except ValueError as e:
            raise GrantError(f"unknown clause {clause}") from e
        if action in LEVEL0:
            raise PolicyDenied(action, "level 0 cannot be granted")
        if action not in LEVEL1:
            raise GrantError(f"{action} is not a level-1 clause")
        hours = int(ttl_hours)
        if hours < 1 or hours > 24 * 90:
            raise GrantError("ttl_hours must be 1..2160")
        now = _now()
        rec = {
            "id": "grant-" + uuid.uuid4().hex[:12],
            "author": author,
            "created_at": now.strftime(ISO),
            "expires_at": (now + timedelta(hours=hours)).strftime(ISO),
            "source_or_subject": source_or_subject,
            "reason": reason,
            "clause": action.value,
            "status": "active",
        }
        self.store.grant_put(rec)
        self.store.journal("grant.issued", rec["id"], rec)
        return rec

    def revoke(self, grant_id: str, *, author: str) -> dict:
        rec = self.store.grant_get(grant_id)
        rec["status"] = "revoked"
        rec["revoked_at"] = _now().strftime(ISO)
        rec["revoked_by"] = author
        self.store.grant_put(rec)
        self.store.journal("grant.revoked", rec["id"], rec)
        return rec

    def expire_due(self) -> list[dict]:
        closed = []
        for rec in self.store.grant_list():
            if rec.get("status") != "active":
                continue
            if _parse(rec["expires_at"]) <= _now():
                rec["status"] = "expired"
                self.store.grant_put(rec)
                self.store.journal("grant.expired", rec["id"], {"id": rec["id"]})
                closed.append(rec)
        return closed

    def active_for(self, action: ActionClass, source_or_subject: str) -> dict | None:
        self.expire_due()
        subj = (source_or_subject or "").strip().lower()
        for rec in self.store.grant_list():
            if rec.get("status") != "active":
                continue
            if rec.get("clause") != action.value:
                continue
            if rec.get("source_or_subject", "").strip().lower() != subj:
                continue
            if _parse(rec["expires_at"]) <= _now():
                continue
            return rec
        return None

    def authorize(self, action: ActionClass, source_or_subject: str) -> dict | None:
        lv = level_of(action)
        if lv is Level.L0:
            decide(action, grant_ok=False)
        if lv is Level.L2:
            decide(action, grant_ok=False)
            return None
        grant = self.active_for(action, source_or_subject)
        decide(action, grant_ok=grant is not None)
        return grant
