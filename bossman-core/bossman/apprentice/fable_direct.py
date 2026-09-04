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
import time
from typing import Any

import httpx

from ._bootstrap import trace
from .claude_code_client import ClaudeCodeClient
from .errors import BudgetExhausted, FallbackRefused

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

# Потолок, прайс и сам журнал резервов живут в общем модуле корня репозитория:
# ровно тот же объект использует Command Center. Второй экземпляр этой логики
# означал бы два потолка по три доллара вместо одного.
from .._shared import AVAILABLE as _shared_available  # noqa: E402,F401 — кладёт корень репозитория в sys.path
from bossman_shared.fable_budget import (  # noqa: E402
    FABLE_HARD_CAP_USD,
    PRICE_TABLE,
    DirectApiBudget,
    actual_usd,
    canonical_budget,
    estimate_worst_case_usd,
)

__all__ = ["API_URL", "API_VERSION", "FABLE_HARD_CAP_USD", "PRICE_TABLE",
           "DirectApiBudget", "FableDirectClient", "actual_usd",
           "canonical_budget", "estimate_worst_case_usd"]


def _release_unsent(budget: DirectApiBudget, reservation_id: str) -> None:
    """Снять резерв, под который запрос так и не ушёл.

    Это не смягчение правила «неопределённую трату не возвращаем»: правило про
    отправленные запросы, у которых неизвестен исход. Здесь adapter не вызывался
    вовсе, поэтому держать деньги не за что.
    """
    with contextlib.suppress(Exception):
        budget.trusted_reconcile(reservation_id, request_id="request-never-sent", actual_usd=None)


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
        self.cap: DirectApiBudget | None = None
        self.cap_reservation_id: str | None = None

    def _each_hold(self):
        """Оба журнала разом: общий потолок и бюджет миссии, если он есть."""
        if self.cap is not None and self.cap_reservation_id:
            yield self.cap, self.cap_reservation_id
        if self.budget is not None and self.reservation_id:
            yield self.budget, self.reservation_id

    def _hold_uncertain(self) -> None:
        """Исход неизвестен (обрыв, таймаут, ошибка, ответ без usage) — резерв
        остаётся висеть до разбора: провайдер мог списать деньги."""
        for budget, rid in self._each_hold():
            with contextlib.suppress(Exception):
                budget.mark_reconciling(rid)

    def _settle(self, cost_usd: float, request_id: str) -> None:
        for budget, rid in self._each_hold():
            with contextlib.suppress(Exception):
                budget.commit(rid, cost_usd, request_id=request_id)

    def run(self, bundle: dict) -> dict[str, Any]:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise FallbackRefused("direct Anthropic API key is not configured in the process environment")
        # Резерв под ХУДШИЙ случай до сети, всегда: сначала на общем потолке
        # (его не поднять ничем), потом — на бюджете миссии, если он задан.
        worst = estimate_worst_case_usd(
            self.model, len(json.dumps(bundle, sort_keys=True)), self.max_output_tokens)
        self.cap = canonical_budget()
        self.cap_reservation_id = self.cap.reserve(worst, purpose="fable-direct-call")
        if self.budget is not None:
            try:
                self.reservation_id = self.budget.reserve(worst, purpose="fable-direct-call")
            except BaseException:
                _release_unsent(self.cap, self.cap_reservation_id)   # запрос не ушёл
                self.cap_reservation_id = None
                raise
        prompt = ClaudeCodeClient._prompt(None, bundle)  # identical sanitized instruction, no reasoning request
        started = time.time()
        self.calls += 1
        try:
            resp = httpx.post(API_URL, timeout=self.timeout_s, headers={
                "x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"},
                json={"model": self.model, "max_tokens": self.max_output_tokens,
                      "messages": [{"role": "user", "content": prompt}]})
        except BaseException as exc:  # noqa: BLE001 — таймаут и отмена тоже: деньги не возвращаем
            self._hold_uncertain()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise FallbackRefused(f"direct API transport failed: {type(exc).__name__}") from exc
        latency_ms = int((time.time() - started) * 1000)
        body: dict[str, Any] = {}
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            pass
        if resp.status_code != 200 or body.get("type") == "error":
            self._hold_uncertain()
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
        if not usage:
            self._hold_uncertain()      # нет свидетельства расхода — держим, а не прощаем
        else:
            self._settle(est_cost, request_id)
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
        transcript_recorder = None
        try:  # transcript agent: OFF unless BOSSMAN_FABLE_TRANSCRIPT_DIR is set
            from .fable_transcript import recorder_from_env

            transcript_recorder = recorder_from_env(os.environ.get("BOSSMAN_FABLE_TRANSCRIPT_MISSION", "default"))
        except Exception:  # noqa: BLE001 — recording must never break a paid call
            transcript_recorder = None
        if transcript_recorder is not None:
            try:
                transcript_recorder.record(bundle=bundle, response_text=text,
                                           usage=self.usage[-1], request_id=request_id,
                                           purpose=str(bundle.get("PROBLEM_ID", bundle.get("ROLE", ""))[:120]),
                                           stop_reason=str(body.get("stop_reason", "")))
            except Exception:  # noqa: BLE001
                pass
        return parsed
