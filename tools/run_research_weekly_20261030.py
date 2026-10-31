"""Runner for AiMaxBossman Weekly Research Digest (2026-10-30).

Executes all 4 benchmarks in priority order and writes a signed receipt to:
  docs/audits/2026-10-30-weekly-research-benchmarks/receipt.json

Pass `--live` to ALSO run live end-to-end tasks against `:8801` (`/api/tasks`,
`/api/approvals/auto-full`, `/api/memory/facts`) and include their measured receipt.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CC_DIR = ROOT / "command-center"
if str(CC_DIR) not in sys.path:
    sys.path.insert(0, str(CC_DIR))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from bcc.config import Settings
from bcc.db import Database
from bcc.features.research_weekly_20261030 import run_all_weekly_research_checks
from bcc.v2.recovery import StepStateGuard
from bcc.v2.verification import (
    ExpectedState,
    capture_initial_snapshot,
    payload_digest,
    verify_criterion_with_proof,
)


class _MiniServices:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db


async def _run_live_against_8801(svc: _MiniServices, agent_id: int = 48) -> dict:
    """Run live checks against running `:8801` (`/api/tasks`, `/api/approvals/auto-full`, `/api/memory/facts`)."""
    import prove_chat_attachments as p

    t0 = time.perf_counter()
    health = p.api("GET", "/health/live", timeout=15)
    ws = Path(r"C:\Users\asd\Bossman\wt-agent-proof-20261025\weekly-live")
    ws.mkdir(parents=True, exist_ok=True)
    target_file = ws / "live_state_proof.json"
    if target_file.exists():
        target_file.unlink()

    exp_true = ExpectedState(
        kind="file",
        target=str(target_file),
        expect={"exists": True, "contains": '"status": "VERIFIED"', "min_bytes": 15},
    )
    init_snap = await capture_initial_snapshot(exp_true, svc=svc, task={"id": 1}, roots=[ws])

    # 1. Enable Auto Full for `ws`
    af = p.api("POST", "/api/approvals/auto-full", {
        "folder": str(ws),
        "ttl_seconds": 900,
        "max_uses": 30,
        "by": "owner:weekly_research_live",
    })
    lease_id = ((af or {}).get("current") or {}).get("id")

    # 2. Submit a real live task to agent `agent_id` through `/api/tasks`
    prompt = (
        "Выполни ровно одну команду через terminal.run (mode=project_host, поле cwd НЕ передавай — "
        "рабочая папка уже выбрана в карточке владельца Auto Full): "
        "создай в текущей папке через python файл live_state_proof.json "
        "с JSON {\"orders_checked\": 12, \"status\": \"VERIFIED\"} и ответь коротко 'ГОТОВО'."
    )
    created = p.api("POST", "/api/tasks", {
        "prompt": prompt,
        "title": "Weekly Research Live AgentProofBench",
        "agent_id": agent_id,
        "workspace_path": str(ws),
        "run_now": True,
        "max_retries": 2,
    })
    task_id = created.get("id") or created["task"]["id"]
    deadline = time.time() + 180
    task_status = "running"
    while time.time() < deadline:
        d = p.api("GET", f"/api/tasks/{task_id}", timeout=30)
        task_status = d["task"]["status"]
        if task_status in ("completed", "failed", "stopped", "blocked"):
            break
        await asyncio.sleep(3)

    af_after = p.api("GET", "/api/approvals/auto-full?limit_log=10")
    if lease_id:
        p.api("POST", "/api/approvals/auto-full/revoke", {"lease_id": lease_id, "by": "owner:weekly_research_live"})

    # Verify independent application state via AgentProofBench verifier (`verify_criterion_with_proof`)
    proof_true = await verify_criterion_with_proof(
        exp_true,
        rule_id="live-agent-proof-01",
        rule_version="apb-2026.10.31-live",
        initial_snapshot=init_snap,
        agent_claim_text="Готово: файл live_state_proof.json создан и проверен.",
        svc=svc,
        task={"id": task_id},
        roots=[ws],
    )
    # Negative control: false claim when file does not contain IMPOSSIBLE_VALUE
    exp_neg = ExpectedState(
        kind="file",
        target=str(target_file),
        expect={"exists": True, "contains": '"status": "IMPOSSIBLE_VALUE"', "min_bytes": 15},
    )
    proof_false_claim = await verify_criterion_with_proof(
        exp_neg,
        rule_id="live-agent-proof-neg-01",
        rule_version="apb-2026.10.31-live",
        initial_snapshot=init_snap,
        agent_claim_text="Готово: успешно сохранено status=IMPOSSIBLE_VALUE.",
        svc=svc,
        task={"id": task_id},
        roots=[ws],
    )

    # 3. Live CUA-Recovery step-state guard check on the real file
    guard = StepStateGuard(allow_auto_repair=True)
    obs_ok = json.loads(target_file.read_text(encoding="utf-8")) if target_file.is_file() else {}
    step_ok = guard.verify_before_next_action(
        step_index=1,
        action_name="publish_verified_orders",
        irreversible=True,
        expected_state={"status": "VERIFIED", "orders_checked": 12},
        observed_state=obs_ok,
    )
    step_fault = guard.verify_before_next_action(
        step_index=2,
        action_name="publish_verified_orders",
        irreversible=True,
        expected_state={"status": "VERIFIED", "orders_checked": 12},
        observed_state={"status": "SAVING_ERROR", "orders_checked": 0},
    )

    # 4. Live TimlMemBench API check on `:8801` (`/api/memory/facts`)
    subj = f"WeeklyLiveMem-{int(time.time())}"
    f1 = p.api("POST", "/api/memory/facts", {
        "subject": subj,
        "predicate": "deployment_branch",
        "object": "wt-final-old",
        "statement": f"В тестовом сценарии {subj} автостарт системы временно указывал на старую ветку wt-final-old до обновления.",
        "valid_at": "2026-10-29",
        "mode": "append",
        "source_kind": "run",
        "scope": "personal",
    })
    f2 = p.api("POST", "/api/memory/facts", {
        "subject": subj,
        "predicate": "deployment_branch",
        "object": "main",
        "statement": f"В тестовом сценарии {subj} автостарт системы переведён на каноническую ветку main с проверкой.",
        "valid_at": "2026-10-31",
        "mode": "replace-current",
        "source_kind": "run",
        "scope": "personal",
    })
    work_search = p.api("GET", f"/api/memory/facts?subject={subj}&caller_scope=work")
    personal_search = p.api("GET", f"/api/memory/facts?subject={subj}&caller_scope=personal")
    revoked = p.api("POST", "/api/memory/facts/revoke", {"subject": subj, "predicate": "deployment_branch"})
    after_revoke = p.api("GET", f"/api/memory/facts?subject={subj}&caller_scope=personal")

    live_ok = (
        task_status == "completed"
        and proof_true.verifier_status == "VERIFIED"
        and proof_false_claim.verifier_status != "VERIFIED"
        and proof_false_claim.false_claim_caught is True
        and step_ok.matched is True
        and step_fault.matched is False
        and step_fault.decision in ("rollback_and_retry", "halt_before_irreversible")
        and work_search.get("total") == 0
        and personal_search.get("total") == 1
        and personal_search["items"][0]["object"] == "main"
        and after_revoke.get("total") == 0
    )
    return {
        "status": "PASS_LIVE" if live_ok else "PARTIAL_LIVE",
        "runtime_sha": health.get("build_sha"),
        "agent_id": agent_id,
        "task_id": task_id,
        "task_status": task_status,
        "auto_full_lease_id": lease_id,
        "auto_full_actions_logged": len((af_after or {}).get("action_log") or []),
        "agent_proof_bench_live": {
            "true_claim_verifier_status": proof_true.verifier_status,
            "false_claim_caught": proof_false_claim.false_claim_caught,
            "proof_sha256": proof_true.proof_sha256,
        },
        "cua_recovery_live": {
            "valid_state_matched": step_ok.matched,
            "fault_state_halted_before_irreversible": not step_fault.matched,
            "fault_decision": step_fault.decision,
        },
        "timl_mem_bench_live_api": {
            "fact_v1_id": f1.get("id"),
            "fact_v2_id": f2.get("id"),
            "out_of_scope_work_hits": work_search.get("total"),
            "in_scope_personal_hits": personal_search.get("total"),
            "active_object_after_replace": personal_search["items"][0]["object"] if personal_search.get("items") else None,
            "revoked_count": revoked.get("total"),
            "hits_after_revoke": after_revoke.get("total"),
        },
        "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
    }


async def _main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="Also run live tasks & API checks against :8801")
    ap.add_argument("--agent", type=int, default=48)
    args = ap.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="bossman_weekly_20261030_") as tmp:
        tmp_path = Path(tmp)
        settings = Settings(data_dir=tmp_path / "data")
        settings.ensure_dirs()
        db = Database(settings.database_url)
        await db.create_all()
        try:
            svc = _MiniServices(settings=settings, db=db)
            report = await run_all_weekly_research_checks(svc, work_dir=tmp_path / "work")
            if args.live:
                live_proof = await _run_live_against_8801(svc, args.agent)
                report["live_agent_proof"] = live_proof
                report["live_agent_status"] = live_proof["status"]
                report["receipt_sha256"] = payload_digest(report)
        finally:
            await db.close()

    out_dir = ROOT / "docs" / "audits" / "2026-10-30-weekly-research-benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = out_dir / "receipt.json"
    receipt_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"Receipt saved to: {receipt_path}")
    print(f"unit_guards_status = {report['unit_guards_status']}")
    print(f"live_agent_status  = {report['live_agent_status']}")
    print(f"all_targets_met    = {report['all_targets_met']}")
    print(f"receipt_sha256     = {report['receipt_sha256']}")
    for key, val in report["results"].items():
        print(
            f"  [{key}] unit_guard_passed={val['unit_guard_passed']} "
            f"live_agent_status={val['live_agent_status']} elapsed_ms={val['elapsed_ms']}"
        )
    return 0 if report["unit_guards_status"] == "PASS_UNIT" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
