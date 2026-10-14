"""Tests for rigorous GTO poker mathematics, board texture & range/nut advantage, street EV loss,
and the 5-player tournament (Claude, Codex, Argon, Local Bossman, GTO Reference Solver Bot).
"""
from __future__ import annotations

import math
import random

import pytest

from botlab.cards import parse_cards
from botlab.engine import LegalActions
from botlab.gto import (
    BB_DEFENSE_RANGES,
    RFI_RANGES,
    VS_OPEN_RANGES,
    analyze_spot_ev,
    calibrate_bossman_from_tournament,
    classify_board,
    compute_range_and_nut_advantage,
    geometric_bet_amount,
    geometric_bet_fraction,
    mdf,
    optimal_bluff_alpha,
    optimal_bluff_share,
    pot_odds,
    range_combos,
    required_equity,
    run_5max_gto_tournament,
    seat_position_name,
    spr,
)


def test_pot_odds_mdf_and_alpha_exact_identities() -> None:
    # Half-pot bet: pot=100, bet=50 -> total pot after call = 200 -> pot_odds = 50/200 = 0.25
    assert pot_odds(100, 50) == pytest.approx(0.25)
    # Equivalent when pot_including_bet = 150 and to_call = 50
    assert pot_odds(150, 50, pot_includes_bet=True) == pytest.approx(0.25)
    # MDF = 100 / 150 = 2/3, alpha = 50 / 150 = 1/3
    assert mdf(100, 50) == pytest.approx(2.0 / 3.0)
    assert mdf(150, 50, pot_includes_bet=True) == pytest.approx(2.0 / 3.0)
    assert optimal_bluff_alpha(100, 50) == pytest.approx(1.0 / 3.0)
    assert optimal_bluff_share(100, 50) == pytest.approx(0.25)

    # Fundamental identities across arbitrary pot and bet sizes
    for p in (20.0, 75.0, 100.0, 340.0):
        for b in (10.0, 25.0, 50.0, 100.0, 200.0):
            po = pot_odds(p, b)
            m = mdf(p, b)
            a = optimal_bluff_alpha(p, b)
            bs = optimal_bluff_share(p, b)
            assert m + a == pytest.approx(1.0, abs=1e-12)
            assert a / (1.0 + a) == pytest.approx(po, abs=1e-12)
            assert bs == pytest.approx(po, abs=1e-12)
            assert pot_odds(p + b, b, pot_includes_bet=True) == pytest.approx(po, abs=1e-12)

    # Edge cases: zero or negative bet/pot
    assert pot_odds(100, 0) == 0.0
    assert pot_odds(100, -5) == 0.0
    assert mdf(100, 0) == 1.0
    assert optimal_bluff_alpha(100, 0) == 0.0
    assert required_equity(100, 50, realization=0.80) == pytest.approx(0.25 / 0.80)
    assert required_equity(100, 100, realization=0.10) == 1.0


def test_spr_and_geometric_bet_sizing_exact_all_in_property() -> None:
    assert spr(300, 100) == pytest.approx(3.0)
    assert math.isinf(spr(100, 0))

    # Over N streets of betting fraction f of the current pot (and getting called),
    # the pot grows as pot * (1 + 2*f)^N and must equal pot + 2 * effective_stack.
    for pot_init in (40.0, 100.0, 250.0):
        for stack_init in (50.0, 150.0, 400.0, 1000.0):
            for n_streets in (1, 2, 3):
                f = geometric_bet_fraction(stack_init, pot_init, n_streets)
                cur_pot = pot_init
                rem_stack = stack_init
                for _ in range(n_streets):
                    bet = cur_pot * f
                    rem_stack -= bet
                    cur_pot += 2.0 * bet
                assert rem_stack == pytest.approx(0.0, abs=1e-6)
                assert cur_pot == pytest.approx(pot_init + 2.0 * stack_init, abs=1e-6)

    assert geometric_bet_amount(0, 100, 3) == 0.0
    assert geometric_bet_amount(100, 0, 3) == 0.0


def test_positional_5max_6max_ranges_and_seat_mapping() -> None:
    # 5-max seat positions: 0=BTN, 1=SB, 2=BB, 3=HJ, 4=CO
    assert [seat_position_name(i, 5) for i in range(5)] == ["BTN", "SB", "BB", "HJ", "CO"]
    # 6-max seat positions: 0=BTN, 1=SB, 2=BB, 3=UTG, 4=HJ, 5=CO
    assert [seat_position_name(i, 6) for i in range(6)] == ["BTN", "SB", "BB", "UTG", "HJ", "CO"]

    # Range nesting: UTG <= HJ <= CO <= BTN <= SB
    combos = {pos: range_combos(RFI_RANGES[pos]["raise"]) for pos in ("UTG", "HJ", "CO", "BTN", "SB")}
    assert combos["UTG"] < combos["HJ"] < combos["CO"] < combos["BTN"] < combos["SB"]
    assert RFI_RANGES["UTG"]["raise"] <= RFI_RANGES["HJ"]["raise"] <= RFI_RANGES["CO"]["raise"] <= RFI_RANGES["BTN"]["raise"]
    assert range_combos(BB_DEFENSE_RANGES["vs_BTN"]["call"]) > range_combos(BB_DEFENSE_RANGES["vs_UTG"]["call"])
    assert range_combos(VS_OPEN_RANGES["vs_BTN"]["threebet"]) > range_combos(VS_OPEN_RANGES["vs_UTG"]["threebet"])


