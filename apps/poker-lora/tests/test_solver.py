"""Solver correctness: a toy game with a KNOWN equilibrium, exploitability, zero-sum, blocker handling."""
import numpy as np
import pytest

from pokerlora.solver import Solver, Spot, TreeConfig, build_tree


def toy(pot=1.0, stack=1.0):
    # OOP: half nuts (strength 2), half air (0); IP: all bluff-catchers (1). One bet size = pot, no raise, no all-in extras.
    cfg = TreeConfig(pot=pot, stack=stack, bet_fracs=(1.0,), allow_raise=False, allow_allin=False)
    s0 = np.array([2.0, 0.0]); s1 = np.array([1.0])
    return Spot(cfg, s0, s1, np.array([1.0, 1.0]), np.array([1.0]), np.ones((2, 1)))


def test_toy_game_matches_the_known_equilibrium():
    sv = Solver(toy(stack=10.0))
    ex = sv.solve(3000)
    assert ex < 5e-3
    root = sv.nodes[0]
    sig = sv.average(root)                         # [hand, action]: check, bet100
    labels = [a for a, _ in root.actions]
    bet = labels.index("bet100")
    assert sig[0, bet] > 0.97                      # nuts always bet for value
    assert sig[1, bet] == pytest.approx(0.5, abs=0.03)   # air bluffs so that bluffs:value = 1:2 (bettor bluffs 1/3 of bets)
    facing = [n for n in sv.nodes if n.kind == "decision" and n.player == 1 and n.facing > 0][0]
    call = [a for a, _ in facing.actions].index("call")
    assert sv.average(facing)[0, call] == pytest.approx(0.5, abs=0.03)    # IP calls 1/2 (pot-sized bet)


def test_zero_sum_and_exploitability_of_a_bad_profile_is_large():
    sv = Solver(toy(stack=10.0)); sv.solve(1500)
    v0, v1 = sv.values()
    assert v0 + v1 == pytest.approx(0.0, abs=1e-6)
    # a profile where IP always folds is hugely exploitable
    fold_nodes = [n for n in sv.nodes if n.kind == "decision" and n.player == 1 and n.facing > 0]
    prof = {}
    for n in fold_nodes:
        m = np.zeros((1, len(n.actions))); m[0, 0] = 1.0; prof[n.nid] = m
    assert sv.exploitability(prof) > 0.1


def test_blockers_remove_impossible_matchups():
    cfg = TreeConfig(pot=10.0, stack=40.0)
    s0 = np.array([5.0, 3.0]); s1 = np.array([4.0, 1.0])
    compat = np.array([[1, 0], [1, 1]], dtype=float)       # hand 0 of OOP shares a card with hand 1 of IP
    sv = Solver(Spot(cfg, s0, s1, np.ones(2), np.ones(2), compat))
    assert sv.solve(800) < 0.05 * 10
    v0, v1 = sv.values()
    assert v0 + v1 == pytest.approx(0.0, abs=1e-6)


def test_tree_has_the_documented_shape():
    nodes = build_tree(TreeConfig(pot=10.0, stack=100.0))
    root = nodes[0]
    assert [a for a, _ in root.actions] == ["check", "bet50", "bet100"]          # spr 10: no extra all-in option
    short = build_tree(TreeConfig(pot=10.0, stack=20.0))
    assert [a for a, _ in short[0].actions][-1] == "allin"
