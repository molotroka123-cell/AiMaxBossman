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
from .tools_code import allowed_roots

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
    if previous and previous.get("repo") != str(repo):
        previous = None  # a scan of another checkout is not this repository's baseline
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
            "scan": _read(root / "scan-latest.json", None),
            "work": _work_state(svc)[1]["jobs"]}


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


def _is_bossman_checkout(path: Path) -> bool:
    return (path / ".git").exists() and (path / "command-center" / "bcc").is_dir() and (path / "bossman-core").is_dir()


async def _scan_source(svc, raw: str | None) -> Path:
    """Explicit path: the loop's code-root policy plus a Bossman check. None: the one Bossman checkout among roots.

    Other Git roots (an owner's game or site) are normal; scanning them would
    report zero Bossman capabilities and poison the next comparison.
    """
    from . import evolution
    if raw:
        repo = await evolution._repo(svc, raw)
        if not _is_bossman_checkout(repo):
            raise HTTPException(422, {"code": "CAPABILITY_SCAN_NOT_BOSSMAN", "path": str(repo)})
        return repo
    found = [root for root in await allowed_roots(svc) if _is_bossman_checkout(root)]
    if len(found) != 1:
        raise HTTPException(422, {"code": "CAPABILITY_SCAN_SOURCE_REQUIRED", "candidates": [str(r) for r in found],
                                  "message": "add one Bossman checkout to the code roots or choose source_repo"})
    return found[0]


@router.post("/scan")
async def scan(body: ScanBody, request: Request):
    svc = request.app.state.svc
    if _SCAN_LOCK.locked():
        raise HTTPException(409, {"code": "CAPABILITY_SCAN_ALREADY_RUNNING"})
    async with _SCAN_LOCK:
        repo = await _scan_source(svc, body.source_repo)
        path = _tree_dir(svc) / "scan-latest.json"
        previous = _read(path, None)
        try:
            result = await asyncio.to_thread(scan_repository, repo, _seed(), previous)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise HTTPException(422, {"code": "CAPABILITY_SCAN_FAILED", "message": str(exc)[:500]}) from exc
        _atomic(path, result)
        return result


class WorkBody(BaseModel):
    node_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:/-]+$")
    instruction: str = Field(default="", max_length=4000)
    source_repo: str | None = Field(default=None, max_length=1000)


_WORK_LOCK = threading.Lock()
_ZONE_FILES_MAX = 48
_REPORTS_KEPT = 300
_DONE = ("completed", "failed", "blocked")


def _zone_scope(seed: dict, node: dict, repo: Path) -> tuple[list[str], list[str], list[str]]:
    """(editable source files, test folders, existing tests to verify) for a node and its leaves.

    Only files present in THIS checkout: a zone whose code lives on another
    branch cannot be worked on until that branch is integrated.
    """
    group = [node] + [n for n in seed["nodes"] if n.get("parent") == node["id"]]
    files: list[str] = []
    for n in group:
        for src in n.get("sources") or []:
            rel = str(src.get("path") or "")
            if rel and rel not in files and (repo / rel).is_file():
                files.append(rel)
    files = files[:_ZONE_FILES_MAX]
    test_dirs: list[str] = []
    for rel in files:
        top = rel.split("/")[0]
        folder = next((c for c in (f"{top}/tests", "tests") if (repo / c).is_dir()), None)
        if folder and folder not in test_dirs:
            test_dirs.append(folder)
    verify: list[str] = []
    for rel in files:
        stem = Path(rel).stem
        for folder in test_dirs:
            for hit in sorted((repo / folder).glob(f"test_{stem}*.py"))[:2]:
                path = hit.relative_to(repo).as_posix()
                if path not in verify:
                    verify.append(path)
    return files, test_dirs, verify[:8]


def _zone_instruction(node: dict, owner_text: str) -> str:
    text = (f"Зона дерева развития Bossman: «{node.get('label')}» ({node['id']}).\n"
            f"Описание зоны: {node.get('detail') or '—'}\n"
            f"Следующий шаг по карте: {node.get('next_action') or '—'}\n\n"
            "Задача: изучи файлы зоны, найди ОДИН реальный ограниченный дефект или недоработку, "
            "воспроизведи его тестом, исправь минимально и запусти проверку. Правь только разрешённые файлы. "
            "Если дефекта нет — так и напиши и ничего не меняй. Наличие кода не равно проверенной работе.")
    if owner_text.strip():
        text += "\n\nПожелание владельца: " + owner_text.strip()
    return text


def _work_state(svc) -> tuple[Path, dict]:
    path = _tree_dir(svc) / "zone-work.json"
    state = _read(path, {})
    state.setdefault("jobs", [])
    state.setdefault("reports", [])
    state.setdefault("seq", 0)
    return path, state


