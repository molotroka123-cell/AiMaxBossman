"""Harness run driver: one goal through the REAL Bossman chat path, then an independent check, then a receipt.

    goal ─► POST /api/chat/threads + /send (agent «Bossman Harness» or any agent) ─► engine run
         ─► poll /api/tasks/{id}; every pending approval of this task is printed (the owner decides in the pult —
            this driver never approves anything)
         ─► final state + answer
         ─► independent verification: the owner's own command (`--verify "pytest -q tests/x.py"`), run here,
            outside the agent; its exit code decides, not the agent's words
         ─► receipt JSON

Verdicts (honest-verdict rule: «done» is not «verified»):
  PASS            task completed AND the verify command exited 0
  FAIL            task failed/blocked, or the verify command exited non-zero
  UNVERIFIED      task completed, no verify command given — the agent's claim was not checked
  STOPPED         the owner (or STOP) stopped/cancelled the task
  TIMEOUT         the task did not finish in time (it is NOT cancelled by this driver; it keeps running)
  NOT_RUN         the run never started (agent missing, API refused)
"""
from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

FINAL = ("completed", "failed", "stopped", "cancelled", "blocked")
VERIFY_TAIL = 4000


class Api(Protocol):
    def get(self, path: str, **kw: Any) -> Any: ...
    def post(self, path: str, body: Any = None, **kw: Any) -> Any: ...


@dataclass
class VerifyResult:
    command: list[str]
    returncode: int | None
    output_tail: str = ""
    error: str = ""
    seconds: float = 0.0


