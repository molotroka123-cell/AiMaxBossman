"""Read-only token holder distribution monitor.

Purpose: tell an owner/analyst HOW CONCENTRATED a token's supply is, as a risk
signal (a few wallets can dump on you). Nothing here hides, splits, disguises or
relocates holdings, and nothing signs or sends anything.

Inputs: a holders list (address, balance, optional first_seen timestamp and
label). Outputs: top-N share, Gini, HHI, effective holder count and the share
held by recently created wallets.

Optional live data: ``ReadOnlyRpcAdapter`` is OFF by default, uses no keys,
allows only an allowlist of read-only JSON-RPC methods, rate-limits requests
and raises if anything else is asked. It cannot sign: there is no key material
and no write method in the allowlist.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .safety import assert_no_live_execution

# Labels that are infrastructure, not "holders" in the risk sense.
INFRA_LABELS = frozenset({"pool", "lp", "burn", "bonding_curve", "program", "exchange"})

READ_ONLY_RPC_METHODS = frozenset({"getTokenSupply", "getTokenLargestAccounts",
                                   "getAccountInfo", "getHealth", "getBlockHeight"})


class HolderMonitorError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Holder:
    address: str
    balance: float
    first_seen_ts: float | None = None   # unix seconds; None = unknown age
    label: str = ""


@dataclass
class ConcentrationReport:
    holders_counted: int
    excluded_infra: int
    supply_counted: float
    top_n_share: dict[int, float]
    gini: float
    hhi: float
    effective_holders: float
    new_wallet_share: float | None       # None if no age data
    new_wallet_window_days: float
    unknown_age_share: float
    risk_level: str
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["top_n_share"] = {str(k): v for k, v in self.top_n_share.items()}
        d["notes"] = list(self.notes)
        return d


def load_holders(path: str | Path) -> list[Holder]:
    """JSON list of {address,balance[,first_seen_ts][,label]}. Local file only."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise HolderMonitorError("holders file must be a JSON list")
    return [Holder(str(r["address"]), float(r["balance"]),
                   (float(r["first_seen_ts"]) if r.get("first_seen_ts") is not None else None),
                   str(r.get("label", ""))) for r in raw]


def gini(balances: Sequence[float]) -> float:
    xs = sorted(b for b in balances if b > 0)
    n = len(xs)
    total = sum(xs)
    if n == 0 or total <= 0:
        return 0.0
    cum = sum((i + 1) * x for i, x in enumerate(xs))
    return (2 * cum) / (n * total) - (n + 1) / n


def hhi(balances: Sequence[float]) -> float:
    """Herfindahl-Hirschman index on shares, range (0, 1]."""
    total = sum(b for b in balances if b > 0)
    if total <= 0:
        return 0.0
    return sum((b / total) ** 2 for b in balances if b > 0)


def analyse(holders: Iterable[Holder], *, now_ts: float | None = None,
            top_ns: Sequence[int] = (1, 5, 10, 20), new_wallet_days: float = 7.0,
            exclude_infra: bool = True) -> ConcentrationReport:
    hs = list(holders)
    if any(h.balance < 0 for h in hs):
        raise HolderMonitorError("negative balance in holders list")
    excluded = [h for h in hs if exclude_infra and h.label.lower() in INFRA_LABELS]
    counted = [h for h in hs if h not in excluded and h.balance > 0]
    bal = sorted((h.balance for h in counted), reverse=True)
    total = sum(bal)
    notes: list[str] = []
    if not counted or total <= 0:
        return ConcentrationReport(0, len(excluded), 0.0, {n: 0.0 for n in top_ns}, 0.0, 0.0,
                                   0.0, None, new_wallet_days, 0.0, "UNKNOWN",
                                   ("no holders with positive balance",))
    top = {n: sum(bal[:n]) / total for n in top_ns}
    h_idx = hhi(bal)
    eff = (1.0 / h_idx) if h_idx > 0 else 0.0
    now = now_ts if now_ts is not None else time.time()
    aged = [h for h in counted if h.first_seen_ts is not None]
    unknown_share = sum(h.balance for h in counted if h.first_seen_ts is None) / total
    new_share: float | None = None
    if aged:
        cutoff = now - new_wallet_days * 86400
        new_share = sum(h.balance for h in aged if h.first_seen_ts >= cutoff) / total
    else:
        notes.append("no wallet age data: new-wallet share unknown")
    if len(counted) < 20:
        notes.append(f"only {len(counted)} holders in the sample: metrics are coarse")
    if excluded:
        notes.append(f"{len(excluded)} infrastructure accounts excluded "
                     "(pool/burn/curve/program/exchange)")
    if unknown_share > 0.2:
        notes.append(f"{unknown_share:.0%} of counted supply has unknown wallet age")
    g = gini(bal)
    t10 = top.get(10, sum(bal[:10]) / total)
    if t10 >= 0.8 or top.get(1, 0) >= 0.4:
        risk = "VERY_HIGH"
    elif t10 >= 0.6 or h_idx >= 0.15:
        risk = "HIGH"
    elif t10 >= 0.4:
        risk = "MODERATE"
    else:
        risk = "LOW"
    return ConcentrationReport(len(counted), len(excluded), total, top, g, h_idx, eff,
                               new_share, new_wallet_days, unknown_share, risk, tuple(notes))


