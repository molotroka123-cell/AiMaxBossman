"""CLI runner and audit receipt generator for Bossman Boost Autopilot (`/boost`).

Executes all 3 closed-loop pipelines (`system_health_and_hygiene`, `bossblock_world_audit`,
`daily_digest_and_proof`) plus mandatory negative controls:
1. AgentProofBench: catches false success claim when artifact is not written (`false_claim_caught=True`).
2. CUA-Recovery: detects mid-step fault, repairs from checkpoint (`rollback_and_retry`), and halts
   before irreversible action when unrepairable (`halt_before_irreversible`).
3. TimlMemBench: verifies scoped fact storage, superseding old runs, zero cross-scope leakage, and
   instant revocation.
4. Secret Scrubbing: verifies `pult_report.redact()` scrubs API keys before writing digest artifacts.
5. BossBlock 3D Audit: verifies Dnipro city center 5-layer game assets (`mesh.glb`, `satellite.png`,
   `osm_buildings_roads.geojson`, `streetview/photos`).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
for extra in (REPO_ROOT, REPO_ROOT / "command-center", REPO_ROOT / "apps" / "bossblock"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _git_head_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            text=True,
            timeout=10,
        ).strip()
        return out
    except Exception:
        return "unknown"


async def run_boost_verification() -> dict[str, Any]:
    import httpx
    from bcc.api import create_app
    from bcc.auth import HEADER
    from bcc.config import Settings
    from bcc.v2.memory.facts import FactStore

    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="bossman_boost_autopilot_") as tmp:
        tmp_path = Path(tmp)
        data_dir = tmp_path / "data"
        settings = Settings(
            data_dir=data_dir,
            database_url=f"sqlite+aiosqlite:///{data_dir / 'bcc.db'}",
            ui_dir=tmp_path / "no-ui",
        )
        app = create_app(settings, announce_token=False, start_workers=False)
        svc = app.state.svc
        await svc.start()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={HEADER: svc.auth.token},
            ) as client:
                # 1. Run all 3 autopilot pipelines
                r_all = await client.post(
                    "/api/autopilot/run",
                    json={"pipeline": "all", "note": "Boost full pipeline verification"},
                )
                all_res = r_all.json()["result"]

                # 2. AgentProofBench negative control (false claim caught)
                r_false = await client.post(
                    "/api/autopilot/run",
                    json={
                        "pipeline": "system_health_and_hygiene",
                        "simulate_unwritten_artifact": True,
                        "agent_claim_text": "Готово: файл отчёта успешно сохранён и проверен.",
                    },
                )
                false_res = r_false.json()["result"]

                # 3. CUA-Recovery positive auto-repair & negative halt control
                r_repair = await client.post(
                    "/api/autopilot/run",
                    json={
                        "pipeline": "bossblock_world_audit",
                        "inject_mid_step_fault": "wrong_form_field",
                        "allow_auto_repair": True,
                    },
                )
                repair_res = r_repair.json()["result"]

                r_halt = await client.post(
                    "/api/autopilot/run",
                    json={
                        "pipeline": "bossblock_world_audit",
                        "inject_mid_step_fault": "closed_dialog",
                        "allow_auto_repair": False,
                    },
                )
                halt_res = r_halt.json()["result"]

                # 4. TimlMemBench scope isolation, superseding, and revocation
                store = FactStore(svc)
                r_mem1 = await client.post(
                    "/api/autopilot/run",
                    json={"pipeline": "daily_digest_and_proof", "scope": "personal"},
                )
                f1 = r_mem1.json()["result"]["memory_fact"]
                work_leak = await store.search(
                    subject="autopilot:daily_digest_and_proof", caller_scope="work"
                )
                public_leak = await store.search(
                    subject="autopilot:daily_digest_and_proof", caller_scope="public"
                )
                personal_ok = await store.search(
                    subject="autopilot:daily_digest_and_proof", caller_scope="personal"
                )
                r_mem2 = await client.post(
                    "/api/autopilot/run",
                    json={"pipeline": "daily_digest_and_proof", "scope": "personal"},
                )
                f2 = r_mem2.json()["result"]["memory_fact"]
                revoked = await store.revoke(fact_id=f2["id"])
                after_revoke = await store.search(
                    subject="autopilot:daily_digest_and_proof", caller_scope="personal"
                )

                # 5. Secret redaction check
                fake_sk = "sk-proj-" + ("9f8e7d6c" * 4)
                r_sec = await client.post(
                    "/api/autopilot/run",
                    json={
                        "pipeline": "daily_digest_and_proof",
                        "note": f"owner_key={fake_sk} must be redacted",
                    },
                )
                sec_res = r_sec.json()["result"]

                r_status = await client.get("/api/autopilot/status")
                status_body = r_status.json()
        finally:
            await svc.stop()

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    checks = {
        "all_3_pipelines_verified": all_res["verdict"] == "VERIFIED" and all_res["pipelines_run"] == 3,
        "agent_proof_bench_false_claim_caught": bool(
            false_res["verifier_status"] == "FAILED"
            and false_res["false_claim_caught"] is True
            and false_res["memory_fact"] is None
        ),
        "cua_recovery_auto_repaired": bool(
            repair_res["cua_recovery"]["decision"] == "rollback_and_retry"
            and repair_res["cua_recovery"]["repaired"] is True
            and repair_res["verifier_status"] == "VERIFIED"
        ),
        "cua_recovery_halted_before_irreversible": bool(
            halt_res["status"] == "HALTED_BEFORE_IRREVERSIBLE"
            and halt_res["cua_recovery"]["decision"] == "halt_before_irreversible"
            and halt_res["memory_fact"] is None
        ),
        "timl_memory_zero_scope_leak_and_revoke": bool(
            len(work_leak) == 0
            and len(public_leak) == 0
            and len(personal_ok) == 1
            and f1["id"] != f2["id"]
            and len(revoked) == 1
            and len(after_revoke) == 0
        ),
        "secret_redacted_before_persist": bool(
            sec_res["secret_redacted"] is True
            and fake_sk not in sec_res["sanitized_note"]
            and "[скрыто]" in sec_res["sanitized_note"]
        ),
        "bossblock_dnipro_3d_ready": bool(
            all_res["results"][1]["payload_summary"]["world_audit"]["core_3d_ready"] is True
            and all_res["results"][1]["payload_summary"]["world_audit"]["layers"]["osm"]["buildings"] == 67
            and all_res["results"][1]["payload_summary"]["world_audit"]["layers"]["streetview"]["photos_count"] == 52
        ),
    }
    all_passed = all(checks.values())
    receipt: dict[str, Any] = {
        "schema": "bossman.audit.boost_autopilot.v1",
        "date": "2026-10-31",
        "verdict": "PASS" if all_passed else "FAIL",
        "source_sha": _git_head_sha(),
        "rule_version": status_body["rule_version"],
        "elapsed_ms": elapsed_ms,
        "checks": checks,
        "metrics": status_body["metrics"],
        "pipelines_verified": [
            {
                "pipeline": r["pipeline"],
                "run_id": r["run_id"],
                "verifier_status": r["verifier_status"],
                "proof_sha256": r["proof_sha256"],
            }
            for r in all_res["results"]
        ],
        "negative_controls": {
            "agent_proof_bench_false_claim": {
                "verifier_status": false_res["verifier_status"],
                "false_claim_caught": false_res["false_claim_caught"],
                "memory_polluted": false_res["memory_fact"] is not None,
                "proof_sha256": false_res["proof_sha256"],
            },
            "cua_recovery_fault_injection": {
                "repair_decision": repair_res["cua_recovery"]["decision"],
                "halt_decision": halt_res["cua_recovery"]["decision"],
                "halted_before_irreversible": halt_res["status"] == "HALTED_BEFORE_IRREVERSIBLE",
            },
            "timl_memory_isolation": {
                "work_scope_leaks": len(work_leak),
                "public_scope_leaks": len(public_leak),
                "personal_scope_hits": len(personal_ok),
                "hits_after_revoke": len(after_revoke),
            },
            "secret_redaction": {
                "secret_redacted": sec_res["secret_redacted"],
                "sanitized_note": sec_res["sanitized_note"],
            },
        },
        "bossblock_dnipro_world_summary": {
            "verdict": all_res["results"][1]["payload_summary"]["world_audit"]["verdict"],
            "ready_layers_count": all_res["results"][1]["payload_summary"]["world_audit"]["ready_layers_count"],
            "buildings": all_res["results"][1]["payload_summary"]["world_audit"]["layers"]["osm"]["buildings"],
            "roads": all_res["results"][1]["payload_summary"]["world_audit"]["layers"]["osm"]["roads"],
            "streetview_photos": all_res["results"][1]["payload_summary"]["world_audit"]["layers"]["streetview"]["photos_count"],
            "mesh_octree_nodes": all_res["results"][1]["payload_summary"]["world_audit"]["layers"]["mesh"]["octree_nodes_saved"],
            "receipt_sha256": all_res["results"][1]["payload_summary"]["world_audit"]["receipt_sha256"],
        },
        "not_proven": [
            "Tier 5 self-improvement holdout transfer across 48h soak",
            "Live commercial revenue from external customers",
        ],
    }
    digest_bytes = json.dumps(
        {k: v for k, v in receipt.items() if k != "elapsed_ms"},
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    receipt["receipt_sha256"] = hashlib.sha256(digest_bytes).hexdigest()
    return receipt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run Bossman Boost Autopilot verification and write receipt.json")
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=REPO_ROOT / "docs" / "audits" / "2026-10-31-boost-autopilot-pipeline" / "receipt.json",
        help="Path to write receipt.json",
    )
    args = ap.parse_args(argv)
    receipt = asyncio.run(run_boost_verification())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"verdict": receipt["verdict"], "receipt_sha256": receipt["receipt_sha256"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if receipt["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
