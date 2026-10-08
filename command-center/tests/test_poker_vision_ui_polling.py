"""Poker Vision page: the periodic polls never overlap (static invariant, no browser needed).

A slow service (the proxy waits up to 20 s) used to stack a new 5-request tick every 700 ms
and a new overlay fetch every 250 ms behind the stuck ones.
"""
from __future__ import annotations

import re
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "ui" / "pages" / "poker_vision.js").read_text(encoding="utf-8")


def test_intervals_run_guarded_callbacks_only():
    intervals = re.findall(r"setInterval\((\w+),\s*(\d+)\)", JS)
    assert ("tickOnce", "700") in intervals and ("overlayOnce", "250") in intervals
    assert ("tick", "700") not in intervals and ("pollOverlay", "250") not in intervals


def test_each_guard_skips_while_busy_and_always_releases():
    for flag, fn in (("ticking", "tick"), ("overlayBusy", "pollOverlay")):
        body = JS.split(f"if ({flag}) return;", 1)
        assert len(body) == 2, f"{flag} guard missing"
        assert f"try {{ await {fn}(); }} finally {{ {flag} = false; }}" in body[1].split("};", 1)[0]
