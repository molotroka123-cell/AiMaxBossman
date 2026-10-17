import json

from conftest import Env, H


def test_roles_are_enforced(env):
    assert env.req("GET", "/tenders", None).status_code == 401
    assert env.req("POST", "/ingest", "viewer", json={"source": "demo"}).status_code == 403
    env.ingest_ted()
    a = env.req("POST", "/assessments", "analyst", json={"tender_key": "EU:100001-2026", "dry_run": False}).json()
    assert env.req("POST", f"/assessments/{a['id']}/review", "analyst", json={"gate_id": "documents", "decision": "cleared", "note": "analyst cannot sign"}).status_code == 403
    assert env.req("GET", "/audit/events", "analyst").status_code == 403
    assert env.req("GET", "/learning/dataset", "compliance").status_code == 403


def test_profile_change_waits_for_owner_approval_and_unknown_fields_are_refused(env):
    pending = env.req("PATCH", "/company/profile", "analyst", json={"changes": {"name": "Future Co s.r.o.", "country": "CZ"}}).json()
    assert pending["status"] == "approval_required"
    assert env.req("GET", "/company/profile", "viewer").json()["profile"] == {}
    assert env.req("POST", f"/approvals/{pending['approval_id']}/decide", "compliance", json={"approve": True}).status_code == 403
    env.req("POST", f"/approvals/{pending['approval_id']}/decide", "owner", json={"approve": True})
    assert env.req("GET", "/company/profile", "viewer").json()["profile"]["country"] == "CZ"
    rej = env.req("PATCH", "/company/profile", "analyst", json={"changes": {"name": "Evil"}}).json()["approval_id"]
    env.req("POST", f"/approvals/{rej}/decide", "owner", json={"approve": False, "note": "no"})
    assert env.req("GET", "/company/profile", "viewer").json()["profile"]["name"] == "Future Co s.r.o."
    assert env.req("PATCH", "/company/profile", "analyst", json={"changes": {"api_key": "x"}}).status_code == 422
    assert env.req("POST", f"/approvals/{rej}/decide", "owner", json={"approve": True}).status_code == 404       # already decided


def test_bid_contact_and_purchase_actions_are_never_available(env):
    for name in ("submit-bid", "contact-counterparty", "purchase-controlled-goods"):
        r = env.req("POST", f"/actions/{name}", "owner")
        assert r.status_code == 403 and r.json()["detail"]["code"] == "NOT_IMPLEMENTED_REQUIRES_LEGAL_REVIEW"
    events = [e["event"] for e in env.req("GET", "/audit/events", "compliance").json()["items"]]
    assert events.count("action.refused") == 3


def test_audit_chain_detects_tampering(env):
    env.ingest_ted()
    env.req("PUT", "/fx", "analyst", json={"rates": {"EURUSD": 1.1}, "as_of": "2026-10-16", "source": "manual"})
    assert env.req("GET", "/audit/verify", "compliance").json()["ok"] is True
    env.store.db.execute("UPDATE audit SET payload='{\"fetched\": 0}' WHERE seq=1")
    env.store.db.commit()
    bad = env.req("GET", "/audit/verify", "compliance").json()
    assert bad["ok"] is False and bad["broken_at"] == 1
    assert env.c.get("/api/v1/health").json()["audit_chain_ok"] is False


def test_sources_are_honest_about_status(tmp_path):
    off = Env(tmp_path / "off", live=False)
    rows = {s["id"]: s for s in off.req("GET", "/sources/status", "viewer").json()["sources"]}
    assert rows["ted"]["status"] == "DISABLED" and rows["sam"]["status"] == "NOT_CONNECTED" and rows["ucdp"]["status"] == "NOT_CONNECTED"
    on = Env(tmp_path / "on", live=True)
    assert {s["id"]: s for s in on.req("GET", "/sources/status", "viewer").json()["sources"]}["ted"]["status"] == "NOT_RUN"
    on.ingest_ted()
    st = {s["id"]: s for s in on.req("GET", "/sources/status", "viewer").json()["sources"]}
    assert st["ted"]["status"] == "LIVE" and st["ted"]["last_count"] == 6 and st["cz_nen"]["status"] == "NOT_CONNECTED"
    h = on.c.get("/api/v1/health").json()
    assert h["mode"] == "LIVE" and h["watermark"] is None


def test_conflicts_never_pretend_to_be_real(env):
    empty = env.req("GET", "/conflicts?include_demo=false", "viewer").json()
    assert empty["status"] == "NOT_CONNECTED" and empty["items"] == [] and "тактическ" in empty["safety"]
    env.req("POST", "/ingest", "analyst", json={"source": "demo"})
    demo = env.req("GET", "/conflicts", "viewer").json()
    assert demo["real_count"] == 0 and demo["items"] and all(c["demo"] for c in demo["items"])
    bad = env.req("POST", "/conflicts/import", "compliance", json={"items": [{"name": "x", "country": "XX", "lat": 1, "lon": 1, "severity": "high",
                                                                              "source_id": "ucdp", "source_url": "not-a-url", "licence": "CC"}]})
    assert bad.status_code == 422                                  # provenance is mandatory
    ok = env.req("POST", "/conflicts/import", "compliance", json={"items": [{"name": "Region R", "country": "XX", "lat": 1, "lon": 1, "severity": "high",
                                                                             "source_id": "ucdp", "source_url": "https://ucdp.uu.se/", "licence": "CC BY 4.0"}]})
    assert ok.status_code == 200 and env.req("GET", "/conflicts?include_demo=false", "viewer").json()["status"] == "OK"


def test_usaspending_and_gdelt_are_stored_with_snapshots(env):
    a = env.req("POST", "/ingest", "analyst", json={"source": "usaspending", "keyword": "fuel"}).json()
    g = env.req("POST", "/ingest", "analyst", json={"source": "gdelt", "keyword": "Guinea fuel"}).json()
    assert a["fetched"] == 1 and len(a["snapshot_sha256"]) == 64 and g["fetched"] == 1
    assert env.store.kv_get("awards:fuel")[0]["recipient"] == "ACME FUEL LLC"
    news = env.req("GET", "/conflicts/news?query=Guinea fuel", "viewer").json()
    assert news["status"] == "OK" and "не подтверждённые" in news["disclaimer"]
    assert env.req("GET", "/conflicts/news?query=never loaded", "viewer").json()["status"] == "NOT_RUN"
    assert env.req("POST", "/ingest", "analyst", json={"source": "sam"}).status_code == 409           # no key: NOT_CONNECTED


def test_checklist_is_a_read_only_draft(env):
    env.ingest_ted()
    c = env.req("GET", "/tenders/EU:100001-2026/checklist", "viewer").json()
    assert c["draft"] is True and any(i["status"] == "REQUIRED" for i in c["items"]) and any("documents" in i["step"] for i in c["items"])
