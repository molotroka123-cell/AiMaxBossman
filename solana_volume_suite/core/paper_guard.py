"""Single choke point for the paper-only invariant of the Solana volume suite.

Owner rule (10.10, master prompt s.7): no live trades, no wallet signing, no token creation/deploy, no fund movement,
no paid requests. The code that built transactions (Pump.fun instructions/bundles, Jupiter swap/sign helpers, v0 compilation)
was removed on 2026-10-11 (audit H4); any future code path that could produce a SIGNED transaction or reach a
swap/quote API must call `refuse_live_signing()` BEFORE touching a keypair or the network. There is intentionally no flag that turns this off:
going live needs a separate, reviewed change by the owner, not an environment variable.
"""
from __future__ import annotations


class LiveSigningDisabled(RuntimeError):
    """Raised instead of signing/sending anything: the suite is paper-only."""


def refuse_live_signing(what: str) -> None:
    raise LiveSigningDisabled(
        f"LIVE_SIGNING_DISABLED: {what} refused (paper-only: no wallet signing, no swap API, no token creation)."
    )
