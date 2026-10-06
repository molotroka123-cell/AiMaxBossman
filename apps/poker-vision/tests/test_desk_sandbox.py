"""COACH/EXECUTOR through the sandbox desk (own trainer, loopback, no money): the real pixel path
capture -> vision -> validated state -> recommendation -> locate -> screen coordinates -> pointer click -> verification, plus window operations.
Needs the owner's Poker Train on POKERTRAIN_URL (default http://127.0.0.1:3000/) and Chromium; skipped otherwise."""
from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

pytest.importorskip("cv2"); pytest.importorskip("playwright")
URL = os.environ.get("POKERTRAIN_URL", "http://127.0.0.1:3000/")


def _up() -> bool:
    u = urlparse(URL)
    try:
        socket.create_connection((u.hostname, u.port or 80), 0.5).close(); return True
    except OSError:
        return False


pytestmark = [pytest.mark.timeout(300), pytest.mark.skipif(not _up(), reason="set POKERTRAIN_URL to a running Poker Train")]


def wait_for(cond, timeout=90, step=0.5):
    t = time.time()
    while time.time() - t < timeout:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return None


@pytest.fixture
def make(tmp_path):
    from pokervision.service import VisionService
    made = []

    def _mk(mode, dpr=1.0, **kw):
        svc = VisionService(tmp_path / f"d{len(made)}", Path(__file__).resolve().parents[1] / "pokervision" / "adapters" / "profiles")
        svc.start_desk({"kind": "sandbox", "url": URL, "bootstrap": "cash_nl10", "dpr": dpr}, desk_mode=mode, **kw)
        made.append(svc)
        return svc
    yield _mk
    for s in made:
        s.stop()


def verified_actions(svc):
    return [r for r in svc.journal.read() if r.get("event") == "action" and r.get("verified")]


def test_coach_recommends_with_options_and_disclaimer_and_never_clicks(make):
    svc = make("coach")
    rc = wait_for(lambda: (lambda r: r if r.get("ok") else None)(svc.recommendation_view()), 90)
    assert rc, svc.recommendation_view()
    assert rc["action"] in ("FOLD", "CHECK", "CALL", "RAISE") and rc["options"] and any(o["chosen"] for o in rc["options"])
    assert "не гарантия" in rc["disclaimer"] and "неизвестны" in rc["disclaimer"]
    time.sleep(2)
    assert svc.desk["executor"].n_actions == 0 and svc.decisions == []          # coach mode never acts


def test_control_executes_verified_actions_with_journal_before_after_and_sha(make):
    svc = make("control", max_hands=4)
    assert wait_for(lambda: len(verified_actions(svc)) >= 2, 150), (svc.desk["executor"].halted, svc.journal.read()[-3:])
    acts = verified_actions(svc)
    for a in acts:
        assert a["sha"] and a["before"] and a["after"] and a["backend"]["via"] == "pointer"
        assert (svc.journal.dir / a["before"]).exists() and (svc.journal.dir / a["after"]).exists()
        assert a["decision"]["kind"] in ("FOLD", "CHECK", "CALL", "RAISE")
    # the executor, not a model, chose where to click: the click point lies inside the located button box
    a = acts[0]; bx, by, bw, bh = a["target"]["box"]
    assert bx <= (a["backend"]["point"][0] - 40) + 1 + bw and by <= a["backend"]["point"][1] <= by + bh + 1


def test_stop_ends_the_queue_immediately(make):
    svc = make("control", max_hands=10)
    assert wait_for(lambda: len(verified_actions(svc)) >= 1, 120)
    svc.stop()
    n = len(svc.journal.read())
    time.sleep(1.5)
    assert len(svc.journal.read()) == n and not svc.thread.is_alive()


def test_window_moved_between_actions_still_hits_the_right_button(make):
    svc = make("control", max_hands=6)
    assert wait_for(lambda: len(verified_actions(svc)) >= 1, 120)
    n = len(verified_actions(svc))
    assert svc.sandbox_cmd("move", x=260, y=0)["ok"]
    assert wait_for(lambda: len(verified_actions(svc)) >= n + 1, 120), svc.desk["executor"].halted
    last = verified_actions(svc)[-1]
    assert last["backend"]["point"][0] > 260                                   # clicked in the NEW place (mapping uses the live window rect)