def panel_text(rep: ConcentrationReport) -> str:
    """Dashboard text. Informational risk wording only - never advice on how to
    change what outsiders can see about holdings."""
    lines = ["Holder concentration (read-only, informational)"]
    lines.append(f"Risk level: {rep.risk_level}")
    lines.append("Top holders share of counted supply: " + ", ".join(
        f"top{n}={v:.1%}" for n, v in sorted(rep.top_n_share.items())))
    lines.append(f"Gini {rep.gini:.2f}; HHI {rep.hhi:.3f}; effective holders {rep.effective_holders:.1f}")
    if rep.new_wallet_share is None:
        lines.append("Share held by new wallets: unknown (no age data)")
    else:
        lines.append(f"Share held by wallets younger than {rep.new_wallet_window_days:g} days: "
                     f"{rep.new_wallet_share:.1%}")
    lines.append("High concentration means a few holders can move the price when they sell. "
                 "Treat it as a risk to your position, not as something to adjust.")
    lines.extend(rep.notes)
    return "\n".join(lines)


# ----------------------------------------------------- optional RPC adapter
@dataclass
class ReadOnlyRpcAdapter:
    """Disabled unless constructed with enabled=True AND env BOSSMAN_HOLDER_RPC=1.

    No keys, no headers beyond Content-Type, allowlisted read methods, minimum
    interval between requests. ``transport`` is injectable for tests."""

    endpoint: str = "https://api.mainnet-beta.solana.com"
    enabled: bool = False
    min_interval_s: float = 1.0
    timeout_s: float = 10.0
    transport: Callable[[str, bytes, float], bytes] | None = None
    _last: float = field(default=0.0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    requests_made: int = field(default=0, init=False)

    def _active(self) -> bool:
        return self.enabled and os.environ.get("BOSSMAN_HOLDER_RPC") == "1"

    def _call(self, method: str, params: list) -> dict:
        assert_no_live_execution("holder_rpc_read", stage="report")
        if method not in READ_ONLY_RPC_METHODS:
            raise HolderMonitorError(f"RPC method {method!r} is not in the read-only allowlist")
        if not self._active():
            raise HolderMonitorError("RPC adapter is OFF (enabled=False or "
                                     "BOSSMAN_HOLDER_RPC!=1); using fixtures only")
        with self._lock:
            wait = self.min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                           "params": params}).encode()
        if self.transport is not None:
            raw = self.transport(self.endpoint, body, self.timeout_s)
        else:  # pragma: no cover - network path, exercised only by the owner
            req = urllib.request.Request(self.endpoint, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310
                raw = resp.read()
        self.requests_made += 1
        out = json.loads(raw)
        if "error" in out:
            raise HolderMonitorError(f"RPC error: {out['error']}")
        return out["result"]

    def largest_holders(self, mint: str) -> list[Holder]:
        """Top-20 token accounts (RPC limit). Owner wallets are NOT resolved here,
        so these are token accounts; concentration may be under-stated if one
        owner holds several accounts. That limitation is reported, not hidden."""
        res = self._call("getTokenLargestAccounts", [mint])
        rows = res.get("value", [])
        return [Holder(str(r["address"]), float(r.get("uiAmount") or 0.0)) for r in rows]
