"""Single choke point for the paper-only invariant of the Solana volume suite.

Owner rule (10.10, master prompt s.7): no live trades, no wallet signing, no token creation/deploy, no fund movement,
no paid requests. Every code path that could produce a SIGNED transaction or reach a swap/quote API must call
`refuse_live_signing()` BEFORE touching a keypair or the network. There is intentionally no flag that turns this off:
going live needs a separate, reviewed change by the owner, not an environment variable.
"""
from __future__ import annotations

from typing import Any, List

from solders.signature import Signature
from solders.transaction import VersionedTransaction


class LiveSigningDisabled(RuntimeError):
    """Raised instead of signing/sending anything: the suite is paper-only."""


def refuse_live_signing(what: str) -> None:
    raise LiveSigningDisabled(
        f"LIVE_SIGNING_DISABLED: {what} refused (paper-only: no wallet signing, no swap API, no token creation)."
    )


def unsigned_paper_transaction(message: Any) -> VersionedTransaction:
    """A structurally valid transaction carrying DEFAULT (all-zero) signatures: it can be inspected and size-checked in
    paper simulation, but it is not signed by any key and cannot be accepted by a cluster."""
    required = int(message.header.num_required_signatures)
    sigs: List[Signature] = [Signature.default()] * max(1, required)
    return VersionedTransaction.populate(message, sigs)
