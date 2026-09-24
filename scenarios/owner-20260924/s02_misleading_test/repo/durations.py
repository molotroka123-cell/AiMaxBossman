"""Parse human durations. Contract: parse_duration returns SECONDS as int."""
from __future__ import annotations
import re

_UNITS = {"h": 3600, "m": 60, "s": 1}


def parse_duration(text: str) -> int:
    """'1h30m' -> 5400, '45s' -> 45, '2m5s' -> 125. Returns seconds."""
    parts = re.findall(r"(\d+)\s*([hms])", text.strip().lower())
    if not parts or "".join(n + u for n, u in parts) != re.sub(r"\s+", "", text.strip().lower()):
        raise ValueError(f"bad duration: {text!r}")
    return sum(int(n) * _UNITS[u] for n, u in parts)
