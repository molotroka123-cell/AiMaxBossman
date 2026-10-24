"""warterminal.dossier: per-client tender-lead dossier + POST /api/v1/monetization/dossier.
Synthetic tenders (temp DB / in memory) and the real fixtures; no network, no model call."""
from __future__ import annotations

import json
import pathlib
from datetime import date, datetime, timezone

import pytest

from conftest import TODAY
from warterminal import dossier as DS
from warterminal.connectors import prozorro as pz

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures_prozorro.json").read_text(encoding="utf-8"))
FUEL = {"name": "Palivo Test s.r.o.", "country": "CZ", "cpv_codes": ["09", "15"], "keywords": {"en": ["diesel"]}, "countries": ["CZ", "DE", "UA"]}
FX = {"EURUSD": 1.1, "USDCZK": 20.0}


def tender(key_id, title, cpv, *, value=40000.0, cur="EUR", country="CZ", days=30, status="open", buyer="Mesto Test"):
    deadline = f"{TODAY.fromordinal(TODAY.toordinal() + days).isoformat()}T23:59:59Z" if days is not None else None
    return {"id": f"test:{key_id}", "official_notice_id": key_id, "title": title, "jurisdiction": country, "country": country,
            "category": "other", "status": status, "deadline": deadline, "estimated_value": value, "currency": cur,
            "source_url": f"https://nen.nipez.cz/{key_id}", "source_id": "cz", "retrieved_at": "2026-10-17T00:00:00+00:00",
            "published_at": TODAY.isoformat(), "source_version": "t", "checksum": key_id, "data_quality": "ok", "buyer": buyer, "cpv": cpv}


LEGAL = tender("D-FUEL", "Dodavka motorove nafty (diesel) pro obecni vozidla", ["09134200"], value=38000.0)
FOOD = tender("D-FOOD", "Potravinove balicky", ["15800000"], value=None, cur=None)
EXPIRED = tender("D-OLD", "Diesel for winter", ["09134200"], days=-2)
NO_DEADLINE = tender("D-NODL", "Diesel without a deadline", ["09134200"], days=None)
AMMO = tender("D-AMMO", "Small arms ammunition 5.56x45", ["35331000"], value=2000000.0, cur="CZK")
RADAR = tender("D-RADAR", "Weather radar apparatus", ["38115000"], value=90000.0)
RU = tender("D-RU", "Diesel supply", ["09134200"], country="RU")
OTHER = tender("D-IT", "Software licences", ["48000000"])
ALL = [LEGAL, FOOD, EXPIRED, NO_DEADLINE, AMMO, RADAR, RU, OTHER]
CTRL = {"name": "Mixed Test s.r.o.", "country": "CZ", "cpv_codes": ["09", "353", "38"]}


def keys(d):
    return [i["tender_key"] for i in d["items"]]


def excl(d):
    return {e["tender_key"]: e["reason"] for e in d["excluded"]}


# ------------------------------------------------------------------ builder
def test_legal_match_has_value_buyer_deadline_why_margin_and_next_steps():
    d = DS.build_dossier(FUEL, ALL, today=TODAY, fx_rates=FX)
    assert keys(d) == ["CZ:D-FUEL", "CZ:D-FOOD"]                 # priced/higher first, then unpriced
    it = d["items"][0]
    assert it["buyer"] == "Mesto Test" and it["deadline"] == LEGAL["deadline"] and it["days_left"] == 30
    assert it["estimated_value"] == "38000.00" and it["currency"] == "EUR" and it["estimated_value_usd"] == "41800.00"
    assert any("09134200" in w for w in it["why"]) and any("diesel" in w for w in it["why"])
    b2b = it["economics"]["back_to_back_supply"]
    assert b2b["margin_pct"] and b2b["gross_profit"] and it["economics"]["agency_introducer"]["commission_base"] == "1140.00"
    assert it["next_steps"] and it["source_url"] in it["next_steps"][0]
    assert it["controlled"] is False and it["flags"] == [] and it["regulatory"]["track"] == "UNCONTROLLED_FAST_START"
    food = d["items"][1]
    assert food["estimated_value"] is None and food["economics"]["priced"] is False and food["estimated_value_usd"] is None
    assert d["delivery"] == "NOT_SENT" and d["footer"] == DS.FOOTER and d["sanctions_screened"] is False


