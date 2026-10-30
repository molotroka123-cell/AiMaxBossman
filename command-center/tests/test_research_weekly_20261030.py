"""Tests for AiMaxBossman Weekly Research Digest (2026-10-30):
  1. AgentProofBench (arXiv:2610.34634) — 50 Computer Use tasks, >=95% agreement, 0 false positives.
  2. CUA-Recovery (arXiv:2610.27645) — mid-trajectory fault injection & StepStateGuard (>=80% detection, >=50% cascade reduction).
  3. Safety Must Survive Self-Improvement (arXiv:2610.01073) — score reset, full + composite checks, rollback to validated build.
  4. TimlMemBench (ACL Anthology 2026.findings-emnlp.507) — fact source/date/scope/status lifecycle, >=95% updates, 0 leaks.
"""
from __future__ import annotations

from pathlib import Path

from bcc.features.research_weekly_20261030 import (
    run_agent_proof_bench,
    run_all_weekly_research_checks,
    run_cua_recovery_bench,
    run_self_improve_safety_bench,
    run_timl_mem_bench,
)


async def test_1_agent_proof_bench_50_tasks(env, tmp_path: Path):
    res = await run_agent_proof_bench(env.svc, tmp_path)
    assert res["total_tasks"] == 50
    assert res["agreement_rate"] >= 0.95
    assert res["false_positives"] == 0
    assert res["false_positive_rate"] == 0.0
    assert res["agent_false_claims_caught"] == 14
    assert res["defective_rules_caught"] == 2
    assert res["all_have_rule_version_snapshot_and_evidence"] is True
    assert res["unit_guard_passed"] is True
    assert res["live_agent_status"] == "NOT_RUN"
    assert res["passed_target"] == "NOT_RUN"


def test_2_cua_recovery_mid_trajectory_faults():
    res = run_cua_recovery_bench()
    assert res["total_scenarios"] == 30
    assert res["fault_breakdown"] == {
        "wrong_form_field": 10,
        "closed_dialog": 10,
        "failed_save": 10,
    }
    assert res["baseline"]["status"] == "NOT_RUN"
    gb = res["guarded_bossman"]
    assert gb["detection_rate"] >= 0.80
    assert gb["cascade_failure_reduction"] == "NOT_RUN"
    assert gb["cascading_failures"] == 0
    assert res["unit_guard_passed"] is True
    assert res["live_agent_status"] == "NOT_RUN"
    assert res["passed_target"] == "NOT_RUN"


def test_3_self_improve_safety_reset_and_rollback(tmp_path: Path):
    res = run_self_improve_safety_bench(tmp_path)
    assert res["total_histories"] == 12
    assert res["unsafe_candidates_tested"] == 8
    assert res["unsafe_rolled_back"] == 8
    assert res["safe_candidates_tested"] == 4
    assert res["safe_activated"] == 4
    assert res["stale_evaluations_reset_count"] == 12
    assert res["unvalidated_active_versions"] == 0
    assert res["post_rollback_pass_rate"] == 1.0
    assert res["journal_chain_verified"] is True
    assert res["unit_guard_passed"] is True
    assert res["live_agent_status"] == "NOT_RUN"
    assert res["passed_target"] == "NOT_RUN"


async def test_4_timl_mem_bench_lifecycle_and_scope(env):
    res = await run_timl_mem_bench(env.svc)
    assert res["total_scenarios"] == 40
    assert res["update_accuracy"] >= 0.95
    assert res["leaked_revoked_facts"] == 0
    assert res["leaked_out_of_scope_facts"] == 0
    assert res["total_revoked_or_scoped_leaks"] == 0
    assert res["all_facts_store_source_date_scope_status"] is True
    assert res["unit_guard_passed"] is True
    assert res["live_agent_status"] == "NOT_RUN"
    assert res["passed_target"] == "NOT_RUN"


async def test_http_research_weekly_endpoints(env):
    st = await env.client.get("/api/research-weekly/status")
    assert st.status_code == 200, st.text
    assert st.json()["status"] == "READY"

    run_resp = await env.client.post("/api/research-weekly/run")
    assert run_resp.status_code == 200, run_resp.text
    data = run_resp.json()
    assert data["unit_guards_status"] == "PASS_UNIT"
    assert data["live_agent_status"] == "NOT_RUN"
    assert data["all_targets_met"] == "NOT_RUN"
    assert len(data["receipt_sha256"]) == 64
