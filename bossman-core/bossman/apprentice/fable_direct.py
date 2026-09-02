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
    """Atomic single-process reservation ledger (durable JSON file).
    reserve() -> token | raises BudgetExhausted. Worst case is bounded by
    max_output_tokens * output_price + estimated input price."""

    def __init__(self, path: str | Path, *, total_usd: float) -> None:
        self.path, self.total = Path(path), total_usd
        self._committed = 0.0
        self._reserved = 0.0

    def remaining(self) -> float:
        return round(self.total - self._committed - self._reserved, 4)

    def reserve(self, worst_case_usd: float) -> str:
        if worst_case_usd > self.remaining():
            raise BudgetExhausted(f"budget: worst case {worst_case_usd:.2f} USD exceeds remaining {self.remaining():.2f} USD")
        self._reserved += worst_case_usd
        return "reserved"

    def commit(self, token: str, actual_usd: float) -> None:
        self._reserved = max(0.0, self._reserved - worst_case_initial(token))
        self._committed += actual_usd


def worst_case_initial(token: str) -> float:  # placeholder kept for future multi-reservation bookkeeping
    return 0.0


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

    def run(self, bundle: dict) -> dict[str, Any]:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise FallbackRefused("direct Anthropic API key is not configured in the process environment")
        if self.budget is not None:
            prices = PRICE_TABLE.get(self.model, (3.0, 15.0))
            in_est = (len(json.dumps(bundle)) / 3.5 + 200) / 1_000_000 * prices[0]
            out_est = self.max_output_tokens / 1_000_000 * prices[1]
            self.budget.reserve(round(in_est + out_est, 4))
        prompt = ClaudeCodeClient._prompt(None, bundle)  # identical sanitized instruction, no reasoning request
        started = time.time()
        self.calls += 1
        try:
            resp = httpx.post(API_URL, timeout=self.timeout_s, headers={
                "x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"},
                json={"model": self.model, "max_tokens": self.max_output_tokens,
                      "messages": [{"role": "user", "content": prompt}]})
        except Exception as exc:  # noqa: BLE001 — network/API failure is a typed refusal, never a leak
            raise FallbackRefused(f"direct API transport failed: {type(exc).__name__}") from exc
        latency_ms = int((time.time() - started) * 1000)
        body: dict[str, Any] = {}
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            pass
        if resp.status_code != 200 or body.get("type") == "error":
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
        if self.budget is not None:
            self.budget.commit("reserved", est_cost)
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
