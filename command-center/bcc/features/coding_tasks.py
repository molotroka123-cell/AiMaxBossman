"""Coding tasks — the owner-visible path to the OpenHands runtime.

Owner audit 2026-09-08, F4: the Coding page managed worktrees (create / diff /
merge / discard) but had no way to hand a coding task to the agent — "0
responses" was an exact description of the UI. The engine existed
(bossman.apprentice.openhands_client, exercised end to end by bossman-core's
tests) and the owner could not reach it.

This feature is an API/UI wrapper around that ONE runtime; no second coding
agent is built here:

    Coding → New coding task → workspace (a repository inside the allowed
    roots) → instruction + allowed/protected paths → OpenHands status →
    diff / changed files / evidence / sandbox cleanup state.

What it deliberately does NOT do: push, merge, deploy, widen the sidecar's
permissions, or mark anything "complete" on the sidecar's word. The sidecar
runs in a disposable clone with no remote (IsolatedWorktree); its result is
admitted only through OpenHandsClient's independent evidence derivation; the
patch is returned as EVIDENCE for the owner, and applying it to the source
repository stays a separate owner decision made with the existing coding
session tooling.

Readiness is reported honestly: without the bossman-core runtime importable
or without `BOSSMAN_OPENHANDS_COMMAND` configured, the page says so and why,
instead of a button that does nothing.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import Feature
from .tools_code import _within, allowed_roots

router = APIRouter()

RUNTIME_MODULE = "bossman.apprentice.openhands_client"
WORKTREE_MODULE = "bossman.apprentice.isolated_worktree"
COMMAND_ENV = "BOSSMAN_OPENHANDS_COMMAND"
TERMINAL = ("completed", "failed", "blocked")
_ID_RE = re.compile(r"^[a-z0-9]{12}$")


class TaskIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=8000)
    source_repo: str = Field(min_length=1, max_length=1000)
    allowed_paths: list[str] = Field(default_factory=list, max_length=64)
    protected_paths: list[str] = Field(default_factory=list, max_length=64)
    model: str | None = Field(default=None, max_length=200)
    timeout_seconds: int = Field(default=900, ge=30, le=7200)


def _runtime() -> tuple[Any, Any, str]:
    """(openhands_client module, isolated_worktree module, reason-if-missing)."""
    try:
        oc = importlib.import_module(RUNTIME_MODULE)
        wt = importlib.import_module(WORKTREE_MODULE)
    except Exception as exc:  # noqa: BLE001 — the reason is shown to the owner
        return None, None, (f"рантайм OpenHands (bossman-core) не установлен рядом с Command Center: "
                            f"{type(exc).__name__}: {exc}")
    return oc, wt, ""


async def readiness(svc) -> dict[str, Any]:
    oc, wt, reason = _runtime()
    command = os.environ.get(COMMAND_ENV, "").strip()
    roots = [str(r) for r in await allowed_roots(svc)]
    out = {"available": False, "runtime": oc is not None, "sidecar_command": bool(command),
           "roots": roots, "reason": ""}
    if oc is None:
        out["reason"] = reason
    elif not command:
        out["reason"] = (f"команда сайдкара не настроена: задайте {COMMAND_ENV} "
                         "(путь к OpenHands-сайдкару) и перезапустите Bossman")
    else:
        out["available"] = True
    return out


def _store(svc) -> Path:
    root = Path(svc.settings.data_dir) / "coding-tasks"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path(svc, task_id: str) -> Path:
    if not _ID_RE.match(task_id or ""):
        raise HTTPException(404, {"message": "задача не найдена"})
    return _store(svc) / f"{task_id}.json"


def _write(svc, record: dict) -> None:
    path = _path(svc, record["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read(svc, task_id: str) -> dict:
    path = _path(svc, task_id)
    if not path.exists():
        raise HTTPException(404, {"message": "задача не найдена"})
    return json.loads(path.read_text(encoding="utf-8"))


def _list(svc) -> list[dict]:
    items = []
    for p in _store(svc).glob("*.json"):
        try:
            items.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    items.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return items


def _public(record: dict) -> dict:
    """The list view: no patch bodies, so the page stays light."""
    out = dict(record)
    out.pop("diff", None)
    out["diff_bytes"] = len(record.get("diff") or "")
    return out


async def _confined_repo(svc, raw: str) -> Path:
    roots = await allowed_roots(svc)
    try:
        p = Path(str(raw)).expanduser().resolve(strict=True)
    except (OSError, ValueError):
        raise HTTPException(400, {"message": f"путь репозитория недоступен: {raw}"})
    if not any(_within(p, [r]) for r in roots):
        raise HTTPException(403, {"message": "репозиторий вне разрешённых корней",
                                  "hint": "добавьте корень в настройки code/terminal roots"})
    if not (p / ".git").exists():
        raise HTTPException(400, {"message": "это не git-репозиторий"})
    return p


def _execute(record: dict, repo: Path, body: TaskIn) -> dict:
    """Blocking: runs in a worker thread. Returns the terminal record."""
    oc, wt, reason = _runtime()
    if oc is None:
        return {**record, "status": "blocked", "error": reason}
    sandbox = wt.IsolatedWorktree(str(repo))
    started = time.time()
    try:
        root = sandbox.create()
    except Exception as exc:  # noqa: BLE001
        return {**record, "status": "failed",
                "error": f"песочница не создана: {type(exc).__name__}: {exc}"[:500]}
    result_fields: dict[str, Any] = {}
    try:
        client = oc.OpenHandsClient()
        request = oc.OpenHandsRequest(body.instruction, root, tuple(body.allowed_paths),
                                      tuple(body.protected_paths), model=body.model,
                                      timeout_seconds=int(body.timeout_seconds),
                                      metadata={"coding_task_id": record["id"]})
        result = client.run(request)
        result_fields = {"status": "completed" if result.status == "completed" else "failed",
                         "sidecar_status": result.status,
                         "changed_files": list(result.changed_files), "diff": result.diff,
                         "sidecar": {k: v for k, v in dict(result.sidecar).items()
                                     if k in ("schema", "status", "summary", "tests", "notes")},
                         "error": "" if result.status == "completed" else "сайдкар сообщил о неудаче"}
    except oc.OpenHandsError as exc:
        # The evidence boundary refused the result: out-of-scope/protected
        # change, tampering with HEAD/config/remotes/index, invalid contract.
        result_fields = {"status": "blocked", "error": str(exc)[:800], "changed_files": [], "diff": ""}
    except Exception as exc:  # noqa: BLE001
        result_fields = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:800],
                         "changed_files": [], "diff": ""}
    finally:
        try:
            evidence = sandbox.derive_evidence()
        except Exception as exc:  # noqa: BLE001
            evidence = {"error": f"{type(exc).__name__}: {exc}"}
        sandbox.cleanup()
        cleanup = sandbox.cleanup_state()
    return {**record, **result_fields, "evidence": evidence, "sandbox_cleanup": cleanup,
            "duration_seconds": round(time.time() - started, 2), "finished_at": time.time()}


async def _run(svc, record: dict, repo: Path, body: TaskIn) -> None:
    try:
        final = await asyncio.to_thread(_execute, record, repo, body)
    except Exception as exc:  # noqa: BLE001
        final = {**record, "status": "failed", "error": f"{type(exc).__name__}: {exc}"[:800],
                 "finished_at": time.time()}
    _write(svc, final)
    await svc.bus.emit(f"coding.task.{final['status']}", task_id=final["id"], repo=str(repo),
                       changed_files=len(final.get("changed_files") or []),
                       error=str(final.get("error") or "")[:300])


@router.get("/coding-tasks/readiness")
async def get_readiness(request: Request):
    return await readiness(request.app.state.svc)


@router.get("/coding-tasks")
async def list_tasks(request: Request):
    return {"items": [_public(r) for r in _list(request.app.state.svc)]}


@router.get("/coding-tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    return _read(request.app.state.svc, task_id)


@router.post("/coding-tasks")
async def create_task(body: TaskIn, request: Request):
    svc = request.app.state.svc
    ready = await readiness(svc)
    if not ready["available"]:
        raise HTTPException(503, {"code": "OPENHANDS_UNAVAILABLE", "message": ready["reason"],
                                  "readiness": ready})
    if not body.allowed_paths:
        raise HTTPException(422, {"message": "allowed_paths обязателен: агент должен получить явную область правок"})
    repo = await _confined_repo(svc, body.source_repo)
    record = {"id": secrets.token_hex(6), "status": "running", "instruction": body.instruction,
              "source_repo": str(repo), "allowed_paths": list(body.allowed_paths),
              "protected_paths": list(body.protected_paths), "model": body.model,
              "created_at": time.time(), "finished_at": None, "changed_files": [], "diff": "",
              "error": "", "evidence": None, "sandbox_cleanup": None,
              "authority": {"push": False, "merge": False, "deploy": False}}
    _write(svc, record)
    await svc.bus.emit("coding.task.created", task_id=record["id"], repo=str(repo))
    task = asyncio.create_task(_run(svc, record, repo, body))
    running = getattr(svc, "_coding_tasks_running", None)
    if running is None:
        running = svc._coding_tasks_running = set()
    running.add(task)
    task.add_done_callback(running.discard)
    return _public(record)


FEATURE = Feature(name="coding_tasks", router=router)
