"""Branch 9 (agents): the owner's STOP / cancel is honoured BETWEEN every step of an operator task, not only at
the start of an attempt. Offline: the planner, the coder and the cloud are the fakes of test_qwen_operator.py."""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc.features import qwen_operator as qo

from .test_qwen_operator import PLAN, Fakes, git, repo, stack  # noqa: F401  (fixtures)


def _saved(svc, rid):
    return json.loads((Path(svc.settings.data_dir) / "qwen-operator" / f"{rid}.json").read_text("utf-8"))


def _stop_file(svc) -> Path:
    stop = Path(svc.settings.data_dir) / "computer" / "STOP"
    stop.parent.mkdir(parents=True, exist_ok=True)
    return stop


async def _run(svc, repo, f, **kw):
    rec = await qo.start_task(svc, "улучши докстринг", source_repo=str(repo), decision_wait_s=0,
                              local_chat=kw.get("local_chat", f.local_chat), free_cloud=f.free_cloud,
                              coder=kw.get("coder", f.coder))
    await qo._JOBS[rec["id"]]
    return _saved(svc, rec["id"])


def _lab_untouched(saved):
    lab = Path(saved["lab"]["path"])
    assert git(lab, "rev-parse", "HEAD") == saved["lab"]["base_commit"]
    assert git(lab, "status", "--porcelain") == ""


async def test_stop_pressed_while_the_coder_runs_applies_and_commits_nothing(stack, repo):  # noqa: F811
    svc, _c, _a = stack
    f = Fakes(repo, PLAN)
    stop = _stop_file(svc)

    async def coder(run, instruction, paths, tests, attempt):
        out = await f.coder(run, instruction, paths, tests, attempt)
        stop.write_text("{}", encoding="utf-8")          # the owner pressed STOP during the (long) coder call
        return out
    try:
        saved = await _run(svc, repo, f, coder=coder)
    finally:
        stop.unlink(missing_ok=True)
    assert saved["status"] == "stopped", saved.get("error")
    assert not saved.get("commit_sha") and not saved.get("diff")
    assert not any(c.get("why") == "mandatory verification" for c in saved["commands"])   # no lab test run
    assert not saved["decisions"]                                                        # no release asked
    _lab_untouched(saved)


async def test_stop_pressed_during_the_lab_tests_leaves_no_commit(stack, repo, monkeypatch):  # noqa: F811
    svc, _c, _a = stack
    f = Fakes(repo, PLAN)
    stop = _stop_file(svc)
    real = qo.run_tests_in_lab

    def tests_then_stop(scope, tests):
        out = real(scope, tests)
        stop.write_text("{}", encoding="utf-8")
        return out
    monkeypatch.setattr(qo, "run_tests_in_lab", tests_then_stop)
    try:
        saved = await _run(svc, repo, f)
    finally:
        stop.unlink(missing_ok=True)
    assert saved["status"] == "stopped", saved.get("error")
    assert not saved.get("commit_sha")
    _lab_untouched(saved)


async def test_stop_pressed_while_the_planner_runs_starts_no_further_step(stack, repo):  # noqa: F811
    svc, _c, _a = stack
    f = Fakes(repo, PLAN)
    stop = _stop_file(svc)

    async def planner_then_stop(model, messages, **kw):
        out = await f.local_chat(model, messages, **kw)
        if kw.get("json_mode"):
            stop.write_text("{}", encoding="utf-8")
        return out
    try:
        saved = await _run(svc, repo, f, local_chat=planner_then_stop)
    finally:
        stop.unlink(missing_ok=True)
    assert saved["status"] == "stopped"
    assert f.coder_calls == 0 and not saved.get("worker_draft")


async def test_a_resumed_task_never_asks_the_owner_twice_for_the_same_decision(stack, repo):  # noqa: F811
    svc, _c, _a = stack
    plan = {**PLAN, "read_files": ["docs/secrets/notes.md"]}              # a credential read parks a decision
    f = Fakes(repo, plan)
    first = await _run(svc, repo, f)
    cats = sorted(d["category"] for d in first["decisions"])
    assert cats == ["credentials", "release_main"], cats
    task_file = Path(svc.settings.data_dir) / "qwen-operator" / f"{first['id']}.json"
    first["status"], first["finished_at"] = "running", None                 # killed mid-run after these steps
    task_file.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
    qo._JOBS.clear()
    qo._QUEUE.clear()
    out = await qo.recover_interrupted_tasks(svc, resume=True, decision_wait_s=0, local_chat=f.local_chat,
                                             free_cloud=f.free_cloud, coder=f.coder)
    assert [o["action"] for o in out] == ["resumed"]
    await qo._JOBS[first["id"]]
    final = _saved(svc, first["id"])
    assert sorted(d["category"] for d in final["decisions"]) == cats
    assert len(await svc.approvals.list("pending")) == 2                    # no second approvals row


async def test_cancelling_the_operator_kills_the_coder_sidecar_and_records_it(tmp_path, monkeypatch):
    """asyncio cancel stops only the coroutine; the coder runs in a worker thread with its own process tree."""
    from bcc.features import coding_tasks as ct
    started, killed = threading.Event(), threading.Event()

    class Tree:
        def kill(self):
            killed.set()

    def fake_execute(record, repo, body, context, env, command, data_dir=None):
        ct._ACTIVE[record["id"]] = Tree()
        started.set()
        killed.wait(3)                                      # a real sidecar would run until its process dies
        ct._ACTIVE.pop(record["id"], None)
        cancelled = record["id"] in ct._CANCELLED
        ct._CANCELLED.discard(record["id"])
        return {**record, "status": "failed", "outcome": "CANCELLED" if cancelled else "", "finished_at": 1.0}

    async def variant(base, n, transport=None):
        return {"ok": True, "tag": base, "created": False}
    monkeypatch.setattr(ct, "_runtime", lambda: (object(), object(), ""))
    monkeypatch.setattr(ct, "_execute", fake_execute)
    monkeypatch.setattr(qo, "ensure_ctx_variant", variant)
    (tmp_path / "lab").mkdir()
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=str(tmp_path / "data")))
    run = qo.Run(svc, {"id": "0123456789ab", "operator_agent_id": None},
                 {"coder": {"model": "m", "num_ctx": 4096, "ctx_variant": {"tag": "m"}}},
                 qo.OperatorScope(tmp_path / "lab", data_dir=tmp_path / "data"))
    task = asyncio.create_task(qo.run_sidecar(run, "x", ["pkg/mod.py"], ["tests/test_mod.py"], 1))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(killed.wait, 2), "the coder's process tree was left running"
    store = tmp_path / "data" / "coding-tasks"
    for _ in range(50):                                     # the worker thread writes the terminal record
        recs = [json.loads(p.read_text("utf-8")) for p in store.glob("*.json")] if store.is_dir() else []
        if recs:
            break
        await asyncio.sleep(0.1)
    assert [(r["status"], r["outcome"], r["operator_task"]) for r in recs] == [("failed", "CANCELLED", "0123456789ab")]
