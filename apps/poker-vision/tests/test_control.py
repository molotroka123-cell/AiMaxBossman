"""Executor/guard/geometry/locator tests with scripted doubles: every refusal path, STOP, no blind retry, model cannot redirect clicks."""
from __future__ import annotations

import threading
import types

import numpy as np
import pytest

from pokervision.control.backends import ClickRefused, ComputerUseBackend, PointerBackend
from pokervision.control.executor import Executor, Fresh, Panel, Preset
from pokervision.control.geometry import GeometryError, Rect, ScreenMapper, preview_to_frame
from pokervision.control.identity import GuardConfig, IdentityGuard, StaticProbe, WindowIdentity, WindowState
from pokervision.control.intent import PolicyDecision, hand_key
from pokervision.control.locator import ModelLocator, VisionLocator
from pokervision.schema import Money

ID = WindowIdentity("window", 1001, 77, "poker.exe", 1000.0, "Poker Train")
RECT = Rect(100, 50, 520, 900)


def mk_state(buttons, quality=None):
    return types.SimpleNamespace(quality={"buttons": buttons, "boxes": [], **(quality or {})})


def btn(label, x, y=720, w=140, h=54, amount=None):
    return {"label": label, "x": x, "y": y, "w": w, "h": h, "amount": amount, "conf": 0.9}


def mk_committed(actions=(("FOLD", None), ("CALL", 20.0), ("RAISE", None)), hero_turn=True, blocked=(), pot=60.0, board=()):
    return types.SimpleNamespace(hero_cards=["Ah", "Kd"], board=list(board), pot=Money(pot), to_call=Money(20.0), hero_stack=Money(900.0),
                                 hero_turn=hero_turn, blocked=list(blocked), actions=[list(a) for a in actions], t_ms=1000, seats={}, pending=[])


def mk_fresh(committed=None, buttons=None, age=100.0):
    committed = committed or mk_committed()
    names = [a[0] for a in committed.actions]
    buttons = buttons if buttons is not None else [btn(n, 20 + 170 * i) for i, n in enumerate(names)]
    fr = types.SimpleNamespace(bgr=np.zeros((900, 520, 3), np.uint8), w=520, h=900, t_ms=1000)
    return Fresh(fr, mk_state(buttons), committed, age)


class Script:
    """read_fresh() yields queued Fresh objects, repeating the last one."""
    def __init__(self, *items): self.items = list(items); self.i = 0
    def __call__(self):
        it = self.items[min(self.i, len(self.items) - 1)]; self.i += 1; return it


def mk_exec(script, probe=None, backend=None, locator=None, stop=None, **kw):
    clicks = []
    backend = backend or PointerBackend(lambda x, y: clicks.append((x, y)))
    probe = probe or StaticProbe(WindowState(ID, RECT))
    ex = Executor(read_fresh=script, locator=locator or VisionLocator(), backend=backend, probe=probe, guard=IdentityGuard(ID, (520, 900)),
                  stop=stop or threading.Event(), verify_timeout_s=0.3, settle_s=0.01, sleep=lambda s: None, **kw)
    return ex, clicks


def dec(kind="CALL", **kw):
    base = dict(kind=kind, raise_to=None, hand_key=hand_key(["Ah", "Kd"], []), street="preflop", t_ms=1000, to_call=20.0)
    base.update(kw)
    return PolicyDecision(**base)


AFTER = lambda: mk_fresh(mk_committed(actions=(), hero_turn=False))


# ---------------------------------------------------------------- geometry
def test_mapper_applies_window_position_and_scale():
    m = ScreenMapper(520, 900, Rect(100, 50, 1040, 1800))            # 2x DPI capture window at (100, 50)
    assert m.frame_to_screen(260, 450) == (100 + 520, 50 + 900)
    m2 = ScreenMapper(520, 900, RECT, pointer_scale=0.5)             # DPI-unaware pointer
    assert m2.target_point(Rect(0, 0, 100, 100)) == ((100 + 50) * 0.5, (50 + 50) * 0.5)


