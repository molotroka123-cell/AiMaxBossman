"""ATTEMPT backends of the evolution loop. Each returns a diff; none decides.

  bossman_coding  the PRODUCT coding path: a coding task through the Command
                  Center API (POST /api/coding-tasks) with a lab agent profile ->
                  IsolatedWorktree -> local sidecar with real tools. The student
                  explores, reproduces, edits and tests by itself; the loop only
                  sends the task and reads the host-derived diff back.
  mock_patch      DETERMINISTIC TEST MODEL without a server: scripted edits applied
                  to a fresh clone. Always MOCK_MODEL; plumbing, never skill.
  local / claude  Aster's JSON proposers (runner.LocalProposer / ClaudeProposer):
                  exact replacements in editable files only, verified in Docker.

The student's own summary is kept as an UNTRUSTED claim next to the evidence;
the verifier never receives it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

from . import verifier as v

MOCK_MARKER = v.MOCK_MARKER


@dataclass
class AttemptContext:
    cycle_id: str
    task: dict
    base_sha: str
    source: Path                   # the candidate repository (bare) to clone from
    folder: Path                   # this attempt's evidence folder
    deadline: float                # time.monotonic() deadline
    instruction: str
    observation: dict = field(default_factory=dict)
    should_abort: Callable[[], str] = lambda: ""       # "", "STOP", "TIMEOUT", "MEMORY_BUDGET", ...
    on_submitted: Callable[[dict], None] = lambda info: None
    register_tree: Callable[[Any], None] = lambda tree: None


@dataclass
class AttemptResult:
    status: str                    # COMPLETED | FAILED | TIMEOUT | STOPPED | BLOCKED
    diff: str = ""
    changed_files: list = field(default_factory=list)
    model: str = ""
    model_kind: str = "UNKNOWN"    # MOCK_MODEL | REAL_MODEL | UNKNOWN
    cost_usd: float | None = None
    tokens: int | None = None
    student_claim: str = ""        # untrusted; never a verifier input
    detail: dict = field(default_factory=dict)
    reason: str = ""


def kind_of(model: str, *, declared: str = "") -> str:
    if MOCK_MARKER in (model or "") or declared == "MOCK_MODEL":
        return "MOCK_MODEL"
    return "REAL_MODEL" if declared == "REAL_MODEL" or model else "UNKNOWN"


def student_clone(ctx: AttemptContext, name: str = "student-repo") -> Path:
    """A clean clone of the cycle base on a named branch, without a remote."""
    dest = ctx.folder / name
    v.clean_checkout(ctx.source, ctx.base_sha, dest, home=ctx.folder / "git-home")
    v.git(dest, "checkout", "-q", "-b", "evo-student", home=ctx.folder / "git-home")
    return dest


def derive_diff(repo: Path, home: Path) -> tuple[str, list[str]]:
    v.git(repo, "add", "-A", home=home)
    diff = v.git(repo, "diff", "--cached", "--binary", home=home)[1]
    names = [n for n in v.git(repo, "diff", "--cached", "--name-only", home=home)[1].splitlines() if n]
    return diff, names


# ---------------------------------------------------------------- mock_patch
class MockPatchBackend:
    """Scripted edits per task and attempt number (1-based), e.g.
    {"model": "DETERMINISTIC-TEST-MODEL-mock", "tasks": {"money": [{"edits": [...],
      "writes": {...}, "deletes": [...], "sleep_seconds": 0, "claim": "..."}]}}"""

    name = "mock_patch"

    def __init__(self, script: dict):
        self.script = script
        model = str(script.get("model") or f"{MOCK_MARKER}-mock-patch")
        self.model = model if MOCK_MARKER in model else f"{MOCK_MARKER}-{model}"

    def prepare(self, loop) -> dict:
        return {"model": self.model, "model_kind": "MOCK_MODEL"}

    def attempt(self, ctx: AttemptContext) -> AttemptResult:
        runs = list((self.script.get("tasks") or {}).get(ctx.task["id"]) or [])
        number = int(ctx.observation.get("attempt_number") or 1)
        step = runs[min(number, len(runs)) - 1] if runs else {}
        ctx.on_submitted({"backend": self.name, "script_step": number})
        repo = student_clone(ctx)
        sleep = float(step.get("sleep_seconds") or 0)
        until = time.monotonic() + sleep
        while time.monotonic() < until:                     # bounded: ends at `until`
            why = ctx.should_abort()
            if why:
                return AttemptResult("STOPPED" if why == "STOP" else "TIMEOUT", model=self.model,
                                     model_kind="MOCK_MODEL", reason=why)
            time.sleep(min(0.2, max(0.0, until - time.monotonic())))
        why = ctx.should_abort()
        if why:
            return AttemptResult("STOPPED" if why == "STOP" else "TIMEOUT", model=self.model,
                                 model_kind="MOCK_MODEL", reason=why)
        for e in step.get("edits") or []:
            path = repo / e["path"]
            text = path.read_text(encoding="utf-8")
            if text.count(e["old"]) != 1:
                return AttemptResult("FAILED", model=self.model, model_kind="MOCK_MODEL",
                                     reason=f"scripted edit does not match once in {e['path']}")
            path.write_bytes(text.replace(e["old"], e["new"]).encode("utf-8"))
        for rel, body in (step.get("writes") or {}).items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_bytes(str(body).encode("utf-8"))
        for rel in step.get("deletes") or []:
            (repo / rel).unlink()
        diff, names = derive_diff(repo, ctx.folder / "git-home")
        return AttemptResult("COMPLETED" if diff else "FAILED", diff=diff, changed_files=names, model=self.model,
                             model_kind="MOCK_MODEL", student_claim=str(step.get("claim") or ""),
                             cost_usd=0.0, detail={"script_step": number})


# ---------------------------------------------------------------- JSON proposers
class JsonProposerBackend:
    """Aster's proposer contract (prompt, cwd, budget, timeout) -> (json, cost)."""

    def __init__(self, name: str, proposer, model: str, budget_usd: float):
        self.name, self.proposer, self.model, self.budget_usd = name, proposer, model, budget_usd

    def prepare(self, loop) -> dict:
        return {"model": self.model, "model_kind": kind_of(self.model, declared="REAL_MODEL")}

    def attempt(self, ctx: AttemptContext) -> AttemptResult:
        from . import runner as r  # noqa: PLC0415
        repo = student_clone(ctx)
        memory = r.LearningStore(ctx.folder / "no-memory")        # past attempts come via the instruction
        baseline = (ctx.observation.get("baseline") or {}).get(ctx.task["id"]) or {"status": "FAIL"}
        prompt = r.prompt_for(repo, ctx.task, baseline, memory) + "\n" + ctx.instruction
        cwd = ctx.folder / "provider"
        cwd.mkdir(parents=True, exist_ok=True)
        remaining = int(max(1, ctx.deadline - time.monotonic()))
        ctx.on_submitted({"backend": self.name})
        try:
            proposal, cost = self.proposer(prompt, cwd, self.budget_usd, remaining)
            paths = r.apply_edits(repo, proposal, ctx.task["editable"])
        except (ValueError, RuntimeError, OSError, KeyError, TypeError, SyntaxError,
                subprocess.TimeoutExpired) as exc:
            status = "TIMEOUT" if isinstance(exc, subprocess.TimeoutExpired) else "FAILED"
            return AttemptResult(status, model=self.model, model_kind=kind_of(self.model, declared="REAL_MODEL"),
                                 reason=v.redact_text(str(exc))[:500])
        diff, names = derive_diff(repo, ctx.folder / "git-home")
        return AttemptResult("COMPLETED", diff=diff, changed_files=names or paths, model=self.model,
                             model_kind=kind_of(self.model, declared="REAL_MODEL"), cost_usd=cost,
                             student_claim=str(proposal.get("summary") or "")[:1000])


