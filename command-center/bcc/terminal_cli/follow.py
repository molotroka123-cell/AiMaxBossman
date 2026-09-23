"""Follow ONE backend task to a verdict: live events + periodic truth check.

The live stream (SSE, resumed by cursor) gives the moment-to-moment view; the
task's own status (GET /api/tasks/{id}) decides the outcome, so a lost event
can never turn into a wrong verdict. Approvals are never decided here unless
the owner answers an explicit prompt (interactive `ask` mode):

  wait  keep following while the owner decides elsewhere (web, Telegram,
        `bossman approve` in another window)
  fail  stop following with WAIT_APPROVAL; the task stays parked, nothing approved
  ask   show the approval and ask the owner in THIS terminal (y / n / details)
"""
from __future__ import annotations

import queue
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .api_client import BossmanError, Client, EventPump
from .records import (EXIT_INTERRUPTED, STATE_EXIT, TERMINAL_STATUSES, model_kind, normalize,
                      record, state_of)

Sink = Callable[[dict], None]


@dataclass
class FollowOptions:
    approval_mode: str = "wait"          # wait | fail | ask
    max_seconds: float | None = None
    on_timeout: str = "stop"             # stop | detach
    poll_interval: float = 2.0
    verbose: bool = False
    stop_confirm_seconds: float = 20.0


@dataclass
class FollowState:
    task_id: int
    run_id: int | None = None
    status: str = "queued"
    started: float = field(default_factory=time.monotonic)
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    cost_known: bool = False
    context_window: int | None = None
    model: str | None = None
    answer: str = ""
    pending_approvals: dict[int, dict] = field(default_factory=dict)
    decided: set[int] = field(default_factory=set)
    last_seq: int = 0
    stream_down: bool = False

    def absorb(self, rec: dict) -> None:
        t = rec.get("type")
        if isinstance(rec.get("seq"), int):
            self.last_seq = max(self.last_seq, rec["seq"])
        if rec.get("run_id") and t in ("task", "step", "usage"):
            self.run_id = rec["run_id"]
        if t == "task" and rec.get("status"):
            self.status = rec["status"]
        elif t == "step":
            self.status = "running"
            if rec.get("model"):
                self.model = rec["model"]
        elif t == "usage":
            if rec.get("reported"):
                self.tokens_in, self.tokens_out = rec.get("tokens_in"), rec.get("tokens_out")
            self.cost_known = bool(rec.get("cost_known"))
            self.cost_usd = rec.get("cost_usd") if self.cost_known else None
            self.context_window = rec.get("context_window") or self.context_window
            self.model = rec.get("model") or self.model
        elif t == "assistant_message":
            self.answer = rec.get("text") or self.answer
        elif t == "approval_required" and isinstance(rec.get("approval_id"), int):
            self.pending_approvals[rec["approval_id"]] = rec
        elif t == "approval_decided" and isinstance(rec.get("approval_id"), int):
            self.pending_approvals.pop(rec["approval_id"], None)
            self.decided.add(rec["approval_id"])


