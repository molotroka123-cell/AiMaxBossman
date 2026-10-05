"""Evidence-aware capability tree for the existing Bossman evolution loop.

The tree is a read-only map of product capabilities plus a small owner journal.
It does not promote candidates, edit stable, or infer PASS from source files.
The deterministic scanner uses Git and syntax only; no LLM is called.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import Feature

router = APIRouter(prefix="/capability-tree", tags=["capability-tree"])
PACKAGE_MAP = Path(__file__).resolve().parents[1] / "capability_tree_seed.json"
PRODUCT_PREFIXES = ("bossman-core/bossman/", "bossman-core/bossman_v3/", "command-center/bcc/",
                    "command-center/ui/", "apps/", ".agents/skills/", ".claude/skills/")
PRODUCT_SUFFIXES = (".py", ".js", ".html", "SKILL.md", "app.manifest.yaml")
_NOTES_LOCK = threading.Lock()
_SCAN_LOCK = asyncio.Lock()


class NoteBody(BaseModel):
    node_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:/-]+$")
    text: str = Field(default="", max_length=4000)
    state: Literal["note", "working", "blocked", "done"] = "note"


class ScanBody(BaseModel):
    source_repo: str | None = Field(default=None, max_length=1000)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _tree_dir(svc) -> Path:
    path = Path(svc.settings.data_dir).resolve() / "evolution" / "capability-tree"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _seed() -> dict:
    data = _read(PACKAGE_MAP, {})
    if data.get("schema_version") != "1.0" or not isinstance(data.get("nodes"), list):
        raise HTTPException(503, {"code": "CAPABILITY_TREE_SEED_MISSING"})
    return data


def _git(repo: Path, *args: str, timeout: int = 120) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, check=False)
    if proc.returncode:
        raise ValueError(f"git {' '.join(args[:2])}: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _current_symbols(repo: Path) -> dict:
    commands, routes, tools, pages, plugins = [], [], [], [], []
    for path in sorted(repo.rglob("*.py")):
        rel = path.relative_to(repo).as_posix()
        if any(p.startswith(".") or p in {"tests", "artifacts", "archive", "build", "dist", "node_modules"}
               for p in path.relative_to(repo).parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
            vals = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if fn == "add_parser" and vals:
                commands.append({"name": vals[0], "path": rel, "line": node.lineno})
            if fn in {"get", "post", "put", "delete", "patch", "websocket"} and vals and vals[0].startswith("/"):
                routes.append({"name": f"{fn.upper()} {vals[0]}", "path": rel, "line": node.lineno})
            if fn in {"ToolSpec", "register_tool"}:
                name = next((k.value.value for k in node.keywords if k.arg == "name" and isinstance(k.value, ast.Constant)), None)
                if name or vals:
                    tools.append({"name": name or vals[0], "path": rel, "line": node.lineno})
            if fn == "Capability" and vals:
                plugins.append({"name": ".".join(vals[:2]), "path": rel, "line": node.lineno})
    page_re = re.compile(r"\bid:\s*['\"]([^'\"]+)['\"]")
    for path in sorted((repo / "command-center" / "ui" / "pages").glob("*.js")):
        with contextlib.suppress(OSError, UnicodeError):
            text = path.read_text(encoding="utf-8")
            match = page_re.search(text)
            if match:
                pages.append({"name": match.group(1), "path": path.relative_to(repo).as_posix()})
    return {"commands": commands, "routes": routes, "tools": tools, "plugins": plugins, "pages": pages}


def scan_repository(repo: Path, seed: dict, previous: dict | None = None) -> dict:
    """Inventory every origin ref and report capability-looking files absent from map sources."""
    repo = repo.resolve(strict=True)
    if not (repo / ".git").exists():
        raise ValueError("source_repo is not a Git checkout")
    raw_refs = _git(repo, "for-each-ref", "--format=%(refname:short)|%(objectname)", "refs/remotes/origin")
    refs = []
    union: set[str] = set()
    for row in raw_refs.splitlines():
        if not row or row.startswith("origin/HEAD|"):
            continue
        name, sha = row.split("|", 1)
        refs.append({"name": name.removeprefix("origin/"), "sha": sha})
        if len(refs) > 500:
            raise ValueError("more than 500 remote refs: prune/fetch an explicit repository mirror first")
        union.update(_git(repo, "ls-tree", "-r", "--name-only", sha, timeout=180).splitlines())
    head = _git(repo, "rev-parse", "HEAD").strip()
    known = {s.get("path") for n in seed.get("nodes", []) for s in n.get("sources", []) if s.get("path")}
    candidates = sorted(p for p in union if p.startswith(PRODUCT_PREFIXES) and p.endswith(PRODUCT_SUFFIXES)
                        and not any(part in {"tests", "test", "artifacts", "archive", "snapshots"}
                                    for part in Path(p).parts)
                        and not Path(p).name.startswith("test_") and Path(p).name not in {"__init__.py", "setup.py"})
    unmapped = [p for p in candidates if p not in known]
    skills = sorted(p for p in union if p.endswith("/SKILL.md"))
    apps = sorted({p.split("/")[1] for p in union if p.startswith("apps/") and len(p.split("/")) > 2})
    symbols = _current_symbols(repo)
    fingerprint_payload = {"refs": refs, "candidates": candidates, "skills": skills, "apps": apps, **symbols}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True).encode()).hexdigest()
    old_paths = set((previous or {}).get("candidate_paths") or [])
    result = {"schema": "bossman.capability-scan/1", "generated_at": _now(), "repo": str(repo),
              "head_sha": head, "fingerprint": fingerprint, "ai_used": False,
              "branch_count": len(refs), "union_path_count": len(union), "candidate_paths": candidates,
              "new_since_previous": sorted(set(candidates) - old_paths) if previous else [],
              "baseline_created": previous is None, "unmapped_capability_files": unmapped,
              "skill_files": skills, "apps": apps, "symbols": symbols,
              "warning": "Source presence is discovery evidence only; it never promotes a node to PASS."}
    return result


def _campaign(svc) -> tuple[str, dict]:
    """The existing loop's campaign to show: /api/evolution first, else the 1.5 owner-run one.

    Both are the same bossman_v3.self_improvement.loop, started by different owner
    controls into different work dirs; a running campaign wins over a finished one.
    """
    from . import evolution  # local import prevents feature-load cycle
    data = Path(svc.settings.data_dir).resolve()
    options = (("evolution", data / "evolution" / "campaign"),
               ("v15_owner_run", data / "v1.5" / "owner-run" / "self-improve" / "evolution"))
    present = [(name, work) for name, work in options if (work / "loop-state.json").is_file()]
    if not present:
        return "evolution", evolution._view(svc)
    module = evolution._loop()
    views = [(name, module.status(work)) for name, work in present]
    return next(((n, v) for n, v in views if v.get("loop_running")), views[0])


def _activity(svc) -> dict:
    source, campaign = _campaign(svc)
    cycle = campaign.get("cycle") or {}
    query = " ".join(str(cycle.get(k) or "") for k in ("task", "phase")).lower()
    seed = _seed()
    matches = []
    if query.strip():
        terms = {t for t in re.split(r"[^a-zа-я0-9_.-]+", query) if len(t) > 2}
        for node in seed["nodes"]:
            hay = (str(node.get("label", "")) + " " + str(node.get("detail", ""))).lower()
            score = sum(t in hay for t in terms)
            if score:
                matches.append({"node_id": node["id"], "label": node["label"], "score": score})
        matches.sort(key=lambda row: (-row["score"], row["label"]))
    return {"schema": "bossman.capability-activity/1", "observed_at": _now(), "campaign_source": source,
            "campaign": campaign, "active_matches": matches[:8], "proof_level": "runtime_state_only"}


def _stable_activity(value: dict) -> dict:
    """What counts as a change worth persisting: not RAM, heartbeat or elapsed seconds."""
    campaign = value.get("campaign") or {}
    cycle = campaign.get("cycle") or {}
    return {"source": value.get("campaign_source"), "proof_level": value.get("proof_level"),
            "matches": value.get("active_matches"),
            "campaign": {k: campaign.get(k) for k in ("campaign_id", "status", "loop_running", "paused", "stopped",
                                                       "halt_reason", "cycles_closed", "verifier_verdict")},
            "cycle": {k: cycle.get(k) for k in ("index", "id", "task", "phase", "outcome")}}


@router.get("")
async def tree(request: Request):
    svc = request.app.state.svc
    root = _tree_dir(svc)
    return {"tree": _seed(), "activity": _activity(svc),
            "notes": _read(root / "owner-notes.json", {}),
            "scan": _read(root / "scan-latest.json", None)}


@router.post("/note")
async def save_note(body: NoteBody, request: Request):
    svc = request.app.state.svc
    seed = _seed()
    if body.node_id not in {n["id"] for n in seed["nodes"]}:
        raise HTTPException(404, {"code": "CAPABILITY_NODE_NOT_FOUND"})
    path = _tree_dir(svc) / "owner-notes.json"
    with _NOTES_LOCK:
        notes = _read(path, {})
        if body.text or body.state != "note":
            notes[body.node_id] = {"text": body.text, "state": body.state, "updated_at": _now()}
        else:
            notes.pop(body.node_id, None)
        _atomic(path, notes)
    return {"saved": True, "node_id": body.node_id, "note": notes.get(body.node_id)}


@router.post("/scan")
async def scan(body: ScanBody, request: Request):
    from . import evolution
    svc = request.app.state.svc
    if _SCAN_LOCK.locked():
        raise HTTPException(409, {"code": "CAPABILITY_SCAN_ALREADY_RUNNING"})
    async with _SCAN_LOCK:
        repo = await evolution._repo(svc, body.source_repo)
        path = _tree_dir(svc) / "scan-latest.json"
        previous = _read(path, None)
        try:
            result = await asyncio.to_thread(scan_repository, repo, _seed(), previous)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise HTTPException(422, {"code": "CAPABILITY_SCAN_FAILED", "message": str(exc)[:500]}) from exc
        _atomic(path, result)
        return result


async def tick(svc) -> None:
    """Durably mirror only changed campaign activity; no mutation of the seed map."""
    try:
        value = _activity(svc)
        path = _tree_dir(svc) / "activity-latest.json"
        if _stable_activity(value) != _stable_activity(_read(path, {})):
            _atomic(path, value)
    except Exception:
        return


FEATURE = Feature(name="capability_tree", router=router, tick=tick, tick_seconds=5.0)
