"""Live End-to-End Proof: Bossman OpenCode Web Pult Self-Coding with Free & Paid Cloud Models.

Proves:
1. Web Pult OpenCode API exposes curated Free & Paid cloud coding models (`GET /api/coding-tasks/workers`)
   and self-coding repo targeting (`POST /api/coding-tasks/self-target`).
2. A Free Cloud Coding Model executes a real multi-turn OpenCode tool session (`read_file` -> `edit_file`
   -> `run_tests` -> `finish`) inside an IsolatedWorktree clone of Bossman, passes Bossman's independent
   `_verify_in_sandbox` pytest check, and issues a cryptographic `Mission Receipt v1` (`VERIFIED`).
3. A Paid Top Cloud Coding Model executes a real multi-turn OpenCode tool session (`read_file` -> `edit_file`
   -> `run_tests` -> `finish`) inside an IsolatedWorktree clone of Bossman, passes `_verify_in_sandbox`,
   issues `Mission Receipt v1` (`VERIFIED`), and applies its self-written code + unit test directly into
   Bossman's canonical repository (`state == "APPLIED"`).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "command-center"))
sys.path.insert(0, str(REPO_ROOT / "bossman-core"))
sys.path.insert(0, str(REPO_ROOT / "bossman-shared"))


def _wait_for_state(client, task_id: str, target_states: set[str], timeout_s: float = 240.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last = {}
    while time.monotonic() < deadline:
        r = client.get(f"/api/coding-tasks/{task_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if last.get("state") in target_states:
            return last
        time.sleep(1.0)
    raise TimeoutError(f"Task {task_id} did not reach {target_states} within {timeout_s}s; last state={last.get('state')}")


def main() -> int:
    os.environ["BOSSMAN_VERIFY_PYTHON"] = sys.executable
    tmp_db = Path(tempfile.mkdtemp(prefix="bossman_opencode_proof_")) / "proof_bcc.db"
    os.environ["BCC_DB_PATH"] = str(tmp_db)

    from fastapi.testclient import TestClient
    from bcc.app import create_app

    app = create_app()
    client = TestClient(app)

    # 1. Inspect Worker Catalog & Cloud Readiness
    r_workers = client.get("/api/coding-tasks/workers")
    assert r_workers.status_code == 200, r_workers.text
    workers_data = r_workers.json()

    r_self = client.post("/api/coding-tasks/self-target")
    assert r_self.status_code == 200, r_self.text
    self_target_data = r_self.json()
    assert self_target_data["ok"] is True

    print(
        f"[1/4] OpenCode Catalog Ready: total={workers_data['summary']['total']} "
        f"(free={workers_data['summary']['free_count']}, paid={workers_data['summary']['paid_count']}), "
        f"self_repo={self_target_data['source_repo']}"
    )

    # 2. Live Self-Coding Run #1: FREE Cloud Model
    free_prompt = (
        "You are self-coding Bossman in an isolated worktree.\n"
        "Follow these exact steps using your tools:\n"
        "1. Call `read_file` on `command-center/bcc/features/coding_tasks.py` with `start_line=150` and `end_line=215`.\n"
        "2. Call `edit_file` on `command-center/bcc/features/coding_tasks.py` to add a helper function "
        "`list_free_opencode_worker_ids() -> list[str]` right after `summarize_opencode_roster()`:\n"
        "```python\n"
        "def list_free_opencode_worker_ids() -> list[str]:\n"
        "    \"\"\"Return IDs of all curated free cloud coding models in the OpenCode catalog.\"\"\"\n"
        "    return [k for k, v in WORKERS.items() if v.get(\"tier\") == \"free\"]\n"
        "```\n"
        "3. Call `read_file` on `command-center/tests/test_opencode_web_pult_self_coding.py`.\n"
        "4. Call `edit_file` on `command-center/tests/test_opencode_web_pult_self_coding.py` to append a test "
        "`test_list_free_opencode_worker_ids()` that imports `list_free_opencode_worker_ids` from "
        "`bcc.features.coding_tasks` and asserts `'poolside-laguna-free' in list_free_opencode_worker_ids()`.\n"
        "5. Call `run_tests` with `paths=['command-center/tests/test_opencode_web_pult_self_coding.py']` and `runner='pytest'`.\n"
        "6. Once `run_tests` passes, call `finish` with a concise summary."
    )

    r_create_free = client.post(
        "/api/coding-tasks",
        json={
            "title": "Self-Coding Proof #1 (Free Cloud Model): list_free_opencode_worker_ids",
            "description": free_prompt,
            "worker": "poolside-laguna-free",
            "fallback_workers": [
                "minimax-m31-free",
                "openrouter-code-free",
                "groq-gpt-oss-120b-free",
                "gemini-3-flash-free",
                "groq-qwen38-free",
            ],
            "source_repo": "self",
            "self_target": True,
            "allowed_paths": [
                "command-center/bcc/features/coding_tasks.py",
                "command-center/tests/test_opencode_web_pult_self_coding.py",
            ],
            "test_command": "pytest command-center/tests/test_opencode_web_pult_self_coding.py",
        },
    )
    assert r_create_free.status_code == 200, r_create_free.text
    free_task = r_create_free.json()
    free_id = free_task["id"]
    print(f"[2/4] Created Free Cloud Self-Coding Task {free_id} (worker={free_task['worker']}) -> approving plan...")

    r_app_free = client.post(f"/api/coding-tasks/{free_id}/approve", json={"by": "owner-proof"})
    assert r_app_free.status_code == 200, r_app_free.text

    free_done = _wait_for_state(client, free_id, {"VERIFY", "FAILED", "ABORTED"}, timeout_s=240.0)
    print(
        f"      Free Cloud Task {free_id} finished: state={free_done['state']}, "
        f"used_worker={free_done.get('result', {}).get('worker_used', free_done['worker'])}, "
        f"verify_passed={free_done.get('verification', {}).get('passed')}"
    )
    assert free_done["state"] == "VERIFY", json.dumps(free_done, indent=2, ensure_ascii=False)
    assert free_done["verification"]["passed"] is True, json.dumps(free_done["verification"], indent=2)
    assert free_done["result"]["mission_receipt"]["verdict"] == "VERIFIED"

    # 3. Live Self-Coding Run #2: PAID Top Cloud Model + Apply to Bossman Repo
    paid_prompt = (
        "You are self-coding Bossman in an isolated worktree.\n"
        "Follow these exact steps using your tools:\n"
        "1. Call `read_file` on `command-center/bcc/features/coding_tasks.py` with `start_line=150` and `end_line=215`.\n"
        "2. Call `edit_file` on `command-center/bcc/features/coding_tasks.py` to add a helper function "
        "`get_opencode_self_coding_capabilities() -> dict` right after `summarize_opencode_roster()`:\n"
        "```python\n"
        "def get_opencode_self_coding_capabilities() -> dict:\n"
        "    \"\"\"Return OpenCode Web Pult self-coding capabilities and live roster counts.\"\"\"\n"
        "    summary = summarize_opencode_roster()\n"
        "    return {\n"
        "        \"engine\": \"bossman-opencode-v1\",\n"
        "        \"self_coding\": True,\n"
        "        \"isolated_worktree\": True,\n"
        "        **summary,\n"
        "    }\n"
        "```\n"
        "3. Call `read_file` on `command-center/tests/test_opencode_web_pult_self_coding.py`.\n"
        "4. Call `edit_file` on `command-center/tests/test_opencode_web_pult_self_coding.py` to append a unit test "
        "`test_opencode_self_coding_capabilities()` that imports `get_opencode_self_coding_capabilities` from "
        "`bcc.features.coding_tasks` and asserts:\n"
        "   - `caps['engine'] == 'bossman-opencode-v1'`\n"
        "   - `caps['self_coding'] is True`\n"
        "   - `caps['isolated_worktree'] is True`\n"
        "   - `caps['free_count'] >= 5`\n"
        "   - `caps['paid_count'] >= 4`\n"
        "5. Call `run_tests` with `paths=['command-center/tests/test_opencode_web_pult_self_coding.py']` and `runner='pytest'`.\n"
        "6. Once `run_tests` passes, call `finish` with a concise summary."
    )

    r_create_paid = client.post(
        "/api/coding-tasks",
        json={
            "title": "Self-Coding Proof #2 (Paid Cloud Model): get_opencode_self_coding_capabilities",
            "description": paid_prompt,
            "worker": "deepseek-v4.1-flash",
            "fallback_workers": [
                "haiku-5.5",
                "glm-flash",
                "sonnet-4.6",
                "gpt-5.4-codex",
            ],
            "source_repo": "self",
            "self_target": True,
            "allowed_paths": [
                "command-center/bcc/features/coding_tasks.py",
                "command-center/tests/test_opencode_web_pult_self_coding.py",
            ],
            "test_command": "pytest command-center/tests/test_opencode_web_pult_self_coding.py",
        },
    )
    assert r_create_paid.status_code == 200, r_create_paid.text
    paid_task = r_create_paid.json()
    paid_id = paid_task["id"]
    print(f"[3/4] Created Paid Cloud Self-Coding Task {paid_id} (worker={paid_task['worker']}) -> approving plan...")

    r_app_paid = client.post(f"/api/coding-tasks/{paid_id}/approve", json={"by": "owner-proof"})
    assert r_app_paid.status_code == 200, r_app_paid.text

    paid_done = _wait_for_state(client, paid_id, {"VERIFY", "FAILED", "ABORTED"}, timeout_s=240.0)
    print(
        f"      Paid Cloud Task {paid_id} finished: state={paid_done['state']}, "
        f"used_worker={paid_done.get('result', {}).get('worker_used', paid_done['worker'])}, "
        f"verify_passed={paid_done.get('verification', {}).get('passed')}"
    )
    assert paid_done["state"] == "VERIFY", json.dumps(paid_done, indent=2, ensure_ascii=False)
    assert paid_done["verification"]["passed"] is True, json.dumps(paid_done["verification"], indent=2)
    assert paid_done["result"]["mission_receipt"]["verdict"] == "VERIFIED"

    # 4. Apply the Paid Cloud Model's verified self-coded patch directly into Bossman's canonical repository
    print(f"[4/4] Applying verified self-coded patch from Task {paid_id} into Bossman repo...")
    r_req_apply = client.post(f"/api/coding-tasks/{paid_id}/apply", json={"by": "owner-proof"})
    assert r_req_apply.status_code == 200, r_req_apply.text
    wait_apply = r_req_apply.json()
    assert wait_apply["state"] == "WAIT_APPROVAL" and wait_apply["approval"]["gate"] == "apply"

    r_confirm_apply = client.post(f"/api/coding-tasks/{paid_id}/approve", json={"by": "owner-proof"})
    assert r_confirm_apply.status_code == 200, r_confirm_apply.text
    applied_task = r_confirm_apply.json()
    assert applied_task["state"] == "APPLIED", json.dumps(applied_task, indent=2, ensure_ascii=False)
    assert applied_task["applied"]["applied"] is True

    # Write proof artifact JSON
    out_dir = REPO_ROOT / "docs" / "audits" / "2026-10-20-opencode-self-coding"
    out_dir.mkdir(parents=True, exist_ok=True)
    proof_path = out_dir / "opencode_self_coding_proof.json"

    proof_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "PASS",
        "catalog_summary": workers_data["summary"],
        "providers": workers_data["providers"],
        "self_target": {
            "ok": self_target_data["ok"],
            "default_allowed_paths": self_target_data["default_allowed_paths"],
            "default_test_command": self_target_data["default_test_command"],
        },
        "free_cloud_self_coding_proof": {
            "task_id": free_done["id"],
            "requested_worker": free_done["worker"],
            "worker_used": free_done["result"].get("worker_used", free_done["worker"]),
            "worker_tier": free_done["result"].get("worker_tier", "free"),
            "model_id": free_done["result"].get("model_id"),
            "state": free_done["state"],
            "verification": free_done["verification"],
            "changed_files": free_done["diff"]["changed_files"],
            "tool_calls": free_done["result"]["sidecar"].get("tool_calls", []),
            "mission_receipt": free_done["result"]["mission_receipt"],
        },
        "paid_cloud_self_coding_proof": {
            "task_id": paid_done["id"],
            "requested_worker": paid_done["worker"],
            "worker_used": paid_done["result"].get("worker_used", paid_done["worker"]),
            "worker_tier": paid_done["result"].get("worker_tier", "paid"),
            "model_id": paid_done["result"].get("model_id"),
            "state": applied_task["state"],
            "verification": paid_done["verification"],
            "changed_files": paid_done["diff"]["changed_files"],
            "tool_calls": paid_done["result"]["sidecar"].get("tool_calls", []),
            "mission_receipt": paid_done["result"]["mission_receipt"],
            "applied": applied_task["applied"],
        },
    }
    proof_path.write_text(json.dumps(proof_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"SUCCESS: Proof artifact written to {proof_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