class Follower:
    def __init__(self, client: Client, task_id: int, *, sink: Sink, options: FollowOptions,
                 after: int = 0, decide: Callable[[dict], str] | None = None,
                 on_tick: Callable[[FollowState], None] | None = None):
        self.client = client
        self.state = FollowState(task_id=task_id, last_seq=after)
        self.sink = sink
        self.options = options
        self.after = after
        self.decide = decide
        self.on_tick = on_tick
        self.pump: EventPump | None = None
        self.stop_requested_at: float | None = None
        self._poll_failures = 0
        #: MOCK_MODEL/REAL_MODEL from the model's NAME (an alias may hide it)
        self.model_kind_hint: str | None = None

    # -- public -------------------------------------------------------------

    def run(self, before: Callable[[], dict | None] | None = None) -> dict:
        """Open the stream FIRST, then run `before` (e.g. start the draft), so
        not even a transient event of the new run is missed."""
        self.pump = EventPump(self.client, self.state.task_id, after=self.after).start()
        try:
            if before is not None:
                self.pump.opened.wait(5.0)
                admission = before()
                if isinstance(admission, dict) and admission.get("ok") is False:
                    self.state.status = "blocked"
                    rec = record("task", subtype="status", task_id=self.state.task_id,
                                 status="blocked", reason=admission.get("reason"))
                    self.sink(rec)
                    return self._result("BLOCKED", note=str(admission.get("reason") or "")[:500])
            return self._loop()
        finally:
            self.pump.close()

    def request_stop(self) -> dict | None:
        """Ask the backend to stop the task. The outcome is what the backend
        confirms later (task.stopped), not what we draw now."""
        self.stop_requested_at = time.monotonic()
        try:
            return self.client.post(f"/api/tasks/{self.state.task_id}/stop")
        except BossmanError as exc:
            self.sink(record("log", level="error", kind="client.stop_failed", message=exc.message))
            return None

    # -- loop -----------------------------------------------------------------

    def _loop(self) -> dict:
        opts = self.options
        last_poll = 0.0
        while True:
            now = time.monotonic()
            if (opts.max_seconds is not None and self.stop_requested_at is None
                    and now - self.state.started > opts.max_seconds):
                if opts.on_timeout == "stop":
                    self.request_stop()
                    self._await_stop()
                return self._result("TIMEOUT", note="max_seconds elapsed" +
                                    ("; stop requested" if opts.on_timeout == "stop" else "; detached"))
            if self.stop_requested_at is not None and now - self.stop_requested_at > opts.stop_confirm_seconds:
                return self._result(state_of(self.state.status), note="stop requested, not yet confirmed")
            msg = self._next(0.25)
            if msg is not None:
                verdict = self._handle(msg)
                if verdict is not None:
                    return verdict
            if self.on_tick is not None:
                self.on_tick(self.state)
            if time.monotonic() - last_poll >= opts.poll_interval:
                last_poll = time.monotonic()
                verdict = self._poll()
                if verdict is not None:
                    return verdict

    def _next(self, timeout: float) -> dict | None:
        assert self.pump is not None
        try:
            return self.pump.items.get(timeout=timeout)
        except queue.Empty:
            return None

    def _handle(self, msg: dict) -> dict | None:
        kind = msg.get("kind")
        if kind == "stream.keepalive":
            return None
        if kind in ("stream.disconnected", "stream.error"):
            self.state.stream_down = True
        for rec in normalize(msg, verbose=self.options.verbose):
            self.state.absorb(rec)
            self.sink(rec)
            if rec.get("type") == "approval_required":
                verdict = self._on_approval(rec)
                if verdict is not None:
                    return verdict
        if self.state.status in TERMINAL_STATUSES:
            return self._finish()
        return None

    def _on_approval(self, rec: dict) -> dict | None:
        mode = self.options.approval_mode
        if mode == "fail":
            return self._result("WAIT_APPROVAL", approval_id=rec.get("approval_id"),
                                note="approval required; nothing was approved")
        if mode == "ask" and self.decide is not None:
            answer = self.decide(rec)
            if answer in ("approve", "deny"):
                self._decide(rec["approval_id"], answer == "approve")
        return None

    def _decide(self, approval_id: int, approve: bool) -> None:
        try:
            row = self.client.post(f"/api/approvals/{approval_id}",
                                   {"approve": approve, "by": "owner:terminal"})
            status = (row or {}).get("status")
            if status not in ("approved", "rejected"):
                self.sink(record("log", level="warn", kind="client.approval",
                                 message=f"подтверждение #{approval_id}: уже решено ({status})"))
        except BossmanError as exc:
            self.sink(record("log", level="error", kind="client.approval", message=exc.message))

    def _poll(self) -> dict | None:
        try:
            data = self.client.get(f"/api/tasks/{self.state.task_id}")
            self._poll_failures = 0
        except BossmanError as exc:
            if exc.kind == "not_found":
                return self._result("FAIL", note="task not found", exit_code=9)
            self._poll_failures += 1
            if self._poll_failures >= 5 and self.state.stream_down:
                return self._result("DISCONNECTED", note=exc.message)
            return None
        task = data.get("task") or {}
        status = str(task.get("status") or self.state.status)
        runs = data.get("runs") or []
        if runs:
            self.state.run_id = runs[-1].get("id") or self.state.run_id
        if status != self.state.status:
            self.state.status = status
            rec = record("task", subtype="status", task_id=self.state.task_id,
                         run_id=self.state.run_id, status=status, source="poll")
            self.sink(rec)
        if status in TERMINAL_STATUSES:
            self._drain(0.5)
            return self._finish(data)
        if status == "waiting_approval" and self.options.approval_mode == "fail":
            pending = self._pending_approval_id()
            return self._result("WAIT_APPROVAL", approval_id=pending,
                                note="approval required; nothing was approved")
        if status == "paused" and self.options.approval_mode == "fail":
            return self._result("PARTIAL", note="task paused: waits for the owner")
        return None

    def _pending_approval_id(self) -> int | None:
        if self.state.pending_approvals:
            return max(self.state.pending_approvals)
        try:
            rows = self.client.get("/api/approvals", params={"status": "pending"}) or []
        except BossmanError:
            return None
        mine = [r for r in rows if r.get("task_id") == self.state.task_id]
        return mine[0]["id"] if mine else None

    def _drain(self, seconds: float) -> None:
        """Take the last events that are already on their way (tool results
        before task.completed), without waiting for more than `seconds`."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            msg = self._next(0.05)
            if msg is None:
                continue
            for rec in normalize(msg, verbose=self.options.verbose):
                self.state.absorb(rec)
                self.sink(rec)

    def _await_stop(self) -> None:
        deadline = time.monotonic() + self.options.stop_confirm_seconds
        while time.monotonic() < deadline:
            msg = self._next(0.25)
            if msg is not None:
                for rec in normalize(msg, verbose=self.options.verbose):
                    self.state.absorb(rec)
                    self.sink(rec)
            if self.state.status in ("stopped", "cancelled", "completed", "failed"):
                return
            try:
                status = ((self.client.get(f"/api/tasks/{self.state.task_id}") or {}).get("task") or {}).get("status")
            except BossmanError:
                continue
            if status in ("stopped", "cancelled", "completed", "failed"):
                self.state.status = status
                return

    def _finish(self, data: dict | None = None) -> dict:
        if data is None:
            try:
                data = self.client.get(f"/api/tasks/{self.state.task_id}")
            except BossmanError:
                data = {}
        task = (data or {}).get("task") or {}
        status = str(task.get("status") or self.state.status)
        self.state.status = status
        return self._result(state_of(status), data=data)

    def _result(self, task_state: str, *, data: dict | None = None, approval_id: int | None = None,
                note: str | None = None, exit_code: int | None = None) -> dict:
        data = data or {}
        runs = data.get("runs") or []
        last = runs[-1] if runs else {}
        model = last.get("model_alias") or self.state.model
        tokens_in = last.get("tokens_in") if last else self.state.tokens_in
        tokens_out = last.get("tokens_out") if last else self.state.tokens_out
        usage: dict[str, Any] = {}
        if self.state.tokens_in is not None or (tokens_in or tokens_out):
            usage = {"tokens_in": tokens_in, "tokens_out": tokens_out}
        usage["cost_usd"] = self.state.cost_usd if self.state.cost_known else None
        result_text = data.get("result")
        if result_text is None and self.state.answer and task_state == "PASS":
            result_text = self.state.answer
        from .console import sanitize
        return record(
            "result", ok=task_state not in ("DISCONNECTED",), task_state=task_state,
            status=self.state.status, task_id=self.state.task_id,
            run_id=last.get("id") or self.state.run_id,
            duration_ms=int((time.monotonic() - self.state.started) * 1000),
            result=sanitize(result_text) if result_text else None,
            error=sanitize(data.get("error")) if data.get("error") else None,
            usage=usage, model=model,
            model_kind=self.model_kind_hint or (model_kind(model) if model else None),
            approval_id=approval_id, note=note, cursor=self.state.last_seq,
            exit_code=exit_code if exit_code is not None else STATE_EXIT.get(task_state, 1))


def interrupted_result(state: FollowState, *, stop_confirmed: bool) -> dict:
    return record("result", ok=True, task_state="STOPPED" if stop_confirmed else "INTERRUPTED",
                  status=state.status, task_id=state.task_id, run_id=state.run_id,
                  duration_ms=int((time.monotonic() - state.started) * 1000),
                  note="Ctrl+C: stop requested" + ("; confirmed by the backend" if stop_confirmed
                                                    else "; not confirmed yet"),
                  cursor=state.last_seq, exit_code=EXIT_INTERRUPTED)
