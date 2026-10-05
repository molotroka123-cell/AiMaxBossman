from __future__ import annotations

import json
import random

from botlab.agent import ProfileAgent
from botlab.arena import run_arena
from botlab.cards import parse_cards
from botlab.engine import LegalActions, Observation, play_hand
from botlab.equity import mc_equity, preflop_equity, preflop_percentile
from botlab.learn import LearnConfig, learn
from botlab.profiles import STUDENT, TRAIN_POOL, TRAINING_OPPONENTS, UNSEEN_POOL, Profile, from_vector, to_vector


def test_equity_sanity() -> None:
    rng = random.Random(3)
    assert 0.83 < preflop_equity(parse_cards("AsAh"), 1) < 0.87
    assert 0.32 < preflop_equity(parse_cards("7c2d"), 1) < 0.37
    assert preflop_percentile(parse_cards("AsAh")) < 0.01
    assert preflop_percentile(parse_cards("7c2d")) > 0.95
    # made nut flush on the river vs one random hand is a near lock
    assert mc_equity(parse_cards("AsKs"), parse_cards("Qs 7s 2s 9d 4c"), 1, 400, rng) > 0.95


def test_all_profiles_only_take_legal_actions_in_many_hands() -> None:
    profiles = list(TRAINING_OPPONENTS.values()) + [STUDENT]
    rng = random.Random(5)
    for hand in range(400):
        n = rng.randint(2, 6)
        seated = [ProfileAgent(rng.choice(profiles)) for _ in range(n)]
        stacks = [rng.randint(5, 400) for _ in range(n)]  # odd stacks force all-in edge cases
        res = play_hand(seated, stacks, button=hand % n, small_blind=1, big_blind=2, deck_seed=hand)  # engine validates
        assert sum(res.deltas) == 0


def _obs(**kw: object) -> Observation:
    legal = LegalActions(to_call=50, can_check=False, can_call=True, can_fold=True, can_bet=False,
                         can_raise=True, min_to=100, max_to=60)
    base = dict(hand_id=0, seat=0, street="flop", hole=tuple(parse_cards("AsAh")), board=tuple(parse_cards("Ad Kc 2h")),
                pot=40, to_call=50, stack=60, my_street_bet=0, current_bet=50, big_blind=2, n_players=2,
                n_active_opponents=1, raises_this_street=1, position=0, legal=legal)
    base.update(kw)
    return Observation(**base)  # type: ignore[arg-type]


def test_agent_clamps_raise_to_all_in_when_short() -> None:
    legal = LegalActions(to_call=50, can_check=False, can_call=True, can_fold=True, can_bet=False,
                         can_raise=True, min_to=60, max_to=60)
    agent = ProfileAgent(TRAINING_OPPONENTS["Maniac"])
    for k in range(30):
        act = agent.act(_obs(legal=legal), random.Random(k))
        legal.validate(act)


def test_rock_folds_trash_preflop_station_calls_cheap() -> None:
    legal = LegalActions(to_call=2, can_check=False, can_call=True, can_fold=True, can_bet=False,
                         can_raise=True, min_to=4, max_to=200)
    trash = _obs(street="preflop", hole=tuple(parse_cards("7c2d")), board=(), pot=3, to_call=2, stack=200,
                 current_bet=2, raises_this_street=0, legal=legal)
    assert ProfileAgent(TRAINING_OPPONENTS["Rock"]).act(trash, random.Random(1)).kind == "fold"
    aces = _obs(street="preflop", hole=tuple(parse_cards("AsAh")), board=(), pot=3, to_call=2, stack=200,
                current_bet=2, raises_this_street=0, legal=legal)
    assert ProfileAgent(TRAINING_OPPONENTS["Rock"]).act(aces, random.Random(1)).kind == "raise"


def test_profiles_vector_roundtrip_and_bounds() -> None:
    vec = to_vector(STUDENT)
    assert from_vector(STUDENT, vec, "x").vpip == STUDENT.vpip
    wild = from_vector(STUDENT, [9.0] * len(vec), "x")
    assert wild.vpip <= 0.75 and wild.pfr <= wild.vpip
    assert all(p.training_opponent for p in TRAINING_OPPONENTS.values())
    assert len(TRAINING_OPPONENTS) >= 6
    assert not {p.name for p in TRAIN_POOL} & {p.name for p in UNSEEN_POOL}
    assert Profile.from_dict(STUDENT.to_dict()) == STUDENT


def test_arena_is_deterministic_and_conserves_chips() -> None:
    bots = [STUDENT, TRAINING_OPPONENTS["Rock"], TRAINING_OPPONENTS["Maniac"]]
    r1 = run_arena(bots, hands=90, seed=3).to_dict()
    r2 = run_arena(bots, hands=90, seed=3).to_dict()
    assert r1 == r2
    assert r1["chip_conservation"]
    assert r1["hands"] == 90 and r1["deals"] == 30
    r3 = run_arena(bots, hands=90, seed=4).to_dict()
    assert r3 != r1
    for b in r1["bots"]:
        assert 0 <= b["vpip_pct"] <= 100 and b["pfr_pct"] <= b["vpip_pct"]
        ci = b["bb_per_100"]
        assert ci["ci95_lo"] <= ci["mean"] <= ci["ci95_hi"]


def test_arena_stats_reflect_styles() -> None:
    res = run_arena([TRAINING_OPPONENTS["Rock"], TRAINING_OPPONENTS["Maniac"], TRAINING_OPPONENTS["Calling Station"]],
                    hands=600, seed=1).to_dict()
    by = {b["profile"]: b for b in res["bots"]}
    assert by["Rock"]["vpip_pct"] < by["Calling Station"]["vpip_pct"]
    assert by["Maniac"]["pfr_pct"] > by["Rock"]["pfr_pct"]
    assert by["Maniac"]["af"] > by["Calling Station"]["af"]


def test_learn_smoke_tiny_budget(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cfg = LearnConfig(generations=2, population=4, deals_per_candidate=3, test_deals=4, seed=1)
    report = learn(cfg, out_root=tmp_path, log=lambda _: None)
    run_dir = next(tmp_path.iterdir())
    saved = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert saved["kind"] == "botlab.learn.report"
    assert len(saved["generations"]) == 2
    assert (run_dir / "learned_profile.json").is_file()
    unseen = saved["eval_unseen_pool"]
    d = unseen["paired_diff_bb100"]
    assert saved["improvement_supported_on_unseen"] == (d["ci95_lo"] > 0)
    assert set(unseen["pool"]) == {p.name for p in UNSEEN_POOL}
    assert report["verdict"].split(":")[0] in ("IMPROVED on unseen opponents", "REGRESSED on unseen opponents", "NOT PROVEN")
    # same config -> same learned profile (fixed seeds)
    again = learn(cfg, out_root=None, log=lambda _: None)
    assert again["learned_profile"] == report["learned_profile"]
