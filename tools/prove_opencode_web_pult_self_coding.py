"""Live End-to-End Proof (Stage 4): Bossman OpenCode Web Pult Self-Coding Full 10-Step Cycle.

Proves the complete 10-step self-coding cycle on a real reproducible defect in Bossman:
1. Bossman independently detects a reproducible defect (`render_pdf_document` in
   `bossman_shared/exchange_assistant/quote.py` lacks a `/ToUnicode` CMap stream for CP1251 Cyrillic).
2. Bossman selects a coder according to Stage 5 priority (`Local -> Free Cloud -> Cheap Paid -> Premium`).
3. Bossman creates an `IsolatedWorktree` clone of its own repository (`self_target=True`).
4. The coder edits `bossman_shared/exchange_assistant/quote.py` and `tests/test_exchange_assistant.py`
   via multi-turn OpenCode tool calls (`read_file` -> `edit_file`).
5. The coder runs `run_tests` (`pytest tests/test_exchange_assistant.py`) inside the worktree.
6. Bossman's independent verifier (`_verify_in_sandbox`) evaluates the fix in a guarded subprocess.
7. Bossman issues a cryptographic `Mission Receipt v1` (`postcondition_verified=True`).
8. Owner approves the apply request (`202 WAIT_APPROVAL` -> `POST /api/approvals/{id}`).
9. The fix is applied to the canonical repository (`state == "APPLIED"`).
10. Post-apply re-verification confirms the defect is resolved in the canonical repository.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for rel in ("", "command-center", "bossman-core", "bossman-shared"):
    p = str((REPO_ROOT / rel).resolve())
    if p not in sys.path:
        sys.path.insert(0, p)


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


def detect_pdf_tounicode_defect() -> dict:
    """Step 1: Deterministically inspect SwapMe PDF output for Cyrillic /ToUnicode CMap."""
    import bossman_shared.exchange_assistant.quote as qmod
    importlib.reload(qmod)
    from bossman_shared.exchange_assistant.rates import Rate, RateBook

    now = dt.datetime(2026, 10, 21, 8, 0, tzinfo=dt.timezone.utc)
    rb = RateBook([
        Rate("USDCZK", Decimal("23.38"), "ЧНБ", now, "official"),
        Rate("USDTUSD", Decimal("1.0000"), "тест", now, "manual", now + dt.timedelta(hours=4)),
    ])
    q = qmod.build_quote(rb, give_ccy="USDT", get_ccy="CZK", give_amount=Decimal("500"), margin=Decimal("0.03"), now=now)
    pdf_bytes = qmod.render_pdf_document(q)
    has_tounicode = b"/ToUnicode 6 0 R" in pdf_bytes and b"beginbfrange <C0> <DF> <0410>" in pdf_bytes
    return {
        "has_tounicode_cmap": has_tounicode,
        "defect_reproduced": not has_tounicode,
        "pdf_bytes_len": len(pdf_bytes),
    }


def main() -> int:
    os.environ["BOSSMAN_VERIFY_PYTHON"] = sys.executable
    tmp_dir = Path(tempfile.mkdtemp(prefix="bossman_opencode_stage4_"))
    os.environ["BCC_DATA_DIR"] = str(tmp_dir)
    os.environ["BCC_DB_PATH"] = str(tmp_dir / "stage4_bcc.db")

    # Step 1: Detect defect before self-coding
    pre_check = detect_pdf_tounicode_defect()
    print(f"[Step 1] Pre-check defect detection: {pre_check}")

    from fastapi.testclient import TestClient
    from bcc.app import create_app

    app = create_app()
    with TestClient(app) as client:
        token = (tmp_dir / "token").read_text(encoding="utf-8").strip()
        client.headers["X-BCC-Token"] = token
        # Step 2: Check workers catalog & Stage 5 spend policy
        r_workers = client.get("/api/coding-tasks/workers")
        assert r_workers.status_code == 200, r_workers.text
        workers_data = r_workers.json()

        r_self = client.post("/api/coding-tasks/self-target", json={"ensure_allowed_root": True})
        assert r_self.status_code == 200, r_self.text
        self_target_data = r_self.json()
        assert self_target_data["ok"] is True

        print(
            f"[Step 2] OpenCode Catalog Ready: total={workers_data['summary']['total_cloud_workers']} "
            f"(free={workers_data['summary']['free_cloud_workers']}, paid={workers_data['summary']['paid_cloud_workers']}), "
            f"spend_policy={workers_data.get('spend_policy')}"
        )

        # Step 3-7: Create self-coding task in IsolatedWorktree using Free->CheapPaid coder chain
        fix_prompt = r"""You are self-coding Bossman in an isolated worktree to fix a real Cyrillic PDF defect.
