"""Deterministic GTO-level debrief of one Poker Train session (every hand) for the Jeff reply. Pure functions, no I/O.

Input = the trimmed session the game sends (handSummaries + records). Output = Russian text. The optional LLM
comment is added by the caller; these numbers never depend on a model.

Includes exact GTO mathematics:
- Pot odds: bet / (pot + 2 * bet) (or toCall / (pot_including_bet + toCall))
- Required equity & Minimum Defense Frequency: MDF = pot / (pot + bet)
- Optimal bluff-to-value ratio: alpha = bet / (pot + bet)
- SPR = effective_stack / pot & geometric bet sizing: ((1 + 2 * SPR) ** (1 / N) - 1) / 2
- Board texture classification (dry / wet / paired / monotone / connected)
- Street-by-street EV (EV_fold, EV_call, EV_raise) and delta_EV_bb detection.
"""
from __future__ import annotations

from collections import Counter, defaultdict

RANKS = "23456789TJQKA"
MISTAKE_RU = {
    "bad_fold": "лишний пас",
    "overfold_vs_mdf": "оверфолд против MDF",
    "bad_call": "плохой колл",
    "bad_call_pot_odds": "колл не по шансам банка",
    "too_passive": "слишком пассивно",
    "too_passive_missed_value": "недобор вэлью",
    "open_limp": "опен-лимп вместо рейза",
    "push_fold_error": "ошибка пуш/фолд",
    "icm_error": "ошибка ICM",
    "draw_fold_error": "пас с дро",
    "overplay": "переигрыш",
    "bluff_error": "неудачный блеф",
    "suboptimal_cbet_sizing": "неоптимальный сайзинг контбета",
}
STAGE_RU = {"preflop": "префлоп", "flop": "флоп", "turn": "тёрн", "river": "ривер"}


def _num(v, default=0.0) -> float:
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _cards(c) -> str:
    return " ".join(str(x) for x in c[:7]) if isinstance(c, list) else "—"


def pot_odds(pot: float, bet: float, *, pot_includes_bet: bool = False) -> float:
    """Exact pot odds: ``bet / (pot + 2 * bet)`` when ``pot`` is pot before the bet."""
    pot, bet = max(0.0, _num(pot)), max(0.0, _num(bet))
    if bet <= 0:
        return 0.0
    denom = (pot + bet) if pot_includes_bet else (pot + 2.0 * bet)
    return bet / denom if denom > 0 else 0.0


def mdf(pot: float, bet: float, *, pot_includes_bet: bool = False) -> float:
    """Minimum Defense Frequency: ``MDF = pot / (pot + bet)``."""
    pot, bet = max(0.0, _num(pot)), max(0.0, _num(bet))
    if bet <= 0:
        return 1.0
    pot_before = max(0.0, pot - bet) if pot_includes_bet else pot
    denom = pot_before + bet
    return pot_before / denom if denom > 0 else 0.0


def optimal_bluff_alpha(pot: float, bet: float, *, pot_includes_bet: bool = False) -> float:
    """Optimal bluff-to-value ratio: ``alpha = bet / (pot + bet) = 1 - MDF``."""
    if _num(bet) <= 0:
        return 0.0
    return 1.0 - mdf(pot, bet, pot_includes_bet=pot_includes_bet)


def spr(effective_stack: float, pot: float) -> float:
    """Stack-to-Pot Ratio: ``effective_stack / pot``."""
    st, p = max(0.0, _num(effective_stack)), _num(pot)
    return st / p if p > 0 else 0.0


def geometric_bet_fraction(effective_stack: float, pot: float, streets_remaining: int = 3) -> float:
    """Geometric bet fraction across N streets: ``((1 + 2 * SPR) ** (1 / N) - 1) / 2``."""
    s = spr(effective_stack, pot)
    if s <= 0:
        return 0.0
    n = max(1, int(streets_remaining))
    return max(0.0, ((1.0 + 2.0 * s) ** (1.0 / n) - 1.0) / 2.0)


def classify_board(board: list | tuple) -> str:
    """Classify board texture into preflop | monotone | paired | wet | connected | dry."""
    if not isinstance(board, (list, tuple)) or not board:
        return "preflop"
    ranks, suits = [], []
    for c in board[:5]:
        s = str(c).strip()
        if len(s) >= 2 and s[0] in RANKS:
            ranks.append(RANKS.index(s[0]))
            suits.append(s[1])
    if not ranks:
        return "preflop"
    suit_max = max(Counter(suits).values())
    rank_max = max(Counter(ranks).values())
    uniq = sorted(set(ranks))
    with_wheel = ([-1] + uniq) if 12 in uniq else uniq
    max_span = max((sum(1 for r in with_wheel if 0 <= r - base <= 4) for base in with_wheel), default=1)
    if suit_max >= 3 and len(ranks) == 3 or suit_max >= 4:
        return "monotone"
    if suit_max >= 2 and max_span >= 3:
        return "wet"
    if rank_max >= 2:
        return "paired"
    if max_span >= 3:
        return "connected"
    return "dry"