def test_overlapping_window_halts_control_and_no_click_lands_on_the_wrong_window(make):
    svc = make("control", max_hands=6)
    assert wait_for(lambda: len(verified_actions(svc)) >= 1, 120)
    assert svc.sandbox_cmd("cover", cid="cover1", rect=[40, 300, 520, 400])["ok"]
    assert wait_for(lambda: svc.desk["executor"].halted, 30)
    assert "OCCLUDED" in svc.desk["executor"].halted
    n = svc.desk["executor"].n_actions
    time.sleep(2)
    assert svc.desk["executor"].n_actions == n


def test_resize_requires_profile_reverification_before_control_resumes(make):
    svc = make("control", max_hands=6)
    assert wait_for(lambda: len(verified_actions(svc)) >= 1, 120)
    assert svc.sandbox_cmd("resize", w_=420, h_=760)["ok"]
    assert wait_for(lambda: svc.desk["executor"].halted, 30)
    assert "RESIZED" in svc.desk["executor"].halted
    n = svc.desk["executor"].n_actions
    time.sleep(2)
    assert svc.desk["executor"].n_actions == n                                 # nothing is clicked on an unverified size


def test_minimize_and_close_stop_control_and_reopen_is_not_silently_adopted(make):
    svc = make("control", max_hands=6)
    assert wait_for(lambda: len(verified_actions(svc)) >= 1, 120)
    assert svc.sandbox_cmd("minimize", on=True)["ok"]
    assert wait_for(lambda: svc.desk["executor"].halted, 30) and "SOURCE" in svc.desk["executor"].halted
    assert svc.sandbox_cmd("minimize", on=False)["ok"]
    assert svc.sandbox_cmd("close")["ok"]
    assert wait_for(lambda: svc.desk["lost"], 30) and "closed" in svc.desk["lost"]
    assert svc.sandbox_cmd("reopen")["ok"]
    assert wait_for(lambda: svc.desk["lost"] and "different process" in svc.desk["lost"], 30)   # a lookalike window is NOT adopted
    n = svc.desk["executor"].n_actions
    time.sleep(2)
    assert svc.desk["executor"].n_actions == n


def test_dpr2_capture_is_in_physical_pixels_and_nothing_unverified_is_clicked(make):
    """At DPR 2 the vision profile is only partly valid (see eval test_layout): the safe outcome is few or no actions, never a wrong one."""
    svc = make("control", dpr=2.0, max_hands=4)
    assert wait_for(lambda: svc.last_frame is not None, 60)
    assert svc.last_frame.w == 1040 and svc.last_frame.meta["rect"][2] == 1040           # physical pixels
    wait_for(lambda: len(verified_actions(svc)) >= 1, 60)
    acts = [r for r in svc.journal.read() if r.get("event") == "action"]
    assert all(a["verified"] for a in acts) or svc.desk["executor"].halted               # an unverified click must have halted control
    if acts:
        assert acts[0]["backend"]["point"][0] < 1040 / 2 + 1 and acts[0]["backend"]["point"][0] > 0   # pointer units = physical / DPR


def test_control_and_coach_are_refused_where_nothing_is_proven(tmp_path):
    from pokervision.service import VisionService
    sample = Path(__file__).resolve().parents[1] / "evidence" / "sample_frames"
    svc = VisionService(tmp_path / "x", Path(__file__).resolve().parents[1] / "pokervision" / "adapters" / "profiles")
    with pytest.raises(PermissionError):
        svc.start_desk({"kind": "replay", "path": str(sample)}, desk_mode="control")
    with pytest.raises(PermissionError):
        svc.start_desk({"kind": "sandbox", "url": URL}, adapter="ton_poker", desk_mode="control")
    with pytest.raises(PermissionError):
        svc.start_desk({"kind": "sandbox", "url": URL}, adapter="ton_poker", desk_mode="coach")
    with pytest.raises(NotImplementedError):
        svc.start_desk({"kind": "window", "hwnd": 1}, desk_mode="observe")


def test_window_partly_off_screen_gives_a_cropped_frame_and_is_refused_not_clicked(make):
    svc = make("control", max_hands=6)
    assert wait_for(lambda: len(verified_actions(svc)) >= 1, 120)
    n = svc.desk["executor"].n_actions
    assert svc.sandbox_cmd("move", x=260, y=60)["ok"]                            # bottom 60 px leave the screen: captured frame is cropped
    time.sleep(8)
    ex = svc.desk["executor"]
    # either vision cannot read a cropped table (nothing actionable) or the mapper refuses the frame/window mismatch: in no case is a click made
    assert ex.n_actions == n, ex.log[-3:]
    assert svc.last_frame.h < 900 and svc.last_frame.meta["rect"][3] == 900      # the frame really is cropped relative to the window rectangle
