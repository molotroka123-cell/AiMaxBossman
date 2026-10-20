"""Deterministic Decimal quote: mid-market from sourced rates, desk margin, expiry, a readable formula.

Client rate = mid x (1 - margin), whichever way the client swaps (both directions carry the margin).
Rounding always in the desk's favour and stated: what the client receives is rounded down,
what the client must give is rounded up, to 0.01.
"""
from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import InvalidOperation, ROUND_DOWN, ROUND_UP, Decimal

from .rates import Rate, RateBook, RateError

CURRENCIES = ("USDT", "USD", "EUR", "CZK")
DEFAULT_MARGIN = Decimal("0.03")
SOFT_MARGIN_LIMIT = Decimal("0.10")
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
    client_rate: Decimal         # mid x (1 - margin) or 1 / (unit_mid x (1 + margin))
    margin: Decimal
    created_at: datetime
    valid_until: datetime
    rates: tuple[str, ...] = field(default=())
    formula: str = ""
    fixed_side: str = "give"     # which amount the client named
    direction: str = "SELL"      # "BUY" (client buys get_ccy) or "SELL" (client sells give_ccy)
    manager_margin_decision: str = "default"  # "explicit" when manager specified margin, else "default"

    def text(self) -> str:
        pct = (self.margin * 100).normalize()
        dir_label = "Покупка клиентом" if self.direction == "BUY" else "Продажа клиентом"
        return "\n".join([
            f"Котировка {self.id} ({dir_label})",
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


def _coerce_decimal(val: Decimal | str | int | float | None, name: str) -> Decimal | None:
    if val is None:
        return None
    if isinstance(val, Decimal):
        return val
    try:
        return Decimal(str(val).strip().replace(",", ".").replace(" ", ""))
    except (InvalidOperation, ValueError, TypeError):
        raise QuoteError(f"{name}: некорректное число ({val!r})") from None


def build_quote(book: RateBook, *, give_ccy: str, get_ccy: str, now: datetime, give_amount: Decimal | None = None,
                get_amount: Decimal | None = None, margin: Decimal | None = None,
                require_explicit_margin: bool = False, confirm_high_margin: bool = False,
                client_buys_get: bool = False,
                ttl: timedelta = DEFAULT_TTL) -> Quote:
    """Build a deterministic Decimal quote.
    - When `margin is None` (not specified in message), `manager_margin_decision='default'` and `DEFAULT_MARGIN` is used
      unless `require_explicit_margin=True`, which raises `QuoteError`.
    - Soft limit: when `margin > 10%` (`SOFT_MARGIN_LIMIT`) and `confirm_high_margin=False`, raises `QuoteError` asking
      for explicit confirmation of the high fee.
    """
    give_ccy, get_ccy = give_ccy.upper(), get_ccy.upper()
    for c in (give_ccy, get_ccy):
        if c not in CURRENCIES:
            raise QuoteError(f"валюта {c} не поддерживается (есть: {', '.join(CURRENCIES)})")
    if give_ccy == get_ccy:
        raise QuoteError("валюты совпадают")
    give_amount = _coerce_decimal(give_amount, "сумма")
    get_amount = _coerce_decimal(get_amount, "сумма")
    margin = _coerce_decimal(margin, "наценка")
    if (give_amount is None) == (get_amount is None):
        raise QuoteError("укажите ровно одну сумму: сколько клиент отдаёт ИЛИ сколько хочет получить")
    if margin is None:
        if require_explicit_margin:
            raise QuoteError("процент комиссии (margin) не указан менеджером: укажите явную ставку по сделке")
        margin = DEFAULT_MARGIN
        margin_decision = "default"
    else:
        margin_decision = "explicit"
    if not margin.is_finite() or not (Decimal(0) <= margin < Decimal("0.5")):
        raise QuoteError("наценка вне 0–50%")
    if margin > SOFT_MARGIN_LIMIT and not confirm_high_margin:
        pct_str = (margin * 100).normalize()
        raise QuoteError(
            f"ставка {pct_str}% превышает мягкий лимит 10% — подтвердите высокую наценку "
            f"(добавьте «подтверждаю ставку» или укажите ставку до 10%)"
        )
    amount = give_amount if give_amount is not None else get_amount
    assert amount is not None
    if not amount.is_finite() or amount <= 0 or amount > MAX_AMOUNT:
        raise QuoteError(f"сумма должна быть больше 0 и не больше {MAX_AMOUNT}")
    try:
        mid, used, fmid = mid_rate(give_ccy, get_ccy, book, now)
    except RateError as e:
        raise QuoteError(str(e)) from None
    try:
        if give_amount is not None:
            client_rate = mid * (Decimal(1) - margin)
            give = give_amount.quantize(CENT)
            get = (give * client_rate).quantize(CENT, rounding=ROUND_DOWN)
            formula = f"{give} × [{fmid}] × (1 − {margin}) = {get} {get_ccy} (вниз до 0.01)"
            side = "give"
            direction = "SELL"
        elif client_buys_get:
            assert get_amount is not None
            unit_mid, used_inv, fmid_inv = mid_rate(get_ccy, give_ccy, book, now)
            used = used_inv
            get = get_amount.quantize(CENT)
            unit_client = unit_mid * (Decimal(1) + margin)
            give = (get * unit_client).quantize(CENT, rounding=ROUND_UP)
            client_rate = Decimal(1) / unit_client
            formula = f"{get} × [{fmid_inv}] × (1 + {margin}) = {give} {give_ccy} (покупка {get_ccy}, вверх до 0.01)"
            side = "get"
            direction = "BUY"
        else:
            assert get_amount is not None
            client_rate = mid * (Decimal(1) - margin)
            get = get_amount.quantize(CENT)
            give = (get / client_rate).quantize(CENT, rounding=ROUND_UP)
            formula = f"{get} ÷ ([{fmid}] × (1 − {margin})) = {give} {give_ccy} (вверх до 0.01)"
            side = "get"
            direction = "BUY"
    except InvalidOperation as exc:
        raise QuoteError(f"ошибка округления суммы: {exc}") from None
    if get <= 0 or give <= 0:
        raise QuoteError("после округления сумма равна нулю — слишком маленькая сделка")
    qid = _quote_id(give_ccy, give, get_ccy, get, margin, now.isoformat(), *(r.label() for r in used))
    return Quote(qid, give_ccy, give, get_ccy, get, mid, client_rate, margin, now, now + ttl,
                 tuple(r.label() for r in used), formula, side, direction, margin_decision)


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
    """Render a branded SwapMe HTML/SVG receipt or quote card with strict html.escape on all dynamic fields."""
    esc = html.escape
    is_cancelled = bool(deal and (deal.get("status") == "reversed" or deal.get("crm_status") == "cancelled"))
    doc_kind = "ОТМЕНЁННАЯ СДЕЛКА (VOID)" if is_cancelled else ("ПОДТВЕРЖДЁННАЯ СДЕЛКА" if deal else "КОТИРОВКА (РАСЧЁТ)")
    doc_id = esc(f"D-{deal['id']} (по {q.id})" if deal else q.id)
    status_raw = "CANCELLED / VOID" if is_cancelled else (str(deal.get("crm_status") or deal.get("status") or "settled").upper() if deal else "QUOTED")
    status_label = esc(status_raw)
    pct = esc(f"{(q.margin * 100).normalize():f}%")
    give_str = esc(f"{_fmt_spaced(q.give_amount)} {q.give_ccy}")
    get_str = esc(f"{_fmt_spaced(q.get_amount)} {q.get_ccy}")
    ts_str = esc(q.created_at.strftime("%d.%m.%Y %H:%M UTC"))
    exp_str = esc(q.valid_until.strftime("%d.%m.%Y %H:%M UTC"))
    formula_str = esc(q.formula)
    rates_html = "".join(f"<li>{esc(str(r))}</li>" for r in q.rates)
    cancel_block = ""
    if is_cancelled and deal:
        reason_str = esc(str(deal.get("reverse_reason") or "отменена"))
        rev_at_str = esc(str(deal.get("reversed_at") or ""))
        cancel_block = f'<div class="row"><span>Причина отмены:</span><span class="val">{reason_str} ({rev_at_str})</span></div>'
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>SwapMe — {esc(doc_kind)} {doc_id}</title>
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
    <div class="badge">{esc(doc_kind)} · {status_label}</div>
  </div>
  <div class="row"><span>Документ:</span><span class="val">{doc_id}</span></div>
  <div class="row"><span>Дата и время:</span><span class="val">{ts_str}</span></div>
  {cancel_block}
  <div class="highlight">
    <div class="row"><span>Клиент отдаёт:</span><span class="val">{give_str}</span></div>
    <div class="row"><span>Клиент получает:</span><span class="val">{get_str}</span></div>
    <div class="row"><span>Согласованная ставка:</span><span class="val">{pct}</span></div>
  </div>
  <div class="row"><span>Формула расчёта:</span><span class="val">{formula_str}</span></div>
    <div class="row"><span>Действует до:</span><span class="val">{exp_str}</span></div>
  <div class="meta">
    <strong>Источники курсов (провенанс):</strong>
    <ul>{rates_html}</ul>
  </div>
</div>
</body>
</html>"""


def _pdf_cp1251_literal(text: str) -> bytes:
    """Encode text (including Cyrillic) as a CP1251 PDF literal string (...) for Type1/WinAnsi-compatible PDF streams."""
    normalized = (
        text.replace("—", "-")
        .replace("–", "-")
        .replace("−", "-")
        .replace("×", "x")
        .replace("÷", "/")
        .replace("•", "*")
        .replace("·", ".")
    )
    raw = normalized.encode("cp1251", errors="replace")
    out = bytearray(b"(")
    for b in raw:
        if b in (0x28, 0x29, 0x5C):  # '(', ')', '\\'
            out.append(0x5C)
            out.append(b)
        elif 32 <= b <= 126:
            out.append(b)
        else:
            out.extend(f"\\{b:03o}".encode("ascii"))
    out.append(0x29)
    return bytes(out)


def render_pdf_document(q: Quote, *, deal: dict | None = None) -> bytes:
    """Render a deterministic, dependency-free %PDF-1.4 receipt/quote with CP1251 Cyrillic support and VOID/cancelled status."""
    is_cancelled = bool(deal and (deal.get("status") == "reversed" or deal.get("crm_status") == "cancelled"))
    doc_kind = "VOID / ОТМЕНЕНА (КВИТАНЦИЯ)" if is_cancelled else ("КВИТАНЦИЯ СДЕЛКИ / SETTLED RECEIPT" if deal else "КОТИРОВКА / EXCHANGE QUOTE")
    doc_id = f"D-{deal['id']} ({q.id})" if deal else q.id
    status_label = "CANCELLED / VOID (ОТМЕНЕНА)" if is_cancelled else (
        str(deal.get("crm_status") or deal.get("status") or "settled").upper() if deal else "QUOTED"
    )
    pct = f"{(q.margin * 100).normalize():f}%"
    raw_in = deal.get("leg_in_actual") if deal else None
    raw_out = deal.get("leg_out_actual") if deal else None
    in_amt = Decimal(str(raw_in)) if raw_in and str(raw_in) != "unknown" else q.give_amount
    out_amt = Decimal(str(raw_out)) if raw_out and str(raw_out) != "unknown" else q.get_amount
    give_str = f"{_fmt_spaced(in_amt)} {q.give_ccy}"
    get_str = f"{_fmt_spaced(out_amt)} {q.get_ccy}"
    ts_str = q.created_at.strftime("%d.%m.%Y %H:%M UTC")
    exp_str = q.valid_until.strftime("%d.%m.%Y %H:%M UTC")
    rate_str = f"1 {q.give_ccy} = {q.client_rate.quantize(Decimal('0.000001'))} {q.get_ccy} (mid {q.mid.quantize(Decimal('0.000001'))})"

    lines = [
        f"SwapMe Exchange - Prague  |  {doc_kind}",
        f"Документ / Document: {doc_id}   Статус / Status: {status_label}",
        f"Создан / Created:  {ts_str}   Действует до: {exp_str}",
        "-" * 68,
        f"Клиент отдаёт (inflow):   {give_str}",
        f"Клиент получает (outflow): {get_str}",
        f"Ставка / Desk margin:     {pct} ({q.direction})",
        f"Курс клиента / Rate:      {rate_str}",
        "-" * 68,
    ]
    if is_cancelled and deal:
        lines.append(f"СТАТУС: СДЕЛКА ОТМЕНЕНА (VOID) | Причина: {deal.get('reverse_reason') or 'отмена'}")
        if deal.get("reversed_at"):
            lines.append(f"Время отмены / Reversed at: {deal.get('reversed_at')}")
        lines.append("-" * 68)
    if deal and (deal.get("slippage_in") not in (None, "0", "0.00") or deal.get("slippage_out") not in (None, "0", "0.00")):
        lines.append(f"Отклонение / Slippage:    in={deal.get('slippage_in', '0.00')} {q.give_ccy}, out={deal.get('slippage_out', '0.00')} {q.get_ccy}")
    for idx, r_label in enumerate(q.rates, 1):
        lines.append(f"Источник курса #{idx}: {r_label}")

    stream_chunks: list[bytes] = [
        ("% SwapMe CP1251/UTF-8 metadata: " + f"SwapMe Exchange | {doc_id} | {status_label} | {give_str} -> {get_str} | {pct}").encode("utf-8"),
        b"q",
        b"0.07 0.09 0.15 rg 36 710 523 64 re f",
        b"0.22 0.74 0.97 RG 2 w 52 726 32 32 re S",
        b"0.22 0.74 0.97 RG 2 w 60 746 m 76 746 l S 60 738 m 76 738 l S",
        b"Q",
        b"BT",
        b"/F1 14 Tf 1 1 1 rg 96 746 Td (SwapMe Exchange - Prague) Tj",
        b"/F1 10 Tf 0.58 0.77 0.99 rg 0 -18 Td " + _pdf_cp1251_literal(f"{doc_kind}  |  {doc_id}  |  {status_label}") + b" Tj",
        b"ET",
        b"BT",
        b"/F1 11 Tf 0.1 0.12 0.16 rg 44 680 Td 18 TL",
    ]
    for line in lines:
        stream_chunks.append(_pdf_cp1251_literal(line) + b" '")
    stream_chunks.append(b"ET")
    content_bytes = b"\n".join(stream_chunks)

    objs: list[bytes] = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>\nendobj\n",
        f"5 0 obj\n<< /Length {len(content_bytes)} >>\nstream\n".encode("ascii") + content_bytes + b"\nendstream\nendobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)
