"""Offline proof: War Terminal per-client tender-lead dossier (earn scenario C, fast-start track) on the real fixtures only.

Runs with the network blocked at the socket layer, no services, no model call. Inputs: apps/war-terminal/tests/fixtures_cz.json
(ISVZ open data), fixtures_prozorro.json (Prozorro API answers), fixtures_fx/cnb_daily.txt (CNB fixing). Writes
docs/audits/2026-10-24-earn-tender-dossier/{receipt.json, sample-dossier.md, sample-dossier.json}.

The verdict covers CAPABILITY only. revenue = "NOT_RUN": no real client, no real payment, nothing sent to any supplier or buyer.

Usage: python tools/prove_tender_dossier.py
"""
from __future__ import annotations

import hashlib
import json
import socket
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "war-terminal"
FIX = APP / "tests"
OUT = ROOT / "docs" / "audits" / "2026-10-24-earn-tender-dossier"
sys.path.insert(0, str(APP))

from warterminal import dossier as DS  # noqa: E402
from warterminal.connectors import cnb, cz, prozorro as pz  # noqa: E402

# Fixture retrieval moment of the Prozorro answers: every fixture deadline is judged against this, then re-judged "today".
REF = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)
TODAY_REF = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
SAMPLE = {"name": "Sample fast-start supplier (synthetic profile)", "country": "CZ", "cpv_codes": ["09", "15"],
          "keywords": {"en": ["diesel"]}, "countries": ["CZ", "UA"], "capacity": {"max_contract_value": 100000, "currency": "EUR"}}
CONTROLLED_PROBE = {"name": "Controlled-goods probe (synthetic)", "cpv_codes": ["353", "347"], "countries": ["UA"]}

_net = {"attempts": 0}


def _blocked(*a, **k):
    _net["attempts"] += 1
    raise OSError("network blocked by prove_tender_dossier (offline proof)")


def block_network() -> None:
    socket.socket.connect = _blocked            # type: ignore[assignment]
    socket.socket.connect_ex = _blocked         # type: ignore[assignment]
    socket.create_connection = _blocked         # type: ignore[assignment]
    socket.getaddrinfo = _blocked               # type: ignore[assignment]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_tenders() -> list[dict]:
    raw = json.loads((FIX / "fixtures_cz.json").read_text(encoding="utf-8"))
    cz_docs = cz.normalize_all({"files": [{"url": cz.FILE_URL.format(m=9, y=2026), **raw["header"]}], "data": raw["data"]},
                               "2026-10-17T08:00:00Z", "fixture", date(2026, 10, 17))
    pzx = json.loads((FIX / "fixtures_prozorro.json").read_text(encoding="utf-8"))
    ua_docs = pz.normalize_all(pzx["details"], "2026-10-21T20:00:00+00:00", "fixture", date(2026, 10, 21))
    return cz_docs + ua_docs