def test_expired_and_no_deadline_and_restricted_country_are_excluded():
    d = DS.build_dossier(FUEL, ALL, today=TODAY, fx_rates=FX)
    ex = excl(d)
    assert ex["CZ:D-OLD"] == "EXPIRED" and ex["CZ:D-NODL"] == "NO_DEADLINE"
    assert ex["RU:D-RU"] in ("COUNTRY_FILTER", "BLOCKED")
    assert "CZ:D-OLD" not in keys(d) and "CZ:D-NODL" not in keys(d) and "RU:D-RU" not in keys(d)
    assert "CZ:D-IT" not in ex and d["summary"]["no_match"] >= 1      # no overlap = not a lead, not an exclusion
    # with no country filter the restricted buyer is still never listed
    d2 = DS.build_dossier({**FUEL, "countries": []}, ALL, today=TODAY, fx_rates=FX)
    assert excl(d2)["RU:D-RU"] == "BLOCKED" and "RU:D-RU" not in keys(d2)


def test_deadline_is_checked_against_the_reference_time_not_the_stored_status():
    d = DS.build_dossier(FUEL, [LEGAL], today=datetime(2026, 11, 17, 0, 0, 1, tzinfo=timezone.utc), fx_rates=FX)
    assert keys(d) == [] and excl(d) == {"CZ:D-FUEL": "EXPIRED"}


def test_controlled_items_excluded_without_compliance_approval():
    d = DS.build_dossier(CTRL, ALL, today=TODAY, fx_rates=FX)
    ex = excl(d)
    assert ex["CZ:D-AMMO"] == "CONTROLLED_NOT_APPROVED" and ex["CZ:D-RADAR"] == "CONTROLLED_NOT_APPROVED"
    assert all(not i["controlled"] for i in d["items"]) and "CZ:D-FUEL" in keys(d)
    assert d["parameters"]["fast_start_only"] is True
    assert "ammunition" not in DS.dossier_markdown(d).lower()


def test_approved_controlled_items_are_included_and_flagged():
    d = DS.build_dossier(CTRL, ALL, today=TODAY, fx_rates=FX, approved="compliance")
    by = {i["tender_key"]: i for i in d["items"]}
    assert by["CZ:D-AMMO"]["controlled"] and by["CZ:D-AMMO"]["regulatory"]["track"] == "MILITARY_MPO_LICENCE"
    assert by["CZ:D-RADAR"]["regulatory"]["track"] == "DUAL_USE_2021_821"
    for k in ("CZ:D-AMMO", "CZ:D-RADAR"):
        assert by[k]["flags"] == [DS.CONTROLLED_FLAG] and any("licence" in s for s in by[k]["next_steps"])
    assert keys(d)[0] == "CZ:D-FUEL"                                 # uncontrolled leads stay on top
    assert excl(d).get("RU:D-RU") == "BLOCKED"                       # approval never lifts a block
    md = DS.dossier_markdown(d)
    assert DS.CONTROLLED_FLAG in md and "Compliance approval" in md


def test_bad_approval_value_is_refused():
    with pytest.raises(DS.DossierError) as e:
        DS.build_dossier(CTRL, ALL, today=TODAY, approved="owner-said-ok")
    assert e.value.code == "APPROVAL_INVALID"


@pytest.mark.parametrize("profile", [None, {}, {"name": ""}, {"name": "Acme"}, {"name": "Acme", "cpv_codes": [], "keywords": {"en": []}},
                                     {"name": " ", "cpv_codes": ["09"]}])
def test_empty_profile_is_refused(profile):
    with pytest.raises(DS.DossierError) as e:
        DS.build_dossier(profile, ALL, today=TODAY)
    assert e.value.code == "PROFILE_EMPTY"


