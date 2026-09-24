from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _module():
    path = ROOT / "tools" / "v15_economy_orchestrator.py"
    sys.path.insert(0, str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location("v15eco", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_policy_is_free_first_with_three_independent_nemotron_roles():
    m = _module()
    policy = m.load_policy()
    roles = policy["roles"]
    assert set(m.NEMOTRON_ROLES) == {
        "nemotron_extract", "nemotron_skeptic", "nemotron_curriculum"
    }
    assert len({roles[r]["purpose"] for r in m.NEMOTRON_ROLES}) == 3
    assert all(
        roles[r]["model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"
        and roles[r]["paid"] is False
        for r in m.NEMOTRON_ROLES
    )
    assert roles["ling_coder"]["model"] == "inclusionai/ling-3.0-flash-fin:free"
    assert roles["ling_coder"]["paid"] is False
    assert roles["glm_finalizer"]["model"] == "z-ai/glm-5.3-flash"
    assert roles["glm_finalizer"]["paid"] is True
    assert policy["budget"]["free_first"] is True
    assert policy["budget"]["no_auto_recharge"] is True
    assert policy["trading"] == {
        "execution": "OFF", "paper_only": True, "external_write_actions": "DENY"
    }


def test_glm_has_hard_total_and_call_caps():
    m = _module()
    policy = m.load_policy()
    assert 0 < policy["budget"]["glm_max_total_usd"] <= 0.50
    assert 1 <= policy["budget"]["glm_max_calls"] <= 4


def test_worker_client_refuses_paid_without_explicit_cap(monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    import worker_client
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with pytest.raises(worker_client.PaidViolation):
        worker_client.Worker(worker_client.OPENROUTER, "z-ai/glm-5.3-flash")
    with pytest.raises(worker_client.PaidViolation):
        worker_client.Worker(
            worker_client.OPENROUTER, "z-ai/glm-5.3-flash", allow_paid=True)
    worker = worker_client.Worker(
        worker_client.OPENROUTER, "z-ai/glm-5.3-flash",
        allow_paid=True, max_total_cost_usd=0.50)
    assert worker.budget()["max_total_cost_usd"] == 0.50


def test_learning_run_never_self_promotes(tmp_path, monkeypatch):
    m = _module()
    video = tmp_path / "inbox" / "video1"
    video.mkdir(parents=True)
    (video / "candidate_cases.jsonl").write_text(
        json.dumps({
            "case_id": "x", "timestamp_seconds": 0,
            "transcript_excerpt": "public evidence",
            "observation": {"chart_price": 1},
            "future_outcomes": {"900s": {"return_pct": 1}},
            "learning_status": "UNVERIFIED",
        }) + "\n", encoding="utf-8")

    class FakeJev:
        def __init__(self, require=True):
            self.require = require
        def choose(self, stage, options, summary):
            return m.JevRoute(next(iter(options)), 1.0, "jev-test")

    class FakeWorker:
        model = "fake"
        def chat(self, *args, **kwargs):
            return {
                "text": json.dumps({"verdict": "PASS"}),
                "error": None, "record_id": "r", "budget": {},
            }
        def budget(self):
            return {}

    class FakeRecorder:
        count = 0
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(m, "JevCoordinator", FakeJev)
    monkeypatch.setattr(m, "_worker", lambda *args, **kwargs: FakeWorker())
    monkeypatch.setattr(m, "Recorder", FakeRecorder)

    result = m.run(
        m.load_policy(), tmp_path / "inbox", tmp_path / "out",
        require_jev=True, allow_paid=False, paid_cap=0.50,
        run_ling_scenarios=False,
    )
    assert result["status"] == "COMPLETE_QUARANTINED"
    assert result["videos"][0]["promotion"] == "QUARANTINE"
    assert result["weights_changed"] is False
