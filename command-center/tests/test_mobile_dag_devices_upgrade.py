"""Comprehensive test suite for Mobile Pult 2x, Stars BL-121, Swarm DAG, and Devices (Branch 1, 2, 3, 8/9, 15).

Tests:
1. Stars BL-121 two-phase reservation, auto-refund on delivery failure, downtime reconciliation.
2. Mobile Pult :8840 token streaming (run.token_delta) and 4 protected endpoints:
   - GET /m/api/pc/screen-stream
   - POST /m/api/pc/touch-preview
   - POST /m/api/voice/turn
   - POST /m/api/tasks/{task_id}/rollback & GET /m/api/tasks/{task_id}/blast-radius
3. AppActionPassport contract, validation across child apps, T3 secret leak prohibition, SHA-256 receipts.
4. SwarmDAGPlanner, CrossFamilyVerifierEngine (proposer != verifier), memory_retrieval_v16 context packing,
   and POST /api/agent/swarm-dag endpoint.
5. RemoteWorkerNode with HMAC-SHA256, T3 forwarding prohibition, artifact SHA-256 validation,
   cascading STOP <= 2.0s, and heartbeat lease expiration.
6. VirtualUVCCameraProbe Stage 6 camera verification.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Repositories on sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "command-center") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "command-center"))
if str(REPO_ROOT / "apps" / "bossman-mobile") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "bossman-mobile"))

from bcc.devices.worker_node import (
    ArtifactIntegrityError,
    CASCADING_STOP_TIMEOUT_SECONDS,
    NodeResourceState,
    RemoteWorkerNode,
    ResourceQueue,
    T3SecretForwardingProhibitedError,
    VirtualUVCCameraProbe,
    WorkerClusterCoordinator,
)
from bcc.features.memory_retrieval_v16 import MemoryCandidate, Signals
from bcc.pit.stars_ledger import DOCS30, StarsLedger
from bcc.telegram_companion.stars import (
    TwoPhaseStarReservation,
    reconcile_star_transactions_after_downtime,
    refund_quota_on_delivery_failure,
    reserve_quota_two_phase,
)
from bcc.v2.app_passport import (
    AppActionPassport,
    PassportValidationError,
    T3SecretLeakError,
    normalize_app_name,
)
from bcc.v2.swarm_dag import (
    CrossFamilyVerifierEngine,
    CrossFamilyViolationError,
    SwarmDAGPlanner,
)
from bossmobile.api import PRIVACY_BLACKLIST, STREAM_FIELDS, STREAM_KINDS, create_app
from bossmobile.config import Settings


# ---------------------------------------------------------------------------
# Fixture: Mock Mobile App Environment
# ---------------------------------------------------------------------------
@pytest.fixture
def mobile_env(tmp_path):
    tok = tmp_path / "bcc-token"
    tok.write_text("TEST-OWNER-TOKEN-12345", encoding="utf-8")
    st = Settings(
        bossman_url="http://127.0.0.1:8801",
        token_file=tok,
        data_dir=tmp_path / "mobile",
        stop_file=tmp_path / "STOP",
        niche_dir=tmp_path / "niche",
    )
    app = create_app(st, pairing_token="pair-secret-42")
    client = TestClient(app, base_url="http://127.0.0.1:8840")
    # Login to acquire session cookie and CSRF token
    login_resp = client.post("/m/api/login", json={"token": "pair-secret-42"})
    assert login_resp.status_code == 200, login_resp.text
    csrf = login_resp.json()["csrf"]
    return {"client": client, "csrf": csrf, "app": app, "tmp_path": tmp_path}


# ---------------------------------------------------------------------------
# 1. Stars Two-Phase & BL-121 Tests
# ---------------------------------------------------------------------------
def test_stars_two_phase_reservation_commit(tmp_path):
    ledger = StarsLedger(tmp_path / "stars.sqlite3")
    key = "person_user_1"
    # Create order and record payment to grant quota
    payload = ledger.create_order(key, 101, now=100.0)
    outcome = ledger.record_payment(
        person_key=key,
        user_id=101,
        payload=payload,
        charge_id="ch_101",
        currency="XTR",
        total_amount=DOCS30.stars,
        now=105.0,
    )
    assert outcome.status == "granted"

    ent_before = ledger.entitlement(key, now=110.0)
    assert ent_before["paid_requests_left"] == DOCS30.requests

    # Phase 1: Reserve
    res = reserve_quota_two_phase(ledger, key, kind="doc_query", now=110.0)
    assert res.ok is True
    assert res.usage_id > 0
    assert res.pool == "paid"
    assert res.committed is False

    # Phase 2a: Commit
    res.commit()
    assert res.committed is True

    ent_after = ledger.entitlement(key, now=110.0)
    assert ent_after["paid_requests_left"] == DOCS30.requests - 1


def test_stars_two_phase_reservation_refund_on_delivery_failure(tmp_path):
    ledger = StarsLedger(tmp_path / "stars.sqlite3")
    key = "person_user_2"
    payload = ledger.create_order(key, 102, now=100.0)
    ledger.record_payment(
        person_key=key,
        user_id=102,
        payload=payload,
        charge_id="ch_102",
        currency="XTR",
        total_amount=DOCS30.stars,
        now=105.0,
    )

    # Reserve quota
    res = reserve_quota_two_phase(ledger, key, kind="doc_query", now=110.0)
    assert res.ok is True
    u_id = res.usage_id

    # Simulate Telegram network outage during delivery: auto-refund without burning daily void quota
    refunded = res.refund(reason="telegram_delivery_timeout", now=112.0)
    assert refunded is True
    assert res.refunded is True

    # Quota should be fully restored
    ent = ledger.entitlement(key, now=115.0)
    assert ent["paid_requests_left"] == DOCS30.requests


def test_stars_two_phase_context_manager(tmp_path):
    ledger = StarsLedger(tmp_path / "stars.sqlite3")
    key = "person_user_ctx"
    payload = ledger.create_order(key, 103, now=100.0)
    ledger.record_payment(
        person_key=key,
        user_id=103,
        payload=payload,
        charge_id="ch_103",
        currency="XTR",
        total_amount=DOCS30.stars,
        now=105.0,
    )

    # 1. Success block auto-commits
    with reserve_quota_two_phase(ledger, key, now=110.0) as res:
        assert res.ok is True
        # Do turn work...
    assert res.committed is True

    # 2. Exception block auto-refunds
    try:
        with reserve_quota_two_phase(ledger, key, now=111.0) as res2:
            assert res2.ok is True
            raise ConnectionError("Telegram API dropped connection")
    except ConnectionError:
        pass
    assert res2.refunded is True


@pytest.mark.asyncio
async def test_stars_reconcile_downtime_bl_121(tmp_path):
    ledger = StarsLedger(tmp_path / "stars.sqlite3")

    # Mock Telegram Adapter returning missed payments after bot downtime > 24 hours
    class FakeTelegramAdapter:
        async def payment_call(self, method: str, payload: dict) -> dict:
            assert method == "getStarTransactions"
            return {
                "ok": True,
                "result": {
                    "transactions": [
                        {
                            "id": "tx_missed_1",
                            "amount": DOCS30.stars,
                            "source": {"id": 999},
                            "invoice_payload": "reconciled_order_1",
                            "person_key": "user_reconciled_999",
                        },
                        {
                            "id": "tx_missed_refund",
                            "amount": -DOCS30.stars,
                            "source": {"id": 888},
                            "is_refund": True,
                        },
                    ]
                },
            }

    # Downtime > 24h
    now = time.time()
    last_online = now - 90000.0  # 25 hours ago

    report = await reconcile_star_transactions_after_downtime(
        adapter=FakeTelegramAdapter(),
        ledger=ledger,
        last_online_ts=last_online,
        now=now,
    )

    assert report["reconciled"] is True
    assert report["status"] == "PASS"
    assert report["transactions_checked"] == 2
    assert report["recovered_payments"] == 1
    assert report["recovered_refunds"] == 1


# ---------------------------------------------------------------------------
# 2. Mobile Pult :8840 Token Streaming & 4 Protected Endpoints Tests
# ---------------------------------------------------------------------------
def test_mobile_stream_kinds_includes_token_delta():
    assert "run.token_delta" in STREAM_KINDS
    assert "delta" in STREAM_FIELDS
    assert "token" in STREAM_FIELDS


def test_mobile_screen_stream_auth_and_privacy(mobile_env, monkeypatch):
    from PIL import Image, ImageGrab

    client = mobile_env["client"]
    # 1. Unauthenticated request without cookie -> 401
    unauth_client = TestClient(mobile_env["app"], base_url="http://127.0.0.1:8840")
    r_unauth = unauth_client.get("/m/api/pc/screen-stream")
    assert r_unauth.status_code == 401

    # 2. Authenticated request with clean desktop -> 200 with screen preview and coordinates
    monkeypatch.setattr("bossmobile.api.PRIVACY_BLACKLIST", ("zzz-no-such-window-title-zzz",))
    monkeypatch.setattr(ImageGrab, "grab", lambda *a, **k: Image.new("RGB", (1920, 1080), (40, 80, 120)))
    r_auth = client.get("/m/api/pc/screen-stream")
    assert r_auth.status_code == 200
    data = r_auth.json()
    assert data["ok"] is True
    assert "coordinates_space" in data
    assert data["coordinates_space"]["max_x"] >= 1280
    assert "touch_to_act_enabled" in data
    assert data["privacy_filtered"] is False

    # 3. Verify privacy blacklist contains critical apps
    for app in ("1password", "bitwarden", "keepass", "bank", "metamask"):
        assert app in PRIVACY_BLACKLIST


def test_mobile_touch_preview(mobile_env):
    client = mobile_env["client"]
    csrf = mobile_env["csrf"]

    # 1. Missing CSRF header -> 403
    r_no_csrf = client.post("/m/api/pc/touch-preview", json={"x": 500, "y": 300})
    assert r_no_csrf.status_code == 403

    # 2. Missing coordinates -> 422
    r_missing = client.post("/m/api/pc/touch-preview", json={}, headers={"X-BM-CSRF": csrf})
    assert r_missing.status_code == 422

    # 3. Valid tap coordinates -> 200 with preview card
    r_ok = client.post(
        "/m/api/pc/touch-preview",
        json={"x": 640.5, "y": 480.0, "tier": "T2"},
        headers={"X-BM-CSRF": csrf},
    )
    assert r_ok.status_code == 200
    body = r_ok.json()
    assert body["ok"] is True
    assert body["tier"] in ("T1", "T2")
    assert "action_preview" in body
    assert body["requires_approval"] is True
    assert body["target"]["x"] == 640.5
    assert body["target"]["y"] == 480.0


def test_mobile_voice_turn(mobile_env):
    """Audit 28.10: this used to expect a fabricated task id «voice_...» and a canned reply «Выполняю в контуре» for any input."""
    client = mobile_env["client"]
    csrf = mobile_env["csrf"]

    r_no_csrf = client.post("/m/api/voice/turn", json={"text": "Привет, Джефф"})
    assert r_no_csrf.status_code == 403

    r_ok = client.post("/m/api/voice/turn", json={"text": "Запусти аудит безопасности", "mode": "push_to_talk"},
                       headers={"X-BM-CSRF": csrf})
    assert r_ok.status_code == 200
    body = r_ok.json()
    assert body["ok"] is True and body["transcript"] == "Запусти аудит безопасности"
    assert body["executed"] is False and body["task_id"] is None and body["reply"] == ""
    assert "Выполняю" not in r_ok.text

    assert client.post("/m/api/voice/turn", json={}, headers={"X-BM-CSRF": csrf}).status_code == 422


def test_mobile_task_rollback_and_blast_radius(mobile_env):
    """Audit 28.10: these routes used to answer «rolled_back» and «no network, $0, no files» for ANY id and ANY failure."""
    client = mobile_env["client"]
    csrf = mobile_env["csrf"]

    # an id that is not a PC-agent task number never reaches Bossman and never gets an invented success
    for bad in ("task_test_99", "7;rm", "abc"):
        assert client.get(f"/m/api/tasks/{bad}/blast-radius").status_code == 422
        r = client.post(f"/m/api/tasks/{bad}/rollback", headers={"X-BM-CSRF": csrf})
        assert r.status_code == 422 and "rolled_back" not in r.text

    assert client.post("/m/api/tasks/7/rollback").status_code == 403           # CSRF is still required

    # a numeric id goes to Bossman; whatever it answers (or its absence) is passed through, never replaced by zeros
    br = client.get("/m/api/tasks/7/blast-radius")
    assert br.status_code in (200, 501, 502), br.text
    if br.status_code != 200:
        for invented in ("network_used", "cost_usd", "affected_files"):
            assert invented not in br.text


# ---------------------------------------------------------------------------
# 3. AppActionPassport Contract Tests
# ---------------------------------------------------------------------------
def test_app_action_passport_contract():
    passport = AppActionPassport(
        task_id="task_app_01",
        app_name="War Terminal",
        action_type="order_execution",
        payload={"pair": "SOL/USDC", "amount": 10.5},
    )
    assert passport.app_name == "war_terminal"
    assert len(passport.sha256_receipt) == 64
    assert passport.verify_receipt() is True
    assert passport.is_stopped() is False


def test_app_action_passport_all_child_apps():
    apps = [
        "War Terminal",
        "Social Farm",
        "Accountant",
        "Shorts Factory",
        "BossBlocks",
        "Poker Vision",
    ]
    for app in apps:
        norm = normalize_app_name(app)
        p = AppActionPassport(
            task_id=f"task_{norm}",
            app_name=app,
            action_type="ping",
            payload={"test": 1},
        )
        assert p.app_name == norm


def test_app_action_passport_t3_secret_leak_forbidden():
    with pytest.raises(T3SecretLeakError):
        AppActionPassport(
            task_id="task_leak",
            app_name="Accountant",
            action_type="transfer",
            payload={"bank_token": "secret_bank_key_123"},
        )

    with pytest.raises(T3SecretLeakError):
        AppActionPassport(
            task_id="task_leak_2",
            app_name="War Terminal",
            action_type="sign",
            payload={"private_key": "my_solana_private_key"},
        )


def test_app_action_passport_stop_check():
    stopped = False
    passport = AppActionPassport(
        task_id="task_stop_check",
        app_name="Social Farm",
        action_type="publish_post",
        payload={"content": "hello"},
        stop_check=lambda: stopped,
    )
    assert passport.is_stopped() is False
    passport.assert_not_stopped()

    # Trigger STOP
    stopped = True
    assert passport.is_stopped() is True
    with pytest.raises(RuntimeError, match="STOP triggered"):
        passport.assert_not_stopped()


# ---------------------------------------------------------------------------
# 4. Swarm DAG Planner & Cross-Family Verifier Tests
# ---------------------------------------------------------------------------
def test_cross_family_verifier_success():
    verifier = CrossFamilyVerifierEngine(verifier_family="qwen")
    receipt = verifier.verify(
        subtask_id="sub_1",
        proposer_family="gemma",
        proposal={"code": "def solve(): return 42"},
    )
    assert receipt["passed"] is True
    assert receipt["proposer_family"] == "gemma"
    assert receipt["verifier_family"] == "qwen"
    assert len(receipt["sha256_verdict"]) == 64
    # audit 28.10: without a checker this is a structural check, and the verdict says it is not an independent judgement
    assert receipt["kind"] == "structural_only" and receipt["independent"] is False


def test_cross_family_verifier_uses_a_real_checker_when_given():
    verifier = CrossFamilyVerifierEngine(verifier_family="qwen")
    bad = verifier.verify(subtask_id="s", proposer_family="gemma", proposal={"answer": 41}, custom_checker=lambda p: p["answer"] == 42)
    assert bad["passed"] is False and bad["kind"] == "custom_checker" and bad["independent"] is True
    good = verifier.verify(subtask_id="s", proposer_family="gemma", proposal={"answer": 42}, custom_checker=lambda p: p["answer"] == 42)
    assert good["passed"] is True and good["independent"] is True


def test_cross_family_verifier_violation_raises():
    verifier = CrossFamilyVerifierEngine(verifier_family="gemma")
    with pytest.raises(CrossFamilyViolationError):
        verifier.verify(
            subtask_id="sub_2",
            proposer_family="gemma",
            proposal={"code": "test"},
        )


def _candidates():
    return [
        MemoryCandidate(ref="arch_doc_1", tokens=120, signals=Signals(semantic=0.9, verified=0.8), mandatory=True),
        MemoryCandidate(ref="recent_fix", tokens=80, signals=Signals(semantic=0.7, recency=0.9)),
    ]


@pytest.mark.asyncio
async def test_swarm_dag_without_an_executor_is_plan_only_and_claims_nothing():
    """Audit 28.10: this used to «execute» three fixed subtasks as a mock and report SUCCEEDED / cross_family_verified True."""
    planner = SwarmDAGPlanner(proposer_family="gemma", verifier_family="qwen", max_repairs=2)
    receipt = await planner.run_dag(goal="Upgrade mobile pult streaming to 250ms", candidates=_candidates())
    assert receipt.status == "PLAN_ONLY" and receipt.executed is False and receipt.verification_kind == "none"
    assert receipt.dag_nodes == 3 and receipt.completed_nodes == 0
    assert receipt.cross_family_verified is False
    assert (receipt.proposer_family, receipt.verifier_family) == ("gemma", "qwen")
    assert receipt.context_tokens_used == 200 and len(receipt.sha256_receipt) == 64


@pytest.mark.asyncio
async def test_swarm_dag_runs_only_what_an_executor_does_and_is_cross_verified_only_by_a_real_checker():
    from bcc.v2.swarm_dag import SwarmExecutionError
    planner = SwarmDAGPlanner(proposer_family="gemma", verifier_family="qwen", max_repairs=1)
    ran = []

    async def executor(subtask):
        ran.append(subtask.id)
        return {"output": f"did {subtask.id}"}

    structural = await planner.run_dag(goal="g", candidates=_candidates(), executor_fn=executor)
    assert ran == ["task_1_prep", "task_2_execute", "task_3_finalize"]
    assert structural.status == "SUCCEEDED" and structural.completed_nodes == 3 and structural.executed is True
    assert structural.cross_family_verified is False and structural.verification_kind == "structural_only"

    checked = await planner.run_dag(goal="g", candidates=_candidates(), executor_fn=executor,
                                    checker_fn=lambda p: str(p.get("output", "")).startswith("did "))
    assert checked.cross_family_verified is True and checked.verification_kind == "custom_checker"

    with pytest.raises(SwarmExecutionError):                 # a checker that rejects exhausts the repairs and the DAG fails
        await planner.run_dag(goal="g", candidates=_candidates(), executor_fn=executor, checker_fn=lambda p: False)


@pytest.mark.asyncio
async def test_execute_subtask_without_an_executor_refuses_to_mock():
    from bcc.v2.swarm_dag import SwarmNotWired, SwarmSubtask
    sub = SwarmSubtask(id="t", title="t")
    with pytest.raises(SwarmNotWired):
        await SwarmDAGPlanner().execute_subtask(sub)
    assert sub.status == "NOT_EXECUTED"


def test_agent_opencode_swarm_dag_endpoint():
    from bcc.features.agent_opencode import router
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    resp = client.post(
        "/api/agent/swarm-dag",
        json={
            "goal": "Verify cross-family VerifierEngine integration",
            "proposer_family": "gemma",
            "verifier_family": "qwen",
            "budget_tokens": 2048,
            "context_candidates": [
                {"ref": "dag_spec_v1", "tokens": 60, "signals": {"semantic": 0.85, "verified": 0.9}, "mandatory": True}
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # audit 28.10: no executor is wired to the endpoint, so it returns a PLAN, not a success
    assert data["ok"] is False and data["status"] == "PLAN_ONLY" and data["executed"] is False
    assert data["completed_nodes"] == 0 and data["cross_family_verified"] is False and data["verification_kind"] == "none"
    assert data["proposer_family"] == "gemma" and data["verifier_family"] == "qwen"
    assert data["dag_nodes"] == 3 and len(data["sha256_receipt"]) == 64


# ---------------------------------------------------------------------------
# 5. RemoteWorkerNode & Cascading STOP <= 2.0s Tests
# ---------------------------------------------------------------------------
def test_remote_worker_node_auth_and_t3_ban():
    node = RemoteWorkerNode(
        node_id="worker_pc_02",
        hostname="WORKER-PC-ASUS",
        shared_secret="ultra_secure_shared_key_123",
    )

    # 1. Valid dispatch with signature
    envelope = node.dispatch_task(
        task_id="task_comp_10",
        payload={"script": "python -m pytest", "timeout": 30},
        input_files={"input.py": b"print('worker ok')"},
    )
    assert envelope["node_id"] == "worker_pc_02"
    assert "signature" in envelope
    assert node.verify_signature(envelope, envelope["signature"]) is True

    # 2. T3 secret forwarding ban
    with pytest.raises(T3SecretForwardingProhibitedError):
        node.dispatch_task(
            task_id="task_leak",
            payload={"master_password": "owner_master_pwd"},
        )


def test_remote_worker_artifact_hashes():
    node = RemoteWorkerNode(
        node_id="worker_pc_hash",
        hostname="WORKER-PC",
        shared_secret="secret_key_88",
    )
    out_content = b"artifact output data"
    correct_hash = hashlib.sha256(out_content).hexdigest()

    # Verified output
    res = node.receive_result(
        result_payload={"task_id": "t1", "exit_code": 0},
        output_files={"result.json": out_content},
        expected_hashes={"result.json": correct_hash},
    )
    assert res["status"] == "VERIFIED"

    # Corrupted output fails integrity check
    with pytest.raises(ArtifactIntegrityError):
        node.receive_result(
            result_payload={"task_id": "t1"},
            output_files={"result.json": b"corrupted data"},
            expected_hashes={"result.json": correct_hash},
        )


def test_remote_worker_coordinator_cascade_stop_sub_2s():
    coordinator = WorkerClusterCoordinator()
    node1 = coordinator.register_node("pc_02", "PC2", "key1")
    node2 = coordinator.register_node("laptop_01", "LAPTOP", "key2")

    node1.dispatch_task("task_p2", {"job": "build"})
    assert node1.state == "BUSY"

    # Trigger cascading STOP
    stop_report = coordinator.trigger_cascade_stop()
    assert stop_report["global_stop"] is True
    assert stop_report["nodes_stopped"] == 2
    assert stop_report["cascade_stop_sub_2s"] is True
    assert stop_report["elapsed_s"] <= CASCADING_STOP_TIMEOUT_SECONDS
    assert node1.state == "STOPPED"
    assert node2.state == "STOPPED"


def test_remote_worker_heartbeat_lease_expiration():
    simulated_time = 1000.0
    coordinator = WorkerClusterCoordinator(clock=lambda: simulated_time)
    node = coordinator.register_node("pc_worker", "PC", "key")

    assert node.heartbeat() is True
    assert coordinator.sweep_heartbeats() == []

    # Advance clock past 2.0s lease timeout
    simulated_time += 2.5
    expired = coordinator.sweep_heartbeats()
    assert "pc_worker" in expired
    assert node.state == "DISCONNECTED"


# ---------------------------------------------------------------------------
# 6. VirtualUVCCameraProbe Stage 6 Tests
# ---------------------------------------------------------------------------
def test_virtual_uvc_camera_probe_stage6():
    """Audit 28.10: the probe used to return PASS / presence_detected / calibrated for a camera it never looked for."""
    # the synthetic self-test is only a harness check and says so
    report = VirtualUVCCameraProbe().run_self_test()
    assert report["self_test"] == "SYNTHETIC_ONLY" and report["status"] == "SYNTHETIC_ONLY"
    assert report["presence_detected"] is False and report["calibrated"] is False and report["devices"] == []
    assert len(report["synthetic_frame_sha256"]) == 64

    # a present, working camera is DETECTED (not PASS) and no resolution/fps is invented
    cam = [{"status": "OK", "class": "Camera", "name": "Test Camera", "instance_id": "USB-VID-TEST"}]
    found = VirtualUVCCameraProbe(enumerate_devices=lambda: cam).probe()
    assert found["status"] == "DETECTED" and found["mode"] == "physical_usb" and found["presence_detected"] is True
    assert found["devices"] == [{"name": "Test Camera", "class": "Camera", "status": "OK"}]
    assert "fps" not in found and "resolution" not in found

    # a broken device and an empty list are NO_CAMERA, never a virtual PASS
    broken = VirtualUVCCameraProbe(enumerate_devices=lambda: [{"status": "Error", "class": "Camera", "name": "Bad", "instance_id": "x"}]).probe()
    none = VirtualUVCCameraProbe(enumerate_devices=lambda: []).probe()
    for r in (broken, none):
        assert r["status"] == "NO_CAMERA" and r["presence_detected"] is False and r["mode"] == "none"
    assert broken["devices"][0]["status"] == "Error"


def test_camera_probe_sees_the_real_machine_without_capturing():
    """Whatever this machine has, the answer is one of the honest states and never the old fabricated PASS."""
    r = VirtualUVCCameraProbe().probe()
    assert r["status"] in ("DETECTED", "NO_CAMERA")
    assert r["presence_detected"] is (r["status"] == "DETECTED")
    assert "calibrated" not in r and r.get("status") != "PASS"


def test_stop_timings_declare_that_they_measure_an_in_process_flip_only():
    """Audit 28.10: `cascade_stop_sub_2s` times a field change on an in-memory object; it must say so, not read as a network STOP."""
    from bcc.devices.worker_node import IN_PROCESS_SCOPE
    node = RemoteWorkerNode(node_id="n1", hostname="h", shared_secret="s")
    assert node.emergency_stop()["scope"] == IN_PROCESS_SCOPE
    coord = WorkerClusterCoordinator()
    coord.register_node(node_id="n2", hostname="h2", shared_secret="s")
    report = coord.trigger_cascade_stop()
    assert report["scope"] == IN_PROCESS_SCOPE and "no remote node" in report["scope"]
