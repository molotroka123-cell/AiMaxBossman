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
instead of a button that does nothing. A configured command is not readiness
either (lab checkpoint 2026-09-22): the sidecar must answer a
``bossman.openhands.v1`` handshake — protocol, tools, a reachable model and one
real tool call — or the page shows the handshake's reason.

2026-09-23 additions, all on this one path:
  * a saved agent (``agents`` row) becomes the sidecar's profile — system prompt,
    allowed tools, step cap, "tests before finish";
  * memory: the engine's own recall (lifecycle_wiring.recall_for_task) plus
    VERIFIED executable recipes (features.coding_recipes, when installed) go to
    the sidecar as quoted context; they widen nothing;
  * ``verify_tests``: after the sidecar, BOSSMAN runs the named tests in the
    sandbox itself with the guarded runner — the task is completed only if they
    pass; the sidecar's own "tests passed" is not the verdict;
  * a task still "running" from a previous server process is shown as
    ``failed`` with outcome UNKNOWN_INTERRUPTED — never as still running and
    never silently re-run.
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
#: Identifies THIS server process; a running record from another boot is orphaned.
BOOT_ID = secrets.token_hex(8)
HANDSHAKE_TTL_S = 60.0
HANDSHAKE_TIMEOUT_S = 150
_handshake_cache: dict[str, tuple[float, dict]] = {}
#: task id -> running sidecar process tree (this process only) / cancel requests.
_ACTIVE: dict[str, Any] = {}
_CANCELLED: set[str] = set()


class TaskIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=8000)
    source_repo: str = Field(min_length=1, max_length=1000)
    allowed_paths: list[str] = Field(default_factory=list, max_length=64)
    protected_paths: list[str] = Field(default_factory=list, max_length=64)
    model: str | None = Field(default=None, max_length=200)
    timeout_seconds: int = Field(default=900, ge=30, le=7200)
    agent_id: int | None = Field(default=None, ge=1)
    project_id: str | None = Field(default=None, max_length=120)
    use_memory: bool = True
    verify_tests: list[str] = Field(default_factory=list, max_length=32)


def _runtime() -> tuple[Any, Any, str]:
    """(openhands_client module, isolated_worktree module, reason-if-missing)."""
    try:
        oc = importlib.import_module(RUNTIME_MODULE)
        wt = importlib.import_module(WORKTREE_MODULE)
    except Exception as exc:  # noqa: BLE001 — the reason is shown to the owner
        return None, None, (f"рантайм OpenHands (bossman-core) не установлен рядом с Command Center: "
                            f"{type(exc).__name__}: {exc}")
    return oc, wt, ""


def _handshake(command: str) -> dict:
    """Blocking: one handshake with the configured sidecar (cached briefly)."""
    now = time.monotonic()
    hit = _handshake_cache.get(command)
    if hit and now - hit[0] < HANDSHAKE_TTL_S:
        return hit[1]
    oc, _wt, reason = _runtime()
    if oc is None:
        return {"ok": False, "reason": reason}
    try:
        resp = oc.OpenHandsClient().handshake(timeout_seconds=HANDSHAKE_TIMEOUT_S)
        out = {"ok": True, "executor": resp.get("executor"), "model": resp.get("model"),
               "tools": resp.get("tools"), "tool_call_ok": resp.get("tool_call_ok"),
               "test_runners": resp.get("test_runners"), "isolation": resp.get("isolation"),
               "deterministic_test_model": bool(resp.get("deterministic_test_model")),
               "model_kind": resp.get("model_kind") or ("MOCK_MODEL" if resp.get("deterministic_test_model") else ""),
               "endpoint": resp.get("endpoint")}
    except Exception as exc:  # noqa: BLE001 — shown to the owner
        out = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:400]}
    _handshake_cache[command] = (now, out)
    return out


async def readiness(svc) -> dict[str, Any]:
    oc, wt, reason = _runtime()
    command = os.environ.get(COMMAND_ENV, "").strip()
    roots = [str(r) for r in await allowed_roots(svc)]
    out = {"available": False, "runtime": oc is not None, "sidecar_command": bool(command),
           "roots": roots, "reason": "", "handshake": None}
    if oc is None:
        out["reason"] = reason
    elif not command:
        out["reason"] = (f"команда сайдкара не настроена: задайте {COMMAND_ENV} "
                         "(путь к OpenHands-сайдкару) и перезапустите Bossman")
    else:
        hs = await asyncio.to_thread(_handshake, command)
        out["handshake"] = hs
        if hs.get("ok"):
            out["available"] = True
        else:
            out["reason"] = f"сайдкар не прошёл проверку готовности: {hs.get('reason')}"
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


