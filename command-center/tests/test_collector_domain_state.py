"""Per-domain politeness delay and daily page cap (bcc/collector/domain_state.py)."""
from __future__ import annotations

from bcc.collector.domain_state import DomainState


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_min_delay_is_enforced(tmp_path):
    clock = FakeClock()
    state = DomainState(tmp_path / "state.json", min_delay_s=8.0, daily_cap=50, clock=clock)
    assert state.check("example.com").ok is True
    state.record_fetch("example.com")
    check = state.check("example.com")
    assert check.ok is True
    assert check.wait_s > 7.9        # must wait out (almost) the full delay
    clock.advance(8.0)
    assert state.check("example.com").wait_s == 0.0


def test_delay_floor_cannot_be_lowered_below_five_seconds(tmp_path):
    state = DomainState(tmp_path / "state.json", min_delay_s=0.5, daily_cap=50)
    assert state.min_delay_s >= 5.0


def test_daily_cap_is_enforced(tmp_path):
    clock = FakeClock()
    state = DomainState(tmp_path / "state.json", min_delay_s=0.0, daily_cap=2, clock=clock)
    state.record_fetch("example.com")
    clock.advance(100)
    state.record_fetch("example.com")
    clock.advance(100)
    check = state.check("example.com")
    assert check.ok is False
    assert "cap" in check.reason.lower()


def test_daily_cap_is_per_domain(tmp_path):
    clock = FakeClock()
    state = DomainState(tmp_path / "state.json", min_delay_s=0.0, daily_cap=1, clock=clock)
    state.record_fetch("a.example")
    assert state.check("a.example").ok is False
    assert state.check("b.example").ok is True


def test_state_persists_across_instances(tmp_path):
    path = tmp_path / "state.json"
    clock = FakeClock()
    state1 = DomainState(path, min_delay_s=0.0, daily_cap=1, clock=clock)
    state1.record_fetch("example.com")
    state2 = DomainState(path, min_delay_s=0.0, daily_cap=1, clock=clock)
    assert state2.check("example.com").ok is False


def test_daily_cap_resets_on_a_new_utc_day(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    state = DomainState(path, min_delay_s=0.0, daily_cap=1)
    state.record_fetch("example.com")
    assert state.check("example.com").ok is False
    # Simulate a new day by rewriting the stored date directly.
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    data["example.com"]["date"] = "2000-01-01"
    path.write_text(json.dumps(data), encoding="utf-8")
    state2 = DomainState(path, min_delay_s=0.0, daily_cap=1)
    assert state2.check("example.com").ok is True
