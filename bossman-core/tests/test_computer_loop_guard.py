"""Loop / no-progress protection: Bossman не кликает вслепую бесконечно."""
from __future__ import annotations

from bossman.computer_operator.loop_guard import (
    GuardVerdict, LoopGuard, action_signature, state_signature,
)
from bossman.computer_operator.models import ActionKind, ComputerAction, ExpectedState, Observation


def _act(kind=ActionKind.CLICK, target="Save", text=None):
    return ComputerAction.make(kind, expected=ExpectedState(contains_text="ok"),
                               target=target, text=text)


def _obs(app="notepad", title="Untitled", url="", summary="s", tree=None, gen=0):
    return Observation(id="o", created_at=0.0,
                       foreground={"app": app, "title": title, "url": url},
                       summary=summary, ui_tree=tree, screenshot_ref=None,
                       sensitive=False, generation=gen)


# ---------- signatures ----------

def test_action_signature_stable_and_discriminating():
    a1, a2 = _act(), _act()
    assert action_signature(a1) == action_signature(a2)          # id не влияет
    assert action_signature(a1) != action_signature(_act(target="Cancel"))


def test_state_signature_ignores_screenshot_but_tracks_structure():
    a = _obs(); b = _obs()
    b.screenshot_ref = "different.png"
    assert state_signature(a) == state_signature(b)              # пиксели не шумят
    assert state_signature(a) != state_signature(_obs(title="Saved"))


def test_state_signature_tracks_ui_tree_changes():
    t1 = {"elements": [{"control_type": "Button", "name": "Save"}]}
    t2 = {"elements": [{"control_type": "Button", "name": "Saved"}]}
    assert state_signature(_obs(tree=t1)) != state_signature(_obs(tree=t2))


# ---------- detection ----------

def test_empty_guard_allows_first_action():
    assert LoopGuard().check(_act(), _obs()).tripped is False


def test_repeat_same_action_same_state_trips():
    g = LoopGuard(max_identical=3)
    a, o = _act(), _obs()
    for _ in range(3):
        g.record(a, o, o, False)
    v = g.check(a, o)
    assert v.tripped and v.kind in {"repeat", "no_progress", "verify_loop"}


def test_no_progress_trips_even_when_verification_unknown():
    g = LoopGuard(max_identical=99, max_verify_fail=99, max_no_progress=3)
    a, o = _act(), _obs()
    for _ in range(3):
        g.record(a, o, o, None)          # состояние не изменилось
    v = g.check(a, o)
    assert v.tripped and v.kind == "no_progress"


def test_verify_loop_trips_on_repeated_failed_verification():
    g = LoopGuard(max_identical=99, max_no_progress=99, max_verify_fail=3)
    a = _act()
    before, after = _obs(), _obs(title="changed")
    for _ in range(3):
        g.record(a, before, after, False)   # состояние меняется, но верификация падает
    v = g.check(a, before)
    assert v.tripped and v.kind == "verify_loop"


def test_oscillation_between_two_states_trips():
    g = LoopGuard(max_identical=99, max_no_progress=99, max_verify_fail=99)
    a = _act()
    x, y = _obs(title="A"), _obs(title="B")
    for st in (x, y, x, y):
        g.record(a, st, st, True)
    assert g.check(a, x).kind == "oscillation"


def test_progress_does_not_trip():
    """Реальный прогресс: состояние меняется каждый раз -> guard молчит."""
    g = LoopGuard()
    a = _act()
    for i in range(6):
        b, af = _obs(title=f"s{i}"), _obs(title=f"s{i+1}")
        g.record(a, b, af, True)
    assert g.check(a, _obs(title="s6")).tripped is False


def test_different_actions_do_not_trip_repeat():
    g = LoopGuard(max_identical=3)
    o = _obs()
    for tgt in ("A", "B", "C"):
        g.record(_act(target=tgt), o, o, True)
    assert g.check(_act(target="D"), o).tripped is False


def test_reset_clears_history_after_takeover():
    g = LoopGuard(max_identical=2)
    a, o = _act(), _obs()
    g.record(a, o, o, False); g.record(a, o, o, False)
    assert g.check(a, o).tripped is True
    g.reset()
    assert g.check(a, o).tripped is False
