"""SwapMe practical flow (owner item 7, 01.11.2026): one deal from a sourced rate to a client statement.

    official rate (CNB/ECB through the existing War Terminal FX log: value + source + as_of + fixing date)
 -> the manager's markup, typed explicitly for THIS deal (there is no hidden default)
 -> Decimal calculation, one rounding at the end: ROUND_HALF_UP to 0.01
 -> quote (status «quote») -> client agreed («pending») -> ACTUAL amounts confirmed within tolerance («confirmed»)
 -> local CRM record (client alias, amounts, rate, source, date, status) -> statement per client / period (CSV, text).

Nothing is sent, paid or transferred here, and no wallet is touched. A quote or a pending deal is never revenue;
a confirmed deal is what the manager typed as actually received/paid — Bossman does not see a bank or a wallet.
Floats never enter the arithmetic: every input is parsed to Decimal (a float input goes through its repr string).
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from .quote import DEFAULT_TTL, MAX_AMOUNT, Quote, _quote_id
from .rates import OFFICIAL_MAX_AGE, Rate, RateBook, RateError, pair_name
from .store import Store, StoreError

CENT = Decimal("0.01")
ROUNDING = ROUND_HALF_UP
ROUNDING_RULE = "ROUND_HALF_UP до 0.01: одно округление итоговой суммы, половина сотой — вверх (10.005 → 10.01)"
MARKUP_MIN_PCT = Decimal("0")
MARKUP_MAX_PCT = Decimal("10")
MAX_RATE_AGE_HOURS = int(OFFICIAL_MAX_AGE.total_seconds() // 3600)     # 96 h: a daily fixing + a weekend
DEFAULT_TOLERANCE_PCT = Decimal("0.5")
MAX_TOLERANCE_PCT = Decimal("2")
BASES = ("USDT", "USD", "EUR")
QUOTE_CCYS = ("CZK",)
SIDES = ("client_buys", "client_sells")
USDT_PARITY_NOTE = ("ДОПУЩЕНИЕ: 1 USDT ≈ 1 USD (паритет 1:1). Это не рыночный курс USDT: официального источника "
                    "для USDT нет, риск отклонения USDT от доллара несёт менеджер.")
NOT_REVENUE_NOTE = ("Котировка и ожидающая сделка — не выручка. «Подтверждена» значит: менеджер ввёл фактически полученные и "
                    "выданные суммы; деньги на счёте/кошельке Bossman не проверяет.")
STATUS_RU = {"quote": "котировка (не сделка, не выручка)", "pending": "клиент согласен, ждём деньги (не выручка)",
             "confirmed": "подтверждена фактическими суммами", "expired": "котировка просрочена", "cancelled": "отменена"}
_ALIAS = re.compile(r"^[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё ._\-]{0,39}$")


class FlowError(ValueError):
    """A refused step of the practical flow; the message says what is wrong and what to do."""


# ------------------------------------------------------------------ inputs
def dec(raw: object, name: str, *, positive: bool = True, max_places: int | None = None) -> Decimal:
    """Strict Decimal from user input: no bool/None/NaN/inf, no float arithmetic, optional > 0 and max decimal places."""
    if raw is None or isinstance(raw, bool):
        raise FlowError(f"{name}: не указано")
    if isinstance(raw, Decimal):
        v = raw
    elif isinstance(raw, int):
        v = Decimal(raw)
    elif isinstance(raw, float):
        v = Decimal(repr(raw))           # 'nan' / 'inf' become Decimal NaN/Infinity and are refused below
    elif isinstance(raw, str):
        s = raw.strip().replace(" ", "").replace(" ", "").replace(",", ".")
        if not s:
            raise FlowError(f"{name}: пусто")
        try:
            v = Decimal(s)
        except InvalidOperation:
            raise FlowError(f"{name}: не число ({raw!r})") from None
    else:
        raise FlowError(f"{name}: не число ({type(raw).__name__})")
    if not v.is_finite():
        raise FlowError(f"{name}: не число (NaN/бесконечность)")
    if positive and v <= 0:
        raise FlowError(f"{name}: должно быть больше 0 (получено {v})")
    if max_places is not None and v != v.quantize(Decimal(1).scaleb(-max_places)):
        raise FlowError(f"{name}: не больше {max_places} знаков после точки (получено {v})")
    return v


def client_alias(raw: object) -> str:
    alias = " ".join(str(raw or "").split())
    if not _ALIAS.match(alias):
        raise FlowError("клиент: псевдоним 1–40 символов (буквы, цифры, пробел . _ -), начинается с буквы или цифры")
    if re.search(r"\d{7,}", alias):
        raise FlowError("клиент: похоже на телефон/номер — в CRM пишется только псевдоним, без персональных данных")
    return alias


# ------------------------------------------------------------------ rate with source and date
@dataclass(frozen=True)
class SourcedRate:
    base: str
    quote: str
    value: Decimal                  # units of quote per 1 base, mid-market (no fee)
    legs: tuple[dict, ...]          # provenance of every rate used (value, source, as_of, rate_date)
    assumptions: tuple[str, ...]
    formula: str

    @property
    def source(self) -> str:
        return " + ".join(f"{leg['source']} {leg['pair_display']}" for leg in self.legs) or "—"

    @property
    def as_of(self) -> str:
        """The OLDEST observation among the legs: a cross rate is only as fresh as its oldest part."""
        return min((leg["timestamp_utc"] for leg in self.legs), default="")

    @property
    def rate_date(self) -> str | None:
        dates = [leg["rate_date"] for leg in self.legs if leg.get("rate_date")]
        return min(dates) if dates else None


def _fresh(r: Rate, now: datetime, max_age_h: int) -> dict:
    if not (r.source or "").strip():
        raise FlowError(f"курс {pair_name(r.pair)}: нет источника — курс без источника не используется")
    if r.as_of - now > timedelta(minutes=5):
        raise FlowError(f"курс {pair_name(r.pair)}: время из будущего ({r.as_of.isoformat()})")
    age = now - r.as_of
    if age > timedelta(hours=max_age_h):
        raise FlowError(f"курс {pair_name(r.pair)} устарел: снят {r.as_of.astimezone(timezone.utc):%d.%m.%Y %H:%M UTC}, "
                        f"возраст {int(age.total_seconds() // 3600)} ч > {max_age_h} ч; обновите курс")
    if r.rate_date:
        try:
            published_for = date.fromisoformat(r.rate_date)
        except ValueError:
            raise FlowError(f"курс {pair_name(r.pair)}: непонятная дата фиксинга {r.rate_date!r}") from None
        valid_through = datetime.combine(published_for + timedelta(days=1), dtime(0), tzinfo=timezone.utc)
        if now - valid_through > timedelta(hours=max_age_h):
            raise FlowError(f"курс {pair_name(r.pair)} устарел: фиксинг на {published_for:%d.%m.%Y}, это старше {max_age_h} ч; "
                            "обновите курс")
    return r.provenance()


def sourced_rate(book: RateBook, base: str, quote: str, now: datetime, *, max_age_h: int = MAX_RATE_AGE_HOURS,
                 assume_usdt_usd_parity: bool = False) -> SourcedRate:
    base, quote = str(base or "").upper(), str(quote or "").upper()
    if base not in BASES or quote not in QUOTE_CCYS:
        raise FlowError(f"пара {base}/{quote} не поддерживается (есть: {', '.join(b + '/CZK' for b in BASES)})")

    def get(pair: str) -> Rate:
        try:
            return book.get(pair, now)
        except RateError as e:
            raise FlowError(str(e)) from None

    legs: list[dict] = []
    assumptions: list[str] = []
    if base == "EUR":
        r = get("EURCZK")
        legs.append(_fresh(r, now, max_age_h))
        return SourcedRate(base, quote, r.value, tuple(legs), (), f"EUR/CZK {r.value} ({r.source})")
    r = get("USDCZK")
    legs.append(_fresh(r, now, max_age_h))
    if base == "USD":
        return SourcedRate(base, quote, r.value, tuple(legs), (), f"USD/CZK {r.value} ({r.source})")
    try:                                     # USDT: an owner-set manual USDT/USD rate with its source wins
        u = book.get("USDTUSD", now)
    except RateError as e:
        if not assume_usdt_usd_parity:
            raise FlowError(f"{e}. Либо явно примите допущение USDT≈USD 1:1 для этой сделки "
                            "(assume_usdt_usd_parity / «usdt=usd»)") from None
        assumptions.append(USDT_PARITY_NOTE)
        return SourcedRate(base, quote, r.value, tuple(legs), tuple(assumptions),
                           f"USD/CZK {r.value} ({r.source}) × USDT/USD 1 (допущение)")
    legs.append(_fresh(u, now, max_age_h))
    return SourcedRate(base, quote, u.value * r.value, tuple(legs), (),
                       f"USD/CZK {r.value} ({r.source}) × USDT/USD {u.value} ({u.source})")


# ------------------------------------------------------------------ the calculation
def manager_total(amount: Decimal, market_rate: Decimal, markup_pct: Decimal, side: str) -> tuple[Decimal, Decimal]:
    """(manager_rate, total). client_buys: total = amount × rate × (1 + markup/100); client_sells: × (1 − markup/100).
    Exact Decimal product, ONE rounding at the end (ROUND_HALF_UP to 0.01). 1000 × 25.00 × 1.03 = 25750.00."""
    if side not in SIDES:
        raise FlowError(f"сторона сделки: {side!r} (есть: client_buys — клиент покупает, client_sells — клиент продаёт)")
    k = Decimal(1) + markup_pct / Decimal(100) if side == "client_buys" else Decimal(1) - markup_pct / Decimal(100)
    manager_rate = market_rate * k
    return manager_rate, (amount * market_rate * k).quantize(CENT, rounding=ROUNDING)


# ------------------------------------------------------------------ steps
def quote(store: Store, book: RateBook, *, client: object, side: str, base: str, amount: object, markup_pct: object,
          now: datetime, quote_ccy: str = "CZK", max_rate_age_hours: object = MAX_RATE_AGE_HOURS,
          assume_usdt_usd_parity: bool = False, tolerance_pct: object = DEFAULT_TOLERANCE_PCT, training: bool = False,
          by: str = "owner", ttl: timedelta = DEFAULT_TTL) -> dict:
    """Step 1–3: sourced rate + explicit markup -> Decimal quote saved with its CRM record. Status «quote»."""
    alias = client_alias(client)
    if side not in SIDES:
        raise FlowError(f"сторона сделки: {side!r} (есть: client_buys — клиент покупает, client_sells — клиент продаёт)")
    amt = dec(amount, "сумма", max_places=2)
    if amt > MAX_AMOUNT:
        raise FlowError(f"сумма: больше лимита {MAX_AMOUNT}")
    amt = amt.quantize(CENT)
    if markup_pct is None or (isinstance(markup_pct, str) and not markup_pct.strip()):
        raise FlowError("наценка не указана: введите наценку менеджера для ЭТОЙ сделки явно (скрытой наценки по умолчанию нет)")
    markup = dec(markup_pct, "наценка, %", positive=False)
    if not MARKUP_MIN_PCT <= markup <= MARKUP_MAX_PCT:
        raise FlowError(f"наценка {markup}% вне допустимого диапазона {MARKUP_MIN_PCT}–{MARKUP_MAX_PCT}%")
    age_h = dec(max_rate_age_hours, "макс. возраст курса, ч", max_places=0)
    if not 1 <= age_h <= MAX_RATE_AGE_HOURS:
        raise FlowError(f"макс. возраст курса: от 1 до {MAX_RATE_AGE_HOURS} ч (получено {age_h})")
    tol = dec(tolerance_pct, "допуск расхождения, %", positive=False)
    if not Decimal(0) <= tol <= MAX_TOLERANCE_PCT:
        raise FlowError(f"допуск расхождения {tol}% вне диапазона 0–{MAX_TOLERANCE_PCT}%")
    src = sourced_rate(book, base, quote_ccy, now, max_age_h=int(age_h), assume_usdt_usd_parity=assume_usdt_usd_parity)
    manager_rate, total = manager_total(amt, src.value, markup, side)
    if total <= 0:
        raise FlowError("после округления итог равен 0 — слишком маленькая сумма")
    sign = "+" if side == "client_buys" else "−"
    formula = (f"{amt} {src.base} × [{src.formula}] × (1 {sign} {markup}%) = {total} {src.quote} "
               f"({ROUNDING_RULE.split(':')[0]})")
    if side == "client_buys":           # the client receives `amt` base and pays `total` CZK
        give_ccy, give_amt, get_ccy, get_amt = src.quote, total, src.base, amt
        mid, client_rate, direction, fixed = Decimal(1) / src.value, amt / total, "BUY", "get"
    else:                               # the client gives `amt` base and receives `total` CZK
        give_ccy, give_amt, get_ccy, get_amt = src.base, amt, src.quote, total
        mid, client_rate, direction, fixed = src.value, manager_rate, "SELL", "give"
    labels = tuple(f"{leg['pair_display']} = {leg['value']} ({leg['source']}, снят {leg['timestamp_utc']}"
                   + (f", курс на {leg['rate_date']}" if leg.get("rate_date") else "") + ")" for leg in src.legs) + src.assumptions
    qid = _quote_id("flow", alias, side, give_ccy, give_amt, get_ccy, get_amt, markup, now.isoformat(), *labels)
    q = Quote(qid, give_ccy, give_amt, get_ccy, get_amt, mid, client_rate, markup / Decimal(100), now, now + ttl,
              labels, formula, fixed, direction, "explicit")
    store.save_flow_quote(q, {
        "quote_id": qid, "client_alias": alias, "created_at": now.astimezone(timezone.utc).isoformat(), "side": side,
        "base_ccy": src.base, "quote_ccy": src.quote, "amount": str(amt), "total": str(total),
        "market_rate": str(src.value), "rate_source": src.source, "rate_as_of": src.as_of, "rate_date": src.rate_date,
        "rate_provenance": json.dumps(list(src.legs), ensure_ascii=False), "markup_pct": str(markup),
        "manager_rate": str(manager_rate), "rounding": ROUNDING_RULE,
        "assumptions": json.dumps(list(src.assumptions), ensure_ascii=False), "tolerance_pct": str(tol),
        "max_rate_age_hours": int(age_h), "training": 1 if training else 0, "created_by": by})
    return record(store, qid, now)


def _flow_row(store: Store, qid: str) -> dict:
    rec = store.crm_row(qid)
    if rec is None:
        raise FlowError(f"котировка {qid} не из практического потока (нет CRM-записи) или не найдена")
    return rec


def accept(store: Store, qid: str, now: datetime, *, by: str = "owner") -> dict:
    """Step 4a: the client agreed to the quote while it is valid -> «pending». No amounts yet, never revenue."""
    rec = _flow_row(store, qid)
    store.confirm(rec["quote_id"], now, by, crm_status="accepted")
    return record(store, rec["quote_id"], now)


def confirm(store: Store, qid: str, now: datetime, *, actual_in: object, actual_out: object, by: str = "owner") -> dict:
    """Step 4b: the ACTUAL amounts (what the desk really received / really paid) -> «confirmed».
    actual_in is in the currency the client gives, actual_out in the currency the client receives.
    Refused beyond the record's tolerance; there is no override here — a different deal needs a new quote."""
    rec = _flow_row(store, qid)
    a_in = dec(actual_in, "фактически получено от клиента", max_places=2).quantize(CENT)
    a_out = dec(actual_out, "фактически выдано клиенту", max_places=2).quantize(CENT)
    q = store.quote(rec["quote_id"])
    assert q is not None
    tol = Decimal(rec["tolerance_pct"])
    q_in, q_out = Decimal(q["give_amount"]), Decimal(q["get_amount"])
    dev_in = abs(a_in - q_in) * 100 / q_in
    dev_out = abs(a_out - q_out) * 100 / q_out
    if dev_in > tol or dev_out > tol:
        raise FlowError(
            f"фактические суммы расходятся с котировкой {rec['quote_id']} больше допуска {tol}%: "
            f"получено {a_in} {q['give_ccy']} при котировке {q_in} ({dev_in.quantize(CENT, rounding=ROUNDING)}%), "
            f"выдано {a_out} {q['get_ccy']} при котировке {q_out} ({dev_out.quantize(CENT, rounding=ROUNDING)}%). "
            "Проверьте суммы; если условия изменились — сделайте новую котировку")
    deal, _created = store.confirm(rec["quote_id"], now, by, crm_status="settled", leg_in_actual=a_in, leg_out_actual=a_out,
                                   max_deviation_pct=tol, confirm_deviation=False)
    if deal.get("crm_status") != "settled":
        raise FlowError(f"сделка D-{deal['id']} не подтверждена (статус {deal.get('crm_status')})")
    if Decimal(str(deal["leg_in_actual"])) != a_in or Decimal(str(deal["leg_out_actual"])) != a_out:
        raise FlowError(f"по котировке {rec['quote_id']} уже подтверждены другие суммы (D-{deal['id']}: "
                        f"{deal['leg_in_actual']} / {deal['leg_out_actual']}); повтор не записан")
    return record(store, rec["quote_id"], now)


