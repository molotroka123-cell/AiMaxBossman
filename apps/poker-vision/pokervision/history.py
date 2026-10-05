"""Observed-event history from committed snapshots, PHH export, and incompleteness bookkeeping.

Only what was observed is recorded. Anything that cannot be seen (checks in Poker Train, actions during a frame gap) is
listed under ``unobservable`` / ``gaps`` — never invented. The history is explicitly flagged complete=False in that case.
"""
from __future__ import annotations

import json
from typing import Any

POKER_TRAIN_UNOBSERVABLE = ["checks (no visible change)", "hero's own actions unless taken through the actuator log"]


def _amt(m) -> float | None:
    return None if m is None else (m["amount"] if isinstance(m, dict) else m.amount)


def derive_events(hand: dict, unobservable: list[str] | None = None) -> dict:
    """Walk the hand's committed snapshots and emit observed events with their evidence times."""
    snaps = hand["snapshots"]
    events: list[dict] = []
    gaps = list(hand.get("gaps", []))
    prev: dict | None = None
    first = True
    for sn in snaps:
        t = sn["t_ms"]
        if prev is None:
            if all(sn["hero_cards"]):
                events.append({"type": "deal_hero", "cards": sn["hero_cards"], "t": t})
            for slot, sd in sn["seats"].items():
                if sd.get("bet"):
                    events.append({"type": "initial_bet", "slot": slot, "amount": _amt(sd["bet"]), "t": t, "note": "blind/ante or action before first observation"})
            if sn["street"] not in (None, "preflop") or sn["board"]:
                hand.setdefault("flags", []).append("starts_mid_hand")
            prev = sn
            continue
        # new street / board cards
        pb, cb = prev["board"], sn["board"]
        if len(cb) > len(pb):
            for i in range(len(pb), len(cb)):
                events.append({"type": "deal_board", "index": i, "card": cb[i], "t": t, "readable": cb[i] is not None})
        # seat bets
        pmax = max([_amt(v.get("bet")) or 0 for v in prev["seats"].values()] + [0])
        for slot, sd in sn["seats"].items():
            nb = _amt(sd.get("bet"))
            ob = _amt(prev["seats"].get(slot, {}).get("bet")) if slot in prev["seats"] else None
            if nb is None or nb <= 0:
                continue
            if ob is not None and abs(nb - ob) < 0.5:
                continue
            if len(cb) > len(pb) and ob is None:
                pmax = 0           # bets are swept into the pot when a street starts
            others = [(_amt(v.get("bet")) or 0) for k, v in sn["seats"].items() if k != slot]
            hi = max(others + [pmax if len(cb) == len(pb) else 0, 0])
            if hi <= 0:
                typ = "bet"
            elif abs(nb - hi) < 0.5:
                typ = "call"
            elif nb > hi:
                typ = "raise"
            else:
                typ = "bet_change"      # below the current highest bet and not equal: cannot classify honestly
            events.append({"type": typ, "slot": slot, "to": nb, "t": t})
        # folds: a seat that was present is gone while the hand continues (street unchanged or later)
        for slot in prev["seats"]:
            if slot not in sn["seats"] and sn["hero_cards"] and all(sn["hero_cards"]):
                events.append({"type": "seat_left_hand", "slot": slot, "t": t, "note": "fold (seat hidden by the UI) — presumed, not shown"})
        prev = sn
    hand["events"] = events
    hand["unobservable"] = list(unobservable or [])
    hand["gaps"] = gaps
    hand["complete"] = False if (gaps or unobservable or hand.get("flags") or not hand.get("snapshots")) else bool(hand.get("complete"))
    return hand


def to_phh(hand: dict, game: str = "NT", cards_ok_only: bool = True) -> str:
    """PHH-like TOML for an *incomplete* observed hand. Unknown values are omitted, observation metadata goes in [vision]."""
    lines = ['variant = "NT"', "# Observed by pixels: not a complete hand history (see [vision].complete)"]
    hero = None
    for e in hand["events"]:
        if e["type"] == "deal_hero":
            hero = e["cards"]
    board = [e["card"] for e in hand["events"] if e["type"] == "deal_board" and e.get("readable")]
    if hero:
        lines.append(f'hero_cards = "{"".join(hero)}"')
    if board:
        lines.append(f'board_cards = "{"".join(board)}"')
    acts = []
    for e in hand["events"]:
        if e["type"] in ("bet", "raise"):
            acts.append(f'"slot{e["slot"]} cbr {int(e["to"])}"')
        elif e["type"] == "call":
            acts.append(f'"slot{e["slot"]} cc"')
    lines.append("observed_actions = [" + ", ".join(acts) + "]")
    lines.append("")
    lines.append("[vision]")
    lines.append(f'complete = {str(bool(hand.get("complete"))).lower()}')
    lines.append(f'linked_to_previous = {str(bool(hand.get("linked", True))).lower()}')
    lines.append("unobservable = " + json.dumps(hand.get("unobservable", [])))
    lines.append("gaps = " + json.dumps(hand.get("gaps", [])))
    lines.append("flags = " + json.dumps(hand.get("flags", [])))
    return "\n".join(lines) + "\n"
