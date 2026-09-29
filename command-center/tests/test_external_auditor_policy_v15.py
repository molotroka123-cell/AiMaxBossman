from __future__ import annotations

import json
from pathlib import Path

from bcc.economy_orchestrator import model_policy

ROOT = Path(__file__).resolve().parents[2]


def _json(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_aster_is_hard_read_only_in_runtime_policy():
    policy = model_policy()
    assert policy["auditor"] == "aster_read_only_external"
    ap = policy["external_auditor_policy"]
    assert ap["may_write_code"] is False
    assert ap["may_apply_patch"] is False
    assert ap["may_commit"] is False
    assert ap["may_merge"] is False
    assert ap["may_promote_candidate"] is False
    assert ap["may_run_tests"] is True
    assert policy["rules"]["bossman_owns_repairs"] is True
    assert policy["rules"]["aster_never_codes"] is True


def test_self_improvement_config_forbids_external_auditor_writes():
    cfg = _json("config/v1.5/self-improvement.json")
    p = cfg["aster_policy"]
    for key in ("code_write", "patch_apply", "commit", "merge", "candidate_promotion"):
        assert p[key] is False, key
    assert cfg["self_improvement_north_star"]["routine_external_coder_dependency"] is False


def test_provider_signup_is_owner_required_not_bossman_automation():
    cfg = _json("config/v1.5/provider-pool.json")["policy"]
    assert cfg["auto_signup"] is False
    assert cfg["bossman_may_create_account"] is False
    assert cfg["bossman_may_open_signup_page"] is False
    assert cfg["bossman_may_fill_signup_fields"] is False
    assert cfg["owner_account_creation_required"] is True
    assert cfg["aster_owner_required_helper"] is True
    assert cfg["owner_completes_registration"] is True
    assert cfg["owner_may_supply_fields_via_telegram"] is True


def test_machine_policy_matches_no_code_contract():
    cfg = _json("config/v1.5/external-auditor-policy.json")
    forbidden = set(cfg["forbidden"])
    assert {"write_product_code", "apply_patch", "create_fix_commit", "merge_fix"} <= forbidden
    assert cfg["provider_onboarding"]["bossman_auto_signup"] is False
    assert cfg["provider_onboarding"]["aster_auto_signup"] is False
    assert cfg["provider_onboarding"]["mode"] == "OWNER_REQUIRED"