def _report(state: dict, job: dict, status: str, text: str) -> None:
    state["seq"] += 1
    state["reports"].append({"seq": state["seq"], "at": _now(), "node_id": job["node_id"], "label": job["label"],
                             "task_id": job["task_id"], "status": status, "text": text})
    state["reports"] = state["reports"][-_REPORTS_KEPT:]


def _task_summary(job: dict, rec: dict) -> str:
    status = rec.get("status")
    head = f"Зона «{job['label']}», задача {job['task_id']}: "
    if status == "running":
        return head + "Bossman работает в изолированной копии."
    if status == "completed":
        changed = rec.get("changed_files") or []
        ver = rec.get("verification")
        check = ("независимая проверка Bossman: " + ("пройдена" if ver.get("passed") else "НЕ пройдена")
                 if isinstance(ver, dict) else "независимой проверки не было (тесты зоны не найдены)")
        if not changed:
            return head + "готово без изменений — дефект не найден. " + check + "."
        return (head + f"кандидат готов, изменено файлов: {len(changed)} ({', '.join(changed[:4])}). {check}. "
                "В проект попадёт только после вашего подтверждения (Coding → Применить).")
    return head + f"{status}: {str(rec.get('error') or '')[:400]}"


@router.post("/work")
async def start_zone_work(body: WorkBody, request: Request):
    """Owner picked a zone: start ONE existing coding task scoped to that zone's files."""
    from . import coding_tasks  # local import: the coding feature owns tasks, sandbox and apply gates
    svc = request.app.state.svc
    seed = _seed()
    node = next((n for n in seed["nodes"] if n["id"] == body.node_id), None)
    if node is None:
        raise HTTPException(404, {"code": "CAPABILITY_NODE_NOT_FOUND"})
    repo = await _scan_source(svc, body.source_repo)
    files, test_dirs, verify = _zone_scope(seed, node, repo)
    if not files:
        raise HTTPException(422, {"code": "CAPABILITY_ZONE_HAS_NO_SOURCES",
                                  "message": "в этой сборке нет файлов зоны: она в другой ветке или это только идея"})
    _, state = _work_state(svc)
    if any(j["node_id"] == node["id"] and j["status"] not in _DONE for j in state["jobs"]):
        raise HTTPException(409, {"code": "CAPABILITY_ZONE_ALREADY_RUNNING"})
    task = await coding_tasks.create_task(coding_tasks.TaskIn(
        instruction=_zone_instruction(node, body.instruction), source_repo=str(repo),
        allowed_paths=files + test_dirs, verify_tests=verify, project_id="capability-tree"), request)
    job = {"node_id": node["id"], "label": node.get("label") or node["id"], "task_id": task["id"],
           "status": task.get("status") or "running", "created_at": _now(), "files": files, "verify_tests": verify}
    with _WORK_LOCK:
        path, state = _work_state(svc)
        state["jobs"] = [j for j in state["jobs"] if j["node_id"] != node["id"]] + [job]
        _report(state, job, "started", f"Зона «{job['label']}»: Bossman начал работу (задача {job['task_id']}, "
                f"файлов в области: {len(files)}, тестов для проверки: {len(verify)}).")
        _atomic(path, state)
    await svc.bus.emit("capability_tree.zone_work_started", node_id=node["id"], task_id=task["id"])
    return {"job": job, "task": task}


@router.get("/reports")
async def zone_reports(request: Request, after: int = 0):
    """Zone work reports with seq > after; the owner's Telegram пульт relays them."""
    _, state = _work_state(request.app.state.svc)
    rows = [r for r in state["reports"] if r["seq"] > after][:20]
    return {"items": rows, "last_seq": state["seq"]}


def _sync_zone_work(svc) -> None:
    from . import coding_tasks
    with _WORK_LOCK:
        path, state = _work_state(svc)
        changed = False
        for job in state["jobs"]:
            if job["status"] in _DONE:
                continue
            try:
                rec = coding_tasks._read(svc, job["task_id"])
            except HTTPException:
                rec = {"status": "failed", "error": "запись задачи не найдена"}
            status = rec.get("status") or "running"
            if status != job["status"]:
                job["status"] = status
                job["changed_files"] = list(rec.get("changed_files") or [])
                _report(state, job, status, _task_summary(job, rec))
                changed = True
        if changed:
            _atomic(path, state)


async def tick(svc) -> None:
    """Durably mirror only changed campaign activity and zone-work status; no mutation of the seed map."""
    try:
        value = _activity(svc)
        path = _tree_dir(svc) / "activity-latest.json"
        if _stable_activity(value) != _stable_activity(_read(path, {})):
            _atomic(path, value)
    except Exception:
        pass
    try:
        await asyncio.to_thread(_sync_zone_work, svc)
    except Exception:
        return


FEATURE = Feature(name="capability_tree", router=router, tick=tick, tick_seconds=5.0)
