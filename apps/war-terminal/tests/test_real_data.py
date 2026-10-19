"""Real data only: live is the default, no demo mode/connectors exist, records without provenance are archived (not destroyed) and never shown,
and an unreachable source is reported per source instead of silently showing old data."""
import importlib
import json
import pathlib

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import Env, H, TODAY, TOK
from fixtures import fake_get, fake_post
from warterminal.api import create_app
from warterminal.provenance import is_real, unverified_reason
from warterminal.store import Store

GOOD = {"id": "ted:1", "official_notice_id": "G-1", "title": "Real notice", "jurisdiction": "EU", "country": "CZ", "category": "food", "status": "open",
        "deadline": "2026-12-01T00:00:00Z", "source_url": "https://ted.europa.eu/en/notice/-/detail/G-1", "source_id": "ted", "retrieved_at": "2026-10-17T00:00:00+00:00",
        "data_quality": "ok", "cpv": ["15000000"], "buyer": "Mesto"}


def raw_insert(store, doc, key=None, version=1):
    key = key or f"{doc['jurisdiction']}:{doc['official_notice_id']}"
    store.db.execute("INSERT INTO tenders(key,version,doc,retrieved_at) VALUES(?,?,?,?)", (key, version, json.dumps(dict(doc, key=key, version=version)), "x"))
    store.db.commit()


BAD = {
    "demo_flag": dict(GOOD, official_notice_id="DEMO-001", demo=True),
    "synthetic_quality": dict(GOOD, official_notice_id="S-1", data_quality="synthetic"),
    "fake_host": dict(GOOD, official_notice_id="F-1", source_url="https://example.invalid/demo/1"),
    "no_https_source_url": dict(GOOD, official_notice_id="H-1", source_url="http://ted.europa.eu/x"),
    "no_fetched_at": {k: v for k, v in dict(GOOD, official_notice_id="T-1").items() if k != "retrieved_at"},
    "no_source": {k: v for k, v in dict(GOOD, official_notice_id="N-1").items() if k not in ("source_id",)},
}


def test_provenance_rules_each_reason_is_detected_and_a_real_record_passes():
    assert is_real(GOOD) and unverified_reason(GOOD) is None
    for reason, doc in BAD.items():
        assert unverified_reason(doc) == reason, reason
    assert unverified_reason(dict(GOOD, source_id="demo")) == "demo_source"


