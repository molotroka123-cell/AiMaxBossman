"""COACH: explains the visible situation and recommends an action. It is advice under uncertainty, never a guarantee:
opponents' cards and ranges are unknown, so equity is computed against RANDOM hands and the pot-odds rule is a simplification."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .control.intent import PolicyDecision, hand_key
from .strategy import VisibleInfo, decide

DISCLAIMER = ("Рекомендация — оценка по видимой информации: карты соперников и их диапазоны неизвестны, эквити считается против случайных рук, "
              "не против реального диапазона. Это не оптимальная стратегия и не гарантия результата.")


@dataclass
class Option:
    action: str
    amount: float | None
    note: str
    chosen: bool = False


@dataclass
class Recommendation:
    ok: bool
    why_not: str = ""
    decision: PolicyDecision | None = None
    options: list = field(default_factory=list)
    equity: float | None = None
    pot_odds: float | None = None
    explanation: str = ""
    uncertainty: list = field(default_factory=list)
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        d = self.decision
        return {"ok": self.ok, "why_not": self.why_not, "action": d.kind if d else None, "raise_to": d.raise_to if d else None,
                "size_range": [d.size_min, d.size_max] if d and d.raise_to is not None else None, "equity": self.equity, "pot_odds": self.pot_odds,
                "options": [o.__dict__ for o in self.options], "explanation": self.explanation, "uncertainty": self.uncertainty, "disclaimer": self.disclaimer,
                "t_ms": d.t_ms if d else None}


def _size(info: VisibleInfo, strong: bool) -> tuple[float, float, float]:
    """Policy-owned bet size in chips: ideal target and acceptable range (the trainer offers presets only)."""
    pot = max(info.pot, 1.0)
    pre = len(info.board) == 0
    ideal = (2.5 if pre else (0.75 if strong else 0.5)) * pot
    lo, hi = (1.2 * pot, 4.0 * pot) if pre else (0.3 * pot, 1.01 * pot)
    cap = info.stack
    return min(ideal, cap), min(lo, cap), min(hi, cap)


def recommend(cm, rng: random.Random, sims: int = 300) -> Recommendation:
    """cm: reconcile.Committed (validated state). Returns a recommendation or the reason there is none."""
    unc = [f"поле {p}: есть конкурирующее чтение, ещё не подтверждено" for p in (cm.pending or [])]
    if cm.blocked:
        return Recommendation(False, "состояние заблокировано: " + "; ".join(cm.blocked), uncertainty=unc)
    if not all(cm.hero_cards):
        return Recommendation(False, "карты героя не подтверждены")
    if cm.hero_turn is not True or not cm.actions or not cm.pot or not cm.hero_stack:
        return Recommendation(False, "сейчас не ход героя или кнопки/банк/стек не подтверждены")
    labels = tuple(a[0] for a in cm.actions)
    amounts = {a[0]: a[1] for a in cm.actions}
    if "CHECK" in amounts:
        to_call = 0.0
    elif amounts.get("CALL") is not None:
        to_call = float(amounts["CALL"])
    elif amounts.get("ALL IN") is not None:
        to_call = min(float(amounts["ALL IN"]), cm.hero_stack.amount)
    else:
        return Recommendation(False, "сумма колла не прочитана")
    n_opp = max(1, len([s for s in cm.seats.values() if s.get("stack")]))
    board = tuple(c for c in cm.board if c)
    info = VisibleInfo(tuple(cm.hero_cards), board, cm.pot.amount, to_call, cm.hero_stack.amount, min(n_opp, 5), labels)
    ch = decide(info, rng, sims)
    street = "preflop" if not board else {3: "flop", 4: "turn", 5: "river"}.get(len(board), "?")
    kind = ch.action
    if kind not in labels:
        kind = "CHECK" if "CHECK" in labels else "FOLD" if "FOLD" in labels else None
    if kind is None:
        return Recommendation(False, "нет допустимого действия среди прочитанных кнопок")
    raise_to = lo = hi = None
    if kind == "RAISE":
        if "RAISE" not in labels:
            return Recommendation(False, "RAISE недоступен")
        raise_to, lo, hi = _size(info, ch.equity > 0.78)
        if raise_to <= to_call:
            kind, raise_to, lo, hi = ("CALL" if "CALL" in labels else "CHECK"), None, None, None
    d = PolicyDecision(kind, raise_to, hand_key(list(cm.hero_cards), list(cm.board)), street, cm.t_ms, to_call if kind == "CALL" else (to_call or None),
                       reason=ch.reason, size_min=lo, size_max=hi)
    opts = []
    for lab in labels:
        if lab == "FOLD":
            opts.append(Option("FOLD", None, "сдаться: 0 фишек в игре, теряем уже вложенное"))
        elif lab == "CHECK":
            opts.append(Option("CHECK", None, "бесплатно увидеть следующую карту"))
        elif lab == "CALL":
            need = ch.pot_odds
            opts.append(Option("CALL", to_call, f"нужно эквити ≥ {need:.0%} для безубыточного колла; оценка эквити {ch.equity:.0%}"))
        elif lab in ("RAISE", "ALL IN"):
            opts.append(Option(lab, raise_to if lab == "RAISE" else cm.hero_stack.amount, "ставка/рейз: выигрыш зависит от реакции соперников — не оценивается без модели диапазонов"))
    for o in opts:
        o.chosen = o.action == kind
    expl = (f"Улица: {street}. Банк {cm.pot.amount:g}, к доплате {to_call:g}, стек {cm.hero_stack.amount:g}, соперников ≈{info.n_opponents}. "
            f"Эквити против случайных рук ≈{ch.equity:.0%}, шансы банка требуют {ch.pot_odds:.0%}. Рекомендация: {kind}"
            + (f" до {raise_to:g}" if raise_to else "") + f" — {ch.reason}.")
    if info.n_opponents > 1:
        unc.append("число соперников оценено по видимым стекам")
    return Recommendation(True, "", d, opts, round(ch.equity, 3), round(ch.pot_odds, 3), expl, unc)
