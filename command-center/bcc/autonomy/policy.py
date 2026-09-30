"""Authority matrix: constitution rules + autonomy level + release tier + scope.

``Policy.check(req, goal, level) -> Decision(allowed, reason, needs_user)``.
Order of evaluation (first match wins, everything unknown fails closed):

1. constitution not pinned / changed        -> refused, needs the user (BLOCKED)
2. malformed request / foreign goal         -> refused
3. always-the-user actions (money, external messages, credentials,
   constitution / approval gate, destructive ops, disabling safety,
   production deploy, new earning method)   -> needs_user
4. unknown action                           -> refused (fail closed)
5. autonomy level below the action's level  -> refused
6. scope: paths inside the assigned worktree, protected gate files,
   argv allowlist, time budget, goal risk tier -> refused / needs_user
7. release tiers: only docs_tests from L3 is automatic; the rest needs the user
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from . import schemas
from .types import RISK_TIERS, Goal, HandRequest

LEVELS = ("L0", "L1", "L2", "L3", "L4")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    needs_user: bool = False


@dataclass(frozen=True)
class ActionRule:
    level: str
    kind: str          # read | write | exec | candidate | stage | release | rollback


ACTIONS: dict[str, ActionRule] = {
    "read_file": ActionRule("L0", "read"),
    "list_files": ActionRule("L0", "read"),
    "read_logs": ActionRule("L0", "read"),
    "write_file": ActionRule("L1", "write"),
    "apply_patch": ActionRule("L1", "write"),
    "run_tests": ActionRule("L1", "exec"),
    "run_command": ActionRule("L1", "exec"),
    "git_commit": ActionRule("L1", "exec"),
    "prepare_candidate": ActionRule("L2", "candidate"),
    "stage_candidate": ActionRule("L2", "stage"),
    "apply_release": ActionRule("L3", "release"),
    "rollback_release": ActionRule("L4", "rollback"),
    # names the Line B cycle uses for the same two gates
    "apply_candidate": ActionRule("L3", "release"),
    "rollback": ActionRule("L4", "rollback"),
}

#: Constitution "always the user's decision" -> why.
ALWAYS_USER: dict[str, str] = {
    "spend_money": "money", "purchase": "money", "subscribe": "money", "apply_financing": "money",
    "send_message": "external message", "publish": "external publication", "post_external": "external message",
    "send_email": "external message", "create_credential": "credentials", "disclose_credential": "credentials",
    "read_credential": "credentials", "rotate_credential": "credentials", "account_change": "credentials",
    "edit_constitution": "constitution", "pin_constitution": "constitution",
    "change_approval_gate": "approval gate", "delete_data": "destructive operation",
    "destructive_cleanup": "destructive operation", "disable_safety": "disabling safety",
    "production_deploy": "production deploy", "deploy": "production deploy", "restart_service": "production deploy",
    "start_earning_method": "new earning method", "git_push": "production release",
    "merge_protected_branch": "production release",
}

SHELLS = frozenset({"sh", "bash", "zsh", "cmd", "powershell", "pwsh", "wsl", "busybox"})
DESTRUCTIVE_BINARIES = frozenset({"rm", "rmdir", "del", "erase", "rd", "format", "mkfs", "dd", "diskpart", "shutdown",
                                  "reboot", "reg", "schtasks", "sc", "net", "taskkill", "kill", "pkill", "icacls",
                                  "takeown", "chmod", "chown"})
NETWORK_BINARIES = frozenset({"curl", "wget", "ssh", "scp", "sftp", "ftp", "telnet", "nc", "ncat", "gh"})
DEFAULT_COMMANDS = frozenset({"python", "python3", "py", "pytest", "git", "node", "npm", "ruff"})
DEFAULT_GIT = frozenset({"status", "diff", "add", "commit", "log", "show", "rev-parse", "ls-files", "branch",
                         "switch", "restore", "stash"})
DESTRUCTIVE_GIT = frozenset({"push", "reset", "clean", "rebase", "filter-branch", "gc", "prune", "update-ref",
                             "reflog", "remote", "config"})
PYTHON_MODULES = frozenset({"pytest", "ruff", "mypy", "compileall", "json.tool"})
NPM_SUBCOMMANDS = frozenset({"test", "run"})

#: Constitution / approval-gate / safety files: writing them is always the user's decision.
PROTECTED_GLOBS = ("docs/constitution/*", "command-center/bcc/autonomy/constitution.py",
                   "command-center/bcc/autonomy/policy.py", "command-center/bcc/autonomy/goals.py",
                   "command-center/bcc/autonomy/hands.py", "command-center/bcc/autonomy/journal.py",
                   "command-center/bcc/autonomy/types.py", "schemas/autonomy/*", ".github/workflows/*", ".git/*",
                   ".git")

TIER_GLOBS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("docs_tests", ("*.md", "*.rst", "docs/*", "tests/*", "*/tests/*", "test_*.py", "*/test_*.py")),
    ("memory_keys_telegram_services", ("*memory*", "*secret*", "*vault*", "*keys*", "*credential*", "*telegram*",
                                       "*service*", "*.ps1", "*scheduler*", "*.env", "*token*")),
    ("prompts_models", ("*prompt*", "*persona*", "*identity*", "*/models*", "*model_*", "*router*", "*routing*",
                        "config/*.json", "config/*.yaml", "config/*.yml")),
)


def classify_path(rel: str) -> str:
    """Release tier of one repository path: docs/tests first (they cannot change the runtime), then the
    most sensitive match; unknown code is critical_runtime."""
    p = rel.replace("\\", "/").lower()
    for tier, globs in TIER_GLOBS:
        if any(fnmatch(p, g) for g in globs):
            return tier
    return "critical_runtime"


def tier_rank(tier: str) -> int:
    return RISK_TIERS.index(tier)


def _base(argv0: str) -> str:
    name = PurePosixPath(argv0.replace("\\", "/")).name.lower()
    for ext in (".exe", ".cmd", ".bat", ".com", ".ps1"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


@dataclass(frozen=True)
class Scope:
    worktree: Path
    allowed_commands: frozenset[str] = DEFAULT_COMMANDS
    git_subcommands: frozenset[str] = DEFAULT_GIT
    time_budget_s: float = 3600.0
    started_at: float | None = None
    protected_globs: tuple[str, ...] = PROTECTED_GLOBS
    extra_protected: tuple[Path, ...] = field(default_factory=tuple)   # e.g. the pin file, owner data dir
    output_roots: tuple[Path, ...] = field(default_factory=tuple)      # argv may name files here (reports)


class PolicyRefusal(Exception):
    def __init__(self, reason: str, needs_user: bool = False):
        super().__init__(reason)
        self.reason, self.needs_user = reason, needs_user


class Policy:
    def __init__(self, scope: Scope, *, constitution_status: Callable[[], Any] | Any = None,
                 level: str = "L2", clock: Callable[[], float] = time.time):
        self.scope = scope
        self._constitution = constitution_status
        self.level = level
        self._clock = clock
        self._started = scope.started_at if scope.started_at is not None else clock()

    # .......................................................... helpers
    def _constitution_ok(self) -> tuple[bool, str]:
        st = self._constitution() if callable(self._constitution) else self._constitution
        if st is None:
            from . import constitution
            st = constitution.verify()
        return bool(getattr(st, "ok", False)), str(getattr(st, "reason", "constitution status unknown"))

    def resolve(self, raw: str) -> Path | None:
        """A path inside the worktree, or None when it escapes it."""
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            return None
        root = self.scope.worktree.resolve()
        p = Path(raw)
        p = (p if p.is_absolute() else root / p).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            return None
        return p

    def rel(self, p: Path) -> str:
        return p.relative_to(self.scope.worktree.resolve()).as_posix()

    def _protected(self, p: Path) -> bool:
        rel = self.rel(p)
        if any(rel == g or fnmatch(rel, g) for g in self.scope.protected_globs):
            return True
        for extra in self.scope.extra_protected:
            try:
                p.relative_to(Path(extra).resolve())
                return True
            except ValueError:
                continue
        return False

    def in_output_roots(self, raw: str) -> bool:
        try:
            p = Path(raw).resolve()
        except (OSError, ValueError):
            return False
        for root in self.scope.output_roots:
            try:
                p.relative_to(Path(root).resolve())
                return True
            except ValueError:
                continue
        return False

    def scope_for(self, req: HandRequest) -> Scope:
        """The scope an allowed request runs in (a routed policy picks it per request)."""
        return self.scope

    def prepare(self, req: HandRequest) -> HandRequest:
        """Normalise a request before the check (identity here). Raises PolicyRefusal."""
        return req

    def remaining_s(self) -> float:
        return self.scope.time_budget_s - (self._clock() - self._started)

    # .......................................................... argv
    def check_argv(self, argv: Any, goal: Goal) -> Decision:
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
            return Decision(False, "arguments.argv must be a non-empty list of strings (no shell strings)")
        base = _base(argv[0])
        if base in SHELLS:
            return Decision(False, f"shell interpreters are not allowed ({base}); pass argv directly")
        if base in DESTRUCTIVE_BINARIES:
            return Decision(False, f"destructive command {base} needs the user", needs_user=True)
        if base in NETWORK_BINARIES:
            return Decision(False, f"network/external command {base} needs the user", needs_user=True)
        if base not in self.scope.allowed_commands:
            return Decision(False, f"command {base} is not in the allowlist")
        args = argv[1:]
        if base == "git":
            if args and args[0].startswith("-"):
                return Decision(False, "git global options are not allowed")
            sub = args[0] if args else ""
            if sub in DESTRUCTIVE_GIT:
                return Decision(False, f"git {sub} is destructive or external: needs the user", needs_user=True)
            if sub not in self.scope.git_subcommands:
                return Decision(False, f"git {sub or '(none)'} is not allowed")
            if sub == "branch" and any(a in ("-D", "-d", "--delete", "-M", "-m", "--force", "-f") for a in args):
                return Decision(False, "deleting/renaming branches needs the user", needs_user=True)
            if sub in ("restore", "stash") and any(a in ("--staged", "--worktree", "drop", "clear") for a in args):
                return Decision(False, f"git {sub} with discard semantics needs the user", needs_user=True)
        if base in ("python", "python3", "py"):
            if args[:1] == ["-m"] and len(args) > 1:
                if args[1] not in PYTHON_MODULES:
                    return Decision(False, f"python -m {args[1]} is not allowed")
            elif args and not args[0].startswith("-"):
                script = self.resolve(args[0])
                if script is None:
                    return Decision(False, "python script outside the worktree")
            else:
                return Decision(False, "python needs -m <allowed module> or a script inside the worktree")
        if base == "npm" and (not args or args[0] not in NPM_SUBCOMMANDS):
            return Decision(False, "npm is limited to test/run")
        for a in args:
            if a.startswith("--") and "=" in a:
                a = a.split("=", 1)[1]
            looks_path = os.path.isabs(a) or a.startswith("..") or "/../" in a.replace("\\", "/") \
                or re.match(r"^[A-Za-z]:[\\/]", a) is not None
            if looks_path:
                p = self.resolve(a)
                if p is None and os.path.isabs(a) and self.in_output_roots(a):
                    continue
                if p is None:
                    return Decision(False, f"argument points outside the worktree: {a}")
                if self._protected(p):
                    return Decision(False, f"argument touches a protected file: {self.rel(p)}", needs_user=True)
        return Decision(True, "argv allowed")

    # .......................................................... main check
    def check(self, req: HandRequest, goal: Goal | None, level: str | None = None) -> Decision:
        ok, why = self._constitution_ok()
        if not ok:
            return Decision(False, f"BLOCKED: {why}", needs_user=True)
        try:
            schemas.validate("action", schemas.to_json(req))
        except schemas.SchemaError as exc:
            return Decision(False, f"malformed hand request: {exc}")
        if goal is None or goal.goal_id != req.goal_id:
            return Decision(False, "request is outside the active goal")
        level = level or self.level
        if level not in LEVELS:
            return Decision(False, f"unknown autonomy level {level!r}")
        if req.action in ALWAYS_USER:
            return Decision(False, f"always the user's decision: {ALWAYS_USER[req.action]}", needs_user=True)
        rule = ACTIONS.get(req.action)
        if rule is None:
            return Decision(False, f"unknown action {req.action!r} (fail closed)")
        if LEVELS.index(level) < LEVELS.index(rule.level):
            return Decision(False, f"{req.action} needs autonomy level {rule.level}, current {level}")
        if req.requested_by == "jeff" and rule.kind != "read":
            return Decision(False, "Jeff may only request read actions")
        if req.risk_class == "high":
            return Decision(False, "high-risk action needs the user", needs_user=True)
        if req.timeout_s > self.remaining_s():
            return Decision(False, "time budget exhausted for this scope")
        if req.timeout_s > goal.budget.max_minutes * 60:
            return Decision(False, "action timeout exceeds the goal budget")
        if rule.kind in ("write", "exec", "candidate") and not req.rollback.strip():
            return Decision(False, "a rollback path is required")

        if rule.kind in ("read", "write"):
            raw = req.arguments.get("path", req.target) if isinstance(req.arguments, dict) else None
            p = self.resolve(raw) if isinstance(raw, str) else None
            if p is None:
                return Decision(False, "path is outside the assigned worktree")
            if rule.kind == "write":
                if self._protected(p):
                    return Decision(False, f"{self.rel(p)} is a constitution/approval-gate/safety file",
                                    needs_user=True)
                tier = classify_path(self.rel(p))
                if tier_rank(tier) > tier_rank(goal.risk_tier):
                    return Decision(False, f"{self.rel(p)} is tier {tier}, above the goal's {goal.risk_tier}")
            return Decision(True, f"{req.action} inside the worktree")
        if rule.kind == "exec":
            argv = req.arguments.get("argv") if isinstance(req.arguments, dict) else None
            if req.action == "git_commit":
                if not (isinstance(argv, list) and argv[:2] == ["git", "commit"]):
                    return Decision(False, "git_commit must be argv ['git', 'commit', ...]")
            return self.check_argv(argv, goal)
        if rule.kind in ("candidate", "stage"):
            return Decision(True, f"{req.action} allowed at {level}")
        if rule.kind == "release":
            if goal.risk_tier == "docs_tests":
                return Decision(True, "automatic tier: docs/tests release after tests + both approvals")
            return Decision(False, f"release of tier {goal.risk_tier} needs the user", needs_user=True)
        if rule.kind == "rollback":
            return Decision(True, "L4 automatic rollback on regression")
        return Decision(False, "unhandled action kind (fail closed)")


__all__ = ["ACTIONS", "ALWAYS_USER", "Decision", "LEVELS", "Policy", "PolicyRefusal", "Scope", "classify_path",
           "tier_rank"]
