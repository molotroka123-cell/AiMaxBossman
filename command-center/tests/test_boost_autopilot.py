"""Tests for Bossman Boost Autopilot (`bcc.features.boost_autopilot`).

Covers:
1. All 3 autopilot pipelines (`system_health_and_hygiene`, `bossblock_world_audit`, `daily_digest_and_proof`, `all`)
2. AgentProofBench (`verify_criterion_with_proof` + negative control catching false success claim)
3. CUA-Recovery (`StepStateGuard` auto-repair from checkpoint + halt before irreversible when unrepairable)
4. TimlMemBench (`FactStore` scoped memory write, superseding old run, scope isolation, and revocation)
5. Secret redaction (`pult_report.redact`) in autopilot notes and digest
6. Agent tools (`autopilot.run_pipeline`, `autopilot.status`, `autopilot.configure`) and background `_tick`
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from bcc.features import boost_autopilot as ba
from bcc.tools import REGISTRY, ToolContext
from bcc.v2.memory.facts import FactStore


async def test_autopilot_all_pipelines_and_status_api(env):
    r_status = await env.client.get("/api/autopilot/status")
    assert r_status.status_code == 200
    st = r_status.json()
    assert st["ok"] is True
    assert st["rule_version"] == ba.RULE_VERSION
    assert set(st["available_pipelines"]) == set(ba.PIPELINES)
    assert st["metrics"]["total_runs"] == 0

    # Run all 3 pipelines at once
    r_run = await env.client.post("/api/autopilot/run", json={"pipeline": "all", "note": "boost check"})
    assert r_run.status_code == 200
    body = r_run.json()["result"]
    assert body["mode"] == "all"
    assert body["verdict"] == "VERIFIED"
    assert body["pipelines_run"] == 3
    for item in body["results"]:
        assert item["verifier_status"] == "VERIFIED"
        assert item["false_claim_caught"] is False
        assert item["initial_snapshot"]["pre_status"] == "FAILED"
        assert item["final_evidence"]["status"] == "VERIFIED"
        assert len(item["proof_sha256"]) == 64
        assert Path(item["artifact_path"]).is_file()
        assert item["memory_fact"] is not None
        assert item["memory_fact"]["status_ru"] == "действующий"

    # Verify GET /api/autopilot/runs and durable STATE_KEY restoration after cache clear
    delattr(env.svc, "_autopilot_state")
    r_runs = await env.client.get("/api/autopilot/runs?limit=5")
    assert r_runs.status_code == 200
    assert len(r_runs.json()["runs"]) == 3
    r_status_after = await env.client.get("/api/autopilot/status")
    assert r_status_after.json()["metrics"]["verified_runs"] == 3


async def test_autopilot_agent_proof_bench_catches_false_claim_and_verify_artifact(env, tmp_path: Path):
    # 1. Negative control: agent claims success, but artifact was not written to disk
    r = await env.client.post(
        "/api/autopilot/run",
        json={
            "pipeline": "system_health_and_hygiene",
            "simulate_unwritten_artifact": True,
            "agent_claim_text": "Готово! Пайплайн успешно выполнен и файл сохранён.",
        },
    )
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["status"] == "VERIFICATION_FAILED"
    assert res["verifier_status"] == "FAILED"
    assert res["agent_claimed_success"] is True
    assert res["false_claim_caught"] is True
    # Must NOT record an unverified claim into temporal memory
    assert res["memory_fact"] is None

    # 2. Negative control: bossblock_world_audit on an incomplete folder fails post-state verification
    incomplete_dir = tmp_path / "incomplete_world"
    incomplete_dir.mkdir()
    r_inc = await env.client.post(
        "/api/autopilot/run",
        json={
            "pipeline": "bossblock_world_audit",
            "target_folder": str(incomplete_dir),
            "agent_claim_text": "Готово: 3D-мир полностью готов к игре.",
        },
    )
    assert r_inc.status_code == 200
    inc_res = r_inc.json()["result"]
    assert inc_res["payload_summary"]["world_audit"]["verdict"] == "INCOMPLETE"
    assert inc_res["verifier_status"] == "FAILED"
    assert inc_res["false_claim_caught"] is True
    assert inc_res["memory_fact"] is None

    # 3. Nonexistent explicit target_folder raises HTTP 400 instead of silently falling back to dnipro_assets
    r_missing = await env.client.post(
        "/api/autopilot/run",
        json={
            "pipeline": "bossblock_world_audit",
            "target_folder": str(tmp_path / "does_not_exist"),
        },
    )
    assert r_missing.status_code == 400

    # 4. POST /api/autopilot/verify-artifact without initial_snapshot records pre_status=UNVERIFIED (not VERIFIED)
    runs_dir = ba._runs_dir(env.svc)
    sample_file = runs_dir / "custom_check.json"
    sample_file.write_text('{"ok": true, "bytes_check": 123456}', encoding="utf-8")
    r_ver = await env.client.post(
        "/api/autopilot/verify-artifact",
        json={
            "target": str(sample_file),
            "expect": {"exists": True, "contains": '"ok": true', "min_bytes": 10},
            "agent_claim_text": "Готово: файл сохранён.",
        },
    )
    assert r_ver.status_code == 200
    rec = r_ver.json()["receipt"]
    assert rec["verifier_status"] == "VERIFIED"
    assert rec["initial_snapshot"]["pre_status"] == "UNVERIFIED"
    assert rec["false_claim_caught"] is False


async def test_autopilot_cua_recovery_repair_and_halt(env):
    # 1. With allow_auto_repair=True, injected mid-step fault is detected and repaired from checkpoint
    r_repair = await env.client.post(
        "/api/autopilot/run",
        json={
            "pipeline": "bossblock_world_audit",
            "inject_mid_step_fault": "wrong_form_field",
            "allow_auto_repair": True,
        },
    )
    assert r_repair.status_code == 200
    res_rep = r_repair.json()["result"]
    assert res_rep["cua_recovery"]["matched"] is False
    assert res_rep["cua_recovery"]["detected_fault"] == "wrong_form_field"
    assert res_rep["cua_recovery"]["decision"] == "rollback_and_retry"
    assert res_rep["cua_recovery"]["repaired"] is True
    assert res_rep["verifier_status"] == "VERIFIED"

    # 2. Negative control: with allow_auto_repair=False, fault halts BEFORE irreversible step
    r_halt = await env.client.post(
        "/api/autopilot/run",
        json={
            "pipeline": "bossblock_world_audit",
            "inject_mid_step_fault": "closed_dialog",
            "allow_auto_repair": False,
        },
    )
    assert r_halt.status_code == 200
    res_halt = r_halt.json()["result"]
    assert res_halt["status"] == "HALTED_BEFORE_IRREVERSIBLE"
    assert res_halt["verifier_status"] == "UNVERIFIED"
    assert res_halt["cua_recovery"]["detected_fault"] == "closed_dialog"
    assert res_halt["cua_recovery"]["decision"] == "halt_before_irreversible"
    assert res_halt["cua_recovery"]["repaired"] is False
    assert res_halt["memory_fact"] is None


async def test_autopilot_timl_memory_scope_supersede_revoke_and_concurrency(env):
    store = FactStore(env.svc)

    # First run in 'personal' scope
    r1 = await env.client.post(
        "/api/autopilot/run",
        json={"pipeline": "daily_digest_and_proof", "scope": "personal"},
    )
    f1 = r1.json()["result"]["memory_fact"]
    assert f1 is not None
    assert f1["scope"] == "personal"

    # Scope isolation check: 'work' and 'public' callers must never see 'personal' autopilot facts
    work_hits = await store.search(subject="autopilot:daily_digest_and_proof", caller_scope="work")
    public_hits = await store.search(subject="autopilot:daily_digest_and_proof", caller_scope="public")
    personal_hits = await store.search(subject="autopilot:daily_digest_and_proof", caller_scope="personal")
    assert len(work_hits) == 0
    assert len(public_hits) == 0
    assert len(personal_hits) == 1

    # Second run supersedes first fact
    r2 = await env.client.post(
        "/api/autopilot/run",
        json={"pipeline": "daily_digest_and_proof", "scope": "personal"},
    )
    f2 = r2.json()["result"]["memory_fact"]
    assert f2["id"] != f1["id"]

    history = await store.history(subject="autopilot:daily_digest_and_proof", predicate="last_verified_run")
    by_id = {row["id"]: row for row in history}
    assert by_id[f1["id"]]["status_ru"] == "заменённый"
    assert by_id[f2["id"]]["status_ru"] == "действующий"

    # Revoke active fact
    revoked = await store.revoke(fact_id=f2["id"])
    assert len(revoked) == 1
    assert revoked[0]["status_ru"] == "отозванный"
    after_revoke = await store.search(subject="autopilot:daily_digest_and_proof", caller_scope="personal")
    assert len(after_revoke) == 0

    # Concurrent runs with pipeline='all' must serialize FactStore writes cleanly without FactWriteError
    c1, c2 = await asyncio.gather(
        env.client.post("/api/autopilot/run", json={"pipeline": "all", "scope": "work"}),
        env.client.post("/api/autopilot/run", json={"pipeline": "all", "scope": "work"}),
    )
    assert c1.status_code == 200 and c2.status_code == 200
    for resp in (c1.json()["result"], c2.json()["result"]):
        assert resp["verdict"] == "VERIFIED"
        for sub in resp["results"]:
            assert sub["verifier_status"] == "VERIFIED"
            assert sub["memory_fact"] is not None


async def test_autopilot_secret_redaction_and_tools_and_tick(env):
    fake_secret = "sk-proj-" + ("A1b2C3d4" * 4)  # ci-secret-scan: allow
    r = await env.client.post(
        "/api/autopilot/run",
        json={"pipeline": "daily_digest_and_proof", "note": f"token={fake_secret} do not leak"},
    )
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["secret_redacted"] is True
    assert fake_secret not in res["sanitized_note"]
    assert "[скрыто]" in res["sanitized_note"]
    artifact_text = Path(res["artifact_path"]).read_text(encoding="utf-8")
    assert fake_secret not in artifact_text

    # Agent tools registered in REGISTRY
    assert REGISTRY.get("autopilot.run_pipeline") is not None
    assert REGISTRY.get("autopilot.status") is not None
    assert REGISTRY.get("autopilot.configure") is not None

    ctx = ToolContext(svc=env.svc, task={"id": 1}, run_id=1, agent={"name": "Argon"})
    cfg_res = await ba._tool_configure(
        {"enabled": True, "interval_minutes": 15, "default_pipeline": "all"},
        ctx,
    )
    assert not cfg_res.error
    assert cfg_res.data["config"]["interval_minutes"] == 15
    assert cfg_res.data["config"]["default_pipeline"] == "all"

    tool_run = await ba._tool_run_pipeline({"pipeline": "bossblock_world_audit", "note": "via tool"}, ctx)
    assert not tool_run.error
    assert tool_run.data["verifier_status"] == "VERIFIED"

    # Background tick executes default_pipeline='all' (all 3 pipelines) when interval has elapsed
    before_st = await ba._tool_status({}, ctx)
    before_verified = before_st.data["metrics"]["verified_runs"]
    ba._last_tick_ts = 0.0
    await ba._tick(env.svc)
    st_res = await ba._tool_status({}, ctx)
    assert not st_res.error
    assert st_res.data["metrics"]["verified_runs"] == before_verified + 3

    # Invalid pipeline & invalid config return HTTP 400
    bad_run = await env.client.post("/api/autopilot/run", json={"pipeline": "non_existent_pipeline"})
    assert bad_run.status_code == 400
    bad_cfg = await env.client.post("/api/autopilot/config", json={"scope": "invalid_scope"})
    assert bad_cfg.status_code == 400