def _settle_orphan(svc, record: dict) -> dict:
    """A record left "running" by an earlier server process has no worker any
    more. Its outcome is unknown: say so, and never re-run it silently."""
    if record.get("status") == "running" and record.get("boot_id") != BOOT_ID:
        record = {**record, "status": "failed", "outcome": "UNKNOWN_INTERRUPTED",
                  "error": "задача прервана перезапуском Bossman; исход неизвестен, повторный запуск — решение владельца",
                  "finished_at": time.time()}
        try:
            _write(svc, record)
        except OSError:
            pass
    return record


def _read(svc, task_id: str) -> dict:
    path = _path(svc, task_id)
    if not path.exists():
        raise HTTPException(404, {"message": "задача не найдена"})
    return _settle_orphan(svc, json.loads(path.read_text(encoding="utf-8")))


def _list(svc) -> list[dict]:
    items = []
    for p in _store(svc).glob("*.json"):
        try:
            items.append(_settle_orphan(svc, json.loads(p.read_text(encoding="utf-8"))))
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


SIDECAR_FIELDS = ("schema", "status", "summary", "tests", "notes", "steps", "stop_reason", "tool_calls",
                  "recipes_applied", "executor", "model", "deterministic_test_model", "model_kind", "profile",
                  "memory_used", "skills_used", "endpoint", "elapsed_seconds", "tool_calls_total")


def _verify_in_sandbox(root: Path, tests: list[str], timeout: int) -> dict:
    """Bossman's own check of the sidecar's work: the named tests run in the
    sandbox with the guarded runner (minimal env, private HOME/TEMP, process
    tree killed on timeout). The sidecar's claim about tests is not used."""
    try:
        from bossman.apprentice import local_sidecar as ls  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        return {"ran": False, "passed": False, "error": f"проверяющий раннер недоступен: {type(exc).__name__}"}
    import tempfile  # noqa: WPS433
    ws = ls.Workspace(root, [], [])
    with tempfile.TemporaryDirectory(prefix="bossman-verify-") as scratch:
        try:
            res = ls.tool_run_tests(ws, {"paths": list(tests), "runner": "auto"}, scratch=Path(scratch),
                                    deadline=time.monotonic() + timeout, test_timeout=timeout)
        except ls.ToolError as exc:
            return {"ran": False, "passed": False, "error": str(exc)}
    return {"ran": True, **res}


class _Cancelled(Exception):
    """The owner cancelled the task (STOP)."""


