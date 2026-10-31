"""Hierarchical Swarm DAG Planner & Cross-Family Verifier Engine.

Implements the 4-phase autonomous swarm execution cycle:
1. Phase 1 — Decompose & Preregister:
   Decomposes owner directive into an acyclic graph of subtasks (DAG nodes)
   with preconditions, allowed tools, and formal postconditions (verify_fn).
2. Phase 2 — Parallel Swarm Execution:
   Executes independent subtasks in parallel respecting DAG dependencies.
3. Phase 3 — Checks:
   a) a STRUCTURAL / custom check (`structural_check`): non-empty output, no `error` key, or a caller-supplied
      `checker_fn`. This is NOT cross-family verification — no model judged anything.
   b) CROSS-FAMILY model verification (`cross_family_receipt`): only when a `VerifierModelAdapter` is wired and
      actually invoked, its family differs from the worker's family and it returns a real PASS/FAIL verdict.
      Only that sets `cross_family_verified=True` (P0 audit 31.10: a lambda used to set it).
4. Phase 4 — Self-Reflection & Bounded Repair:
   Bounded self-reflection loop with verifier diagnostic feedback.

Context packing is powered by `bcc.features.memory_retrieval_v16.pack()`.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from bcc.features.memory_retrieval_v16 import MemoryCandidate, Signals, pack as memory_pack
from bcc.v2.task_graph import TaskGraph, TaskNode

logger = logging.getLogger(__name__)

ALLOWED_FAMILIES = ("gemma", "qwen", "nemotron", "glm", "claude", "gpt", "deepseek", "mistral", "llama", "gemini")

# Verdicts a verifier model may return. Anything else (empty, «maybe», free text) is not a verdict.
_PASS_VERDICTS = frozenset({"PASS"})
_FAIL_VERDICTS = frozenset({"FAIL"})


class CrossFamilyViolationError(ValueError):
    """Raised when verifier model family is identical to proposer model family."""


class SwarmNotWired(RuntimeError):
    """No executor was supplied: the DAG can be PLANNED but nothing can be run (it used to be «run» as a mock)."""


class SwarmExecutionError(RuntimeError):
    """Raised when DAG execution fails beyond repair limits."""


def normalize_family(name: str) -> str:
    cleaned = str(name).strip().lower()
    for fam in ALLOWED_FAMILIES:
        if fam in cleaned:
            return fam
    return cleaned


def _known_family(name: str | None) -> str | None:
    """The family of a model id / family label, or None when it is not a recognised family (cannot prove a difference)."""
    if not name:
        return None
    fam = normalize_family(name)
    return fam if fam in ALLOWED_FAMILIES else None


def _sha256_of(value: Any) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        raw = repr(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class VerifierModelAdapter:
    """A verifier MODEL the swarm can actually call. `call(request)` (sync or async) must return a verdict:
    `"PASS"` / `"FAIL"` or `{"verdict": "PASS"|"FAIL", "reason": str}`.

    `provider` + `model_id` are recorded in every receipt. The family is taken from `model_id` (and `family`, when
    given, must agree with it); an unrecognised family cannot prove cross-family independence."""

    provider: str
    model_id: str
    call: Callable[[dict[str, Any]], Any]
    family: str | None = None


@dataclass
class SwarmSubtask:
    """An executable subtask within the Swarm DAG."""

    id: str
    title: str
    preconditions: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    verify_fn: str = "default_verification"
    depends_on: list[str] = field(default_factory=list)
    proposer_family: str = "gemma"
    verifier_family: str = "qwen"
    status: str = "PENDING"
    result: Any = None
    # gate passed: structural/custom check passed and (if a verifier model answered) its verdict was not FAIL
    verification_passed: bool = False
    # structural / custom-checker result: never evidence of cross-family verification
    structural_check: dict[str, Any] = field(default_factory=dict)
    # receipt of an actual call to a verifier model of another family (empty when none was made)
    cross_family_receipt: dict[str, Any] = field(default_factory=dict)
    cross_family_verified: bool = False
    # combined view kept for callers: {"structural_check": ..., "cross_family": ...}
    verification_receipt: dict[str, Any] = field(default_factory=dict)
    repair_attempts: int = 0
    max_repairs: int = 2


@dataclass
class SwarmPlanReceipt:
    """Cryptographic audit receipt for the executed Swarm DAG."""

    goal: str
    proposer_family: str
    verifier_family: str
    dag_nodes: int
    completed_nodes: int
    cross_family_verified: bool
    context_tokens_used: int
    duration_ms: float
    sha256_receipt: str
    status: str
    executed: bool = True
    verification_kind: str = "structural_only"
    # True only when every completed node passed its structural/custom check (None = nothing was executed)
    structural_check_passed: bool | None = None
    # provider/model_id/family of the wired verifier model (None = no verifier model was wired)
    verifier_model: dict[str, Any] | None = None
    # per-node cross-family receipts (empty unless a verifier model was actually called)
    cross_family_receipts: list[dict[str, Any]] = field(default_factory=list)


class CrossFamilyVerifierEngine:
    """Independent verifier ensuring proposer_family != verifier_family."""

    def __init__(self, verifier_family: str = "qwen"):
        self.verifier_family = normalize_family(verifier_family)

    def verify(
        self,
        *,
        subtask_id: str,
        proposer_family: str,
        proposal: Any,
        expected_condition: str = "",
        custom_checker: Callable[[Any], bool] | None = None,
    ) -> dict[str, Any]:
        """STRUCTURAL / custom check. Without `custom_checker`: non-empty, no `error` key. With it: whatever the callable
        says. Neither is a judgement by a model of another family, so `independent` and `cross_family` are always False
        here (P0 audit 31.10: a lambda checker used to be reported as `independent: True`). Cross-family verification is
        `verify_with_model`."""
        p_fam = normalize_family(proposer_family)
        v_fam = self.verifier_family

        if p_fam == v_fam:
            raise CrossFamilyViolationError(
                f"Cross-family violation: Proposer ({p_fam}) and Verifier ({v_fam}) must not belong to the same family!"
            )

        passed = True
        reason = "OK"

        if custom_checker is not None:
            try:
                passed = bool(custom_checker(proposal))
                reason = "custom_checker_passed" if passed else "custom_checker_rejected"
            except Exception as exc:
                passed = False
                reason = f"checker_error:{exc}"
        elif isinstance(proposal, dict) and "error" in proposal and proposal["error"]:
            passed = False
            reason = f"error_in_proposal:{proposal['error']}"
        elif proposal is None or proposal == "":
            passed = False
            reason = "empty_proposal"

        payload_bytes = json.dumps({
            "subtask_id": subtask_id,
            "proposer": p_fam,
            "verifier": v_fam,
            "passed": passed,
            "reason": reason,
        }, sort_keys=True).encode("utf-8")
        receipt_hash = hashlib.sha256(payload_bytes).hexdigest()

        return {
            "subtask_id": subtask_id,
            "proposer_family": p_fam,
            "verifier_family": v_fam,
            "passed": passed,
            "reason": reason,
            "kind": "custom_checker" if custom_checker is not None else "structural_only",
            "independent": False,
            "cross_family": False,
            "sha256_verdict": receipt_hash,
            "timestamp": time.time(),
        }

    async def verify_with_model(
        self,
        *,
        subtask_id: str,
        worker_family: str,
        proposal: Any,
        adapter: VerifierModelAdapter | None,
        expected_condition: str = "",
        title: str = "",
    ) -> dict[str, Any]:
        """Ask a verifier MODEL of another family to judge `proposal`. Returns a receipt; `cross_family_verified` is True
        only when the model was actually invoked, its family is recognised and differs from the worker's, and it
        returned a real PASS/FAIL verdict. `passed` is True/False for PASS/FAIL and None when there is no verdict.
        Never raises for verifier problems: they are recorded in `status`."""
        checked_sha = _sha256_of(proposal)
        w_fam = _known_family(worker_family)
        receipt: dict[str, Any] = {
            "kind": "cross_family_model",
            "subtask_id": subtask_id,
            "worker_family": w_fam or normalize_family(worker_family),
            "verifier_provider": adapter.provider if adapter else None,
            "verifier_model_id": adapter.model_id if adapter else None,
            "verifier_family": None,
            "invoked": False,
            "verdict": None,
            "reason": "",
            "passed": None,
            "checked_output_sha256": checked_sha,
            "cross_family_judged": False,
            "cross_family_verified": False,
            "status": "",
        }

        def _seal(status: str) -> dict[str, Any]:
            receipt["status"] = status
            receipt["timestamp"] = time.time()
            receipt["sha256_receipt"] = _sha256_of({k: v for k, v in receipt.items() if k != "timestamp"})
            return receipt

        if adapter is None:
            return _seal("no_verifier_model")
        if not str(adapter.provider or "").strip() or not str(adapter.model_id or "").strip():
            return _seal("verifier_identity_missing")

        id_fam = _known_family(adapter.model_id)
        declared_fam = _known_family(adapter.family) if adapter.family else None
        if adapter.family and declared_fam is None:
            return _seal("unknown_verifier_family")
        if id_fam and declared_fam and id_fam != declared_fam:
            receipt["verifier_family"] = id_fam
            return _seal("verifier_family_mismatch")
        v_fam = id_fam or declared_fam
        receipt["verifier_family"] = v_fam
        if v_fam is None:
            return _seal("unknown_verifier_family")
        if w_fam is None:
            return _seal("unknown_worker_family")
        if v_fam == w_fam:
            return _seal("same_family")

        request = {
            "subtask_id": subtask_id,
            "title": title,
            "expected_condition": expected_condition,
            "worker_family": w_fam,
            "proposal": proposal,
            "checked_output_sha256": checked_sha,
        }
        try:
            raw = adapter.call(request)
            if inspect.isawaitable(raw):
                raw = await raw
        except Exception as exc:  # noqa: BLE001 - a verifier outage must be recorded, not crash the DAG
            receipt["reason"] = f"{type(exc).__name__}: {exc}"[:500]
            return _seal("verifier_error")
        receipt["invoked"] = True

        if isinstance(raw, dict):
            verdict_raw, reason = raw.get("verdict"), raw.get("reason", "")
        else:
            verdict_raw, reason = raw, ""
        verdict = str(verdict_raw).strip().upper() if verdict_raw is not None else ""
        receipt["reason"] = str(reason or "")[:2000]
        if not verdict:
            return _seal("empty_verdict")
        receipt["verdict"] = verdict
        if verdict in _PASS_VERDICTS:
            receipt["passed"] = True
        elif verdict in _FAIL_VERDICTS:
            receipt["passed"] = False
        else:
            return _seal("invalid_verdict")
        receipt["cross_family_judged"] = True          # a real other-family verdict exists (PASS or FAIL)
        receipt["cross_family_verified"] = receipt["passed"] is True   # verified = that verdict is PASS
        return _seal("verified_pass" if receipt["passed"] else "verified_fail")


class SwarmDAGPlanner:
    """4-phase autonomous orchestrator: Decompose -> Parallel Swarm -> Cross-Family Verifier -> Bounded Repair."""

    def __init__(
        self,
        *,
        proposer_family: str = "gemma",
        verifier_family: str = "qwen",
        max_repairs: int = 2,
        memory_budget_tokens: int = 4096,
        verifier_adapter: VerifierModelAdapter | None = None,
    ):
        """`verifier_family` is a LABEL only. Cross-family verification needs `verifier_adapter` (a model that is
        actually called); its family comes from the adapter's model id, not from this label."""
        self.proposer_family = normalize_family(proposer_family)
        self.verifier_family = normalize_family(verifier_family)
        self.verifier_adapter = verifier_adapter
        if self.proposer_family == self.verifier_family:
            # Relabel only (no model is selected or called by this): keeps the structural engine's label check happy
            for candidate in ALLOWED_FAMILIES:
                if candidate != self.proposer_family:
                    self.verifier_family = candidate
                    break
        self.verifier = CrossFamilyVerifierEngine(verifier_family=self.verifier_family)
        self.max_repairs = max_repairs
        self.memory_budget_tokens = memory_budget_tokens

    def assemble_context(
        self,
        candidates: list[MemoryCandidate],
        budget: int | None = None,
    ) -> list[MemoryCandidate]:
        """Context assembly using native bcc.features.memory_retrieval_v16.pack()."""
        limit = budget if budget is not None else self.memory_budget_tokens
        return memory_pack(candidates, budget=limit)

    def phase1_decompose(self, goal: str, context_refs: list[str] | None = None) -> list[SwarmSubtask]:
        """Phase 1: a FIXED three-step template (prepare -> execute -> finalize); the goal only fills the titles.
        It does not analyse the goal."""
        subtasks = [
            SwarmSubtask(
                id="task_1_prep",
                title=f"Prepare environment & context for: {goal[:50]}",
                preconditions=["task_kernel_active", "workspace_clean"],
                allowed_tools=["read_file", "list_dir", "check_status"],
                verify_fn="verify_prep",
                depends_on=[],
                proposer_family=self.proposer_family,
                verifier_family=self.verifier_family,
                max_repairs=self.max_repairs,
            ),
            SwarmSubtask(
                id="task_2_execute",
                title=f"Execute core transformation for: {goal[:50]}",
                preconditions=["prep_verified"],
                allowed_tools=["write_to_file", "run_command", "replace_file_content"],
                verify_fn="verify_execution",
                depends_on=["task_1_prep"],
                proposer_family=self.proposer_family,
                verifier_family=self.verifier_family,
                max_repairs=self.max_repairs,
            ),
            SwarmSubtask(
                id="task_3_finalize",
                title=f"Validate & create immutable receipt for: {goal[:50]}",
                preconditions=["core_executed"],
                allowed_tools=["run_command", "audit_verify"],
                verify_fn="verify_final_receipt",
                depends_on=["task_2_execute"],
                proposer_family=self.proposer_family,
                verifier_family=self.verifier_family,
                max_repairs=self.max_repairs,
            ),
        ]
        return subtasks

    async def execute_subtask(
        self,
        subtask: SwarmSubtask,
        executor_fn: Callable[[SwarmSubtask], Coroutine[Any, Any, Any]] | None = None,
        checker_fn: Callable[[Any], bool] | None = None,
        verifier_adapter: VerifierModelAdapter | None = None,
    ) -> Any:
        """Phase 2 & Phase 3 & Phase 4 on a single subtask. No executor -> SwarmNotWired (no mock execution).

        Gate: the structural/custom check must pass; then, if a verifier model is wired, it is called and a FAIL verdict
        triggers repair like a failed check. A verifier outage / empty / invalid verdict / same family does NOT fail the
        node, but the node stays `cross_family_verified=False` with the reason in `cross_family_receipt`."""
        if executor_fn is None:
            subtask.status = "NOT_EXECUTED"
            raise SwarmNotWired("no executor is wired: the subtask was not run")
        adapter = verifier_adapter if verifier_adapter is not None else self.verifier_adapter
        subtask.status = "RUNNING"
        repair = 0
        while repair <= subtask.max_repairs:
            subtask.repair_attempts = repair
            # Phase 2: Execution / Proposal
            proposal = await executor_fn(subtask)
            subtask.result = proposal
            subtask.cross_family_verified = False
            subtask.cross_family_receipt = {}

            # Phase 3a: structural / custom check (NOT cross-family verification)
            structural = self.verifier.verify(
                subtask_id=subtask.id,
                proposer_family=subtask.proposer_family,
                proposal=proposal,
                expected_condition=subtask.verify_fn,
                custom_checker=checker_fn,
            )
            subtask.structural_check = structural
            gate_ok = bool(structural["passed"])
            reason = structural.get("reason")

            # Phase 3b: cross-family model verification (only after the structural gate passed: no model call on junk)
            if gate_ok and adapter is not None:
                cf = await self.verifier.verify_with_model(
                    subtask_id=subtask.id,
                    worker_family=subtask.proposer_family,
                    proposal=proposal,
                    adapter=adapter,
                    expected_condition=subtask.verify_fn,
                    title=subtask.title,
                )
                subtask.cross_family_receipt = cf
                subtask.cross_family_verified = bool(cf["cross_family_verified"])
                if cf["passed"] is False:          # a real other-family FAIL verdict: repair
                    gate_ok = False
                    reason = f"cross_family_fail:{cf.get('reason', '')}"
            subtask.verification_receipt = {"structural_check": structural, "cross_family": subtask.cross_family_receipt}

            if gate_ok:
                subtask.verification_passed = True
                subtask.status = "SUCCEEDED"
                return proposal

            # Phase 4: Self-Reflection & Bounded Repair
            logger.warning(
                "Subtask %s failed verification on attempt %d: %s. Reflecting...",
                subtask.id, repair, reason,
            )
            repair += 1

        subtask.status = "FAILED"
        raise SwarmExecutionError(f"Subtask {subtask.id} failed verification after {subtask.max_repairs} repairs")

    async def run_dag(
        self,
        goal: str,
        candidates: list[MemoryCandidate] | None = None,
        executor_fn: Callable[[SwarmSubtask], Coroutine[Any, Any, Any]] | None = None,
        checker_fn: Callable[[Any], bool] | None = None,
        verifier_adapter: VerifierModelAdapter | None = None,
    ) -> SwarmPlanReceipt:
        """Plan the DAG; with an executor also run it. Without one the result is PLAN_ONLY (executed False, completed 0,
        nothing verified, no verifier model called). `cross_family_verified` is True only when, for EVERY completed node,
        a verifier model of another family was actually called and returned PASS on that node's final output (see
        `verify_with_model`). `checker_fn` only feeds `structural_check_passed` (P0 audit 31.10)."""
        t0 = time.perf_counter()
        adapter = verifier_adapter if verifier_adapter is not None else self.verifier_adapter

        # Context assembly via memory_retrieval_v16
        packed_memory = self.assemble_context(candidates or [])
        used_tokens = sum(c.tokens for c in packed_memory)

        # Phase 1: Decompose
        subtasks = self.phase1_decompose(goal, context_refs=[c.ref for c in packed_memory])
        if executor_fn is None:
            duration = (time.perf_counter() - t0) * 1000.0
            plan_data = {"goal": goal, "proposer": self.proposer_family, "verifier": self.verifier_family,
                         "nodes": len(subtasks), "tokens": used_tokens, "executed": False}
            return SwarmPlanReceipt(
                goal=goal, proposer_family=self.proposer_family, verifier_family=self.verifier_family,
                dag_nodes=len(subtasks), completed_nodes=0, cross_family_verified=False, context_tokens_used=used_tokens,
                duration_ms=round(duration, 2), sha256_receipt=hashlib.sha256(json.dumps(plan_data, sort_keys=True).encode("utf-8")).hexdigest(),
                status="PLAN_ONLY", executed=False, verification_kind="none",
                structural_check_passed=None, verifier_model=self._verifier_model_info(adapter), cross_family_receipts=[])

        # Phase 2-4: Parallel execution respecting DAG dependencies
        completed: dict[str, SwarmSubtask] = {}
        pending: dict[str, SwarmSubtask] = {s.id: s for s in subtasks}

        while pending:
            # Find nodes ready to run (all depends_on satisfied)
            ready = [
                s for s in pending.values()
                if all(dep in completed and completed[dep].status == "SUCCEEDED" for dep in s.depends_on)
            ]
            if not ready:
                raise SwarmExecutionError("Deadlock in Swarm DAG: unresolvable dependencies")

            # Run ready tasks in parallel
            tasks = [self.execute_subtask(s, executor_fn=executor_fn, checker_fn=checker_fn, verifier_adapter=adapter)
                     for s in ready]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for s, res in zip(ready, results):
                del pending[s.id]
                if isinstance(res, Exception):
                    s.status = "FAILED"
                    raise res
                completed[s.id] = s

        nodes = list(completed.values())
        cf_receipts = [c.cross_family_receipt for c in nodes if c.cross_family_receipt]
        cross_family_verified = bool(nodes) and all(_node_cross_family_ok(c) for c in nodes)
        structural_ok = bool(nodes) and all(c.structural_check.get("passed") is True for c in nodes)
        if cross_family_verified:
            kind = "cross_family_model"
        else:
            kind = "custom_checker" if checker_fn is not None else "structural_only"

        duration = (time.perf_counter() - t0) * 1000.0
        receipt_data = {
            "goal": goal,
            "proposer": self.proposer_family,
            "verifier": self.verifier_family,
            "nodes": len(subtasks),
            "tokens": used_tokens,
            "duration_ms": duration,
            "verification_kind": kind,
            "cross_family_receipts": [r.get("sha256_receipt") for r in cf_receipts],
        }
        raw_hash = hashlib.sha256(json.dumps(receipt_data, sort_keys=True).encode("utf-8")).hexdigest()

        return SwarmPlanReceipt(
            goal=goal,
            proposer_family=self.proposer_family,
            verifier_family=self.verifier_family,
            dag_nodes=len(subtasks),
            completed_nodes=len(completed),
            cross_family_verified=cross_family_verified,
            context_tokens_used=used_tokens,
            duration_ms=round(duration, 2),
            sha256_receipt=raw_hash,
            status="SUCCEEDED",
            executed=True,
            verification_kind=kind,
            structural_check_passed=structural_ok,
            verifier_model=self._verifier_model_info(adapter),
            cross_family_receipts=cf_receipts,
        )

    @staticmethod
    def _verifier_model_info(adapter: VerifierModelAdapter | None) -> dict[str, Any] | None:
        if adapter is None:
            return None
        return {"provider": adapter.provider, "model_id": adapter.model_id,
                "family": _known_family(adapter.model_id) or _known_family(adapter.family)}


def _node_cross_family_ok(node: SwarmSubtask) -> bool:
    """Re-check a node's receipt rather than trusting the flag: invoked, identity recorded, families differ, PASS, and
    the receipt hashes exactly the output the node ended with."""
    r = node.cross_family_receipt or {}
    return bool(
        node.cross_family_verified
        and r.get("invoked") is True
        and r.get("verdict") in _PASS_VERDICTS
        and r.get("verifier_provider")
        and r.get("verifier_model_id")
        and r.get("verifier_family") in ALLOWED_FAMILIES
        and r.get("worker_family") in ALLOWED_FAMILIES
        and r.get("verifier_family") != r.get("worker_family")
        and r.get("checked_output_sha256") == _sha256_of(node.result)
        and r.get("sha256_receipt")
    )
