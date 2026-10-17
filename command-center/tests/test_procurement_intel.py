"""Bosman Terminal: разбор TED/USAspending и скоринг «реализуемо». Данные синтетические, сеть не нужна."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from bossman_shared.procurement_intel import (Opportunity, fetch_awards, fetch_ted, parse_ted,
                                              parse_usaspending, score)

TODAY = date(2026, 10, 17)

TED = {"notices": [
    {"publication-number": "1-2026", "notice-title": {"hun": ["Lőszer"], "eng": ["Cartridges 7.62"]},
     "buyer-name": {"lit": ["Agency A"]}, "publication-date": "2026-10-10+02:00",
     "deadline-receipt-tender-date-lot": ["2026-11-12+01:00"], "estimated-value-lot": ["60000", "40000"],
     "estimated-value-cur-lot": ["EUR"], "classification-cpv": ["35330000", "35330000"],
     "place-of-performance-country-lot": ["LTU"], "links": {"pdf": {"ENG": "https://ted/1.pdf"}}},
    {"publication-number": "2-2026", "notice-title": {"eng": ["Fire brigade uniforms"]},
     "buyer-name": {"fra": ["Mairie B"]}, "publication-date": "2026-10-11+02:00",
     "estimated-value-lot": [], "classification-cpv": ["35113400"], "links": {"xml": {"MUL": "https://ted/2.xml"}}},
]}
USA = {"results": [{"Award ID": "W1", "Recipient Name": "ACME LLC", "Award Amount": 1261790.4,
                    "Awarding Agency": "DHS", "Description": "AMMUNITION 9MM", "generated_internal_id": "CONT_W1"}]}


def test_parse_ted_prefers_english_title_and_sums_lots():
    a, b = parse_ted(TED)
    assert a.title == "Cartridges 7.62" and a.value == Decimal("100000") and a.currency == "EUR"
    assert a.deadline == "2026-11-12" and a.cpv == ["35330000"] and a.url == "https://ted/1.pdf"
    assert b.value is None and b.url == "https://ted/2.xml"


def test_parse_usaspending_is_award_intel():
    (o,) = parse_usaspending(USA)
    assert o.kind == "award" and o.value == Decimal("1261790.4") and o.title.startswith("ACME LLC: AMMUNITION")


def test_military_cpv_gets_licence_flag_but_fire_uniforms_do_not():
    a, b = parse_ted(TED)
    assert "licence_check" in score(a, today=TODAY)["flags"]
    assert "licence_check" not in score(b, today=TODAY)["flags"]      # CPV 351 — гражданские


def test_award_is_never_a_submittable_opportunity():
    (o,) = parse_usaspending(USA)
    s = score(o, today=TODAY)
    assert s["score"] == 0 and "intel_only" in s["flags"] and "licence_check" in s["flags"]


def test_expired_and_over_cap_are_rejected_but_good_one_scores_high():
    base = dict(source="ted", kind="tender", id="x", title="Cartridges", buyer="b", country="LTU",
                currency="EUR", published="2026-10-01", cpv=["35330000"])
    good = Opportunity(value=Decimal("90000"), deadline="2026-11-20", **base)
    expired = Opportunity(value=Decimal("90000"), deadline="2026-10-01", **base)
    big = Opportunity(value=Decimal("5000000"), deadline="2026-11-20", **base)
    g, e, b = (score(o, cpv_prefixes=("353",), today=TODAY) for o in (good, expired, big))
    assert g["score"] >= 80 and e["score"] == 0 and "expired" in e["flags"]
    assert "value_over_cap" in b["flags"] and b["score"] < g["score"] - 30


def test_fetch_uses_injected_transport_and_builds_queries():
    calls = []

    def fake(url, body):
        calls.append((url, body))
        return TED if "ted" in url else USA

    assert len(fetch_ted("353", 7, post=fake)) == 2
    assert len(fetch_awards("ammunition", 7, post=fake)) == 1
    assert "classification-cpv=353*" in calls[0][1]["query"]
    assert calls[1][1]["filters"]["keywords"] == ["ammunition"]
