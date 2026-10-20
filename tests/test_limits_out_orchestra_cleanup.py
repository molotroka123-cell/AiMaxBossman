"""Branch 9 (agents): the limits-out orchestra never leaves a self-repair cycle running unattended and never
starts one after STOP. Offline: Popen, the kill and the clock are fakes; no cycle, model or backend runs."""
from __future__ import annotations

import pytest

from tools import limits_out_orchestra as orch


class FakeProc:
    pid = 4242

    def __init__(self, *a, **k):
        self.returncode = None
        FakeProc.started.append(a[0] if a else None)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    FakeProc.started = []
    killed = []

    def kill(proc):
        killed.append(proc.pid)
        proc.returncode = -9
    monkeypatch.setattr(orch.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(orch, "_kill_tree", kill)
    monkeypatch.setattr(orch, "direct_gen_busy", lambda: [])
    monkeypatch.setattr(orch, "_cancel_task", lambda out_file: "")
    return killed


def _cycle(tmp_path):
    return orch.run_cycle("discovery", None, tmp_path, 60, "py", str(tmp_path))


def test_an_unexpected_exit_from_the_poll_loop_kills_the_cycle_tree(tmp_path, monkeypatch, fakes):
    def interrupted(_s):
        raise KeyboardInterrupt                      # Ctrl+C / service stop while the cycle runs
    monkeypatch.setattr(orch, "stop_requested", lambda: "")
    monkeypatch.setattr(orch.time, "sleep", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _cycle(tmp_path)
    assert FakeProc.started and fakes == [4242]


def test_stop_pressed_just_before_the_start_starts_nothing(tmp_path, monkeypatch, fakes):
    monkeypatch.setattr(orch, "stop_requested", lambda: str(tmp_path / "STOP"))
    out = _cycle(tmp_path)
    assert FakeProc.started == [] and fakes == []
    assert out["interrupted"] == "STOP" and out["rc"] == -2 and out.get("not_started") is True


def test_stop_during_the_cycle_still_kills_and_reports_it(tmp_path, monkeypatch, fakes):
    calls = iter(["", "STOP"])                       # free at the start, STOP at the first poll
    monkeypatch.setattr(orch, "stop_requested", lambda: next(calls, "STOP"))
    monkeypatch.setattr(orch.time, "sleep", lambda _s: None)
    out = _cycle(tmp_path)
    assert fakes and fakes[0] == 4242 and out["interrupted"] == "STOP" and out["rc"] == -2
