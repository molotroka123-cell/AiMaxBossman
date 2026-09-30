"""HandBroker: the ONLY privileged executor path of the autonomy loop.

Claude / Codex / Jev / Jeff send a structured ``HandRequest``; the broker

1. hashes the exact request and journals it,
2. asks ``Policy.check`` (constitution, level, tier, scope) and, for writing
   actions, requires the engineering lease to be held for the same goal,
3. runs it through the injected executor (default: argv-only subprocess
   confined to the worktree, own process group, hard timeout, env without
   secrets; never a shell string),
4. returns a ``HandResult`` with sha256 of every artifact (stdout, stderr,
   report files) and journals it. Missing expected evidence makes ``ok`` false.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import schemas
from .journal import Journal, canonical, utc_now
from .lease import kill_process_group
from .policy import ACTIONS, Decision, Policy, PolicyRefusal, Scope
from .types import Goal, HandRequest, HandResult

MAX_CAPTURE = 4 * 1024 * 1024
EXCERPT = 2000
WRITING_KINDS = frozenset({"write", "exec", "candidate", "stage", "release", "rollback"})
LEASE_EXEMPT = frozenset({"run_tests"})      # read-only test runs in the isolated worktree


@dataclass
class ExecOutcome:
    exit_code: int | None
    stdout: bytes = b""
    stderr: bytes = b""
    files: dict[str, Path] = field(default_factory=dict)
    timed_out: bool = False
    error: str = ""


Executor = Callable[[HandRequest, Scope], ExecOutcome]


def request_hash(req: HandRequest) -> str:
    return hashlib.sha256(canonical(schemas.to_json(req))).hexdigest()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _under(p: Path, roots) -> bool:
    for root in roots:
        try:
            p.resolve().relative_to(Path(root).resolve())
            return True
        except ValueError:
            continue
    return False


def _child_env() -> dict[str, str]:
    from ..rave.connectors import child_env
    return child_env()


class SubprocessExecutor:
    """Default executor. argv lists only, cwd confined to the worktree."""

    def __init__(self, *, env_factory: Callable[[], dict[str, str]] = _child_env, max_capture: int = MAX_CAPTURE):
        self._env = env_factory
        self._max = max_capture

    @staticmethod
    def _inside(scope: Scope, raw: Any) -> Path | None:
        if not isinstance(raw, str) or not raw:
            return None
        root = scope.worktree.resolve()
        p = Path(raw)
        p = (p if p.is_absolute() else root / p).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            return None
        return p

    def __call__(self, req: HandRequest, scope: Scope) -> ExecOutcome:
        args = req.arguments if isinstance(req.arguments, dict) else {}
        if req.action in ("read_file", "read_logs"):
            p = self._inside(scope, args.get("path", req.target))
            if p is None or not p.is_file():
                return ExecOutcome(1, error="file not found inside the worktree")
            return ExecOutcome(0, stdout=p.read_bytes()[: self._max], files={"file": p})
        if req.action == "list_files":
            p = self._inside(scope, args.get("path", req.target))
            if p is None or not p.is_dir():
                return ExecOutcome(1, error="directory not found inside the worktree")
            root = scope.worktree.resolve()
            names = sorted(x.relative_to(root).as_posix() for x in p.iterdir())
            return ExecOutcome(0, stdout="\n".join(names).encode("utf-8"))
        if req.action == "write_file":
            p = self._inside(scope, args.get("path", req.target))
            content = args.get("content")
            if p is None or not isinstance(content, str):
                return ExecOutcome(1, error="write_file needs a worktree path and string content")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content.encode("utf-8"))
            return ExecOutcome(0, files={"written": p})
        if req.action in ("run_tests", "run_command", "git_commit"):
            return self._run(req, scope, args)
        return ExecOutcome(None, error=f"the default executor does not perform {req.action}")

    def _run(self, req: HandRequest, scope: Scope, args: dict) -> ExecOutcome:
        argv = args.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            return ExecOutcome(None, error="argv must be a list of strings")
        cwd = self._inside(scope, args.get("cwd", "."))
        if cwd is None or not cwd.is_dir():
            return ExecOutcome(None, error="cwd is outside the worktree")
        exe = shutil.which(argv[0]) or argv[0]
        env = self._env()
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")      # a test run leaves the candidate tree clean
        extra_path = args.get("pythonpath") or []
        if extra_path:
            dirs = [self._inside(scope, d) for d in extra_path] if isinstance(extra_path, list) else [None]
            if any(d is None for d in dirs):
                return ExecOutcome(None, error="pythonpath entries must be inside the worktree")
            env["PYTHONPATH"] = os.pathsep.join(str(d) for d in dirs)
        kw: dict[str, Any] = {}
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        try:
            proc = subprocess.Popen([exe, *argv[1:]], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, **kw)
        except OSError as exc:
            return ExecOutcome(None, error=f"cannot start: {type(exc).__name__}")
        try:
            out, err = proc.communicate(timeout=req.timeout_s)
            timed_out = False
        except subprocess.TimeoutExpired:
            kill_process_group(proc.pid)
            out, err = proc.communicate()
            timed_out = True
        files = {}
        report = args.get("report_path")
        if isinstance(report, str):
            rp = self._inside(scope, report if Path(report).is_absolute() else str(Path(cwd, report)))
            if rp is None and Path(report).is_absolute() and _under(Path(report), scope.output_roots):
                rp = Path(report).resolve()
            if rp is not None and rp.is_file():
                files["report_path"] = rp
        return ExecOutcome(None if timed_out else proc.returncode, out[: self._max], err[: self._max], files,
                           timed_out)


class HandBroker:
    def __init__(self, policy: Policy, journal: Journal, executor: Executor | None = None, *,
                 goals: Any = None, level: str | None = None, lease: Any = None,
                 clock: Callable[[], str] = utc_now):
        self.policy = policy
        self.journal = journal
        self.executor: Executor = executor or SubprocessExecutor()
        self._goals = goals
        self.level = level
        self.lease = lease
        self._clock = clock

    def _goal(self, goal_id: str) -> Goal | None:
        g = self._goals
        if g is None:
            return None
        try:
            if isinstance(g, Goal):
                return g if g.goal_id == goal_id else None
            if hasattr(g, "goal"):
                return g.goal(goal_id)
            return g(goal_id)
        except (KeyError, ValueError, LookupError):
            return None

    def _refuse(self, req: HandRequest, rh: str, started: str, reason: str, needs_user: bool) -> HandResult:
        self.journal.append("hand.refused", {"goal_id": req.goal_id, "request_hash": rh, "action": req.action,
                                             "reason": reason, "needs_user": needs_user})
        return HandResult(request_hash=rh, ok=False, exit_code=None, started_at=started, finished_at=self._clock(),
                          artifacts={}, refused_reason=reason)

    def execute(self, req: HandRequest) -> HandResult:
        started = self._clock()
        try:
            rh = request_hash(req)
        except (TypeError, ValueError):
            rh = hashlib.sha256(repr(req).encode("utf-8", "replace")).hexdigest()
        self.journal.append("hand.request", {"goal_id": getattr(req, "goal_id", ""), "request_hash": rh,
                                             "request": schemas.to_json(req)})
        if not isinstance(req, HandRequest):
            return self._refuse(req, rh, started, "not a HandRequest", False)
        original = req
        try:
            req = self.policy.prepare(req)
        except PolicyRefusal as exc:
            return self._refuse(original, rh, started, exc.reason, exc.needs_user)
        decision: Decision = self.policy.check(req, self._goal(req.goal_id), self.level)
        if not decision.allowed:
            return self._refuse(original, rh, started, decision.reason, decision.needs_user)
        rule = ACTIONS.get(req.action)
        if self.lease is not None and rule is not None and rule.kind in WRITING_KINDS                 and req.action not in LEASE_EXEMPT:
            cur = self.lease.current()
            if cur is None or cur.goal_id != req.goal_id:
                return self._refuse(original, rh, started, "the engineering lease is not held for this goal", False)
        if req is not original:
            self.journal.append("hand.prepared", {"goal_id": req.goal_id, "request_hash": rh,
                                                  "arguments": schemas.to_json(req.arguments)})
        try:
            out = self.executor(req, self.policy.scope_for(req))
        except Exception as exc:  # noqa: BLE001 - an executor failure is evidence, not a crash of the loop
            out = ExecOutcome(None, error=f"executor error: {type(exc).__name__}: {exc}")
        artifacts = {"stdout": _sha(out.stdout), "stderr": _sha(out.stderr)}
        for name, path in sorted(out.files.items()):
            try:
                artifacts[name] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            except OSError:
                pass
        have = set(artifacts) | ({"exit_code"} if out.exit_code is not None else set())
        have |= {"stdout_hash"} if "stdout" in artifacts else set()
        missing = sorted(set(req.expected_evidence) - have)
        ok = out.exit_code == 0 and not out.timed_out and not out.error and not missing
        reason = out.error or ("timed out" if out.timed_out else "") or \
            (f"missing evidence: {missing}" if missing else "")
        result = HandResult(request_hash=rh, ok=ok, exit_code=out.exit_code, started_at=started,
                            finished_at=self._clock(), artifacts=artifacts, refused_reason=reason)
        schemas.validate("result", schemas.to_json(result))
        self.journal.append("hand.result", {
            "goal_id": req.goal_id, "request_hash": rh, "ok": ok, "exit_code": out.exit_code,
            "timed_out": out.timed_out, "artifacts": artifacts, "missing_evidence": missing, "reason": reason,
            "stdout_tail": out.stdout[-EXCERPT:].decode("utf-8", "replace"),
            "stderr_tail": out.stderr[-EXCERPT:].decode("utf-8", "replace")})
        return result


# ------------------------------------------------------------------ default wiring for the cycle

#: Safety suite every candidate must keep green (paths relative to command-center/).
PROTECTED_SUITE = ("tests/test_autonomy_policy.py", "tests/test_autonomy_hands.py", "tests/test_autonomy_goals.py",
                   "tests/test_autonomy_constitution.py", "tests/test_autonomy_journal.py",
                   "tests/test_autonomy_types.py")
MAX_LEVEL = "L2"                 # constitution: the system starts at L2 at most


def _pytest_ids(tests: Any) -> list[str]:
    out = []
    for t in tests or ():
        t = str(t).strip()
        if not t.startswith("pytest:"):
            continue
        node = t[len("pytest:"):].strip().replace("\\", "/")
        if node.startswith("command-center/"):
            node = node[len("command-center/"):]
        path = node.split("::")[0]
        if node and not path.startswith(("/", "..")) and "/../" not in path and ":" not in path:
            out.append(node)
    return out


class RoutedPolicy(Policy):
    """Policy whose worktree is the request's own isolated worktree (``arguments.worktree``).

    The worktree must be a git checkout under one of ``worktree_roots`` (the cycle's
    work dir); its HEAD must equal ``arguments.sha`` when given. ``run_tests`` with a
    ``suite`` and no argv becomes ``python -m pytest`` on the goal's ``pytest:``
    acceptance tests (``acceptance``) or on the safety suite (``protected``), with a
    JUnit report written outside the worktree.
    """

    def __init__(self, worktree_roots: Any, reports_root: Path, *, constitution_status: Any = None,
                 level: str = MAX_LEVEL, python: str | None = None, head_of: Callable[[Path], str] | None = None,
                 **scope_kw: Any):
        import sys
        self.worktree_roots = tuple(Path(r) for r in worktree_roots)
        self.reports_root = Path(reports_root)
        self.python = python or sys.executable
        self._head_of = head_of
        self._scope_kw = scope_kw
        super().__init__(Scope(worktree=self.reports_root, output_roots=(self.reports_root,), **scope_kw),
                         constitution_status=constitution_status, level=level)

    def _worktree(self, req: HandRequest) -> Path:
        raw = req.arguments.get("worktree") if isinstance(req.arguments, dict) else None
        if not isinstance(raw, str) or not raw:
            raise PolicyRefusal("request names no isolated worktree (arguments.worktree)")
        wt = Path(raw).resolve()
        if not _under(wt, self.worktree_roots) or wt in {r.resolve() for r in self.worktree_roots}:
            raise PolicyRefusal("worktree is not an isolated cycle worktree")
        if not (wt / ".git").exists():
            raise PolicyRefusal("worktree is not a git checkout")
        return wt

    def scope_for(self, req: HandRequest) -> Scope:
        return Scope(worktree=self._worktree(req), output_roots=(self.reports_root,), **self._scope_kw)

    def check(self, req: HandRequest, goal: Goal | None, level: str | None = None) -> Decision:
        try:
            scope = self.scope_for(req)
        except PolicyRefusal as exc:
            return Decision(False, exc.reason, exc.needs_user)
        inner = Policy(scope, constitution_status=self._constitution, level=self.level, clock=self._clock)
        inner._started = self._started
        return inner.check(req, goal, level)

    def _head(self, wt: Path) -> str:
        if self._head_of is not None:
            return self._head_of(wt)
        from ..rave import workspace as rws
        return rws.head_commit(wt)

    def prepare(self, req: HandRequest) -> HandRequest:
        args = dict(req.arguments) if isinstance(req.arguments, dict) else {}
        wt = self._worktree(req)
        sha = args.get("sha")
        if isinstance(sha, str) and sha:
            try:
                head = self._head(wt)
            except Exception:  # noqa: BLE001
                raise PolicyRefusal("cannot read the worktree HEAD") from None
            if head != sha:
                raise PolicyRefusal("candidate SHA changed (worktree HEAD differs from the request)")
        if req.action == "run_tests" and "argv" not in args:
            suite = args.get("suite")
            if suite == "acceptance":
                ids = _pytest_ids(args.get("acceptance_tests"))
            elif suite == "protected":
                ids = list(PROTECTED_SUITE)
            else:
                raise PolicyRefusal(f"unknown test suite {suite!r}")
            if not ids:
                raise PolicyRefusal("no executable pytest: acceptance tests")
            cc = wt / "command-center"
            base = cc if cc.is_dir() else wt
            missing = [i for i in ids if not (base / i.split("::")[0]).exists()]
            if missing:
                raise PolicyRefusal(f"test files missing in the candidate: {missing}")
            gid = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in req.goal_id)
            report = (self.reports_root / gid / f"{suite}-{(sha or 'head')[:12]}.xml").resolve()
            report.parent.mkdir(parents=True, exist_ok=True)
            report.unlink(missing_ok=True)
            core = wt / "bossman-core"
            args.update(argv=[self.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}",
                              *ids],
                        cwd=str(base), report_path=str(report),
                        pythonpath=[str(base)] + ([str(core)] if core.is_dir() else []))
        fields = {f: getattr(req, f) for f in req.__dataclass_fields__}
        return HandRequest(**{**fields, "arguments": args})


def build_default_broker(root: str | os.PathLike, journal: Journal, *, level: str = MAX_LEVEL,
                         constitution_status: Any = None, executor: Executor | None = None) -> HandBroker:
    """The real hands for the cycle: routed policy (level capped at L2), default argv
    executor, goals from ``GoalStore(root)``, engineering lease ``EngineeringLease(root)``."""
    from .goals import GoalStore
    from .lease import EngineeringLease
    from .policy import LEVELS

    if level not in LEVELS or LEVELS.index(level) > LEVELS.index(MAX_LEVEL):
        level = MAX_LEVEL
    root = Path(root)
    # the goal budget (GoalStore) bounds the cycle; the broker only bounds each action by its timeout
    policy = RoutedPolicy([root / "cycles"], root / "reports", constitution_status=constitution_status, level=level,
                          time_budget_s=10 * 24 * 3600.0)
    return HandBroker(policy, journal, executor or SubprocessExecutor(), goals=GoalStore(root, journal),
                      level=level, lease=EngineeringLease(root, journal=journal))


__all__ = ["ExecOutcome", "Executor", "HandBroker", "MAX_LEVEL", "PROTECTED_SUITE", "RoutedPolicy",
           "SubprocessExecutor", "build_default_broker", "request_hash"]
