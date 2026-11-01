"""Tests for Bossman Web Trend Teacher (`bcc.features.web_trend_teacher`).

Covers:
1. Readiness audit (`audit_web_learning_readiness`) and full 4-domain Teacher session
2. Teacher Lesson Card distillation, pronoun stripping, I-1 FactStore compliance, and Mastery Exam (>= 0.85)
3. AgentProofBench (`verify_criterion_with_proof` + negative control catching false claim on unwritten artifact)
4. CUA-Recovery (`StepStateGuard` auto-repair from checkpoint + halt before irreversible when unrepairable)
5. TimlMemBench (`FactStore` scoped memory write, superseding previous trend facts, scope isolation, recall)
6. Secret redaction (`pult_report.redact`) and agent tools (`teacher.learn_web_trends`, `teacher.trend_status`, `teacher.recall_trends`)
"""
from __future__ import annotations

from pathlib import Path

from bcc.features import web_trend_teacher as wtt
from bcc.tools import REGISTRY, ToolContext
from bcc.v2.memory.facts import PRONOUNS, FactStore


async def test_web_trend_teacher_status_audit_and_teach_session(env):
    r_status = await env.client.get("/api/web-trend-teacher/status")
    assert r_status.status_code == 200
    st = r_status.json()
    assert st["ok"] is True
    assert st["rule_version"] == wtt.RULE_VERSION
    assert set(st["available_domains"]) == set(wtt.TREND_DOMAINS.keys())
    assert st["audit"]["audit_verdict"] == "READY_FOR_LIVE_WEB_LEARNING"
    assert len(st["audit"]["gaps_closed"]) == 3

    # Run a deterministic teacher session (offline-resilient mode for unit speed + deterministic assertions)
    r_teach = await env.client.post(
        "/api/web-trend-teacher/teach",
        json={"allow_live_network": False, "note": "scheduled teacher check"},
    )
    assert r_teach.status_code == 200
    res = r_teach.json()["result"]
    assert res["status"] == "COMPLETED"
    assert res["verifier_status"] == "VERIFIED"
    assert res["false_claim_caught"] is False
    assert res["initial_snapshot"]["pre_status"] == "FAILED"
    assert res["final_evidence"]["status"] == "VERIFIED"
    assert len(res["proof_sha256"]) == 64
    assert Path(res["artifact_path"]).is_file()
    assert res["lessons_count"] == 4
    assert res["average_mastery_score"] >= 0.85
    assert len(res["memory_facts_written"]) == 5  # 4 domain facts + 1 master session fact

    for lesson in res["lessons"]:
        assert lesson["mastery_exam"]["passed"] is True
        assert lesson["mastery_exam"]["mastery_score"] >= 0.85
        words = lesson["temporal_fact"]["statement"].split()
        assert 15 <= len(words) <= 80
        assert not any(w.strip(".,:;").lower() in PRONOUNS for w in words)

    # Verify durable state restoration after clearing in-memory cache
    delattr(env.svc, "_web_trend_teacher_state")
    r_after = await env.client.get("/api/web-trend-teacher/status")
    assert r_after.status_code == 200
    assert r_after.json()["metrics"]["verified_sessions"] == 1
    assert r_after.json()["metrics"]["lessons_mastered"] == 4


async def test_web_trend_teacher_agent_proof_bench_catches_false_claim(env):
    # Negative control: agent claims success, but curriculum artifact was not written to disk
    r = await env.client.post(
        "/api/web-trend-teacher/teach",
        json={
            "allow_live_network": False,
            "simulate_unwritten_artifact": True,
            "agent_claim_text": "Готово! Учебная программа трендов успешно сохранена.",
        },
    )
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["status"] == "VERIFICATION_FAILED"
    assert res["verifier_status"] == "FAILED"
    assert res["false_claim_caught"] is True
    # Must NOT write unverified trend claims into FactStore
    assert res["memory_facts_written"] == []

    # Invalid domain raises HTTP 400
    r_bad = await env.client.post(
        "/api/web-trend-teacher/teach",
        json={"domains": ["nonexistent_domain"]},
    )
    assert r_bad.status_code == 400


