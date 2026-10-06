"""PokerKit as the independent rules oracle: does the EXISTING BotLab evaluator rank hands the same way?

No second engine is introduced: PokerKit only judges. Also round-trips a PHH hand through PokerKit's loader."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

BOTLAB = Path(__file__).resolve().parents[3] / "poker-botlab"
sys.path.insert(0, str(BOTLAB))


def compare_evaluators(n: int = 20000, seed: int = 20261006) -> dict:
    from botlab import cards as cd, evaluator as ev
    from pokerkit import Card, StandardHighHand
    rng = random.Random(seed)
    deck = [r + s for r in "23456789TJQKA" for s in "shdc"]
    agree = disagree = 0
    examples = []
    for _ in range(n):
        cs = rng.sample(deck, 9)
        a, b, board = cs[:2], cs[2:4], cs[4:9]
        sa = ev.evaluate(cd.parse_cards(a + board)); sb = ev.evaluate(cd.parse_cards(b + board))
        ha = StandardHighHand.from_game("".join(a), "".join(board)); hb = StandardHighHand.from_game("".join(b), "".join(board))
        mine = (sa > sb) - (sa < sb)
        ref = (ha > hb) - (ha < hb)
        if mine == ref:
            agree += 1
        else:
            disagree += 1
            if len(examples) < 5: examples.append({"a": a, "b": b, "board": board, "botlab": mine, "pokerkit": ref})
    return {"hands": n, "agree": agree, "disagree": disagree, "examples": examples}


def phh_round_trip() -> dict:
    from pokerkit import Automation, HandHistory, NoLimitTexasHoldem
    game = NoLimitTexasHoldem(
        (Automation.ANTE_POSTING, Automation.BET_COLLECTION, Automation.BLIND_OR_STRADDLE_POSTING, Automation.CARD_BURNING,
         Automation.HAND_KILLING, Automation.CHIPS_PUSHING, Automation.CHIPS_PULLING), True, 0, (5, 10), 10)
    state = NoLimitTexasHoldem.create_state(
        (Automation.ANTE_POSTING, Automation.BET_COLLECTION, Automation.BLIND_OR_STRADDLE_POSTING, Automation.CARD_BURNING,
         Automation.HAND_KILLING, Automation.CHIPS_PUSHING, Automation.CHIPS_PULLING),
        True, 0, (5, 10), 10, (1000, 1000, 1000), 3)
    state.deal_hole("AsKd"); state.deal_hole("7c2h"); state.deal_hole("QsQd")
    state.fold(); state.check_or_call(); state.check_or_call()
    state.deal_board("Qh7d2c"); state.complete_bet_or_raise_to(20); state.check_or_call()
    hh = HandHistory.from_game_state(game, state)
    text = hh.dumps()
    back = HandHistory.loads(text)
    return {"phh_chars": len(text), "round_trip_actions_equal": list(back.actions) == list(hh.actions), "variant": back.variant}


if __name__ == "__main__":
    out = {"evaluator_vs_pokerkit": compare_evaluators(), "phh": phh_round_trip()}
    print(json.dumps(out, indent=1))
