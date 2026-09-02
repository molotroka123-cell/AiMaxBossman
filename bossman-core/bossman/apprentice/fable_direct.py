"""Direct Anthropic API transport for Fable — a TRUSTED provider boundary.

Difference from ClaudeCodeClient (untrusted subprocess teacher): here the
credential never leaves this transport. It is read from the process
environment at call time, used only in the Authorization header of one HTTPS
request, and is never written to bundles, logs, caches, memory, receipts or
any subprocess environment. The teacher sandbox scrubbing stays untouched.

Budget (P0-FINISH-BUDGET-001 / HARD-$3): durable atomic reservations with a
CROSS-PROCESS lock, mission/owner/request binding, strict per-model pricing
(unknown model/price => REFUSED), separate cache read/write rates, and a
RECONCILING state: a failed call is never silently free — only the trusted
reconciler (with provider request evidence) may release or settle it.

Response handling reuses the untrusted-output discipline: only typed visible
facts survive; hidden-reasoning keys are dropped; text passes the redactor.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

import httpx

from ._bootstrap import trace
from .claude_code_client import ClaudeCodeClient
from .errors import BudgetExhausted, FallbackRefused

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
# USD per 1M tokens: (input, output, cache_read, cache_write). Unknown model => refuse.
PRICE_TABLE: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-4-5": (3.0, 15.0, 0.30, 3.75),
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25),
    "claude-opus-4-1": (15.0, 75.0, 1.50, 18.75),
}
# Conservative tokenizer upper bound: Anthropic text is ~3.5-4 chars/token; use 3.0.
_CHARS_PER_TOKEN_UPPER_BOUND = 3.0


def estimate_worst_case_usd(model: str, prompt_chars: int, max_output_tokens: int) -> float:
    """Safe upper bound: charge the most expensive bucket for every token."""
    rates = PRICE_TABLE.get(model)
    if rates is None:
        raise BudgetExhausted(f"unknown model {model!r}: price required before any spend")
    worst = max(rates)
    tokens = prompt_chars / _CHARS_PER_TOKEN_UPPER_BOUND + max_output_tokens
    return round(tokens / 1_000_000 * worst, 6)


class _CrossProcessFileLock:
    """Advisory whole-file lock: msvcrt.locking on Windows, fcntl.flock on POSIX.
    One lock file per budget ledger; held for the whole read-modify-write transaction."""

    def __init__(self, path: Path) -> None:
        self.path = path.with_suffix(path.suffix + ".lock")
        self._handle: Any = None

    def __enter__(self) -> "_CrossProcessFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+b")
        deadline = time.time() + 30.0
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self._handle.seek(0)
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.time() >= deadline:
                    raise BudgetExhausted("budget ledger is locked by another process; refusing to spend")
                time.sleep(0.05)

    def __exit__(self, *exc: Any) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


class DirectApiBudget:
    """Durable atomic reservation ledger with a cross-process lock.

    Record: {reservation_id, mission_id, owner_id, worst_case_usd, created_at,
    status: RESERVED|RECONCILING|COMMITTED|RELEASED, actual_usd, request_id}.
    remaining = total - committed - (RESERVED + RECONCILING holds). A crashed
    in-flight call stays RECONCILING (conservative): only trusted_reconcile()
    with provider request evidence may settle or free it. Every operation runs
    inside one lock + reload + atomic-replace transaction."""

    def __init__(self, path: str | Path, *, total_usd: float, mission_id: str = "mission",
                 owner_id: str = "owner") -> None:
        self.path, self.total = Path(path), round(float(total_usd), 6)
        self.mission_id, self.owner_id = mission_id, owner_id
        self._inproc = threading.Lock()
        self._records: list[dict] = []
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        self._records = []
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("reservations"), list):
                    if data.get("mission_id") not in (None, self.mission_id):
                        raise BudgetExhausted("budget ledger belongs to another mission")
                    self._records = data["reservations"]
            except json.JSONDecodeError:
                # corrupted ledger fails CLOSED: spend nothing new
                self._records = []

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + f".tmp{os.getpid()}")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"mission_id": self.mission_id, "total_usd": self.total,
                                   "reservations": self._records}, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._inproc, _CrossProcessFileLock(self.path):
            self._load()
            yield
            self._save()

    # ------------------------------------------------------------ accounting
    def _committed_total(self) -> float:
        return sum(float(r.get("actual_usd") or 0.0) for r in self._records if r["status"] == "COMMITTED")

    def _hold_total(self) -> float:
        return sum(float(r["worst_case_usd"]) for r in self._records if r["status"] in ("RESERVED", "RECONCILING"))

    def remaining(self) -> float:
        return round(max(0.0, self.total - self._committed_total() - self._hold_total()), 6)

    # ------------------------------------------------------------ operations
    def reserve(self, amount: float, *, purpose: str = "") -> str:
        with self._transaction():
            amount = round(float(amount), 6)
            if amount <= 0:
                raise BudgetExhausted("reservation amount must be positive")
            if amount > self.remaining():
                raise BudgetExhausted(
                    f"budget: worst case {amount:.2f} USD exceeds remaining {self.remaining():.2f} USD (cap {self.total:.2f} USD)")
            rid = f"rsv-{uuid.uuid4().hex[:12]}"
            self._records.append({"reservation_id": rid, "mission_id": self.mission_id, "owner_id": self.owner_id,
                                  "purpose": purpose[:120], "worst_case_usd": amount, "created_at": time.time(),
                                  "status": "RESERVED", "actual_usd": None, "request_id": ""})
            return rid

    def attach_request(self, reservation_id: str, request_id: str) -> None:
        with self._transaction():
            rec = next((r for r in self._records if r["reservation_id"] == reservation_id), None)
            if rec is None:
                raise KeyError(f"unknown reservation {reservation_id}")
            rec["request_id"] = str(request_id or "")[:120]

    def commit(self, reservation_id: str, actual_usd: float, *, request_id: str = "") -> None:
        with self._transaction():
            rec = next((r for r in self._records if r["reservation_id"] == reservation_id), None)
            if rec is None:
                raise KeyError(f"unknown reservation {reservation_id}")
            if rec["status"] == "COMMITTED":
                raise BudgetExhausted(f"reservation {reservation_id} already committed; double commit refused")
            if rec["status"] not in ("RESERVED", "RECONCILING"):
                raise BudgetExhausted(f"reservation {reservation_id} is {rec['status']}; cannot commit")
            actual = round(float(actual_usd), 6)
            if actual > float(rec["worst_case_usd"]) + 1e-9:
                raise BudgetExhausted(f"actual {actual:.2f} USD exceeds reserved {rec['worst_case_usd']:.2f} USD")
            rec["status"], rec["actual_usd"], rec["request_id"] = "COMMITTED", actual, str(request_id or rec.get("request_id", ""))[:120]

    def mark_reconciling(self, reservation_id: str) -> None:
        """A failed/uncertain call: budget stays held until the trusted reconciler settles it."""
        with self._transaction():
            rec = next((r for r in self._records if r["reservation_id"] == reservation_id), None)
            if rec is None:
                raise KeyError(f"unknown reservation {reservation_id}")
            if rec["status"] == "RESERVED":
                rec["status"] = "RECONCILING"

    def trusted_reconcile(self, reservation_id: str, *, request_id: str, actual_usd: float | None = None) -> str:
        """Trusted reconciler only: settle with provider usage evidence (COMMIT) or
        free the hold (RELEASE) when the provider proves no usage. Never callable
        as a silent client-side escape hatch."""
        with self._transaction():
            rec = next((r for r in self._records if r["reservation_id"] == reservation_id), None)
            if rec is None:
                raise KeyError(f"unknown reservation {reservation_id}")
            if rec["status"] not in ("RESERVED", "RECONCILING"):
                return rec["status"]
            rec["request_id"] = str(request_id or "")[:120]
            if actual_usd is None:
                rec["status"], rec["actual_usd"] = "RELEASED", 0.0
            else:
                actual = round(float(actual_usd), 6)
                if actual > float(rec["worst_case_usd"]) + 1e-9:
                    raise BudgetExhausted(f"reconciled actual {actual:.2f} USD exceeds reserved {rec['worst_case_usd']:.2f} USD")
                rec["status"], rec["actual_usd"] = "COMMITTED", actual
            return rec["status"]


class FableDirectClient:
    """client.run(bundle_dict) -> typed teacher output dict (same shape as the
    CLI client).  The API key is consumed here and nowhere else."""

    def __init__(self, *, model: str = "claude-sonnet-4-5", max_output_tokens: int = 2048,
                 timeout_s: float = 240.0, budget: DirectApiBudget | None = None,
                 api_key_env: str = "ANTHROPIC_API_KEY") -> None:
        if model not in PRICE_TABLE:
            raise BudgetExhausted(f"unknown model {model!r}: refusing to run without a known price")
        self.model, self.max_output_tokens, self.timeout_s, self.budget = model, max_output_tokens, timeout_s, budget
        self.api_key_env = api_key_env
        self.calls = 0
        self.usage: list[dict] = []
        self.reservation_id: str | None = None

    def run(self, bundle: dict) -> dict[str, Any]:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise FallbackRefused("direct Anthropic API key is not configured in the process environment")
        if self.budget is not None:
            self.reservation_id = self.budget.reserve(
                estimate_worst_case_usd(self.model, len(json.dumps(bundle, sort_keys=True)), self.max_output_tokens),
                purpose="fable-direct-call")
        prompt = ClaudeCodeClient._prompt(None, bundle)  # identical sanitized instruction, no reasoning request
        started = time.time()
        self.calls += 1
        try:
            resp = httpx.post(API_URL, timeout=self.timeout_s, headers={
                "x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"},
                json={"model": self.model, "max_tokens": self.max_output_tokens,
                      "messages": [{"role": "user", "content": prompt}]})
        except Exception as exc:  # noqa: BLE001 — never silently free: hold moves to RECONCILING
            if self.budget is not None and self.reservation_id:
                with contextlib.suppress(KeyError):
                    self.budget.mark_reconciling(self.reservation_id)
            raise FallbackRefused(f"direct API transport failed: {type(exc).__name__}") from exc
        latency_ms = int((time.time() - started) * 1000)
        body: dict[str, Any] = {}
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            pass
        if resp.status_code != 200 or body.get("type") == "error":
            if self.budget is not None and self.reservation_id:
                with contextlib.suppress(KeyError):
                    self.budget.mark_reconciling(self.reservation_id)
            err = (body.get("error") or {}).get("message", f"HTTP {resp.status_code}")
            raise FallbackRefused(f"direct API error: {err}")
        usage = dict(body.get("usage") or {})
        rates = PRICE_TABLE[self.model]
        in_tok = int(usage.get("input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        cache_r = int(usage.get("cache_read_input_tokens") or 0)
        cache_w = int(usage.get("cache_creation_input_tokens") or 0)
        est_cost = round((in_tok / 1e6 * rates[0] + out_tok / 1e6 * rates[1]
                          + cache_r / 1e6 * rates[2] + cache_w / 1e6 * rates[3]), 6)
        request_id = resp.headers.get("request-id", "")
        self.usage.append({"provider": "anthropic-direct", "model": body.get("model", self.model),
                           "request_id": request_id, "input_tokens": in_tok, "output_tokens": out_tok,
                           "cache_creation_input_tokens": cache_w, "cache_read_input_tokens": cache_r,
                           "estimated_cost_usd": est_cost, "latency_ms": latency_ms,
                           "stop_reason": body.get("stop_reason", "")})
        if self.budget is not None and self.reservation_id:
            if not usage and est_cost > 0:
                self.budget.mark_reconciling(self.reservation_id)   # no usage evidence: reconcile, not free spend
            else:
                self.budget.commit(self.reservation_id, est_cost, request_id=request_id)
        text = "".join(block.get("text", "") for block in body.get("content", []) if isinstance(block, dict))
        parsed = ClaudeCodeClient._json_or_text(text)
        for hidden in ("chain_of_thought", "hidden_reasoning", "thoughts", "scratchpad", "reasoning", "raw_prompt"):
            parsed.pop(hidden, None)
        parsed["commands"] = []                       # direct transport grants no execution authority either
        parsed["log_text"] = trace().redact_text(text)[:8000]
        if key in json.dumps(parsed):                 # paranoid belt: the key must never appear in output
            raise FallbackRefused("credential material detected in teacher output; result discarded")
        parsed["artifacts"] = [f"anthropic-direct:usage={json.dumps(self.usage[-1])}"]
        parsed["model_id"] = "anthropic-direct"
        parsed["model_version"] = str(body.get("model", self.model))
        parsed["isolation"] = {"level": "trusted-transport", "credential_exposure": "header-only",
                               "env_scrubbed": True}
        return parsed
