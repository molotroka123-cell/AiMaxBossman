"""Global daily cap of the autonomy loop: cycles, agent turns and USD per UTC day.

``<root>/budget.json`` holds the LIMITS and is written by the owner only (it is created
with the conservative defaults when missing; the loop never changes it, and a writer cannot reach it:
it lives outside the repository). ``<root>/budget-usage.json`` holds today's counters.
Every check is journaled (``budget.check``) so the owner can see why a cycle did not start.

Defaults are deliberately small: 3 cycles, 30 agent turns and 0 USD per day. The subscription CLIs cost
no per-token money, so the USD cap only matters for a paid route, which stays off (0).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .journal import atomic_write_bytes, file_lock

DEFAULT_LIMITS = {"cycles_per_day": 3, "turns_per_day": 30, "usd_per_day": 0.0}
LIMIT_KEYS = tuple(DEFAULT_LIMITS)


@dataclass
class Usage:
    day: str
    cycles: int = 0
    turns: int = 0
    usd: float = 0.0


def today(clock: Callable[[], float] = time.time) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(clock()))


class DailyBudget:
    def __init__(self, root: str | os.PathLike, *, journal: Any = None, clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.limits_path = self.root / "budget.json"
        self.usage_path = self.root / "budget-usage.json"
        self.lock_path = self.root / "budget.lock"
        self.journal = journal
        self._clock = clock

    # ------------------------------------------------------------ limits (owner-owned)
    def limits(self) -> dict:
        """The owner's limits. A missing file yields the defaults (and is NOT written by the loop); a malformed
        or negative value falls back to the default for that key (fail closed: never more than the default)."""
        out = dict(DEFAULT_LIMITS)
        try:
            raw = json.loads(self.limits_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return out
        if not isinstance(raw, dict):
            return out
        for key in LIMIT_KEYS:
            v = raw.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                continue
            out[key] = float(v) if key == "usd_per_day" else int(v)
        return out

    # ------------------------------------------------------------ usage
    def usage(self) -> Usage:
        day = today(self._clock)
        try:
            raw = json.loads(self.usage_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("day") == day:
                return Usage(day=day, cycles=int(raw.get("cycles", 0)), turns=int(raw.get("turns", 0)),
                             usd=float(raw.get("usd", 0.0)))
        except (OSError, ValueError, TypeError):
            pass
        return Usage(day=day)

    def _add(self, *, cycles: int = 0, turns: int = 0, usd: float = 0.0) -> Usage:
        with file_lock(self.lock_path):
            u = self.usage()
            u.cycles += int(cycles)
            u.turns += int(turns)
            u.usd = round(u.usd + float(usd), 6)
            atomic_write_bytes(self.usage_path, json.dumps(asdict(u), sort_keys=True).encode("utf-8"))
            return u

    def _log(self, kind: str, payload: dict) -> None:
        if self.journal is not None:
            self.journal.append(kind, payload)

    def snapshot(self) -> dict:
        lim, u = self.limits(), self.usage()
        return {"day": u.day, "limits": lim, "used": {"cycles": u.cycles, "turns": u.turns, "usd": u.usd},
                "remaining": {"cycles": max(0, lim["cycles_per_day"] - u.cycles),
                              "turns": max(0, lim["turns_per_day"] - u.turns),
                              "usd": round(max(0.0, lim["usd_per_day"] - u.usd), 6)}}

    # ------------------------------------------------------------ checks
    def exhausted(self, *, need_turns: int = 0) -> str:
        """Empty string when one more ``need_turns`` fit into today's caps, otherwise the reason."""
        lim, u = self.limits(), self.usage()
        if u.turns + need_turns > lim["turns_per_day"]:
            return f"daily budget: turns/day cap reached ({u.turns}/{lim['turns_per_day']})"
        if u.usd > lim["usd_per_day"] + 1e-9:
            return f"daily budget: usd/day cap reached ({u.usd}/{lim['usd_per_day']})"
        return ""

    def start_cycle(self, goal_id: str) -> str:
        """Count one cycle against today's cap. Returns the refusal reason ('' = started). Journaled."""
        lim, u = self.limits(), self.usage()
        reason = ""
        if u.cycles + 1 > lim["cycles_per_day"]:
            reason = f"daily budget: cycles/day cap reached ({u.cycles}/{lim['cycles_per_day']})"
        else:
            reason = self.exhausted(need_turns=1)
        self._log("budget.check", {"goal_id": goal_id, "kind": "start_cycle", "allowed": not reason,
                                   "reason": reason, "used": {"cycles": u.cycles, "turns": u.turns, "usd": u.usd},
                                   "limits": lim})
        if not reason:
            self._add(cycles=1)
        return reason

    def charge(self, goal_id: str, *, turns: int = 0, usd: float = 0.0) -> Usage:
        u = self._add(turns=turns, usd=usd)
        self._log("budget.charge", {"goal_id": goal_id, "turns": turns, "usd": usd,
                                    "used": {"cycles": u.cycles, "turns": u.turns, "usd": u.usd}})
        return u


__all__ = ["DEFAULT_LIMITS", "DailyBudget", "Usage", "today"]
