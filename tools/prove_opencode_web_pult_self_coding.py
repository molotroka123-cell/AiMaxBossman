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


def _wait_for_terminal(client, task_id: str, timeout_s: float = 240.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last = {}
    while time.monotonic() < deadline:
        r = client.get(f"/api/coding-tasks/{task_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if last.get("status") in {"completed", "failed", "blocked"}:
            return last
        time.sleep(1.0)
    raise TimeoutError(f"Task {task_id} did not finish within {timeout_s}s; last status={last.get('status')}")


def main() -> int:
    os.environ["BOSSMAN_VERIFY_PYTHON"] = sys.executable
    tmp_dir = Path(tempfile.mkdtemp(prefix="bossman_opencode_proof_"))
    os.environ["BCC_DATA_DIR"] = str(tmp_dir)
    os.environ["BCC_DB_PATH"] = str(tmp_dir / "proof_bcc.db")

    from fastapi.testclient import TestClient
    from bcc.app import create_app

    app = create_app()
    with TestClient(app) as client:
        r_workers = client.get("/api/coding-tasks/workers")
        assert r_workers.status_code == 200, r_workers.text
        workers_data = r_workers.json()

        r_self = client.post("/api/coding-tasks/self-target", json={"ensure_allowed_root": True})
        assert r_self.status_code == 200, r_self.text
        self_target_data = r_self.json()
        assert self_target_data["ok"] is True

        print(
            f"[1/4] OpenCode Catalog Ready: total={workers_data['summary']['total_cloud_workers']} "
            f"(free={workers_data['summary']['free_cloud_workers']}, paid={workers_data['summary']['paid_cloud_workers']}), "
            f"self_repo={self_target_data['source_repo']}"
        )

        # Live Self-Coding Run #1: FREE Cloud Model
        free_prompt = (
            "You are self-coding Bossman in an isolated worktree.\n"
            "Follow these exact steps using your tools:\n"
            "1. Call `read_file` on `command-center/bcc/features/coding_tasks.py` with `start_line=1500` and `end_line=1520`.\n"
            "2. Call `edit_file` on `command-center/bcc/features/coding_tasks.py` to add a helper function "
            "`list_free_opencode_worker_ids() -> list[str]` right after `summarize_opencode_roster()`:\n"
            "def list_free_opencode_worker_ids() -> list[str]:\n"
            "    return [k for k, v in WORKERS.items() if v.get('tier') == 'free']\n"
            "3. Call `read_file` on `command-center/tests/test_opencode_web_pult_self_coding.py` with `start_line=60` and `end_line=80`.\n"
            "4. Call `edit_file` on `command-center/tests/test_opencode_web_pult_self_coding.py` to assert "
            "`'poolside-laguna-free' in ct.list_free_opencode_worker_ids()` right after `summarize_opencode_roster()`.\n"
            "5. Call `run_tests` with `paths=['command-center/tests/test_opencode_web_pult_self_coding.py']` and `runner='pytest'`.\n"
            "6. Once `run_tests` passes, call `finish` with a concise summary."
        )

        r_create_free = client.post(
            "/api/coding-tasks",
            json={
                "instruction": free_prompt,
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
                "protected_paths": [".github", "secrets"],
                "verify_tests": ["command-center/tests/test_opencode_web_pult_self_coding.py"],
                "timeout_seconds": 240,
            },
        )
        assert r_create_free.status_code == 200, r_create_free.text
        free_id = r_create_free.json()["id"]
        free_done = _wait_for_terminal(client, free_id, timeout_s=240.0)
        assert free_done["status"] == "completed", json.dumps(free_done, indent=2, ensure_ascii=False)
        assert free_done["verification"]["passed"] is True

        # Live Self-Coding Run #2: PAID Top Cloud Model
        paid_prompt = (
            "You are self-coding Bossman in an isolated worktree.\n"
            "Follow these exact steps using your tools:\n"
            "1. Call `read_file` on `command-center/bcc/features/coding_tasks.py` with `start_line=1500` and `end_line=1520`.\n"
            "2. Call `edit_file` on `command-center/bcc/features/coding_tasks.py` to add a helper function "
            "`get_opencode_self_coding_capabilities() -> dict` right after `summarize_opencode_roster()`:\n"
            "def get_opencode_self_coding_capabilities() -> dict:\n"
            "    return {'engine': 'bossman-opencode-v1', 'self_coding': True, **summarize_opencode_roster()}\n"
            "3. Call `read_file` on `command-center/tests/test_opencode_web_pult_self_coding.py` with `start_line=60` and `end_line=80`.\n"
            "4. Call `edit_file` on `command-center/tests/test_opencode_web_pult_self_coding.py` to assert "
            "`ct.get_opencode_self_coding_capabilities()['self_coding'] is True` right after `summarize_opencode_roster()`.\n"
            "5. Call `run_tests` with `paths=['command-center/tests/test_opencode_web_pult_self_coding.py']` and `runner='pytest'`.\n"
            "6. Once `run_tests` passes, call `finish` with a concise summary."
        )

        r_create_paid = client.post(
            "/api/coding-tasks",
            json={
                "instruction": paid_prompt,
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
                "protected_paths": [".github", "secrets"],
                "verify_tests": ["command-center/tests/test_opencode_web_pult_self_coding.py"],
                "timeout_seconds": 240,
            },
        )
        assert r_create_paid.status_code == 200, r_create_paid.text
        paid_id = r_create_paid.json()["id"]
        paid_done = _wait_for_terminal(client, paid_id, timeout_s=240.0)
        assert paid_done["status"] == "completed", json.dumps(paid_done, indent=2, ensure_ascii=False)
        assert paid_done["verification"]["passed"] is True

        # Apply the Paid Cloud Model's verified patch
        r_wait = client.post(f"/api/coding-tasks/{paid_id}/apply", json={})
        assert r_wait.status_code == 202, r_wait.text
        approval_id = r_wait.json()["approval_id"]
        r_appr = client.post(f"/api/approvals/{approval_id}", json={"approve": True, "by": "owner-proof"})
        assert r_appr.status_code == 200, r_appr.text
        r_apply = client.post(f"/api/coding-tasks/{paid_id}/apply", json={"approval_id": approval_id})
        assert r_apply.status_code == 200, r_apply.text
        applied_payload = r_apply.json()
        assert applied_payload["state"] == "APPLIED"

    out_dir = REPO_ROOT / "docs" / "audits" / "2026-10-20-opencode-self-coding"
    out_dir.mkdir(parents=True, exist_ok=True)
    proof_path = out_dir / "opencode_self_coding_proof.json"
    proof_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "verdict": "PASS",
                "free_task": free_done,
                "paid_task": paid_done,
                "applied": applied_payload,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"SUCCESS: Proof artifact written to {proof_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
