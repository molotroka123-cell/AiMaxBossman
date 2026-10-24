"""warterminal.monetization: deal maths (Decimal, unpriced, FX never 1:1), regulatory tracks and ranking order.
Synthetic tenders in a temp DB only; no network."""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from conftest import TODAY
from warterminal import contacts as C, monetization as M, rules
from warterminal.store import Store

D = Decimal
CONTACT = {"id": 1, "company": "Fuel Test s.r.o.", "country": "CZ"}


def tender(key_id, title="Diesel fuel supply", cpv=("09134200",), *, value=100000.0, cur="EUR", country="CZ", days=30, status="open"):
    deadline = f"{TODAY.fromordinal(TODAY.toordinal() + days).isoformat()}T23:59:59Z"
    return {"id": f"test:{key_id}", "official_notice_id": key_id, "key": f"CZ:{key_id}", "title": title, "jurisdiction": country, "country": country,
            "category": "fuel_energy", "status": status, "deadline": deadline, "estimated_value": value, "currency": cur,
            "source_url": f"https://nen.nipez.cz/{key_id}", "source_id": "cz", "retrieved_at": "2026-10-17T00:00:00+00:00",
            "published_at": TODAY.isoformat(), "source_version": "t", "checksum": key_id, "data_quality": "ok", "buyer": "Test buyer", "cpv": list(cpv)}


# ------------------------------------------------------------------ unpriced + FX
@pytest.mark.parametrize("value,cur", [(None, "EUR"), (0, "EUR"), (-5, "EUR"), (50000.0, None), (50000.0, "")])
def test_unpriced_tender_gives_none_money_never_zero(value, cur):
    e = M.calculate_deal_economics(tender("U1", value=value, cur=cur), CONTACT, fx_rates={"EURUSD": 1.1})
    assert e["priced"] is False and e["pricing_status"] == "FRAMEWORK_OR_UNPRICED"
    assert e["estimated_value"] is None and e["estimated_value_usd"] is None and e["estimated_value_eur"] is None
    assert e["back_to_back_supply"] is None
    ag = e["agency_introducer"]
    assert ag["commission_base"] is None and ag["commission_2pct"] is None and ag["commission_5pct"] is None and ag["commission_base_usd"] is None
    assert e["local_subcontract"]["fee_amount"] is None


def test_missing_fx_gives_none_not_a_one_to_one_rate():
    e = M.calculate_deal_economics(tender("F1", value=500000.0, cur="UAH"), CONTACT, fx_rates={"EURUSD": 1.1, "USDCZK": 20.0})
    assert e["priced"] is True and e["estimated_value"] == "500000.00"
    assert e["fx_status"] == "FX_RATE_MISSING"
    assert e["estimated_value_usd"] is None and e["estimated_value_eur"] is None       # NOT "500000.00"
    assert e["agency_introducer"]["commission_base_usd"] is None
    b2b = e["back_to_back_supply"]
    assert b2b["gross_profit_usd"] is None and b2b["peak_funding_need_usd"] is None
    assert b2b["capital_unmatched"]["status"] == "FX_REQUIRED" and b2b["capital_unmatched"]["allowed"] is False


def test_no_fx_at_all_still_none_and_same_currency_needs_no_rate():
    e = M.calculate_deal_economics(tender("F2", value=1000.0, cur="CZK"), CONTACT, fx_rates={})
    assert e["estimated_value_usd"] is None and e["fx_status"] == "FX_RATE_MISSING"
    u = M.calculate_deal_economics(tender("F3", value=1000.0, cur="USD"), CONTACT, fx_rates={})
    assert u["estimated_value_usd"] == "1000.00" and u["fx_status"] == "OK" and u["estimated_value_eur"] is None


def test_cross_rate_through_czk_is_exact():
    e = M.calculate_deal_economics(tender("F4", value=24325.0, cur="EUR"), CONTACT, fx_rates={"EURCZK": 24.325, "USDCZK": 21.622})
    assert e["estimated_value_usd"] == str((D("24325.00") * D("24.325") / D("21.622")).quantize(D("0.01")))


# ------------------------------------------------------------------ Decimal exactness
@pytest.mark.parametrize("value", [0.1 + 0.2, 33651521.35, 1234.565, 99999.99, 155000.0])
def test_money_is_decimal_exact_and_reconciles(value):
    e = M.calculate_deal_economics(tender("X1", value=value, cur="EUR"), CONTACT, fx_rates={"EURUSD": 1.1})
    bid = D(str(value)).quantize(D("0.01"))
    assert e["estimated_value"] == str(bid)
    ag = e["agency_introducer"]
    assert ag["commission_2pct"] == str((bid * 2 / 100).quantize(D("0.01")))
    assert ag["commission_base"] == str((bid * 3 / 100).quantize(D("0.01")))
    assert ag["commission_5pct"] == str((bid * 5 / 100).quantize(D("0.01")))
    b2b = e["back_to_back_supply"]
    assert D(b2b["landed_cost"]) + D(b2b["gross_profit"]) == bid                          # no float drift: parts sum to the bid
    for v in (ag["commission_base"], b2b["landed_cost"], b2b["gross_profit"], e["estimated_value_usd"]):
        assert isinstance(v, str) and len(v.split(".")[1]) == 2 and "e" not in v.lower()