@dataclass
class Receipt:
    goal: str
    verdict: str = "NOT_RUN"
    reason: str = ""
    agent: dict = field(default_factory=dict)
    thread_id: str = ""
    task_id: int | None = None
    run_id: int | None = None
    status_timeline: list[dict] = field(default_factory=list)
    approvals_seen: list[dict] = field(default_factory=list)
    final_status: str = ""
    answer_excerpt: str = ""
    answer_sha256: str = ""
    error: str = ""
    verify: dict | None = None
    started_at: float = 0.0
    finished_at: float = 0.0
    backend: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def run_verify(command: str, *, cwd: str | None = None, timeout: float = 1800.0,
               runner: Callable[..., Any] = subprocess.run, clock: Callable[[], float] = time.monotonic) -> VerifyResult:
    """The owner's check, shell-free (shlex split). A command that cannot start is a failed check, never a pass."""
    argv = shlex.split(command)
    if not argv:
        return VerifyResult(command=[], returncode=None, error="пустая команда проверки")
    t0 = clock()
    try:
        cp = runner(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return VerifyResult(command=argv, returncode=None, error=f"проверка не уложилась в {timeout:.0f} с",
                            seconds=clock() - t0)
    except OSError as exc:
        return VerifyResult(command=argv, returncode=None, error=f"проверка не запустилась: {exc}",
                            seconds=clock() - t0)
    out = (cp.stdout or "") + (("\n" + cp.stderr) if cp.stderr else "")
    return VerifyResult(command=argv, returncode=cp.returncode, output_tail=out[-VERIFY_TAIL:], seconds=clock() - t0)


def resolve_agent(api: Api, *, name: str | None, agent_id: int | None) -> dict | None:
    agents = api.get("/api/agents") or []
    if isinstance(agents, dict):
        agents = agents.get("items") or []
    for a in agents:
        if agent_id is not None and a.get("id") == agent_id:
            return a
        if agent_id is None and name and a.get("name") == name:
            return a
    return None


def verdict_for(final_status: str, verify: VerifyResult | None, *, timed_out: bool) -> tuple[str, str]:
    if timed_out:
        return "TIMEOUT", "задача не завершилась за отведённое время (продолжает работать, драйвер её не отменял)"
    if final_status in ("stopped", "cancelled"):
        return "STOPPED", f"задача остановлена ({final_status})"
    if final_status != "completed":
        return "FAIL", f"задача завершилась статусом {final_status or 'неизвестно'}"
    if verify is None:
        return "UNVERIFIED", "агент завершил задачу, независимой проверки не было (нет --verify)"
    if verify.returncode == 0:
        return "PASS", "задача завершена и независимая проверка прошла (код 0)"
    why = verify.error or f"код {verify.returncode}"
    return "FAIL", f"задача завершена, но независимая проверка не прошла ({why})"


def run_goal(api: Api, goal: str, *, agent_name: str | None = "Bossman Harness", agent_id: int | None = None,
             verify: str | None = None, verify_cwd: str | None = None, timeout_s: float = 1800.0,
             poll_s: float = 2.0, on_event: Callable[[str], None] = print,
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
             wall: Callable[[], float] = time.time, verify_runner: Callable[..., Any] = subprocess.run,
             backend: dict | None = None) -> Receipt:
    rec = Receipt(goal=goal, started_at=wall(), backend=dict(backend or {}))
    agent = resolve_agent(api, name=agent_name, agent_id=agent_id)
    if agent is None:
        rec.reason = (f"агент {agent_id if agent_id is not None else agent_name!r} не найден — "
                      "сначала `harness_agent.py sync` (создаёт Bossman Harness на локальной модели)")
        rec.finished_at = wall()
        return rec
    rec.agent = {"id": agent.get("id"), "name": agent.get("name"), "model_id": agent.get("model_id")}
    thread = api.post("/api/chat/threads", {"title": ("Harness: " + goal)[:80]})
    rec.thread_id = str((thread or {}).get("id") or "")
    if not rec.thread_id:
        rec.reason = "чат-тред не создан"
        rec.finished_at = wall()
        return rec
    sent = api.post(f"/api/chat/threads/{rec.thread_id}/send", {"text": goal, "agent_id": agent.get("id")})
    rec.task_id = int(((sent or {}).get("task") or {}).get("id") or 0) or None
    rec.run_id = (sent or {}).get("run_id")
    if rec.task_id is None:
        rec.reason = "задача не создана"
        rec.finished_at = wall()
        return rec
    on_event(f"задача #{rec.task_id} запущена агентом «{rec.agent['name']}»")
    deadline = clock() + timeout_s
    last_status, seen_approvals, timed_out, view = "", set(), False, {}
    while True:
        view = api.get(f"/api/tasks/{rec.task_id}") or {}
        status = str((view.get("task") or {}).get("status") or "")
        if status != last_status:
            rec.status_timeline.append({"status": status, "at": round(wall(), 3)})
            on_event(f"статус: {status}")
            last_status = status
        if status == "waiting_approval":
            for a in api.get("/api/approvals") or []:
                if a.get("task_id") == rec.task_id and a.get("id") not in seen_approvals:
                    seen_approvals.add(a.get("id"))
                    rec.approvals_seen.append({"id": a.get("id"), "kind": a.get("kind"),
                                               "preview": str(a.get("preview") or "")[:300]})
                    on_event(f"ждёт вашего решения в пульте: одобрение #{a.get('id')} — "
                             f"{str(a.get('preview') or '')[:160]}")
        if status in FINAL:
            break
        if clock() >= deadline:
            timed_out = True
            break
        sleep(poll_s)
    rec.final_status = last_status
    answer = str(view.get("result") or view.get("failure_summary") or "")
    rec.answer_excerpt = answer[:2000]
    rec.answer_sha256 = hashlib.sha256(answer.encode("utf-8")).hexdigest() if answer else ""
    rec.error = str(view.get("error") or "")[:1000]
    vr = None
    if verify and rec.final_status == "completed" and not timed_out:
        on_event(f"независимая проверка: {verify}")
        vr = run_verify(verify, cwd=verify_cwd, runner=verify_runner)
        rec.verify = {"command": vr.command, "returncode": vr.returncode, "error": vr.error,
                      "output_tail": vr.output_tail, "seconds": round(vr.seconds, 2)}
    elif verify:
        rec.verify = {"command": shlex.split(verify), "returncode": None,
                      "error": "не запускалась: задача не завершилась успешно", "output_tail": "", "seconds": 0}
    rec.verdict, rec.reason = verdict_for(rec.final_status, vr, timed_out=timed_out)
    rec.finished_at = wall()
    return rec


def write_receipt(rec: Receipt, path: str) -> None:
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = ["FINAL", "Receipt", "VerifyResult", "resolve_agent", "run_goal", "run_verify", "verdict_for",
           "write_receipt"]
