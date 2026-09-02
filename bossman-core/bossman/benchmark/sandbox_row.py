"""Trusted row contract for REAL_SANDBOX benchmark cases.

A case module NEVER decides whether it passed.  It records *observed facts*
through :class:`CaseProbe` — each fact is a named (actual, expected) pair — and
the verdict is computed here, by a module the case does not control, and then
recomputed a second time by :func:`verify_row` at the runtime boundary before
the row leaves the process.  A case that tries to set ``verified`` directly is
overwritten.

Coverage rules enforced by the trusted verifier (not by the case):

* at least one POSITIVE check — the production path really did the work;
* at least one NEGATIVE check — the production path really refused hostile or
  invalid input.  A case that only proves the happy path is UNVERIFIED, so a
  capability can never be "covered" by success cases alone;
* every recorded check must hold.

Cost/token fields are only allowed when provider evidence is attached; there is
no paid call on any REAL_SANDBOX path, so they stay 0 and ``provider_evidence``
stays False.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

CONTRACT = "bossman-sandbox-runtime/v2"
KIND_POSITIVE = "positive"
KIND_NEGATIVE = "negative"


@dataclass(slots=True)
class Check:
    name: str
    kind: str
    actual: Any
    expected: Any

    @property
    def ok(self) -> bool:
        return self.actual == self.expected

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "ok": self.ok,
                "actual": _plain(self.actual), "expected": _plain(self.expected)}


def _plain(value: Any) -> Any:
    """JSON-safe projection; keeps the evidence readable in the report."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return repr(value)[:200]


@dataclass
class CaseProbe:
    """Accumulates observed facts for one REAL_SANDBOX case.

    ``positive``/``negative`` record what the *production code* actually
    returned or raised.  Nothing here interprets a claim made by the code under
    test: the expected value is written by the benchmark author and compared by
    this module.
    """

    case_id: str
    capability: str
    seed: int
    checks: list[Check] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    _started: float = field(default_factory=time.monotonic)

    # ---------------------------------------------------------------- record
    def positive(self, name: str, actual: Any, expected: Any = True) -> "CaseProbe":
        self.checks.append(Check(name, KIND_POSITIVE, actual, expected))
        return self

    def negative(self, name: str, actual: Any, expected: Any = True) -> "CaseProbe":
        """A refusal/rejection observed from the real production path."""
        self.checks.append(Check(name, KIND_NEGATIVE, actual, expected))
        return self

    def refused(self, name: str, callable_, *exc_types: type[BaseException], contains: str = "") -> "CaseProbe":
        """Run ``callable_`` and record that production refused it as expected.

        The recorded fact is the *exception class name* plus whether the
        message carries ``contains`` — so a silently-swallowed error or a
        different failure mode is a FAILED check, not a pass.
        """
        try:
            callable_()
        except exc_types as exc:  # type: ignore[misc]
            got = type(exc).__name__
            text = str(exc)
            ok_text = (contains.lower() in text.lower()) if contains else True
            return self.negative(name, {"raised": got, "message_matches": ok_text},
                                 {"raised": got, "message_matches": True})
        except Exception as exc:  # noqa: BLE001 — wrong exception type is a real failure
            return self.negative(name, {"raised": type(exc).__name__, "message_matches": False},
                                 {"raised": _names(exc_types), "message_matches": True})
        return self.negative(name, {"raised": None, "message_matches": False},
                             {"raised": _names(exc_types), "message_matches": True})

    def count(self, **counters: int) -> "CaseProbe":
        for key, value in counters.items():
            self.counters[key] = self.counters.get(key, 0) + int(value)
        return self

    def tag(self, *tags: str) -> "CaseProbe":
        self.tags.extend(tags)
        return self

    # ---------------------------------------------------------------- finish
    def finish(self, **extra: Any) -> dict[str, Any]:
        """Build the row.  ``verified`` is computed here, never supplied."""
        extra.pop("verified", None)                    # a case cannot self-report success
        latency_ms = round((time.monotonic() - self._started) * 1000, 3)
        row: dict[str, Any] = {
            # `claimed_capability` is what the case says it covers; the runner
            # compares it with the manifest and refuses a mismatch, so coverage
            # can never be self-awarded.
            "case_id": self.case_id, "capability": self.capability, "claimed_capability": self.capability,
            "seed": self.seed,
            "mode": "REAL_SANDBOX", "contract": CONTRACT, "training_eligible": False,
            "checks": [c.as_dict() for c in self.checks],
            "evidence": [c.name for c in self.checks if c.ok] + [f"failed:{c.name}" for c in self.checks if not c.ok],
            "tags": list(self.tags),
            "latency_ms": latency_ms, "wall_clock_source": "time.monotonic",
            "effects": 0, "duplicate_effects": 0, "actions": 0, "refused": 0, "recoveries": 0,
            "tokens_in": 0, "tokens_out": 0, "cache_reads": 0, "cache_writes": 0, "cache_hits": 0,
            "estimated_cost_usd": 0.0, "provider_evidence": False,
        }
        row.update(self.counters)
        row.update(extra)
        row["actions"] = row.get("actions") or sum(1 for c in self.checks if c.kind == KIND_POSITIVE)
        row["refused"] = row.get("refused") or sum(1 for c in self.checks if c.kind == KIND_NEGATIVE and c.ok)
        return verify_row(row)


def _names(exc_types: tuple[type[BaseException], ...]) -> str:
    return "|".join(t.__name__ for t in exc_types) or "Exception"


def verify_row(row: dict[str, Any]) -> dict[str, Any]:
    """Independent verdict: recomputed from the recorded checks alone.

    Called by :class:`CaseProbe.finish` and AGAIN by the runtime entrypoint, so
    a case that mutates ``verified`` after ``finish()`` is still corrected.
    """
    checks = row.get("checks") or []
    positives = [c for c in checks if c.get("kind") == KIND_POSITIVE]
    negatives = [c for c in checks if c.get("kind") == KIND_NEGATIVE]
    failed = [c["name"] for c in checks if not c.get("ok")]
    reasons: list[str] = []
    if not checks:
        reasons.append("no observed checks: a case cannot be verified by assertion-free execution")
    if not positives:
        reasons.append("no positive check: capability was never exercised")
    if not negatives:
        reasons.append("no negative check: refusal path unproven, happy-path-only coverage is refused")
    if failed:
        reasons.append("failed checks: " + ", ".join(sorted(failed)))
    if row.get("provider_evidence") is not True and (int(row.get("tokens_in", 0)) or int(row.get("tokens_out", 0))
                                                     or float(row.get("estimated_cost_usd", 0.0))):
        reasons.append("token/cost reported without provider evidence")
    row["verified"] = not reasons
    row["verification_reasons"] = reasons
    row["verified_by"] = "bossman.benchmark.sandbox_row.verify_row"
    row["coverage"] = {"positive": len(positives), "negative": len(negatives), "failed": len(failed)}
    return row
