"""Technical sanctions for teacher output (automatic, not verbal), scoped
reliability scores and a circuit breaker on repeated identical errors.

Statuses come from teacher.PatchVerifier (TeacherVerdict.status); this module
turns them into enforced consequences: rollback / learning block / retry budget
/ owner approval / restored tests / adversarial regression entry / reliability
delta / stop. One failure is not a permanent blacklist (scores are windowed);
one success is not unconditional trust (bounded increments)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ._bootstrap import trace
from .models import sha

DEFAULT_SCORE = 0.5
TRUST_THRESHOLD = 0.6
DELTAS = {"TEACHER_OUTPUT_ACCEPTED": +0.10, "TEACHER_OUTPUT_REJECTED": -0.10,
          "TEACHER_OUTPUT_QUARANTINED": -0.25, "ACCEPTANCE_TAMPERING": -0.40}
MAX_CORRECTIVE_RETRIES = 1
WINDOW_S = 7 * 24 * 3600


class SanctionKind(str, Enum):
    NONE = "NONE"; TEACHER_OUTPUT_REJECTED = "TEACHER_OUTPUT_REJECTED"
    TEACHER_OUTPUT_QUARANTINED = "TEACHER_OUTPUT_QUARANTINED"; ACCEPTANCE_TAMPERING = "ACCEPTANCE_TAMPERING"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"


@dataclass(frozen=True, slots=True)
class SanctionDecision:
    kind: str
    stop: bool
    retry_allowed: bool
    learning_blocked: bool
    owner_approval_required: bool
    rollback: bool
    tests_restored: bool
    reliability_delta: float
    critique: str
    report: str
    reason: str
    violation_type: str = ""
    adversarial_entry: dict | None = None


@dataclass(frozen=True, slots=True)
class ReliabilityKey:
    model_id: str
    model_version: str
    task_type: str
    repository: str
    window: str

    @classmethod
    def make(cls, *, model_id: str, model_version: str, task_type: str, repository: str, now: float,
             window_s: int = WINDOW_S) -> "ReliabilityKey":
        return cls(model_id, model_version, task_type, repository, f"w{int(now // window_s)}")


class ReliabilityLedger:
    """Score in [0, 1] per (model, version, task type, repository, time window)."""

    def __init__(self) -> None:
        self._scores: dict[ReliabilityKey, tuple[float, int]] = {}

    def get(self, key: ReliabilityKey) -> float:
        return self._scores.get(key, (DEFAULT_SCORE, 0))[0]

    def samples(self, key: ReliabilityKey) -> int:
        return self._scores.get(key, (DEFAULT_SCORE, 0))[1]

    def update(self, key: ReliabilityKey, delta: float) -> float:
        score, n = self._scores.get(key, (DEFAULT_SCORE, 0))
        score = max(0.0, min(1.0, round(score + delta, 4)))
        self._scores[key] = (score, n + 1)
        return score

    def trusted(self, key: ReliabilityKey) -> bool:
        return self.get(key) >= TRUST_THRESHOLD

    def as_dict(self) -> dict:
        return {f"{k.model_id}@{k.model_version}|{k.task_type}|{k.repository}|{k.window}": {"score": s, "n": n}
                for k, (s, n) in self._scores.items()}


class CircuitBreaker:
    """Opens when the same error signature repeats `max_repeats` times in this run."""

    def __init__(self, max_repeats: int = 2) -> None:
        self.max_repeats = max_repeats
        self._streak: list[str] = []
        self._open_reason = ""

    def record(self, signature: str) -> bool:
        if not signature:
            self._streak.clear(); return False
        if self._streak and self._streak[-1] != signature:
            self._streak.clear()
        self._streak.append(signature)
        if len(self._streak) >= self.max_repeats:
            self._open_reason = f"same error repeated {len(self._streak)}x: {signature[:80]}"
        return self.is_open()

    def is_open(self) -> bool:
        return bool(self._open_reason)

    def reset(self) -> None:
        self._streak.clear(); self._open_reason = ""

    def report(self) -> str:
        return ("circuit breaker open — no new teacher calls and no further spend in this run. "
                f"{self._open_reason}. Options for the owner: (1) inspect the failing evidence bundle, "
                "(2) narrow the scope / bundle and start a new run, (3) fix manually, (4) explicitly re-authorize "
                "one more attempt with a higher budget.")


class AdversarialRegister:
    """Adversarial regression entries (acceptance tampering, quarantines) — no secrets."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def add(self, entry: dict) -> dict:
        tr = trace()
        e = tr.redact_obj(dict(entry))
        if any(tr.has_secret(s) for s in tr._walk_strings(e)):
            e = {"violation_type": e.get("violation_type", "unknown"), "note": "details withheld (secret-like content)"}
        self.entries.append(e)
        return e