# ------------------------------------------------------------------ CRM view and statement
def _status(row: dict, now: datetime) -> str:
    if row.get("deal_id") is not None:
        if row.get("deal_status") == "reversed" or row.get("deal_crm_status") == "cancelled":
            return "cancelled"
        if row.get("deal_crm_status") == "settled" and row.get("leg_in_actual") not in (None, "unknown") \
                and row.get("leg_out_actual") not in (None, "unknown"):
            return "confirmed"
        return "pending"
    if row.get("quote_status") == "cancelled":
        return "cancelled"
    return "expired" if now > datetime.fromisoformat(row["valid_until"]) else "quote"


def _view(row: dict, now: datetime) -> dict:
    body = json.loads(row["quote_body"])
    status = _status(row, now)
    out = {
        "quote_id": row["quote_id"], "deal_id": row.get("deal_id"), "client": row["client_alias"],
        "created_at": row["created_at"], "valid_until": row["valid_until"], "confirmed_at": row.get("confirmed_at") if status == "confirmed" else None,
        "status": status, "status_ru": STATUS_RU[status],
        # a quote / pending / training deal is never revenue; a confirmed real deal is the manager's typed amounts
        "counts_as_revenue": status == "confirmed" and not row["training"],
        "training": bool(row["training"]), "side": row["side"],
        "client_gives": {"ccy": body["give_ccy"], "quoted": body["give_amount"],
                         "actual": row["leg_in_actual"] if status == "confirmed" else None},
        "client_gets": {"ccy": body["get_ccy"], "quoted": body["get_amount"],
                        "actual": row["leg_out_actual"] if status == "confirmed" else None},
        "market_rate": row["market_rate"], "rate_pair": f"{row['base_ccy']}/{row['quote_ccy']}",
        "rate_source": row["rate_source"], "rate_as_of": row["rate_as_of"], "rate_date": row["rate_date"],
        "markup_pct": row["markup_pct"], "manager_rate": str(Decimal(row["manager_rate"]).quantize(Decimal("0.0001"), rounding=ROUNDING)),
        "rounding": row["rounding"], "assumptions": json.loads(row["assumptions"]), "tolerance_pct": row["tolerance_pct"],
        "formula": body.get("formula", ""), "markup_value": None,
    }
    if status == "confirmed":
        # value of the markup against the source rate, in CZK; NOT profit (the desk's cost of the currency is unknown)
        a_in, a_out, mkt = Decimal(row["leg_in_actual"]), Decimal(row["leg_out_actual"]), Decimal(row["market_rate"])
        val = a_in - a_out * mkt if row["side"] == "client_buys" else a_in * mkt - a_out
        out["markup_value"] = {"ccy": row["quote_ccy"], "amount": str(val.quantize(CENT, rounding=ROUNDING)),
                               "note": "наценка к курсу-источнику, не прибыль: себестоимость валюты неизвестна"}
    return out