def evaluate_record_gto(r: dict, default_bb: float = 10.0) -> dict:
    """Compute street-by-street GTO metrics (EV_fold, EV_call, EV_raise, delta_EV_bb) for a single decision record."""
    stage = str(r.get("stage") or "preflop")
    blinds = r.get("blinds") if isinstance(r.get("blinds"), dict) else {}
    bb = max(1.0, _num(blinds.get("bb"), default_bb))
    pot_val = max(0.0, _num(r.get("pot"), 0.0))
    to_call = max(0.0, _num(r.get("toCall", r.get("to_call")), 0.0))
    stack_val = max(0.0, _num(r.get("heroStack", r.get("stack")), 100.0 * bb))
    eq = _num(r.get("equity"), -1.0)
    board = r.get("board") if isinstance(r.get("board"), list) else []
    tex = classify_board(board)

    pot_before = max(0.0, pot_val - to_call) if pot_val >= to_call else pot_val
    po = pot_odds(pot_before, to_call, pot_includes_bet=False)
    mdf_v = mdf(pot_before, to_call, pot_includes_bet=False)
    alpha_v = optimal_bluff_alpha(pot_before, to_call, pot_includes_bet=False)
    spr_v = spr(stack_val, max(bb, pot_val))
    streets_rem = {"preflop": 3, "flop": 3, "turn": 2, "river": 1}.get(stage, 1)
    geo_f = geometric_bet_fraction(stack_val, max(bb, pot_val), streets_rem)

    ev_fold = 0.0
    ev_call = (eq * (pot_before + 2.0 * to_call) - to_call) if (to_call > 0 and 0.0 <= eq <= 1.0) else None
    bet_frac = 0.33 if tex in ("dry", "paired", "monotone") else 0.67
    bet_size = max(bb, (pot_val or bb) * bet_frac)
    fe = optimal_bluff_alpha(max(bb, pot_val), bet_size, pot_includes_bet=False)
    ev_raise = (fe * max(bb, pot_val) + (1.0 - fe) * ((eq * (max(bb, pot_val) + 2.0 * bet_size)) - bet_size)) if 0.0 <= eq <= 1.0 else None

    rec_ev_lost = max(0.0, _num(r.get("evLost"), 0.0))
    if rec_ev_lost == 0.0 and ev_call is not None:
        act = str(r.get("action") or "").lower()
        if act == "fold" and ev_call > 0:
            rec_ev_lost = ev_call
        elif act == "call" and ev_call < 0:
            rec_ev_lost = -ev_call

    return {
        "stage": stage,
        "board_texture": tex,
        "pot_odds": round(po, 4),
        "required_equity": round(po, 4),
        "mdf": round(mdf_v, 4),
        "alpha": round(alpha_v, 4),
        "spr": round(spr_v, 2),
        "geometric_bet_frac": round(geo_f, 4),
        "ev_fold": ev_fold,
        "ev_call": round(ev_call, 2) if ev_call is not None else None,
        "ev_raise": round(ev_raise, 2) if ev_raise is not None else None,
        "delta_ev_bb": round(rec_ev_lost / bb, 3),
    }


