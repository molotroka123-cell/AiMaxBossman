from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "config" / "v1.5" / "model-routing-stack.json"


def test_owner_model_routing_stack_is_explicit_and_jev_controlled():
    data = json.loads(STACK.read_text(encoding="utf-8"))
    assert data["controller"] == "bossman/jev"
    assert data["no_silent_alias_substitution"] is True
    roles = data["roles"]
    assert roles["simple_task"]["preferred_alias"] == "Qwen3.6"
    assert roles["coding_project"]["preferred_alias"] == "Qwen3.8"
    assert roles["coding_project"]["workers"] == ["Xing", "Occamy"]
    assert roles["screen_browser"]["preferred_alias"] == "Nex-N2.5"
    assert roles["hard_reasoning"]["preferred_alias"] == "Flash-Next"
    assert roles["verifier"]["preferred_alias"] == "gpt-oss-120B"
    assert roles["verifier"]["must_be_independent_from_worker"] is True
    assert roles["image"]["preferred_alias"] == "Qwen-Image-2.1"
    assert roles["video"]["preferred_aliases"] == ["LTX-2.5", "Wan"]


def test_model_stack_requires_live_refresh_and_no_silent_paid_fallback():
    data = json.loads(STACK.read_text(encoding="utf-8"))
    assert data["aster_bootstrap"]["must_run_first"] is True
    assert data["routing_rules"]["measure_before_route"] is True
    assert data["routing_rules"]["unknown_model_state_is_not_green"] is True
    assert data["routing_rules"]["paid_cloud_never_silent"] is True
    cloud = data["cloud_economy_overlay"]
    assert cloud["nemotron_teacher_swarm"].endswith(":free")
    assert cloud["ling_coder"].endswith(":free")
    assert cloud["glm_finalizer"] == "z-ai/glm-5.3-flash"
