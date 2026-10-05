"""Jeff 1.5 long tasks: state record, restart resume, no repeated external action.

Fakes only. A crash is simulated with a BaseException the store does not catch, so the
persisted ``STARTED`` marker is exactly what a killed process leaves behind.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from bcc.pit import learning_counters as lc
from bcc.pit import tasks
from bcc.pit.tasks import ActionNotPerformed, TaskError, TaskStore

OWNER = "a" * 64


class Crash(BaseException):
    """Process killed mid-action (not an Exception, so nothing handles it)."""


class FakeTelegram:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, key, params):
        self.sent.append((key, params["text"]))
        return "msg-1"

    def already_sent(self, key, params):
        return any(k == key for k, _ in self.sent)


def store(tmp_path, sha="build1"):
    return TaskStore(tmp_path, build_sha=sha)


def steps():
    return [{"id": "prep", "title": "prepare"},
            {"id": "send", "title": "tell", "kind": "telegram_send", "params": {"text": "готово"}},
            {"id": "wrap", "title": "wrap up"}]


def run(coro):
    return asyncio.run(coro)


def test_happy_path_persists_every_field(tmp_path):
    s, tg = store(tmp_path), FakeTelegram()
    task = s.create(OWNER, "ship report", steps(), constraints=["no spend"], approval_refs=["ap-1"])
    assert task["state"] == "PLANNED" and task["build_sha"] == "build1"
    done = run(s.run(OWNER, task["id"], {"telegram_send": tg.send}))
    assert done["state"] == "DONE" and done["done"] == ["prep", "send", "wrap"]
    assert len(tg.sent) == 1
    saved = s.get(OWNER, task["id"])
    assert saved["constraints"] == ["no spend"] and saved["approval_refs"] == ["ap-1"]
    assert saved["actions"] and all(a["status"] == "COMPLETED" for a in saved["actions"].values())


def test_rerun_of_finished_task_never_repeats_the_send(tmp_path):
    s, tg = store(tmp_path), FakeTelegram()
    task = s.create(OWNER, "g", steps())
    run(s.run(OWNER, task["id"], {"telegram_send": tg.send}))
    run(store(tmp_path).run(OWNER, task["id"], {"telegram_send": tg.send}))
    assert len(tg.sent) == 1


def crash_after_send(tg):
    def send(key, params):
        tg.send(key, params)              # the message really went out...
        raise Crash()                      # ...then the process died before recording it
    return send


def test_crash_after_send_verifier_true_completes_without_second_send(tmp_path):
    tg = FakeTelegram()
    s = store(tmp_path)
    task = s.create(OWNER, "g", steps())
    with pytest.raises(Crash):
        run(s.run(OWNER, task["id"], {"telegram_send": crash_after_send(tg)}))
    assert s.get(OWNER, task["id"])["state"] == "RUNNING"
    restarted = store(tmp_path, "build2")
    assert [t["id"] for t in restarted.resumable(OWNER)] == [task["id"]]
    final = run(restarted.run(OWNER, task["id"], {"telegram_send": tg.send},
                              verifiers={"telegram_send": tg.already_sent}))
    assert final["state"] == "DONE" and len(tg.sent) == 1
    assert final["resumed_on_builds"] == ["build2"]


def test_crash_before_send_verifier_false_retries_exactly_once(tmp_path):
    tg = FakeTelegram()
    s = store(tmp_path)
    task = s.create(OWNER, "g", steps())

    def die_first(key, params):
        raise Crash()

    with pytest.raises(Crash):
        run(s.run(OWNER, task["id"], {"telegram_send": die_first}))
    final = run(store(tmp_path).run(OWNER, task["id"], {"telegram_send": tg.send},
                                    verifiers={"telegram_send": tg.already_sent}))
    assert final["state"] == "DONE" and len(tg.sent) == 1


def test_no_verifier_means_unknown_outcome_and_no_resend(tmp_path):
    tg = FakeTelegram()
    s = store(tmp_path)
    task = s.create(OWNER, "g", steps())
    with pytest.raises(Crash):
        run(s.run(OWNER, task["id"], {"telegram_send": crash_after_send(tg)}))
    restarted = store(tmp_path)
    parked = run(restarted.run(OWNER, task["id"], {"telegram_send": tg.send}))
    assert parked["state"] == "UNKNOWN_OUTCOME" and len(tg.sent) == 1
    # asking again does not resend either
    assert run(restarted.run(OWNER, task["id"], {"telegram_send": tg.send}))["state"] == "UNKNOWN_OUTCOME"
    assert len(tg.sent) == 1
    assert [t["state"] for t in restarted.resumable(OWNER)] == ["UNKNOWN_OUTCOME"]


def test_unknown_outcome_needs_explicit_confirmation(tmp_path):
    tg = FakeTelegram()
    s = store(tmp_path)
    task = s.create(OWNER, "g", steps())
    with pytest.raises(Crash):
        run(s.run(OWNER, task["id"], {"telegram_send": crash_after_send(tg)}))
    parked = run(store(tmp_path).run(OWNER, task["id"], {"telegram_send": tg.send}))
    key = next(iter(parked["actions"]))
    restarted = store(tmp_path)
    with pytest.raises(TaskError):
        restarted.confirm_unknown(OWNER, task["id"], key, happened=True, actor="jeff")
    restarted.confirm_unknown(OWNER, task["id"], key, happened=True, actor="participant")
    final = run(restarted.run(OWNER, task["id"], {"telegram_send": tg.send}))
    assert final["state"] == "DONE" and len(tg.sent) == 1
    events = (tmp_path / "tasks" / OWNER / "events.jsonl").read_text(encoding="utf-8")
    assert "confirm_unknown" in events and "готово" not in events   # audit never stores params


def test_unknown_confirmed_not_happened_then_retries_once(tmp_path):
    tg = FakeTelegram()
    s = store(tmp_path)
    task = s.create(OWNER, "g", steps())

    def timeout(key, params):
        raise TimeoutError("network")     # may or may not have been delivered

    parked = run(s.run(OWNER, task["id"], {"telegram_send": timeout}))
    assert parked["state"] == "UNKNOWN_OUTCOME"
    key = next(iter(parked["actions"]))
    s.confirm_unknown(OWNER, task["id"], key, happened=False, actor="owner")
    final = run(s.run(OWNER, task["id"], {"telegram_send": tg.send}))
    assert final["state"] == "DONE" and len(tg.sent) == 1


def test_definite_failure_retries_then_fails_after_limit(tmp_path):
    s = store(tmp_path)
    task = s.create(OWNER, "g", steps())
    calls = []

    def refuse(key, params):
        calls.append(key)
        raise ActionNotPerformed()

    final = run(s.run(OWNER, task["id"], {"telegram_send": refuse}))
    assert final["state"] == "FAILED" and len(calls) == tasks.MAX_ATTEMPTS
    assert len(set(calls)) == 1            # same idempotency key on every attempt


def test_waiting_input_and_approval_survive_restart(tmp_path):
    tg = FakeTelegram()
    s = store(tmp_path)
    task = s.create(OWNER, "g", [
        {"id": "ask", "needs_input": True},
        {"id": "pay", "kind": "telegram_send", "params": {"text": "x"}, "needs_approval": True}])
    assert run(s.run(OWNER, task["id"], {"telegram_send": tg.send}))["state"] == "WAITING_INPUT"
    s2 = store(tmp_path)
    assert run(s2.run(OWNER, task["id"], {"telegram_send": tg.send}))["state"] == "WAITING_INPUT"
    with pytest.raises(TaskError):
        s2.provide_input(OWNER, task["id"], "other", "v")
    s2.provide_input(OWNER, task["id"], "ask", "42")
    assert run(s2.run(OWNER, task["id"], {"telegram_send": tg.send}))["state"] == "WAITING_APPROVAL"
    assert tg.sent == []                   # nothing external before approval
    s3 = store(tmp_path)
    with pytest.raises(TaskError):
        s3.approve(OWNER, task["id"], "pay", "  ")
    s3.approve(OWNER, task["id"], "pay", "owner-yes-17")
    final = run(s3.run(OWNER, task["id"], {"telegram_send": tg.send}))
    assert final["state"] == "DONE" and final["approval_refs"] == ["pay:owner-yes-17"]
    assert final["inputs"] == {"ask": "42"} and len(tg.sent) == 1


def test_resume_continues_from_last_completed_step(tmp_path):
    s, seen = store(tmp_path), []
    task = s.create(OWNER, "g", [
        {"id": "a", "kind": "k", "params": {"n": 1}}, {"id": "b", "kind": "k", "params": {"n": 2}}])

    def flaky(key, params):
        seen.append(params["n"])
        if params["n"] == 2 and seen.count(2) == 1:
            raise Crash()
        return "ok"

    with pytest.raises(Crash):
        run(s.run(OWNER, task["id"], {"k": flaky}))
    saved = s.get(OWNER, task["id"])
    assert saved["done"] == ["a"]
    final = run(store(tmp_path).run(OWNER, task["id"], {"k": flaky},
                                    verifiers={"k": lambda key, params: False}))
    assert final["state"] == "DONE" and seen == [1, 2, 2]     # step a not repeated


def test_illegal_transition_and_bad_inputs_are_rejected(tmp_path):
    s = store(tmp_path)
    with pytest.raises(TaskError):
        s.create(OWNER, "", steps())
    with pytest.raises(TaskError):
        s.create("not-a-key", "g", steps())
    with pytest.raises(TaskError):
        s.create(OWNER, "g", [{"id": "a", "kind": "k", "params": {"token": "sk-" + "a" * 30}}])
    task = s.create(OWNER, "g", steps())
    with pytest.raises(TaskError):
        s._move(task, "DONE")
    assert set(tasks.STATES) == {"PLANNED", "RUNNING", "WAITING_INPUT", "WAITING_APPROVAL", "DONE",
                                 "FAILED", "UNKNOWN_OUTCOME"}


def test_tasks_are_isolated_per_owner(tmp_path):
    s = store(tmp_path)
    task = s.create(OWNER, "g", steps())
    other = "b" * 64
    with pytest.raises(TaskError):
        s.get(other, task["id"])
    assert s.list(other) == []
    assert s.summary(OWNER)["PLANNED"] == 1 and s.summary(other)["PLANNED"] == 0


def test_task_files_survive_in_plain_json(tmp_path):
    s = store(tmp_path)
    task = s.create(OWNER, "g", steps())
    data = json.loads((tmp_path / "tasks" / OWNER / f"{task['id']}.json").read_text(encoding="utf-8"))
    assert data["schema"] == "jeff.task/1" and data["state"] == "PLANNED"


# -- before/after counters -----------------------------------------------------------------------
def test_counters_count_real_outcomes_per_build(tmp_path):
    for sha, ok_n, fail_n in (("old", 3, 3), ("new", 5, 1)):
        s = TaskStore(tmp_path, build_sha=sha)
        for _ in range(ok_n):
            t = s.create(OWNER, "g", [{"id": "a"}])
            run(s.run(OWNER, t["id"], {}))
        for _ in range(fail_n):
            t = s.create(OWNER, "g", [{"id": "a", "kind": "missing"}])
            run(s.run(OWNER, t["id"], {}))
    data = lc.load(tmp_path)["builds"]
    assert data["old"]["task"]["ok"] == 3 and data["new"]["task"]["fail"] == 1
    result = lc.compare(tmp_path, "old", "new", "task")
    assert result["status"] == "measured" and result["success_rate_delta"] == 0.3333


def test_compare_refuses_to_measure_without_enough_samples(tmp_path):
    lc.record(tmp_path, "old", "task", ok=True)
    lc.record(tmp_path, "new", "task", ok=True)
    assert lc.compare(tmp_path, "old", "new", "task")["status"] == "insufficient"
    assert lc.compare(tmp_path, "old", "nope", "task")["status"] == "insufficient"
    for _ in range(lc.MIN_SAMPLES):
        lc.record(tmp_path, "old", "task", ok=False, latency_ms=10)
        lc.record(tmp_path, "new", "task", ok=True, latency_ms=5, intervention=True, cost_usd=0.01)
    result = lc.compare(tmp_path, "old", "new", "task")
    assert result["status"] == "measured" and result["success_rate_delta"] > 0
    assert result["after"]["avg_latency_ms"] == 5 and result["after"]["cost_usd"] == 0.05
