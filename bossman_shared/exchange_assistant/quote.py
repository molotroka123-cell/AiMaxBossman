"""Deterministic Decimal quote: mid-market from sourced rates, desk margin, expiry, a readable formula.

Client rate = mid x (1 - margin), whichever way the client swaps (both directions carry the margin).
Rounding always in the desk's favour and stated: what the client receives is rounded down,
what the client must give is rounded up, to 0.01.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from .rates import Rate, RateBook, RateError

CURRENCIES = ("USDT", "USD", "EUR", "CZK")
DEFAULT_MARGIN = Decimal("0.03")
DEFAULT_TTL = timedelta(minutes=15)
CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("10000000")


class QuoteError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Quote:
    id: str
    give_ccy: str
    give_amount: Decimal
    get_ccy: str
    get_amount: Decimal
    mid: Decimal                 # get_ccy per 1 give_ccy, mid-market
    client_rate: Decimal         # mid x (1 - margin)
    margin: Decimal
    created_at: datetime
    valid_until: datetime
    rates: tuple[str, ...] = field(default=())
    formula: str = ""
    fixed_side: str = "give"     # which amount the client named

    def text(self) -> str:
        pct = (self.margin * 100).normalize()
        return "\n".join([
            f"Котировка {self.id}",
            f"Клиент отдаёт: {self.give_amount} {self.give_ccy}",
            f"Клиент получает: {self.get_amount} {self.get_ccy}",
            f"Курс клиента: 1 {self.give_ccy} = {self.client_rate.quantize(Decimal('0.000001'))} {self.get_ccy} "
            f"(середина {self.mid.quantize(Decimal('0.000001'))}, наценка {pct}%)",
            f"Формула: {self.formula}",
            "Курсы: " + "; ".join(self.rates),
            f"Действует до {self.valid_until.strftime('%d.%m.%Y %H:%M UTC')}",
            "Это расчёт, а не сделка. Сделка записывается только после «обмен выполнен».",
        ])


def _usd_value(ccy: str, book: RateBook, now: datetime, used: list[Rate]) -> tuple[Decimal, str]:
    """USD value of 1 unit of ccy and its formula fragment."""
    if ccy == "USD":
        return Decimal(1), "1"
    if ccy == "USDT":
        r = book.get("USDTUSD", now); used.append(r)
        return r.value, f"USDT/USD {r.value}"
    if ccy == "EUR":
        r = book.get("EURUSD", now); used.append(r)
        return r.value, f"EUR/USD {r.value}"
    if ccy == "CZK":
        r = book.get("USDCZK", now); used.append(r)
        return Decimal(1) / r.value, f"1/(USD/CZK {r.value})"
    raise QuoteError(f"валюта {ccy} не поддерживается (есть: {', '.join(CURRENCIES)})")


def mid_rate(give: str, get: str, book: RateBook, now: datetime) -> tuple[Decimal, list[Rate], str]:
    used: list[Rate] = []
    if {give, get} == {"EUR", "CZK"}:                    # direct CNB cross, no USD detour
        r = book.get("EURCZK", now); used.append(r)
        if give == "EUR":
            return r.value, used, f"EUR/CZK {r.value}"
        return Decimal(1) / r.value, used, f"1/(EUR/CZK {r.value})"
    a, fa = _usd_value(give, book, now, used)
    b, fb = _usd_value(get, book, now, used)
    return a / b, used, f"({fa}) / ({fb})"


def _quote_id(*parts) -> str:
    return "Q-" + hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:8].upper()


def build_quote(book: RateBook, *, give_ccy: str, get_ccy: str, now: datetime, give_amount: Decimal | None = None,
                get_amount: Decimal | None = None, margin: Decimal = DEFAULT_MARGIN, ttl: timedelta = DEFAULT_TTL) -> Quote:
    give_ccy, get_ccy = give_ccy.upper(), get_ccy.upper()
    for c in (give_ccy, get_ccy):
        if c not in CURRENCIES:
            raise QuoteError(f"валюта {c} не поддерживается (есть: {', '.join(CURRENCIES)})")
    if give_ccy == get_ccy:
        raise QuoteError("валюты совпадают")
    if (give_amount is None) == (get_amount is None):
        raise QuoteError("укажите ровно одну сумму: сколько клиент отдаёт ИЛИ сколько хочет получить")
    if not (Decimal(0) <= margin < Decimal("0.5")):
        raise QuoteError("наценка вне 0–50%")
    amount = give_amount if give_amount is not None else get_amount
    if not amount.is_finite() or amount <= 0 or amount > MAX_AMOUNT:
        raise QuoteError(f"сумма должна быть больше 0 и не больше {MAX_AMOUNT}")
    try:
        mid, used, fmid = mid_rate(give_ccy, get_ccy, book, now)
    except RateError as e:
        raise QuoteError(str(e)) from None
    client_rate = mid * (Decimal(1) - margin)
    if give_amount is not None:
        give = give_amount.quantize(CENT)
        get = (give * client_rate).quantize(CENT, rounding=ROUND_DOWN)
        formula = f"{give} × [{fmid}] × (1 − {margin}) = {get} {get_ccy} (вниз до 0.01)"
        side = "give"
    else:
        get = get_amount.quantize(CENT)
        give = (get / client_rate).quantize(CENT, rounding=ROUND_UP)
        formula = f"{get} ÷ ([{fmid}] × (1 − {margin})) = {give} {give_ccy} (вверх до 0.01)"
        side = "get"
    if get <= 0 or give <= 0:
        raise QuoteError("после округления сумма равна нулю — слишком маленькая сделка")
    qid = _quote_id(give_ccy, give, get_ccy, get, margin, now.isoformat(), *(r.label() for r in used))
    return Quote(qid, give_ccy, give, get_ccy, get, mid, client_rate, margin, now, now + ttl,
                 tuple(r.label() for r in used), formula, side)
