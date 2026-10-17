from conftest import Env, TODAY
from fixtures import ted_notice, ted_payload


def gate(a, gid):
    return next(g for g in a["gates"] if g["id"] == gid)


def test_demo_data_is_always_marked_and_watermarked(env):
    assert env.req("POST", "/ingest", "analyst", json={"source": "demo"}).json()["demo"] is True
    items = env.req("GET", "/tenders", "viewer").json()["items"]
    assert items and all(i["demo"] is True and i["official_notice_id"].startswith("DEMO") for i in items)
    h = env.c.get("/api/v1/health").json()
    assert h["mode"] == "DEMO" and h["watermark"] == "DEMO / NOT LIVE" and h["demo_tenders"] == len(items)


def test_live_disabled_blocks_external_ingest(tmp_path):
    e = Env(tmp_path, live=False)
    r = e.req("POST", "/ingest", "analyst", json={"source": "ted"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "LIVE_DISABLED"


def test_ted_ingest_normalizes_and_a_duplicate_notice_changes_nothing(env):
    first = env.ingest_ted()
    assert first["fetched"] == 6 and first["new"] == 6
    again = env.ingest_ted()
    assert again["new"] == 0 and again["updated"] == 0 and again["duplicates"] == 6 and again["snapshot_new"] is False
    t = env.tender("100001-2026")["tender"]
    assert t["status"] == "open" and t["currency"] == "CZK" and t["estimated_value"] == 250000.0 and t["category"] == "fuel_energy"
    assert t["title"] == "Diesel supply for municipal fleet" and t["source_url"].endswith("/100001-2026") and t["demo"] is False
    assert env.tender("100003-2026")["tender"]["status"] == "closed"          # contract award notice


def test_revised_deadline_creates_a_new_version_and_keeps_history(tmp_path):
    e = Env(tmp_path)
    e.ingest_ted()
    revised = ted_payload()
    revised["notices"][0]["deadline-receipt-tender-date-lot"] = ["2026-12-01+01:00"]
    e2 = Env(tmp_path, ted=revised)                  # same data dir, same db: the notice changed upstream
    out = e2.ingest_ted()
    assert out["updated"] == 1 and out["new"] == 0
    d = e2.tender("100001-2026")
    assert d["versions"] == [1, 2] and d["tender"]["deadline"].startswith("2026-12-01")


def test_expired_or_awarded_notice_is_blocked_and_cannot_be_cleared(env):
    env.ingest_ted()
    a = env.req("POST", "/assessments", "analyst", json={"tender_key": env.key("100003-2026"), "dry_run": False}).json()
    assert gate(a, "deadline")["status"] == "BLOCKED" and a["complexity"] == "BLOCKED"
    env.req("POST", f"/assessments/{a['id']}/review", "compliance", json={"gate_id": "deadline", "decision": "cleared", "note": "please let it pass"})
    again = env.req("POST", "/assessments", "analyst", json={"tender_key": env.key("100003-2026")}).json()
    assert gate(again, "deadline")["status"] == "BLOCKED"


def test_restricted_country_is_a_hard_block(env):
    env.ingest_ted()
    a = env.req("POST", "/assessments", "analyst", json={"tender_key": env.key("100004-2026")}).json()
    assert gate(a, "jurisdiction")["status"] == "BLOCKED" and a["compliance_status"] == "BLOCKED" and a["complexity"] == "BLOCKED"


def test_missing_currency_is_review_required(env):
    env.ingest_ted()
    a = env.req("POST", "/assessments", "analyst", json={"tender_key": env.key("100006-2026")}).json()
    assert gate(a, "currency")["status"] == "REVIEW_REQUIRED"


def test_controlled_goods_never_pass_and_need_a_legal_reference(env):
    env.ingest_ted()
    a = env.req("POST", "/assessments", "analyst", json={"tender_key": env.key("100005-2026"), "dry_run": False}).json()
    assert gate(a, "controlled_goods")["status"] == "REVIEW_REQUIRED"
    no_ref = env.req("POST", f"/assessments/{a['id']}/review", "compliance",
                     json={"gate_id": "controlled_goods", "decision": "cleared", "note": "looks fine to me"})
    assert no_ref.status_code == 422 and no_ref.json()["detail"]["code"] == "LEGAL_REFERENCE_REQUIRED"
    ok = env.req("POST", f"/assessments/{a['id']}/review", "compliance",
                 json={"gate_id": "controlled_goods", "decision": "cleared", "note": "counsel opinion attached", "legal_reference": "Opinion 2026/17"})
    assert ok.status_code == 200


def test_unknown_facts_are_review_required_never_pass(env):
    env.ingest_ted()
    a = env.req("POST", "/assessments", "analyst", json={"tender_key": env.key("100001-2026")}).json()
    assert gate(a, "eligibility")["status"] == "REVIEW_REQUIRED"          # no company profile yet
    assert gate(a, "documents")["status"] == "REVIEW_REQUIRED"            # attachments are not analysed
    assert gate(a, "counterparty_screening")["status"] == "REVIEW_REQUIRED"   # sanctions list NOT_CONNECTED
    assert a["compliance_status"] == "REVIEW_REQUIRED" and a["complexity"] == "REVIEW_REQUIRED"
    assert "не вероятность" in a["fit"]["disclaimer"] or "НЕ вероятность" in a["fit"]["disclaimer"]


def test_company_without_registration_or_sam_uei_lacks_eligibility(env):
    env.ingest_ted()
    ap = env.req("PATCH", "/company/profile", "analyst", json={"changes": {"country": "CZ", "registered": False}}).json()["approval_id"]
    env.req("POST", f"/approvals/{ap}/decide", "owner", json={"approve": True})
    a = env.req("POST", "/assessments", "analyst", json={"tender_key": env.key("100001-2026")}).json()
    assert gate(a, "eligibility")["status"] == "REVIEW_REQUIRED" and "не зарегистрирована" in gate(a, "eligibility")["reason"]


def test_signed_reviews_clear_open_gates_and_unlock_a_tier(env):
    env.ingest_ted()
    for changes in ({"country": "CZ", "registered": True, "cpv_capabilities": ["09", "15", "60"], "capital_limit_usd": 50000},):
        ap = env.req("PATCH", "/company/profile", "analyst", json={"changes": changes}).json()["approval_id"]
        env.req("POST", f"/approvals/{ap}/decide", "owner", json={"approve": True})
    env.req("PUT", "/fx", "analyst", json={"rates": {"CZKUSD": 0.043}, "as_of": "2026-10-16", "source": "manual entry from bank rate"})
    env.req("POST", "/sanctions/import", "compliance", json={"entries": ["Totally Other Person"], "source": "EU consolidated (test fixture)", "list_version": "t1", "as_of": "2026-10-16"})
    env.req("POST", "/suppliers", "analyst", json={"name": "Fuel Supplier sro", "country": "CZ", "cpv_prefixes": ["09"], "confirmed": True})
    a = env.req("POST", "/assessments", "analyst", json={"tender_key": env.key("100001-2026"), "dry_run": False}).json()
    assert gate(a, "counterparty_screening")["status"] == "PASS" and a["complexity"] == "REVIEW_REQUIRED" and a["tier_if_cleared"]
    r = env.req("POST", f"/assessments/{a['id']}/review", "compliance", json={"gate_id": "documents", "decision": "cleared", "note": "documentation read, no special certificates"})
    assert r.status_code == 200
    final = env.req("POST", "/assessments", "analyst", json={"tender_key": env.key("100001-2026")}).json()
    assert final["compliance_status"] == "PASS" and final["complexity"] in ("EASY", "MEDIUM", "HARD")
    assert "снято проверкой" in gate(final, "documents")["reason"]


def test_sanction_exact_match_blocks_partial_goes_to_human(env):
    env.ingest_ted()
    env.req("POST", "/sanctions/import", "compliance", json={"entries": ["Mesto Brno"], "source": "fixture list", "list_version": "t", "as_of": "2026-10-16"})
    a = env.req("POST", "/assessments", "analyst", json={"tender_key": env.key("100001-2026")}).json()
    assert gate(a, "counterparty_screening")["status"] == "BLOCKED"
    env.req("POST", "/sanctions/import", "compliance", json={"entries": ["Mesto Brno Logistics"], "source": "fixture list", "list_version": "t2", "as_of": "2026-10-16"})
    b = env.req("POST", "/assessments", "analyst", json={"tender_key": env.key("100001-2026")}).json()
    assert gate(b, "counterparty_screening")["status"] == "REVIEW_REQUIRED"         # possible false positive: human decides


def test_two_currencies_need_an_explicit_rate(env):
    env.ingest_ted()
    a = env.req("POST", "/assessments", "analyst", json={"tender_key": env.key("100001-2026")}).json()
    assert a["usd_value"] is None and "capital" in a["fit"]["unknowns"]              # CZK without a stored rate: unknown, not 1:1
    env.req("PUT", "/fx", "analyst", json={"rates": {"CZKUSD": 0.04}, "as_of": "2026-10-16", "source": "manual"})
    b = env.req("POST", "/assessments", "analyst", json={"tender_key": env.key("100001-2026")}).json()
    assert b["usd_value"] == "10000.00" or b["usd_value"].startswith("10000")        # 250000 CZK * 0.04


def test_capital_limit_and_matched_deal(env):
    env.ingest_ted()
    env.req("PUT", "/fx", "analyst", json={"rates": {"CZKUSD": 0.04}, "as_of": "2026-10-16", "source": "manual"})
    env.store.upsert_tender({"jurisdiction": "EU", "official_notice_id": "BIG-1", "title": "Big fuel contract", "country": "CZE", "category": "fuel_energy",
                             "status": "open", "deadline": "2026-12-01T23:59:59Z", "estimated_value": 1000000, "currency": "USD", "source_url": "https://example.org/x",
                             "retrieved_at": "2026-10-17T00:00:00+00:00", "cpv": ["09134200"], "demo": False, "buyer": "Mesto"})
    base = env.req("POST", "/assessments", "analyst", json={"tender_key": "EU:BIG-1"}).json()
    assert base["fit"]["components"]["capital"]["value"] == 0.0
    # supplier cannot certify -> not matched -> capital stays binding
    d1 = env.req("POST", "/deals", "analyst", json={"title": "x", "tender_key": "EU:BIG-1", "buyer_country": "GN", "goods": "fuel", "order_confirmed": True,
                                                    "supplier": {"name": "Maybe Supplier", "country": "CZ", "confirmed": False}, "supplier_contract_signed": True}).json()
    assert d1["matched"] is False
    env.req("POST", "/sanctions/import", "compliance", json={"entries": ["Unrelated Name"], "source": "fixture list", "list_version": "t", "as_of": "2026-10-16"})
    d2 = env.req("POST", "/deals", "analyst", json={"title": "Guinea order + Czech supplier", "tender_key": "EU:BIG-1", "buyer_country": "GN", "goods": "fuel",
                                                    "order_confirmed": True, "supplier": {"name": "Real Supplier sro", "country": "CZ", "confirmed": True},
                                                    "supplier_contract_signed": True}).json()
    assert d2["matched"] is True
    matched = env.req("POST", "/assessments", "analyst", json={"tender_key": "EU:BIG-1"}).json()
    assert matched["fit"]["components"]["capital"]["value"] == 0.9 and matched["back_to_back"] is True
    ap = env.req("POST", f"/deals/{d2['id']}/request-approval", "analyst").json()
    assert ap["status"] == "approval_required"
    assert env.req("POST", f"/deals/{d1['id']}/request-approval", "analyst").status_code == 409


def test_restricted_supplier_country_is_rejected(env):
    r = env.req("POST", "/suppliers", "analyst", json={"name": "Some Trader", "country": "IR", "cpv_prefixes": ["09"]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "SUPPLIER_JURISDICTION_BLOCKED"


def test_tenders_filters_and_demo_exclusion(env):
    env.ingest_ted()
    env.req("POST", "/ingest", "analyst", json={"source": "demo"})
    allr = env.req("GET", "/tenders", "viewer").json()
    real = env.req("GET", "/tenders?include_demo=false", "viewer").json()
    assert real["total"] == 6 and allr["total"] > 6 and all(not i["demo"] for i in real["items"])
    blocked = env.req("GET", "/tenders?complexity=BLOCKED&include_demo=false", "viewer").json()
    assert {i["official_notice_id"] for i in blocked["items"]} >= {"100003-2026", "100004-2026"}
    assert env.req("GET", "/tenders?country=CZ&category=fuel_energy&include_demo=false", "viewer").json()["total"] >= 1


def test_tender_docs_validate_against_the_spec_schema(env):
    import json
    import pathlib

    import jsonschema
    schema = json.loads((pathlib.Path(__file__).resolve().parents[1] / "schemas" / "tender.schema.json").read_text(encoding="utf-8"))
    env.ingest_ted()
    env.req("POST", "/ingest", "analyst", json={"source": "demo"})
    for t in env.store.latest_tenders():
        jsonschema.validate(t, schema, format_checker=jsonschema.FormatChecker())


def test_ted_placeholder_values_and_majority_category():
    from warterminal.connectors import ted as tedc
    n = ted_notice("200001-2026", "Glassware", cpv=("15000000", "33790000", "33790000", "33790000"), value=("0",), cur=("EUR",))
    d = tedc.normalize(n, "2026-10-17T00:00:00+00:00", "x", TODAY)
    assert d["estimated_value"] is None and d["data_quality"] == "partial"      # 0 EUR is "not stated", not a price
    assert d["category"] == "medical"                                           # 3 of 4 codes, not the first code
    one = tedc.normalize(ted_notice("200002-2026", "x", value=("1",), cur=("EUR",)), "t", "x", TODAY)
    assert one["estimated_value"] is None


def test_open_tenders_are_listed_before_closed(env):
    env.ingest_ted()
    items = env.req("GET", "/tenders?include_demo=false", "viewer").json()["items"]
    statuses = [i["status"] for i in items]
    assert statuses.index("closed") > max(i for i, s in enumerate(statuses) if s == "open")


def test_qualification_uses_share_of_cpv_not_any_single_code():
    from decimal import Decimal
    from warterminal import scoring
    prof = {"cpv_capabilities": ["09", "15"]}
    lab = {"cpv": ["15000000", "33790000", "33791000", "33792000", "33793000"]}            # 1 of 5 codes is food: subject is lab glassware
    fuel = {"cpv": ["09134200", "09134100"]}
    q = lambda t: scoring.components(t, prof, [], [], capital_limit_usd=Decimal(50000), back_to_back=False, usd_value=None, today=TODAY)["qualification"][0]
    assert q(fuel) == 1.0 and q(lab) == 0.4 and q({"cpv": ["33790000"]}) == 0.1
