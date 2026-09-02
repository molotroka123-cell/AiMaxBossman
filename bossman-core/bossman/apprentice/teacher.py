"""Claude Code as an EXTERNAL, UNTRUSTED teacher (flag BOSSMAN_CLAUDE_CODE_FALLBACK).

The apprentice tries itself first. Fallback only for a typed FallbackReason. It
builds a minimal sanitized ProblemBundle, observes the teacher's visible process
(typed fields only — never hidden chain-of-thought, never raw logs), verifies the
patch with an INDEPENDENT Principal (tests, diff review, security scan, evidence
freshness, task/run/HEAD binding, regressions) and only then accepts. Everything
the teacher says is UNTRUSTED_TEACHER_OUTPUT until verified; the teacher cannot
declare VERIFIED, touch acceptance tests, weaken policy, add secrets or train the
system on its own answer.

Patches are path -> new content mappings (full-file replacement); applying real
unified diffs to a live checkout is a live-run gap documented in the handoff."""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from bossman.cybersec.injection import inspect as firewall_inspect, scan as firewall_scan
from bossman.cybersec.trust import TrustLevel
from bossman.deep_fix import Evidence, Principal

from . import flags
from ._bootstrap import trace
from .errors import BudgetExhausted, CircuitOpen, FallbackRefused, FlagDisabled, SecretInRecord
from .models import ApprenticeTask, sha
from .skills import EvidenceBinding, SelfVerificationRefused

MAX_BUNDLE_FILES = 12
MAX_BUNDLE_CHARS = 40_000
MAX_EXCERPT_CHARS = 6_000
PROTECTED_PATH_TOKENS = ("policy", "cybersec", "sandbox", "learning_guard", "approvals", "perimeter", "deep_fix",
                         "cost_control", "apprentice/sanctions", "apprentice/teacher", ".github/workflows")
_WEAKENING = (
    ("test_skip", re.compile(r"pytest\.(skip|xfail)|@pytest\.mark\.(skip|xfail)|unittest\.skip")),
    ("tls_off", re.compile(r"verify\s*=\s*False")),
    ("check_disabled", re.compile(r"(?i)(security|policy|guard|check|verif\w*|approval)\w*\s*=\s*(False|0|None)\b")),
    ("flag_flip", re.compile(r"os\.environ\[\s*['\"]BOSSMAN_[A-Z_]+['\"]\s*\]\s*=\s*['\"](1|true|yes)")),
    ("assert_removed", re.compile(r"(?m)^\s*#\s*assert\b")),
    ("eval_exec", re.compile(r"\b(eval|exec)\s*\(")),
    ("shell_true", re.compile(r"shell\s*=\s*True")),
)


class FallbackReason(str, Enum):
    ATTEMPTS_EXHAUSTED = "ATTEMPTS_EXHAUSTED"; LOW_CONFIDENCE = "LOW_CONFIDENCE"; TESTS_STILL_FAILING = "TESTS_STILL_FAILING"
    UNKNOWN_ARCHITECTURE = "UNKNOWN_ARCHITECTURE"; OWNER_REQUESTED = "OWNER_REQUESTED"


class TeacherStatus(str, Enum):
    UNTRUSTED_TEACHER_OUTPUT = "UNTRUSTED_TEACHER_OUTPUT"
    TEACHER_OUTPUT_ACCEPTED = "TEACHER_OUTPUT_ACCEPTED"
    TEACHER_OUTPUT_REJECTED = "TEACHER_OUTPUT_REJECTED"
    TEACHER_OUTPUT_QUARANTINED = "TEACHER_OUTPUT_QUARANTINED"
    ACCEPTANCE_TAMPERING = "ACCEPTANCE_TAMPERING"


# ------------------------------------------------------------------ bundle
@dataclass(frozen=True, slots=True)
class ProblemBundle:
    bug_description: str
    files: dict[str, str]
    failing_test: str
    constraints: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    acceptance_tests: tuple[str, ...]
    repo_instruction_findings: tuple[str, ...] = ()
    critique: str = ""
    bundle_id: str = ""

    def as_dict(self) -> dict:
        return {"bundle_id": self.bundle_id, "bug_description": self.bug_description, "files": dict(self.files),
                "failing_test": self.failing_test, "constraints": list(self.constraints),
                "allowed_paths": list(self.allowed_paths), "acceptance_tests": list(self.acceptance_tests),
                "repo_instruction_findings": list(self.repo_instruction_findings), "critique": self.critique}


