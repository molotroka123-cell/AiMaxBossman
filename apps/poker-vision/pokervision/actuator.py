"""Actuator for the owner's OWN trainer only (loopback Playwright page). Never attached to an external client.

Every click is gated: not stopped, adapter allowed to act, committed state unblocked and fresh, hero's turn, action
actually on screen. After a click the state must change; otherwise acting halts (ambiguity => stop)."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .sources import LoopbackBrowserSource, NotLoopback, assert_loopback


class ActionRefused(Exception):
    pass


@dataclass
class ActionLog:
    entries: list = field(default_factory=list)

    def add(self, **kw) -> dict:
        kw["t"] = time.time(); self.entries.append(kw); return kw


class TrainerActuator:
    def __init__(self, source: LoopbackBrowserSource, stop: threading.Event, max_actions: int = 200):
        if not isinstance(source, LoopbackBrowserSource):
            raise ActionRefused("actuator can only drive the loopback trainer source")
        assert_loopback(source.page.url)
        self.src, self.stop, self.max_actions = source, stop, max_actions
        self.log = ActionLog()
        self.halted: str | None = None

    def guard(self, committed, now_ms: int, adapter_caps, stale_ms: int = 1500) -> str | None:
        """Return the reason acting is forbidden right now, else None."""
        if self.stop.is_set():
            return "STOP"
        if self.halted:
            return f"HALTED: {self.halted}"
        if not adapter_caps.act:
            return "adapter has no act capability"
        if len(self.log.entries) >= self.max_actions:
            return "action budget exhausted"
        if committed.blocked:
            return "blocked: " + "; ".join(committed.blocked)
        if committed.hero_turn is not True:
            return "not hero's turn (or unknown)"
        if not all(committed.hero_cards):
            return "hero cards not committed"
        if now_ms - committed.t_ms > stale_ms:
            return "stale committed state"
        return None

    def click(self, label: str) -> bool:
        if self.stop.is_set():
            raise ActionRefused("STOP")
        assert_loopback(self.src.page.url)
        btns = [b for b in self.src.page.query_selector_all("button") if b.inner_text().strip().split("\n")[0] == label]
        if len(btns) != 1:
            raise ActionRefused(f"button {label!r}: {len(btns)} matches on the page")
        btns[0].click()
        self.log.add(action="click", label=label)
        return True

    def halt(self, why: str) -> None:
        self.halted = why
        self.log.add(action="halt", reason=why)
