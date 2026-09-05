"""Organization Layer → V3 → ЗАМОРОЖЕННЫЙ V2 Command Center: весь стек живьём.

Организация формирует команду и делегирует контракт; V3ExecutionBridge водит
UniversalComputerAgent, собранный адаптерами command_center поверх настоящего
`bcc` (SQLite, реальный `terminal.run`, реальная очередь approvals, реальная
`bcc/v2/verification`). Улика в контракте — файл на диске, перечитанный V2.

Пропускается, если `bcc` не установлен рядом с ядром (Bossman Core CI ставит
только ядро) — честный skip с причиной.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

bcc = pytest.importorskip("bcc", reason="Command Center (bcc) не установлен рядом с ядром")

import httpx  # noqa: E402
import sqlalchemy as sa  # noqa: E402

from bossman_v3.adapters.command_center import CommandCenterRuntime, build_agent  # noqa: E402
from bossman_v3.contracts import SideEffectClass, TypedAction  # noqa: E402
from bossman_v3.execution import PlanStep  # noqa: E402
from bossman_v3.organization import (  # noqa: E402
    EXECUTOR, REVIEWER, AgentProfile, DelegationContract, Department, EvidenceRequirement, MissionState,
    OrganizationRuntime, OrganizationStore, RecordingHumanReview, Resources, RiskTier, TaskState,
    V3ExecutionBridge, step_to_dict)


class Live:
    def __init__(self, rt, svc, work: Path, task: dict, agent: dict, run_id: int):
        self.rt, self.svc, self.work, self.task, self.agent, self.run_id = rt, svc, work, task, agent, run_id

    def approve_all_pending(self) -> int:
        pending = self.rt.call(self.svc.approvals.list(status="pending", limit=100))
        for row in pending:
            self.rt.call(self.svc.approvals.decide(row["id"], True, by="owner-test"))
        return len(pending)

    def executed_tool_calls(self) -> int:
        from bcc.db import tool_calls as t
        async def q():
            async with self.svc.db.session() as s:
                rows = (await s.execute(sa.select(t.c.status))).fetchall()
                return sum(1 for r in rows if r[0] == "executed")
        return self.rt.call(q())


@pytest.fixture
def live(tmp_path):
    from bcc.api import create_app
    from bcc.auth import HEADER
    from bcc.config import Settings
    from bcc.db import settings_kv, task_runs

    rt = CommandCenterRuntime()
    data = tmp_path / "data"
    settings = Settings(data_dir=data, database_url=f"sqlite+aiosqlite:///{data / 'bcc.db'}", ui_dir=tmp_path / "no-ui")
    work = tmp_path / "work"
    work.mkdir()

    async def boot():
        app = create_app(settings, announce_token=False, start_workers=False)
        svc = app.state.svc
        await svc.start()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t",
                                     headers={HEADER: svc.auth.token}) as c:
            pr = (await c.post("/api/providers", json={"name": "p", "kind": "openai_compat",
                  "base_url": "http://127.0.0.1:8080/v1", "api_key": "sk-test-abcd"})).json()
            m = (await c.post("/api/models", json={"provider_id": pr["id"], "name": "m", "alias": "org-live"})).json()
            ag = (await c.post("/api/agents", json={"name": "org", "system_prompt": "-", "model_id": m["id"],
                                                     "max_steps": 4})).json()
            ag = (await c.patch(f"/api/agents/{ag['id']}", json={"tools": ["terminal.run"],
                                "permissions": {"terminal.run": True}})).json()
            task = (await c.post("/api/tasks", json={"title": "org", "prompt": "организация → v3 → v2",
                    "agent_id": ag["id"], "run_now": True})).json()["task"]
        async with svc.db.session() as s:
            await s.execute(sa.insert(settings_kv).values(
                key="terminal.roots", value_enc=svc.vault.encrypt(json.dumps([str(work)]))))
            await s.commit()
            run_id = (await s.execute(sa.select(task_runs.c.id).where(task_runs.c.task_id == task["id"]))).scalar_one()
        return svc, task, ag, run_id

    svc, task, ag, run_id = rt.call(boot())
    yield Live(rt, svc, work, task, ag, run_id)
    rt.call(svc.stop())
    rt.close()


def _step(work: Path, sid: str, name: str) -> dict:
    action = TypedAction("terminal.run",
                         {"command": f"python -c \"open('{name}','w').write('1')\"", "mode": "project_host",
                          "cwd": str(work),
                          "expect": {"kind": "file", "target": str(work / name), "expect": {"exists": True}}},
                         side_effect=SideEffectClass.IDEMPOTENT_WRITE)
    return step_to_dict(PlanStep(sid, f"создать {name}", action))


def _org(live: Live, tmp_path) -> tuple[OrganizationRuntime, RecordingHumanReview]:
    human = RecordingHumanReview()
    bridge = V3ExecutionBridge(
        agent_factory=lambda agent_id, contract: build_agent(live.rt, live.svc, task=live.task,
                                                            agent=live.agent, run_id=live.run_id),
        journal_root=tmp_path / "journals")
    rt = OrganizationRuntime(store=OrganizationStore(tmp_path / "org.sqlite"), execution=bridge, human_review=human)
    rt.register_department(Department("engineering", capabilities={"terminal.run"}, budget=Resources(usd=5)))
    rt.register_agent(AgentProfile("coder", "engineering", {EXECUTOR}, {"terminal.run"}, tier="local_small", model="glm"))
    rt.register_agent(AgentProfile("reviewer", "engineering", {REVIEWER}, {"terminal.run"}, tier="local_small", model="llama"))
    return rt, human


def test_org_mission_over_frozen_v2_waits_for_owner_then_completes_with_real_evidence(live, tmp_path):
    rt, human = _org(live, tmp_path)
    contract = DelegationContract(
        work_id="w1", mission_id="m1", department_id="engineering", goal="создать два файла в проекте",
        required_capability="terminal.run", success_criteria=["оба файла существуют"],
        evidence_required=[EvidenceRequirement("file", str(live.work / "a.txt")),
                           EvidenceRequirement("file", str(live.work / "b.txt"))],
        budget=Resources(usd=0.5), risk=RiskTier.MEDIUM,
        steps=[_step(live.work, "s1", "a.txt"), _step(live.work, "s2", "b.txt")])
    rt.receive_mission("m1", title="live", department_id="engineering", contracts=[contract], source="test")

    # project_host в V2 — всегда ASK: первый прогон останавливается на владельце, ничего не исполнено
    status = rt.run_mission("m1")
    assert status.waiting_approval == ("w1",) and status.state == MissionState.BLOCKED.value
    assert not (live.work / "a.txt").exists() and rt.store.work("w1")["attempts"] == 0
    assert human.requests and "waits for the owner" in human.requests[-1][1]

    # владелец одобряет по одному действию, как в UI; организация продолжает с того же журнала
    for _ in range(3):
        assert live.approve_all_pending() >= 1
        status = rt.run_mission("m1")
        if status.done:
            break
    assert status.done and status.verified_results == ("w1",)
    assert (live.work / "a.txt").read_text() == "1" and (live.work / "b.txt").read_text() == "1"
    assert live.executed_tool_calls() == 2                                   # ровно два реальных исполнения
    r = rt.store.result("w1")
    assert {e.kind for e in r.evidence} == {"file"} and all(e.source.startswith("journal:m1__w1/") for e in r.evidence)
    assert r.reviewed_by == "reviewer" and r.metadata["review"]["approved"] is True


def test_org_over_v2_tool_ran_but_effect_absent_is_failed_not_completed(live, tmp_path):
    rt, _ = _org(live, tmp_path)
    bad = TypedAction("terminal.run", {"command": "python -c \"open('x.txt','w').write('1')\"", "mode": "project_host",
                                       "cwd": str(live.work),
                                       "expect": {"kind": "file", "target": str(live.work / "y.txt"),
                                                  "expect": {"exists": True}}})
    contract = DelegationContract(
        work_id="w1", mission_id="m1", department_id="engineering", goal="создать y.txt",
        required_capability="terminal.run", success_criteria=["y.txt существует"],
        evidence_required=[EvidenceRequirement("file", str(live.work / "y.txt"))], budget=Resources(usd=0.5),
        risk=RiskTier.LOW, steps=[step_to_dict(PlanStep("s1", "y.txt", bad))])
    rt.receive_mission("m1", title="live", department_id="engineering", contracts=[contract])
    rt.run_mission("m1")
    live.approve_all_pending()
    status = rt.run_mission("m1")
    live.approve_all_pending()
    status = rt.run_mission("m1")

    assert (live.work / "x.txt").exists()                                    # инструмент реально сработал
    assert not status.done and status.verified_results == ()
    # local_small провалился → эскалация на local_strong, которого в отделе нет →
    # BLOCKED с запросом владельцу; ни один слой не назвал это успехом
    assert rt.store.work("w1")["state"] == TaskState.BLOCKED.value
    assert rt.store.result("w1").success is False
    assert status.quality["false_success_attempts"] == 1
    assert rt.learning.stats("coder", "terminal.run").false_success_attempts == 1
