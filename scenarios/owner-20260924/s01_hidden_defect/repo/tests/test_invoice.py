from invoice import invoice_total, line_total


def test_line_without_discount():
    assert line_total(1000, 3) == 3000


def test_invoice_with_vat():
    assert invoice_total([(1000, 1, 0)]) == 1210