def test_startup_archives_unverified_rows_with_exact_counts_and_hides_them(tmp_path):
    st = Store(tmp_path / "war.db")
    st.upsert_tender(GOOD)
    for doc in BAD.values():
        raw_insert(st, doc)
    raw_insert(st, BAD["demo_flag"], version=2)                                   # a second version of the same demo notice
    st.add_doc("assessments", "EU:DEMO-001", {"demo": True, "fit": {}}, dry_run=False)
    st.kv_set("conflicts_demo", [{"name": "DEMO-ZONE-A", "demo": True}])
    st.kv_set("conflicts", [{"name": "real", "country": "Mali", "source_id": "ucdp", "source_url": "https://ucdp.uu.se/downloads/", "retrieved_at": "2026-10-17T00:00:00+00:00"},
                            {"name": "DEMO-ZONE-B", "demo": True, "source_id": "demo", "source_url": "https://example.invalid/demo"}])
    st.db.commit()
    assert st.count_tenders() == {"rows": 8, "keys": 7, "visible_keys": 1}
    del st

    app = create_app(tmp_path, tokens=TOK, post=fake_post(), get=fake_get, today=TODAY, bossman=False)
    rep = app.state.purge_report
    assert rep["tenders_rows"] == 7 and rep["tender_keys"] == 6 and rep["dependent_docs"] == 1 and rep["conflicts"] == 1 and rep["conflicts_demo_key"] == 1
    assert rep["before"] == {"rows": 8, "keys": 7, "visible_keys": 1} and rep["after"] == {"rows": 1, "keys": 1, "visible_keys": 1}
    assert rep["archived_total"] == 7 + 1 + 1 + 1 and rep["reasons"]["demo_flag"] == 2
    arch = pathlib.Path(rep["archive_file"])
    assert arch.parent.name == "archive" and arch.name.startswith("demo-removed-") and arch.suffix == ".jsonl"
    lines = [json.loads(x) for x in arch.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == rep["archived_total"] and {x["table"] for x in lines} == {"tenders", "assessments", "kv"}
    assert any(x.get("doc", {}).get("official_notice_id") == "DEMO-001" for x in lines)          # archived, not destroyed

    c = TestClient(app)
    items = c.get("/api/v1/tenders", headers=H("viewer")).json()["items"]
    assert [i["official_notice_id"] for i in items] == ["G-1"]
    assert c.get("/api/v1/conflicts", headers=H("viewer")).json()["real_count"] == 1
    h = c.get("/api/v1/health").json()
    assert h["mode"] == "LIVE" and h["excluded_unverified"] == rep["archived_total"]
    ev = app.state.store.audit_events(10)
    assert any(e["event"] == "provenance.archive" for e in ev)
    # idempotent: a second start finds nothing more
    again = create_app(tmp_path, tokens=TOK, post=fake_post(), get=fake_get, today=TODAY, bossman=False).state.purge_report
    assert again["archived_total"] == 0 and again["before"] == again["after"]


def test_write_path_refuses_records_without_provenance(tmp_path):
    st = Store(tmp_path / "w.db")
    for reason, doc in BAD.items():
        with pytest.raises(ValueError, match="UNVERIFIED_RECORD"):
            st.upsert_tender(doc)
    assert st.count_tenders()["rows"] == 0


def test_browser_ingest_rejects_records_without_provenance(env):
    good = dict(GOOD, source="browser", fetched_at="2026-10-17T00:00:00+00:00", source_id="browser_nen")
    r = env.req("POST", "/ingest/browser", "analyst", json={"portal": "nen", "tenders": [good, dict(good, official_notice_id="X", demo=True), dict(good, official_notice_id="Y", source_url="http://x")]})
    assert r.status_code == 200 and (r.json()["new"], r.json()["rejected"]) == (1, 2)


def test_live_is_the_default_and_there_is_no_demo_connector(tmp_path, monkeypatch):
    monkeypatch.delenv("WAR_TERMINAL_LIVE", raising=False)
    app = create_app(tmp_path / "a", tokens=TOK, post=fake_post(), get=fake_get, today=TODAY, bossman=False)
    assert app.state.live is True
    monkeypatch.setenv("WAR_TERMINAL_LIVE", "0")                                  # emergency network kill switch: nothing is substituted
    off = create_app(tmp_path / "b", tokens=TOK, post=fake_post(), get=fake_get, today=TODAY, bossman=False)
    c = TestClient(off)
    assert off.state.live is False and c.get("/api/v1/health").json()["mode"] == "OFFLINE"
    assert c.post("/api/v1/ingest", headers=H("analyst"), json={"source": "ted"}).status_code == 403
    assert c.get("/api/v1/tenders", headers=H("viewer")).json()["items"] == []
    for mod in ("warterminal.connectors.demo", "warterminal.connectors.demo_conflicts"):
        with pytest.raises(ImportError):
            importlib.import_module(mod)
    src = pathlib.Path(__file__).resolve().parents[1] / "warterminal"
    leftovers = [str(p.relative_to(src)) for p in src.rglob("*") if p.suffix in (".py", ".js", ".html") and p.name not in ("provenance.py", "store.py") and "vendor" not in p.parts
                 and ("demo_conn" in p.read_text(encoding="utf-8") or "conflicts_demo" in p.read_text(encoding="utf-8") or "include_demo" in p.read_text(encoding="utf-8"))]
    assert leftovers == []


def test_cli_has_no_demo_switch_and_keeps_live_flag_for_old_launchers(monkeypatch):
    from warterminal import cli
    monkeypatch.delenv("WAR_TERMINAL_LIVE", raising=False)
    seen = {}
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(live=app.state.live, kw=kw))
    monkeypatch.setattr("warterminal.api.create_app", lambda *a, **k: type("A", (), {"state": type("S", (), {"store": type("St", (), {"path": "x/war.db"})(), "live": True})()})())
    assert cli.main(["--port", "8899"]) == 0 and seen["live"] is True and seen["kw"]["host"] == "127.0.0.1"
    assert cli.main(["--port", "8899", "--live"]) == 0                           # old launchers still start


