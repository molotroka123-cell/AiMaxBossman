"""Production composition root for the apprentice safety guards (PASS 3).

LIVE mode: DurableSafetyStore is mandatory for the ledger, the approval registry
and the owner issuer — no caller can forget to inject it because there is no
LIVE constructor without it. SIMULATED mode keeps the in-memory fallback.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .durable import DurableSafetyStore
from .guards import ApprovalRegistry, DurableRequired, SideEffectLedger
from .owner_auth import OwnerApprovalIssuer

MODES = ("SIMULATED", "LIVE")


@dataclass(slots=True)
class Guards:
    mode: str
    ledger: SideEffectLedger
    approvals: ApprovalRegistry
    store: Any | None = None
    issuer: OwnerApprovalIssuer | None = None

    def outreach_gate(self, **kw: Any):
        """OutreachGate bound to these guards; LIVE guards yield a LIVE gate (no memory fallback)."""
        from .outreach import OutreachGate  # noqa: WPS433
        return OutreachGate(ledger=self.ledger, approvals=self.approvals, mode=self.mode, **kw)


def build_guards(mode: str, *, store_path: str | Path | None = None, store: Any | None = None,
                 authenticate: Callable[[str], Any] | None = None, clock: Callable[[], float] = time.time) -> Guards:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if mode == "SIMULATED":
        return Guards("SIMULATED", SideEffectLedger(store), ApprovalRegistry(clock, store), store, None)
    if store is None:
        if store_path is None:
            raise DurableRequired("LIVE guards need store_path or an open DurableSafetyStore")
        store = DurableSafetyStore(store_path, clock=clock)
    if authenticate is None:
        raise DurableRequired("LIVE guards need an owner authenticator (perimeter principal with the approve scope)")
    return Guards("LIVE", SideEffectLedger(store, live=True), ApprovalRegistry(clock, store, live=True), store,
                  OwnerApprovalIssuer(store, authenticate=authenticate, clock=clock))
