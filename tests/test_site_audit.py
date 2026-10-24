"""Scenario D site audit: real findings on a bad local site, nothing fabricated on good/clean ones.

All pages are local fixtures served on 127.0.0.1; no model is called (synthetic clinic copy is a cached
fixture) and nothing is delivered anywhere.
"""
from __future__ import annotations

import functools
import http.server
import importlib
import inspect
import json
import re
import sqlite3
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from tools.site_audit import audit as A  # noqa: E402
from tools.site_audit import crm, emulator, offer  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "site_audit"
YEAR = 2026


@pytest.fixture(scope="module")
def site():
    seen: list[str] = []

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send_head(self):
            seen.append(self.path)
            return super().send_head()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(FIX)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", seen
    srv.shutdown()


@pytest.fixture(scope="module")
def bad(site, tmp_path_factory):
    base, _ = site
    return A.audit_url(base + "/bad/index.html", tmp_path_factory.mktemp("shots_bad"),
                       current_year=YEAR, polite_delay_s=0)


@pytest.fixture(scope="module")
def good(site, tmp_path_factory):
    base, _ = site
    return A.audit_url(base + "/good/index.html", tmp_path_factory.mktemp("shots_good"),
                       current_year=YEAR, polite_delay_s=0)


def _by_id(res):
    return {f.id: f for f in res.findings}


# ------------------------------------------------------------------ legal case

def test_bad_site_real_problems_found_with_measured_evidence(bad):
    f = _by_id(bad)
    expected = {"no_viewport_meta", "mobile_horizontal_overflow", "broken_links", "no_visible_cta",
                "outdated_copyright", "images_without_alt", "missing_lang", "missing_h1",
                "missing_meta_description", "console_errors"}
    assert expected <= set(f), sorted(f)
    assert f["mobile_horizontal_overflow"].evidence == {"viewport_width_px": 390, "content_width_px": 1200}
    broken = f["broken_links"].evidence["broken"]
    assert [b["status"] for b in broken] == [404] and broken[0]["url"].endswith("/bad/cenik.html")
    assert f["outdated_copyright"].evidence["year"] == 2017
    assert f["images_without_alt"].evidence["images_without_alt"] == 2  # alt="" is valid, not counted
    assert f["no_visible_cta"].evidence["tel_links"] == 0
    assert "legacyCounterInit" in f["console_errors"].evidence["samples"][0]
    assert all(s["bytes"] > 1000 and Path(s["path"]).is_file() for s in bad.screenshots)
    assert len(bad.screenshots) == 2
    assert {"check": "https", "reason": "loopback test host – not applicable"} in bad.skipped_checks


def test_good_site_produces_no_fabricated_findings(good, site):
    _, seen = site
    assert seen.count("/good/index.html") == 1  # HTML downloaded once; mobile passes replay it
    assert good.status == "ok"
    assert good.findings == [], [f.id for f in good.findings]
    assert good.signals["cta"]["tel_links"] == 1 and good.signals["cta"]["forms"] == 1


# ------------------------------------------------------------------ negative controls

@pytest.mark.parametrize("kwargs", [
    {"id": "x", "severity": "high", "evidence": {}, "fix_suggestion": "fix"},
    {"id": "x", "severity": "high", "evidence": {"value": ""}, "fix_suggestion": "fix"},
    {"id": "x", "severity": "high", "evidence": None, "fix_suggestion": "fix"},
    {"id": "x", "severity": "critical", "evidence": {"v": 1}, "fix_suggestion": "fix"},
    {"id": "x", "severity": "low", "evidence": {"v": 1}, "fix_suggestion": "  "},
])
def test_finding_without_evidence_is_rejected(kwargs):
    with pytest.raises(A.EvidenceError):
        A.Finding(**kwargs)
    with pytest.raises(A.EvidenceError):
        A.validate_findings([kwargs])


