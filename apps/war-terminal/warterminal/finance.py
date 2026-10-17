"""Landed cost, margin vs markup, cashflow, delay stress, capital limit. Decimal everywhere; no implicit FX or margin.

The podcast guest's numbers (5-20% margin, net 30) are NOT rules here: every figure is an explicit input.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

D = Decimal
Q = D("0.01")


def _d(x) -> Decimal:
    return x if isinstance(x, Decimal) else D(str(x))


def to_base(amount, currency: str, base: str, fx: dict | None) -> Decimal:
    """fx = {"EURUSD": 1.08, ...} meaning 1 EUR = 1.08 USD. Missing rate -> error, never a silent 1:1."""
    amount = _d(amount)
    if currency == base:
        return amount
    fx = fx or {}
    direct, inverse = f"{currency}{base}", f"{base}{currency}"
    if direct in fx:
        return amount * _d(fx[direct])
    if inverse in fx:
        return amount / _d(fx[inverse])
    for pair, rate in fx.items():                  # cross rate: EURCZK and USDCZK -> EUR->USD
        if len(pair) == 6 and pair.startswith(currency) and f"{base}{pair[3:]}" in fx:
            return amount * _d(rate) / _d(fx[f"{base}{pair[3:]}"])
    raise ValueError(f"нет курса {currency}->{base}: передайте fx явно (источник и дату курса вносит человек)")


def landed_cost(*, purchase, freight=0, insurance=0, duties_pct=0, certification=0, financing=0, risk_reserve_pct=0, overhead=0) -> dict:
    purchase, freight, insurance = _d(purchase), _d(freight), _d(insurance)
    duties = (purchase + freight + insurance) * _d(duties_pct) / 100          # customs value = CIF
    reserve = purchase * _d(risk_reserve_pct) / 100
    total = purchase + freight + insurance + duties + _d(certification) + _d(financing) + reserve + _d(overhead)
    return {"purchase": purchase, "freight": freight, "insurance": insurance, "duties": duties.quantize(Q), "certification": _d(certification),
            "financing": _d(financing), "risk_reserve": reserve.quantize(Q), "overhead": _d(overhead), "landed_cost": total.quantize(Q)}


def price_metrics(bid, landed) -> dict:
    """margin = profit/bid, markup = profit/landed cost: they differ and the report shows both."""
    bid, landed = _d(bid), _d(landed)
    profit = bid - landed
    return {"bid": bid, "landed_cost": landed, "profit": profit.quantize(Q),
            "margin_pct": (profit / bid * 100).quantize(Q) if bid else None,
            "markup_pct": (profit / landed * 100).quantize(Q) if landed else None}


@dataclass
class Event:
    day: int
    amount: Decimal              # + inflow, - outflow
    label: str


def cashflow(events: list[Event]) -> dict:
    cum, low, peak_day, rows = D(0), D(0), 0, []
    for e in sorted(events, key=lambda e: e.day):
        cum += e.amount
        rows.append({"day": e.day, "label": e.label, "amount": e.amount.quantize(Q), "cumulative": cum.quantize(Q)})
        if cum < low:
            low, peak_day = cum, e.day
    return {"timeline": rows, "peak_funding_need": (-low).quantize(Q), "peak_day": peak_day, "final_balance": cum.quantize(Q)}


def build_events(*, supplier_prepay_pct, supplier_total, delivery_day: int, acceptance_day: int, bid_price, customer_pay_delay_days: int,
                 vat_import=0, vat_recovery_days=60, logistics_cost=0, other_costs=0, buyer_prepay_pct=0) -> list[Event]:
    supplier_total, bid_price = _d(supplier_total), _d(bid_price)
    ev = [Event(0, -(supplier_total * _d(supplier_prepay_pct) / 100), "аванс поставщику")]
    rest = supplier_total * (100 - _d(supplier_prepay_pct)) / 100
    if rest:
        ev.append(Event(delivery_day, -rest, "остаток поставщику"))
    if _d(logistics_cost):
        ev.append(Event(delivery_day, -_d(logistics_cost), "логистика"))
    if _d(other_costs):
        ev.append(Event(delivery_day, -_d(other_costs), "прочие расходы"))
    if _d(vat_import):
        ev.append(Event(delivery_day, -_d(vat_import), "ввозной НДС"))
        ev.append(Event(delivery_day + vat_recovery_days, _d(vat_import), "возврат НДС"))
    pre = bid_price * _d(buyer_prepay_pct) / 100
    if pre:
        ev.append(Event(0, pre, "аванс заказчика"))
    ev.append(Event(acceptance_day + customer_pay_delay_days, bid_price - pre, "оплата заказчиком"))
    return ev


def stress(events_fn, delays=(30, 60, 90), annual_rate=D("0.12"), bid_price=D(0), landed=D(0)) -> list[dict]:
    """events_fn(extra_delay_days) -> events. Extra financing = peak need * annual rate * extra days / 365 is charged to profit."""
    out = []
    for extra in (0, *delays):
        cf = cashflow(events_fn(extra))
        carry = (cf["peak_funding_need"] * _d(annual_rate) * D(extra) / 365).quantize(Q)
        m = price_metrics(bid_price, _d(landed) + carry)
        out.append({"extra_delay_days": extra, "peak_funding_need": cf["peak_funding_need"], "extra_carry_cost": carry,
                    "profit": m["profit"], "margin_pct": m["margin_pct"], "final_balance": cf["final_balance"]})
    return out


def capital_check(peak_need_usd, *, limit_usd=D(50000), order_confirmed=False, supplier_contract_signed=False) -> dict:
    """Owner rule: 50k USD cap, unless the deal is matched (confirmed buyer order + signed supplier contract) - then it
    still needs explicit owner approval and legal review."""
    peak = _d(peak_need_usd)
    if peak <= _d(limit_usd):
        return {"status": "WITHIN_LIMIT", "allowed": True, "needs_owner_approval": False, "reason": f"пик потребности {peak} <= лимит {limit_usd}"}
    if order_confirmed and supplier_contract_signed:
        return {"status": "MATCHED_DEAL", "allowed": True, "needs_owner_approval": True,
                "reason": f"пик {peak} выше лимита {limit_usd}, но заказ и поставщик состыкованы: нужны одобрение владельца и юридическая проверка"}
    return {"status": "OVER_LIMIT", "allowed": False, "needs_owner_approval": True,
            "reason": f"пик {peak} выше лимита {limit_usd}; без подтверждённого заказа и подписанного контракта с поставщиком недопустимо"}