def _execute(record: dict, repo: Path, body: TaskIn, context: dict | None = None) -> dict:
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
    task_id = record["id"]
    try:
        if task_id in _CANCELLED:
            raise _Cancelled()
        client = oc.OpenHandsClient(on_process=lambda tree: _ACTIVE.__setitem__(task_id, tree))
        extra = {"context": context} if context else {}
        request = oc.OpenHandsRequest(body.instruction, root, tuple(body.allowed_paths),
                                      tuple(body.protected_paths), model=body.model,
                                      timeout_seconds=int(body.timeout_seconds),
                                      metadata={"coding_task_id": record["id"]}, **extra)
        try:
            result = client.run(request)
        finally:
            _ACTIVE.pop(task_id, None)
        if task_id in _CANCELLED:
            raise _Cancelled()
        sidecar = {k: v for k, v in dict(result.sidecar).items() if k in SIDECAR_FIELDS}
        ok = result.status == "completed"
        result_fields = {"status": "completed" if ok else "failed",
                         "sidecar_status": result.status,
                         "changed_files": list(result.changed_files), "diff": result.diff,
                         "sidecar": sidecar,
                         "error": "" if ok else "сайдкар сообщил о неудаче"}
        if ok and body.verify_tests:
            verification = _verify_in_sandbox(Path(root), list(body.verify_tests),
                                              max(30, min(600, int(body.timeout_seconds))))
            result_fields["verification"] = verification
            if not verification.get("passed"):
                result_fields["status"] = "failed"
                result_fields["error"] = "независимая проверка Bossman не прошла: " + (
                    verification.get("error") or f"exit={verification.get('exit_code')}")
    except _Cancelled:
        result_fields = {"status": "failed", "outcome": "CANCELLED", "changed_files": [], "diff": "",
                         "error": "задача отменена владельцем; процессы сайдкара остановлены"}
    except oc.OpenHandsError as exc:
        if task_id in _CANCELLED:     # the killed sidecar left no valid answer: that is the cancel
            result_fields = {"status": "failed", "outcome": "CANCELLED", "changed_files": [], "diff": "",
                             "error": "задача отменена владельцем; процессы сайдкара остановлены"}
        else:
            # The evidence boundary refused the result: out-of-scope/protected
            # change, tampering with HEAD/config/remotes/index, invalid contract.
            result_fields = {"status": "blocked", "error": str(exc)[:800], "changed_files": [], "diff": ""}
    except Exception as exc:  # noqa: BLE001
        result_fields = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:800],
                         "changed_files": [], "diff": ""}
    finally:
        _CANCELLED.discard(task_id)
        try:
            evidence = sandbox.derive_evidence()
        except Exception as exc:  # noqa: BLE001
            evidence = {"error": f"{type(exc).__name__}: {exc}"}
        sandbox.cleanup()
        cleanup = sandbox.cleanup_state()
    return {**record, **result_fields, "evidence": evidence, "sandbox_cleanup": cleanup,
            "duration_seconds": round(time.time() - started, 2), "finished_at": time.time()}


async def _agent_profile(svc, agent_id: int | None) -> dict | None:
    """A saved agent (the product's ``agents`` row) as the sidecar profile."""
    if agent_id is None:
        return None
    import sqlalchemy as sa  # noqa: WPS433
    from ..db import agents as agents_t  # noqa: WPS433
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(agents_t).where(agents_t.c.id == int(agent_id)))).mappings().first()
    if row is None:
        raise HTTPException(404, {"message": f"агент {agent_id} не найден"})
    if row.get("enabled") is False:
        raise HTTPException(409, {"message": f"агент {row['name']} выключен"})
    perms = row.get("permissions") if isinstance(row.get("permissions"), dict) else {}
    # A lab observer (auditor / result verifier / UX observer) is not a student: its
    # read-only tool list would otherwise reach the sidecar as "no tools" and fall
    # back to the FULL sidecar set, edit tools included.
    if row.get("role") == "lab:observer" or perms.get("lab_observer"):
        raise HTTPException(409, {"code": "LAB_OBSERVER_NOT_A_STUDENT",
                                  "message": f"агент {row['name']} — наблюдатель лаборатории; "
                                             "coding-задачи он не выполняет и инструментов правки не получает"})
    tools = row.get("tools") if isinstance(row.get("tools"), list) else []
    return {"agent_id": int(row["id"]), "name": row["name"], "system_prompt": row.get("system_prompt") or "",
            "max_steps": int(row.get("max_steps") or 0) or None,
            "tools": [str(t) for t in tools] or None,
            "require_tests_before_finish": bool(perms.get("require_tests_before_finish")),
            "use_memory": perms.get("use_memory")}


async def _memory_context(svc, body: TaskIn, profile: dict | None) -> tuple[dict, dict]:
    """(context for the sidecar, memory facts for the record). Recall failure
    costs the task its memory, never its run (same rule as the engine)."""
    use = body.use_memory and (profile is None or profile.get("use_memory") is not False)
    info: dict[str, Any] = {"requested": bool(use), "recalled": False, "sources": [], "recipe_ids": [], "error": ""}
    ctx: dict[str, Any] = {}
    if not use:
        return ctx, info
    project = body.project_id or "bossman"
    try:
        from ..v2.memory.lifecycle_wiring import recall_for_task  # noqa: WPS433
        pack = await asyncio.wait_for(recall_for_task(svc, {"id": f"coding-{secrets.token_hex(3)}",
                                                            "prompt": body.instruction,
                                                            "meta": {"project_id": project}}), timeout=20)
        if pack:
            ctx["memory_text"] = pack["text"]
            info["recalled"] = True
            info["sources"] = list(pack.get("sources") or [])[:20]
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"recall: {type(exc).__name__}"
    try:
        recipes_mod = importlib.import_module("bcc.features.coding_recipes")
    except ImportError:
        recipes_mod = None
    if recipes_mod is not None:
        try:
            fn = recipes_mod.executable_recipes
            recipes = (await fn(svc, body.instruction, project) if asyncio.iscoroutinefunction(fn)
                       else await asyncio.to_thread(fn, svc, body.instruction, project))
            if recipes:
                ctx["recipes"] = recipes
                info["recipe_ids"] = [str(r.get("id")) for r in recipes]
                info["recalled"] = True
        except Exception as exc:  # noqa: BLE001
            info["error"] = (info["error"] + f"; recipes: {type(exc).__name__}").strip("; ")
    return ctx, info


