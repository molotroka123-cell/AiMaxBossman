from __future__ import annotations

import random

import pytest

from botlab.engine import Action, IllegalAction, play_hand

from .helpers import RandomLegalAgent, ScriptAgent, build_deck


def test_side_pots_and_uncalled_bet_returned() -> None:
    # seat0 = button (A, 50), seat1 = SB (B, 100), seat2 = BB (C, 200)
    a = ScriptAgent("A", [Action("raise", 50)])
    b = ScriptAgent("B", [Action("call"), Action("call")])
    c = ScriptAgent("C", [Action("raise", 200)])
    deck = build_deck(["AsAh", "KsKh", "7c2d"], "Ad Kd 9c 5s 3h")
    res = play_hand([a, b, c], [50, 100, 200], button=0, small_blind=1, big_blind=2, deck_seed=0, deck=deck)
    assert res.deltas == [100, 0, -100]
    assert res.end_stacks == [150, 100, 100]
    assert res.pots == [(150, [0, 1, 2], [0]), (100, [1, 2], [1]), (100, [2], [2])]
    assert sum(res.deltas) == 0


def test_short_stack_wins_only_main_pot() -> None:
    # Short stack has the best hand but can only win what it matched from each player.
    a = ScriptAgent("A", [Action("raise", 30)])
    b = ScriptAgent("B", [Action("raise", 100)])
    c = ScriptAgent("C", [Action("call")])
    deck = build_deck(["AsAh", "KsKh", "QsQh"], "2d 7c 9c Ts 3h")
    res = play_hand([a, b, c], [30, 100, 100], button=0, small_blind=1, big_blind=2, deck_seed=0, deck=deck)
    assert res.deltas == [60, 40, -100]


def test_split_pot_with_odd_chip_goes_left_of_button() -> None:
    p0 = ScriptAgent("P0", [Action("call")])
    p1 = ScriptAgent("P1", [Action("fold")])
    p2 = ScriptAgent("P2", [])
    deck = build_deck(["2c3d", "4h4s", "2d3c"], "Ts Jd Qh Kc Ad")
    res = play_hand([p0, p1, p2], [100, 100, 100], button=0, small_blind=1, big_blind=2, deck_seed=0, deck=deck)
    # pot 5 split between seat0 and seat2; odd chip to seat2 (first winner after the button)
    assert res.deltas == [0, -1, 1]
    assert res.pots[-1][2] == [0, 2]


def test_min_raise_tracks_last_full_raise() -> None:
    btn = ScriptAgent("BTN", [Action("raise", 6), Action("fold")])
    bb = ScriptAgent("BB", [Action("raise", 20)])
    res = play_hand([btn, bb], [200, 200], button=0, small_blind=1, big_blind=2, deck_seed=3)
    assert bb.seen[0].legal.min_to == 10  # 6 + (6 - 2)
    assert btn.seen[1].legal.min_to == 34  # 20 + (20 - 6)
    assert btn.seen[0].legal.min_to == 4
    assert res.deltas == [-6, 6]


def test_short_all_in_does_not_reopen_raising() -> None:
    s0 = ScriptAgent("S0", [Action("raise", 10), Action("call")])
    s1 = ScriptAgent("S1", [Action("raise", 14)])  # all-in, increment 4 < 8
    s2 = ScriptAgent("S2", [Action("call")])
    play_hand([s0, s1, s2], [200, 14, 200], button=0, small_blind=1, big_blind=2, deck_seed=5)
    assert s2.seen[0].legal.can_raise  # has not acted yet -> may raise
    second = s0.seen[1]
    assert second.street == "preflop"
    assert second.legal.can_call and not second.legal.can_raise
    assert second.to_call == 4


def test_heads_up_order_button_is_small_blind() -> None:
    btn = ScriptAgent("BTN", [Action("call")])
    bb = ScriptAgent("BB", [])
    res = play_hand([btn, bb], [100, 100], button=0, small_blind=1, big_blind=2, deck_seed=11)
    posts = [(a.seat, a.kind, a.added) for a in res.actions[:2]]
    assert posts == [(0, "post_sb", 1), (1, "post_bb", 2)]
    first_pre = next(a for a in res.actions if a.street == "preflop" and not a.kind.startswith("post"))
    first_flop = next(a for a in res.actions if a.street == "flop")
    assert first_pre.seat == 0
    assert first_flop.seat == 1


def test_short_blind_posts_all_in() -> None:
    a = ScriptAgent("A", [Action("call")])
    b = ScriptAgent("B", [])
    res = play_hand([a, b], [100, 1], button=0, small_blind=1, big_blind=2, deck_seed=2)
    assert sum(res.deltas) == 0
    assert res.actions[1].added == 1  # BB posted only what it had


def test_illegal_actions_are_rejected() -> None:
    with pytest.raises(IllegalAction):
        play_hand([ScriptAgent("x", [Action("check")]), ScriptAgent("y", [])], [100, 100], 0, 1, 2, deck_seed=1)
    with pytest.raises(IllegalAction):
        play_hand([ScriptAgent("x", [Action("raise", 3)]), ScriptAgent("y", [])], [100, 100], 0, 1, 2, deck_seed=1)
    with pytest.raises(IllegalAction):
        play_hand([ScriptAgent("x", [Action("bet", 10)]), ScriptAgent("y", [])], [100, 100], 0, 1, 2, deck_seed=1)


def test_chip_conservation_fuzz_with_side_pots() -> None:
    rng = random.Random(1234)
    agent = RandomLegalAgent()
    for hand in range(3000):
        n = rng.randint(2, 6)
        stacks = [rng.choice([0, rng.randint(1, 30), rng.randint(1, 400)]) for _ in range(n)]
        while sum(1 for s in stacks if s > 0) < 2:
            stacks[rng.randrange(n)] = rng.randint(1, 400)
        res = play_hand([agent] * n, stacks, button=rng.randrange(n), small_blind=1, big_blind=2, deck_seed=hand)
        assert sum(res.deltas) == 0
        assert sum(res.end_stacks) == sum(stacks)
        assert all(s >= 0 for s in res.end_stacks)
        for seat, start in enumerate(stacks):
            if start == 0:
                assert res.end_stacks[seat] == 0  # sitting out


def test_same_seed_same_hand() -> None:
    agent = RandomLegalAgent()
    r1 = play_hand([agent] * 4, [200] * 4, 1, 1, 2, deck_seed=99)
    r2 = play_hand([agent] * 4, [200] * 4, 1, 1, 2, deck_seed=99)
    assert r1.summary() == r2.summary()
    r3 = play_hand([agent] * 4, [200] * 4, 1, 1, 2, deck_seed=100)
    assert r3.summary() != r1.summary()