def record(store: Store, qid: str, now: datetime) -> dict:
    rows = [r for r in store.crm_rows() if r["quote_id"] == str(qid).upper()]
    if not rows:
        raise FlowError(f"котировка {qid} не найдена в CRM")
    return _view(rows[0], now)


def statement(store: Store, now: datetime, *, client: object = None, start: datetime | None = None,
              end: datetime | None = None) -> dict:
    """Step 5: the CRM statement for one client (or all) over [start, end) by record creation time."""
    alias = client_alias(client) if client not in (None, "") else None
    if start is not None and end is not None and end <= start:
        raise FlowError("период: конец должен быть позже начала")
    rows = []
    for r in store.crm_rows(alias):
        t = datetime.fromisoformat(r["created_at"])
        if (start is None or t >= start) and (end is None or t < end):
            rows.append(_view(r, now))
    totals: dict[str, dict[str, Decimal]] = {}
    counts = {k: 0 for k in STATUS_RU}
    markup_total: dict[str, Decimal] = {}
    for v in rows:
        counts[v["status"]] += 1
        if v["status"] != "confirmed":
            continue
        gin, gout = v["client_gives"], v["client_gets"]
        totals.setdefault(gin["ccy"], {"received": Decimal(0), "paid": Decimal(0)})["received"] += Decimal(gin["actual"])
        totals.setdefault(gout["ccy"], {"received": Decimal(0), "paid": Decimal(0)})["paid"] += Decimal(gout["actual"])
        mv = v["markup_value"]
        markup_total[mv["ccy"]] = markup_total.get(mv["ccy"], Decimal(0)) + Decimal(mv["amount"])
    return {
        "client": alias or "все клиенты",
        "period": {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None},
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "rows": rows, "counts": counts,
        "confirmed_totals": {k: {kk: str(vv) for kk, vv in v.items()} for k, v in sorted(totals.items())},
        "confirmed_markup_value": {k: str(v) for k, v in sorted(markup_total.items())},
        "revenue_note": NOT_REVENUE_NOTE,
        "training_rows": sum(1 for v in rows if v["training"]),
    }


