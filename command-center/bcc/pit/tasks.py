"""Long tasks that survive a restart (Jeff 1.5). Not a second engine.

A task is one small state record in ``pit-v1.7/tasks/<owner>/<task_id>.json`` written with
the vault's atomic helper (the same persistence the passport uses). ``TaskStore.run`` walks the
steps; the store itself never talks to Telegram or any service: callers pass executors.

States: PLANNED, RUNNING, WAITING_INPUT, WAITING_APPROVAL, DONE, FAILED, UNKNOWN_OUTCOME.

External-action safety (the point of this module):

- every external action has an idempotency key = hash(task, step, kind, params);
- ``STARTED`` is persisted (fsync) BEFORE the executor runs, ``COMPLETED`` right after;
- after a restart an action found ``STARTED`` is never blindly repeated: the caller's verifier
  is asked whether it already happened (True -> completed, False -> safe to retry, None or no
  verifier -> UNKNOWN_OUTCOME);
- an executor that raises anything except ``ActionNotPerformed`` is treated as unknown
  (a timeout after a send may still have delivered);
- UNKNOWN_OUTCOME leaves only ``confirm_unknown`` (explicit, audited) as the way forward.

Task files hold owner-scoped goals and step params; audit rows never store params.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from . import learning_counters
from .secret_filter import redact_secrets
from .vault import _append_jsonl, _atomic_json

SCHEMA = "jeff.task/1"
STATES = ("PLANNED", "RUNNING", "WAITING_INPUT", "WAITING_APPROVAL", "DONE", "FAILED",
          "UNKNOWN_OUTCOME")
TERMINAL = frozenset({"DONE", "FAILED"})
_ALLOWED = {
    "PLANNED": {"RUNNING", "FAILED"},
    "RUNNING": {"WAITING_INPUT", "WAITING_APPROVAL", "DONE", "FAILED", "UNKNOWN_OUTCOME", "RUNNING"},
    "WAITING_INPUT": {"RUNNING", "FAILED"},
    "WAITING_APPROVAL": {"RUNNING", "FAILED"},
    "UNKNOWN_OUTCOME": {"RUNNING", "FAILED"},
    "DONE": set(),
    "FAILED": set(),
}
MAX_ATTEMPTS = 3
_OWNER = re.compile(r"^(owner|[0-9a-f]{64})$")
_ID = re.compile(r"^[0-9a-f]{16}$")
EVENTS_FILE = "events.jsonl"


class ActionNotPerformed(Exception):
    """The executor guarantees the external action did NOT happen (safe to retry)."""


class TaskError(ValueError):
    pass


Executor = Callable[[str, dict[str, Any]], Any]              # (idempotency_key, params) -> result_ref
Verifier = Callable[[str, dict[str, Any]], Any]              # (idempotency_key, params) -> True|False|None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def idempotency_key(task_id: str, step_id: str, kind: str, params: dict[str, Any]) -> str:
    blob = json.dumps([task_id, step_id, kind, params], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


async def _call(fn: Callable[..., Any], *args: Any) -> Any:
    result = fn(*args)
    if inspect.isawaitable(result):
        result = await result
    return result


class TaskStore:
    def __init__(self, home: Path, *, build_sha: str = "unknown"):
        self.home = Path(home)
        self.root = self.home / "tasks"
        self.build_sha = str(build_sha)[:40]
        self._running: set[str] = set()

    # -- persistence ---------------------------------------------------------------------
    def _dir(self, owner: str) -> Path:
        if not _OWNER.fullmatch(str(owner)):
            raise TaskError("invalid task owner")
        return self.root / owner

    def _path(self, owner: str, task_id: str) -> Path:
        if not _ID.fullmatch(str(task_id)):
            raise TaskError("invalid task id")
        return self._dir(owner) / f"{task_id}.json"

    def _save(self, task: dict[str, Any]) -> None:
        task["updated_at"] = _now()
        _atomic_json(self._path(task["owner"], task["id"]), task)

    def _event(self, task: dict[str, Any], action: str, **detail: Any) -> None:
        row = {"at": _now(), "task": task["id"], "action": action, "state": task["state"]}
        row.update({k: v for k, v in detail.items() if isinstance(v, (str, int, bool))})
        try:
            _append_jsonl(self._dir(task["owner"]) / EVENTS_FILE, row)
        except OSError:
            pass

    def get(self, owner: str, task_id: str) -> dict[str, Any]:
        try:
            data = json.loads(self._path(owner, task_id).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TaskError("task not found or unreadable") from exc
        if data.get("owner") != owner:
            raise TaskError("task belongs to another owner")
        return data

    def list(self, owner: str) -> list[dict[str, Any]]:
        out = []
        folder = self._dir(owner)
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out

    # -- lifecycle -------------------------------------------------------------------------
    def create(self, owner: str, goal: str, steps: list[dict[str, Any]], *,
               constraints: list[str] | None = None,
               approval_refs: list[str] | None = None) -> dict[str, Any]:
        if not str(goal).strip() or not steps:
            raise TaskError("a task needs a goal and at least one step")
        stamp = f"{owner}|{goal}|{time.time_ns()}"
        task_id = hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:16]
        clean_steps, seen = [], set()
        for index, raw in enumerate(steps, 1):
            step_id = str(raw.get("id") or f"s{index}")
            if step_id in seen:
                raise TaskError("duplicate step id")
            seen.add(step_id)
            params = dict(raw.get("params") or {})
            if redact_secrets(json.dumps(params, ensure_ascii=False))[1]:
                raise TaskError("step params must not contain secrets")
            clean_steps.append({"id": step_id, "title": str(raw.get("title", ""))[:200],
                                "kind": str(raw["kind"]) if raw.get("kind") else None,
                                "params": params, "needs_input": bool(raw.get("needs_input")),
                                "needs_approval": bool(raw.get("needs_approval")),
                                "status": "PENDING"})
        task = {"schema": SCHEMA, "id": task_id, "owner": owner,
                "goal": str(goal).strip()[:500], "state": "PLANNED", "steps": clean_steps,
                "done": [], "awaited_input": None, "inputs": {},
                "constraints": [str(c)[:200] for c in (constraints or [])][:20],
                "build_sha": self.build_sha, "approval_refs": list(approval_refs or [])[:20],
                "actions": {}, "resumed_on_builds": [], "created_at": _now(), "error": ""}
        self._save(task)
        self._event(task, "create")
        return task

    def _move(self, task: dict[str, Any], state: str, **detail: Any) -> None:
        if state not in _ALLOWED.get(task["state"], set()):
            raise TaskError(f"illegal transition {task['state']} -> {state}")
        task["state"] = state
        self._save(task)
        self._event(task, "state", **detail)
        if state in TERMINAL:
            learning_counters.record(self.home, task["build_sha"], "task", ok=state == "DONE")

    def provide_input(self, owner: str, task_id: str, step_id: str, value: Any) -> dict[str, Any]:
        task = self.get(owner, task_id)
        if task["state"] != "WAITING_INPUT" or (task["awaited_input"] or {}).get("step") != step_id:
            raise TaskError("task is not waiting for this input")
        if redact_secrets(json.dumps(value, ensure_ascii=False))[1]:
            raise TaskError("input must not contain secrets")
        task["inputs"][step_id] = value
        task["awaited_input"] = None
        self._move(task, "RUNNING", why="input")
        return task

    def approve(self, owner: str, task_id: str, step_id: str, approval_ref: str) -> dict[str, Any]:
        task = self.get(owner, task_id)
        if task["state"] != "WAITING_APPROVAL" or task.get("awaited_approval") != step_id:
            raise TaskError("task is not waiting for this approval")
        if not str(approval_ref).strip():
            raise TaskError("approval needs a reference")
        task["approval_refs"].append(f"{step_id}:{str(approval_ref)[:120]}")
        task["awaited_approval"] = None
        self._move(task, "RUNNING", why="approved")
        return task

    def confirm_unknown(self, owner: str, task_id: str, key: str, *, happened: bool,
                        actor: str) -> dict[str, Any]:
        """Explicit human decision for an action whose outcome could not be established."""
        task = self.get(owner, task_id)
        if task["state"] != "UNKNOWN_OUTCOME":
            raise TaskError("task has no unknown outcome")
        action = task["actions"].get(key)
        if not action or action["status"] != "UNKNOWN":
            raise TaskError("no such unknown action")
        if actor not in {"participant", "owner"}:
            raise TaskError("only the participant or the owner may confirm")
        if happened:
            action.update(status="COMPLETED", result_ref="confirmed_by_" + actor,
                          finished_at=_now())
        else:
            action.update(status="NOT_PERFORMED", finished_at=_now())
        action["confirmed_by"] = actor
        if not any(a["status"] == "UNKNOWN" for a in task["actions"].values()):
            self._move(task, "RUNNING", why="unknown_confirmed", happened=happened)
        else:
            self._save(task)
        self._event(task, "confirm_unknown", happened=happened, actor=actor)
        return task

    def resumable(self, owner: str | None = None) -> list[dict[str, Any]]:
        """Tasks to look at after a restart: RUNNING/PLANNED ones (need ``run``) and UNKNOWN ones."""
        owners = [owner] if owner else [p.name for p in sorted(self.root.iterdir())
                                        if p.is_dir()] if self.root.is_dir() else []
        rows = []
        for who in owners:
            rows += [t for t in self.list(who) if t["state"] in {"PLANNED", "RUNNING", "UNKNOWN_OUTCOME"}]
        return rows

    def summary(self, owner: str | None = None) -> dict[str, int]:
        """Counts per state for status pages (no goals, no params)."""
        counts = {state: 0 for state in STATES}
        owners = [owner] if owner else [p.name for p in sorted(self.root.iterdir())
                                        if p.is_dir()] if self.root.is_dir() else []
        for who in owners:
            for task in self.list(who):
                counts[task["state"]] = counts.get(task["state"], 0) + 1
        return counts

    # -- execution ---------------------------------------------------------------------------
    async def run(self, owner: str, task_id: str, executors: dict[str, Executor], *,
                  verifiers: dict[str, Verifier] | None = None) -> dict[str, Any]:
        """Walk the steps from the last safe point. Safe to call again after any crash."""
        run_id = f"{owner}/{task_id}"
        if run_id in self._running:
            raise TaskError("task is already running in this process")
        self._running.add(run_id)
        try:
            return await self._run(owner, task_id, executors, verifiers or {})
        finally:
            self._running.discard(run_id)

    async def _run(self, owner: str, task_id: str, executors: dict[str, Executor],
                   verifiers: dict[str, Verifier]) -> dict[str, Any]:
        task = self.get(owner, task_id)
        if task["state"] in TERMINAL:
            return task
        if task["state"] in {"WAITING_INPUT", "WAITING_APPROVAL", "UNKNOWN_OUTCOME"}:
            return task                                    # only explicit input/approval/confirm moves it
        if task["build_sha"] != self.build_sha and self.build_sha not in task["resumed_on_builds"]:
            task["resumed_on_builds"].append(self.build_sha)
        if task["state"] != "RUNNING":
            self._move(task, "RUNNING", why="start")
        started = time.monotonic()
        for step in task["steps"]:
            if step["status"] == "DONE":
                continue
            if step["needs_input"] and step["id"] not in task["inputs"]:
                task["awaited_input"] = {"step": step["id"], "since": _now()}
                self._move(task, "WAITING_INPUT", step=step["id"])
                return task
            if step["needs_approval"] and not any(
                    ref.startswith(step["id"] + ":") for ref in task["approval_refs"]):
                task["awaited_approval"] = step["id"]
                self._move(task, "WAITING_APPROVAL", step=step["id"])
                return task
            if step["kind"]:
                outcome = await self._do_action(task, step, executors, verifiers)
                if outcome == "UNKNOWN":
                    self._move(task, "UNKNOWN_OUTCOME", step=step["id"])
                    return task
                if outcome == "FAILED":
                    task["error"] = f"step {step['id']} failed"
                    self._move(task, "FAILED", step=step["id"])
                    return task
            step["status"] = "DONE"
            task["done"].append(step["id"])
            self._save(task)                                # safe point: step done, persisted
        self._move(task, "DONE")
        learning_counters.record(self.home, task["build_sha"], "task_latency", ok=True,
                                 latency_ms=int((time.monotonic() - started) * 1000))
        return task

    async def _do_action(self, task: dict[str, Any], step: dict[str, Any],
                         executors: dict[str, Executor], verifiers: dict[str, Verifier]) -> str:
        kind, params = step["kind"], step["params"]
        key = idempotency_key(task["id"], step["id"], kind, params)
        action = task["actions"].get(key)
        if action and action["status"] == "COMPLETED":
            return "OK"                                     # already happened: never repeat
        if action and action["status"] in {"STARTED", "UNKNOWN"}:
            verifier = verifiers.get(kind)
            verdict = None
            if verifier is not None:
                try:
                    verdict = await _call(verifier, key, params)
                except Exception:                           # noqa: BLE001 - unverifiable == unknown
                    verdict = None
            if verdict is True:
                action.update(status="COMPLETED", result_ref="verified_after_restart",
                              finished_at=_now())
                self._save(task)
                return "OK"
            if verdict is not False:
                action["status"] = "UNKNOWN"
                self._save(task)
                self._event(task, "unknown_outcome", step=step["id"])
                return "UNKNOWN"
            action["status"] = "NOT_PERFORMED"              # verified absent: retry is safe
        executor = executors.get(kind)
        if executor is None:
            return "FAILED"
        attempts = int(action["attempts"]) if action else 0
        if attempts >= MAX_ATTEMPTS:
            return "FAILED"
        task["actions"][key] = {"step": step["id"], "kind": kind, "status": "STARTED",
                                "attempts": attempts + 1, "started_at": _now()}
        self._save(task)                                    # persisted BEFORE the external call
        try:
            result = await _call(executor, key, params)
        except ActionNotPerformed:
            task["actions"][key]["status"] = "NOT_PERFORMED"
            self._save(task)
            return "FAILED" if attempts + 1 >= MAX_ATTEMPTS else await self._do_action(
                task, step, executors, verifiers)
        except Exception:                                   # noqa: BLE001 - may have been delivered
            task["actions"][key]["status"] = "UNKNOWN"
            self._save(task)
            self._event(task, "unknown_outcome", step=step["id"])
            return "UNKNOWN"
        task["actions"][key].update(status="COMPLETED", finished_at=_now(),
                                    result_ref=str(result if result is not None else "")[:120])
        self._save(task)
        return "OK"
