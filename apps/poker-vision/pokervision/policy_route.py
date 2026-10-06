"""Route: validated state -> (optional) Poker-LoRA policy -> action validation -> PolicyDecision for the executor.

Vision and clicking stay separate: the policy only ever sees a model INPUT built from the committed (validated) state. Its answer is accepted only if
`pokerlora.validate.validate_output` accepts it (legal action, size equal to that action's amount, probabilities over legal actions) AND it maps onto
buttons that are on screen. Anything else falls back to the transparent heuristic and goes to the review queue (never to labels).

Applicability is strict: the model was trained on heads-up river spots with GIVEN ranges. From vision alone that is rarely PROVABLE (opponent count,
position and the street's betting history are not read reliably), so the route says "not applicable" instead of guessing. Ranges are never observed:
they must be supplied by the caller and are flagged as assumed."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .control.intent import PolicyDecision, hand_key

_LORA = Path(__file__).resolve().parents[2] / "poker-lora"


def _lora():
    if str(_LORA) not in sys.path:
        sys.path.insert(0, str(_LORA))
    from pokerlora import validate, ranges   # noqa: F401
    return validate, ranges


@dataclass
class Applicability:
    ok: bool
    reason: str = ""


def applicable(cm, *, heads_up_confirmed: bool, position: str | None, history: list | None, ranges: dict | None) -> Applicability:
    if len([c for c in cm.board if c]) != 5:
        return Applicability(False, "not the river")
    if not heads_up_confirmed:
        return Applicability(False, "heads-up is not confirmed (the format of the model is heads-up only)")
    if position not in ("OOP", "IP"):
        return Applicability(False, "hero position (OOP/IP) is not known")
    if history is None:
        return Applicability(False, "the river betting history is not known")
    if not ranges or "hero" not in ranges or "villain" not in ranges:
        return Applicability(False, "ranges were not supplied (they are never observed; they must be given and are flagged as assumed)")
    if cm.blocked or cm.hero_turn is not True or not all(cm.hero_cards) or not cm.pot or not cm.hero_stack or not cm.actions:
        return Applicability(False, "state is not validated for acting")
    return Applicability(True)


def build_input(cm, position: str, history: list, ranges: dict, pot_at_river_start: float, effective_stack: float) -> dict:
    """Model input from the committed state. Raises ValueError when a legal-action mapping would be a guess."""
    names = {a[0]: a[1] for a in cm.actions}
    pot_now = cm.pot.amount
    to_call = float(names.get("CALL") or 0.0) if "CALL" in names else 0.0
    put = max(0.0, effective_stack - cm.hero_stack.amount)
    legal = []
    if "FOLD" in names and to_call > 0:
        legal.append({"action": "fold", "amount": 0.0})
    if "CHECK" in names:
        legal.append({"action": "check", "amount": 0.0})
    if "CALL" in names:
        legal.append({"action": "call", "amount": to_call})
    if "RAISE" in names or "ALL IN" in names:
        if to_call <= 0:
            for lab, f in (("bet50", 0.5), ("bet100", 1.0)):
                amt = round(f * pot_now, 2)
                if 0 < amt < cm.hero_stack.amount:
                    legal.append({"action": lab, "amount": amt})
        else:
            extra = round(pot_now + to_call, 2)
            if to_call + extra < cm.hero_stack.amount:
                legal.append({"action": "raise", "amount": round(to_call + extra, 2)})
        if cm.hero_stack.amount <= 3 * max(pot_at_river_start, 1) or not any(a["action"] in ("bet50", "bet100", "raise") for a in legal):
            legal.append({"action": "allin", "amount": float(cm.hero_stack.amount)})
    if not legal:
        raise ValueError("no legal action could be derived from the buttons")
    return {"task": "hu_nlhe_river_action", "format": "HU NLHE river, abstracted bet sizes", "units": "chips",
            "hero": {"position": position, "hole": list(cm.hero_cards)}, "board": [c for c in cm.board if c],
            "pot_at_river_start": pot_at_river_start, "effective_stack": effective_stack, "pot_now": pot_now, "hero_in_this_street": round(put, 2),
            "to_call": to_call, "history": history, "ranges": ranges, "legal": legal}


def to_decision(cm, inp: dict, dist: dict, out: dict) -> PolicyDecision | None:
    """Map the validated abstract action onto the on-screen buttons. A mapping that would change the amount is refused (None)."""
    act = max(dist, key=dist.get)
    names = [a[0] for a in cm.actions]
    amt = {a["action"]: a["amount"] for a in inp["legal"]}[act]
    hk = hand_key(list(cm.hero_cards), list(cm.board))
    street = "river"
    if act == "fold" and "FOLD" in names:
        return PolicyDecision("FOLD", None, hk, street, cm.t_ms, reason="poker-lora: " + str(out.get("explanation", ""))[:120])
    if act == "check" and "CHECK" in names:
        return PolicyDecision("CHECK", None, hk, street, cm.t_ms, reason="poker-lora")
    if act == "call" and "CALL" in names:
        return PolicyDecision("CALL", None, hk, street, cm.t_ms, to_call=inp["to_call"], reason="poker-lora")
    if act in ("bet50", "bet100", "raise", "allin") and ("RAISE" in names or "ALL IN" in names):
        lo, hi = (amt * 0.8, amt * 1.2) if act != "allin" else (amt, amt)
        return PolicyDecision("RAISE", amt, hk, street, cm.t_ms, reason="poker-lora", size_min=lo, size_max=hi)
    return None


def decide_with_policy(cm, policy, *, position, history, ranges, pot_at_river_start, effective_stack, heads_up_confirmed, review_root: Path | None = None):
    """Returns (PolicyDecision | None, info). None means: use the heuristic (info says why)."""
    ap = applicable(cm, heads_up_confirmed=heads_up_confirmed, position=position, history=history, ranges=ranges)
    if not ap.ok:
        return None, {"source": "heuristic", "why": ap.reason}
    validate, _ = _lora()
    try:
        inp = build_input(cm, position, history, ranges, pot_at_river_start, effective_stack)
    except ValueError as exc:
        return None, {"source": "heuristic", "why": str(exc)}
    bad = validate.validate_input(inp)
    if bad:
        return None, {"source": "heuristic", "why": "input invalid: " + "; ".join(bad)}
    try:
        raw = policy.act(inp)
    except Exception as exc:  # noqa: BLE001
        _review(review_root, inp, None, [f"policy error {type(exc).__name__}"])
        return None, {"source": "heuristic", "why": f"policy error {type(exc).__name__}"}
    out = validate.parse_model_output(raw)
    ok, reasons, dist = validate.validate_output(out, inp)
    if not ok:
        _review(review_root, inp, raw, reasons)
        return None, {"source": "heuristic", "why": "answer rejected: " + "; ".join(reasons)}
    d = to_decision(cm, inp, dist, out)
    if d is None:
        _review(review_root, inp, raw, ["action does not map onto the buttons on screen"])
        return None, {"source": "heuristic", "why": "action does not map onto the buttons on screen"}
    return d, {"source": "poker_lora", "probs": dist, "assumed_ranges": True, "explanation": out.get("explanation", "")}


def _review(root, inp, out, reasons):
    if root is None:
        return
    if str(_LORA) not in sys.path:
        sys.path.insert(0, str(_LORA))
    from pokerlora import mistakes
    mistakes.record(Path(root), inp, out, reasons, "bossman-live")