def test_offer_card_rejects_unevidenced_finding_from_outside():
    audit = {"url": "http://x", "signals": {}, "findings": [
        {"id": "made_up_problem", "severity": "high", "evidence": {}, "fix_suggestion": "trust me"}]}
    with pytest.raises(A.EvidenceError):
        offer.build_offer_card(audit, "X")


def test_robots_disallow_respected(site, tmp_path):
    base, seen = site
    res = A.audit_url(base + "/private/index.html", tmp_path, current_year=YEAR, polite_delay_s=0)
    assert res.status == "skipped_robots" and res.findings == [] and res.screenshots == []
    assert not any(p.startswith("/private/") for p in seen), seen


def test_robots_disallowed_link_is_not_checked(bad, site):
    _, seen = site
    assert any(u.endswith("/private/index.html") for u in bad.metrics["links"]["skipped_by_robots"])
    assert not any(p.startswith("/private/") for p in seen)


def test_discount_above_limit_refused():
    assert offer.apply_discount(14900, offer.MAX_DISCOUNT_PCT) == 13410
    for bad_pct in (offer.MAX_DISCOUNT_PCT + 0.5, 50, -1):
        with pytest.raises(offer.DiscountRefused):
            offer.apply_discount(24900, bad_pct)


def test_discount_limit_enforced_in_card(bad):
    with pytest.raises(offer.DiscountRefused):
        offer.build_offer_card(bad.to_dict(), "Truhlarstvi Fiktivni", discount_pct=15)


def test_no_findings_means_no_offer(good):
    with pytest.raises(offer.NothingRealToOffer):
        offer.build_offer_card(good.to_dict(), "Kavarna Testovaci")


def test_package_has_no_delivery_capability():
    pkg = Path(A.__file__).parent
    for py in pkg.glob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert not re.search(r"smtplib|sendmail|import email\b|from email\b|def send|\.send_message", src), py.name
        mod = importlib.import_module(f"tools.site_audit.{py.stem}") if py.stem != "__init__" else None
        if mod is None:
            continue
        for name, obj in inspect.getmembers(mod):
            if callable(obj):
                assert not re.search(r"send|smtp|mail|outreach|deliver", name, re.I), f"{py.stem}.{name}"


def test_offer_card_is_internal_and_has_no_outreach_text(bad, tmp_path):
    card = offer.build_offer_card(bad.to_dict(), "Truhlarstvi Fiktivni")
    assert card["outreach"] is None and "no outreach" in card["status"]
    pj, pm = offer.write_offer_card(card, tmp_path)
    md = pm.read_text(encoding="utf-8")
    assert "Dobrý den" not in md and "S pozdravem" not in md
    saved = json.loads(pj.read_text(encoding="utf-8"))
    assert saved["tier"] == "landing" and saved["final_price_czk"] == 14900
    assert len(saved["top_findings"]) == 3
    assert all(f["severity"] == "high" for f in saved["top_findings"])
    assert all(f["evidence"] for f in saved["top_findings"])


# ------------------------------------------------------------------ tier rules

@pytest.mark.parametrize("signals,tier", [
    ({"internal_pages": 3}, "landing"),
    ({"internal_pages": 5}, "landing"),
    ({"internal_pages": 6}, "business"),
    ({"internal_pages": 2, "eshop_keywords": ["košík"]}, "business"),
    ({"internal_pages": 9, "clinic_scale_keywords": ["klinik"]}, "premium"),
    ({"internal_pages": 1, "language_versions": ["cs", "en"]}, "premium"),
    ({"internal_pages": 1, "language_versions": ["cs"]}, "landing"),
    ({"internal_pages": 1, "foreign_audience_keywords": ["zahraničn"]}, "premium"),
])
def test_tier_rules(signals, tier):
    got, reasons = offer.choose_tier(signals)
    assert got == tier and reasons
    assert offer.TIERS[got]["price_czk"] == {"landing": 14900, "business": 24900, "premium": 34900}[tier]


