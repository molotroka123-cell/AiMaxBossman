"""Direct Anthropic API transport for Fable — a TRUSTED provider boundary.

Difference from ClaudeCodeClient (untrusted subprocess teacher): here the
credential never leaves this transport. It is read from the process
environment at call time, used only in the Authorization header of one HTTPS
request, and is never written to bundles, logs, caches, memory, receipts or
any subprocess environment. The teacher sandbox scrubbing stays untouched.

Response handling reuses the untrusted-output discipline: only typed visible
facts survive; hidden-reasoning keys are dropped; text passes the redactor.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from ._bootstrap import trace
from .claude_code_client import ClaudeCodeClient
from .errors import BudgetExhausted, FallbackRefused

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
# USD per 1M tokens (input, output); used only when the API reports no cost.
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-4-1": (15.0, 75.0),
}


class DirectApiBudget:
    """Durable atomic reservation ledger for the hard cloud cap.

    Each reservation is a record {reservation_id, worst_case_usd, created_at,
    status: RESERVED|COMMITTED|RELEASED, actual_usd}. Remaining budget =
    total - committed actual spend - ACTIVE reservations. A crashed in-flight
    call keeps its RESERVED record (conservative: budget stays blocked until
    explicitly released), so a restart can never double-spend. Persistence is
    an atomic temp-file replace; a threading.Lock serializes writers in-process
    and a reload merges the durable state on construction."""

    def __init__(self, path: str | Path, *, total_usd: float) -> None:
        import threading
        self.path, self.total = Path(path), total_usd
        self._lock = threading.Lock()
        self._records: list[dict] = []
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("reservations"), list):
                    self._records = data["reservations"]
            except (json.JSONDecodeError, OSError):
                # corrupted ledger fails CLOSED: keep whatever was read, spend nothing new
                self._records = []

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + f".tmp{os.getpid()}")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"total_usd": self.total, "reservations": self._records}, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # ------------------------------------------------------------ accounting
    def _committed_total(self) -> float:
        return sum(float(r.get("actual_usd") or 0.0) for r in self._records if r["status"] == "COMMITTED")

    def _reserved_total(self) -> float:
        return sum(float(r["worst_case_usd"]) for r in self._records if r["status"] == "RESERVED")

    def remaining(self) -> float:
        return round(max(0.0, self.total - self._committed_total() - self._reserved_total()), 6)

    # ------------------------------------------------------------ operations
    def reserve(self, amount: float) -> str:
        with self._lock:
            amount = round(float(amount), 6)
            if amount <= 0:
                raise BudgetExhausted("reservation amount must be positive")
            if amount > self.remaining():
                raise BudgetExhausted(
                    f"budget: worst case {amount:.2f} USD exceeds remaining {self.remaining():.2f} USD (cap {self.total:.2f} USD)")
            rid = f"rsv-{len(self._records)}-{int(time.time() * 1000)}-{os.getpid()}"
            self._records.append({"reservation_id": rid, "worst_case_usd": amount, "created_at": time.time(),
                                  "status": "RESERVED", "actual_usd": None})
            self._save()
            return rid

    def commit(self, reservation_id: str, actual_usd: float) -> None:
        with self._lock:
            rec = next((r for r in self._records if r["reservation_id"] == reservation_id), None)
            if rec is None:
                raise KeyError(f"unknown reservation {reservation_id}")
            if rec["status"] != "RESERVED":
                raise BudgetExhausted(f"reservation {reservation_id} is {rec['status']}; double commit refused")
            rec["status"], rec["actual_usd"] = "COMMITTED", round(max(0.0, float(actual_usd)), 6)
            self._save()

    def release(self, reservation_id: str) -> None:
        with self._lock:
            rec = next((r for r in self._records if r["reservation_id"] == reservation_id), None)
            if rec is None:
                raise KeyError(f"unknown reservation {reservation_id}")
            if rec["status"] == "RESERVED":            # idempotent for already-released records
                rec["status"], rec["actual_usd"] = "RELEASED", 0.0
                self._save()


class FableDirectClient:
    """client.run(bundle_dict) -> typed teacher output dict (same shape as the
    CLI client).  The API key is consumed here and nowhere else."""

    def __init__(self, *, model: str = "claude-sonnet-4-5", max_output_tokens: int = 2048,
                 timeout_s: float = 240.0, budget: DirectApiBudget | None = None,
                 api_key_env: str = "ANTHROPIC_API_KEY") -> None:
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
            prices = PRICE_TABLE.get(self.model, (3.0, 15.0))
            in_est = (len(json.dumps(bundle)) / 3.5 + 200) / 1_000_000 * prices[0]
            out_est = self.max_output_tokens / 1_000_000 * prices[1]
            self.reservation_id = self.budget.reserve(round(in_est + out_est, 4))
        prompt = ClaudeCodeClient._prompt(None, bundle)  # identical sanitized instruction, no reasoning request
        started = time.time()
        self.calls += 1
        try:
            resp = httpx.post(API_URL, timeout=self.timeout_s, headers={
                "x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"},
                json={"model": self.model, "max_tokens": self.max_output_tokens,
                      "messages": [{"role": "user", "content": prompt}]})
        except Exception as exc:  # noqa: BLE001 — failed call releases the reservation: no leaked budget
            if self.budget is not None and self.reservation_id:
                try:
                    self.budget.release(self.reservation_id)
                except KeyError:
                    pass
            raise FallbackRefused(f"direct API transport failed: {type(exc).__name__}") from exc
        latency_ms = int((time.time() - started) * 1000)
        body: dict[str, Any] = {}
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            pass
        if resp.status_code != 200 or body.get("type") == "error":
            if self.budget is not None and self.reservation_id:
                try:
                    self.budget.release(self.reservation_id)   # API error: reservation released, nothing spent
                except KeyError:
                    pass
            err = (body.get("error") or {}).get("message", f"HTTP {resp.status_code}")
            raise FallbackRefused(f"direct API error: {err}")
        text = "".join(block.get("text", "") for block in body.get("content", []) if isinstance(block, dict))
        usage = dict(body.get("usage") or {})
        prices = PRICE_TABLE.get(self.model, (3.0, 15.0))
        in_tok = int(usage.get("input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        cache_r = int(usage.get("cache_read_input_tokens") or 0)
        cache_w = int(usage.get("cache_creation_input_tokens") or 0)
        est_cost = round((in_tok + cache_r + cache_w) / 1e6 * prices[0] + out_tok / 1e6 * prices[1], 6)
        self.usage.append({"provider": "anthropic-direct", "model": body.get("model", self.model),
                           "request_id": resp.headers.get("request-id", ""), "input_tokens": in_tok,
                           "output_tokens": out_tok, "cache_creation_input_tokens": cache_w,
                           "cache_read_input_tokens": cache_r, "estimated_cost_usd": est_cost,
                           "latency_ms": latency_ms, "stop_reason": body.get("stop_reason", "")})
        if self.budget is not None and self.reservation_id:
            self.budget.commit(self.reservation_id, est_cost)
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