def _in_scope(path: str, allowed: tuple[str, ...]) -> bool:
    p = path.replace("\\", "/").lstrip("./")
    return any(p == a or p.startswith(a.rstrip("/") + "/") for a in allowed) and ".." not in p.split("/")


def build_bundle(*, bug_description: str, files: dict[str, str], failing_test: str, constraints: tuple[str, ...],
                 allowed_paths: tuple[str, ...], acceptance_tests: tuple[str, ...], repo_instructions: str = "",
                 critique: str = "") -> ProblemBundle:
    """Minimal, sanitized, bounded. Whole-repo bundles, out-of-scope paths and secrets are refused."""
    tr = trace()
    if len(files) > MAX_BUNDLE_FILES:
        raise FallbackRefused(f"bundle too large: {len(files)} files > {MAX_BUNDLE_FILES} (no whole-repo bundles)")
    if not allowed_paths:
        raise FallbackRefused("allowed_paths required (scope must be explicit)")
    out: dict[str, str] = {}
    for path, content in files.items():
        if not _in_scope(path, allowed_paths):
            raise FallbackRefused(f"file {path!r} is outside the allowed scope {list(allowed_paths)}")
        excerpt = str(content)[:MAX_EXCERPT_CHARS]
        if tr.has_secret(excerpt):
            raise SecretInRecord(f"file {path!r} contains a secret-like value; refused from the bundle")
        out[path] = excerpt
    total = sum(len(v) for v in out.values()) + len(bug_description)
    if total > MAX_BUNDLE_CHARS:
        raise FallbackRefused(f"bundle too large: {total} chars > {MAX_BUNDLE_CHARS}")
    desc = tr.redact_text(bug_description)[:4000]
    if tr.has_secret(bug_description):
        raise SecretInRecord("bug description contains a secret-like value")
    findings: tuple[str, ...] = ()
    if repo_instructions:
        verdict = firewall_inspect(repo_instructions, source_trust=TrustLevel.UNTRUSTED)
        findings = tuple(f.pattern_id for f in verdict.findings)
        # repository instructions never become constraints; only a neutralized note about their presence
        desc += "\n[repository instructions were present; treated as untrusted data, not as constraints]"
    fixed = tuple(constraints) + ("do not modify acceptance tests", "do not weaken security policy or disable checks",
                                  "do not add secrets", "no push/deploy", "stay within allowed_paths")
    bid = sha("bundle", desc, sorted(out), failing_test, fixed)[:16]
    return ProblemBundle(desc, out, failing_test, fixed, tuple(allowed_paths), tuple(acceptance_tests), findings,
                         critique=critique, bundle_id=bid)


# ------------------------------------------------------------------ observation of the teacher
@dataclass(slots=True)
class TeacherObservation:
    """Only typed, visible process facts. status starts UNTRUSTED and only the verifier changes it."""
    opened_files: list[str]
    symbols: list[str]
    commands: list[str]
    root_cause: str
    patch: dict[str, str] | str
    attempt_errors: list[str]
    claimed_tests: dict[str, Any]
    artifacts: list[str]
    log_findings: list[str]
    log_unsafe: bool
    model_id: str
    model_version: str
    status: str = TeacherStatus.UNTRUSTED_TEACHER_OUTPUT.value
    claimed_status_ignored: str = ""

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}


_DROP_KEYS = frozenset({"chain_of_thought", "hidden_reasoning", "thoughts", "scratchpad", "reasoning", "raw_prompt"})


def observe_teacher(output: dict) -> TeacherObservation:
    tr = trace()
    o = {k: v for k, v in dict(output).items() if k not in _DROP_KEYS}
    log = str(o.get("log_text") or "")
    verdict = firewall_inspect(log, source_trust=TrustLevel.UNTRUSTED)
    raw_patch = o.get("patch") or o.get("diff") or {}
    patch = {str(p): str(c) for p, c in raw_patch.items()} if isinstance(raw_patch, dict) else (str(raw_patch) if raw_patch else {})
    obs = TeacherObservation(
        opened_files=[str(x) for x in o.get("opened_files") or []][:100],
        symbols=[str(x) for x in o.get("symbols") or []][:200],
        commands=[tr.redact_text(str(x)) for x in o.get("commands") or []][:100],
        root_cause=tr.redact_text(str(o.get("root_cause") or ""))[:2000],
        patch=patch,
        attempt_errors=[tr.redact_text(str(x))[:500] for x in o.get("attempt_errors") or []][:50],
        claimed_tests=dict(o.get("test_results") or {}),
        artifacts=[str(x) for x in o.get("artifacts") or []][:50],
        log_findings=[f.pattern_id for f in verdict.findings], log_unsafe=not verdict.safe,
        model_id=str(o.get("model_id") or "claude-code"), model_version=str(o.get("model_version") or "unknown"),
        claimed_status_ignored=str(o.get("status") or ""))
    return obs


