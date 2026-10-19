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
                get_amount: Decimal | None = None, margin: Decimal | None = DEFAULT_MARGIN,
                require_explicit_margin: bool = False, client_buys_get: bool = False,
                ttl: timedelta = DEFAULT_TTL) -> Quote:
    """Build a deterministic Decimal quote.
    - When `require_explicit_margin=True`, `margin=None` raises `QuoteError` (no silent 3% assumption).
    - When `client_buys_get=True` and `get_amount` is specified (e.g. client buys 1 000 USDT for CZK
      at agreed fee `margin`), the quote uses the direct markup formula:
      `give = get_amount * mid_get_to_give * (1 + margin)` (so 1 000 USDT at 25 CZK/USDT and 3% = 25 750.00 CZK).
    """
    give_ccy, get_ccy = give_ccy.upper(), get_ccy.upper()
    for c in (give_ccy, get_ccy):
        if c not in CURRENCIES:
            raise QuoteError(f"валюта {c} не поддерживается (есть: {', '.join(CURRENCIES)})")
    if give_ccy == get_ccy:
        raise QuoteError("валюты совпадают")
    if (give_amount is None) == (get_amount is None):
        raise QuoteError("укажите ровно одну сумму: сколько клиент отдаёт ИЛИ сколько хочет получить")
    if margin is None:
        if require_explicit_margin:
            raise QuoteError("процент комиссии (margin) не указан менеджером: укажите явную ставку по сделке")
        margin = DEFAULT_MARGIN
    if not (Decimal(0) <= margin < Decimal("0.5")):
        raise QuoteError("наценка вне 0–50%")
    amount = give_amount if give_amount is not None else get_amount
    if not amount.is_finite() or amount <= 0 or amount > MAX_AMOUNT:
        raise QuoteError(f"сумма должна быть больше 0 и не больше {MAX_AMOUNT}")
    try:
        mid, used, fmid = mid_rate(give_ccy, get_ccy, book, now)
    except RateError as e:
        raise QuoteError(str(e)) from None
    if give_amount is not None:
        client_rate = mid * (Decimal(1) - margin)
        give = give_amount.quantize(CENT)
        get = (give * client_rate).quantize(CENT, rounding=ROUND_DOWN)
        formula = f"{give} × [{fmid}] × (1 − {margin}) = {get} {get_ccy} (вниз до 0.01)"
        side = "give"
    elif client_buys_get:
        # Agreed buy markup formula: client buys `get` units of `get_ccy` and pays `give_ccy`:
        # unit_mid = give_ccy per 1 get_ccy = 1 / mid
        unit_mid, used_inv, fmid_inv = mid_rate(get_ccy, give_ccy, book, now)
        used = used_inv
        get = get_amount.quantize(CENT)
        unit_client = unit_mid * (Decimal(1) + margin)
        give = (get * unit_client).quantize(CENT, rounding=ROUND_UP)
        client_rate = Decimal(1) / unit_client
        formula = f"{get} × [{fmid_inv}] × (1 + {margin}) = {give} {give_ccy} (покупка {get_ccy}, вверх до 0.01)"
        side = "get"
    else:
        client_rate = mid * (Decimal(1) - margin)
        get = get_amount.quantize(CENT)
        give = (get / client_rate).quantize(CENT, rounding=ROUND_UP)
        formula = f"{get} ÷ ([{fmid}] × (1 − {margin})) = {give} {give_ccy} (вверх до 0.01)"
        side = "get"
    if get <= 0 or give <= 0:
        raise QuoteError("после округления сумма равна нулю — слишком маленькая сделка")
    qid = _quote_id(give_ccy, give, get_ccy, get, margin, now.isoformat(), *(r.label() for r in used))
    return Quote(qid, give_ccy, give, get_ccy, get, mid, client_rate, margin, now, now + ttl,
                 tuple(r.label() for r in used), formula, side)


def _fmt_spaced(val: Decimal) -> str:
    q = val.quantize(CENT)
    sign = "-" if q < 0 else ""
    s = f"{abs(q):.2f}"
    int_part, frac_part = s.split(".")
    chunks = []
    while int_part:
        chunks.append(int_part[-3:])
        int_part = int_part[:-3]
    return f"{sign}{' '.join(reversed(chunks))},{frac_part}"


def render_branded_document(q: Quote, *, deal: dict | None = None) -> str:
    """Render a branded SwapMe HTML/SVG receipt or quote card without external dependencies."""
    doc_kind = "ПОДТВЕРЖДЁННАЯ СДЕЛКА" if deal else "КОТИРОВКА (РАСЧЁТ)"
    doc_id = f"D-{deal['id']} (по {q.id})" if deal else q.id
    status_label = str(deal.get("crm_status") or deal.get("status") or "settled").upper() if deal else "QUOTED"
    pct = f"{(q.margin * 100).normalize():f}%"
    give_str = f"{_fmt_spaced(q.give_amount)} {q.give_ccy}"
    get_str = f"{_fmt_spaced(q.get_amount)} {q.get_ccy}"
    ts_str = q.created_at.strftime("%d.%m.%Y %H:%M UTC")
    exp_str = q.valid_until.strftime("%d.%m.%Y %H:%M UTC")
    rates_html = "".join(f"<li>{r}</li>" for r in q.rates)
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>SwapMe — {doc_kind} {doc_id}</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0b0f19; color: #f3f4f6; margin: 0; padding: 24px; }}
  .card {{ max-width: 560px; margin: 0 auto; background: #111827; border: 1px solid #1f2937; border-radius: 16px; padding: 24px; box-shadow: 0 10px 30px rgba(0,0,0,0.45); }}
  .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #1f2937; padding-bottom: 16px; margin-bottom: 16px; }}
  .brand {{ font-size: 22px; font-weight: 700; color: #38bdf8; letter-spacing: 0.5px; }}
  .badge {{ background: #1e293b; color: #93c5fd; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }}
  .row {{ display: flex; justify-content: space-between; margin: 10px 0; font-size: 15px; }}
  .val {{ font-weight: 600; color: #ffffff; }}
  .highlight {{ background: #0f172a; border-left: 4px solid #38bdf8; padding: 12px; border-radius: 8px; margin: 14px 0; }}
  .meta {{ font-size: 12px; color: #9ca3af; margin-top: 16px; line-height: 1.5; }}
</style>
</head>
<body>
<div class="card" data-doc-id="{doc_id}" data-status="{status_label}">
  <div class="header">
    <div class="brand"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2" style="vertical-align:middle;margin-right:6px"><circle cx="12" cy="12" r="9"/><path d="M8 10h8M8 14h8"/></svg>SwapMe Exchange · Prague</div>
    <div class="badge">{doc_kind} · {status_label}</div>
  </div>
  <div class="row"><span>Документ:</span><span class="val">{doc_id}</span></div>
  <div class="row"><span>Дата и время:</span><span class="val">{ts_str}</span></div>
  <div class="highlight">
    <div class="row"><span>Клиент отдаёт:</span><span class="val">{give_str}</span></div>
    <div class="row"><span>Клиент получает:</span><span class="val">{get_str}</span></div>
    <div class="row"><span>Согласованная ставка:</span><span class="val">{pct}</span></div>
  </div>
  <div class="row"><span>Формула расчёта:</span><span class="val">{q.formula}</span></div>
  <div class="row"><span>Действует до:</span><span class="val">{exp_str}</span></div>
  <div class="meta">
    <strong>Источники курсов (провенанс):</strong>
    <ul>{rates_html}</ul>
  </div>
</div>
</body>
</html>"""