def test_invalid_profile_fields_are_refused():
    for bad in ({"name": "Acme", "cpv_codes": ["abc"]}, {"name": "Acme", "cpv_codes": ["09"], "countries": ["Atlantis"]},
                {"name": "Acme", "cpv_codes": ["09"], "capacity": {"max_contract_value": -1, "currency": "EUR"}}):
        with pytest.raises(DS.DossierError) as e:
            DS.build_dossier(bad, ALL, today=TODAY)
        assert e.value.code == "PROFILE_INVALID"


def test_capacity_fit_and_missing_fx_are_honest():
    big = tender("D-BIG", "Diesel framework", ["09134200"], value=900000.0)
    uah = tender("D-UAH", "Diesel UA", ["09134200"], value=500000.0, cur="UAH", country="UA")
    prof = {**FUEL, "capacity": {"max_contract_value": 100000, "currency": "EUR"}}
    d = DS.build_dossier(prof, [LEGAL, big, uah], today=TODAY, fx_rates=FX)
    by = {i["tender_key"]: i for i in d["items"]}
    assert by["CZ:D-FUEL"]["capacity_fit"]["status"] == "WITHIN" and by["CZ:D-BIG"]["capacity_fit"]["status"] == "EXCEEDS"
    assert by["UA:D-UAH"]["capacity_fit"]["status"] == "FX_RATE_MISSING"
    assert by["UA:D-UAH"]["estimated_value_usd"] is None and by["UA:D-UAH"]["fx_status"] == "FX_RATE_MISSING"
    assert d["summary"]["fx_missing_listed"] == 1


def test_summarizer_hook_default_none_and_pluggable():
    d = DS.build_dossier(FUEL, [LEGAL], today=TODAY, fx_rates=FX)
    assert d["items"][0]["summary"] is None and d["items"][0]["summary_status"] == "NO_HOOK"
    seen = []
    d2 = DS.build_dossier(FUEL, [LEGAL], today=TODAY, fx_rates=FX, summarizer=lambda b: seen.append(b) or "Diesel for a town fleet.")
    assert d2["items"][0]["summary"] == "Diesel for a town fleet." and seen[0]["title"] == LEGAL["title"]

    def boom(_):
        raise RuntimeError("model down")
    d3 = DS.build_dossier(FUEL, [LEGAL], today=TODAY, fx_rates=FX, summarizer=boom)
    assert d3["items"][0]["summary"] is None and d3["items"][0]["summary_status"] == "HOOK_ERROR:RuntimeError"


def test_markdown_and_json_sibling():
    d = DS.build_dossier(FUEL, ALL, today=TODAY, fx_rates=FX)
    md = DS.dossier_markdown(d)
    assert md.startswith("# Open tender leads for Palivo Test s.r.o. (2026-10-17)")
    assert "Mesto Test" in md and "38000.00 EUR" in md and "Why it matches" in md and "Next steps" in md
    assert md.rstrip().endswith(f"_{DS.FOOTER}_")
    assert "Sanctions:** lists not loaded" in md
    assert json.loads(DS.dossier_json(d))["items"][0]["tender_key"] == "CZ:D-FUEL"


def test_order_is_deterministic_regardless_of_input_order():
    a = tender("D-A", "Diesel A", ["09134200"], value=10000.0)
    b = tender("D-B", "Diesel B", ["09134200"], value=10000.0)
    c = tender("D-C", "Diesel C", ["09134200"], value=70000.0)
    first = keys(DS.build_dossier(FUEL, [a, b, c], today=TODAY, fx_rates=FX))
    assert first == keys(DS.build_dossier(FUEL, [c, b, a], today=TODAY, fx_rates=FX)) == ["CZ:D-C", "CZ:D-A", "CZ:D-B"]


