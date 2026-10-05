"""Number / currency / card parsing with explicit failure (None) instead of guessing."""
from __future__ import annotations

import re

from .schema import Money, RANKS, SUITS

_CUR = {"$": "USD", "€": "EUR", "£": "GBP", "₽": "RUB", "¥": "JPY"}
_AMT = re.compile(r"^(?P<cur>[$€£₽¥])?\s*(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac>\d+))?\s*(?P<suf>[KkMm])?$")


def parse_money(text: str) -> Money | None:
    """'1,000' -> 1000; '$50.0K' -> 50000 (step 100); '1.2M' -> 1.2e6 (step 1e5). Malformed -> None.

    Thousands commas must be groups of exactly three digits; a decimal point is only legal before K/M."""
    t = (text or "").strip()
    m = _AMT.match(t)
    if not m:
        return None
    frac, suf = m.group("frac"), (m.group("suf") or "").upper()
    mult = {"": 1, "K": 1_000, "M": 1_000_000}[suf]
    num = float(m.group("num").replace(",", "") + (("." + frac) if frac else ""))
    if frac and not suf:
        return None  # chip counts are integers in the supported layouts; "12.5" without K/M is ambiguous
    step = mult * (10 ** -len(frac)) if frac else (mult if suf else 1)
    return Money(num * mult, float(step), _CUR.get(m.group("cur") or ""), t)


def parse_card(text: str) -> str | None:
    t = (text or "").strip()
    if len(t) == 2 and t[0].upper() in RANKS and t[1].lower() in SUITS:
        return t[0].upper() + t[1].lower()
    return None


def card_label(card: str) -> str:
    return ("10" if card[0] == "T" else card[0]) + {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}[card[1]]