def test_float_noise_never_leaks_into_money_strings():
    e = M.calculate_deal_economics(tender("X2", value=0.1 + 0.2, cur="EUR"), CONTACT, fx_rates={"EURUSD": 1.1})
    assert e["estimated_value"] == "0.30" and "0000000000" not in str(e)


# ------------------------------------------------------------------ regulatory tracks
def test_military_and_dual_use_are_not_fast_start():
    prof = C.Profile({"id": 1, "company": "x", "cpv_prefixes": ["353", "09", "35613", "38"], "keywords": {}})
    for t, want in ((tender("R1", "Small arms ammunition 5.56", ["35331000"]), "MILITARY_MPO_LICENCE"),
                    (tender("R2", "Body armour vests", ["18100000"]), "MILITARY_MPO_LICENCE"),
                    (tender("R3", "Unmanned aerial vehicles", ["35613000"]), "MILITARY_MPO_LICENCE"),   # CPV 356* = military
                    (tender("R5", "Weather radar apparatus", ["38115000"]), "DUAL_USE_2021_821"),
                    (tender("R4", "Diesel fuel supply", ["09134200"]), "UNCONTROLLED_FAST_START")):
        badges, _ = C.assess(t, prof, C.match(t, prof), None)
        reg = M.classify_regulatory_track({"badges": badges, "tender": t})
        assert reg["track"] == want, t["title"]
        assert reg["fast_start"] is (want == "UNCONTROLLED_FAST_START")


def _store_with(tmp_path, tenders, contact_docs):
    s = Store(tmp_path / "war.db")
    for t in tenders:
        s.upsert_tender({k: v for k, v in t.items() if k != "key"})
    for c in contact_docs:
        s.add_contact(C.clean(c))
    return s


def test_military_track_excluded_from_fast_start_count(tmp_path):
    s = _store_with(tmp_path, [tender("M1", "Diesel fuel supply", ["09134200"], value=20000.0),
                               tender("M2", "Small arms ammunition 5.56", ["35331000"], value=20000.0)],
                    [{"company": "Mixed Test s.r.o.", "country": "CZ", "cpv_prefixes": ["09", "353"]}])
    rep = M.monetization_opportunities(s, None, today=TODAY, fx_override={"EURUSD": 1.1})
    by_track = {i["tender"]["key"]: i["regulatory"] for i in rep["items"]}
    assert by_track["CZ:M1"]["fast_start"] is True
    assert by_track["CZ:M2"]["track"] == "MILITARY_MPO_LICENCE" and by_track["CZ:M2"]["fast_start"] is False
    fast = [i["tender"]["key"] for i in rep["items"] if i["regulatory"]["fast_start"]]
    assert fast == ["CZ:M1"] and rep["summary"]["fast_start_uncontrolled_pairs"] == 1
    mil = next(i for i in rep["items"] if i["tender"]["key"] == "CZ:M2")
    assert mil["recommendable"] is False and mil["recommendation_status"] == "REVIEW_REQUIRED_DO_NOT_PITCH"


# ------------------------------------------------------------------ ranking order
def test_ranking_is_sorted_and_deterministic_under_insertion_order(tmp_path):
    values = [5000.0, 250000.0, 40000.0, None, 40000.0, 900.0]
    base = [tender(f"K{i}", "Diesel fuel supply", ["09134200"], value=v) for i, v in enumerate(values)]
    contact_docs = [{"company": "Fuel A s.r.o.", "country": "CZ", "cpv_prefixes": ["09"]},
                    {"company": "Fuel B s.r.o.", "country": "CZ", "cpv_prefixes": ["091"]}]
    runs = []
    for seed in range(3):
        order = base[:]
        random.Random(seed).shuffle(order)
        d = tmp_path / f"r{seed}"
        d.mkdir()
        s = _store_with(d, order, contact_docs)
        rep = M.monetization_opportunities(s, None, k_per_contact=10, today=TODAY, fx_override={"EURUSD": 1.1})
        items = rep["items"]
        keys = [(-i["priority_score"], -i["match_score"], -float(i["economics"]["estimated_value_usd"] or 0), i["tender"]["key"], i["contact"]["company"])
                for i in items]
        assert keys == sorted(keys)
        runs.append([(i["tender"]["key"], i["contact"]["company"]) for i in items])
    assert runs[0] == runs[1] == runs[2]
    # higher value first among equal scores; priced before unpriced; equal values broken by tender key
    first_contact = [k for k, c in runs[0] if c == "Fuel A s.r.o."]
    assert first_contact == ["CZ:K1", "CZ:K2", "CZ:K4", "CZ:K0", "CZ:K5", "CZ:K3"]


def test_expired_tender_is_not_an_opportunity(tmp_path):
    s = _store_with(tmp_path, [tender("E1", days=-1), tender("E2", days=30)], [{"company": "Fuel A s.r.o.", "country": "CZ", "cpv_prefixes": ["09"]}])
    rep = M.monetization_opportunities(s, None, today=TODAY)
    assert [i["tender"]["key"] for i in rep["items"]] == ["CZ:E2"]
    assert rules.effective_status(tender("E1", days=-1), TODAY) == "closed"