# ------------------------------------------------------------------ crm

def test_crm_roundtrip_and_score(bad, tmp_path):
    con = crm.connect(tmp_path)
    lid = crm.upsert_lead(con, company_name="Fiktivni", website="http://127.0.0.1/bad", source="fixture",
                          category="truhlář", city="Brno")
    assert crm.upsert_lead(con, company_name="Fiktivni 2", website="http://127.0.0.1/bad", source="fixture") == lid
    crm.record_audit(con, lid, bad.to_dict(), tier="landing")
    row = con.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
    assert row["status"] == "audited" and row["score"] == 100
    assert "broken_links" in json.loads(row["detected_problems"])
    assert crm.lead_score([{"severity": "low"}, {"severity": "medium"}]) == 20
    with pytest.raises(sqlite3.IntegrityError):  # there is no 'sent' state
        con.execute("INSERT INTO messages(lead_id, kind, status, created_at) VALUES (?,?,?,?)",
                    (lid, "offer_card", "sent", "now"))
    con.close()


# ------------------------------------------------------------------ emulator

def test_texts_fixture_is_synthetic_with_provenance():
    data = json.loads(emulator.TEXTS_FIXTURE.read_text(encoding="utf-8"))
    assert data["SYNTHETIC"] is True
    blob = json.dumps(data, ensure_ascii=False)
    assert not re.search(r"@|\+420|https?://|MUDr\.", blob)
    for b in data["batches"]:
        assert b["provider"]
    texts, providers = emulator.load_texts()
    assert len(texts) >= 4 and len(providers) == len(texts)


def test_scrub_removes_signals_the_emulator_controls():
    out = emulator._scrub("Dobrá péče. Objednat se můžete online přes rezervaci. Jsme klinika. Od roku 1999. Konec.")
    assert out == "Dobrá péče. Konec."


def test_scorer_counts_fabrication_and_misses():
    rows = [
        {"site": "a", "defects": ["broken_links"], "expected": ["broken_links"], "allowed": ["broken_links"],
         "found": ["broken_links", "slow_load"], "tier": "landing", "expected_tier": "landing"},
        {"site": "b", "defects": [], "expected": [], "allowed": [], "found": ["missing_h1"],
         "tier": "premium", "expected_tier": "landing"},
        {"site": "c", "defects": ["missing_h1"], "expected": ["missing_h1"], "allowed": ["missing_h1"],
         "found": [], "tier": "landing", "expected_tier": "landing"},
    ]
    sc = emulator.score(rows)
    assert sc["totals"] == {"tp": 1, "fp": 2, "fn": 1, "precision": 0.333, "recall": 0.5}
    assert sc["clean_control_false_positives"] == 1
    assert {"site": "a", "id": "slow_load"} in sc["fabricated_findings"]
    assert sc["tier_accuracy"] == round(2 / 3, 3)


def test_owner_reaction_rule():
    assert emulator.owner_reaction(2) and not emulator.owner_reaction(1)


def test_emulator_ground_truth_precision_recall(tmp_path):
    sc = emulator.run(tmp_path, year=YEAR)
    assert sc["clean_controls"] == 4
    assert sc["clean_control_false_positives"] == 0
    assert sc["fabricated_findings"] == []
    assert sc["totals"]["recall"] >= 0.9
    assert set(sc["per_type"]) >= set().union(*emulator.EXPECTED.values())
    assert sc["tier_accuracy"] == 1.0
    pipe = sc["simulated_pipeline"]
    assert pipe["SIMULATED"] is True and pipe["real_money"] == 0
    lines = (tmp_path / "site_audit_simulated_ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 16 and all(json.loads(x)["real_money"] == 0 for x in lines)
    con = crm.connect(tmp_path)
    assert con.execute("SELECT COUNT(*) FROM leads WHERE status='simulated'").fetchone()[0] == 16
    con.close()
