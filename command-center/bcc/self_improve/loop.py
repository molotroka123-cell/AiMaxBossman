"""The improvement loop as an explicit state machine.

    DETECTED -> PROPOSED -> EXPERIMENT -> CHECKED -> REPORTED -> AWAITING_OWNER -> APPLIED -> ROLLED_BACK
                    \\___________ any state -> REJECTED | BLOCKED | NOT_RUN (terminal) ___________/

Rules enforced in code (each has a test):
* a hypothesis opens only after >= ``recurrence_threshold`` similar failures (same agent + error class);
* the proposer sees the sanitized failure summary only - never the held-out tasks, never their expected answers;
* the held-out task set is hashed and journaled BEFORE the experiment; a changed hash -> BLOCKED; a held-out task
  that is also a failure example the proposer saw -> BLOCKED (leakage);
* the verifier must be a different role AND model than the proposer; none available -> NOT_RUN (self-review is not evidence);
* the baseline is run once per task-set hash, journaled, and reused (frozen);
* time / token / cost caps and the delegation depth are checked as the experiment runs; a breach -> BLOCKED;
* PASS needs: enough held-out tasks, a strictly higher pass rate than the baseline, ZERO regressions, within caps;
* PASS only moves a hypothesis to AWAITING_OWNER. Applying is ``apply.apply_draft`` and needs the owner's token.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

from .delegation import DelegationChain, DelegationStop
from .drafts import Draft, DraftStore
from .limits import BLOCKED, FAIL, NA, NOT_RUN, PASS, LimitViolation, Limits
from .redact import sanitize
from .sandbox import Sandbox
from .store import Store

STATES = ("DETECTED", "PROPOSED", "EXPERIMENT", "CHECKED", "REPORTED", "AWAITING_OWNER", "APPLIED", "ROLLED_BACK",
          "REJECTED", "BLOCKED", "NOT_RUN")
TERMINAL = ("REJECTED", "BLOCKED", "NOT_RUN", "ROLLED_BACK")
TRANSITIONS = {
    "DETECTED": ("PROPOSED", "BLOCKED", "NOT_RUN"),
    "PROPOSED": ("EXPERIMENT", "BLOCKED", "NOT_RUN", "REJECTED"),
    "EXPERIMENT": ("CHECKED", "BLOCKED", "NOT_RUN"),
    "CHECKED": ("REPORTED",),
    "REPORTED": ("AWAITING_OWNER", "REJECTED", "BLOCKED", "NOT_RUN"),
    "AWAITING_OWNER": ("APPLIED", "REJECTED"),
    "APPLIED": ("ROLLED_BACK",),
}


class Proposer(Protocol):
    role: str
    model: str

    def propose(self, candidate: "Candidate") -> dict: ...  # {kind,target_rel,base_text,proposed_text,justification}


class Executor(Protocol):
    role: str
    model: str

    def run(self, instruction: str, task: dict, sandbox: Sandbox) -> dict: ...  # {output,tokens,cost_usd}


class Verifier(Protocol):
    role: str
    model: str

    def judge(self, task: dict, output: str) -> bool: ...


@dataclass(frozen=True)
class Candidate:
    """What the proposer is allowed to see. Contains sanitized classes and counts, never raw text."""
    hypothesis_id: str
    agent_id: str
    error_class: str
    count: int
    instruction_version: str
    example_task_ids: tuple[str, ...]


@dataclass(frozen=True)
class TaskSet:
    tasks: tuple[dict, ...]
    set_hash: str

    @staticmethod
    def freeze(tasks: Iterable[dict]) -> "TaskSet":
        ts = tuple(sorted(({"id": str(t["id"]), "input": t["input"], "expected": t["expected"]} for t in tasks),
                          key=lambda t: t["id"]))
        return TaskSet(ts, hashlib.sha256(json.dumps(ts, sort_keys=True, ensure_ascii=False).encode()).hexdigest())

    def verify_hash(self) -> bool:
        return TaskSet.freeze(self.tasks).set_hash == self.set_hash

    def ids(self) -> set[str]:
        return {t["id"] for t in self.tasks}


@dataclass
class Budget:
    limits: Limits
    clock: Callable[[], float] = time.monotonic
    started: float = 0.0
    tokens: int = 0
    cost: float = 0.0

    def __post_init__(self) -> None:
        self.started = self.clock()

    def charge(self, tokens: int, cost: float) -> None:
        self.tokens += int(tokens or 0)
        self.cost += float(cost or 0.0)
        if self.clock() - self.started > self.limits.max_wall_seconds:
            raise LimitViolation("wall-clock cap exceeded")
        if self.tokens > self.limits.max_tokens:
            raise LimitViolation("token cap exceeded")
        if self.cost > self.limits.cost_cap_usd + 1e-12:
            raise LimitViolation(f"cost cap exceeded (cap {self.limits.cost_cap_usd} USD)")


class InvalidTransition(RuntimeError):
    pass


def detect_recurring(observations: Iterable[dict], limits: Limits) -> list[Candidate]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for o in observations:
        if o.get("outcome") in (FAIL, BLOCKED) and o.get("error_class") not in (None, "none"):
            groups[(str(o.get("agent_id")), str(o.get("error_class")))].append(o)
    out = []
    for (agent, cls), rows in sorted(groups.items()):
        if len(rows) >= limits.recurrence_threshold:
            sig = hashlib.sha256(f"{agent}|{cls}".encode()).hexdigest()[:10]
            out.append(Candidate(f"H-{sig}", agent, cls, len(rows), str(rows[-1].get("instruction_version", NA)),
                                 tuple(str(r["task_id"]) for r in rows[-5:])))
    return out


class ImprovementLoop:
    def __init__(self, store: Store, root, limits: Limits | None = None, *, live_data_dir=None,
                 clock: Callable[[], float] = time.monotonic):
        self.store = store
        self.root = root
        self.limits = limits or Limits()
        self.live_data_dir = live_data_dir
        self.clock = clock
        self.drafts = DraftStore(root, self.limits)
        self._state: dict[str, str] = {}
        self._proposer_view: dict[str, Candidate] = {}
        self._proposer_id: dict[str, tuple[str, str]] = {}
        self._seen_examples: dict[str, set[str]] = {}
        self.store.record("limits.frozen", {**self.limits.__dict__, "digest": self.limits.digest()})

    # --------------------------------------------------------------- state machine
    def state_of(self, hid: str) -> str:
        return self._state.get(hid, "NONE")

    def _go(self, hid: str, new: str, **info: Any) -> None:
        cur = self._state.get(hid)
        if cur is None and new != "DETECTED":
            raise InvalidTransition(f"{hid}: unknown hypothesis")
        if cur is not None and new not in TRANSITIONS.get(cur, ()):
            raise InvalidTransition(f"{hid}: {cur} -> {new} is not allowed")
        self._state[hid] = new
        self.store.record("hypothesis", {"id": hid, "state": new, **info})

    def detect(self, observations: Iterable[dict]) -> list[Candidate]:
        found = []
        for c in detect_recurring(observations, self.limits):
            if c.hypothesis_id in self._state:
                continue
            self._go(c.hypothesis_id, "DETECTED", agent_id=c.agent_id, error_class=c.error_class, count=c.count,
                     instruction_version=c.instruction_version, example_task_ids=list(c.example_task_ids))
            self._proposer_view[c.hypothesis_id] = c
            self._seen_examples[c.hypothesis_id] = set(c.example_task_ids)
            found.append(c)
        return found

    # --------------------------------------------------------------- propose
    def propose(self, hid: str, proposer: Proposer) -> Draft | None:
        cand = self._proposer_view[hid]
        try:
            raw = proposer.propose(cand)  # the proposer receives ONLY the candidate
            draft = self.drafts.save(f"D-{hid}", kind=raw["kind"], target_rel=raw["target_rel"],
                                     base_text=raw.get("base_text"), proposed_text=raw["proposed_text"],
                                     author_role=proposer.role, author_model=proposer.model,
                                     justification=raw.get("justification", ""))
        except LimitViolation as exc:
            self._go(hid, "BLOCKED", reason=str(exc)[:200])
            return None
        self._proposer_id[hid] = (proposer.role, proposer.model)
        self._go(hid, "PROPOSED", draft_id=draft.draft_id, draft_sha256=draft.proposed_sha256, kind=draft.kind,
                 justification=draft.justification[:400], proposer_role=proposer.role, proposer_model=proposer.model)
        return draft

    # --------------------------------------------------------------- experiment
    def _terminal(self, hid: str, status: str, reason: str, **extra: Any) -> dict:
        report = {"hypothesis_id": hid, "status": status, "reason": reason,
                  "claim": claim_label(status), "rollback": NA, "owner_decision": NA, **extra}
        self.store.record("result", report)
        if self._state.get(hid) == "EXPERIMENT":
            self._go(hid, "CHECKED", status=status)
            self._go(hid, "REPORTED", status=status)
        target = {FAIL: "REJECTED", BLOCKED: "BLOCKED", NOT_RUN: "NOT_RUN"}[status]
        if self._state.get(hid) == "REPORTED":
            self._go(hid, target, status=status, reason=reason)
        elif self._state.get(hid) in ("PROPOSED", "DETECTED"):
            self._go(hid, target if target != "REJECTED" else "BLOCKED", status=status, reason=reason)
        return report

    def run_experiment(self, hid: str, *, executor: Executor, verifier: Verifier | None, taskset: TaskSet,
                       baseline_text: str, chain: DelegationChain | None = None) -> dict:
        draft, proposed_text = self.drafts.load(f"D-{hid}")
        prop_role, prop_model = self._proposer_id.get(hid, (draft.author_role, draft.author_model))

        # --- gates that decide NOT_RUN / BLOCKED before any compute is spent
        if verifier is None:
            return self._terminal(hid, NOT_RUN, "no independent verifier available")
        if verifier.role == prop_role or verifier.model == prop_model:
            return self._terminal(hid, NOT_RUN, "verifier is not independent of the proposer (same role or model)")
        if len(taskset.tasks) < self.limits.min_heldout_tasks:
            return self._terminal(hid, NOT_RUN, f"held-out set too small ({len(taskset.tasks)} < "
                                                f"{self.limits.min_heldout_tasks})")
        leaked = taskset.ids() & self._seen_examples.get(hid, set())
        if leaked:
            return self._terminal(hid, BLOCKED, f"held-out tasks overlap failure examples seen by the proposer: "
                                                f"{sorted(leaked)[:3]}")
        frozen_hash = taskset.set_hash
        self.store.record("taskset.frozen", {"hypothesis_id": hid, "set_hash": frozen_hash, "n": len(taskset.tasks)})
        if not taskset.verify_hash():
            return self._terminal(hid, BLOCKED, "held-out set hash does not match the frozen hash")

        self._go(hid, "EXPERIMENT", taskset_hash=frozen_hash, n=len(taskset.tasks),
                 verifier_role=verifier.role, verifier_model=verifier.model)
        budget = Budget(self.limits, self.clock)
        sb = Sandbox(self.root, f"exp-{hid}", self.limits, live_data_dir=self.live_data_dir)
        try:
            chain = chain or DelegationChain(self.limits, self.store)
            chain.enter(f"executor:{executor.role}")
            sb.write_text("candidate.txt", proposed_text)
            baseline = self._frozen_baseline(hid, executor, verifier, taskset, baseline_text, sb, budget)
            cand = self._run_variant(proposed_text, executor, verifier, taskset, sb, budget)
            if not taskset.verify_hash():
                return self._terminal(hid, BLOCKED, "held-out set changed during the run")
        except DelegationStop as exc:
            return self._terminal(hid, BLOCKED, "delegation stopped: " + exc.reason)
        except LimitViolation as exc:
            return self._terminal(hid, BLOCKED, str(exc)[:200], cost_usd=round(budget.cost, 6), tokens=budget.tokens)
        finally:
            sb.cleanup()

        base_pass = {r["id"] for r in baseline["rows"] if r["passed"]}
        cand_pass = {r["id"] for r in cand["rows"] if r["passed"]}
        regressions = sorted(base_pass - cand_pass)
        n = len(taskset.tasks)
        b_rate, c_rate = len(base_pass) / n, len(cand_pass) / n
        status = PASS if (c_rate > b_rate and not regressions) else FAIL
        reason = ("candidate strictly better on held-out tasks with zero regressions" if status == PASS else
                  f"no reproducible gain (baseline {b_rate:.3f}, candidate {c_rate:.3f}, regressions {len(regressions)})")
        report = {"hypothesis_id": hid, "status": status, "reason": reason, "claim": claim_label(status),
                  "taskset_hash": frozen_hash, "n": n,
                  "baseline": {"pass_rate": round(b_rate, 4), "cost_usd": baseline["cost"], "tokens": baseline["tokens"]},
                  "candidate": {"pass_rate": round(c_rate, 4), "cost_usd": cand["cost"], "tokens": cand["tokens"]},
                  "delta": round(c_rate - b_rate, 4), "regressions": regressions,
                  "cost_usd": round(baseline["cost"] + cand["cost"], 6), "cost_cap_usd": self.limits.cost_cap_usd,
                  "proposer": {"role": prop_role, "model": prop_model},
                  "verifier": {"role": verifier.role, "model": verifier.model},
                  "rollback": NA,
                  "owner_decision": "требуется токен владельца для применения" if status == PASS else NA,
                  "os_network_isolation": NOT_RUN}
        self.store.record("result", report)
        self._go(hid, "CHECKED", status=status)
        self._go(hid, "REPORTED", status=status)
        if status == PASS:
            self._go(hid, "AWAITING_OWNER", status=status)
        else:
            self._go(hid, "REJECTED", status=status, reason=reason)
        return report

    def _run_variant(self, instruction: str, executor: Executor, verifier: Verifier, taskset: TaskSet,
                     sb: Sandbox, budget: Budget) -> dict:
        rows, cost, tokens = [], 0.0, 0
        for t in taskset.tasks:
            visible = {"id": t["id"], "input": t["input"]}      # the executor never sees `expected`
            res = executor.run(instruction, visible, sb)
            c, k = float(res.get("cost_usd") or 0.0), int(res.get("tokens") or 0)
            budget.charge(k, c)
            cost, tokens = cost + c, tokens + k
            rows.append({"id": t["id"], "passed": bool(verifier.judge(t, str(res.get("output", ""))))})
        return {"rows": rows, "cost": round(cost, 6), "tokens": tokens}

    def _frozen_baseline(self, hid, executor, verifier, taskset, baseline_text, sb, budget) -> dict:
        for e in self.store.events("baseline.frozen"):
            p = e["payload"]
            if p.get("set_hash") == taskset.set_hash and p.get("instruction_sha256") == hashlib.sha256(
                    baseline_text.encode()).hexdigest():
                return {"rows": p["rows"], "cost": p["cost"], "tokens": p["tokens"]}
        res = self._run_variant(baseline_text, executor, verifier, taskset, sb, budget)
        self.store.record("baseline.frozen", {"hypothesis_id": hid, "set_hash": taskset.set_hash,
                                              "instruction_sha256": hashlib.sha256(baseline_text.encode()).hexdigest(),
                                              **res})
        return res


def claim_label(status: str) -> str:
    """The only wording allowed for the outcome. 'Improved' appears only after a PASS."""
    if status == PASS:
        return "улучшение подтверждено на отложенных задачах независимым проверяющим"
    return f"{NOT_RUN}: воспроизводимого выигрыша нет, улучшение не заявляется (итог: {status})"
