"""The ONLY thing that crosses from strategy to the clicker: a fully decided, frozen intent. A locator/model may find the element for
``label``; it can neither change the label nor the amount."""
from __future__ import annotations

from dataclasses import dataclass

KINDS = ("FOLD", "CHECK", "CALL", "RAISE")


@dataclass(frozen=True)
class PolicyDecision:
    kind: str                         # one of KINDS
    raise_to: float | None            # chips (units below); only for RAISE
    hand_key: str                     # hero cards + board count when it was decided: a different hand/street invalidates it
    street: str
    t_ms: int                         # frame time of the state it was decided on
    to_call: float | None = None      # amount shown on the CALL button when decided (checked again before the click)
    units: str = "chips"
    reason: str = ""
    size_min: float | None = None     # policy-owned acceptable range for the amount actually set (the panel only offers presets)
    size_max: float | None = None

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown action {self.kind!r}")
        if (self.kind == "RAISE") != (self.raise_to is not None):
            raise ValueError("raise_to is required for RAISE and forbidden otherwise")
        if self.kind == "RAISE" and not (self.size_min is not None and self.size_max is not None and self.size_min <= self.raise_to <= self.size_max):
            raise ValueError("RAISE needs size_min <= raise_to <= size_max")


def hand_key(hero: list, board: list) -> str:
    return "".join(c or "??" for c in hero) + "|" + str(len([c for c in board if c]))
