"""Field-level scoring of a TableState against DOM truth. Outcomes: ok / wrong / unknown / na."""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from ..parse import parse_money
from ..schema import TableState, Money

CORE = ("hero_cards", "board", "pot", "to_call", "hero_stack", "street")
FIELDS = CORE + ("seat_stacks", "bets", "dealer", "actions", "hero_turn")


def _money_cmp(pred, truth_text) -> str:
    tm = parse_money(truth_text) if truth_text else None
    if tm is None:
        return "na" if not pred.known else "wrong"     # prediction where nothing is displayed = false positive
    if not pred.known:
        return "unknown"
    return "ok" if isinstance(pred.value, Money) and pred.value.agrees(tm) else "wrong"


def _angle(dx, dy):
    return math.degrees(math.atan2(dy, dx)) % 360


VIS_OK, VIS_HIDDEN = 0.99, 0.34


def _inview(r: dict | None, truth: dict) -> float:
    if not r:
        return 1.0
    w, h = max(r["w"], 1e-6), max(r["h"], 1e-6)
    ix = max(0.0, min(r["x"] + w, truth["vw"]) - max(r["x"], 0.0)) / w
    iy = max(0.0, min(r["y"] + h, truth["vh"]) - max(r["y"], 0.0)) / h
    return ix * iy


def _vis_state(v, inview: float = 1.0) -> str:
    """visible = nothing on top and fully inside the viewport; hidden = covered or outside; otherwise partial (not scored)."""
    v = 1.0 if v is None else v
    eff = v * inview
    if v >= VIS_OK and inview >= VIS_OK:
        return "visible"
    return "hidden" if eff <= VIS_HIDDEN else "partial"


def _align_cards(pred_fields, pred_x, truth_cards, dpr):
    """Pair each truth card with the predicted card whose centre is within half a card width. Returns list of (truth, pred_field|None)."""
    out = []
    used = set()
    for tc in truth_cards:
        tx = (tc["x"] + tc["w"] / 2) * dpr
        best, bd = None, 1e9
        for i, px in enumerate(pred_x):
            if i in used or i >= len(pred_fields):
                continue
            d = abs(px - tx)
            if d < bd:
                best, bd = i, d
        if best is not None and bd <= 0.5 * tc["w"] * dpr:
            used.add(best)
            out.append((tc, pred_fields[best]))
        else:
            out.append((tc, None))
    extra = [pred_fields[i] for i in range(min(len(pred_fields), len(pred_x))) if i not in used]
    return out, extra


def _cards_outcome(pairs, extra, truth):
    wrong = unknown = 0
    detail = []
    for tc, pf in pairs:
        vs = _vis_state(tc.get("vis_corner"), _inview(tc, truth))
        if vs == "partial":
            continue
        known = pf is not None and pf.known
        if vs == "hidden":
            if known:
                wrong += 1; detail.append(f"{tc['card']}: read although covered")
            continue
        if known and pf.value != tc["card"]:
            wrong += 1; detail.append(f"{tc['card']}->{pf.value}")
        elif not known:
            unknown += 1; detail.append(f"{tc['card']}: unknown")
    if any(f.known for f in extra):
        wrong += 1; detail.append("card read where none exists")
    return ("wrong" if wrong else "unknown" if unknown else "ok"), "; ".join(detail)


def _money_cmp_vis(pred, truth_text, vis, inview=1.0):
    vs = _vis_state(vis, inview)
    if vs == "partial":
        return "na"
    if vs == "hidden":
        return "wrong" if pred.known else "ok"
    return _money_cmp(pred, truth_text)


