"""Owner controls for the existing bounded evolution loop.

One Command Center instance, one campaign directory in its configured data
root. The worker is the shipped bossman_v3.self_improvement.loop, not a new
coding engine. Source checkout must be explicitly within the existing code
roots; no endpoint may promote a candidate into stable.
"""
from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import subprocess
import sys
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import Feature
from .tools_code import _within, allowed_roots

router = APIRouter(prefix="/evolution", tags=["evolution"])
_ACTIVE: dict[Path, tuple[object, object]] = {}


class StartBody(BaseModel):
    backend: Literal["bossman_coding"] = "bossman_coding"
    source_repo: str | None = Field(default=None, max_length=1000)
    cycles: int = Field(default=1, ge=1, le=200)
    model: str | None = Field(default=None, max_length=200)


def _loop():
    try:
        return importlib.import_module("bossman_v3.self_improvement.loop")
    except ImportError as exc:
        raise HTTPException(503, {"code": "EVOLUTION_RUNTIME_MISSING", "message": str(exc)[:300]}) from exc


def _work(svc) -> Path:
    root = Path(svc.settings.data_dir).resolve() / "evolution" / "campaign"
    root.mkdir(parents=True, exist_ok=True)
    return root


async def _repo(svc, raw: str | None) -> Path:
    roots = await allowed_roots(svc)
    if raw:
        try:
            path = Path(raw).expanduser().resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise HTTPException(400, {"code": "EVOLUTION_REPO_NOT_FOUND"}) from exc
        if not _within(path, roots):
            raise HTTPException(403, {"code": "EVOLUTION_ROOT_DENIED"})
        options = [path]
    else:
        options = [root for root in roots if (root / ".git").exists()]
    if len(options) != 1 or not (options[0] / ".git").exists():
        raise HTTPException(422, {"code": "EVOLUTION_SOURCE_REQUIRED",
                                  "message": "configure one code root containing a Git checkout or pass source_repo"})
    return options[0]


def _bootstrap(module) -> str:
    # In an embeddable runtime -I ignores cwd/PYTHONPATH. Pin the actual
    # installed package roots in the bootstrap, as the coding runner does.
    roots = []
    for package in ("bossman_v3", "bossman", "learning", "bcc", "bossman_shared"):
        try:
            imported = importlib.import_module(package)
            location = str(Path(imported.__file__).resolve().parents[1])
            if location not in roots:
                roots.append(location)
        except (ImportError, TypeError, OSError):
            continue
    if str(Path(module.__file__).resolve().parents[2]) not in roots:
        roots.insert(0, str(Path(module.__file__).resolve().parents[2]))
    return ("import sys; sys.path[:0] = " + repr(roots) + "; "
            "from bossman_v3.self_improvement.loop import main; raise SystemExit(main(sys.argv[1:]))")


def _worker_command(module, work: Path, repo: Path, suite: Path, body: StartBody, svc) -> list[str]:
    argv = [sys.executable, "-I", "-c", _bootstrap(module), "loop", "--work", str(work), "--repo", str(repo),
            "--suite", str(suite), "--backend", body.backend, "--data-dir", str(svc.settings.data_dir),
            "--max-cycles", str(body.cycles)]
    if body.model:
        argv += ["--model", body.model]
    return argv


async def _reap(work: Path, tree, log) -> None:
    try:
        await asyncio.to_thread(tree.proc.wait)
    finally:
        tree.close()
        log.close()
        _ACTIVE.pop(work, None)


def _view(svc) -> dict:
    return _loop().status(_work(svc))


@router.get("/status")
async def status(request: Request):
    return _view(request.app.state.svc)


@router.get("/report")
async def report(request: Request):
    svc = request.app.state.svc
    work = _work(svc)
    if not (work / "loop-state.json").is_file():
        raise HTTPException(404, {"code": "NO_CAMPAIGN"})
    return _loop().report(work)


@router.post("/start")
async def start(body: StartBody, request: Request):
    svc = request.app.state.svc
    module = _loop()
    work = _work(svc)
    active = _ACTIVE.get(work)
    if active or module.status(work)["loop_running"]:
        raise HTTPException(409, {"code": "EVOLUTION_ALREADY_RUNNING"})
    repo = await _repo(svc, body.source_repo)
    suite = repo / "config" / "evolution" / "owner-v1.1.json"
    if not suite.is_file():
        raise HTTPException(422, {"code": "EVOLUTION_SUITE_MISSING", "path": str(suite)})
    if (work / "loop-state.json").is_file():
        raise HTTPException(409, {"code": "EVOLUTION_EXISTING_CAMPAIGN",
                                  "message": "resume the existing campaign; no silent replacement or reset"})
    from bossman.apprentice.proc_tree import ProcessTree  # noqa: PLC0415
    logfile = (work / "loop-worker.log").open("ab")
    try:
        tree = ProcessTree(_worker_command(module, work, repo, suite, body, svc),
                           stdin=subprocess.DEVNULL, stdout=logfile, stderr=subprocess.STDOUT)
    except Exception:
        logfile.close()
        raise
    _ACTIVE[work] = (tree, logfile)
    task = asyncio.create_task(_reap(work, tree, logfile), name="bcc-evolution-reap")
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    return {"status": "STARTING", "campaign": str(work), "pid": tree.pid,
            "repo": str(repo), "cycles": body.cycles, "model": body.model}


@router.post("/pause")
async def pause(request: Request):
    svc = request.app.state.svc
    return {"control": _loop().request_pause(_work(svc), "owner-api"), "campaign": _view(svc)}


@router.post("/stop")
async def stop(request: Request):
    svc = request.app.state.svc
    work = _work(svc)
    control = _loop().request_stop(work, "owner-api")
    # Loop watchdog observes STOP and cancels the current attempt's entire
    # process tree, retaining the result/evidence. A hard kill here would
    # instead mark the in-flight attempt UNKNOWN_OUTCOME.
    return {"control": control, "campaign": _view(svc)}


@router.post("/resume")
async def resume(request: Request):
    svc = request.app.state.svc
    work = _work(svc)
    module = _loop()
    if not (work / "loop-state.json").is_file():
        raise HTTPException(404, {"code": "NO_CAMPAIGN"})
    if _ACTIVE.get(work) or module.status(work)["loop_running"]:
        raise HTTPException(409, {"code": "EVOLUTION_STILL_RUNNING"})
    state = module._read_json(work / module.STATE_FILE) or {}
    repo = Path((state.get("config") or {}).get("repo") or "")
    await _repo(svc, str(repo))  # recheck code-root policy after server restart
    module.clear_controls(work, "owner-api")
    from bossman.apprentice.proc_tree import ProcessTree  # noqa: PLC0415
    log = (work / "loop-worker.log").open("ab")
    try:
        tree = ProcessTree([sys.executable, "-I", "-c", _bootstrap(module), "loop", "--work", str(work)],
                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    except Exception:
        log.close()
        raise
    _ACTIVE[work] = (tree, log)
    task = asyncio.create_task(_reap(work, tree, log), name="bcc-evolution-reap")
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    return {"status": "RESUMING", "campaign": str(work), "pid": tree.pid}


FEATURE = Feature(name="evolution", router=router)