def test_mapper_refuses_stale_frame_after_resize_and_outside_boxes():
    with pytest.raises(GeometryError):
        ScreenMapper(520, 900, Rect(0, 0, 800, 900))                 # window was resized after the frame
    m = ScreenMapper(520, 900, RECT)
    with pytest.raises(GeometryError):
        m.target_point(Rect(500, 10, 100, 20))
    with pytest.raises(GeometryError):
        preview_to_frame(300, 10, 200, 100, 520, 900)


# ---------------------------------------------------------------- guard
@pytest.mark.parametrize("state,age,expect", [
    (WindowState(None), 10, "SOURCE_CLOSED"),
    (WindowState(WindowIdentity("window", 1001, 78, "poker.exe", 2000.0, "Poker Train"), RECT), 10, "SOURCE_REOPENED_OR_REPLACED"),
    (WindowState(ID, RECT, minimized=True), 10, "SOURCE_MINIMIZED_OR_HIDDEN"),
    (WindowState(ID, RECT, occluded_frac=0.4), 10, "SOURCE_OCCLUDED"),
    (WindowState(ID, RECT), 5000, "FRAME_STALE"),
    (WindowState(ID, Rect(100, 50, 700, 900)), 10, "SOURCE_RESIZED_REVERIFY_PROFILE"),
])
def test_guard_reasons(state, age, expect):
    assert any(expect in r for r in IdentityGuard(ID, (520, 900)).reasons(state, age))


def test_same_title_other_process_is_not_the_same_window():
    other = WindowIdentity("window", 1001, 99, "other.exe", 1000.0, "Poker Train")
    assert other.key != ID.key


def test_guard_clean_state_passes_and_moving_is_allowed():
    g = IdentityGuard(ID, (520, 900))
    assert g.reasons(WindowState(ID, Rect(400, 300, 520, 900)), 10) == []


# ---------------------------------------------------------------- executor
def test_call_clicks_the_relocated_button_and_verifies_state_change():
    ex, clicks = mk_exec(Script(mk_fresh(), mk_fresh(), AFTER()))
    r = ex.execute(dec("CALL"))
    assert r.ok and len(clicks) == 1
    sx = 100 + (20 + 170 * 1 - 0 + 0) + 70 * 1                      # button 'CALL' centre in screen px (x = 190..330 -> 260)
    assert abs(clicks[0][0] - (100 + 190 + 70)) < 1 and abs(clicks[0][1] - (50 + 720 + 27)) < 1
    assert r.steps[0]["verified"] is True


def test_unconfirmed_click_halts_and_is_never_retried():
    ex, clicks = mk_exec(Script(mk_fresh()))                          # state never changes
    r = ex.execute(dec("CALL"))
    assert not r.ok and "not confirmed" in r.halted and len(clicks) == 1
    r2 = ex.execute(dec("CALL"))                                       # halted: no second click
    assert not r2.ok and len(clicks) == 1


@pytest.mark.parametrize("fresh,why", [
    (mk_fresh(age=9000.0), "FRAME_STALE"),
    (mk_fresh(mk_committed(blocked=["SOURCE_NOT_RESPONDING"])), "blocked"),
    (mk_fresh(mk_committed(actions=(("FOLD", None), ("CALL", 55.0), ("RAISE", None)))), "differs from the decided"),
    (mk_fresh(buttons=[btn("FOLD", 20), btn("RAISE", 360)]), "locate"),
    (mk_fresh(buttons=[btn("FOLD", 20), btn("CALL", 190), btn("CALL", 340), btn("RAISE", 500)]), "ambiguous"),
])
def test_preflight_refusals_never_click(fresh, why):
    ex, clicks = mk_exec(Script(fresh))
    r = ex.execute(dec("CALL"))
    assert not r.ok and why in r.halted and clicks == []


