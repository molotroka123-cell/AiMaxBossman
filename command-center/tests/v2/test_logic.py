from pathlib import Path

from bcc.v2.governor import GovernorState, GovernorThresholds
from bcc.v2.kpi import KPI, mission_progress
from bcc.v2.model_router import ModelCandidate, RouteRequest, route
from bcc.v2.permissions import PermissionPolicy
from bcc.v2.recovery import RecoveryPolicy
from bcc.v2.resource_brain import ResourceSnapshot, Reservation, plan_memory
from bcc.v2.reviewer_gate import ReviewGate
from bcc.v2.terminal_control import TerminalPolicy

def test_router_local_coder_wins():
    models = [
        ModelCandidate(1, "fast", local=True, verified_capabilities={"tools"},
                       role_scores={"coding": .4}, gen_tps=80),
        ModelCandidate(2, "coder", local=True, verified_capabilities={"tools"},
                       role_scores={"coding": .95}, gen_tps=35),
    ]
    d = route(RouteRequest("coding", requires={"tools"}), models)
    assert d.model and d.model.alias == "coder"

def test_router_rejects_cloud_when_disabled():
    d = route(RouteRequest("coding", cloud_allowed=False), [
        ModelCandidate(1, "cloud", local=False, role_scores={"coding": 1.0})
    ])
    assert d.model is None
    assert "cloud disabled" in d.rejected["cloud"]

def test_governor_detects_repeat():
    g = GovernorState(GovernorThresholds(repeated_error_limit=3))
    assert g.record_error("same") == "none"
    assert g.record_error("same") == "none"
    assert g.record_error("same") == "replan"

def test_resource_brain_unloads_idle():
    s = ResourceSnapshot(
        total_memory_mb=128000, used_system_mb=16000, reserve_floor_mb=16000,
        reservations=[Reservation("vision", 12000, idle=True), Reservation("coder", 50000)]
    )
    p = plan_memory(s, 40000)
    assert p.allowed
    assert "vision" in p.unload

def test_kpi_progress():
    a = KPI("sites", "Sites", target=10, current=5)
    b = KPI("offers", "Offers", target=2, current=1)
    assert mission_progress([a, b]) == 0.5

def test_reviewer_bounded():
    g = ReviewGate(max_iterations=2)
    g.submit_for_review()
    assert g.review_result(False, "bad") == "fix"
    g.submit_for_review()
    assert g.review_result(False, "still bad") == "waiting_approval"

def test_recovery_non_idempotent_escalates_after_restart():
    p = RecoveryPolicy(retry_limit=1, restart_limit=1, fallback_allowed=False)
    assert p.choose(retries=1, restarts=1, idempotent=False, fallback_available=False) == "escalate"

def test_terminal_policy():
    root = Path("/tmp/project").resolve()
    policy = TerminalPolicy([root], mode="project_host")
    assert policy.decision("git status", root) == "auto"
    assert policy.decision("git push origin main", root) == "ask"
    assert policy.decision("git push --force origin main", root) == "deny"

def test_permission_default_secret_denied():
    p = PermissionPolicy.safe_default()
    assert p.decide("filesystem.read", "project/.env") == "deny"
    assert p.decide("terminal.run", "git status --short") == "auto"
