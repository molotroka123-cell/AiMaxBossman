"""Offline review of KNOWN and synthetic hands: equity against random hands, the pot-odds comparison, abstention and the disclaimer.

Reference values are the published heads-up equities against ONE random hand (AA ≈ 85.2 %, KK ≈ 82.4 %, 72o ≈ 34.6 %).
The coach is advice under uncertainty on visible information only; nothing here acts, bets or touches a client."""
import random
import types

import pytest

from pokervision.coach import DISCLAIMER, recommend
from pokervision.schema import Money

BUTTONS = (("FOLD", None), ("CALL", 50.0), ("RAISE", None))


def cm(hero, board=(), pot=100.0, stack=1000.0, actions=BUTTONS, blocked=(), turn=True, pending=(), seats=None):
    seats = seats if seats is not None else {1: {"stack": 900.0}}
    return types.SimpleNamespace(hero_cards=list(hero), board=list(board), pot=Money(pot), hero_stack=Money(stack), hero_turn=turn,
                                 blocked=list(blocked), actions=[list(a) for a in actions], t_ms=1000, pending=list(pending), seats=seats, to_call=None)


@pytest.mark.parametrize("hero,lo,hi", [(("As", "Ah"), 0.80, 0.90), (("Ks", "Kh"), 0.78, 0.87), (("7c", "2d"), 0.28, 0.40)])
def test_preflop_equity_matches_the_published_value(hero, lo, hi):
    r = recommend(cm(hero), random.Random(7), sims=2000)
    assert r.ok and lo <= r.equity <= hi, (hero, r.equity)


def test_the_nuts_on_the_river_are_recognised():
    r = recommend(cm(("Ah", "Kh"), board=("Qh", "Jh", "Th", "2c", "3d")), random.Random(1), sims=500)
    assert r.ok and r.equity >= 0.99 and r.decision.kind in {"RAISE", "CALL"}


def test_a_hopeless_hand_facing_a_big_bet_is_not_called():
    r = recommend(cm(("7c", "2d"), board=("Ah", "Ks", "Qd", "9c", "4s"), pot=100.0, actions=(("FOLD", None), ("CALL", 400.0))),
                  random.Random(1), sims=500)
    assert r.ok and r.decision.kind == "FOLD" and r.equity < r.pot_odds


def test_pot_odds_are_computed_from_the_visible_numbers():
    r = recommend(cm(("As", "Ah"), pot=100.0, actions=(("FOLD", None), ("CALL", 50.0))), random.Random(1), sims=300)
    assert r.pot_odds == pytest.approx(50 / (100 + 50), abs=0.001)           # break-even equity for a call


def test_same_seed_same_review_and_the_disclaimer_is_always_there():
    a = recommend(cm(("Qs", "Jd")), random.Random(5), sims=400)
    b = recommend(cm(("Qs", "Jd")), random.Random(5), sims=400)
    assert (a.equity, a.decision.kind, a.explanation) == (b.equity, b.decision.kind, b.explanation)
    assert a.disclaimer == DISCLAIMER and "не гарантия" in a.explanation + a.disclaimer


@pytest.mark.parametrize("kw,why", [
    (dict(hero=("As", None)), "карты героя"),
    (dict(hero=("As", "Kd"), turn=False), "не ход героя"),
    (dict(hero=("As", "Kd"), blocked=["stale frame"]), "заблокировано"),
    (dict(hero=("As", "Kd"), actions=(("FOLD", None), ("CALL", None))), "сумма колла"),
])
def test_it_abstains_instead_of_guessing_when_the_state_is_not_proven(kw, why):
    r = recommend(cm(**kw), random.Random(1), sims=100)
    assert not r.ok and why in r.why_not and r.decision is None


def test_unconfirmed_fields_are_reported_as_uncertainty_not_hidden():
    r = recommend(cm(("As", "Kd"), pending=["board[1]"]), random.Random(1), sims=100)
    assert r.ok and any("board[1]" in u for u in r.uncertainty)


def test_several_opponents_are_flagged_as_an_estimate():
    seats = {1: {"stack": 500.0}, 2: {"stack": 700.0}, 3: {"stack": 300.0}}
    r = recommend(cm(("As", "Kd"), seats=seats), random.Random(1), sims=200)
    assert r.ok and any("соперников" in u for u in r.uncertainty)
