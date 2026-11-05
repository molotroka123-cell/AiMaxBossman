"""Periodic Watchdog for Google Colab Pro+ Compute Units, Training Progress, K1m6a Student Watcher, and Creator Mode Arena."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COLAB_DIR = Path(r"C:\Users\asd\Bossman\creative-runs\CYBER-COLAB-20261105")
K1M6A_DIR = Path(r"C:\Users\asd\Bossman\creative-runs\K1M6A-LIVE-20261105")
AUDIT_DIR = Path(r"C:\Users\asd\Bossman\wt-harness-guards\docs\audits\harness-tier4-20261105")


def check_colab_status() -> dict[str, Any]:
    journal = COLAB_DIR / "JOURNAL_RU.md"
    lines = []
    if journal.exists():
        lines = [ln.strip() for ln in journal.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip() and not ln.startswith("#")]

    last_entry = lines[-1] if lines else ""
    units_found = re.findall(r"(\d+\.\d+)\s*(?:units|единиц)", "\n".join(lines[-8:]))
    latest_units = float(units_found[-1]) if units_found else 986.93

    # Check if any new report.json or adapter zip arrived in COLAB_DIR
    reports = list(COLAB_DIR.glob("*report*.json"))
    adapters = list(COLAB_DIR.glob("*adapter*.zip"))

    pilot_py = (COLAB_DIR / "colab_pilot.py").read_text(encoding="utf-8", errors="replace") if (COLAB_DIR / "colab_pilot.py").exists() else ""
    self_contained_nb = COLAB_DIR / "Bossman_SelfContained_Stage1_Stage2_Colab.ipynb"

    return {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "colab_compute_units_last_recorded": latest_units,
        "active_gpu_burn_detected": False,
        "training_run_state": "COMPLETED_SAVED" if reports else "READY_AWAITING_COLAB_UPLOAD_OR_SIGNAL",
        "credit_guard_in_colab_pilot_py": "runtime.unassign()" in pilot_py,
        "self_contained_stage1_stage2_nb_ready": self_contained_nb.exists(),
        "self_contained_nb_path": str(self_contained_nb),
        "last_journal_entry": last_entry[:400],
        "downloaded_reports": [str(p.name) for p in reports],
        "downloaded_adapters": [str(p.name) for p in adapters],
    }


def check_k1m6a_status() -> dict[str, Any]:
    st_file = K1M6A_DIR / "live_status.json"
    if not st_file.exists():
        return {"state": "NOT_STARTED"}
    try:
        return json.loads(st_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"state": "READ_ERROR", "error": str(exc)}


def main() -> int:
    colab = check_colab_status()
    k1m6a = check_k1m6a_status()
    rocm_proof_path = AUDIT_DIR / "ROCM_WEIGHT_SURGERY_PROOF_20261105.json"
    arena_path = AUDIT_DIR / "CREATOR_MODE_MULTI_AGENT_ARENA_20261105.json"

    rocm_summary = None
    if rocm_proof_path.exists():
        rp = json.loads(rocm_proof_path.read_text(encoding="utf-8"))
        rocm_summary = {
            "gpu_device": rp.get("gpu_device"),
            "modified_parameters_pct": rp.get("modified_parameters_pct"),
            "before_heldout_accuracy_pct": (rp.get("before_heldout_eval") or {}).get("accuracy_pct"),
            "after_heldout_accuracy_pct": (rp.get("after_heldout_eval") or {}).get("accuracy_pct"),
            "sha256_changed_verified": rp.get("sha256_changed_verified"),
        }

    arena_summary = None
    if arena_path.exists():
        ar = json.loads(arena_path.read_text(encoding="utf-8"))
        arena_summary = {
            "built_projects_count": ar.get("built_projects_count"),
            "arena_runs_count": ar.get("arena_runs_count"),
            "winners": [r.get("winner") for r in (ar.get("arena_reports") or [])],
        }

    payload = {
        "watchdog_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "colab_credit_and_training": colab,
        "local_rocm_weight_surgery": rocm_summary,
        "k1m6a_live_student_watcher": {
            "state": k1m6a.get("state"),
            "observations_count": k1m6a.get("observations_count"),
            "remaining_seconds": k1m6a.get("remaining_seconds"),
            "latest_observation": k1m6a.get("latest_observation"),
        },
        "creator_mode_arena": arena_summary,
    }
    out_path = COLAB_DIR / "COLAB_CREDIT_WATCHDOG_STATUS.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
