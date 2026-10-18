"""Owner approval: verification side (what the apply path uses).

A token is bound to ONE draft hash, expires, is single-use, and is stored only as sha256 in
``<root>/owner/tokens.json`` AND as a journal entry ``self_improve.owner.approval`` (so approval is
recorded in the tamper-evident journal). The apply path calls ``verify_and_consume``; it can not issue.
Issuing lives in ``owner.py`` which the loop/proposer modules never import (tested).

Limitation (stated): tokens protect against agents that act through this API and against accidental
apply. They are not a cryptographic boundary against code running as the same OS user that can
rewrite files directly; that needs an OS account or hardware key and is NOT_RUN here.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from ..autonomy.journal import atomic_write_bytes
from .store import Store


class ApprovalRequired(PermissionError):
    pass


def token_hash(token: str) -> str:
    return hashlib.sha256(("bossman-self-improve-owner:" + token).encode("utf-8")).hexdigest()


def _tokens_path(root: str | os.PathLike) -> Path:
    return Path(root) / "owner" / "tokens.json"


def load_tokens(root: str | os.PathLike) -> dict:
    try:
        raw = json.loads(_tokens_path(root).read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def save_tokens(root: str | os.PathLike, data: dict) -> None:
    atomic_write_bytes(_tokens_path(root), json.dumps(data, indent=1, sort_keys=True).encode("utf-8"))


def verify_and_consume(store: Store, root: str | os.PathLike, token: str | None, *, hypothesis_id: str,
                       draft_id: str, draft_sha256: str, now: float | None = None) -> str:
    """Return the token hash on success; otherwise journal a refusal and raise ApprovalRequired."""
    def refuse(why: str) -> None:
        store.record("refusal", {"action": "apply", "hypothesis_id": hypothesis_id, "draft_id": draft_id,
                                 "reason": why})
        raise ApprovalRequired(f"owner approval required: {why}")

    if not token or not isinstance(token, str):
        refuse("no owner approval token presented")
    h = token_hash(token)
    tokens = load_tokens(root)
    rec = tokens.get(h)
    if not rec:
        refuse("unknown token")
    if rec.get("used"):
        refuse("token already used")
    if rec.get("draft_id") != draft_id or rec.get("draft_sha256") != draft_sha256 \
            or rec.get("hypothesis_id") != hypothesis_id:
        refuse("token is bound to a different draft")
    if float(rec.get("expires_at", 0)) < (now if now is not None else time.time()):
        refuse("token expired")
    journaled = [e for e in store.events("owner.approval") if e["kind"] == "self_improve.owner.approval"
                 and e["payload"].get("grant_sha256") == h]
    if not journaled:
        refuse("token is not recorded in the journal")
    rec["used"] = True
    tokens[h] = rec
    save_tokens(root, tokens)
    store.record("owner.approval.consumed", {"grant_sha256": h, "hypothesis_id": hypothesis_id,
                                             "draft_id": draft_id, "draft_sha256": draft_sha256})
    return h
