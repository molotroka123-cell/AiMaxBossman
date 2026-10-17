from decimal import Decimal as D

import pytest

from warterminal import finance
from conftest import Env


def test_margin_differs_from_markup():
    lc = finance.landed_cost(purchase=1000, freight=100, insurance=10, duties_pct=5, certification=40, financing=20, risk_reserve_pct=2, overhead=30)
    # duties = (1000+100+10)*5% = 55.5 ; reserve = 20 ; landed = 1000+100+10+55.5+40+20+20+30 = 1275.5
    assert lc["duties"] == D("55.50") and lc["risk_reserve"] == D("20.00") and lc["landed_cost"] == D("1275.50")
    m = finance.price_metrics(1500, lc["landed_cost"])
    assert m["profit"] == D("224.50") and m["margin_pct"] == D("14.97") and m["markup_pct"] == D("17.60") and m["margin_pct"] != m["markup_pct"]


def test_fx_requires_an_explicit_rate_never_one_to_one():
    with pytest.raises(ValueError):
        finance.to_base(100, "CZK", "USD", {})
    assert finance.to_base(100, "EUR", "USD", {"EURUSD": 1.1}) == D("110.0")
    assert finance.to_base(110, "USD", "EUR", {"EURUSD": 1.1}) == D("100")
    assert finance.to_base(5, "USD", "USD", None) == D(5)


def test_cashflow_vat_cash_out_and_recovery_and_peak_need():
    ev = finance.build_events(supplier_prepay_pct=30, supplier_total=1000, delivery_day=30, acceptance_day=40, bid_price=1500, customer_pay_delay_days=30,
                              vat_import=210, vat_recovery_days=60, logistics_cost=100)
    cf = finance.cashflow(ev)
    # day0 -300 ; day30 -700 -100 -210 = -1310 cumulative ; day70 +1500 ; day90 +210
    assert cf["peak_funding_need"] == D("1310.00") and cf["peak_day"] == 30 and cf["final_balance"] == D("400.00")


def test_delay_stress_30_60_90_reduces_profit_monotonically():
    def events(extra):
        return finance.build_events(supplier_prepay_pct=30, supplier_total=1000, delivery_day=30, acceptance_day=40, bid_price=1500,
                                    customer_pay_delay_days=30 + extra, logistics_cost=100)
    rows = finance.stress(events, bid_price=D(1500), landed=D(1200), annual_rate=D("0.12"))
    assert [r["extra_delay_days"] for r in rows] == [0, 30, 60, 90]
    profits = [r["profit"] for r in rows]
    assert profits == sorted(profits, reverse=True) and profits[0] > profits[-1]
    assert rows[0]["extra_carry_cost"] == D("0.00")


def test_capital_check_three_states():
    assert finance.capital_check(40000)["status"] == "WITHIN_LIMIT"
    over = finance.capital_check(120000)
    assert over["status"] == "OVER_LIMIT" and over["allowed"] is False
    matched = finance.capital_check(120000, order_confirmed=True, supplier_contract_signed=True)
    assert matched["status"] == "MATCHED_DEAL" and matched["needs_owner_approval"] is True
    assert finance.capital_check(120000, order_confirmed=True)["status"] == "OVER_LIMIT"       # supplier contract missing


def test_finance_endpoint_and_missing_fx(tmp_path):
    e = Env(tmp_path)
    body = {"currency": "EUR", "fx": {"EURUSD": 1.1}, "purchase": 30000, "freight": 2000, "duties_pct": 0, "bid_price": 40000, "supplier_prepay_pct": 50,
            "delivery_day": 30, "acceptance_day": 35, "customer_pay_delay_days": 60}
    r = e.req("POST", "/finance/estimate", "analyst", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j["landed"]["landed_cost"] == "32000.00" and j["metrics"]["margin_pct"] == "20.00" and j["metrics"]["markup_pct"] == "25.00"
    assert j["capital"]["status"] in ("WITHIN_LIMIT", "OVER_LIMIT", "MATCHED_DEAL") and len(j["stress"]) == 4
    bad = e.req("POST", "/finance/estimate", "analyst", json=dict(body, fx={}))
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "FX_OR_INPUT"
