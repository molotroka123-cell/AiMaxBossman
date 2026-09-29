"""Typed research primitives for rare high-precision long/short setups."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SIDES = ("LONG", "SHORT")
STATUSES = ("CANDIDATE", "VERIFIED_EPISODE", "REJECTED", "NO_SETUP")


@dataclass
class PixelPerfectEpisode:
    source_url: str
    timestamp_s: float
    side: str
    instrument: str
    frame_sha256: str
    entry: float | None = None
    initial_stop: float | None = None
    trigger: str | None = None
    invalidation: str | None = None
    wick_extreme: float | None = None
    levels: dict[str, float] = field(default_factory=dict)
    flow_before: dict[str, Any] = field(default_factory=dict)
    flow_at_trigger: dict[str, Any] = field(default_factory=dict)
    flow_after: dict[str, Any] = field(default_factory=dict)
    outcome: dict[str, Any] = field(default_factory=dict)
    status: str = "CANDIDATE"

    def validate(self) -> list[str]:
        e = []
        if self.side not in SIDES: e.append("side")
        if self.status not in STATUSES: e.append("status")
        if self.timestamp_s < 0 or len(self.frame_sha256) != 64: e.append("evidence")
        if self.status == "VERIFIED_EPISODE":
            if None in (self.entry, self.initial_stop) or not self.trigger or not self.invalidation:
                e.append("verified_without_trade_geometry")
            if not self.outcome:
                e.append("verified_without_outcome")
        return e

    @property
    def risk(self) -> float | None:
        if self.entry is None or self.initial_stop is None:
            return None
        r = abs(self.entry - self.initial_stop)
        return r if r > 0 else None

    def r_multiple(self, price: float) -> float | None:
        r = self.risk
        if r is None or self.entry is None:
            return None
        signed = price - self.entry if self.side == "LONG" else self.entry - price
        return signed / r

    def to_dict(self):
        return asdict(self)


def classify_geometry(*, side: str, entry: float, stop: float, wick: float,
                      max_stop_distance_bps: float = 20.0) -> dict[str, Any]:
    """Geometry filter only; never sufficient to call a setup valid."""
    if side not in SIDES or entry <= 0:
        return {"candidate": False, "reason": "invalid_input"}
    distance_bps = abs(entry - stop) / entry * 10_000
    if distance_bps > max_stop_distance_bps:
        return {"candidate": False, "reason": "stop_too_wide", "stop_distance_bps": distance_bps}
    wick_distance_bps = abs(entry - wick) / entry * 10_000
    return {"candidate": True, "stop_distance_bps": distance_bps,
            "entry_to_wick_bps": wick_distance_bps,
            "note": "geometry_only_requires_structure_flow_and_acceptance"}


def be_policy_outcome(ep: PixelPerfectEpisode, path: list[float], arm_at_r: float) -> dict[str, Any]:
    """Replay one simple BE policy without peeking ahead.

    Stop starts at the original invalidation. Once a past price has reached
    arm_at_r, subsequent samples use entry as the stop. This is research only.
    """
    if ep.risk is None or ep.entry is None or ep.initial_stop is None:
        return {"status": "INSUFFICIENT_GEOMETRY"}
    armed = False
    mfe = float("-inf")
    mae = float("inf")
    for i, price in enumerate(path):
        rm = ep.r_multiple(price)
        mfe, mae = max(mfe, rm), min(mae, rm)
        stop = ep.entry if armed else ep.initial_stop
        hit = price <= stop if ep.side == "LONG" else price >= stop
        if hit:
            return {"status": "BE_STOP" if armed else "INITIAL_STOP", "index": i,
                    "mfe_r": mfe, "mae_r": mae}
        if rm is not None and rm >= arm_at_r:
            armed = True
    return {"status": "OPEN_AT_END", "be_armed": armed, "mfe_r": mfe, "mae_r": mae}
