"""Proof for scenario D (site audit, dental niche, emulator): writes
docs/audits/2026-10-24-earn-site-audit/receipt.json + a sample internal offer card.

What runs: the two local fixtures (bad/good), robots negative control, the synthetic dental emulator
(16 sites, ground-truth scoring, SIMULATED pipeline), and ONE real public page https://example.com
(reserved test domain: robots.txt + one HTML download, no link crawling beyond it).
Screenshots/CRM/ledger go to a temp data dir outside git; the receipt records paths and sizes only.
No outreach is generated or delivered; real_money is 0.
"""
from __future__ import annotations

import datetime as _dt
import functools
import http.server
import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.site_audit import audit as A  # noqa: E402
from tools.site_audit import emulator, offer  # noqa: E402

OUT = REPO / "docs" / "audits" / "2026-10-24-earn-site-audit"
FIX = REPO / "tests" / "fixtures" / "site_audit"
BAD_EXPECTED = {"no_viewport_meta", "mobile_horizontal_overflow", "broken_links", "no_visible_cta",
                "outdated_copyright", "images_without_alt", "missing_lang", "missing_h1",
                "missing_meta_description", "console_errors"}


def _summ(res: A.AuditResult) -> dict:
    return {"url": res.url, "status": res.status, "http_status": res.http_status,
            "findings": [{"id": f.id, "severity": f.severity, "evidence": f.evidence} for f in res.findings],
            "counts": {s: sum(1 for f in res.findings if f.severity == s) for s in A.SEVERITIES},
            "signals": res.signals, "load_ms": res.metrics.get("load_ms"),
            "transfer_bytes": res.metrics.get("transfer_bytes"), "requests_made_by_auditor": res.requests_made,
            "screenshots": res.screenshots, "skipped_checks": res.skipped_checks}


def main() -> int:
    year = _dt.date.today().year
    data = Path(tempfile.mkdtemp(prefix="bossman_site_audit_proof_"))
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    seen: list[str] = []

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send_head(self):
            seen.append(self.path)
            return super().send_head()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(FIX)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        bad = A.audit_url(base + "/bad/index.html", data / "shots_bad", current_year=year, polite_delay_s=0)
        good = A.audit_url(base + "/good/index.html", data / "shots_good", current_year=year, polite_delay_s=0)
        priv = A.audit_url(base + "/private/index.html", data / "shots_private", current_year=year)
    finally:
        srv.shutdown()

    # ---- negatives
    negatives = {}
    try:
        A.Finding("x", "high", {}, "fix")
        negatives["finding_without_evidence_rejected"] = False
    except A.EvidenceError:
        negatives["finding_without_evidence_rejected"] = True
    negatives["good_fixture_findings"] = [f.id for f in good.findings]
    negatives["robots_disallow_respected"] = (priv.status == "skipped_robots"
                                              and not any(p.startswith("/private/") for p in seen))
    try:
        offer.build_offer_card(bad.to_dict(), "Truhlarstvi Fiktivni", discount_pct=offer.MAX_DISCOUNT_PCT + 5)
        negatives["discount_above_limit_refused"] = False
    except offer.DiscountRefused:
        negatives["discount_above_limit_refused"] = True
    src = "".join(p.read_text(encoding="utf-8") for p in (REPO / "tools" / "site_audit").glob("*.py"))
    negatives["no_delivery_code"] = not any(k in src for k in ("smtplib", "sendmail", "def send", "send_message("))

    # ---- sample internal offer card (bad fixture)
    card = offer.build_offer_card(bad.to_dict(), "Truhlarstvi Fiktivni (fixture)")
    pj, pm = offer.write_offer_card(card, data / "offer_cards")
    shutil.copy(pj, OUT / "offer_card_sample.json")
    shutil.copy(pm, OUT / "offer_card_sample.md")

    # ---- emulator (synthetic dental sites)
    emu = emulator.run(data / "emulator", year=year)
    texts_meta = json.loads(emulator.TEXTS_FIXTURE.read_text(encoding="utf-8"))

    # ---- ONE real public page (reserved test domain)
    real = A.audit_url("https://example.com/", data / "shots_example", current_year=year)

    checks = {
        "bad_fixture_expected_found": sorted(BAD_EXPECTED - {f.id for f in bad.findings}) == [],
        "good_fixture_zero_findings": good.findings == [],
        **{k: v for k, v in negatives.items() if isinstance(v, bool)},
        "emulator_clean_control_fp_zero": emu["clean_control_false_positives"] == 0,
        "emulator_fabricated_zero": emu["fabricated_findings"] == [],
        "emulator_recall_ge_0_9": (emu["totals"]["recall"] or 0) >= 0.9,
        "example_com_audited": real.status == "ok",
    }
    verdict = "PASS" if all(checks.values()) else "FAIL"
    receipt = {
        "receipt": "scenario-D site audit (dental niche) — evidence-only findings, emulator, no outreach",
        "date": _dt.date.today().isoformat(),
        "verdict": verdict,
        "checks": checks,
        "fixtures": {"bad": _summ(bad), "good": _summ(good), "private_robots": _summ(priv)},
        "sample_offer_card": {"json": str((OUT / "offer_card_sample.json").relative_to(REPO)),
                              "md": str((OUT / "offer_card_sample.md").relative_to(REPO)),
                              "tier": card["tier"], "price_czk": card["price_czk"],
                              "top_findings": [f["id"] for f in card["top_findings"]]},
        "negatives": negatives,
        "emulator": {
            "sites": len(emu["rows"]), "clean_controls": emu["clean_controls"],
            "clean_control_false_positives": emu["clean_control_false_positives"],
            "fabricated_findings": emu["fabricated_findings"], "totals": emu["totals"],
            "per_type": emu["per_type"], "tier_accuracy": emu["tier_accuracy"],
            "simulated_pipeline": emu["simulated_pipeline"],
            "consequence_rule": {k: sorted(v) for k, v in emulator.CONSEQUENCES.items()},
            "rows": [{k: r[k] for k in ("site", "kind", "defects", "found", "tier", "expected_tier",
                                        "true_high", "accepted_free_mockup", "text_provider", "seconds")}
                     for r in emu["rows"]],
            "text_fixture": {"path": str(emulator.TEXTS_FIXTURE.relative_to(REPO)),
                             "generated_at": texts_meta.get("generated_at"),
                             "batches": [{"provider": b["provider"], "clinics": len(b["clinics"]),
                                          "failed_before": b.get("failed_before", [])}
                                         for b in texts_meta.get("batches", [])]},
        },
        "real_public_page": _summ(real),
        "data_dir_outside_git": str(data),
        "real_money": 0,
        "outreach_generated": False,
        "duration_s": round(time.time() - t0, 1),
        "caveats": [
            "Emulator sites and auditor were written by the same agent: perfect scores show internal "
            "consistency, not real-world precision. Real-site precision is unmeasured.",
            "Findings listed in consequence_rule (e.g. small text after a missing viewport) are counted as "
            "allowed real consequences, neither TP nor FP.",
            "Pipeline value is SIMULATED (deterministic owner-reaction rule), real_money = 0.",
        ],
    }
    (OUT / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": verdict, "checks": checks, "emulator_totals": emu["totals"],
                      "example_com": [f.id for f in real.findings], "data_dir": str(data)}, ensure_ascii=False))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
