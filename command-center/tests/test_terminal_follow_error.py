"""A failed task's reason reaches the terminal's result record (RC19 audit P2-8).

The owner saw the terminal status of a failed task but not WHY («тишина»).
The normal path already carries it (GET /api/tasks/{id} -> `error`). These
pin the gaps: the closing GET fails after the task.failed event, and the poll
sees «failed» before the stream does (the view shows each status once, so a
reason-less first record hid the event's error).
"""
from __future__ import annotations

import queue
from types import SimpleNamespace

from bcc.terminal_cli.api_client import BossmanError
from bcc.terminal_cli.follow import Follower, FollowOptions

REASON = "провайдер отклонил запрос (400): context too long"


class _Client:
    def __init__(self, task: dict | None = None, fail_get: bool = False):
        self.task = task
        self.fail_get = fail_get

    def get(self, path, **_kw):
        if self.fail_get:
            raise BossmanError("связь с Bossman потеряна (ReadError)", kind="disconnected")
        return self.task


def _follower(client) -> tuple[Follower, list[dict]]:
    sink: list[dict] = []
    f = Follower(client, 5, sink=sink.append, options=FollowOptions(approval_mode="fail"))
    f.pump = SimpleNamespace(items=queue.Queue())
    return f, sink


def test_the_failed_events_reason_survives_a_failed_closing_get():
    f, _ = _follower(_Client(fail_get=True))
    res = f._handle({"kind": "task.failed", "task_id": 5, "run_id": 9, "error": REASON, "seq": 3})
    assert res is not None and res["task_state"] == "FAIL"
    assert res["error"] == REASON


def test_a_poll_that_sees_failed_first_carries_the_reason():
    data = {"task": {"id": 5, "status": "failed"}, "runs": [{"id": 9, "status": "failed", "error": REASON}],
            "result": None, "error": REASON}
    f, sink = _follower(_Client(task=data))
    res = f._poll()
    status = [r for r in sink if r.get("type") == "task" and r.get("source") == "poll"]
    assert status and status[0]["status"] == "failed" and status[0]["error"] == REASON
    assert res["error"] == REASON


def test_a_passed_task_never_borrows_an_old_error():
    f, _ = _follower(_Client(task={"task": {"id": 5, "status": "completed"}, "runs": [], "result": "ok"}))
    f.state.error = "старая ошибка прежнего запуска"
    res = f._finish()
    assert res["task_state"] == "PASS" and "error" not in res
