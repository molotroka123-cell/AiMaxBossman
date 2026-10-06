"""Impossible-state checks. A failed check never "repairs" a value: it demotes the offending fields to UNKNOWN."""
from __future__ import annotations

from .schema import Field, Money, STREETS, TableState, money_value

BOARD_BY_STREET = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}
STREET_BY_COUNT = {v: k for k, v in BOARD_BY_STREET.items()}


def _issue(state: TableState, code: str, severity: str, fields: list[str], detail: str = "") -> None:
    state.issues.append({"code": code, "severity": severity, "fields": fields, "detail": detail})


def validate_state(state: TableState) -> TableState:
    """Single-frame consistency. Mutates ``state`` (adds issues, demotes fields) and returns it."""
    # 1) duplicate cards across hero + board
    seen: dict[str, str] = {}
    dup_fields: set[str] = set()
    for name, fields in (("hero_cards", state.hero_cards), ("board", state.board)):
        for i, f in enumerate(fields):
            if not f.known:
                continue
            key = f.value
            if key in seen:
                dup_fields.update({seen[key], f"{name}[{i}]"})
            else:
                seen[key] = f"{name}[{i}]"
    if dup_fields:
        _issue(state, "DUPLICATE_CARD", "error", sorted(dup_fields), "same card seen twice")
        for name, fields in (("hero_cards", state.hero_cards), ("board", state.board)):
            for i, f in enumerate(fields):
                if f"{name}[{i}]" in dup_fields:
                    fields[i] = f.demote("duplicate card across hero/board")
    # 2) board count / street
    n = len(state.board)
    known_n = sum(1 for f in state.board if f.known)
    if n not in BOARD_BY_STREET.values():
        _issue(state, "BAD_BOARD_COUNT", "error", ["board"], f"{n} board slots")
        state.board_count = state.board_count.demote("impossible board size")
        state.street = state.street.demote("impossible board size")
    elif known_n != n:
        _issue(state, "BOARD_PARTIAL", "warn", ["board"], f"{known_n}/{n} board cards readable")
    if state.street.known and state.street.value not in STREETS:
        _issue(state, "BAD_STREET", "error", ["street"], str(state.street.value))
        state.street = state.street.demote("unknown street label")
    if state.street.known and state.board_count.known and BOARD_BY_STREET.get(state.street.value) != state.board_count.value:
        _issue(state, "STREET_BOARD_MISMATCH", "error", ["street", "board_count"], f"{state.street.value} vs {state.board_count.value}")
        state.street = state.street.demote("street/board mismatch")
    # 3) numbers
    for name in ("pot", "to_call", "hero_stack"):
        f: Field = getattr(state, name)
        if f.known and (not isinstance(f.value, Money) or f.value.amount < 0):
            _issue(state, "BAD_NUMBER", "error", [name], str(f.value))
            setattr(state, name, f.demote("negative or malformed amount"))
    pot, bets = money_value(state.pot), [money_value(s.bet) for s in state.seats if s.bet.known]
    hs = money_value(state.hero_stack)
    if pot is not None and bets and sum(b for b in bets if b is not None) > pot * 1.001 + 1:
        _issue(state, "BETS_EXCEED_POT", "warn", ["pot", "seats.bet"], f"sum(bets)={sum(bets)} > pot={pot}")
    tc = money_value(state.to_call)
    if tc is not None and hs is not None and tc > 0 and hs < 0:
        _issue(state, "BAD_STACK", "error", ["hero_stack"], "negative stack")
    # 4) mixed currency
    cur = {f.value.currency for f in (state.pot, state.to_call, state.hero_stack) if f.known and f.value.currency}
    if len(cur) > 1:
        _issue(state, "MIXED_CURRENCY", "error", ["pot", "to_call", "hero_stack"], str(sorted(cur)))
        for name in ("pot", "to_call", "hero_stack"):
            setattr(state, name, getattr(state, name).demote("mixed currency"))
    # 5) hero cards count
    if len(state.hero_cards) not in (0, 2):
        _issue(state, "BAD_HERO_COUNT", "error", ["hero_cards"], f"{len(state.hero_cards)} slots")
    return state


def check_transition(prev: TableState | None, cur: TableState) -> list[dict]:
    """Cross-frame consistency inside one hand. Returns issues (does not mutate)."""
    out: list[dict] = []
    if prev is None:
        return out
    ps, cs = prev.street, cur.street
    if ps.known and cs.known and STREETS.index(cs.value) < STREETS.index(ps.value):
        out.append({"code": "STREET_REGRESSION", "severity": "error", "fields": ["street"], "detail": f"{ps.value}->{cs.value}"})
    pb, cb = prev.card_strings("board"), cur.card_strings("board")
    k = min(len(pb), len(cb))
    for i in range(k):
        if pb[i] and cb[i] and pb[i] != cb[i]:
            out.append({"code": "BOARD_CARD_CHANGED", "severity": "error", "fields": [f"board[{i}]"], "detail": f"{pb[i]}->{cb[i]}"})
    if len(cb) < len(pb) and all(cb):
        out.append({"code": "BOARD_SHRANK", "severity": "error", "fields": ["board"], "detail": f"{len(pb)}->{len(cb)}"})
    ph, ch = prev.card_strings("hero"), cur.card_strings("hero")
    if ph and ch and all(ph) and all(ch) and ph != ch:
        out.append({"code": "HERO_CARDS_CHANGED", "severity": "error", "fields": ["hero_cards"], "detail": f"{ph}->{ch}"})
    pp, cp = money_value(prev.pot), money_value(cur.pot)
    if pp is not None and cp is not None and cp + 0.5 < pp:
        out.append({"code": "POT_DECREASED", "severity": "warn", "fields": ["pot"], "detail": f"{pp}->{cp}"})
    return out


def action_order_issues(events: list[dict], num_seats: int | None, first_to_act_slot: int | None) -> list[dict]:
    """Within a street, actors must advance clockwise without skipping a still-active seat unless a gap is flagged."""
    out: list[dict] = []
    if not num_seats or first_to_act_slot is None:
        return out
    last = None
    for e in events:
        if e.get("type") in ("gap", "deal_street") or e.get("slot") is None:
            last = None if e.get("type") == "deal_street" else last
            continue
        if last is not None:
            step = (e["slot"] - last) % num_seats
            if step == 0 and e.get("type") not in ("raise", "bet"):
                out.append({"code": "REPEATED_ACTOR", "severity": "warn", "fields": ["events"], "detail": f"slot {e['slot']} acted twice in a row"})
        last = e["slot"]
    return out
