from __future__ import annotations

from botlab.engine import Action, Rake, play_hand

from .helpers import ScriptAgent, build_deck


def _hu(rake: Rake | None, stacks=(100, 100), script_a=None, script_b=None):
    a = ScriptAgent("A", script_a if script_a is not None else [Action("raise", 100)])
    b = ScriptAgent("B", script_b if script_b is not None else [Action("call")])
    deck = build_deck(["AsAh", "7c2d"], "Td 5c 9h Js 3d")
    return play_hand([a, b], list(stacks), button=0, small_blind=1, big_blind=2, deck_seed=0, deck=deck, rake=rake)


def test_no_rake_by_default_and_chips_conserved() -> None:
    res = _hu(None)
    assert res.rake == 0 and sum(res.deltas) == 0 and res.deltas == [100, -100]


def test_rake_is_percent_of_contested_pot_with_cap() -> None:
    res = _hu(Rake(pct=0.05, cap=6))
    assert res.rake == 6  # 5% of 200 = 10, capped at 6
    assert res.deltas == [94, -100]
    assert sum(res.deltas) + res.rake == 0
    res2 = _hu(Rake(pct=0.03, cap=100))
    assert res2.rake == 6  # int(200 * 0.03)


def test_no_flop_no_drop() -> None:
    res = _hu(Rake(pct=0.05, cap=100), script_a=[Action("raise", 6)], script_b=[Action("fold")])
    assert res.rake == 0
    res2 = _hu(Rake(pct=0.05, cap=100, no_flop_no_drop=False), script_a=[Action("raise", 6)], script_b=[Action("fold")])
    assert res2.rake == 0  # contested = 2 + 2 blinds-level chips: int(4 * 0.05) = 0
    big = _hu(Rake(pct=0.5, cap=100, no_flop_no_drop=False), script_a=[Action("raise", 6)], script_b=[Action("fold")])
    assert big.rake == 2  # 50% of the contested 4 chips; the uncalled 4 of the raise is never raked


def test_uncalled_bet_is_not_raked() -> None:
    # B has 40 chips, A shoves 100: only 40+40 is contested, 60 is returned and not raked.
    res = _hu(Rake(pct=0.10, cap=1000), stacks=(100, 40))
    assert res.rake == 8  # 10% of 80
    assert res.deltas == [40 - 8, -40]
    assert sum(res.deltas) + res.rake == 0


def test_rake_comes_from_the_main_pot_first() -> None:
    a = ScriptAgent("A", [Action("raise", 50)])
    b = ScriptAgent("B", [Action("call"), Action("call")])
    c = ScriptAgent("C", [Action("raise", 200)])
    deck = build_deck(["AsAh", "KsKh", "7c2d"], "Ad Kd 9c 5s 3h")
    res = play_hand([a, b, c], [50, 100, 200], button=0, small_blind=1, big_blind=2, deck_seed=0, deck=deck,
                    rake=Rake(pct=0.10, cap=1000))
    # contested = 50+100+100 = 250 -> rake 25 from the main pot (150 -> 125); side pot 100 untouched; uncalled 100 returned.
    assert res.rake == 25
    assert res.deltas == [100 - 25, 0, -100]
    assert sum(res.deltas) + res.rake == 0


def test_arena_reports_rake_and_conserves_chips() -> None:
    from botlab.arena import run_arena
    from botlab.engine import Rake
    from botlab.profiles import TRAINING_OPPONENTS

    profs = [TRAINING_OPPONENTS[n] for n in ("Rock", "Maniac", "Calling Station")]
    free = run_arena(profs, hands=300, seed=3)
    raked = run_arena(profs, hands=300, seed=3, rake=Rake(pct=0.05, cap=6))
    d = raked.to_dict()
    assert d["chip_conservation"] is True
    assert d["rake_total_chips"] > 0
    assert free.to_dict()["rake_total_chips"] == 0
    # the same deals: rake can only reduce the players' total result
    assert sum(b.net_chips for b in raked.bots) == -raked.rake_total
    assert sum(b.net_chips for b in free.bots) == 0