CSV_COLUMNS = ("created_at", "client", "quote_id", "deal_id", "status", "training", "side", "client_gives_ccy",
               "client_gives_quoted", "client_gives_actual", "client_gets_ccy", "client_gets_quoted", "client_gets_actual",
               "rate_pair", "market_rate", "rate_source", "rate_as_of", "rate_date", "markup_pct", "manager_rate",
               "rounding", "assumptions", "markup_value_czk")


def statement_csv(st: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for v in st["rows"]:
        w.writerow([v["created_at"], v["client"], v["quote_id"], f"D-{v['deal_id']}" if v["deal_id"] else "", v["status"],
                    "yes" if v["training"] else "no", v["side"], v["client_gives"]["ccy"], v["client_gives"]["quoted"],
                    v["client_gives"]["actual"] or "", v["client_gets"]["ccy"], v["client_gets"]["quoted"],
                    v["client_gets"]["actual"] or "", v["rate_pair"], v["market_rate"], v["rate_source"], v["rate_as_of"],
                    v["rate_date"] or "", v["markup_pct"], v["manager_rate"], v["rounding"], " | ".join(v["assumptions"]),
                    (v["markup_value"] or {}).get("amount", "")])
    w.writerow([])
    w.writerow(["# note", NOT_REVENUE_NOTE])
    return buf.getvalue()


def statement_text(st: dict) -> str:
    p = st["period"]
    head = f"Выписка SwapMe: {st['client']}"
    if p["start"] or p["end"]:
        head += f" · {p['start'] or '…'} — {p['end'] or '…'}"
    lines = [head, f"Сформирована {st['generated_at']}"]
    if not st["rows"]:
        lines.append("Записей нет.")
    for v in st["rows"]:
        g, r = v["client_gives"], v["client_gets"]
        amounts = (f"получено {g['actual']} {g['ccy']}, выдано {r['actual']} {r['ccy']}" if v["status"] == "confirmed"
                   else f"по котировке: клиент отдаёт {g['quoted']} {g['ccy']}, получает {r['quoted']} {r['ccy']}")
        lines.append(f"• {v['created_at'][:16].replace('T', ' ')} {v['client']} {v['quote_id']}"
                     + (f" D-{v['deal_id']}" if v["deal_id"] else "") + f" — {v['status_ru']}"
                     + (" [УЧЕБНАЯ]" if v["training"] else ""))
        lines.append(f"  {amounts}")
        lines.append(f"  курс {v['rate_pair']} {v['market_rate']} ({v['rate_source']}, {v['rate_as_of']}"
                     + (f", фиксинг {v['rate_date']}" if v["rate_date"] else "") + f"), наценка {v['markup_pct']}%, "
                     f"курс менеджера {v['manager_rate']}")
        for a in v["assumptions"]:
            lines.append(f"  {a}")
    c = st["counts"]
    lines.append(f"Итого: подтверждено {c['confirmed']}, ждут денег {c['pending']}, котировок {c['quote']}, "
                 f"просрочено {c['expired']}, отменено {c['cancelled']}")
    for ccy, t in st["confirmed_totals"].items():
        lines.append(f"  {ccy}: получено {t['received']}, выдано {t['paid']} (только подтверждённые)")
    for ccy, amt in st["confirmed_markup_value"].items():
        lines.append(f"  наценка к курсу-источнику: {amt} {ccy} (не прибыль)")
    lines.append(NOT_REVENUE_NOTE)
    return "\n".join(lines)


def quote_text(rec: dict) -> str:
    """Reply for the pult/«Обмен» tab. Starts with «Котировка» and keeps «Клиент отдаёт:/получает:» lines (the Telegram
    pult builds its «Обмен выполнен» button from them)."""
    g, r = rec["client_gives"], rec["client_gets"]
    lines = [f"Котировка {rec['quote_id']} · клиент {rec['client']} · статус: {rec['status_ru']}"
             + (" · УЧЕБНАЯ" if rec["training"] else ""),
             f"Клиент отдаёт: {g['quoted']} {g['ccy']}",
             f"Клиент получает: {r['quoted']} {r['ccy']}",
             f"Курс-источник {rec['rate_pair']}: {rec['market_rate']} ({rec['rate_source']}, снят {rec['rate_as_of']}"
             + (f", курс на {rec['rate_date']}" if rec["rate_date"] else "") + ")",
             f"Наценка менеджера: {rec['markup_pct']}% → курс менеджера {rec['manager_rate']}",
             f"Формула: {rec['formula']}",
             f"Округление: {rec['rounding']}"]
    lines += rec["assumptions"]
    lines.append(f"Действует до {rec['valid_until']}. Дальше: «клиент согласен {rec['quote_id']}», затем "
                 f"«обмен выполнен {rec['quote_id']} приняли <сумма> выдали <сумма>» (допуск {rec['tolerance_pct']}%).")
    return "\n".join(lines)


# ------------------------------------------------------------------ the rate source itself (reuse, not a new fetcher)
def _fx_tracker():
    """The existing War Terminal FX tracker (CNB daily fixing + ECB reference rates; xe.com is never scraped)."""
    try:
        from warterminal import fx_tracker  # type: ignore[import-not-found]
    except ImportError:
        here = Path(__file__).resolve().parents[2] / "apps" / "war-terminal"
        if not (here / "warterminal" / "fx_tracker.py").is_file():
            raise FlowError("War Terminal FX tracker не установлен — курсы ЧНБ/ЕЦБ взять неоткуда") from None
        sys.path.append(str(here))
        from warterminal import fx_tracker  # type: ignore[import-not-found]
    return fx_tracker


def collect_official_rates(fx_dir: str | Path, fetch: Callable[[str], bytes | str], *, now: datetime | None = None,
                           sleep: Callable[[float], None] | None = None) -> dict:
    """One visit of the existing collector into <fx_dir>/fx.db (append-only, source URL + observed_at + fixing date).
    `fetch(url)` is injected: tests pass a fake, the training run passes the collector's own allow-listed HTTP getter.
    No language model is used here (gateway=None)."""
    fx = _fx_tracker()
    store = fx.open_store(fx_dir)
    try:
        kw = {"get": fetch, "gateway": None, "force": True, "log": lambda *a, **k: None}
        if now is not None:
            kw["now"] = now
        if sleep is not None:
            kw["sleep"] = sleep
        res = fx.collect(store, **kw)
    finally:
        store.close()
    return {**res, "fx_db": str(Path(fx_dir) / "fx.db"), "sources": {"cnb": fx.CNB_URL, "ecb": fx.ECB_URL}}
