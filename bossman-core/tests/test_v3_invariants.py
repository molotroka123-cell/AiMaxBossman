"""V3 pack — security/safety invariants (feature-gated, no second engine).

Locks the non-negotiables: shell rejection, feature flags OFF by default with
master gating, Guardian anti-context-starvation (P0/P1 survive even over budget).
"""
from __future__ import annotations

import pytest

from bossman_v3.computer_agent.agent import UniversalComputerAgent, UnsafeActionError
from bossman_v3.contracts import TypedAction
from bossman_v3.data_guardian.guardian import ContextDataGuardian
from bossman_v3.data_guardian.models import ContextItem, GuardianConfig
from bossman_v3.feature_flags import V3Flags


# ---------------- feature flags: OFF by default, master gates everything ----------------

def test_flags_off_by_default(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("BOSSMAN_V3"):
            monkeypatch.delenv(k, raising=False)
    f = V3Flags.from_env()
    assert f.master is False
    assert not any([f.computer_agent, f.visual_state, f.self_healing, f.skill_factory,
                    f.recovery_kernel, f.self_improvement, f.data_guardian])


def test_master_gates_subflags(monkeypatch):
    # sub-flag ON but master OFF → still disabled
    monkeypatch.delenv("BOSSMAN_V3_ENABLED", raising=False)
    monkeypatch.setenv("BOSSMAN_V3_DATA_GUARDIAN", "1")
    assert V3Flags.from_env().data_guardian is False
    # master ON + sub ON → enabled
    monkeypatch.setenv("BOSSMAN_V3_ENABLED", "1")
    assert V3Flags.from_env().data_guardian is True


# ---------------- computer agent: raw shell rejected before anything ----------------

@pytest.mark.parametrize("bad", ["shell", "exec", "cmd", "powershell", "shell.run", "arbitrary_shell"])
def test_computer_agent_rejects_raw_shell(bad):
    agent = UniversalComputerAgent(policy=None, approval=None, executor=None,
                                   observer=None, verifier=None)
    with pytest.raises(UnsafeActionError):
        agent.run(TypedAction(action_type=bad, args={"cmd": "rm -rf /"}))


# ---------------- guardian: P0/P1 & protected survive even over budget ----------------

def test_guardian_never_drops_critical_over_budget():
    cfg = GuardianConfig(token_budget=100)   # tiny budget
    g = ContextDataGuardian(cfg)
    items = [
        ContextItem(item_id="sec", category="security", content="x", token_count=500, priority=0),
        ContextItem(item_id="obj", category="objective", content="goal", token_count=500, priority=1),
        ContextItem(item_id="filler", category="misc", content="noise", token_count=10, priority=6,
                    importance=0.01),
    ]
    rep = g.select(items)
    kept = {i.item_id for i in rep.selected}
    # critical/protected must be kept regardless of budget overflow
    assert "sec" in kept and "obj" in kept
    assert rep.selected_tokens >= 1000  # critical wins over the nominal budget


def test_guardian_dedup_keeps_conflicts():
    cfg = GuardianConfig(token_budget=10000)
    g = ContextDataGuardian(cfg)
    a = ContextItem(item_id="c1", category="key_decision", content="use postgres",
                    token_count=5, priority=1, conflict_group="db")
    b = ContextItem(item_id="c2", category="key_decision", content="use sqlite",
                    token_count=5, priority=1, conflict_group="db")
    rep = g.select([a, b])
    kept = {i.item_id for i in rep.selected}
    # both sides of a conflict preserved (never silently collapse contradictions)
    assert "c1" in kept and "c2" in kept
