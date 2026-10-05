"""Typed observation state. Every field carries value, confidence, frame time and source; UNKNOWN is first-class."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

OK, UNKNOWN, CONFLICT = "OK", "UNKNOWN", "CONFLICT"
STREETS = ("preflop", "flop", "turn", "river")
RANKS = "23456789TJQKA"
SUITS = "shdc"


@dataclass(frozen=True)
class Money:
    """A displayed amount. ``step`` is the display rounding (e.g. 100 for ``50.0K``): two readings agree if within step."""
    amount: float
    step: float = 1.0
    currency: str | None = None
    raw: str = ""

    def agrees(self, other: "Money") -> bool:
        return abs(self.amount - other.amount) <= max(self.step, other.step) / 2 + 1e-9


@dataclass
class Field:
    value: Any = None
    confidence: float = 0.0
    t_ms: int = 0
    source: str = ""
    status: str = UNKNOWN
    reason: str = ""

    @staticmethod
    def ok(value: Any, confidence: float, t_ms: int, source: str) -> "Field":
        return Field(value, float(confidence), int(t_ms), source, OK, "")

    @staticmethod
    def unknown(t_ms: int, source: str, reason: str, confidence: float = 0.0) -> "Field":
        return Field(None, float(confidence), int(t_ms), source, UNKNOWN, reason)

    @property
    def known(self) -> bool:
        return self.status == OK and self.value is not None

    def demote(self, reason: str) -> "Field":
        return Field(None, self.confidence, self.t_ms, self.source, UNKNOWN, reason)


@dataclass
class Seat:
    slot: int
    stack: Field = field(default_factory=Field)
    bet: Field = field(default_factory=Field)
    occupied: Field = field(default_factory=Field)
    dealer: Field = field(default_factory=Field)


@dataclass
class TableState:
    frame_id: str
    t_ms: int
    source: str
    layout: str
    hero_cards: list[Field] = field(default_factory=list)
    board: list[Field] = field(default_factory=list)       # only visible cards, left to right
    board_count: Field = field(default_factory=Field)
    street: Field = field(default_factory=Field)
    pot: Field = field(default_factory=Field)
    to_call: Field = field(default_factory=Field)
    hero_stack: Field = field(default_factory=Field)
    hero_position: Field = field(default_factory=Field)
    dealer_slot: Field = field(default_factory=Field)
    seats: list[Seat] = field(default_factory=list)
    num_seats: Field = field(default_factory=Field)
    hero_turn: Field = field(default_factory=Field)
    actions: Field = field(default_factory=Field)           # [(label, amount|None)]
    quality: dict = field(default_factory=dict)             # blur, animating score, occlusion, stale
    issues: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def card_strings(self, which: str) -> list[str | None]:
        src = self.hero_cards if which == "hero" else self.board
        return [f.value if f.known else None for f in src]


def money_value(f: Field) -> float | None:
    return f.value.amount if f.known and isinstance(f.value, Money) else None
