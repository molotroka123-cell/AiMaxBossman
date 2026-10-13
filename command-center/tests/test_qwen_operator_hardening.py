"""Package B (2.2 sprint): context sufficiency monitor, read-only tasks, queue, decision pump, timings,
non-blocking lab creation. Everything offline with fakes (see test_qwen_operator.py for the fixtures)."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from bcc.features import qwen_operator as qo
from bcc.telegram_companion.operator_bridge import receipt_text

from .test_qwen_operator import PLAN, Fakes, repo, run_task, stack  # noqa: F401  (fixtures)


def _run(chat, num_ctx=65536):
    cfg = {"planner": {"model": "m", "num_ctx": num_ctx}}
    return qo.Run(None, {"model_calls": [], "steps": []}, cfg, None, local_chat=chat)


def _chat_factory(tin, truncated=False):
    calls = []

    async def chat(model, messages, **kw):
        calls.append(kw.get("num_ctx"))
        return {"ok": True, "model": model, "text": "x", "tokens_in": tin, "tokens_out": 3,
                "truncated": truncated if len(calls) == 1 else False, "seconds": 0.1}
    return chat, calls


async def test_under_limit_no_retry():
    chat, calls = _chat_factory(1000)
    run = _run(chat)
    res = await run.chat("planner", [{"role": "user", "content": "hi"}])
    assert calls == [65536] and "ctx_retry" not in res
    assert res["ctx_pressure_pct"] < 10 and not res["ctx_truncated"]
    assert qo.ctx_report([run.usage(res, "planner")])["insufficient"] is False


async def test_over_limit_retries_once_with_the_next_larger_ctx():
    chat, calls = _chat_factory(60000)             # 60000 + 2048 reserve > 80% of 64k
    run = _run(chat)
    res = await run.chat("planner", [{"role": "user", "content": "hi"}])
    assert calls == [65536, 131072]
    assert res["ctx_retry"] == {"from": 65536, "to": 131072, "first_pct": 95, "first_truncated": False}
    assert run.cfg["planner"]["num_ctx"] == 131072          # later calls start at the larger window
    rep = qo.ctx_report([run.usage(res, "planner")])
    assert rep["insufficient"] and rep["max_pct"] == 95


async def test_truncation_flag_forces_retry_even_with_few_tokens():
    chat, calls = _chat_factory(500, truncated=True)
    run = _run(chat)
    res = await run.chat("planner", [{"role": "user", "content": "hi"}])
    assert calls == [65536, 131072] and res["ctx_retry"]["first_truncated"] is True
    assert qo.ctx_report([run.usage(res, "planner")])["truncated"] is True


async def test_no_retry_above_the_top_of_the_ladder():
    chat, calls = _chat_factory(250000)
    run = _run(chat, num_ctx=qo.CTX_256K)
    res = await run.chat("planner", [{"role": "user", "content": "hi"}])
    assert calls == [qo.CTX_256K] and res["ctx_pressure_pct"] > 80


def test_receipt_says_context_was_not_enough():
    calls = [{"role": "planner", "model": "m", "ctx_pressure_pct": 47, "ctx_retry":
              {"from": 65536, "to": 131072, "first_pct": 95, "first_truncated": False}}]
    rec = {"id": "a" * 12, "status": "completed", "model_calls": calls, "ctx_report": qo.ctx_report(calls),
           "timings": {"total_s": 700, "by_role_s": {"coder": 612, "planner": 40}, "tests_s": 25, "load_s": 30,
                       "phase_s": {"coder": 612, "plan": 40}, "ctx_by_role": {"planner": 131072, "coder": 65536}}}
    text = receipt_text(rec)
    assert "контекста не хватало: 95%" in text and "128k" in text
    assert "coder 10м12с" in text and "тесты 25с" in text and "Куда ушло время" in text


def test_default_roles_are_64k():
    assert all(qo.DEFAULT_ROLES[r]["num_ctx"] == 65536 for r in qo.LOCAL_ROLES)


# ---------------------------------------------------------------- read-only tasks
def test_read_only_detection():
    assert qo.is_read_only("проанализируй конституцию и расскажи", {}) is True
    assert qo.is_read_only("поправь докстринг", {}) is False
    assert qo.is_read_only("любое", {"read_only": True}) is True
    assert qo.is_read_only("проанализируй", {"read_only": False}) is False
    assert qo.is_read_only("прочитай и объясни", {"extra_commands": [{"argv": ["x"]}]}) is False


async def test_read_only_analysis_of_protected_file_asks_nothing(stack, repo):  # noqa: F811
    svc, _c, _a = stack
    plan = {"summary": "analysis", "files": [], "read_files": ["docs/constitution/BOSSMAN_CONSTITUTION.md"],
            "read_only": True, "edit_instruction": "", "verify_tests": [], "extra_commands": []}
    rec = await run_task(svc, repo, Fakes(repo, plan), task="проанализируй конституцию")
    assert rec["status"] == "completed", rec.get("error")
    assert rec["decisions"] == [] and rec["analysis"] and not rec.get("commit_sha")
    assert not await svc.approvals.list("pending")
    assert rec["timings"]["total_s"] >= 0 and "ctx_report" in rec


async def test_protected_file_in_files_of_a_read_only_plan_is_not_a_write_request(stack, repo):  # noqa: F811
    svc, _c, _a = stack
    plan = {"summary": "s", "files": ["docs/constitution/BOSSMAN_CONSTITUTION.md"], "edit_instruction": "",
            "verify_tests": [], "extra_commands": []}
    rec = await run_task(svc, repo, Fakes(repo, plan), task="прочитай и объясни конституцию")
    assert rec["decisions"] == [] and rec["status"] == "completed"


async def test_real_write_to_protected_file_still_parks(stack, repo):  # noqa: F811
    svc, _c, _a = stack
    plan = {**PLAN, "files": ["pkg/mod.py", "docs/constitution/BOSSMAN_CONSTITUTION.md"], "read_only": False}
    rec = await run_task(svc, repo, Fakes(repo, plan))
    assert [d["category"] for d in rec["decisions"]] == ["protected_file"]
    assert rec["decisions"][0]["action"].startswith("изменить файл")


# ---------------------------------------------------------------- per-task ctx, timings
async def test_per_task_ctx_override_and_timings(stack, repo):  # noqa: F811
    svc, _c, _a = stack
    f = Fakes(repo, PLAN)
    rec = await qo.start_task(svc, "улучши докстринг", source_repo=str(repo), decision_wait_s=0, num_ctx=131072,
                              local_chat=f.local_chat, free_cloud=f.free_cloud, coder=f.coder)
    await qo._JOBS[rec["id"]]
    saved = _saved(svc, rec["id"])
    assert saved["status"] == "completed" and saved["num_ctx_override"] == 131072
    assert {saved["roles"][r]["num_ctx"] for r in qo.LOCAL_ROLES} == {131072}
    tm = saved["timings"]
    assert "coder" in tm["by_role_s"] and tm["phase_s"] and tm["tests_s"] >= 0
    assert tm["ctx_by_role"]["coder"] == 131072


async def test_bad_ctx_is_rejected(stack, repo):  # noqa: F811
    svc, _c, _a = stack
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        await qo.start_task(svc, "t", source_repo=str(repo), num_ctx=100)


# ---------------------------------------------------------------- queue + decision pump
class Gate(Fakes):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.release = asyncio.Event()
        self.entered = asyncio.Event()

    async def coder(self, run, instruction, paths, tests, attempt):
        self.entered.set()
        await self.release.wait()
        return await super().coder(run, instruction, paths, tests, attempt)


def _saved(svc, rid):
    return json.loads((Path(svc.settings.data_dir) / "qwen-operator" / f"{rid}.json").read_text("utf-8"))


async def test_second_task_is_queued_fifo_and_can_be_cancelled(stack, repo):  # noqa: F811
    svc, client, _a = stack
    g = Gate(repo, PLAN)
    first = await qo.start_task(svc, "первая", source_repo=str(repo), decision_wait_s=0, local_chat=g.local_chat,
                                free_cloud=g.free_cloud, coder=g.coder)
    await asyncio.wait_for(g.entered.wait(), 10)
    g2 = Fakes(repo, PLAN)
    second = await qo.start_task(svc, "вторая", source_repo=str(repo), decision_wait_s=0, local_chat=g2.local_chat,
                                 free_cloud=g2.free_cloud, coder=g2.coder)
    third = await qo.start_task(svc, "третья", source_repo=str(repo), decision_wait_s=0, local_chat=g2.local_chat,
                                free_cloud=g2.free_cloud, coder=g2.coder)
    await asyncio.sleep(0.2)
    assert second["queue_position"] == 1 and qo._QUEUE == [second["id"], third["id"]]
    listing = (await client.get("/api/qwen-operator/tasks")).json()
    assert listing["queue"] == [second["id"], third["id"]]
    assert _saved(svc, second["id"])["status"] == "queued"
    out = (await client.post(f"/api/qwen-operator/tasks/{second['id']}/cancel")).json()
    assert out["cancelled"] is True
    with pytest.raises(asyncio.CancelledError):
        await qo._JOBS[second["id"]]
    assert _saved(svc, second["id"])["status"] == "stopped"
    g.release.set()
    await qo._JOBS[first["id"]]
    await qo._JOBS[third["id"]]
    assert _saved(svc, first["id"])["status"] == "completed"
    assert _saved(svc, third["id"])["status"] in ("completed", "failed")      # ran after the first, in order
    assert qo._QUEUE == []


async def test_protected_answer_is_recorded_before_the_step_ends(stack, repo):  # noqa: F811
    svc, _c, _a = stack
    plan = {**PLAN, "files": ["pkg/mod.py", "docs/constitution/BOSSMAN_CONSTITUTION.md"], "read_only": False}
    g = Gate(repo, plan)
    rec = await qo.start_task(svc, "t", source_repo=str(repo), decision_wait_s=0, local_chat=g.local_chat,
                              free_cloud=g.free_cloud, coder=g.coder)
    await asyncio.wait_for(g.entered.wait(), 10)           # the coder step is running; decision is parked
    pend = await svc.approvals.list("pending")
    assert len(pend) == 1
    await svc.approvals.decide(pend[0]["id"], True, "owner")
    d = {}
    for _ in range(100):
        d = _saved(svc, rec["id"])["decisions"][0]
        if d["status"] != "PENDING":
            break
        await asyncio.sleep(0.1)
    assert d["status"] == "APPROVED_NOT_EXECUTED" and "не выполняет" in d["note"]
    assert not g.release.is_set()                          # known while the step is still running
    g.release.set()
    await qo._JOBS[rec["id"]]


# ---------------------------------------------------------------- the event loop stays free
async def test_health_live_answers_within_a_second_during_slow_lab_creation(stack, repo, monkeypatch):  # noqa: F811
    svc, client, _a = stack
    real = qo.create_lab

    def slow_lab(*a, **k):
        time.sleep(2.0)                                    # a fake slow git clone / worktree add
        return real(*a, **k)
    monkeypatch.setattr(qo, "create_lab", slow_lab)
    f = Fakes(repo, PLAN)
    starter = asyncio.create_task(qo.start_task(svc, "медленная лаборатория", source_repo=str(repo),
                                                decision_wait_s=0, local_chat=f.local_chat,
                                                free_cloud=f.free_cloud, coder=f.coder))
    await asyncio.sleep(0.2)
    worst = 0.0
    for _ in range(5):
        t0 = time.monotonic()
        r = await client.get("/health/live")
        worst = max(worst, time.monotonic() - t0)
        assert r.status_code == 200
        await asyncio.sleep(0.1)
    assert not starter.done(), "the lab creation must still be running while health answered"
    assert worst < 1.0, worst
    rec = await starter
    await qo._JOBS[rec["id"]]