@pytest.mark.parametrize("fresh,why", [
    (mk_fresh(mk_committed(hero_turn=False)), "not hero's turn"),
    (mk_fresh(mk_committed(board=("2c", "3d", "4h"))), "hand/street changed"),
])
def test_obsolete_decision_is_dropped_without_click_and_without_halt(fresh, why):
    ex, clicks = mk_exec(Script(fresh))
    r = ex.execute(dec("CALL"))
    assert not r.ok and r.halted is None and why in r.obsolete and clicks == [] and ex.halted is None


def test_one_bad_frame_before_any_click_is_retried_then_clicks_once():
    bad = mk_fresh(buttons=[btn("FOLD", 20), btn("RAISE", 360)])             # CALL not read on this frame only
    ex, clicks = mk_exec(Script(bad, bad, mk_fresh(), mk_fresh(), AFTER()))
    r = ex.execute(dec("CALL"))
    assert r.ok and len(clicks) == 1


def test_other_window_on_top_at_the_click_point_refuses():
    probe = StaticProbe(WindowState(ID, RECT), top_handle=555)
    ex, clicks = mk_exec(Script(mk_fresh()), probe=probe)
    r = ex.execute(dec("CALL"))
    assert not r.ok and "another window" in r.halted and clicks == []


def test_stop_before_click_wins_and_stop_after_click_is_reported_unverified():
    stop = threading.Event(); stop.set()
    ex, clicks = mk_exec(Script(mk_fresh()), stop=stop)
    r = ex.execute(dec("CALL"))
    assert r.halted.startswith("STOP") and clicks == []
    stop2 = threading.Event()
    sent = []
    ex2, _ = mk_exec(Script(mk_fresh()), stop=stop2, backend=PointerBackend(lambda x, y: (sent.append(1), stop2.set())))
    r2 = ex2.execute(dec("CALL"))
    assert sent == [1] and "STOP" in r2.halted and "not verified" in r2.halted


def test_window_closed_between_decision_and_click():
    probe = StaticProbe(WindowState(None))
    ex, clicks = mk_exec(Script(mk_fresh()), probe=probe)
    assert "SOURCE_CLOSED" in ex.execute(dec("CALL")).halted and clicks == []


class FakeModel:
    def __init__(self, proposal): self.p = proposal
    def propose(self, bgr, instruction): return self.p


@pytest.mark.parametrize("proposal,why", [
    ({"label": "FOLD", "box": [20, 720, 140, 54], "confidence": 0.99}, "different element"),        # model tries to click FOLD instead of CALL
    ({"label": "CALL", "box": [20, 720, 140, 54], "confidence": 0.99}, "does not overlap"),         # right label, wrong place
    ({"label": "CALL", "box": [2000, 720, 140, 54], "confidence": 0.99}, "outside the frame"),
    (None, "different element"),
])
def test_model_locator_cannot_redirect_the_click(proposal, why):
    ex, clicks = mk_exec(Script(mk_fresh()), locator=ModelLocator(FakeModel(proposal)))
    r = ex.execute(dec("CALL"))
    assert not r.ok and why in r.halted and clicks == []


def test_model_locator_agreeing_with_vision_is_used():
    ex, clicks = mk_exec(Script(mk_fresh(), mk_fresh(), AFTER()), locator=ModelLocator(FakeModel({"label": "CALL", "box": [195, 722, 130, 50], "confidence": 0.9})))
    r = ex.execute(dec("CALL"))
    assert r.ok and r.steps[0]["target"]["locator"] == "model"


class PanelScript:
    def __init__(self, panels): self.p = list(panels); self.i = 0
    def read(self, fresh):
        v = self.p[min(self.i, len(self.p) - 1)]; self.i += 1; return v


