from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
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
    assert cfg["external_auditor"] == "aster"
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