def test_without_fx_higher_native_value_first_within_one_currency():
    small = tender("D-S", "Diesel S", ["09134200"], value=155000.0, cur="UAH", country="UA")
    big = tender("D-L", "Diesel L", ["09134200"], value=2349788.58, cur="UAH", country="UA")
    d = DS.build_dossier(FUEL, [small, big], today=TODAY, fx_rates=FX)
    assert keys(d) == ["UA:D-L", "UA:D-S"] and all(i["estimated_value_usd"] is None for i in d["items"])
    assert "no USD total" in DS.dossier_markdown(d) and "~0.00 USD" not in DS.dossier_markdown(d)
    assert d["items"][0]["why"] == ["tender CPV 09134200 is inside your CPV 09*", 'keyword "diesel" found in the tender text: "diesel"']
    assert d["items"][0]["why_ru"]


def test_real_prozorro_fixture_fast_start_only():
    docs = pz.normalize_all(FIX["details"], "2026-10-21T20:00:00+00:00", "snap", date(2026, 10, 21))
    ref = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)
    prof = {"name": "Fuel Food UA Test", "cpv_codes": ["09", "15", "353", "347"], "countries": ["UA"]}
    d = DS.build_dossier(prof, docs, today=ref)
    tracks = {i["regulatory"]["track"] for i in d["items"]}
    assert tracks == {"UNCONTROLLED_FAST_START"} and len(d["items"]) == 2
    assert d["summary"]["excluded"]["CONTROLLED_NOT_APPROVED"] == 2      # magazines (353) + UAV
    later = DS.build_dossier(prof, docs, today=datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc))
    assert "UA:UA-2026-10-21-009863-a" in {e["tender_key"] for e in later["excluded"] if e["reason"] == "EXPIRED"}


# ------------------------------------------------------------------ API
BODY = {"supplier": {"name": "Palivo Test s.r.o.", "country": "CZ", "cpv_codes": ["09", "353"], "keywords": {"en": ["diesel"]}}}


def _seed(env):
    for t in (LEGAL, EXPIRED, AMMO):
        env.store.upsert_tender(t)


def test_api_dossier_builds_for_analyst_and_excludes_controlled(env):
    _seed(env)
    r = env.req("POST", "/monetization/dossier", "analyst", json=BODY)
    assert r.status_code == 200, r.text
    d = r.json()["dossier"]
    assert keys(d) == ["CZ:D-FUEL"]
    assert excl(d) == {"CZ:D-OLD": "EXPIRED", "CZ:D-AMMO": "CONTROLLED_NOT_APPROVED"}
    assert r.json()["markdown"].rstrip().endswith(f"_{DS.FOOTER}_")
    ev = [e for e in env.store.audit_events(50) if e["event"] == "monetization.dossier"]
    assert ev and "Palivo" not in json.dumps(ev, ensure_ascii=False)


def test_api_dossier_markdown_format(env):
    _seed(env)
    r = env.req("POST", "/monetization/dossier", "analyst", json={**BODY, "format": "markdown"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert "# Open tender leads for Palivo Test s.r.o." in r.text


def test_api_dossier_controlled_needs_compliance_role(env):
    _seed(env)
    body = {**BODY, "approved": "compliance"}
    assert env.req("POST", "/monetization/dossier", "analyst", json=body).status_code == 403
    r = env.req("POST", "/monetization/dossier", "compliance", json=body)
    assert r.status_code == 200, r.text
    by = {i["tender_key"]: i for i in r.json()["dossier"]["items"]}
    assert by["CZ:D-AMMO"]["flags"] == [DS.CONTROLLED_FLAG] and by["CZ:D-AMMO"]["controlled"] is True
    assert env.req("POST", "/monetization/dossier", "compliance", json={**BODY, "approved": "yes"}).status_code == 422


def test_api_dossier_refuses_empty_profile_and_viewer(env):
    r = env.req("POST", "/monetization/dossier", "analyst", json={"supplier": {"name": "Acme"}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "PROFILE_EMPTY"
    assert env.req("POST", "/monetization/dossier", "analyst", json={"supplier": {}}).status_code == 422
    assert env.req("POST", "/monetization/dossier", "viewer", json=BODY).status_code == 403
    assert env.req("POST", "/monetization/dossier", None, json=BODY).status_code == 401