def summarize(session: dict) -> dict:
    hands = [h for h in (session.get("handSummaries") or []) if isinstance(h, dict)][:5000]
    records = [r for r in (session.get("records") or []) if isinstance(r, dict)][:50000]
    pf = {}
    for r in records:
        if r.get("stage") == "preflop" and r.get("handNumber") not in pf:
            pf[r.get("handNumber")] = r
    vpip = sum(1 for r in pf.values() if r.get("action") in {"call", "raise", "allin", "all-in", "bet"})
    pfr = sum(1 for r in pf.values() if r.get("action") in {"raise", "allin", "all-in"})
    mistakes = [r for r in records if r.get("mistakeType")]
    by_type = Counter(str(r["mistakeType"]) for r in mistakes)
    by_stage = Counter(str(r.get("stage") or "?") for r in mistakes)
    by_pos: dict[str, float] = defaultdict(float)
    for h in hands:
        by_pos[str(h.get("position") or "?")] += _num(h.get("netProfit"))
    worst = sorted((h for h in hands if _num(h.get("evLost")) > 0), key=lambda h: -_num(h.get("evLost")))[:5]

    # GTO mathematical telemetry across all decision records
    gto_evals = [evaluate_record_gto(r) for r in records]
    facing_recs = [r for r in records if _num(r.get("toCall", r.get("to_call"))) > 0]
    defended = sum(1 for r in facing_recs if str(r.get("action") or "").lower() in {"call", "raise", "allin", "all-in", "bet"})
    post_aggr = sum(1 for r in records if r.get("stage") != "preflop" and str(r.get("action") or "").lower() in {"raise", "bet", "allin", "all-in"})
    post_calls = sum(1 for r in records if r.get("stage") != "preflop" and str(r.get("action") or "").lower() == "call")
    tex_counts = Counter(g["board_texture"] for g in gto_evals if g["board_texture"] != "preflop")
    n_hands = len(hands) or len(pf)
    total_delta_bb = sum(g["delta_ev_bb"] for g in gto_evals)

    return {
        "hands": n_hands,
        "decisions": len(records),
        "vpip": round(100 * vpip / len(pf), 1) if pf else None,
        "pfr": round(100 * pfr / len(pf), 1) if pf else None,
        "net": round(sum(_num(h.get("netProfit")) for h in hands)),
        "mistakes": len(mistakes),
        "ev_lost": round(sum(_num(r.get("evLost")) for r in mistakes)),
        "by_type": dict(by_type.most_common(6)),
        "by_stage": dict(by_stage),
        "by_position": {k: round(v) for k, v in sorted(by_pos.items(), key=lambda kv: kv[1])},
        "worst": [
            {
                "hand": h.get("hand"),
                "position": h.get("position"),
                "cards": _cards(h.get("holeCards") or []),
                "mistake": h.get("mistake"),
                "ev_lost": round(_num(h.get("evLost"))),
                "line": " → ".join(
                    f"{STAGE_RU.get(a.get('stage'), a.get('stage'))}: {a.get('action')}"
                    for a in (h.get("actions") or [])[:8]
                    if isinstance(a, dict)
                ),
            }
            for h in worst
        ],
        "gto": {
            "af": round(post_aggr / post_calls, 2) if post_calls else None,
            "mdf_defense_pct": round(100.0 * defended / len(facing_recs), 1) if facing_recs else None,
            "avg_ev_loss_bb_per_hand": round(total_delta_bb / max(1, n_hands), 3) if n_hands else 0.0,
            "board_textures": dict(tex_counts),
        },
    }


def render(s: dict, player: str) -> str:
    if not s["hands"]:
        return f"🃏 {player}, в сессии нет сыгранных рук — разбирать пока нечего."
    lines = [
        f"🃏 Разбор сессии для {player}",
        "",
        f"Рук: {s['hands']} · решений: {s['decisions']} · итог: {s['net']:+,} фишек".replace(",", " "),
        f"VPIP {s['vpip']}% · PFR {s['pfr']}%" if s["vpip"] is not None else "VPIP/PFR: нет префлоп-решений",
        f"Ошибок: {s['mistakes']} · потеря EV ≈ {s['ev_lost']:,} фишек".replace(",", " "),
    ]
    gto = s.get("gto") or {}
    gto_parts = []
    if gto.get("af") is not None:
        gto_parts.append(f"AF {gto['af']}")
    if gto.get("mdf_defense_pct") is not None:
        gto_parts.append(f"защита vs ставок (MDF) {gto['mdf_defense_pct']}%")
    if gto.get("avg_ev_loss_bb_per_hand"):
        gto_parts.append(f"ΔEV {gto['avg_ev_loss_bb_per_hand']:.2f} BB/руку")
    if gto_parts:
        lines.append("GTO-метрики: " + " · ".join(gto_parts))
    if s["by_type"]:
        lines += ["", "Ошибки по типам: " + ", ".join(f"{MISTAKE_RU.get(k, k)} ×{v}" for k, v in s["by_type"].items())]
    if s["by_stage"]:
        lines.append("По улицам: " + ", ".join(f"{STAGE_RU.get(k, k)} ×{v}" for k, v in s["by_stage"].items()))
    if s["by_position"]:
        lines.append("Итог по позициям: " + ", ".join(f"{k} {v:+}" for k, v in s["by_position"].items()))
    if s["worst"]:
        lines += ["", "Самые дорогие руки:"]
        for w in s["worst"]:
            lines.append(
                f"• Рука #{w['hand']} ({w['position']}, {w['cards']}): {MISTAKE_RU.get(w['mistake'], w['mistake'])},"
                f" −{w['ev_lost']} EV. {w['line']}"
            )
    vpip = s["vpip"] or 0
    tip = (
        "Играете слишком много рук — сузьте диапазон открытия с ранних позиций."
        if vpip > 32
        else "Играете слишком мало рук — чаще открывайтесь с поздних позиций (CO/BTN)."
        if 0 < vpip < 15
        else "Диапазон по рукам в норме — работайте над ошибками из списка выше."
    )
    lines += ["", "Совет: " + tip, "", "Разбор посчитан по вашим рукам автоматически (детерминированно)."]
    return "\n".join(lines)
