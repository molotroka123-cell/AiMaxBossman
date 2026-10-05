from __future__ import annotations

import re
from pathlib import Path

import pytest

from botlab.cards import parse_cards
from botlab.profiles import STUDENT
from botlab.trainer_ui import (
    NotLoopbackError,
    TrainerSession,
    assert_loopback_url,
    is_loopback_request,
    observation_from_dom,
)

PKG = Path(__file__).resolve().parents[1] / "botlab"


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8925/", "http://localhost:8925", "http://[::1]:8925/", "https://127.0.0.1/x", "http://LOCALHOST:1/"],
)
def test_loopback_urls_accepted(url: str) -> None:
    assert assert_loopback_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/",
        "https://www.ggpoker.com/",
        "https://clubgg.com/",
        "https://www.pokerstars.com/",
        "http://127.0.0.1.nip.io:8925/",
        "http://localhost.evil.com/",
        "http://evil.com@127.0.0.1:8925/",  # credentials trick
        "http://127.0.0.1@evil.com/",
        "http://10.0.0.5:8925/",
        "http://192.168.1.10:8925/",
        "http://0.0.0.0:8925/",
        "file:///C:/poker.html",
        "ws://127.0.0.1:8925/",
        "127.0.0.1:8925",
        "http://127.0.0.1:99999/",
    ],
)
def test_non_loopback_urls_refused(url: str) -> None:
    with pytest.raises(NotLoopbackError):
        assert_loopback_url(url)


def test_session_refuses_before_browser_starts(tmp_path: Path) -> None:
    with pytest.raises(NotLoopbackError):
        TrainerSession("https://www.ggpoker.com/", STUDENT, tmp_path)
    assert not any(tmp_path.iterdir())


def test_request_guard() -> None:
    assert is_loopback_request("http://127.0.0.1:8925/assets/index.js")
    assert is_loopback_request("data:image/png;base64,AAAA")
    assert not is_loopback_request("https://fonts.googleapis.com/css")
    assert not is_loopback_request("http://example.com/")


def test_no_allowlist_override_and_no_online_room_code() -> None:
    src = "\n".join(p.read_text(encoding="utf-8") for p in PKG.glob("*.py"))
    # the loopback allowlist is a frozen constant, not read from env/config/CLI
    assert "LOOPBACK_HOSTS = frozenset({\"127.0.0.1\", \"localhost\", \"::1\"})" in src
    assert "environ" not in src
    # no adapters/selectors for online rooms, no OS-level input or screen capture
    for banned in ("clubgg", "ggpoker", "pokerstars", "pyautogui", "pynput", "ImageGrab", "mss", "win32api"):
        assert not re.search(rf"\b{banned}\b", src, re.IGNORECASE), banned


def test_observation_from_dom_maps_state() -> None:
    raw = {
        "heroTurn": True, "handOver": False, "hero": ["6c", "4s"], "board": ["Qc", "4c", "2s"], "shown": [],
        "pot": 94, "toCall": 35, "canCheck": False, "canCall": True, "canRaise": True, "allInOnly": False,
        "heroStack": 973, "opponentStacks": [938], "handLog": ["[BB] X raises to 52"], "header": "Hand #1 | Level 1",
    }
    obs = observation_from_dom(raw, big_blind=10, hand_id=0)
    assert obs.street == "flop"
    assert obs.hole == tuple(parse_cards("6c4s"))
    assert obs.to_call == 35 and obs.pot == 94 and obs.n_active_opponents == 1
    assert obs.legal.can_call and obs.legal.can_raise and not obs.legal.can_check
    assert obs.legal.min_to == 45 and obs.legal.max_to == 973  # postflop: current bet ~= amount to call
    check_state = dict(raw, toCall=0, canCheck=True, canCall=False, board=["Qc", "4c", "2s", "Kc"])
    obs2 = observation_from_dom(check_state, big_blind=10, hand_id=0)
    assert obs2.street == "turn" and obs2.legal.can_check and obs2.legal.can_bet and not obs2.legal.can_fold
    with pytest.raises(ValueError):
        observation_from_dom(dict(raw, hero=[]), big_blind=10, hand_id=0)


def test_preflop_current_bet_recovered_from_this_hands_log_only() -> None:
    raw = {
        "hero": ["Ks", "Kd"], "board": [], "pot": 45, "toCall": 20, "canCheck": False, "canRaise": True,
        "heroStack": 990, "opponentStacks": [1000, 980],
        "handLog": ["[CO] Bob raises to 30", "[UTG] Ann folds", "--- New Hand --- Blinds: 5/10", "[BTN] Old raises to 99"],
    }
    obs = observation_from_dom(raw, big_blind=10, hand_id=0)  # hero is the big blind facing a raise to 30
    assert obs.current_bet == 30 and obs.my_street_bet == 10 and obs.raises_this_street == 1
    assert obs.legal.max_to == 1000 and obs.legal.min_to == 40
    sb = observation_from_dom(dict(raw, toCall=5, handLog=["[UTG] Ann folds", "--- New Hand ---"]), big_blind=10, hand_id=0)
    assert sb.current_bet == 10 and sb.my_street_bet == 5 and sb.raises_this_street == 0
