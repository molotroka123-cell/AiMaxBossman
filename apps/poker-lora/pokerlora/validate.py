"""Validation of training examples and of model answers. Hidden information can never be an input field."""
from __future__ import annotations

import json
import math

FORBIDDEN_INPUT_KEYS = ("villain_hole", "opponent_cards", "opponent_hole", "deck", "seed", "solver", "strategy", "ev_by_action", "probs")
INPUT_KEYS = {"task", "format", "units", "hero", "board", "pot_at_river_start", "effective_stack", "pot_now", "hero_in_this_street", "to_call",
              "history", "ranges", "legal"}
CARD = set("23456789TJQKA"), set("cdhs")


def _card_ok(c) -> bool:
    return isinstance(c, str) and len(c) == 2 and c[0] in CARD[0] and c[1] in CARD[1]


def validate_input(inp: dict) -> list[str]:
    bad = []
    if set(inp) != INPUT_KEYS:
        bad.append(f"keys differ: extra {sorted(set(inp) - INPUT_KEYS)} missing {sorted(INPUT_KEYS - set(inp))}")
    flat = json.dumps(inp).lower()
    for k in FORBIDDEN_INPUT_KEYS:
        if f'"{k}"' in flat:
            bad.append(f"forbidden field {k!r} in the model input")
    hole, board = inp.get("hero", {}).get("hole", []), inp.get("board", [])
    if len(hole) != 2 or len(board) != 5 or not all(_card_ok(c) for c in hole + board):
        bad.append("cards malformed")
    elif len(set(hole + board)) != 7:
        bad.append("duplicate cards")
    for k in ("pot_at_river_start", "effective_stack", "pot_now"):
        v = inp.get(k)
        if not isinstance(v, (int, float)) or not v > 0 or not math.isfinite(v):
            bad.append(f"{k} not positive")
    if not inp.get("legal"):
        bad.append("no legal actions")
    if inp.get("hero", {}).get("position") not in ("OOP", "IP"):
        bad.append("position")
    return bad


def validate_reference(ref: dict, inp: dict) -> list[str]:
    bad = []
    legal = {a["action"] for a in inp["legal"]}
    probs = ref.get("probs", {})
    if not probs or set(probs) - legal:
        bad.append("probs outside the legal set")
    if abs(sum(probs.values()) - 1.0) > 1e-3:
        bad.append("probs do not sum to 1")
    if ref.get("action") not in legal:
        bad.append("reference action not legal")
    return bad


def parse_model_output(text_or_obj) -> dict | None:
    if isinstance(text_or_obj, dict):
        return text_or_obj
    try:
        s = str(text_or_obj).strip()
        a, b = s.find("{"), s.rfind("}")
        return json.loads(s[a:b + 1]) if a >= 0 and b > a else None
    except (ValueError, TypeError):
        return None


def validate_output(out: dict | None, inp: dict) -> tuple[bool, list[str], dict | None]:
    """A model answer is acceptable only if it is parseable, names a legal action, its size equals that action's amount, and probabilities (if
    given) are a distribution over legal actions. Returns (ok, reasons, normalised {action: prob})."""
    if not isinstance(out, dict):
        return False, ["not a JSON object"], None
    legal = {a["action"]: a["amount"] for a in inp["legal"]}
    reasons = []
    act = out.get("action")
    if act not in legal:
        reasons.append(f"illegal or missing action {act!r}")
    size = out.get("size")
    if act in legal and size is not None and abs(float(size) - legal[act]) > max(0.01 * legal[act], 0.05):
        reasons.append(f"size {size} does not match the action's amount {legal[act]}")
    probs = out.get("probs")
    if probs is not None:
        if not isinstance(probs, dict) or not probs or set(probs) - set(legal) or any((not isinstance(v, (int, float))) or v < 0 for v in probs.values()):
            reasons.append("probs malformed or outside the legal set")
        elif abs(sum(probs.values()) - 1.0) > 0.03:
            reasons.append("probs do not sum to 1")
    if reasons:
        return False, reasons, None
    dist = {a: 0.0 for a in legal}
    if probs:
        tot = sum(probs.values())
        for a, v in probs.items():
            dist[a] = v / tot
    else:
        dist[act] = 1.0
    return True, [], dist
