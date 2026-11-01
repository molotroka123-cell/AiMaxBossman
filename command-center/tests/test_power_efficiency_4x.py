from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa

from bcc.config import Settings
from bcc.db import Database, models, providers, resource_reservations
from bcc.features.power_efficiency_4x import (
    coalesce_and_plan_power_budget,
    discover_free_cloud_candidates,
    evict_idle_vram_models,
    reap_expired_leases_and_temp_buffers,
    run_power_efficiency_4x_suite,
)
from bcc.secrets import Vault
from bcc.tools import REGISTRY, ToolContext
from bcc.v2.memory.facts import FactStore


@pytest.mark.asyncio
async def test_evict_idle_vram_models_unloads_when_no_active_holds(tmp_path: Path) -> None:
    db_path = tmp_path / "test_pwr1.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    await db.create_all()

    unloaded_calls: list[str] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "r1-70b-abliterated-ctx32k:latest", "size": 45200 * 1024 * 1024},
                        {"name": "qwen2.5-coder:14b", "size": 9000 * 1024 * 1024},
                    ]
                },
            )
        if request.url.path == "/api/generate":
            payload = json.loads(request.content.decode("utf-8"))
            if payload.get("keep_alive") == 0:
                unloaded_calls.append(str(payload.get("model")))
            return httpx.Response(200, json={"done": True})
        return httpx.Response(404)

    transport = httpx.MockTransport(_handler)
    res = await evict_idle_vram_models(db, transport=transport)
    assert res["ok"] is True
    assert res["loaded_models_before"] == 2
    assert res["loaded_models_after"] == 0
    assert set(unloaded_calls) == {"r1-70b-abliterated-ctx32k:latest", "qwen2.5-coder:14b"}
    assert res["efficiency_gain_x"] >= 4.0
    assert res["zero_fan_silent_mode"] is True
    await db.close()


@pytest.mark.asyncio
async def test_coalesce_and_reap_reservations_4x(tmp_path: Path) -> None:
    db_path = tmp_path / "test_pwr2.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    await db.create_all()

    c_res = await coalesce_and_plan_power_budget(db)
    assert c_res["ok"] is True
    assert c_res["raw_reservation_count"] == 8
    assert c_res["coalesced_reservation_count"] == 2
    assert c_res["row_reduction_x"] == 4.0
    assert c_res["efficiency_gain_x"] >= 4.0
    assert c_res["power_profile"] in ("silent_zero_fan", "balanced_4x", "peak_burst")

    r_res = await reap_expired_leases_and_temp_buffers(
        db,
        scratch_dir=tmp_path / "scratch",
    )
    assert r_res["ok"] is True
    assert r_res["guard_passed"] is True
    assert r_res["reaped_reservations"] == 2
    assert r_res["reaped_temp_files"] == 4
    assert r_res["proof_passed"] is True
    assert len(r_res["proof_sha256"]) == 64

    async with db.engine.connect() as conn:
        held = (
            await conn.execute(
                sa.select(sa.func.count())
                .select_from(resource_reservations)
                .where(resource_reservations.c.status == "held")
            )
        ).scalar()
        assert int(held or 0) == 0

    await db.close()


@pytest.mark.asyncio
async def test_full_suite_and_free_cloud_swarm_verification(tmp_path: Path) -> None:
    vault = Vault(tmp_path)
    db_path = tmp_path / "test_pwr_suite.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    await db.create_all()

    async with db.engine.begin() as conn:
        for idx, pname in enumerate(("Groq-Free", "NVIDIA-NIM-Free", "Google-AI-Free", "OpenRouter-Free"), start=1):
            p_res = await conn.execute(
                providers.insert().values(
                    name=pname,
                    kind="openai_compat",
                    base_url=f"https://cloud-{idx}.example/v1",
                    api_key_enc=vault.encrypt(f"sk-test-free-{idx}"),
                )
            )
            pid = int(p_res.inserted_primary_key[0])
            await conn.execute(
                models.insert().values(
                    provider_id=pid,
                    name=f"free-auditor-{idx}",
                    alias=f"free-auditor-{idx}",
                    kind="cloud",
                    price_in=0.0,
                    price_out=0.0,
                    status="online",
                )
            )

    candidates = await discover_free_cloud_candidates(db, vault)
    assert len(candidates) == 4

    async def _ollama_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, json={"done": True})

    async def _cloud_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        model_name = payload.get("model", "free-auditor")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "verified": True,
                                    "auditor_model": model_name,
                                    "efficiency_gain_x": 4.5,
                                    "verdict": "All power and memory invariants verified at 0% local GPU.",
                                }
                            )
                        }
                    }
                ]
            },
        )

    receipt_file = tmp_path / "receipt" / "power_4x_receipt.json"
    suite_res = await run_power_efficiency_4x_suite(
        db,
        vault,
        scratch_dir=tmp_path / "scratch_suite",
        receipt_path=receipt_file,
        ollama_transport=httpx.MockTransport(_ollama_handler),
        cloud_transport=httpx.MockTransport(_cloud_handler),
    )

    assert suite_res["all_four_functions_verified"] is True
    assert suite_res["overall_efficiency_multiplier_x"] >= 4.0
    assert suite_res["proof_passed"] is True
    assert len(suite_res["proof_sha256"]) == 64
    assert receipt_file.exists()

    swarm = suite_res["functions"]["verify_functions_with_free_cloud_swarm"]
    assert swarm["verified_functions_count"] == 4
    assert swarm["live_cloud_verdicts_count"] == 4

    from types import SimpleNamespace

    store = FactStore(SimpleNamespace(db=db))
    facts = await store.search(subject="bossman:power_efficiency_4x", caller_scope="work")
    assert len(facts) == 1
    assert facts[0]["predicate"] == "verified_4x_power_harness"

    spec = REGISTRY.get("power_efficiency_4x_run")
    assert spec is not None

    class _DummySvc:
        def __init__(self, d, v):
            self.db = d
            self.vault = v

    t_res = await spec.handler(
        {},
        ToolContext(svc=_DummySvc(db, vault), run_id=1, task={"id": 1}, agent={"id": "bossman"}),
    )
    assert t_res.error is False
    assert t_res.data["all_four_functions_verified"] is True

    await db.close()