def error_signature(verdict: Any) -> str:
    """Stable signature of a failure: status + failing tests / first reason class, not attempt numbers."""
    status = str(getattr(verdict, "status", ""))
    if status == "TEACHER_OUTPUT_ACCEPTED":
        return ""
    reasons = [r for r in getattr(verdict, "reasons", []) if not r.startswith("teacher claimed")]
    core = reasons[-1] if reasons else status
    return sha(status, core.split(";")[0][:120])[:16]


class SanctionEngine:
    def __init__(self, *, reliability: ReliabilityLedger | None = None, breaker: CircuitBreaker | None = None,
                 register: AdversarialRegister | None = None) -> None:
        self.reliability = reliability or ReliabilityLedger()
        self.breaker = breaker or CircuitBreaker()
        self.register = register or AdversarialRegister()
        self.quarantined_traces: set[str] = set()
        self.log: list[SanctionDecision] = []

    def promotion_forbidden(self, trace_id: str) -> bool:
        return trace_id in self.quarantined_traces

    def apply(self, verdict: Any, *, model_id: str, model_version: str, task_type: str, repository: str,
              now: float | None = None, trace_ids: tuple[str, ...] = ()) -> SanctionDecision:
        now = time.time() if now is None else now
        key = ReliabilityKey.make(model_id=model_id, model_version=model_version, task_type=task_type, repository=repository, now=now)
        status = str(verdict.status)
        delta = DELTAS.get(status, 0.0)
        score = self.reliability.update(key, delta)
        critique = str(getattr(verdict, "critique", "") or "")
        attempt = int(getattr(verdict, "attempt", 1) or 1)
        violation = str(getattr(verdict, "violation_type", "") or "")
        sig = error_signature(verdict)
        opened = self.breaker.record(sig)
        if status == "TEACHER_OUTPUT_ACCEPTED":
            d = SanctionDecision(SanctionKind.NONE.value, False, False, False, False, False, False, delta, "",
                                 f"accepted; reliability {score:.2f}", "accepted")
        elif status == "ACCEPTANCE_TAMPERING":
            entry = self.register.add({"violation_type": "acceptance_tampering", "model_id": model_id, "model_version": model_version,
                                       "task_type": task_type, "repository": repository, "at": now,
                                       "reasons": [str(r)[:200] for r in getattr(verdict, "reasons", [])][:5]})
            self.quarantined_traces.update(trace_ids)
            d = SanctionDecision(SanctionKind.ACCEPTANCE_TAMPERING.value, True, False, True, False, True, True, delta, critique,
                                 f"acceptance tampering: rejected entirely, tests restored, adversarial regression recorded; reliability {score:.2f}",
                                 "acceptance tampering", "acceptance_tampering", entry)
        elif status == "TEACHER_OUTPUT_QUARANTINED":
            self.quarantined_traces.update(trace_ids)
            entry = self.register.add({"violation_type": violation or "security_regression", "model_id": model_id,
                                       "model_version": model_version, "task_type": task_type, "repository": repository, "at": now})
            d = SanctionDecision(SanctionKind.TEACHER_OUTPUT_QUARANTINED.value, True, False, True, True, True, False, delta, critique,
                                 f"security regression ({violation or 'unspecified'}): patch application stopped, related traces barred "
                                 f"from promotion, owner approval required to continue; reliability {score:.2f}",
                                 "security regression", violation or "security_regression", entry)
        elif status == "TEACHER_OUTPUT_REJECTED":
            if opened:
                d = SanctionDecision(SanctionKind.CIRCUIT_BREAKER.value, True, False, True, False, True, False, delta, critique,
                                     self.breaker.report(), "circuit breaker open")
            else:
                retry = attempt <= MAX_CORRECTIVE_RETRIES
                d = SanctionDecision(SanctionKind.TEACHER_OUTPUT_REJECTED.value, False, retry, True, False, True, False, delta, critique,
                                     f"rejected: rolled back this attempt, failing evidence kept, {'one corrective retry allowed' if retry else 'retry budget exhausted'}; "
                                     f"reliability {score:.2f}", "tests failing")
        else:
            d = SanctionDecision(SanctionKind.NONE.value, False, False, True, False, False, False, 0.0, critique,
                                 f"untrusted output ({status}); nothing learned", status)
        self.log.append(d)
        return d