def test_board_texture_classification_and_range_nut_advantage() -> None:
    assert classify_board([]).primary == "preflop"

    # Dry rainbow board: As 7d 2c
    dry = classify_board("As 7d 2c")
    assert dry.primary == "dry" and "dry" in dry.tags and dry.rainbow and dry.recommended_cbet_frac == pytest.approx(0.33)

    # Monotone board: Ks 9s 4s
    mono = classify_board("Ks 9s 4s")
    assert mono.primary == "monotone" and mono.monotone and mono.flush_possible

    # Paired board: Kh Kd 4c
    paired = classify_board("Kh Kd 4c")
    assert paired.primary == "paired" and paired.paired

    # Wet two-tone connected board: Jh Th 9c
    wet = classify_board("Jh Th 9c")
    assert wet.primary == "wet" and wet.connected and wet.two_tone and wet.recommended_cbet_frac >= 0.60

    # Connected rainbow board: 8c 7d 6h
    conn = classify_board("8c 7d 6h")
    assert conn.primary in ("connected", "wet") and conn.connected and conn.straight_possible

    # Range & nut advantage: A-K-Q rainbow strongly favors UTG/BTN aggressor over BB defender
    adv_high = compute_range_and_nut_advantage("As Kd Qc", aggressor_pos="UTG", defender_pos="BB")
    adv_low = compute_range_and_nut_advantage("6s 5s 4d", aggressor_pos="BTN", defender_pos="BB")
    assert adv_high.range_advantage > adv_low.range_advantage
    assert adv_high.nut_advantage > adv_low.nut_advantage
    assert adv_high.favors == "aggressor"


def test_street_by_street_ev_and_mistake_detection() -> None:
    # 1. River nuts facing a bet: folding is a huge EV loss, calling/raising has high positive EV
    legal_river = LegalActions(to_call=50, can_check=False, can_call=True, can_fold=True, can_bet=False, can_raise=True, min_to=150, max_to=300)
    ev_nuts = analyze_spot_ev(
        hole=parse_cards("As Ks"),
        board=parse_cards("Qs Js Ts 2d 4c"),
        street="river",
        position="BTN",
        pot_including_bet=150,
        to_call=50,
        stack=300,
        big_blind=2,
        n_opponents=1,
        raises_this_street=1,
        legal=legal_river,
        chosen_action="fold",
        rng=random.Random(1),
    )
    assert ev_nuts.ev_fold == 0.0
    assert ev_nuts.ev_call is not None and ev_nuts.ev_call > 100.0
    assert ev_nuts.best_action in ("raise", "call")
    assert ev_nuts.delta_ev_bb > 50.0
    assert ev_nuts.mistake_type in ("overfold_vs_mdf", "bad_fold")

    # 2. River air (7c 2d on Ah Ks Qd 9c 4s) facing a pot-sized bet: calling loses EV, folding is optimal
    ev_air = analyze_spot_ev(
        hole=parse_cards("7c 2d"),
        board=parse_cards("Ah Ks Qd 9c 4s"),
        street="river",
        position="BB",
        pot_including_bet=200,
        to_call=100,
        stack=200,
        big_blind=2,
        n_opponents=1,
        raises_this_street=1,
        legal=legal_river,
        chosen_action="call",
        rng=random.Random(1),
    )
    assert ev_air.ev_call is not None and ev_air.ev_call < -50.0
    assert ev_air.best_action == "fold"
    assert ev_air.delta_ev_bb > 25.0
    assert ev_air.mistake_type == "bad_call_pot_odds"


def test_5max_tournament_determinism_chip_conservation_and_calibration() -> None:
    # Run a fast 100-hand 5-max tournament twice with the same seed -> must be 100% deterministic
    r1 = run_5max_gto_tournament(hands=100, seed=20261014, bossman_mode="baseline")
    r2 = run_5max_gto_tournament(hands=100, seed=20261014, bossman_mode="baseline")
    assert r1["standings"] == r2["standings"]
    assert r1["chip_conservation"] is True and r1["net_chips_sum"] == 0
    assert len(r1["seats"]) == 5
    seat_names = [s["name"] for s in r1["seats"]]
    assert seat_names == ["Claude", "Codex", "Argon", "Local Bossman", "GTO Reference Solver Bot"]

    cal = calibrate_bossman_from_tournament(r1)
    assert len(cal["leaks_diagnosed"]) >= 2

    r_cal = run_5max_gto_tournament(hands=100, seed=20261014, bossman_mode="calibrated", calibrated_params=cal["calibrated_parameters"])
    assert r_cal["chip_conservation"] is True and r_cal["net_chips_sum"] == 0

    b_pre = next(s for s in r1["seats"] if s["name"] == "Local Bossman")
    b_post = next(s for s in r_cal["seats"] if s["name"] == "Local Bossman")
    # Post-calibration Local Bossman must drastically reduce EV loss per hand and improve bb/100
    assert b_post["avg_ev_loss_bb_per_hand"] < b_pre["avg_ev_loss_bb_per_hand"]
    assert b_post["bb_per_100"] > b_pre["bb_per_100"]
    assert b_post["gto_agreement_pct"] > b_pre["gto_agreement_pct"]
