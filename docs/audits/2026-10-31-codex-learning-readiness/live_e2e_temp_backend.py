"""Live end-to-end of lesson intake on a TEMP backend (own port, temp BCC_DATA_DIR). Synthetic data only.

    python docs/audits/2026-10-31-codex-learning-readiness/live_e2e_temp_backend.py http://127.0.0.1:8897 <temp data dir> <keys dir> [out.json]

WRITES lessons, so it refuses the owner's live port 8801 and the default 8800. Run it only against a backend you
started yourself with a throw-away BCC_DATA_DIR.
"""
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

url, data_dir, keys_dir = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
out_path = Path(sys.argv[4]) if len(sys.argv) > 4 else None
if urlparse(url).port in (8800, 8801, None):
    raise SystemExit("refusing: this script writes; use a temp backend on its own port")
tok = (data_dir / "token").read_text(encoding="utf-8").strip()
c = httpx.Client(base_url=url, headers={"X-BCC-Token": tok}, timeout=30)
out: dict = {}
base = {"title": "FX via Decimal", "body": "1000 USDT x 25 CZK/USD x 1.03 = 25750.00 CZK; use ROUND_HALF_UP.",
        "category": "business", "author": "codex", "source": "codex-session:live-probe-1", "observed_at": "2026-10-31"}
r = c.post("/api/memory/lessons", json=base).json()
lid = r["lesson"]["id"]
out["record"] = {"id": lid, "status": r["status"], "proven_learning": r["proven_learning"]}
out["duplicate_deduped"] = c.post("/api/memory/lessons", json=base).json()["deduped"]
out["neg_status_set"] = c.post("/api/memory/lessons", json={**base, "title": "x", "status": "verified_by_test"}).status_code
out["neg_no_source"] = c.post("/api/memory/lessons", json={**base, "title": "y", "source": ""}).status_code
out["neg_no_date"] = c.post("/api/memory/lessons", json={**base, "title": "z", "observed_at": ""}).status_code
out["neg_codex_personal"] = c.post("/api/memory/lessons", json={**base, "title": "p", "category": "personal"}).status_code
out["neg_oversized"] = c.post("/api/memory/lessons", content=json.dumps({**base, "body": "q" * 20000}),
                              headers={"Content-Type": "application/json"}).status_code
sec = c.post("/api/memory/lessons", json={**base, "title": "secret", "body": "password=SyntheticPw12345 here"}).json()
out["secret_redacted"] = bool(sec["redacted"]) and "SyntheticPw12345" not in sec["lesson"]["body"]
out["neg_verify_by_claim"] = c.post(f"/api/memory/lessons/{lid}/verify", json={"eval_ref": "pytest:x"}).status_code
wrong = c.post(f"/api/memory/lessons/{lid}/verify", json={"eval_ref": "business_exercise:fx_price_comparison_v1",
                                                          "answer": {"totals_czk": {}, "cheapest": "OFF-A"}}).json()
out["wrong_answer"] = {"eval_passed": wrong["eval_passed"], "status": wrong["lesson"]["status"]}
right = c.post(f"/api/memory/lessons/{lid}/verify", json={
    "eval_ref": "business_exercise:fx_price_comparison_v1",
    "answer": {"totals_czk": {"OFF-A": "25750.00", "OFF-B": "25261.32", "OFF-C": "25527.00"}, "cheapest": "OFF-B"}}).json()
out["right_answer"] = {"eval_passed": right["eval_passed"], "status": right["lesson"]["status"],
                       "eval_ref": right["lesson"]["eval_ref"], "receipt_sha256": right["lesson"]["eval_receipt_sha256"],
                       "proven_learning": right["lesson"]["proven_learning"]}
out["stale_total_30d"] = c.get("/api/memory/lessons/stale", params={"max_age_days": 30}).json()["total"]
out["keys_files_outside_data_dir"] = sorted(p.name for p in keys_dir.iterdir())
ok = (out["record"]["status"] == "lesson_recorded" and out["duplicate_deduped"] is True
      and out["neg_status_set"] == 422 and out["neg_no_source"] == 422 and out["neg_no_date"] == 422
      and out["neg_codex_personal"] == 403 and out["neg_oversized"] == 413 and out["secret_redacted"]
      and out["neg_verify_by_claim"] == 422 and out["wrong_answer"]["status"] == "lesson_recorded"
      and out["right_answer"]["status"] == "verified_by_test")
out["verdict"] = "PASS" if ok else "FAIL"
text = json.dumps(out, indent=2, ensure_ascii=False)
if out_path:
    out_path.write_text(text + "\n", encoding="utf-8")
print(text)
sys.exit(0 if ok else 1)
