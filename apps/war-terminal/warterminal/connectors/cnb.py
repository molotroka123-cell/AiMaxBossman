"""Czech National Bank daily FX fixing (official, no key). Rate = CZK for `Amount` units of the currency."""
from __future__ import annotations

from datetime import datetime

URL = "https://www.cnb.cz/en/financial-markets/foreign-exchange-market/central-bank-exchange-rate-fixing/central-bank-exchange-rate-fixing/daily.txt"


def parse(text: str) -> dict:
    lines = [l for l in text.splitlines() if l.strip()]
    as_of = datetime.strptime(lines[0].split("#")[0].strip(), "%d %b %Y").date().isoformat()
    rates = {}
    for line in lines[2:]:
        parts = line.split("|")
        if len(parts) != 5:
            continue
        _, _, amount, code, rate = parts
        rates[f"{code.strip()}CZK"] = round(float(rate.replace(",", ".")) / float(amount), 8)
    if "USDCZK" not in rates or "EURCZK" not in rates:
        raise ValueError("ЧНБ: в файле нет USD/EUR")
    return {"as_of": as_of, "rates": rates, "source": "ČNB daily fixing", "url": URL}
