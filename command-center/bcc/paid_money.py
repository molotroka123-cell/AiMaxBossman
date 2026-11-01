"""One rounding rule for paid-call money (owner master prompt 01.11, item 2 «ЗАКРОЙ РАСХОДЫ»).

Codex 01.11 reproduced that a paid estimate below half a micro-dollar was rounded to 0, so 20 reservations of
$0.0000001 passed a total budget of $0.000001. The rule for every paid path in Bossman:

* a positive estimate, reservation or known actual charge is rounded UP to the micro-dollar (never down to 0);
* a paid call never reserves 0 (`paid_quote` refuses 0, negative, NaN/inf, bool, None and non-numbers);
* what is left of a cap is rounded DOWN (`floor_micro`), so rounding never makes room that is not there;
* unknown price or unknown final cost is not money: it stays None and the callers keep failing closed.

These amounts are Bossman's LOCAL bookkeeping against the owner's caps. They do not limit what a provider bills;
the provider's own usage page is the source of truth for the actual charge.
"""
from __future__ import annotations

import math
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

MICRO = Decimal("0.000001")
#: float noise (0.1 + 0.2 = 0.30000000000000004) is trimmed at 1e-12 before rounding, so an exact micro amount is not
#: inflated by one micro-dollar; a positive amount that trims to 0 still becomes one micro-dollar.
_NOISE = Decimal("0.000000000001")


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def ceil_micro(value: Any) -> float:
    """Round a non-negative amount UP to the micro-dollar; 0 stays 0, any positive amount is at least 1e-6.

    Raises ValueError for negative, non-finite or non-numeric input (callers decide how to fail closed)."""
    num = _finite_number(value)
    if num is None or num < 0:
        raise ValueError("invalid money amount")
    if num == 0:
        return 0.0
    try:
        trimmed = Decimal(repr(num)).quantize(_NOISE, rounding=ROUND_HALF_EVEN)
        out = trimmed.quantize(MICRO, rounding=ROUND_CEILING)
    except InvalidOperation as exc:            # absurdly large values: refuse rather than guess
        raise ValueError("invalid money amount") from exc
    return float(max(out, MICRO))


def floor_micro(value: Any) -> float:
    """Round what is LEFT of a cap DOWN to the micro-dollar (never creates headroom)."""
    num = _finite_number(value)
    if num is None:
        raise ValueError("invalid money amount")
    try:
        trimmed = Decimal(repr(num)).quantize(_NOISE, rounding=ROUND_HALF_EVEN)
        return float(trimmed.quantize(MICRO, rounding=ROUND_FLOOR))
    except InvalidOperation as exc:
        raise ValueError("invalid money amount") from exc


def paid_quote(value: Any) -> float:
    """The amount a PAID call reserves: positive, rounded up to the micro-dollar. 0 / unknown / invalid -> ValueError."""
    num = _finite_number(value)
    if num is None or num <= 0:
        raise ValueError("a paid call needs a positive quote")
    return ceil_micro(num)


def ceil_micro_or_none(value: Any) -> float | None:
    """Known actual charge -> rounded up; None / invalid stays None (unknown is never booked as 0)."""
    try:
        return ceil_micro(value)
    except ValueError:
        return None
