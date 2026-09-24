from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_provider_pool_forbids_automatic_signup_and_quota_evasion(monkeypatch):
    pool = load_module("v15_provider_pool_test", "tools/v15_provider_pool.py")
    data = pool.load(ROOT / "config/v1.5/provider-pool.json")
    assert data["policy"]["auto_signup"] is False
    assert data["policy"]["multi_account_limit_evasion"] is False
    assert data["policy"]["unknown_price_is_blocked"] is True
    assert data["policy"]["private_data_to_free_cloud"] is False
    assert {p["id"] for p in data["providers"]} >= {
        "openrouter", "nvidia_nim", "groq", "cerebras", "gemini"
    }
    for provider in data["providers"]:
        assert provider["base_url"].startswith("https://")
        monkeypatch.delenv(provider["key_env"], raising=False)
        monkeypatch.delenv(pool.confirm_env(provider), raising=False)
    monkeypatch.setattr(pool, "_env_file", lambda _path: {})
    status = pool.status(data)
    assert status["ready_zero_cost_providers"] == []
    assert all(not row["zero_cost_worker_eligible"] for row in status["providers"])


def test_key_does_not_mean_free_until_owner_confirms(monkeypatch):
    pool = load_module("v15_provider_pool_confirm_test", "tools/v15_provider_pool.py")
    data = pool.load(ROOT / "config/v1.5/provider-pool.json")
    provider = next(p for p in data["providers"] if p["id"] == "groq")
    monkeypatch.setenv(provider["key_env"], "local-test-key")
    monkeypatch.delenv(pool.confirm_env(provider), raising=False)
    monkeypatch.setattr(pool, "_env_file", lambda _path: {})
    row = next(r for r in pool.status(data)["providers"] if r["id"] == "groq")
    assert row["key_present"] is True
    assert row["owner_confirmed_free"] is False
    assert row["zero_cost_worker_eligible"] is False
    monkeypatch.setenv(pool.confirm_env(provider), "1")
    row = next(r for r in pool.status(data)["providers"] if r["id"] == "groq")
    assert row["zero_cost_worker_eligible"] is True


def test_self_improvement_policy_moves_routine_work_off_aster_and_claude():
    cfg = json.loads((ROOT / "config/v1.5/self-improvement.json").read_text(encoding="utf-8"))
    assert cfg["controller"] == "bossman"
    assert cfg["router"] == "jev"
    assert cfg["external_auditor"] == "bootstrap_then_optional_red_team_only"
    assert cfg["owner_supervisor"] == "telegram_ux_cmd"
    assert cfg["aster_role"] == "launch_self_improvement_then_detach"
    assert cfg["exit_gate"]["status"] == "ASTER_DETACHED_CONTINUITY_PASS"
    assert cfg["exit_gate"]["requires_campaign_started"] is True
    assert cfg["self_improvement_north_star"]["aster_required_after_bootstrap"] is False
    assert cfg["provider_pool"]["target_ready_zero_cost_providers"] >= cfg["provider_pool"]["minimum_ready_zero_cost_providers"]
    assert cfg["runtime_repair"]["auto_create_local_candidate_branch"] is True
    assert cfg["runtime_repair"]["auto_write_stable"] is False
    assert cfg["codex_role"] == "owner_integrator_only"
    assert cfg["claude_role"] == "not_required_for_routine_loop"
    assert cfg["evolution"]["backend"] == "bossman_coding"
    assert 1 <= cfg["evolution"]["max_cycles"] <= 20
    assert cfg["promotion"]["model_done_is_pass"] is False
    assert cfg["promotion"]["requires_unseen_transfer"] is True
    assert cfg["promotion"]["requires_security_non_regression"] is True
    assert cfg["promotion"]["owner_promotion_required"] is True
    assert cfg["promotion"]["auto_write_stable"] is False
    assert cfg["trading"]["execution"] == "OFF"
    assert cfg["trading"]["paper_only"] is True


def test_self_improve_command_uses_bossman_coding_not_claude(tmp_path):
    mod = load_module("v15_self_improve_test", "tools/bossman_15_self_improve.py")
    cfg = json.loads((ROOT / "config/v1.5/self-improvement.json").read_text(encoding="utf-8"))
    cmd = mod.evolution_command(
        cfg,
        repo=ROOT,
        data_dir=tmp_path / "data",
        api_url="http://127.0.0.1:8800",
        work=tmp_path / "work",
        student_model="inclusionai/ling-3.0-flash-fin:free",
        cycles=3,
    )
    joined = " ".join(str(x) for x in cmd)
    assert "--backend bossman_coding" in joined
    assert "--executor host" in joined
    assert "--max-cycles 3" in joined
    assert "inclusionai/ling-3.0-flash-fin:free" in joined
    assert " claude " not in (" " + joined + " ")


