"""Invoice totals. Money is kept in integer cents everywhere."""
from __future__ import annotations


def line_total(unit_cents: int, qty: int, discount_pct: int = 0) -> int:
    """Total of one line in cents after a percentage discount, rounded half up."""
    gross = unit_cents * qty
    return gross - (gross * discount_pct) // 100


def invoice_total(lines: list[tuple[int, int, int]], vat_pct: int = 21) -> int:
    """Sum of line totals plus VAT, in cents, rounded half up once at the end."""
    net = sum(line_total(u, q, d) for u, q, d in lines)
    return net + (net * vat_pct + 50) // 100
