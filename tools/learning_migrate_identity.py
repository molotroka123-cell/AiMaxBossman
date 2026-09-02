"""P0-02 conservative migration of legacy learning records.

Legacy VERIFIED records carry only display strings in verified_by. Policy:
  * verified_by entries that name an executable, independent verifier
    (`pytest:*`, `poc:*`, `glm-*`) become typed verifiers (external_tool /
    cross_model) and one evidence_record dated by the record's created_at and
    bound to end_sha — these are the runs that actually produced the evidence;
  * anything else is demoted to UNVERIFIED (never promoted) with a limitation
    note. Unknown identity/freshness → UNVERIFIED.
Run: python tools/learning_migrate_identity.py [--dry-run]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from learning import LearningStore, validate  # noqa: E402


def _epoch(iso: str) -> float:
    try:
        return time.mktime(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except (TypeError, ValueError):
        return 0.0


def typed_verifier(entry: str) -> dict | None:
    e = entry.strip()
    if e.startswith(("pytest", "poc:")):
        return {"principal_id": f"tool:{e}", "model_id": "", "role": "verifier", "run_id": "",
                "independence_class": "external_tool"}
    if e.startswith("glm-"):
        return {"principal_id": f"model:{e}", "model_id": "glm-5.3", "role": "verifier", "run_id": "",
                "independence_class": "cross_model"}
    return None


def migrate(store: LearningStore, *, dry_run: bool = False) -> dict:
    stats = {"kept": 0, "typed": 0, "demoted": 0}
    for case in store.verified():
        if case.get("verifiers") and case.get("evidence_records"):
            stats["kept"] += 1
            continue
        typed = [v for v in (typed_verifier(x) for x in case.get("verified_by") or []) if v]
        observed = _epoch(case.get("created_at", ""))
        new = dict(case)
        if typed and observed > 0:
            new["verifiers"] = typed
            new["evidence_records"] = [{
                "observed_at": observed, "collected_at": observed, "task_id": case["task_id"],
                "run_id": "", "source": typed[0]["principal_id"], "principal_id": typed[0]["principal_id"],
                "environment": "linux-container", "head_sha": case.get("end_sha", ""),
                "expected": case.get("original_repro_result") or "tests pass",
                "actual": case.get("regression_result") or case.get("original_repro_result") or "tests pass"}]
            stats["typed"] += 1
        else:
            new["learning_status"] = "UNVERIFIED"
            new["outcome"] = "PARTIAL"
            new.setdefault("limitations", []).append("migrated: verifier identity/freshness unknown → UNVERIFIED")
            stats["demoted"] += 1
        errs = validate(new)
        if errs:
            new["learning_status"], new["outcome"] = "UNVERIFIED", "PARTIAL"
            new.setdefault("limitations", []).append("migrated: " + "; ".join(errs)[:300])
            stats["demoted"] += 1
            stats["typed"] = max(0, stats["typed"] - 1)
        for k in ("version", "supersedes_version", "case_id", "created_at"):
            new.pop(k, None)
        new["created_at"] = case.get("created_at")
        if not dry_run:
            store.add(new, write_markdown=True)
    return stats


if __name__ == "__main__":
    print(migrate(LearningStore(), dry_run="--dry-run" in sys.argv))
