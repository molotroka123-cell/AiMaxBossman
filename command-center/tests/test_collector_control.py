"""STOP and PAUSE (bcc/collector/control.py)."""
from __future__ import annotations

import pytest

from bcc.collector.control import RunControl, Stopped


def _no_sleep(_seconds: float) -> None:
    return None


def test_stop_file_raises_stopped(tmp_path):
    control = RunControl(tmp_path, pause_file=tmp_path / "PAUSE", sleep=_no_sleep)
    control.check_stop()  # no STOP yet: must not raise
    control.stop_path.write_text("x", encoding="utf-8")
    with pytest.raises(Stopped):
        control.check_stop()


def test_pause_blocks_until_removed(tmp_path):
    pause_path = tmp_path / "PAUSE"
    pause_path.write_text("paused", encoding="utf-8")
    ticks = {"n": 0}

    def sleep_and_unpause(_seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] >= 3:
            pause_path.unlink(missing_ok=True)

    control = RunControl(tmp_path, pause_file=pause_path, sleep=sleep_and_unpause, poll_s=0.01)
    control.wait_out_pause()          # must return once the file is gone
    assert not control.paused()
    assert ticks["n"] >= 3


def test_stop_wins_even_while_paused(tmp_path):
    pause_path = tmp_path / "PAUSE"
    pause_path.write_text("paused", encoding="utf-8")
    stop_path = tmp_path / "STOP"

    def sleep_then_stop(_seconds: float) -> None:
        stop_path.write_text("x", encoding="utf-8")

    control = RunControl(tmp_path, pause_file=pause_path, sleep=sleep_then_stop, poll_s=0.01)
    with pytest.raises(Stopped):
        control.wait_out_pause()


def test_polite_wait_honours_stop_mid_wait(tmp_path):
    stop_path = tmp_path / "STOP"
    calls = {"n": 0}

    def sleep_then_stop(_seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            stop_path.write_text("x", encoding="utf-8")

    control = RunControl(tmp_path, pause_file=tmp_path / "PAUSE", sleep=sleep_then_stop, poll_s=1.0)
    with pytest.raises(Stopped):
        control.polite_wait(10.0)
    assert calls["n"] == 2


def test_default_pause_path_is_the_owner_rc19_file(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_RC19_PAUSE_FILE", raising=False)
    control = RunControl(tmp_path)
    assert str(control.pause_path) == r"C:\Users\asd\Bossman\rc19-owner-test.PAUSE"