# ---------------------------------------------------------------- the product path
class ApiError(RuntimeError):
    """The Command Center API is unreachable or refused the call."""


class CommandCenterApi:
    """Loopback-only client of the Command Center (X-BCC-Token; never a proxy)."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "localhost", "::1") \
                or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Command Center API must be a loopback http(s) URL")
        if not token:
            raise ValueError("Command Center token missing (BCC_TOKEN, --token-file or --data-dir)")
        self.base, self.token, self.timeout = base_url.rstrip("/"), token, timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method: str, path: str, payload: Any = None, *, timeout: float | None = None) -> tuple[int, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("X-BCC-Token", self.token)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                status, raw = resp.status, resp.read(8_000_001)
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read(200_000)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ApiError(f"Command Center unreachable at {self.base}: {type(exc).__name__}") from exc
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            body = {"raw": raw[:300].decode("utf-8", "replace")}
        if status == 401:
            raise ApiError("Command Center refused the token (401)")
        return status, body

    def get(self, path: str) -> tuple[int, Any]:
        return self.request("GET", path)

    def post(self, path: str, payload: Any = None) -> tuple[int, Any]:
        return self.request("POST", path, payload if payload is not None else {})


def resolve_token(token: str = "", token_file: str = "", data_dir: str = "") -> str:
    if token:
        return token.strip()
    if os.environ.get("BCC_TOKEN"):
        return os.environ["BCC_TOKEN"].strip()
    candidates = [Path(token_file)] if token_file else []
    if data_dir:
        candidates.append(Path(data_dir) / "token")
    for path in candidates:
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


TERMINAL = ("completed", "failed", "blocked")
SIDECAR_KEYS = ("status", "executor", "model", "model_kind", "deterministic_test_model", "stop_reason", "steps",
                "recipes_applied", "memory_used", "skills_used", "profile", "tests")


class BossmanCodingBackend:
    """One coding task per ATTEMPT through the product API; the loop never edits."""

    name = "bossman_coding"

    def __init__(self, api: CommandCenterApi, *, project_id: str = "bossman-evolution", use_memory: bool = True,
                 model: str | None = None, poll_seconds: float = 5.0, add_root: bool = False):
        self.api, self.project_id, self.use_memory, self.model = api, project_id, use_memory, model
        self.poll_seconds, self.add_root = max(0.2, float(poll_seconds)), add_root
        self.readiness: dict = {}
        self.agent: dict | None = None

    def prepare(self, loop) -> dict:
        status, ready = self.api.get("/api/coding-tasks/readiness")
        if status != 200 or not isinstance(ready, dict):
            raise ApiError(f"coding tasks readiness answered {status}")
        if not ready.get("available"):
            raise ApiError("coding path not ready: " + str(ready.get("reason") or "")[:300])
        students = loop.students_root()
        roots = [Path(r) for r in ready.get("roots") or []]
        if not any(_within(students, r) for r in roots):
            if not self.add_root:
                raise ApiError(f"the campaign folder {students} is outside the allowed code roots; add it in "
                               "Settings -> code roots or pass --add-root (an explicit owner decision)")
            status, body = self.api.post("/api/terminal/roots", {"roots": [str(r) for r in roots] + [str(students)]})
            if status != 200:
                raise ApiError(f"adding the campaign root failed ({status})")
        status, body = self.api.post("/api/lab-agents/ensure", {})
        variant = "MEMORY" if self.use_memory else "TOOL_FIRST"
        if status == 200 and isinstance(body, dict):
            self.agent = next(({"id": a.get("id"), "name": a.get("name"), "variant": variant}
                               for a in body.get("agents") or [] if a.get("variant") == variant), None)
        hs = ready.get("handshake") or {}
        self.readiness = {"model": hs.get("model"), "model_kind": hs.get("model_kind") or kind_of(str(hs.get("model"))),
                          "executor": hs.get("executor"), "isolation": hs.get("isolation"),
                          "agent": self.agent, "agent_warning": None if self.agent else
                          f"lab agent {variant} unavailable (POST /api/lab-agents/ensure -> {status})"}
        return dict(self.readiness)

    def body(self, ctx: AttemptContext, repo: Path) -> dict:
        task = ctx.task
        allowed = list(dict.fromkeys(list(task.get("editable") or []) + list(v.new_test_prefixes(task))))
        timeout = int(min(7200, max(30, ctx.deadline - time.monotonic() - 30)))
        return {"instruction": ctx.instruction, "source_repo": str(repo), "allowed_paths": allowed,
                "protected_paths": ["conftest.py", "tests/conftest.py", "pytest.ini", "setup.cfg", "tox.ini",
                                    "pyproject.toml", "config/evolution", ".github"],
                "model": self.model, "timeout_seconds": timeout,
                "agent_id": (self.agent or {}).get("id"), "project_id": self.project_id,
                "use_memory": bool(self.use_memory), "verify_tests": list(task.get("tests") or [])}

    def attempt(self, ctx: AttemptContext) -> AttemptResult:
        repo = student_clone(ctx)
        status, rec = self.api.post("/api/coding-tasks", self.body(ctx, repo))
        if status != 200 or not isinstance(rec, dict) or not rec.get("id"):
            return AttemptResult("BLOCKED", model=str(self.readiness.get("model") or ""),
                                 model_kind=str(self.readiness.get("model_kind") or "UNKNOWN"),
                                 reason=f"coding task refused ({status}): {json.dumps(rec, ensure_ascii=False)[:300]}")
        task_id = str(rec["id"])
        ctx.on_submitted({"backend": self.name, "coding_task_id": task_id, "agent": self.agent})
        record: dict = rec
        abort = ""
        polls = 0
        max_polls = int((ctx.deadline - time.monotonic()) / self.poll_seconds) + 60
        for polls in range(1, max(2, max_polls)):                 # bounded by the attempt deadline
            abort = ctx.should_abort()
            if abort:
                break
            try:
                code, body = self.api.get(f"/api/coding-tasks/{task_id}")
            except ApiError:
                code, body = 0, None
            if code == 200 and isinstance(body, dict):
                record = body
                if record.get("status") in TERMINAL:
                    break
            time.sleep(self.poll_seconds)
        else:
            abort = "TIMEOUT"
        if abort:
            try:
                self.api.post(f"/api/coding-tasks/{task_id}/cancel", {})
            except ApiError:
                pass
            record = self._await_terminal(task_id, record, 60.0)
        return self._result(record, abort, polls)

    def _await_terminal(self, task_id: str, record: dict, seconds: float) -> dict:
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            try:
                code, body = self.api.get(f"/api/coding-tasks/{task_id}")
            except ApiError:
                break
            if code == 200 and isinstance(body, dict):
                record = body
                if body.get("status") in TERMINAL:
                    break
            time.sleep(min(1.0, self.poll_seconds))
        return record

    def cancel(self, task_id: str) -> dict:
        """Recovery helper: cancel a task left running by a crashed loop."""
        try:
            code, body = self.api.get(f"/api/coding-tasks/{task_id}")
            out = {"server_status": (body or {}).get("status") if code == 200 else f"http {code}"}
            if code == 200 and (body or {}).get("status") not in TERMINAL:
                self.api.post(f"/api/coding-tasks/{task_id}/cancel", {})
                out["cancel_requested"] = True
            return out
        except ApiError as exc:
            return {"server_status": "unreachable", "error": str(exc)[:200]}

    def _result(self, record: dict, abort: str, polls: int) -> AttemptResult:
        sidecar = {k: (record.get("sidecar") or {}).get(k) for k in SIDECAR_KEYS}
        model = str(sidecar.get("model") or self.readiness.get("model") or self.model or "")
        declared = str(sidecar.get("model_kind") or ("MOCK_MODEL" if sidecar.get("deterministic_test_model")
                                                     else self.readiness.get("model_kind") or ""))
        detail = {"coding_task_id": record.get("id"), "product_status": record.get("status"),
                  "outcome": record.get("outcome"), "error": str(record.get("error") or "")[:500],
                  "sidecar": sidecar, "memory": record.get("memory"), "skills": record.get("skills"),
                  "product_verification": {k: (record.get("verification") or {}).get(k)
                                           for k in ("ran", "passed", "exit_code", "runner")},
                  "agent": record.get("agent"), "duration_seconds": record.get("duration_seconds"),
                  "sandbox_cleanup": record.get("sandbox_cleanup"), "polls": polls}
        status = {"completed": "COMPLETED", "failed": "FAILED", "blocked": "BLOCKED"}.get(record.get("status"), "FAILED")
        if abort:
            status = "STOPPED" if abort == "STOP" else "TIMEOUT"
        return AttemptResult(status, diff=str(record.get("diff") or ""), changed_files=list(record.get("changed_files") or []),
                             model=model, model_kind=kind_of(model, declared=declared), cost_usd=None,
                             student_claim=str((record.get("sidecar") or {}).get("summary") or "")[:2000],
                             detail=detail, reason=abort or str(record.get("error") or "")[:300])


def _within(child: Path, root: Path) -> bool:
    try:
        child.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False
