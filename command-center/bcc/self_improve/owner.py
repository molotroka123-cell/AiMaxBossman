"""OWNER-ONLY: issue an approval token for one reviewed draft.

Run by the human owner from a terminal after reading the draft's diff and the held-out report::

    python -m bcc.self_improve issue-token --root <data>/self_improve --hypothesis H1 --draft D1

This module is deliberately NOT imported by ``loop``, ``proposer``-side code, ``apply`` or the API
feature (a test scans the imports). The token is printed once and only its hash is stored.
"""
from __future__ import annotations

import secrets
import time

from .approval import load_tokens, save_tokens, token_hash
from .drafts import DraftStore
from .limits import Limits
from .store import Store

DEFAULT_TTL_S = 3600


def issue_token(root, *, hypothesis_id: str, draft_id: str, ttl_s: int = DEFAULT_TTL_S, note: str = "") -> str:
    store = Store(root)
    draft, _ = DraftStore(root, Limits()).load(draft_id)
    token = secrets.token_urlsafe(32)
    h = token_hash(token)
    tokens = load_tokens(root)
    tokens[h] = {"hypothesis_id": hypothesis_id, "draft_id": draft_id, "draft_sha256": draft.proposed_sha256,
                 "expires_at": time.time() + max(60, min(int(ttl_s), 86400)), "used": False}
    save_tokens(root, tokens)
    store.record("owner.approval", {"grant_sha256": h, "hypothesis_id": hypothesis_id, "draft_id": draft_id,
                                    "draft_sha256": draft.proposed_sha256, "ttl_s": int(ttl_s), "note": note[:200],
                                    "issued_by": "owner-cli"})
    return token