async def test_web_trend_teacher_cua_recovery_repair_and_halt(env):
    # 1. With allow_auto_repair=True, injected mid-step fault is detected and repaired from checkpoint
    r_rep = await env.client.post(
        "/api/web-trend-teacher/teach",
        json={
            "domains": ["ai_agents_and_llms"],
            "allow_live_network": False,
            "inject_mid_step_fault": "wrong_form_field",
            "allow_auto_repair": True,
        },
    )
    assert r_rep.status_code == 200
    res_rep = r_rep.json()["result"]
    assert res_rep["cua_recovery"]["matched"] is False
    assert res_rep["cua_recovery"]["detected_fault"] == "wrong_form_field"
    assert res_rep["cua_recovery"]["decision"] == "rollback_and_retry"
    assert res_rep["cua_recovery"]["repaired"] is True
    assert res_rep["verifier_status"] == "VERIFIED"

    # 2. Negative control: with allow_auto_repair=False, fault halts BEFORE irreversible step
    r_halt = await env.client.post(
        "/api/web-trend-teacher/teach",
        json={
            "domains": ["ai_agents_and_llms"],
            "allow_live_network": False,
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
    assert res_halt["memory_facts_written"] == []


async def test_web_trend_teacher_timl_memory_supersede_scope_and_tools(env):
    store = FactStore(env.svc)

    # First session in 'work' scope for a single domain
    r1 = await env.client.post(
        "/api/web-trend-teacher/teach",
        json={
            "domains": ["generative_video_and_cinema"],
            "allow_live_network": False,
            "scope": "work",
            "note": "Token sk-secret-abc12345678901234567890 in note",  # synthetic key; ci-secret-scan: allow
        },
    )
    res1 = r1.json()["result"]
    assert res1["secret_redacted"] is True
    assert "sk-secret-abc" not in res1["sanitized_note"]
    f1 = res1["memory_facts_written"][0]
    assert f1["subject"] == "trend:generative_video_and_cinema"
    assert f1["status_ru"] == "действующий"

    # Second session supersedes the first trend fact
    r2 = await env.client.post(
        "/api/web-trend-teacher/teach",
        json={
            "domains": ["generative_video_and_cinema"],
            "allow_live_network": False,
            "scope": "work",
        },
    )
    res2 = r2.json()["result"]
    f2 = res2["memory_facts_written"][0]
    assert f2["id"] != f1["id"]

    history = await store.history(
        subject="trend:generative_video_and_cinema",
        predicate="current_internet_trend",
    )
    by_id = {row["id"]: row for row in history}
    assert by_id[f1["id"]]["status_ru"] == "заменённый"
    assert by_id[f2["id"]]["status_ru"] == "действующий"

    # Check GET /api/web-trend-teacher/curriculum and agent tools
    r_curr = await env.client.get("/api/web-trend-teacher/curriculum?scope=work")
    assert r_curr.status_code == 200
    assert r_curr.json()["active_trend_facts_count"] >= 1

    ctx = ToolContext(svc=env.svc, task={"id": 910, "agent_id": "bossman"}, run_id=1, agent={"id": "bossman"})
    t_learn = await REGISTRY.get("teacher.learn_web_trends").handler(
        {"domains": ["webgl_3d_and_world_gen"], "allow_live_network": False, "scope": "work"},
        ctx,
    )
    assert not t_learn.error
    assert t_learn.data["verifier_status"] == "VERIFIED"

    t_recall = await REGISTRY.get("teacher.recall_trends").handler(
        {"domain": "webgl_3d_and_world_gen", "scope": "work"},
        ctx,
    )
    assert not t_recall.error
    assert t_recall.data["active_trend_facts_count"] == 1

    t_status = await REGISTRY.get("teacher.trend_status").handler({}, ctx)
    assert not t_status.error
    assert t_status.data["metrics"]["verified_sessions"] >= 3
