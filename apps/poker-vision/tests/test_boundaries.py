"""Policy tests: what the system must refuse, and independence from the existing BotLab boundary."""
import dataclasses
import re
import threading
from pathlib import Path

import numpy as np
import pytest

from pokervision.actuator import ActionRefused, TrainerActuator
from pokervision.adapters import registry
from pokervision.adapters.base import Frame
from pokervision.adapters.ton_poker import TonPokerAdapter
from pokervision.reconcile import Committed
from pokervision.sources import NotLoopback, assert_loopback
from pokervision.strategy import FORBIDDEN, VisibleInfo

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("url", ["http://127.0.0.1:3000/", "http://localhost:3000", "http://[::1]:3000/"])
def test_loopback_ok(url):
    assert assert_loopback(url) == url


@pytest.mark.parametrize("url", ["http://example.com/", "https://127.0.0.1.evil.com/", "http://evil@127.0.0.1/", "http://10.0.0.5/", "file:///x", "ftp://127.0.0.1/", "http://t.me/x"])
def test_non_loopback_refused(url):
    with pytest.raises(NotLoopback):
        assert_loopback(url)


def test_external_adapter_cannot_act_or_advise():
    caps = TonPokerAdapter.capabilities
    assert caps.observe and caps.replay and not caps.act and not caps.advise_live
    assert registry.available()["poker_train"]["capabilities"]["act"] is True


def test_unknown_adapter_is_not_guessed():
    with pytest.raises(KeyError):
        registry.get("clubgg")


def test_ton_without_profile_is_all_unknown():
    ad = TonPokerAdapter(None)
    st = ad.read(Frame(np.zeros((400, 700, 3), np.uint8), 0, "t", "f"))
    assert not any(f.known for f in st.hero_cards) and not st.pot.known and "layout_not_calibrated" in st.pot.reason


def test_actuator_guard_matrix():
    caps = registry.available()
    from pokervision.adapters.base import Capabilities
    class Src: pass
    act = TrainerActuator.__new__(TrainerActuator)
    act.stop = threading.Event(); act.halted = None; act.max_actions = 3
    from pokervision.actuator import ActionLog
    act.log = ActionLog()
    good = Committed(hero_cards=["As", "Kd"], hero_turn=True, t_ms=1000)
    can = Capabilities(act=True)
    assert act.guard(good, 1100, can) is None
    assert act.guard(good, 1100, Capabilities(act=False)) == "adapter has no act capability"
    assert "stale" in act.guard(good, 9000, can)
    assert act.guard(Committed(hero_cards=["As", None], hero_turn=True, t_ms=1000), 1100, can) == "hero cards not committed"
    assert "not hero's turn" in act.guard(Committed(hero_cards=["As", "Kd"], hero_turn=None, t_ms=1000), 1100, can)
    blocked = Committed(hero_cards=["As", "Kd"], hero_turn=True, t_ms=1000, blocked=["PENDING_CHANGE pot"])
    assert act.guard(blocked, 1100, can).startswith("blocked")
    act.stop.set()
    assert act.guard(good, 1100, can) == "STOP"                      # STOP wins over everything
    act.stop.clear(); act.halt("ambiguous")
    assert act.guard(good, 1100, can).startswith("HALTED")


def test_actuator_refuses_non_trainer_source():
    with pytest.raises(ActionRefused):
        TrainerActuator(object(), threading.Event())


def test_strategy_type_cannot_hold_hidden_state():
    names = {f.name for f in dataclasses.fields(VisibleInfo)}
    assert names == {"hero", "board", "pot", "to_call", "stack", "n_opponents", "actions"}
    assert not any(bad in n for n in names for bad in FORBIDDEN)


def test_botlab_boundary_untouched_and_vision_is_separate():
    botlab = ROOT.parent / "poker-botlab" / "botlab"
    src = "\n".join(p.read_text(encoding="utf-8") for p in botlab.glob("*.py"))
    for banned in ("mss", "ImageGrab", "pyautogui", "pynput", "win32api"):
        assert not re.search(rf"\b{banned}\b", src), f"BotLab boundary must stay clean: {banned}"
    assert not (botlab / "vision.py").exists()


def test_no_money_or_external_click_code_paths():
    code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "pokervision").rglob("*.py"))
    for banned in ("pyautogui", "pynput", "SendInput", "mouse_event", "win32api", "ggpoker", "pokerstars", "clubgg"):
        assert banned.lower() not in code.lower(), banned
