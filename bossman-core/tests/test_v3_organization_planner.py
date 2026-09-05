"""ORG-02 (TZ-04 §2.2): контракт без шагов → детерминированный план из лексикона
или BLOCKED/no_executable_steps (не FAILED). Приёмка TZ-04 §7 «PlannerPort»."""
from __future__ import annotations

from bossman_v3.organization import (EXECUTOR, AgentProfile, DelegationContract, Department, DeterministicPlanner,
                                     EvidenceRequirement, OrganizationRuntime, OrganizationStore, RecordingHumanReview,
                                     Resources, RiskTier, TaskState, WorkResult)

SUPPORTED = {"terminal.run", "browser.open"}


def _c(goal, *, cap="terminal.run", evidence=(), **kw):
    return DelegationContract(work_id="w1", mission_id="m1", department_id="engineering", goal=goal,
                              required_capability=cap, success_criteria=["ок"], evidence_required=list(evidence),
                              budget=Resources(usd=1.0, compute_seconds=60), risk=RiskTier.LOW, **kw)


def test_planner_builds_file_step_with_evidence_expect(tmp_path):
    p = DeterministicPlanner(SUPPORTED.__contains__, workspace=str(tmp_path))
    target = str(tmp_path / "a.txt")
    steps = p.plan(_c(f"создай файл {target} с текстом «привет»", evidence=[EvidenceRequirement("file", target)]))
    assert steps and steps[0]["action"]["action_type"] == "terminal.run"
    args = steps[0]["action"]["args"]
    assert args["expect"] == {"kind": "file", "target": target, "expect": {"exists": True}}
    assert "привет" in args["command"] and args["cwd"] == str(tmp_path)
    assert steps[0]["action"]["side_effect"] == "IDEMPOTENT_WRITE"


def test_planner_open_url_and_run_command():
    p = DeterministicPlanner(SUPPORTED.__contains__)
    steps = p.plan(_c("открой https://example.org/docs", cap="browser.open"))
    assert steps[0]["action"]["action_type"] == "browser.open" and steps[0]["action"]["args"]["url"] == "https://example.org/docs"
    steps = p.plan(_c("выполни команду `pytest -q`", side_effect=False))
    assert steps[0]["action"]["args"]["command"] == "pytest -q" and steps[0]["action"]["side_effect"] == "READ_ONLY"


def test_planner_refuses_unknown_goal_unsupported_tool_and_capability_mismatch():
    p = DeterministicPlanner(SUPPORTED.__contains__)
    assert p.plan(_c("сделай всё красиво и быстро")) is None
    # способность контракта — terminal.run, а план требует browser.open → не выдаётся
    assert p.plan(_c("открой https://example.org", cap="terminal.run")) is None
    # инструмент не зарегистрирован у исполнителя
    assert DeterministicPlanner(lambda t: False).plan(_c("выполни ls")) is None
    assert p.plan(_c('выполни команду echo "незакрытая кавычка')) is None


class _Bridge:
    def __init__(self):
        self.seen = []

    def execute(self, contract, *, agent_id):
        self.seen.append([dict(s) for s in contract.steps])
        return WorkResult(contract.work_id, executed=False, produced_by=agent_id, reason="stub")


def _runtime(tmp_path, planner):
    bridge = _Bridge()
    rt = OrganizationRuntime(store=OrganizationStore(tmp_path / "org.sqlite"), execution=bridge,
                             human_review=RecordingHumanReview(), planner=planner)
    rt.register_department(Department("engineering", capabilities={"terminal.run"}, budget=Resources(usd=5, compute_seconds=600)))
    rt.register_agent(AgentProfile("coder", "engineering", {EXECUTOR}, {"terminal.run"}, tier="local_small", model="glm"))
    return rt, bridge


def test_runtime_without_steps_is_blocked_not_failed(tmp_path):
    rt, bridge = _runtime(tmp_path, planner=None)
    rt.receive_mission("m1", title="x", department_id="engineering",
                       contracts=[_c("сделай красиво", evidence=[EvidenceRequirement("file", str(tmp_path / "a.txt"))])])
    status = rt.run_mission("m1")
    work = rt.store.work("w1")
    assert work["state"] == TaskState.BLOCKED.value and work["attempts"] == 0 and bridge.seen == []
    assert "no_executable_steps" in work["contract"].metadata["runtime"]["last_reason"]
    assert status.blockers and rt.human_review.requests and "no_executable_steps" in rt.human_review.requests[-1][1]


def test_runtime_plans_missing_steps_and_delegates_them(tmp_path):
    rt, bridge = _runtime(tmp_path, planner=DeterministicPlanner(SUPPORTED.__contains__))
    target = str(tmp_path / "a.txt")
    rt.receive_mission("m1", title="x", department_id="engineering",
                       contracts=[_c(f"создай файл {target}", evidence=[EvidenceRequirement("file", target)])])
    rt.run_mission("m1")
    assert bridge.seen and bridge.seen[0][0]["action"]["action_type"] == "terminal.run"
    work = rt.store.work("w1")
    assert work["contract"].steps and work["contract"].metadata["planned_by"] == "DeterministicPlanner"
    assert any(e["event"] == "work.planned" for e in rt.store.tail(50, mission_id="m1"))
