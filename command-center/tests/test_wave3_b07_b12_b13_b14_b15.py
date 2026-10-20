"""Wave 3 closure tests for B07 (media), B12 (ops/v2.6.0), B13 (oss), B14 (commerce), and B15 (devices)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc import RELEASE_VERSION, build_identity
from bcc.direct_gen.service import DirectGenService
from bcc.features.bossnet_nodes_v16 import (
    Node,
    TaskNeed,
    choose,
    eligible,
    reconcile_disconnected_node,
    revoke_node,
)
from bcc.features.oss_integrations import verify_oss_candidate
from bossman_shared.exchange_assistant.quote import build_quote
from bossman_shared.exchange_assistant.rates import Rate, RateBook
from bossman_shared.exchange_assistant.store import Store

from .conftest import client_for, make_settings, start_app


# ---------------- B07: Media Provenance Receipt & Cleanup ----------------

@pytest.mark.asyncio
async def test_b07_media_artifact_provenance_receipt_and_delete(tmp_path):
    svc_stub = SimpleNamespace(
        settings=SimpleNamespace(data_dir=tmp_path),
        bus=SimpleNamespace(emit=lambda *a, **k: None),
    )
    dgen = DirectGenService(svc_stub)
    job_id = "b070" * 8
    folder = dgen.store.job_dir("owner", job_id)
    res_dir = folder / "result"
    res_dir.mkdir(parents=True, exist_ok=True)
    inp_bytes = b"reference-image-bytes-b07"
    out_bytes = b"\x89PNG\r\n\x1a\nfake-output-png-b07"
    (folder / "input_image.bin").write_bytes(inp_bytes)
    out_file = res_dir / "image.png"
    out_file.write_bytes(out_bytes)
    out_sha = hashlib.sha256(out_bytes).hexdigest()

    job = {
        "job_id": job_id,
        "participant": "owner",
        "model": "flux-schnell",
        "kind": "photo",
        "mode": "DIRECT",
        "status": "postprocessing",
        "stage": "postprocessing",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "raw_prompt": "studio portrait",
        "effective_prompt": "studio portrait",
        "negative": "",
        "params": {"width": 1024, "height": 1024, "seed": 42, "steps": 4},
        "result": {"file": "image.png", "bytes": len(out_bytes), "sha256": out_sha, "mime": "image/png"},
        "provenance": {"model_id": "flux-schnell", "runtime": "sdcpp"},
        "timeline": [],
    }
    dgen.store.save(job)
    await dgen._set(job, "completed")

    loaded = dgen.get("owner", job_id)
    prov = loaded["provenance"]
    assert prov["privacy_scope"] == "local_owner_only"
    assert prov["output_sha256"] == out_sha
    receipt = prov["artifact_receipt"]
    assert receipt["model_id"] == "flux-schnell"
    assert receipt["output_sha256"] == out_sha
    assert receipt["input_sha256"] == hashlib.sha256(
        hashlib.sha256(inp_bytes).hexdigest().encode("ascii")
    ).hexdigest()
    assert receipt["privacy_scope"] == "local_owner_only"

    sidecar = res_dir / "provenance_receipt.json"
    assert sidecar.is_file()
    sidecar_json = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_json["output_sha256"] == out_sha

    deleted = dgen.delete("owner", job_id)
    assert deleted["ok"] is True
    assert deleted["deleted"] is True
    assert deleted["provenance_deleted"] is True
    assert not sidecar.exists()
    assert not folder.exists()


# ---------------- B12: Ops Release Version 2.6.0 & Build Identity ----------------

@pytest.mark.asyncio
async def test_b12_ops_release_version_2_6_0_exposed_on_health_and_identity(tmp_path):
    assert RELEASE_VERSION == "2.6.0"
    assert build_identity.RELEASE_VERSION == "2.6.0"
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        async with client_for(app, svc) as client:
            live = (await client.get("/health/live")).json()
            health = (await client.get("/health")).json()
            api_health = (await client.get("/api/health")).json()
            identity = (await client.get("/api/identity")).json()
        assert live["version"] == "2.6.0"
        assert live["release_version"] == "2.6.0"
        assert health["version"] == "2.6.0"
        assert health["release_version"] == "2.6.0"
        assert api_health["version"] == "2.6.0"
        assert api_health["release_version"] == "2.6.0"
        assert identity["release_version"] == "2.6.0"
    finally:
        await svc.stop()


# ---------------- B13: OSS Provenance & License/Egress Gate ----------------

def test_b13_oss_provenance_gate_allows_pinned_permissive_and_blocks_unverified():
    good = verify_oss_candidate({
        "id": "whisper",
        "upstream": "https://github.com/SYSTRAN/faster-whisper",
        "license": "MIT",
        "version": "1.2.1",
        "external_egress": "loopback_only",
    })
    assert good["allowed"] is True
    assert good["reasons"] == []
    assert good["privacy_egress"] == "loopback_only"

    bad_license = verify_oss_candidate({
        "id": "mystery-agent",
        "upstream": "https://github.com/example/mystery",
        "license": "unknown",
        "version": "0.1.0",
        "external_egress": False,
    })
    assert bad_license["allowed"] is False
    assert "license_unverified" in bad_license["reasons"]

    bad_egress = verify_oss_candidate({
        "id": "cloud-leaky",
        "upstream": "https://github.com/example/leaky",
        "license": "MIT",
        "version": "1.0.0",
        "external_egress": "https://telemetry.example.com",
    })
    assert bad_egress["allowed"] is False
    assert "unauthorized_external_egress" in bad_egress["reasons"]


# ---------------- B14: Commerce Delivery Receipt & Net Profit Proof ----------------

def test_b14_commerce_deal_profit_receipt_verifies_net_profit_and_pdf_hash(tmp_path):
    store = Store(tmp_path / "assistant.db")
    try:
        now = datetime.now(timezone.utc)
        book = RateBook([
            Rate("USDCZK", Decimal("23.40"), "CNB", now, "live", now + timedelta(minutes=15)),
            Rate("USDTUSD", Decimal("1.0005"), "Kraken", now, "live", now + timedelta(minutes=15)),
        ])
        q = build_quote(
            book,
            give_ccy="USDT",
            get_ccy="CZK",
            give_amount=Decimal("2500"),
            margin=Decimal("0.03"),
            now=now,
        )
        store.save_quote(q, crm_status="quoted")
        deal, created = store.confirm(
            q.id,
            now=now + timedelta(seconds=10),
            by="owner",
            crm_status="settled",
            leg_in_actual=q.give_amount,
            leg_out_actual=q.get_amount,
        )
        assert created is True
        receipt = store.deal_profit_receipt(
            int(deal["id"]),
            platform_fee_ccy=Decimal("45.00"),
            variable_cost_ccy=Decimal("15.00"),
            consent_ref="client_signed_quote",
        )
        assert receipt["formula_verified"] is True
        gross = Decimal(receipt["gross_profit_ccy"])
        fees = Decimal(receipt["platform_fee_ccy"])
        var_cost = Decimal(receipt["variable_cost_ccy"])
        net = Decimal(receipt["net_profit_ccy"])
        assert gross > Decimal("1500.00")
        assert gross - fees - var_cost == net
        assert net > Decimal("1400.00")
        assert len(receipt["deliverable_pdf_sha256"]) == 64
        assert len(receipt["receipt_sha256"]) == 64
    finally:
        store.close()


# ---------------- B15: Device Revocation & Disconnect Reconciliation ----------------

def test_b15_devices_revoke_and_disconnect_reconcile_without_duplicate_side_effects():
    now = 1_760_000_000.0
    node_a = Node(
        node_id="node-mobile-1",
        capabilities=frozenset({"camera", "mic", "notify"}),
        ram_gb=8.0,
        load=0.2,
        last_heartbeat=now - 5,
        owner_bound=True,
    )
    node_b = Node(
        node_id="node-pc-main",
        capabilities=frozenset({"camera", "mic", "notify", "gpu"}),
        ram_gb=32.0,
        gpu_memory_gb=16.0,
        load=0.3,
        last_heartbeat=now - 2,
        owner_bound=True,
    )
    need = TaskNeed(capabilities=frozenset({"notify"}), min_ram_gb=4.0)
    assert eligible(node_a, need, now=now) is True

    rev = revoke_node(node_a, in_flight_tasks=["task-401", "task-402"], reason="owner_revoked", now=now)
    revoked_a = rev["node"]
    assert rev["revoked"] is True
    assert eligible(revoked_a, need, now=now) is False
    assert choose([revoked_a, node_b], need, now=now) == node_b
    assert len(rev["in_flight_reconciliation"]) == 2
    assert all(r["status"] == "RECONCILE_REQUIRED" and r["duplicate_side_effects_blocked"] is True
               for r in rev["in_flight_reconciliation"])

    disc = reconcile_disconnected_node(node_a, in_flight_tasks=["task-501"], now=now + 120, ttl=30)
    assert disc["disconnected"] is True
    assert disc["in_flight_reconciliation"][0]["status"] == "RECONCILE_REQUIRED"
    assert disc["in_flight_reconciliation"][0]["duplicate_side_effects_blocked"] is True
