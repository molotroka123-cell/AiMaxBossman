"""Owner STOP across a backend restart, and what recovery may NOT do to it.

Three claims are checked, each against a restart on the SAME data dir with a
fresh Services (the crash-and-come-back case), fake desktop only:

  1. STOP survives the restart.
  2. Recovery never lifts STOP by itself — observing, reading status, or simply
     coming back up must leave it set until the owner presses "Продолжить".
  3. An unknown outcome (a hung adapter) is verified before anything is retried,
     and that fact must survive the restart too: a backend killed while an
     action's outcome was unknown is exactly when the state matters most.

CONTRACT/MOCK: nothing here touches a real desktop.
"""
from __future__ import annotations

import pytest

from bcc.features import tools_computer as tc

from .conftest import client_for, make_settings, start_app
from .test_computer_use_tools import FakeDesktop, FakeShots

pytest.importorskip("bossman.computer_operator.models")
pytestmark = pytest.mark.timeout(180)


def _attach(svc, settings):
    """A fresh in-process ComputerState the way _state()/_owner_state() build it."""
    st = tc._owner_state(svc)
    st.desktop, st.shots = FakeDesktop(), FakeShots()
    st.desktop.set_interrupt(st.stop)
    return st


async def test_stop_survives_restart_and_recovery_never_lifts_it(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "availability", lambda: (True, ""))
    settings = make_settings(tmp_path)
    stop_file = settings.data_dir / "computer" / tc.STOP_FILE

    app, svc = await start_app(settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            _attach(svc, settings)
            assert (await client.post("/api/computer/stop")).json() == {"stopped": True, "persisted": True}
            assert stop_file.is_file()
    finally:
        await svc.stop()

    # ---- restart on the same data dir: a new process, nothing in memory
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            assert (await client.get("/api/computer/status")).json()["stopped"] is True
            st = _attach(svc, settings)
            assert st.stopped(), "STOP did not survive the restart"

            # Recovery reads the screen. Reading is allowed; lifting STOP is not.
            obs = await tc.observe(svc)
            assert obs["stopped"] is True
            assert st.stopped() and stop_file.is_file(), "an observation lifted the owner's STOP"
            assert (await client.get("/api/computer/status")).json()["stopped"] is True
            with pytest.raises(tc.ActRefused, match="Стоп"):
                await tc.act(svc, {"action": "type", "generation": obs["generation"], "text": "x"})
            assert st.desktop.executed == []

            # Only the owner's own Resume clears it (negative control).
            assert (await client.post("/api/computer/resume")).json()["stopped"] is False
            assert not stop_file.exists() and not st.stopped()
    finally:
        await svc.stop()


async def test_an_unknown_outcome_is_still_unknown_after_a_restart(tmp_path, monkeypatch):
    """Regression: the adapter hung, so the last action's effect on the desktop is unknown.

    In-process this correctly blocks every further action until the screen is re-read.
    The fact was in-memory only, so a backend that died right there came back believing
    the desktop was in a known state — and `launch`/`wait`, which carry no observation
    generation, would run straight away with no check of what the hung action did.
    """
    monkeypatch.setattr(tc, "availability", lambda: (True, ""))
    monkeypatch.setattr(tc, "ACT_TIMEOUT_S", 0.2)
    monkeypatch.setattr(tc, "SETTLE_S", 0)
    settings = make_settings(tmp_path)

    app, svc = await start_app(settings, start_workers=False)
    try:
        st = _attach(svc, settings)
        obs = await tc.observe(svc)
        st.desktop.hang = True
        with pytest.raises(tc.ActRefused, match="исход неизвестен"):
            await tc.act(svc, {"action": "type", "generation": obs["generation"], "text": "delete all"})
        assert st.outcome_unknown, "a hung adapter did not mark the outcome unknown"
        # in-process the block is real, for every kind including the ones without a generation
        with pytest.raises(tc.ActRefused, match="исход прошлого действия неизвестен"):
            await tc.act(svc, {"action": "launch", "target": "notepad"})
    finally:
        await svc.stop()

    app, svc = await start_app(settings, start_workers=False)
    try:
        st = _attach(svc, settings)
        assert st.outcome_unknown, (
            "restart amnesia: the backend came back believing the last action's outcome "
            "was known; nothing forces the consequences to be checked before the retry")
        assert (await tc_status(svc))["outcome_unknown"]
        with pytest.raises(tc.ActRefused, match="исход прошлого действия неизвестен"):
            await tc.act(svc, {"action": "launch", "target": "notepad"})
        # Re-reading the screen is the sanctioned way out, and only that.
        await tc.observe(svc)
        assert not st.outcome_unknown
    finally:
        await svc.stop()


async def tc_status(svc) -> dict:
    st = getattr(svc, "_computer_state", None)
    return {"outcome_unknown": (st.outcome_unknown if st else "") or None}