def main() -> int:
    block_network()
    OUT.mkdir(parents=True, exist_ok=True)
    tenders = load_tenders()
    fx = cnb.parse((FIX / "fixtures_fx" / "cnb_daily.txt").read_text(encoding="utf-8"))
    calls = {"summarizer": 0}
    checks: list[dict] = []

    def check(cid: str, kind: str, ok: bool, detail) -> None:
        checks.append({"id": cid, "kind": kind, "pass": bool(ok), "detail": detail})

    sample = DS.build_dossier(SAMPLE, tenders, today=REF, fx_rates=fx["rates"], fx_meta=fx)
    md = DS.dossier_markdown(sample)
    (OUT / "sample-dossier.md").write_text(md, encoding="utf-8")
    (OUT / "sample-dossier.json").write_text(DS.dossier_json(sample), encoding="utf-8")
    items = sample["items"]

    # ---- positives
    check("P1_sample_lists_open_fast_start_leads", "positive",
          len(items) >= 1 and all(i["regulatory"]["fast_start"] and not i["controlled"] for i in items),
          [{"tender_key": i["tender_key"], "track": i["regulatory"]["track"]} for i in items])
    check("P2_every_lead_has_value_buyer_deadline_why_margin_next_steps", "positive",
          all(i["buyer"] and i["deadline"] and i["why"] and i["next_steps"] and "pricing_status" in i
              and (i["economics"]["back_to_back_supply"] is not None or not i["economics"]["priced"]) for i in items),
          [{"tender_key": i["tender_key"], "estimated_value": i["estimated_value"], "currency": i["currency"],
            "margin_pct": (i["economics"]["back_to_back_supply"] or {}).get("margin_pct"), "deadline": i["deadline"]} for i in items])
    check("P3_deadlines_in_future_of_reference", "positive",
          all(datetime.fromisoformat(i["deadline"].replace("Z", "+00:00")) > REF for i in items), REF.isoformat())
    check("P4_footer_and_not_sent", "positive", md.rstrip().endswith(f"_{DS.FOOTER}_") and sample["delivery"] == "NOT_SENT",
          {"footer": DS.FOOTER, "delivery": sample["delivery"]})

    # ---- negatives
    probe = DS.build_dossier(CONTROLLED_PROBE, tenders, today=REF, fx_rates=fx["rates"])
    check("N1_controlled_excluded_without_compliance", "negative",
          not probe["items"] and probe["summary"]["excluded"]["CONTROLLED_NOT_APPROVED"] >= 2,
          {"listed": len(probe["items"]), "excluded": [e for e in probe["excluded"] if e["reason"] == "CONTROLLED_NOT_APPROVED"]})
    appr = DS.build_dossier(CONTROLLED_PROBE, tenders, today=REF, fx_rates=fx["rates"], approved="compliance")
    check("N2_compliance_approved_controlled_included_and_flagged", "negative",
          bool(appr["items"]) and all(i["controlled"] and i["flags"] == [DS.CONTROLLED_FLAG] for i in appr["items"]),
          [{"tender_key": i["tender_key"], "track": i["regulatory"]["track"], "flags": i["flags"]} for i in appr["items"]])
    later = DS.build_dossier(SAMPLE, tenders, today=TODAY_REF, fx_rates=fx["rates"])
    expired = [e["tender_key"] for e in later["excluded"] if e["reason"] == "EXPIRED"]
    check("N3_expired_tender_excluded_at_today", "negative",
          bool(expired) and not set(expired) & {i["tender_key"] for i in later["items"]},
          {"reference": TODAY_REF.isoformat(), "expired": expired, "still_listed": [i["tender_key"] for i in later["items"]]})
    try:
        DS.build_dossier({"name": "Empty"}, tenders, today=REF)
        empty = None
    except DS.DossierError as exc:
        empty = exc.code
    check("N4_empty_profile_refused", "negative", empty == "PROFILE_EMPTY", empty)
    fx_missing = [i for i in items if i["fx_status"] == "FX_RATE_MISSING"]
    check("N5_missing_fx_is_none_not_one_to_one", "negative",
          all(i["estimated_value_usd"] is None and i["estimated_value_eur"] is None for i in fx_missing),
          {"fx_pairs": sorted(fx["rates"]), "fx_missing_items": [(i["tender_key"], i["currency"], i["estimated_value_usd"]) for i in fx_missing]})
    check("N6_no_network_attempts", "negative", _net["attempts"] == 0, _net["attempts"])
    check("N7_no_model_called", "negative",
          calls["summarizer"] == 0 and all(i["summary_status"] == "NO_HOOK" for i in items + appr["items"]), "summarizer=None")
    controlled_keys = {i["tender_key"] for i in appr["items"]}
    check("N8_controlled_absent_from_sample", "negative", not any(k in md for k in controlled_keys), sorted(controlled_keys))

    verdict = "PASS" if all(c["pass"] for c in checks) else "FAIL"
    receipt = {
        "verdict": verdict,
        "verdict_scope": "CAPABILITY ONLY: the offline dossier builder works on the real fixtures; not earned money",
        "revenue": "NOT_RUN",
        "revenue_note": "no real client, no real payment, no invoice; nothing was sent to any supplier or buyer",
        "sent_to_anyone": False,
        "network_attempts": _net["attempts"],
        "model_calls": 0,
        "reference_time": REF.isoformat(),
        "today_reference_time": TODAY_REF.isoformat(),
        "track": "UNCONTROLLED_FAST_START (fuel, food, logistics, general goods)",
        "sample_profile": SAMPLE,
        "sample_summary": sample["summary"],
        "inputs": {p: sha(FIX / p) for p in ("fixtures_cz.json", "fixtures_prozorro.json", "fixtures_fx/cnb_daily.txt")},
        "code": {p: sha(ROOT / p) for p in ("apps/war-terminal/warterminal/dossier.py", "apps/war-terminal/warterminal/monetization.py",
                                            "tools/prove_tender_dossier.py")},
        "outputs": {"sample-dossier.md": sha(OUT / "sample-dossier.md"), "sample-dossier.json": sha(OUT / "sample-dossier.json")},
        "checks": checks,
        "known_limits": [
            "fixtures are a few real records, not a live feed; deadlines judged at the fixture retrieval time",
            "the CNB fixture has no UAH rate, so UAH leads carry no USD/EUR value (by design: no 1:1 fallback)",
            "sanctions lists not loaded in this offline run: the dossier says buyers are NOT screened",
        ],
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (OUT / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    for c in checks:
        print(f"{'PASS' if c['pass'] else 'FAIL'}  {c['id']}")
    print(f"verdict={verdict} revenue=NOT_RUN listed={len(items)} -> {OUT}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