# ------------------------------------------------------------------ acceptance binding
@dataclass(frozen=True, slots=True)
class AcceptanceBinding:
    """Hash-bound acceptance tests: any change = ACCEPTANCE_TAMPERING."""
    hashes: dict[str, str]
    contents: dict[str, str]

    @classmethod
    def bind(cls, workspace: Any, test_paths: tuple[str, ...]) -> "AcceptanceBinding":
        contents = {p: workspace.read(p) for p in test_paths}
        return cls({p: hashlib.sha256(c.encode("utf-8")).hexdigest() for p, c in contents.items()}, contents)

    def tampered(self, workspace: Any) -> list[str]:
        out = []
        for p, h in self.hashes.items():
            try:
                cur = workspace.read(p)
            except Exception:  # noqa: BLE001 — deleted test counts as tampering
                out.append(p); continue
            if hashlib.sha256(cur.encode("utf-8")).hexdigest() != h:
                out.append(p)
        return out

    def restore(self, workspace: Any) -> None:
        for p, c in self.contents.items():
            try:
                workspace.write(p, c, restore=True)          # LiveWorkspace: protected paths only via restore
            except TypeError:
                workspace.write(p, c)                        # simple test doubles


# ------------------------------------------------------------------ verification
@dataclass(slots=True)
class TeacherVerdict:
    status: str
    reasons: list[str]
    evidence: list[Evidence]
    critique: str = ""
    violation_type: str = ""
    rolled_back: bool = False
    tests_restored: bool = False
    files_changed: list[str] = field(default_factory=list)
    attempt: int = 0

    @property
    def accepted(self) -> bool:
        return self.status == TeacherStatus.TEACHER_OUTPUT_ACCEPTED.value


def patch_paths(patch: dict[str, str] | str) -> list[str]:
    if isinstance(patch, dict):
        return list(patch)
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith("+++ "):
            value = line[4:].strip().split("\t", 1)[0]
            if value != "/dev/null": paths.append(value[2:] if value.startswith("b/") else value)
    return paths


def security_findings(patch: dict[str, str] | str, *, allowed_paths: tuple[str, ...]) -> list[str]:
    tr = trace()
    out: list[str] = []
    items = patch.items() if isinstance(patch, dict) else ((p, patch) for p in patch_paths(patch))
    for path, content in items:
        low = path.lower()
        if any(tok in low for tok in PROTECTED_PATH_TOKENS):
            out.append(f"protected path touched: {path}")
        if not _in_scope(path, allowed_paths):
            out.append(f"out of scope: {path}")
        if tr.has_secret(content):
            out.append(f"secret added in {path}")
        for pid, rx in _WEAKENING:
            if rx.search(content):
                out.append(f"{pid} in {path}")
        for f in firewall_scan(content):
            if f.severity in ("high", "critical"):
                out.append(f"injection:{f.pattern_id} in {path}")
    return out