def test_raise_sets_a_preset_inside_the_policy_range_reads_back_then_confirms():
    presets = [Preset("33%", 49, Rect(14, 603, 161, 49)), Preset("67%", 100, Rect(345, 603, 161, 49)), Preset("Pot", 150, Rect(180, 657, 161, 49))]
    opened = mk_fresh(mk_committed(actions=(("FOLD", None), ("CHECK", None), ("CONFIRM", None))))
    panels = PanelScript([Panel(True, 0, presets), Panel(True, 100, presets)])
    ex, clicks = mk_exec(Script(mk_fresh(mk_committed(actions=(("FOLD", None), ("CHECK", None), ("RAISE", None)))), opened, opened, opened, opened, opened, AFTER()), panel=panels)
    d = dec("RAISE", raise_to=100.0, size_min=60.0, size_max=120.0, to_call=None)
    r = ex.execute(d)
    assert r.ok, r.halted
    assert [s["label"] for s in r.steps] == ["RAISE", "PRESET 67%", "CONFIRM"]
    assert r.steps[1]["amount_decided"] == 100.0 and r.steps[1]["amount_set"] == 100 and r.steps[1]["amount_read"] == 100


def test_raise_halts_when_no_preset_is_inside_the_policy_range_and_when_readback_differs():
    presets = [Preset("Pot", 150, Rect(180, 657, 161, 49))]
    opened = mk_fresh(mk_committed(actions=(("FOLD", None), ("CHECK", None), ("CONFIRM", None))))
    ex, clicks = mk_exec(Script(mk_fresh(mk_committed(actions=(("FOLD", None), ("CHECK", None), ("RAISE", None)))), opened, opened), panel=PanelScript([Panel(True, 0, presets)]))
    r = ex.execute(dec("RAISE", raise_to=100.0, size_min=60.0, size_max=120.0, to_call=None))
    assert not r.ok and "inside the policy's range" in r.halted and len(clicks) == 1                 # only RAISE (opening the panel) was clicked
    presets2 = [Preset("67%", 100, Rect(345, 603, 161, 49))]
    ex2, clicks2 = mk_exec(Script(mk_fresh(mk_committed(actions=(("FOLD", None), ("CHECK", None), ("RAISE", None)))), opened, opened, opened), panel=PanelScript([Panel(True, 0, presets2), Panel(True, 49, presets2)]))
    r2 = ex2.execute(dec("RAISE", raise_to=100.0, size_min=60.0, size_max=120.0, to_call=None))
    assert not r2.ok and "not confirmed" in r2.halted and len(clicks2) == 2                          # CONFIRM never clicked on a wrong amount


def test_policy_decision_validation():
    with pytest.raises(ValueError):
        PolicyDecision("RAISE", None, "k", "flop", 0)
    with pytest.raises(ValueError):
        PolicyDecision("CALL", 10.0, "k", "flop", 0)
    with pytest.raises(ValueError):
        PolicyDecision("RAISE", 500.0, "k", "flop", 0, size_min=10, size_max=100)


# ---------------------------------------------------------------- computer use backend keeps its own perimeter
def test_computer_use_backend_surfaces_refusals_and_names_the_target():
    calls = []
    async def observe(): return {"generation": 3}
    async def act(args):
        calls.append(args)
        raise RuntimeError("окно не в allowlist")
    be = ComputerUseBackend(observe, act)
    with pytest.raises(ClickRefused, match="allowlist"):
        be.press("CALL", None)
    assert calls == [{"action": "click", "target": "CALL", "generation": 3}]
    ex, clicks = mk_exec(Script(mk_fresh()), backend=be)
    r = ex.execute(dec("CALL"))
    assert not r.ok and "REFUSED" in r.halted


def test_capture_failure_inside_the_executor_halts_instead_of_raising():
    def boom():
        raise RuntimeError("window minimised")
    ex, clicks = mk_exec(boom)
    r = ex.execute(dec("CALL"))
    assert not r.ok and "SOURCE_LOST" in r.halted and clicks == [] and ex.halted
