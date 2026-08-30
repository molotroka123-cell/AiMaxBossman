"""State Machine — PLAN → EXECUTE → OBSERVE → VERIFY → RECOVER → DONE."""
from __future__ import annotations

STATE_ORDER = ["PLAN", "EXECUTE", "OBSERVE", "VERIFY", "RECOVER", "DONE"]
_ALLOWED = {
    "PLAN": {"EXECUTE", "RECOVER"},
    "EXECUTE": {"OBSERVE", "RECOVER"},
    "OBSERVE": {"VERIFY", "RECOVER"},
    "VERIFY": {"DONE", "RECOVER", "PLAN"},
    "RECOVER": {"PLAN", "EXECUTE", "DONE"},
    "DONE": set(),
}


class StateMachine:
    def __init__(self, initial: str = "PLAN"):
        if initial not in STATE_ORDER:
            raise ValueError(f"unknown state {initial}")
        self.state = initial
        self.history: list[str] = [initial]

    def can_transition(self, target: str) -> bool:
        return target in _ALLOWED.get(self.state, set())

    def transition(self, target: str) -> str:
        if not self.can_transition(target):
            raise ValueError(f"illegal {self.state} → {target}")
        self.state = target
        self.history.append(target)
        return self.state

    def checkpoint(self) -> dict:
        return {"state": self.state, "history": list(self.history)}

    @classmethod
    def from_checkpoint(cls, data: dict) -> "StateMachine":
        sm = cls(data.get("state", "PLAN"))
        sm.history = list(data.get("history", [sm.state]))
        return sm