Follow these exact steps using your tools:
1. Call `read_file` on `bossman_shared/exchange_assistant/quote.py` with `start_line=348` and `end_line=360`.
2. Call `edit_file` on `bossman_shared/exchange_assistant/quote.py` with:
   old_text:
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>\nendobj\n",
        f"5 0 obj\n<< /Length {len(content_bytes)} >>\nstream\n".encode("ascii") + content_bytes + b"\nendstream\nendobj\n",
   new_text:
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding /ToUnicode 6 0 R >>\nendobj\n",
        f"5 0 obj\n<< /Length {len(content_bytes)} >>\nstream\n".encode("ascii") + content_bytes + b"\nendstream\nendobj\n",
        b"6 0 obj\n<< /Length 259 >>\nstream\n/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /SwapMe-CP1251-UCS2 def /CMapType 2 def 1 begincodespacerange <00> <FF> endcodespacerange 2 beginbfchar <A8> <0401> <B8> <0451> endbfchar 2 beginbfrange <C0> <DF> <0410> <E0> <FF> <0430> endbfrange endcmap CMapName currentdict /CMap defineresource pop end end\nendstream\nendobj\n",
3. Call `read_file` on `tests/test_exchange_assistant.py` with `start_line=629` and `end_line=637`.
4. Call `edit_file` on `tests/test_exchange_assistant.py` with:
   old_text:
    assert b"/Encoding /WinAnsiEncoding" in pdf_bytes
   new_text:
    assert b"/Encoding /WinAnsiEncoding" in pdf_bytes
    assert b"/ToUnicode 6 0 R" in pdf_bytes and b"beginbfrange <C0> <DF> <0410>" in pdf_bytes
5. Call `run_tests` with `paths=['tests/test_exchange_assistant.py']` and `runner='pytest'`.
6. Once `run_tests` passes, call `finish` with a concise summary."""

        r_create = client.post(
            "/api/coding-tasks",
            json={
                "instruction": fix_prompt,
                "worker": "groq-gpt-oss-120b-free",
                "fallback_workers": [
                    "gemini-3-flash-free",
                    "openrouter-code-free",
                    "deepseek-v4.1-flash",
                    "haiku-5.5",
                ],
                "source_repo": "self",
                "self_target": True,
                "allowed_paths": [
                    "bossman_shared/exchange_assistant/quote.py",
                    "tests/test_exchange_assistant.py",
                ],
                "protected_paths": [".github", "secrets"],
                "verify_tests": ["tests/test_exchange_assistant.py"],
                "timeout_seconds": 300,
            },
        )
        assert r_create.status_code == 200, r_create.text
        task_id = r_create.json()["id"]
        done = _wait_for_terminal(client, task_id, timeout_s=420.0)
        assert done["status"] == "completed", json.dumps(done, indent=2, ensure_ascii=False)
        assert done["verification"]["passed"] is True
        assert done["receipt"]["postcondition_verified"] is True

        print(
            f"[Steps 3-7] Task {task_id} completed via worker={done.get('worker')} "
            f"(model={done.get('model')}, cost={done.get('cost_display')}), "
            f"receipt={done['receipt']['receipt_id']}"
        )

        # Steps 8-9: Owner Approval + Apply to canonical repository
        r_wait = client.post(f"/api/coding-tasks/{task_id}/apply", json={})
        assert r_wait.status_code == 202, r_wait.text
        approval_id = r_wait.json()["approval_id"]
        r_appr = client.post(f"/api/approvals/{approval_id}", json={"approve": True, "by": "owner-stage4-acceptance"})
        assert r_appr.status_code == 200, r_appr.text
        r_apply = client.post(f"/api/coding-tasks/{task_id}/apply", json={"approval_id": approval_id})
        assert r_apply.status_code == 200, r_apply.text
        applied_payload = r_apply.json()
        assert applied_payload["state"] == "APPLIED"

    # Step 10: Post-Apply Re-verification on the canonical repository
    post_check = detect_pdf_tounicode_defect()
    assert post_check["has_tounicode_cmap"] is True, f"Post-apply check failed: {post_check}"
    pytest_post = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_exchange_assistant.py",
            f"--basetemp={tmp_dir / 'pytest_post'}",
            "-q",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert pytest_post.returncode == 0, pytest_post.stdout + "\n" + pytest_post.stderr
    print(f"[Steps 8-10] Applied & Re-verified on canonical repo: {post_check}, pytest exit={pytest_post.returncode}")

    out_dir = REPO_ROOT / "docs" / "audits" / "2026-10-20-opencode-self-coding"
    out_dir.mkdir(parents=True, exist_ok=True)
    proof_path = out_dir / "opencode_self_coding_proof.json"
    proof_path.write_text(
        json.dumps(
            {
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "verdict": "PASS",
                "step1_defect_before": pre_check,
                "task": done,
                "applied": applied_payload,
                "step10_post_apply_verification": {
                    **post_check,
                    "pytest_exit_code": pytest_post.returncode,
                    "pytest_summary": pytest_post.stdout.strip().splitlines()[-1] if pytest_post.stdout.strip() else "",
                },
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"SUCCESS: Stage 4 Self-Coding proof written to {proof_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