class PatchVerifier:
    """Independent verification of a teacher patch. `workspace` must provide
    snapshot()/restore(token)/read(path)/write(path, text)/apply(patch)/run_tests(ids) -> (passed: bool, failed: list[str], excerpt: str)."""

    def __init__(self, *, verifier: Principal, clock: Callable[[], float] = time.time) -> None:
        self.verifier = verifier
        self.clock = clock

    def verify(self, bundle: ProblemBundle, obs: TeacherObservation, *, workspace: Any, teacher: Principal,
               acceptance: AcceptanceBinding, binding: EvidenceBinding, regression_tests: tuple[str, ...] = (),
               attempt: int = 1) -> TeacherVerdict:
        ok, why = self.verifier.independent_of(teacher)
        if not ok:
            raise SelfVerificationRefused(f"patch verifier is not independent of the teacher: {why}")
        reasons: list[str] = []
        if obs.claimed_status_ignored:
            reasons.append(f"teacher claimed {obs.claimed_status_ignored!r}: ignored (not evidence)")
        if obs.log_unsafe:
            reasons.append(f"teacher log carried injection findings {obs.log_findings}: not executed, output distrusted")
        if not obs.patch:
            return TeacherVerdict(TeacherStatus.TEACHER_OUTPUT_REJECTED.value, reasons + ["no patch"], [], "teacher produced no patch", attempt=attempt)
        # 1. acceptance tampering (path level, before touching the workspace)
        touched_tests = [p for p in patch_paths(obs.patch) if p in acceptance.hashes]
        # 2. security review of the patch content (before applying)
        sec = security_findings(obs.patch, allowed_paths=bundle.allowed_paths)
        sec = [s for s in sec if not (touched_tests and s.startswith("out of scope") and s.split(": ", 1)[1] in touched_tests)]
        token = workspace.snapshot()
        if touched_tests:
            reasons.append(f"patch modifies acceptance tests {touched_tests}")
            return TeacherVerdict(TeacherStatus.ACCEPTANCE_TAMPERING.value, reasons, [
                self._evidence(binding, "diff_review", False, f"acceptance tests untouched", f"modified {touched_tests}")],
                "acceptance tests are hash-bound and may not be modified", violation_type="acceptance_tampering",
                rolled_back=True, tests_restored=True, attempt=attempt)
        if sec:
            reasons += sec
            return TeacherVerdict(TeacherStatus.TEACHER_OUTPUT_QUARANTINED.value, reasons, [
                self._evidence(binding, "security_scan", False, "no policy weakening / secrets / protected paths", "; ".join(sec)[:500])],
                "security regression: patch not applied", violation_type=sec[0].split(" in ")[0].split(":")[0], rolled_back=True, attempt=attempt)
        # 3. apply, then check the hash binding again (a patch could rewrite tests indirectly)
        try:
            workspace.apply(obs.patch)
        except Exception as exc:  # noqa: BLE001
            workspace.restore(token)
            return TeacherVerdict(TeacherStatus.TEACHER_OUTPUT_REJECTED.value, reasons + [f"patch does not apply: {exc!r}"],
                                  [self._evidence(binding, "apply", False, "patch applies", repr(exc)[:300])],
                                  f"patch does not apply cleanly: {exc!r}"[:500], rolled_back=True, attempt=attempt)
        tampered = acceptance.tampered(workspace)
        if tampered:
            workspace.restore(token); acceptance.restore(workspace)
            return TeacherVerdict(TeacherStatus.ACCEPTANCE_TAMPERING.value, reasons + [f"acceptance tests changed after apply: {tampered}"],
                                  [self._evidence(binding, "diff_review", False, "acceptance hashes unchanged", f"changed {tampered}")],
                                  "acceptance tests restored from hash-bound copy", violation_type="acceptance_tampering",
                                  rolled_back=True, tests_restored=True, attempt=attempt)
        # 4. independent tests (acceptance) + regressions
        passed, failed, excerpt = workspace.run_tests(bundle.acceptance_tests)
        ev = [self._evidence(binding, "test", passed, f"{list(bundle.acceptance_tests)} pass", excerpt[:300] or ("pass" if passed else "fail"))]
        if not passed:
            workspace.restore(token)
            return TeacherVerdict(TeacherStatus.TEACHER_OUTPUT_REJECTED.value, reasons + [f"acceptance tests failing: {failed}"], ev,
                                  f"tests still failing after the patch: {failed}; excerpt: {excerpt[:200]}", rolled_back=True,
                                  files_changed=sorted(patch_paths(obs.patch)), attempt=attempt)
        if regression_tests:
            rp, rf, rex = workspace.run_tests(regression_tests)
            ev.append(self._evidence(binding, "regression", rp, f"{list(regression_tests)} still pass", rex[:300] or ("pass" if rp else "fail")))
            if not rp:
                workspace.restore(token)
                return TeacherVerdict(TeacherStatus.TEACHER_OUTPUT_REJECTED.value, reasons + [f"new regressions: {rf}"], ev,
                                      f"patch fixes the target but breaks {rf}", rolled_back=True, files_changed=sorted(patch_paths(obs.patch)), attempt=attempt)
        # 5. evidence freshness + binding (task / run / HEAD / environment)
        now = self.clock()
        for e in ev:
            err = e.freshness_error(run=binding, now=now)
            if err:
                workspace.restore(token)
                return TeacherVerdict(TeacherStatus.TEACHER_OUTPUT_REJECTED.value, reasons + [f"evidence rejected: {err}"], ev,
                                      f"evidence not fresh/bound: {err}", rolled_back=True, attempt=attempt)
        return TeacherVerdict(TeacherStatus.TEACHER_OUTPUT_ACCEPTED.value, reasons + ["independent tests, diff review, security scan, freshness and binding passed"],
                              ev, "", files_changed=sorted(patch_paths(obs.patch)), attempt=attempt)

    def _evidence(self, b: EvidenceBinding, kind: str, passed: bool, expected: str, actual: str) -> Evidence:
        at = self.clock()
        return Evidence(kind=kind, detail=f"{kind} by {self.verifier.principal_id}", passed=passed, source=self.verifier.principal_id,
                        at=at, collected_at=at, task_id=b.task_id, run_id=b.run_id, principal_id=self.verifier.principal_id,
                        environment=b.environment, head_sha=b.head_sha, expected=expected, actual=actual or "-")


