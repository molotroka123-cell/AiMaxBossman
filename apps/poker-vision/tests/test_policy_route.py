"""Route from a validated state to the executor's decision through a (fake) Poker-LoRA policy."""
import types

import pytest

from pokervision.control.intent import PolicyDecision
from pokervision.policy_route import applicable, build_input, decide_with_policy
from pokervision.schema import Money

RANGES = {"hero": "AA,KK,AKs", "villain": "QQ,JJ,AQs"}


def cm(actions=(("FOLD", None), ("CALL", 20.0), ("RAISE", None)), board=("2c", "7d", "9h", "Jc", "Ks"), pot=60.0, stack=400.0, blocked=(), turn=True):
    return types.SimpleNamespace(hero_cards=["Ah", "Kd"], board=list(board), pot=Money(pot), hero_stack=Money(stack), hero_turn=turn, blocked=list(blocked),
                                 actions=[list(a) for a in actions], t_ms=1000, pending=[], seats={}, to_call=None)


KW = dict(position="IP", history=[{"player": "OOP", "action": "bet50", "amount": 20.0}], ranges=RANGES, pot_at_river_start=40.0, effective_stack=420.0, heads_up_confirmed=True)


class Fake:
    def __init__(self, out): self.out = out
    def act(self, inp): return self.out(inp) if callable(self.out) else self.out


def test_not_applicable_unless_everything_is_proven():
    ok = dict(heads_up_confirmed=True, position="IP", history=[], ranges=RANGES)
    assert applicable(cm(), **ok).ok
    assert "river" in applicable(cm(board=("2c", "7d", "9h")), **ok).reason
    assert "heads-up" in applicable(cm(), **{**ok, "heads_up_confirmed": False}).reason
    assert "position" in applicable(cm(), **{**ok, "position": None}).reason
    assert "history" in applicable(cm(), **{**ok, "history": None}).reason
    assert "ranges" in applicable(cm(), **{**ok, "ranges": None}).reason
    assert "validated" in applicable(cm(blocked=["stale"]), **ok).reason


def test_valid_answer_becomes_a_decision_and_the_amount_is_not_changed():
    d, info = decide_with_policy(cm(), Fake({"action": "call", "size": 20.0, "probs": {"call": 0.7, "fold": 0.3}, "explanation": "bluff catcher"}), **KW)
    assert isinstance(d, PolicyDecision) and d.kind == "CALL" and d.to_call == 20.0 and info["source"] == "poker_lora" and info["assumed_ranges"]
    inp = build_input(cm(), "IP", KW["history"], RANGES, 40.0, 420.0)
    amounts = {a["action"]: a["amount"] for a in inp["legal"]}
    assert amounts["call"] == 20.0 and amounts["raise"] == 100.0
    d2, _ = decide_with_policy(cm(), Fake({"action": "raise", "size": 100.0}), **KW)
    assert d2.kind == "RAISE" and d2.raise_to == 100.0 and d2.size_min == pytest.approx(80.0) and d2.size_max == pytest.approx(120.0)


@pytest.mark.parametrize("answer", [
    {"action": "teleport", "size": 0}, {"action": "raise", "size": 999.0}, "not json", {"action": "call", "probs": {"call": 0.1}},
    {"action": "bet50", "size": 30.0},                                   # not a legal action when facing a bet
])
def test_bad_answers_fall_back_to_the_heuristic_and_go_to_review_never_to_labels(answer, tmp_path):
    d, info = decide_with_policy(cm(), Fake(answer), review_root=tmp_path, **KW)
    assert d is None and info["source"] == "heuristic" and "rejected" in info["why"]
    from pokerlora import mistakes
    assert mistakes.read(tmp_path)[0]["state"] == "needs_review"


def test_policy_crash_and_unmappable_buttons_fall_back(tmp_path):
    def boom(inp): raise RuntimeError("server down")
    d, info = decide_with_policy(cm(), Fake(boom), review_root=tmp_path, **KW)
    assert d is None and "policy error" in info["why"]
    only_fold_call = cm(actions=(("FOLD", None), ("CALL", 20.0)))
    d, info = decide_with_policy(only_fold_call, Fake({"action": "raise", "size": 100.0}), review_root=tmp_path, **KW)
    assert d is None and info["source"] == "heuristic"                    # RAISE is not on screen, so the answer is not legal for this state


def test_coach_uses_the_model_route_only_when_applicable_and_says_so():
    import random
    from pokervision.coach import recommend
    c = cm()
    ctx = {k: v for k, v in KW.items()}
    r = recommend(c, random.Random(1), 50, policy=Fake({"action": "call", "size": 20.0, "probs": {"call": 0.7, "fold": 0.3}}), ctx=ctx)
    assert r.ok and r.source == "poker_lora" and "ПРЕДПОЛОЖЕНЫ" in r.explanation and r.decision.kind == "CALL"
    r2 = recommend(c, random.Random(1), 50, policy=Fake({"action": "call", "size": 20.0}), ctx={**ctx, "heads_up_confirmed": False})
    assert r2.source == "heuristic" and "heads-up" in r2.route_note
