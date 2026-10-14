"""Deterministic debrief of one Poker Train session (every hand) for the Jeff reply. Pure functions, no I/O.

Input = the trimmed session the game sends (handSummaries + records). Output = Russian text. The optional LLM
comment is added by the caller; these numbers never depend on a model.
"""
from __future__ import annotations

from collections import Counter, defaultdict

MISTAKE_RU = {"bad_fold": "лишний пас", "bad_call": "плохой колл", "too_passive": "слишком пассивно",
              "push_fold_error": "ошибка пуш/фолд", "icm_error": "ошибка ICM", "draw_fold_error": "пас с дро",
              "overplay": "переигрыш", "bluff_error": "неудачный блеф"}
STAGE_RU = {"preflop": "префлоп", "flop": "флоп", "turn": "тёрн", "river": "ривер"}


def _num(v, default=0.0) -> float:
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _cards(c) -> str:
    return " ".join(str(x) for x in c[:7]) if isinstance(c, list) else "—"


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
    return {"hands": len(hands) or len(pf), "decisions": len(records),
            "vpip": round(100 * vpip / len(pf), 1) if pf else None, "pfr": round(100 * pfr / len(pf), 1) if pf else None,
            "net": round(sum(_num(h.get("netProfit")) for h in hands)), "mistakes": len(mistakes),
            "ev_lost": round(sum(_num(r.get("evLost")) for r in mistakes)), "by_type": dict(by_type.most_common(6)),
            "by_stage": dict(by_stage), "by_position": {k: round(v) for k, v in sorted(by_pos.items(), key=lambda kv: kv[1])},
            "worst": [{"hand": h.get("hand"), "position": h.get("position"), "cards": _cards(h.get("holeCards") or []),
                       "mistake": h.get("mistake"), "ev_lost": round(_num(h.get("evLost"))),
                       "line": " → ".join(f"{STAGE_RU.get(a.get('stage'), a.get('stage'))}: {a.get('action')}"
                                          for a in (h.get("actions") or [])[:8] if isinstance(a, dict))}
                      for h in worst]}


def render(s: dict, player: str) -> str:
    if not s["hands"]:
        return f"🃏 {player}, в сессии нет сыгранных рук — разбирать пока нечего."
    lines = [f"🃏 Разбор сессии для {player}", "",
             f"Рук: {s['hands']} · решений: {s['decisions']} · итог: {s['net']:+,} фишек".replace(",", " "),
             f"VPIP {s['vpip']}% · PFR {s['pfr']}%" if s["vpip"] is not None else "VPIP/PFR: нет префлоп-решений",
             f"Ошибок: {s['mistakes']} · потеря EV ≈ {s['ev_lost']:,} фишек".replace(",", " ")]
    if s["by_type"]:
        lines += ["", "Ошибки по типам: " + ", ".join(f"{MISTAKE_RU.get(k, k)} ×{v}" for k, v in s["by_type"].items())]
    if s["by_stage"]:
        lines.append("По улицам: " + ", ".join(f"{STAGE_RU.get(k, k)} ×{v}" for k, v in s["by_stage"].items()))
    if s["by_position"]:
        lines.append("Итог по позициям: " + ", ".join(f"{k} {v:+}" for k, v in s["by_position"].items()))
    if s["worst"]:
        lines += ["", "Самые дорогие руки:"]
        for w in s["worst"]:
            lines.append(f"• Рука #{w['hand']} ({w['position']}, {w['cards']}): {MISTAKE_RU.get(w['mistake'], w['mistake'])},"
                         f" −{w['ev_lost']} EV. {w['line']}")
    vpip = s["vpip"] or 0
    tip = ("Играете слишком много рук — сузьте диапазон открытия с ранних позиций." if vpip > 32 else
           "Играете слишком мало рук — чаще открывайтесь с поздних позиций (CO/BTN)." if 0 < vpip < 15 else
           "Диапазон по рукам в норме — работайте над ошибками из списка выше.")
    lines += ["", "Совет: " + tip, "", "Разбор посчитан по вашим рукам автоматически (детерминированно)."]
    return "\n".join(lines)