# ------------------------------------------------------------------ learned strategy (generalized, never the diff)
def strategy_from_verdict(bundle: ProblemBundle, obs: TeacherObservation, verdict: TeacherVerdict, *, task: ApprenticeTask,
                          bug_class: str, agent: str, principal_id: str) -> dict:
    if not verdict.accepted:
        raise FallbackRefused("only an ACCEPTED teacher result can become a strategy")
    tr = trace()
    steps = []
    if obs.opened_files:
        steps.append({"kind": "inspect", "what": "files matching the failing symbol", "count": len(obs.opened_files)})
    cats = sorted({c.split()[0] for c in obs.commands if c.strip()})
    if cats:
        steps.append({"kind": "commands", "categories": cats[:10]})
    steps.append({"kind": "root_cause_category", "text": tr.redact_text(obs.root_cause)[:300]})
    steps.append({"kind": "patch_shape", "files": len(verdict.files_changed), "paths": [p.rsplit("/", 1)[0] for p in verdict.files_changed][:10]})
    steps.append({"kind": "verify", "tests": list(bundle.acceptance_tests)})
    strat = {"task_id": f"strategy_{bug_class}_{sha(bug_class, obs.root_cause)[:10]}", "skill_id": f"strategy_{bug_class}",
             "record_type": "skill", "learning_status": "UNVERIFIED", "skill_state": "CANDIDATE",
             "title": f"bugfix strategy: {bug_class}", "summary": "generalized from a verified teacher result (no diff stored)",
             "task_type": f"bugfix:{bug_class}", "environment": task.environment or "unknown-env", "app": "repository",
             "model": obs.model_id, "agent": agent, "principal_id": principal_id, "run_id": task.run_id,
             "head_sha": task.head_sha, "start_sha": task.head_sha, "end_sha": task.head_sha,
             "applicability": {"bug_class": bug_class, "symbols": obs.symbols[:20]},
             "semantic_actions": steps, "expected_outcomes": [f"{t} passes" for t in bundle.acceptance_tests],
             "source_episode_ids": [task.task_id], "confidence": 0.5, "tags": {"domain": "bugfix", "risk": "medium"}}
    strat = tr.redact_obj(strat)
    from .recording import assert_sanitized
    assert_sanitized(strat, where="strategy")
    return strat


# ------------------------------------------------------------------ orchestration
@dataclass(slots=True)
class TeacherResult:
    status: str
    attempts: list[TeacherVerdict]
    observations: list[TeacherObservation]
    strategy: dict | None = None
    report: str = ""
    calls: int = 0
    denied_reason: str = ""


