"""Учёт сделок обменника: импорт CSV и отчёт за период (MVP пайплайна 3).

Только расчёт по переданным строкам: ни сети, ни секретов, ни реальных клиентских
данных. Деньги считаются в Decimal; строка, которую нельзя разобрать, не молчит,
а попадает в `rejected` с причиной — «пустой отчёт» и «все строки отвергнуты» это
разные исходы.

Колонки CSV: ts (ISO, UTC), side (buy|sell), asset, amount, rate, fee, currency.
  buy  — обменник купил актив у клиента (платит клиенту amount*rate);
  sell — обменник продал актив клиенту (получает amount*rate).
fee — комиссия обменника в валюте расчёта, всегда >= 0.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

REQUIRED = ("ts", "side", "asset", "amount", "rate", "fee", "currency")


@dataclass(frozen=True, slots=True)
class Deal:
    ts: datetime
    side: str
    asset: str
    amount: Decimal
    rate: Decimal
    fee: Decimal
    currency: str

    @property
    def notional(self) -> Decimal:
        return self.amount * self.rate


@dataclass(slots=True)
class Rejected:
    line: int
    reason: str


@dataclass(slots=True)
class Import:
    deals: list[Deal] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)


def _dec(raw: str, name: str, *, positive: bool) -> Decimal:
    try:
        value = Decimal(raw.strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"{name}: не число ({raw!r})") from None
    if not value.is_finite():
        raise ValueError(f"{name}: не конечное число")
    if (positive and value <= 0) or (not positive and value < 0):
        raise ValueError(f"{name}: {'должно быть > 0' if positive else 'не может быть < 0'}")
    return value


def parse_csv(text: str) -> Import:
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"в CSV нет колонок: {', '.join(missing)}")
    out = Import()
    for n, row in enumerate(reader, start=2):
        try:
            ts = datetime.fromisoformat((row["ts"] or "").strip().replace("Z", "+00:00"))
            if ts.tzinfo is None:
                raise ValueError("ts: нужен часовой пояс (UTC, суффикс Z)")
            side = (row["side"] or "").strip().lower()
            if side not in ("buy", "sell"):
                raise ValueError(f"side: {row['side']!r} (нужно buy или sell)")
            asset = (row["asset"] or "").strip().upper()
            currency = (row["currency"] or "").strip().upper()
            if not asset or not currency:
                raise ValueError("asset и currency обязательны")
            out.deals.append(Deal(ts.astimezone(timezone.utc), side, asset,
                                  _dec(row["amount"], "amount", positive=True),
                                  _dec(row["rate"], "rate", positive=True),
                                  _dec(row["fee"], "fee", positive=False), currency))
        except ValueError as exc:
            out.rejected.append(Rejected(n, str(exc)))
    return out


def period_report(deals: list[Deal], start: datetime, end: datetime) -> dict:
    """Отчёт за [start, end): по каждой паре актив/валюта объёмы, комиссии и движение денег.

    cash_flow = полученное от клиентов - выплаченное клиентам. Комиссия показана
    отдельной строкой `fees`, а не вычтена молча. Сверка с договорной ставкой — `fee_check`.
    """
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("период: нужны даты с часовым поясом и end > start")
    rows: dict[tuple[str, str], dict] = {}
    for d in deals:
        if not (start <= d.ts < end):
            continue
        r = rows.setdefault((d.asset, d.currency), dict(
            asset=d.asset, currency=d.currency, deals=0, bought=Decimal(0), sold=Decimal(0),
            paid_out=Decimal(0), received=Decimal(0), fees=Decimal(0)))
        r["deals"] += 1
        r["fees"] += d.fee
        if d.side == "buy":
            r["bought"] += d.amount
            r["paid_out"] += d.notional
        else:
            r["sold"] += d.amount
            r["received"] += d.notional
    pairs = []
    for r in rows.values():
        r["cash_flow"] = r["received"] - r["paid_out"]
        r["net_position"] = r["bought"] - r["sold"]
        pairs.append(r)
    pairs.sort(key=lambda r: (r["asset"], r["currency"]))
    return {"start": start.isoformat(), "end": end.isoformat(),
            "deals": sum(r["deals"] for r in pairs), "pairs": pairs}


def render_report(rep: dict, rejected: list[Rejected], flagged: list[dict]) -> str:
    """Текст отчёта для владельца обменника: цифры, отвергнутые строки и спорные комиссии."""
    lines = [f"Отчёт за период {rep['start']} — {rep['end']}", f"Сделок: {rep['deals']}"]
    for p in rep["pairs"]:
        lines.append(f"{p['asset']}/{p['currency']}: сделок {p['deals']}, куплено {p['bought']}, продано {p['sold']}, "
                     f"выплачено {p['paid_out']}, получено {p['received']}, движение денег {p['cash_flow']}, "
                     f"позиция {p['net_position']}, комиссии {p['fees']}")
    if rep["deals"] == 0:
        lines.append("В периоде сделок нет.")
    lines.append(f"Отвергнуто строк: {len(rejected)}")
    lines += [f"  строка {r.line}: {r.reason}" for r in rejected]
    lines.append(f"Комиссия расходится со ставкой: {len(flagged)}")
    lines += [f"  {f['ts']} {f['asset']}: факт {f['fee']}, по ставке {f['expected']}" for f in flagged]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Отчёт обменника за период по CSV сделок")
    ap.add_argument("csv_path")
    ap.add_argument("--start", required=True, help="ISO-дата начала (UTC), например 2026-10-01")
    ap.add_argument("--end", required=True, help="ISO-дата конца, не включительно")
    ap.add_argument("--fee-pct", default="", help="договорная ставка комиссии в %% для сверки")
    args = ap.parse_args(argv)
    with open(args.csv_path, encoding="utf-8-sig", newline="") as fh:
        imp = parse_csv(fh.read())

    def day(s: str) -> datetime:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    rep = period_report(imp.deals, day(args.start), day(args.end))
    flagged = fee_check(imp.deals, Decimal(args.fee_pct)) if args.fee_pct else []
    print(render_report(rep, imp.rejected, flagged))
    return 1 if (imp.rejected and not imp.deals) else 0


def fee_check(deals: list[Deal], rate_pct: Decimal, tolerance: Decimal = Decimal("0.01")) -> list[dict]:
    """Сделки, где комиссия отличается от notional*rate_pct/100 больше чем на tolerance."""
    bad = []
    for d in deals:
        expected = d.notional * rate_pct / Decimal(100)
        if abs(d.fee - expected) > tolerance:
            bad.append(dict(ts=d.ts.isoformat(), asset=d.asset, fee=d.fee, expected=expected))
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
