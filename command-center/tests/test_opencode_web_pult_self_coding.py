"""Tests for Bossman OpenCode Web Pult Workbench:
- Curated Free & Paid top cloud coding models catalog (/api/coding-tasks/workers)
- Cloud provider API key connection from Web Pult (/api/coding-tasks/connect-key)
- Self-coding target resolution for Bossman's own repository (/api/coding-tasks/self-target)
- Multi-worker cloud fallback when upstream provider returns model_error (429/404)
- Web Pult UI (coding.js) OpenCode Workbench integration
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bcc.features import coding_tasks as ct


def _make_app(tmp_path: Path, *, roots: list[Path] | None = None):
    app = FastAPI()

    async def emit(*_a, **_kw):
        return None

    app.state.svc = SimpleNamespace(
        settings=SimpleNamespace(data_dir=tmp_path),
        bus=SimpleNamespace(emit=emit),
        db=None,
        vault=None,
    )
    app.include_router(ct.router, prefix="/api")
    return app


def test_opencode_workers_catalog_exposes_curated_free_and_paid_cloud_models(tmp_path, monkeypatch):
    keyfile = tmp_path / "provider-keys.env"
    keyfile.write_text(
        "OPENROUTER_API_KEY=dummy-openrouter-test-token-123456\n"
        "GROQ_API_KEY=dummy-groq-test-token-123456\n"
        "GEMINI_API_KEY=dummy-gemini-test-token-123456\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ct, "OWNER_KEYS_FILE", keyfile)

    async def fake_roots(_svc):
        return [ct._canonical_bossman_repo()]

    monkeypatch.setattr(ct, "allowed_roots", fake_roots)
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        res = client.get("/api/coding-tasks/workers")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["summary"]["total_cloud_workers"] >= 14
    assert data["summary"]["free_cloud_workers"] >= 9
    assert data["summary"]["paid_cloud_workers"] >= 5
    assert data["summary"]["any_cloud_ready"] is True
    assert "poolside-laguna-free" in data["summary"]["ready_free_workers"]
    assert "groq-gpt-oss-120b-free" in data["summary"]["ready_free_workers"]
    assert "gemini-3-flash-free" in data["summary"]["ready_free_workers"]
    assert "deepseek-v4.1-flash" in data["summary"]["ready_paid_workers"]
    assert "haiku-5.5" in data["summary"]["ready_paid_workers"]
    assert "glm-flash" in data["summary"]["ready_paid_workers"]
    assert data["self_coding"]["is_bossman_checkout"] is True
    assert len(data["self_coding"]["presets"]) >= 2

    # Verify summarize_opencode_roster deterministic helper
    roster = ct.summarize_opencode_roster()
    assert roster["total_cloud"] == len(ct.WORKERS)
    assert "OpenRouter" in roster["providers"]
    assert "Groq" in roster["providers"]
    assert "Google AI Studio" in roster["providers"]


def test_connect_worker_key_unlocks_cloud_workers_without_leaking_secret(tmp_path, monkeypatch):
    keyfile = tmp_path / "provider-keys.env"
    monkeypatch.setattr(ct, "OWNER_KEYS_FILE", keyfile)

    async def fake_roots(_svc):
        return [tmp_path]

    monkeypatch.setattr(ct, "allowed_roots", fake_roots)
    app = _make_app(tmp_path)
    secret_val = "gsk_super_secret_groq_cloud_key_987654321"
    with TestClient(app) as client:
        res = client.post(
            "/api/coding-tasks/connect-key",
            json={
                "key_env": "GROQ_API_KEY",
                "api_key": secret_val,
                "persist_to_owner_file": True,
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["ok"] is True
        assert body["key_env"] == "GROQ_API_KEY"
        assert body["connected"] is True
        assert secret_val not in res.text
        assert body["masked_preview"] == "gsk_…321"
        assert "groq-qwen38-free" in body["workers_unlocked"]
        assert "groq-gpt-oss-120b-free" in body["workers_unlocked"]

        # Readiness with ?worker=groq-qwen38-free now succeeds without touching local sidecar
        r_ready = client.get("/api/coding-tasks/readiness?worker=groq-qwen38-free")
        assert r_ready.status_code == 200
        assert r_ready.json()["available"] is True
        assert r_ready.json()["worker"] == "groq-qwen38-free"


def test_self_target_endpoint_resolves_bossman_repository(tmp_path, monkeypatch):
    async def fake_roots(_svc):
        return [ct._canonical_bossman_repo()]

    monkeypatch.setattr(ct, "allowed_roots", fake_roots)
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        res = client.post("/api/coding-tasks/self-target", json={"ensure_allowed_root": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["is_bossman_checkout"] is True
    assert body["in_allowed_roots"] is True
    assert body["head_commit"] and len(body["head_commit"]) == 40
    assert "command-center/bcc/features" in body["default_allowed_paths"]


def test_fallback_workers_automatically_recover_from_upstream_model_error(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_VERIFY_PYTHON", raising=False)
    calls_made: list[str] = []

    def fake_execute(record, repo, body, context, env, command):
        calls_made.append(body.worker)
        if body.worker == "openrouter-free":
            return {
                **record,
                "status": "failed",
                "changed_files": [],
                "diff": "",
                "sidecar": {"stop_reason": "model_error", "steps": 1, "model": body.model},
                "error": "сайдкар сообщил о неудаче",
                "finished_at": 1.0,
            }
        return {
            **record,
            "status": "completed",
            "changed_files": ["command-center/bcc/features/coding_tasks.py"],
            "diff": "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n",
            "evidence_digest": "sha256:1234",
            "candidate_files": {"command-center/bcc/features/coding_tasks.py": {"deleted": False}},
            "evidence": {"head_before": "a" * 40, "head_after": "a" * 40},
            "verify_tests": ["command-center/tests/test_coding_workers.py"],
            "verification": {"ran": True, "passed": True, "exit_code": 0},
            "sidecar": {"stop_reason": "finished", "steps": 3, "model": body.model},
            "finished_at": 2.0,
        }

    monkeypatch.setattr(ct, "_execute", fake_execute)

    async def fake_key(name, _svc):
        return f"key-for-{name}"

    monkeypatch.setattr(ct, "_worker_key", fake_key)
    svc = _make_app(tmp_path).state.svc
    body = ct.TaskIn(
        instruction="self-code test",
        source_repo=str(tmp_path),
        allowed_paths=["command-center/bcc/features"],
        worker="openrouter-free",
        fallback_workers=["poolside-laguna-free", "deepseek-v4.1-flash"],
        self_target=True,
    )
    asyncio.run(ct._run(svc, {"id": "fa11bac00001"}, tmp_path, body))
    stored = ct._read(svc, "fa11bac00001")
    assert calls_made == ["openrouter-free", "poolside-laguna-free"]
    assert stored["status"] == "completed"
    assert stored["worker"] == "poolside-laguna-free"
    assert stored["model"] == "poolside/laguna-s-2.1:free"
    assert len(stored["worker_attempts"]) == 2
    assert stored["worker_attempts"][0]["stop_reason"] == "model_error"
    assert stored["worker_attempts"][1]["status"] == "completed"
    assert stored["receipt"]["postcondition_verified"] is True


def test_web_pult_coding_page_contains_opencode_workbench_and_apply_controls():
    ui_file = ct._canonical_bossman_repo() / "command-center" / "ui" / "pages" / "coding.js"
    text = ui_file.read_text(encoding="utf-8")
    for marker in (
        "opencode-workbench",
        "opencode-self-code-btn",
        "opencode-connect-key-btn",
        "coding-worker-select",
        "/api/coding-tasks/workers",
        "/api/coding-tasks/connect-key",
        "/api/coding-tasks/self-target",
        "/apply",
        "Mission Receipt v1",
        "data-task-cost",
        "Local → Free Cloud → Cheap Paid → Premium",
    ):
        assert marker in text, f"missing OpenCode Web Pult marker in coding.js: {marker}"

    local_cost = ct.estimate_coding_task_cost({"status": "completed", "worker": None, "sidecar": {"steps": 4}})
    assert local_cost["cost_usd"] == 0.0 and local_cost["cost_tier"] == "local" and local_cost["within_spend_cap"] is True
    free_cost = ct.estimate_coding_task_cost({"status": "completed", "worker": "poolside-laguna-free", "sidecar": {"steps": 5}})
    assert free_cost["cost_usd"] == 0.0 and free_cost["cost_tier"] == "free" and free_cost["within_spend_cap"] is True
    paid_cost = ct.estimate_coding_task_cost({"status": "completed", "worker": "deepseek-v4.1-flash", "sidecar": {"steps": 4}})
    assert paid_cost["cost_usd"] > 0.0 and paid_cost["cost_tier"] == "cheap_paid" and paid_cost["within_spend_cap"] is True