async def _skills_context(svc, body: TaskIn) -> tuple[list[dict], dict]:
    """Methodology skills from the vetted catalog (features.skills). UNVERIFIED
    guidance only: they grant no tool, no permission and change no success
    criterion; the catalog itself strips policy-violating lines. Never raises."""
    info: dict[str, Any] = {"ids": [], "error": ""}
    try:
        from .skills import skills_for_task  # noqa: WPS433
        items = await skills_for_task(svc, body.instruction)
    except Exception as exc:  # noqa: BLE001 — skills cost the task its guidance, never its run
        info["error"] = f"{type(exc).__name__}"
        return [], info
    skills = [{"id": s.get("id"), "title": s.get("title"), "text": s.get("text"),
               "status": s.get("status"), "unsupported_tools": s.get("unsupported_tools") or []}
              for s in items or [] if s.get("text")]
    info["ids"] = [s["id"] for s in skills]
    return skills, info


async def _run(svc, record: dict, repo: Path, body: TaskIn, context: dict | None = None) -> None:
    try:
        final = await asyncio.to_thread(_execute, record, repo, body, context)
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


@router.post("/coding-tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request):
    """Owner STOP for one coding task: the sidecar's whole process tree is
    killed; the record ends failed/CANCELLED (never completed) once the worker
    returns. Cancelling a finished task changes nothing."""
    rec = _read(request.app.state.svc, task_id)
    if rec.get("status") in TERMINAL:
        return {"id": task_id, "status": rec["status"], "cancelled": False}
    _CANCELLED.add(task_id)
    tree = _ACTIVE.get(task_id)
    if tree is not None:
        await asyncio.to_thread(tree.kill)
    await request.app.state.svc.bus.emit("coding.task.cancel_requested", task_id=task_id)
    return {"id": task_id, "status": "cancelling", "cancelled": True, "process_killed": tree is not None}


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
    profile = await _agent_profile(svc, body.agent_id)
    context, memory = await _memory_context(svc, body, profile)
    skills, skills_info = await _skills_context(svc, body)
    if skills:
        context["skills"] = skills
    if profile:
        context["profile"] = {k: v for k, v in profile.items() if k != "use_memory" and v is not None}
    record = {"id": secrets.token_hex(6), "status": "running", "instruction": body.instruction,
              "boot_id": BOOT_ID, "agent_id": body.agent_id,
              "agent": ({"id": profile["agent_id"], "name": profile["name"]} if profile else None),
              "project_id": body.project_id, "memory": memory, "skills": skills_info,
              "verify_tests": list(body.verify_tests),
              "source_repo": str(repo), "allowed_paths": list(body.allowed_paths),
              "protected_paths": list(body.protected_paths), "model": body.model,
              "created_at": time.time(), "finished_at": None, "changed_files": [], "diff": "",
              "error": "", "evidence": None, "sandbox_cleanup": None,
              "authority": {"push": False, "merge": False, "deploy": False}}
    _write(svc, record)
    await svc.bus.emit("coding.task.created", task_id=record["id"], repo=str(repo))
    task = asyncio.create_task(_run(svc, record, repo, body, context))
    running = getattr(svc, "_coding_tasks_running", None)
    if running is None:
        running = svc._coding_tasks_running = set()
    running.add(task)
    task.add_done_callback(running.discard)
    return _public(record)


FEATURE = Feature(name="coding_tasks", router=router)