def test_unreachable_source_is_reported_per_source_not_hidden(tmp_path):
    def boom(url, body, **kw):
        raise httpx.ConnectError("no route to ted.europa.eu")
    e = Env(tmp_path)
    e.app.state.store.kv_set("last:ted", {"at": "2026-10-16T10:00:00+00:00", "count": 5})           # an older successful fetch exists
    failing = create_app(tmp_path / "x", live=True, tokens=TOK, post=boom, get=fake_get, today=TODAY, bossman=False)
    c = TestClient(failing)
    failing.state.store.kv_set("last:ted", {"at": "2026-10-16T10:00:00+00:00", "count": 5})
    r = c.post("/api/v1/ingest", headers=H("analyst"), json={"source": "ted"})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "SOURCE_UNREACHABLE" and r.json()["detail"]["source"] == "ted"
    ted = {s["id"]: s for s in c.get("/api/v1/sources/status", headers=H("viewer")).json()["sources"]}["ted"]
    assert ted["status"] == "ERROR" and "ConnectError" in ted["reason"] and ted["last_success"] == "2026-10-16T10:00:00+00:00"      # old data is NOT presented as current
    other = {s["id"]: s for s in c.get("/api/v1/sources/status", headers=H("viewer")).json()["sources"]}["worldbank"]
    assert other["status"] == "NOT_RUN"                                                                                              # other sources are unaffected
    # recovery: the next successful ingest clears the error
    ok = Env(tmp_path / "y")
    ok.app.state.store.kv_set("error:ted", {"at": "2026-10-17T00:00:00+00:00", "message": "ConnectError: x"})
    assert {s["id"]: s for s in ok.req("GET", "/sources/status", "viewer").json()["sources"]}["ted"]["status"] == "ERROR"
    ok.ingest_ted()
    assert {s["id"]: s for s in ok.req("GET", "/sources/status", "viewer").json()["sources"]}["ted"]["status"] == "LIVE"


def test_parse_ocds_release_and_evaluate_oss_candidate():
    from warterminal.connectors.ted import evaluate_oss_candidate, parse_ocds_release

    sample_ocds = {
        "ocid": "ocds-b5fd17-12345",
        "buyer": {"id": "CZ-00001", "name": "Ministerstvo obrany"},
        "tender": {
            "title": "Dodavka polnich kuchyni",
            "description": "Nakup mobilnich kuchyni s prislusenstvim",
            "value": {"amount": 1250000.0, "currency": "EUR"},
            "tenderPeriod": {"endDate": "2026-12-15T12:00:00Z"},
            "procurementMethod": "open",
            "awardCriteria": "priceOnly",
            "items": [
                {"id": "1", "classification": {"scheme": "CPV", "id": "15894000"}},
                {"id": "2", "classification": {"scheme": "CPV", "id": "35000000"}},
            ],
        },
    }
    parsed = parse_ocds_release(sample_ocds, retrieved_at="2026-10-19T00:00:00Z")
    # Baseline flat notice without OCDS nesting recognizes only 5 fields; OCDS parser extracts 11 (2.20x >= 1.5x)
    assert parsed["recognized_fields_count"] == 11
    assert parsed["recognized_fields_count"] >= int(5 * 1.5)
    assert parsed["cpv"] == ["15894000", "35000000"]

    # Step 13.3: OSS candidate gate (G·P·D·S·V >= 9.0, permissive license, 0 CVEs)
    good = evaluate_oss_candidate(
        "ocds-kit",
        license_spdx="Apache-2.0",
        scores_gpdsv={"G": 9.5, "P": 9.2, "D": 9.0, "S": 9.4, "V": 9.1},
        known_cves=0,
    )
    assert good["passed"] is True and good["avg_gpdsv"] >= 9.0

    # Negative controls: AGPL license or CVE or low score rejected
    bad_lic = evaluate_oss_candidate(
        "copyleft-pkg",
        license_spdx="AGPL-3.0",
        scores_gpdsv={"G": 9.5, "P": 9.5, "D": 9.5, "S": 9.5, "V": 9.5},
        known_cves=0,
    )
    assert bad_lic["passed"] is False and any("non_permissive_license" in r for r in bad_lic["reasons"])

    bad_cve = evaluate_oss_candidate(
        "vuln-pkg",
        license_spdx="MIT",
        scores_gpdsv={"G": 9.5, "P": 9.5, "D": 9.5, "S": 9.5, "V": 9.5},
        known_cves=1,
    )
    assert bad_cve["passed"] is False and any("known_cves" in r for r in bad_cve["reasons"])