def test_self_improve_economy_uses_bossman_api(monkeypatch, tmp_path):
    mod = load_module("v15_self_improve_api_test", "tools/bossman_15_self_improve.py")

    class FakeApi:
        def __init__(self):
            self.posts = []
            self.gets = 0

        def post(self, path, payload=None):
            self.posts.append((path, payload))
            if path == "/api/v15/economy/start":
                return 200, {"status": "STARTING", "pid": 123}
            if path == "/api/v15/economy/stop":
                return 200, {"status": "STOP_REQUESTED"}
            return 404, {}

        def get(self, path):
            self.gets += 1
            assert path == "/api/v15/economy/status"
            return 200, {"running": False, "run": {"status": "COMPLETE_QUARANTINED"}}

    client = FakeApi()
    out = mod.run_economy_via_bossman(
        client, inbox=tmp_path, allow_glm=True, glm_cap=0.50, timeout_s=5,
    )
    assert out["status"] == "FINISHED"
    assert client.posts[0][0] == "/api/v15/economy/start"
    assert client.posts[0][1]["inbox"] == str(tmp_path)
    assert client.posts[0][1]["allow_paid_finalizer"] is True
    assert client.posts[0][1]["glm_cap_usd"] == 0.50
    assert client.posts[0][1]["run_ling_scenarios"] is True


def test_self_improve_api_timeout_requests_durable_stop(monkeypatch, tmp_path):
    mod = load_module("v15_self_improve_timeout_test", "tools/bossman_15_self_improve.py")

    class FakeApi:
        def __init__(self):
            self.posts = []

        def post(self, path, payload=None):
            self.posts.append(path)
            return 200, {"status": "STARTING"}

        def get(self, path):
            return 200, {"running": True, "run": {"status": "RUNNING"}}

    ticks = iter([0.0, 0.1, 1.1, 1.2])
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    client = FakeApi()
    out = mod.run_economy_via_bossman(
        client, inbox=tmp_path, allow_glm=False, glm_cap=0.25, timeout_s=1.0,
    )
    assert out["status"] == "TIMEOUT_STOP_REQUESTED"
    assert client.posts[-1] == "/api/v15/economy/stop"


def test_self_improve_accepts_exact_installed_bundle_manifest(tmp_path):
    mod = load_module("v15_self_improve_installed_test", "tools/bossman_15_self_improve.py")
    sha = "d" * 40
    (tmp_path / "MANIFEST.json").write_text(
        json.dumps({"source_sha": sha, "source_dirty": False}), encoding="utf-8")
    got_sha, clean, kind = mod._source_identity(tmp_path)
    assert got_sha == sha
    assert clean is True
    assert kind == "installed"


def test_self_improve_refuses_unproven_installed_source(tmp_path):
    mod = load_module("v15_self_improve_unproven_test", "tools/bossman_15_self_improve.py")
    got_sha, clean, kind = mod._source_identity(tmp_path)
    assert got_sha == "unknown"
    assert clean is False
    assert kind == "installed-unproven"


def test_provider_onboarding_metadata_is_owner_safe_and_prioritized(monkeypatch):
    pool = load_module("v15_provider_pool_onboarding_test", "tools/v15_provider_pool.py")
    data = pool.load(ROOT / "config/v1.5/provider-pool.json")
    assert data["policy"]["provider_onboarding_mode"] == "OWNER_REQUIRED_PARALLEL_NONBLOCKING"
    assert data["policy"]["aster_may_accept_terms"] is False
    assert data["policy"]["aster_may_solve_captcha"] is False
    assert data["policy"]["aster_may_create_api_key"] is False
    priorities = [int(p.get("priority") or 999) for p in data["providers"]]
    assert min(priorities) == 1
    assert all((p.get("free_evidence") or {}).get("status") for p in data["providers"])
    for provider in data["providers"]:
        monkeypatch.delenv(provider["key_env"], raising=False)
        monkeypatch.delenv(pool.confirm_env(provider), raising=False)
    monkeypatch.setattr(pool, "_env_file", lambda _path: {})
    rows = pool.status(data)["providers"]
    assert all("free_evidence" in row and "priority" in row for row in rows)