def score_frame(st: TableState, truth: dict, slots: list[float], table_center_dy: float = -172.0, dealer_offsets: list | None = None) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    dpr = truth["dpr"]
    pairs, extra = _align_cards(st.hero_cards, st.quality.get("hero_x", []), truth["hero_cards"], dpr)
    out["hero_cards"] = _cards_outcome(pairs, extra, truth)
    bpairs, bextra = _align_cards(st.board, st.quality.get("board_x", []), truth["board"], dpr)
    o, d = _cards_outcome(bpairs, bextra, truth)
    tb = [c["card"] for c in truth["board"]]
    if st.board_count.known and st.board_count.value != len(tb):
        o, d = "wrong", f"count {st.board_count.value} vs {len(tb)}"
    elif o == "ok" and not st.board_count.known:
        o, d = "unknown", "count unknown"
    out["board"] = (o, d)
    smap = {0: "preflop", 3: "flop", 4: "turn", 5: "river"}
    ts = smap.get(len(tb))
    if st.street.known:
        out["street"] = ("ok" if st.street.value == ts else "wrong", f"{st.street.value} vs {ts}")
    else:
        out["street"] = ("unknown", "")
    for name, key, vkey, rkey in (("pot", "pot_text", "pot_vis", "pot_rect"), ("to_call", "to_call_text", "to_call_vis", "to_call_rect"), ("hero_stack", "hero_stack_text", "hero_stack_vis", "hero_stack_line_rect")):
        o = _money_cmp_vis(getattr(st, name), truth.get(key), truth.get(vkey), _inview(truth.get(rkey), truth))
        out[name] = (o, f"{getattr(st, name).value.raw if getattr(st, name).known else getattr(st, name).reason} vs {truth.get(key)} vis={truth.get(vkey)}")
    # seat stacks / bets: a prediction is wrong only if it matches NO displayed value (clipped or covered ones included);
    # "missed" counts only values that were fully visible. Reading a clipped value correctly is not penalised.
    def match_items(preds, truth_items, text_key="text"):
        all_t = [(parse_money(x[text_key]), _vis_state(x.get("vis"), _inview(x, truth)) == "visible") for x in truth_items if parse_money(x[text_key])]
        used = [False] * len(all_t)
        wrong = matched = 0
        for pm in preds:
            hit = next((i for i, (tm, _) in enumerate(all_t) if not used[i] and pm.agrees(tm)), None)
            if hit is None:
                wrong += 1
            else:
                used[hit] = True; matched += 1
        miss = sum(1 for i, (tm, vis_ok) in enumerate(all_t) if vis_ok and not used[i])
        return wrong, matched, miss
    w, m_, ms = match_items([sd.stack.value for sd in st.seats if sd.stack.known], truth["seats"])
    out["seat_stacks"] = ("wrong" if w else "ok" if ms == 0 else "unknown", f"matched={m_} wrong={w} missed={ms}")
    w, m_, ms = match_items([sd.bet.value for sd in st.seats if sd.bet.known], truth["bets"])
    out["bets"] = ("wrong" if w else "ok" if ms == 0 else "unknown", f"matched={m_} wrong={w} missed={ms}")
    # dealer: the truth seat is the visible seat label at the chip's learned offset; evaluated through the same
    # angle->slot function; "hero" when the chip is by the hero. Chips not attached to a visible seat are "na".
    if truth["dealer"] and truth["hero_cards"]:
        tcx = truth["vw"] / 2
        hero_top = truth["hero_cards"][0]["y"]
        tcy = hero_top + table_center_dy
        chip = (truth["dealer"]["cx"], truth["dealer"]["cy"])
        vis_seats = [x for x in truth["seats"] if _vis_state(x.get("vis"), _inview(x, truth)) == "visible"]
        exp = None
        for sd in vis_seats:
            if any(math.hypot(chip[0] - (sd["cx"] + ox), chip[1] - (sd["cy"] + oy)) <= 6 for ox, oy in (dealer_offsets or [])):
                ang = _angle(sd["cx"] - tcx, sd["cy"] - tcy)
                exp = min(range(len(slots)), key=lambda i: min(abs(slots[i] - ang) % 360, 360 - abs(slots[i] - ang) % 360)) if slots else None
                break
        if exp is None and math.hypot(tcx - chip[0], hero_top + 40 - chip[1]) <= 90:
            exp = "hero"
        if exp is None:
            out["dealer"] = ("na", "dealer chip not attached to a visible seat: not scored")
        elif st.dealer_slot.known:
            out["dealer"] = ("ok" if st.dealer_slot.value == exp else "wrong", f"{st.dealer_slot.value} vs {exp}")
        else:
            out["dealer"] = ("unknown", st.dealer_slot.reason)
    else:
        out["dealer"] = ("na" if not st.dealer_slot.known else "wrong", "no truth dealer")
    # actions
    vocab = [b for b in truth["buttons"] if b["label"] in ("FOLD", "CHECK", "CALL", "RAISE", "ALL IN", "CONFIRM")]
    maxy = max((b["y"] for b in vocab), default=0)
    tl = [(b["label"], b["amount"]) for b in sorted((b for b in vocab if b["y"] >= maxy - 12), key=lambda b: b["x"])]   # the bottom bar only
    t_turn = any(l in ("FOLD",) for l, _ in tl)
    if st.hero_turn.known:
        out["hero_turn"] = ("ok" if st.hero_turn.value == t_turn else "wrong", f"{st.hero_turn.value} vs {t_turn}")
    else:
        out["hero_turn"] = ("unknown", st.hero_turn.reason)
    if st.actions.known:
        pl = [(l, a) for l, a in st.actions.value]
        same = [x[0] for x in pl] == [x[0] for x in tl] and all((a is None and b is None) or (a is not None and b is not None and abs(a - b) < 0.5) for (_, a), (_, b) in zip(pl, tl))
        out["actions"] = ("ok" if same else "wrong", f"{pl} vs {tl}")
    else:
        out["actions"] = ("unknown", st.actions.reason)
    return out


def exact_and_safe(res: dict) -> tuple[bool, bool]:
    core = [res[k][0] for k in CORE]
    return all(o == "ok" for o in core), all(o != "wrong" for o in core)


def aggregate(results: list[dict], fields=FIELDS) -> dict:
    agg = {}
    for f in fields:
        c = Counter(r[f][0] for r in results if f in r)
        scored = c["ok"] + c["wrong"] + c["unknown"]
        agg[f] = {"ok": c["ok"], "wrong": c["wrong"], "unknown": c["unknown"], "na": c["na"],
                  "error_rate": c["wrong"] / scored if scored else None,
                  "unknown_rate": c["unknown"] / scored if scored else None,
                  "acc_answered": c["ok"] / (c["ok"] + c["wrong"]) if (c["ok"] + c["wrong"]) else None}
    ex = [exact_and_safe(r) for r in results]
    agg["_state"] = {"n": len(results), "exact_match": sum(e for e, _ in ex) / max(len(ex), 1), "safe_no_wrong_core": sum(s for _, s in ex) / max(len(ex), 1)}
    return agg


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / den, (c + m) / den)
