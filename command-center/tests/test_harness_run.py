"""Harness run driver (27.11): goal -> real chat path -> owner approvals -> independent check -> honest verdict.

Unit part: a scripted fake API (statuses over time). End-to-end part: the real app — sync creates «Bossman Harness»
on a fake Ollama inventory, the driver sends the goal through /api/chat/threads/…/send, the real engine runs it with a
scripted model, and the verify command is a real subprocess.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest

from bcc.local_operator.harness_run import run_goal, run_verify, verdict_for

from .test_local_operator import _sync
from .test_v21_tool_loop import ToolAdapter

OK_CMD = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
BAD_CMD = f'"{sys.executable}" -c "import sys; print(\'2 failed\'); sys.exit(1)"'


class FakeApi:
    def __init__(self, statuses, *, result="готово", approvals=(), agents=None):
        self.statuses = list(statuses)
        self.result = result
        self.approvals = list(approvals)
        self.agents = agents if agents is not None else [{"id": 80, "name": "Bossman Harness", "model_id": 102}]
        self.posts = []
        self.i = 0

    def get(self, path, **kw):
        if path == "/api/agents":
            return self.agents
        if path == "/api/approvals":
            return self.approvals
        if path.startswith("/api/tasks/"):
            status = self.statuses[min(self.i, len(self.statuses) - 1)]
            self.i += 1
            return {"task": {"id": 5, "status": status}, "result": self.result if status == "completed" else None,
                    "error": None}
        raise AssertionError(path)

    def post(self, path, body=None, **kw):
        self.posts.append((path, body))
        if path == "/api/chat/threads":
            return {"id": "t-1"}
        if path.endswith("/send"):
            return {"task": {"id": 5, "status": "queued"}, "run_id": 9}
        raise AssertionError(path)


def _run(api, **kw):
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("on_event", lambda m: None)
    return run_goal(api, "сделай X", **kw)


def test_completed_with_passing_check_is_pass():
    api = FakeApi(["running", "completed"])
    rec = _run(api, verify=OK_CMD)
    assert rec.verdict == "PASS" and rec.verify["returncode"] == 0
    assert api.posts[1] == ("/api/chat/threads/t-1/send", {"text": "сделай X", "agent_id": 80})
    assert [s["status"] for s in rec.status_timeline] == ["running", "completed"]


def test_completed_without_check_is_unverified_not_pass():
    rec = _run(FakeApi(["completed"]))
    assert rec.verdict == "UNVERIFIED" and rec.verify is None


def test_agent_says_done_but_check_fails_is_fail():
    """Negative control: the agent's «готово» does not outrank the owner's check."""
    rec = _run(FakeApi(["completed"], result="всё готово, тесты зелёные"), verify=BAD_CMD)
    assert rec.verdict == "FAIL" and rec.verify["returncode"] == 1 and "2 failed" in rec.verify["output_tail"]


def test_failed_task_is_fail_and_check_is_not_run():
    rec = _run(FakeApi(["running", "failed"]), verify=OK_CMD)
    assert rec.verdict == "FAIL" and rec.verify["returncode"] is None and "не запускалась" in rec.verify["error"]


def test_stopped_task_is_stopped():
    assert _run(FakeApi(["stopped"])).verdict == "STOPPED"


def test_timeout_does_not_pretend_and_does_not_cancel():
    api = FakeApi(["running"])
    t = iter(range(0, 10_000, 100))
    rec = _run(api, timeout_s=250, clock=lambda: next(t))
    assert rec.verdict == "TIMEOUT"
    assert not [p for p in api.posts if "/stop" in p[0] or "/cancel" in p[0]]


def test_pending_approvals_are_reported_once_and_never_decided():
    api = FakeApi(["waiting_approval", "waiting_approval", "completed"],
                  approvals=[{"id": 31, "task_id": 5, "kind": "tool", "preview": "computer.act click OK"},
                             {"id": 32, "task_id": 6, "kind": "tool", "preview": "чужая задача"}])
    events = []
    rec = _run(api, on_event=events.append)
    assert [a["id"] for a in rec.approvals_seen] == [31]
    assert sum("одобрение #31" in e for e in events) == 1
    assert not [p for p in api.posts if "/approvals" in p[0]]


def test_missing_agent_is_not_run():
    rec = _run(FakeApi(["completed"], agents=[]))
    assert rec.verdict == "NOT_RUN" and "sync" in rec.reason


def test_verify_that_cannot_start_is_a_failed_check():
    v = run_verify("/nonexistent/binary-xyz --flag")
    assert v.returncode is None and "не запустилась" in v.error
    assert verdict_for("completed", v, timed_out=False)[0] == "FAIL"


def test_verify_timeout_is_a_failed_check():
    def runner(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)
    v = run_verify("pytest", runner=runner)
    assert v.returncode is None and "не уложилась" in v.error


# ---------------------------------------------------------------- end to end through the real app

class _ThreadApi:
    """Sync facade over the async test client: the driver runs in a worker thread, the app on the test loop."""

    def __init__(self, env, loop):
        self.env, self.loop = env, loop

    def _call(self, coro):
        r = asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=30)
        assert r.status_code < 400, r.text
        return r.json()

    def get(self, path, **kw):
        return self._call(self.env.client.get(path))

    def post(self, path, body=None, **kw):
        return self._call(self.env.client.post(path, json=body or {}))


async def _drive(env, adapter, **kw):
    assert (await _sync(env)).status_code == 200
    env.svc.registry.adapter_factory = lambda m, p: adapter
    env.svc.engine.poll_interval = 0.02
    worker = asyncio.create_task(env.svc.engine.worker_loop())
    watcher = asyncio.create_task(env.svc.engine.approval_watcher())
    try:
        api = _ThreadApi(env, asyncio.get_running_loop())
        return await asyncio.to_thread(run_goal, api, "проверь, что всё на месте", poll_s=0.05,
                                       on_event=lambda m: None, **kw)
    finally:
        worker.cancel()
        watcher.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)


async def test_e2e_harness_goal_through_the_real_chat_path_with_check(env):
    adapter = ToolAdapter([("text", "Готово: проверил, всё на месте.")])
    rec = await _drive(env, adapter, verify=OK_CMD, timeout_s=30)
    assert rec.agent["name"] == "Bossman Harness"
    assert rec.final_status == "completed", rec.as_dict()
    assert rec.verdict == "PASS" and "всё на месте" in rec.answer_excerpt
    assert adapter.calls >= 1 and adapter.seen_tools[0], "Harness got its tools"


async def test_e2e_desktop_action_waits_for_owner_and_driver_reports_timeout(env):
    adapter = ToolAdapter([("tool", "computer_act", {"action": "click", "target": "OK", "generation": 1}),
                           ("text", "готово")])
    rec = await _drive(env, adapter, verify=OK_CMD, timeout_s=3)
    assert rec.verdict == "TIMEOUT" and rec.final_status == "waiting_approval"
    assert rec.approvals_seen and "computer.act" in rec.approvals_seen[0]["preview"]
    assert rec.verify["returncode"] is None          # nothing to check: the task did not finish


@pytest.mark.parametrize("status,expected", [("completed", "UNVERIFIED"), ("blocked", "FAIL"),
                                             ("cancelled", "STOPPED")])
def test_verdict_table(status, expected):
    assert verdict_for(status, None, timed_out=False)[0] == expected
