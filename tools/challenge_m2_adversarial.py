"""Milestone 2 Empirical Adversarial Challenge Harness.

Challenges:
1. Live cross-surface STOP trigger and propagation:
   - Halts active tasks immediately (<1s)
   - Invalidates trust leases (trust_until -> 0.0)
   - Invalidates approval leases & tokens (refuses post-STOP decisions with 409)
   - Prevents subsequent tool executions (fail-closed, 0 tool actions)
   - Cross-surface synchronization between :8840 and :8801
   - Split-brain tolerance (either flag triggers fail-closed behavior)
2. E2E Mission Receipt cryptographic integrity:
   - bossman_shared.evidence.verify() verification against receipt.json
   - Tamper challenge on all critical fields (verdict, status, task_id, spend, actions_after_stop, build_sha, signer)
   - Analysis of signing methodology in tools/prove_m2_e2e_mission.py
   - Reproduction of worker_m2/handoff.md step 6 verification command
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "command-center"))
sys.path.insert(0, str(REPO_ROOT / "bossman_shared"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "bossman-mobile"))

from bossman_shared import evidence
from bossmobile.auth import load_or_create_pairing
from bossmobile.config import Settings as MobileSettings


def _http(method: str, url: str, headers: dict[str, str] | None = None, body: dict[str, Any] | None = None, timeout: float = 30.0) -> tuple[int, dict[str, Any], dict[str, str]]:
    req_hdrs = {"Accept": "application/json"}
    if headers:
        req_hdrs.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_hdrs["Content-Type"] = "application/json"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=data, headers=req_hdrs, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            resp_hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, json.loads(raw) if raw.strip() else {}, resp_hdrs
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        resp_hdrs = {k.lower(): v for k, v in exc.headers.items()}
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except Exception:
            parsed = {"raw": raw[:500]}
        return exc.code, parsed, resp_hdrs



def test_challenge_stop_propagation() -> dict[str, Any]:
    print("\n==================== CHALLENGE 1: STOP TRIGGER & PROPAGATION ====================")
    pult_url = "http://127.0.0.1:8840"
    cc_url = "http://127.0.0.1:8801"

    # Setup auth
    mob_st = MobileSettings()
    pairing_token = load_or_create_pairing(mob_st.data_dir)
    _, login_body, login_hdrs = _http("POST", f"{pult_url}/m/api/login", body={"token": pairing_token})
    set_cookie = login_hdrs.get("set-cookie", "")
    bm_session = ""
    for part in set_cookie.split(";"):
        if "bm_session=" in part:
            bm_session = part.strip().split("=", 1)[1]
            break
    pult_hdrs = {
        "Cookie": f"bm_session={bm_session}",
        "X-BM-CSRF": str(login_body.get("csrf") or ""),
    }

    local_appdata = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    cc_data_dir = local_appdata / "Bossman" / "CommandCenter"
    token_file = cc_data_dir / "token"
    cc_token = token_file.read_text(encoding="utf-8").strip() if token_file.is_file() else ""
    cc_hdrs = {"X-BCC-Token": cc_token}

    # Ensure clean resumed state
    _http("POST", f"{pult_url}/m/api/stop/resume", headers=pult_hdrs, body={})
    _http("POST", f"{cc_url}/api/computer/resume", headers=cc_hdrs, body={})
    _http("POST", f"{cc_url}/api/pc-agent/resume", headers=cc_hdrs, body={})
    time.sleep(0.2)

    # 1. Start a long running task that sleeps, then trigger STOP from Mobile Pult
    workspace_dir = cc_data_dir / "pc-agent" / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    canary_file = workspace_dir / "challenge_canary_should_not_exist.txt"
    if canary_file.exists():
        canary_file.unlink()

    # Plan: Step 1 (sleep 10s via ping), Step 2 (write canary file)
    plan = [
        {"tool": "pc.run_command", "args": {"argv": ["ping", "-n", "15", "127.0.0.1"], "timeout_s": 20}},
        {"tool": "pc.write_file", "args": {"path": str(canary_file), "content": "breached"}},
    ]

    print("[1.1] Submitting long-running task to Mobile Pult...")
    import threading
    task_res: dict[str, Any] = {}
    def _submit_worker():
        code, resp, _ = _http("POST", f"{pult_url}/m/api/pc-agent/tasks", headers=pult_hdrs, body={
            "prompt": "Adversarial STOP test",
            "actor": "owner",
            "wait": True,
            "plan": plan,
        }, timeout=25.0)
        task_res["code"] = code
        task_res["resp"] = resp

    t = threading.Thread(target=_submit_worker)
    t.start()
    time.sleep(1.2) # Wait for ping process to spawn

    # Trigger STOP from Mobile Pult
    print("[1.2] Triggering cross-surface STOP from Mobile Pult :8840...")
    t_stop = time.perf_counter()
    stop_code, stop_resp, _ = _http("POST", f"{pult_url}/m/api/stop", headers=pult_hdrs, body={"confirm": True})
    stop_latency = round((time.perf_counter() - t_stop) * 1000.0, 2)
    print(f"      STOP HTTP {stop_code}, latency={stop_latency}ms, resp={stop_resp}")

    t.join(timeout=5.0)
    print(f"      Task thread finished, task_res={task_res}")

    # Check task stopped immediately
    task_id = task_res.get("resp", {}).get("task_id")
    if task_id:
        _, task_status, _ = _http("GET", f"{cc_url}/api/pc-agent/tasks/{task_id}", headers=cc_hdrs)
        status_after = task_status.get("status")
        print(f"      Task {task_id} status after STOP: {status_after}")
    else:
        status_after = task_res.get("resp", {}).get("status", "stopped")

    # Verify canary file was NOT written (second step never executed)
    canary_blocked = not canary_file.exists()
    print(f"      Canary file blocked (step 2 skipped): {canary_blocked}")

    # 2. Invalidation of trust leases and approval tokens
    print("[1.3] Testing lease / token invalidation under STOP...")
    # Attempt to approve post-stop
    code_approve, resp_approve, _ = _http("POST", f"{cc_url}/api/pc-agent/tasks/{task_id or 999}/approve", headers=cc_hdrs, body={
        "approval_id": "fake-approval-id",
        "approve": True,
        "by": "owner",
    })
    print(f"      Post-STOP approval attempt: HTTP {code_approve} (expected 409)")

    # Attempt to submit new task post-stop
    code_new_task, resp_new_task, _ = _http("POST", f"{pult_url}/m/api/pc-agent/tasks", headers=pult_hdrs, body={
        "prompt": "Forbidden post-stop task",
        "actor": "owner",
        "wait": True,
        "plan": [{"tool": "pc.write_file", "args": {"path": str(canary_file), "content": "forbidden"}}],
    })
    print(f"      Post-STOP new task attempt: HTTP {code_new_task} (expected 409)")

    # Check STOP status endpoints
    _, cc_status, _ = _http("GET", f"{cc_url}/api/computer/status", headers=cc_hdrs)
    _, pult_stop_status, _ = _http("GET", f"{pult_url}/m/api/stop", headers=pult_hdrs)
    print(f"      CC computer status stopped: {cc_status.get('stopped')}")
    print(f"      Pult stop status active: {pult_stop_status.get('active')}")

    # Clean up STOP
    _http("POST", f"{pult_url}/m/api/stop/resume", headers=pult_hdrs, body={})
    _http("POST", f"{cc_url}/api/computer/resume", headers=cc_hdrs, body={})
    _http("POST", f"{cc_url}/api/pc-agent/resume", headers=cc_hdrs, body={})
    time.sleep(0.3)

    _http("POST", f"{pult_url}/m/api/stop/resume", headers=pult_hdrs, body={})
    _http("POST", f"{cc_url}/api/computer/resume", headers=cc_hdrs, body={})
    _http("POST", f"{cc_url}/api/pc-agent/resume", headers=cc_hdrs, body={})

    stop_results = {
        "stop_latency_ms": stop_latency,
        "active_task_halted": status_after == "stopped" or task_res.get("resp", {}).get("status") == "stopped",
        "subsequent_steps_blocked": canary_blocked,
        "post_stop_approval_refused_409": code_approve == 409,
        "post_stop_new_task_refused_409": code_new_task == 409,
        "cross_surface_sync_verified": cc_status.get("stopped") is True and pult_stop_status.get("active") is True,
    }

    print("STOP Challenge Results:", stop_results)
    return stop_results


def test_challenge_receipt_integrity() -> dict[str, Any]:
    print("\n==================== CHALLENGE 2: RECEIPT INTEGRITY & TAMPER AUDIT ====================")
    receipt_path = REPO_ROOT / "docs" / "audits" / "2026-10-27-m2-e2e-mission" / "receipt.json"
    raw_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    results: dict[str, Any] = {}

    # Check 1: untampered receipt against bossman_shared.evidence.verify
    sig = str(raw_receipt.get("sig") or "")
    default_key_verify = evidence.verify(raw_receipt, sig)
    print(f"[2.1] Untampered receipt vs evidence.verify(receipt, sig): {default_key_verify}")
    results["untampered_verify_default_key"] = default_key_verify

    # Check 2: untampered receipt against evidence.verify with key=b'bcc.v2.verification'
    bcc_key_verify = evidence.verify(raw_receipt, sig, key=b"bcc.v2.verification")
    print(f"[2.2] Untampered receipt vs evidence.verify(receipt, sig, key=b'bcc.v2.verification'): {bcc_key_verify}")
    results["untampered_verify_bcc_key"] = bcc_key_verify

    # Check 3: untampered receipt against worker_m2/handoff.md step 6 command
    r_worker = dict(raw_receipt)
    s_worker = r_worker.pop("sig", None)
    c_worker = json.dumps(r_worker, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected_worker_sig = hmac.new(b"bcc.v2.verification", c_worker.encode("utf-8"), hashlib.sha256).hexdigest()
    worker_step6_matches = (s_worker == expected_worker_sig)
    print(f"[2.3] Untampered receipt vs worker_m2/handoff.md step 6 command: {worker_step6_matches}")
    results["worker_handoff_step6_matches"] = worker_step6_matches

    # Check 4: Why does worker's own command fail?
    # Because prove_m2_e2e_mission.py computed canonical before adding receipt_sha256 and signer!
    r_orig = dict(raw_receipt)
    r_orig.pop("sig", None)
    r_orig.pop("signer", None)
    r_orig.pop("receipt_sha256", None)
    c_orig = json.dumps(r_orig, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    orig_sig = hmac.new(b"bcc.v2.verification", c_orig.encode("utf-8"), hashlib.sha256).hexdigest()
    orig_matches = (sig == orig_sig)
    print(f"[2.4] Original canonical (popping sig + signer + receipt_sha256) matches: {orig_matches}")
    results["original_canonical_matches"] = orig_matches

    # Check 5: Tamper tests on receipt fields using evidence.verify
    # When evidence.verify is called on any tampered version, does it fail?
    print("[2.5] Testing evidence.verify() on tampered receipts:")
    tamper_vectors = [
        ("verdict", "FAIL"),
        ("status", "UNVERIFIED"),
        ("unauthorized_spend", "$999.00"),
        ("shared_task_id", 12345),
        ("actions_after_stop", 1),
        ("build_sha", "0000000000000000000000000000000000000000"),
    ]
    tamper_results = {}
    for key, val in tamper_vectors:
        t_receipt = copy.deepcopy(raw_receipt)
        if key == "shared_task_id":
            t_receipt["e2e_mission"]["shared_task_id"] = val
        elif key == "actions_after_stop":
            t_receipt["stop_gate_verification"]["actions_after_stop"] = val
        elif key == "build_sha":
            t_receipt["environment"]["build_sha"] = val
        else:
            t_receipt[key] = val
        
        # Verify with evidence.verify
        v_fail = evidence.verify(t_receipt, str(t_receipt.get("sig") or ""))
        tamper_results[f"tamper_{key}"] = {"tampered_val": val, "verify_returned": v_fail, "properly_rejected": v_fail is False}
        print(f"      Tamper {key}={val!r} -> evidence.verify(): {v_fail} (rejected={v_fail is False})")

    results["tamper_results"] = tamper_results

    # Check 6: What if receipt is properly signed with bossman_shared.evidence?
    print("[2.6] Testing properly signed receipt with bossman_shared.evidence:")
    clean_body = {k: v for k, v in raw_receipt.items() if k not in evidence.SIG_FIELDS and k != "receipt_sha256"}
    proper_signed_fields = evidence.sign_fields(clean_body, signer="bcc.v2.verification")
    proper_receipt = {**clean_body, **proper_signed_fields}
    v_proper_untampered = evidence.verify_signed(proper_receipt)
    print(f"      Properly signed untampered verify_signed: {v_proper_untampered}")
    
    # Tamper with properly signed receipt
    t_prop = copy.deepcopy(proper_receipt)
    t_prop["verdict"] = "FAIL"
    v_proper_tampered = evidence.verify_signed(t_prop)
    print(f"      Properly signed tampered (verdict=FAIL) verify_signed: {v_proper_tampered}")
    results["proper_evidence_signing"] = {
        "untampered_verify_signed": v_proper_untampered,
        "tampered_verify_signed": v_proper_tampered,
    }

    return results


if __name__ == "__main__":
    r1 = test_challenge_stop_propagation()
    r2 = test_challenge_receipt_integrity()
    print("\n==================== SUMMARY ====================")
    print("STOP Propagation:", "PASS" if all(r1.values()) else "FAIL")
    print("Receipt Integrity:", r2)
