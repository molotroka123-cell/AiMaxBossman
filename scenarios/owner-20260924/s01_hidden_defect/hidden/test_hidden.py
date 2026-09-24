import importlib, sys, pathlib
from invoice import invoice_total, line_total

def test_discount_rounds_half_up():
    # 999 * 1 at 15% = 149.85 discount -> discounted 849.15 -> half up 849
    assert line_total(999, 1, 15) == 849
    # 333 * 3 = 999 at 50% -> 499.5 -> half up 500
    assert line_total(333, 3, 50) == 500
    assert line_total(1000, 3, 10) == 2700

def test_invoice_total_unchanged_contract():
    assert invoice_total([(1000, 1, 0)]) == 1210
    assert invoice_total([(999, 1, 15), (333, 3, 50)]) == ((849 + 500) * 121 + 50) // 100