class TeacherFallback:
    """client.run(bundle_dict) -> output dict. governor (optional) must provide
    reserve_cloud_call(context, usd, idempotency_key=, cloud_allowed=) -> decision with .kind.value and .reservation."""

    def __init__(self, *, client: Any, workspace: Any, verifier: PatchVerifier, teacher: Principal,
                 governor: Any = None, budget_context: Any = None, estimated_usd: float = 0.5, max_calls: int = 2,
                 sanctions: Any = None, memory: Any = None, clock: Callable[[], float] = time.time) -> None:
        self.client, self.workspace, self.verifier, self.teacher = client, workspace, verifier, teacher
        self.governor, self.budget_context, self.estimated_usd = governor, budget_context, estimated_usd
        self.max_calls, self.sanctions, self.memory, self.clock = max_calls, sanctions, memory, clock

    def allowed(self, reason: FallbackReason | str, task: ApprenticeTask) -> str:
        if not flags.enabled(flags.CLAUDE_CODE_FALLBACK):
            return f"{flags.CLAUDE_CODE_FALLBACK} is off"
        try:
            r = FallbackReason(reason)
        except ValueError:
            return f"unknown fallback reason {reason!r}"
        if r is FallbackReason.OWNER_REQUESTED and not task.owner_requested_fallback:
            return "owner did not request the fallback"
        return ""

    def _reserve(self, task: ApprenticeTask, attempt: int) -> tuple[str, Any]:
        if self.governor is None:
            return "", None
        d = self.governor.reserve_cloud_call(self.budget_context, self.estimated_usd,
                                             idempotency_key=f"teacher:{task.task_id}:{task.run_id}:{attempt}", cloud_allowed=True)
        kind = getattr(getattr(d, "kind", None), "value", str(getattr(d, "kind", "")))
        if kind != "allow":
            return f"budget {kind}: {getattr(d, 'reason', '')}", None
        return "", getattr(d, "reservation", None)

    def request(self, *, reason: FallbackReason | str, task: ApprenticeTask, bundle: ProblemBundle,
                acceptance: AcceptanceBinding, binding: EvidenceBinding, regression_tests: tuple[str, ...] = (),
                bug_class: str = "generic", agent: str = "apprentice", principal_id: str = "apprentice") -> TeacherResult:
        why = self.allowed(reason, task)
        if why:
            raise FallbackRefused(why)
        attempts: list[TeacherVerdict] = []
        observations: list[TeacherObservation] = []
        calls = 0
        current = bundle
        status = TeacherStatus.UNTRUSTED_TEACHER_OUTPUT.value
        strategy = None
        while calls < self.max_calls:
            if self.sanctions is not None and self.sanctions.breaker.is_open():
                raise CircuitOpen(self.sanctions.breaker.report())
            denied, reservation = self._reserve(task, calls + 1)
            if denied:
                raise BudgetExhausted(denied)
            calls += 1
            try:
                output = self.client.run(current.as_dict())
            finally:
                if reservation is not None and self.governor is not None:
                    try:
                        self.governor.commit(reservation.id, self.estimated_usd)
                    except Exception:  # noqa: BLE001
                        pass
            obs = observe_teacher(output if isinstance(output, dict) else {})
            observations.append(obs)
            teacher = Principal(self.teacher.principal_id, model_id=obs.model_id or self.teacher.model_id, role="coder",
                                run_id=self.teacher.run_id, independence_class=self.teacher.independence_class)
            verdict = self.verifier.verify(current, obs, workspace=self.workspace, teacher=teacher, acceptance=acceptance,
                                           binding=binding, regression_tests=regression_tests, attempt=calls)
            attempts.append(verdict)
            status = verdict.status
            decision = None
            if self.sanctions is not None:
                decision = self.sanctions.apply(verdict, model_id=obs.model_id, model_version=obs.model_version,
                                                task_type=task.task_type, repository=binding.environment or "repo", now=self.clock())
                if decision.stop:
                    return TeacherResult(status, attempts, observations, None, decision.report, calls, decision.reason)
            if verdict.accepted:
                strategy = strategy_from_verdict(current, obs, verdict, task=task, bug_class=bug_class, agent=agent, principal_id=principal_id)
                if self.memory is not None and flags.enabled(flags.SKILL_RECORDING):
                    self.memory.store_skill(strategy)
                break
            if verdict.status != TeacherStatus.TEACHER_OUTPUT_REJECTED.value:
                break                                     # quarantine / tampering: no corrective retry
            if decision is not None and not decision.retry_allowed:
                break
            current = build_bundle(bug_description=current.bug_description, files=current.files, failing_test=current.failing_test,
                                   constraints=current.constraints, allowed_paths=current.allowed_paths,
                                   acceptance_tests=current.acceptance_tests, critique=verdict.critique)
        report = f"teacher fallback: {status} after {calls} call(s); " + "; ".join(a.reasons[-1] for a in attempts)[:600]
        return TeacherResult(status, attempts, observations, strategy, report, calls)


def learned_strategy(memory: Any, bug_class: str) -> dict | None:
    """On a similar bug the apprentice tries the learned (verified) method itself first."""
    if memory is None:
        return None
    for s in memory.skills(verified_only=True):
        if s.get("task_type") == f"bugfix:{bug_class}" and s.get("skill_state") in ("READY", "SHADOW"):
            return s
    return None
