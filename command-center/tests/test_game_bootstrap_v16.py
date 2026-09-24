from bcc.features.game_bootstrap_v16 import (
    BootstrapRequest,
    WINDOWS_EDITOR,
    WINDOWS_TEMPLATE,
    bossblocks_bootstrap_plan,
)


def test_windows_plan_is_pinned_finite_and_has_fallback():
    plan = bossblocks_bootstrap_plan(BootstrapRequest())
    assert plan["status"] == "PLAN_ONLY"
    assert plan["primary"]["editor"]["sha256"] == WINDOWS_EDITOR["sha256"]
    assert plan["primary"]["template"]["sha256"] == WINDOWS_TEMPLATE["sha256"]
    assert len(plan["steps"]) == 7
    assert plan["fallback"]["trigger"].startswith("primary bootstrap/plugin path not READY")
    assert plan["hard_deadline_minutes"] == 20


def test_plan_never_claims_download_happened():
    plan = bossblocks_bootstrap_plan(BootstrapRequest())
    assert "PLAN_ONLY" in plan["truth_rule"]
    assert all(step["verify"] for step in plan["steps"])


def test_telegram_control_keeps_owner_authority():
    plan = bossblocks_bootstrap_plan(BootstrapRequest())
    commands = plan["telegram_control"]["commands"]
    assert "/approvals" in commands
    assert "/stop" in commands
    assert "/resume" in commands
    assert "cannot approve" in commands["/jev <request>"]


def test_non_windows_does_not_invent_windows_success():
    plan = bossblocks_bootstrap_plan(BootstrapRequest(platform="linux"))
    assert plan["status"] == "FALLBACK"
    assert plan["steps"] == []


def test_existing_engine_can_skip_primary_download_plan():
    plan = bossblocks_bootstrap_plan(BootstrapRequest(already_has_compatible_engine=True))
    assert plan["steps"] == []
    assert plan["status"] == "PLAN_ONLY"
