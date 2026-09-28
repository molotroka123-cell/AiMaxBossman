"""Per-domain politeness: minimum delay between pages and a daily page cap.

Persisted as one small JSON file per data dir so the cap survives across
separate ``bossman collect`` invocations on the same day — a cap that resets
every process start is not a cap.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass(frozen=True, slots=True)
class DomainCheck:
    ok: bool
    reason: str
    wait_s: float = 0.0


class DomainState:
    """One instance per run, backed by ``<data_dir>/domain_state.json``."""

    def __init__(self, path: Path, *, min_delay_s: float, daily_cap: int, clock=None):
        self.path = Path(path)
        self.min_delay_s = max(config.MIN_DELAY_FLOOR_S, float(min_delay_s))
        self.daily_cap = max(1, min(int(daily_cap), config.DAILY_CAP_CEILING))
        self._clock = clock or (lambda: datetime.now(timezone.utc).timestamp())
        self._state: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def check(self, host: str) -> DomainCheck:
        """Read-only: may this host be fetched right now, or must we wait /
        refuse because today's cap is spent? Does not record anything."""
        row = self._state.get(host) or {}
        today = _today_utc()
        count_today = int(row.get("count", 0)) if row.get("date") == today else 0
        if count_today >= self.daily_cap:
            return DomainCheck(False, f"daily cap reached for {host} "
                               f"({count_today}/{self.daily_cap})")
        last = row.get("last_fetch_epoch")
        if last is not None:
            elapsed = self._clock() - float(last)
            remaining = self.min_delay_s - elapsed
            if remaining > 0:
                return DomainCheck(True, "waiting out the per-domain politeness delay",
                                   wait_s=remaining)
        return DomainCheck(True, "ok")

    def record_fetch(self, host: str) -> None:
        today = _today_utc()
        row = self._state.get(host) or {}
        count_today = int(row.get("count", 0)) if row.get("date") == today else 0
        self._state[host] = {
            "date": today,
            "count": count_today + 1,
            "last_fetch_epoch": self._clock(),
        }
        self._save()

    def counts_today(self, host: str) -> int:
        row = self._state.get(host) or {}
        return int(row.get("count", 0)) if row.get("date") == _today_utc() else 0
