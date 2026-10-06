"""One local-model route with empty-answer detection and bounded recovery.

Used by Master Parser (analysis, narrative) and the passport checkpoint, so a
degraded Ollama runner is handled the same way everywhere:

* an empty answer (no text / ``eval_count<=1``) is a retryable ``EmptyAnswer``,
  never a success;
* recovery is bounded: at most ONE unload+reload per scope (participant), then
  one retry; if the retry is empty too the scope is reported honestly as failed;
* concurrent scopes share one runner: if another scope already unloaded it since
  this call started, the retry is enough (the model is not unloaded twice);
* per-scope statistics (calls, empty answers, recoveries, latencies) feed the
  speed report. Scopes are opaque person keys; no text is kept here.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .ollama_native import EmptyAnswer

MAX_RECOVERIES_PER_SCOPE = 1


@dataclass
class ScopeStats:
    calls: int = 0
    empty_answers: int = 0
    recoveries: int = 0
    latencies: list[float] = field(default_factory=list)
    degraded: bool = False
    first_ok_at: float | None = None

    def as_dict(self) -> dict:
        return {"calls": self.calls, "empty_answers": self.empty_answers,
                "recoveries": self.recoveries}


class ResilientChat:
    def __init__(self, adapter, model: str, *, timeout: float = 180.0, settle: float = 0.5,
                 on_call=None, clock=time.perf_counter):
        self.adapter, self.model, self.timeout = adapter, model, timeout
        self.settle = settle
        self.on_call = on_call
        self.clock = clock
        self.stats: dict[str, ScopeStats] = {}
        self.closed = {"calls": 0, "empty_answers": 0, "recoveries": 0}
        self.epoch = 0
        self.unloads = 0
        self.unload_errors = 0
        self._lock = asyncio.Lock()

    def scope(self, name: str) -> ScopeStats:
        return self.stats.setdefault(name, ScopeStats())

    def latencies(self) -> list[float]:
        return [value for st in self.stats.values() for value in st.latencies]

    def end_scope(self, name: str) -> None:
        """Forget a finished scope (a long-lived chat route must not grow per turn nor
        keep a 'degraded' verdict for the next turn); counters are folded into totals."""
        st = self.stats.pop(name, None)
        if st is not None:
            self.closed = {"calls": self.closed["calls"] + st.calls,
                           "empty_answers": self.closed["empty_answers"] + st.empty_answers,
                           "recoveries": self.closed["recoveries"] + st.recoveries}

    async def _call(self, st: ScopeStats, messages: list[dict], kw: dict):
        st.calls += 1
        if self.on_call:
            self.on_call()
        started = self.clock()
        try:
            answer = await self.adapter.chat(self.model, messages, **kw)
            if not str(getattr(answer, "text", "") or "").strip():
                raise EmptyAnswer("model returned empty text",
                                  eval_count=int(getattr(answer, "tokens_out", 0) or 0))
            if st.first_ok_at is None:
                st.first_ok_at = self.clock()
            return answer
        except EmptyAnswer:
            st.empty_answers += 1
            raise
        finally:
            st.latencies.append(self.clock() - started)

    async def _recover(self, epoch_at_start: int) -> None:
        async with self._lock:
            if self.epoch != epoch_at_start:
                return   # another scope already reloaded the runner meanwhile
            unload = getattr(self.adapter, "unload", None)
            if unload is not None:
                try:
                    await unload(self.model)
                    self.unloads += 1
                except Exception:  # noqa: BLE001 — still retry: the next call reloads
                    self.unload_errors += 1
            self.epoch += 1
            if self.settle:
                await asyncio.sleep(self.settle)

    async def chat(self, messages: list[dict], *, scope: str = "", **kw):
        st = self.scope(scope)
        kw.setdefault("timeout", self.timeout)
        if st.degraded:
            raise EmptyAnswer("model still empty for this participant after recovery")
        epoch = self.epoch
        try:
            return await self._call(st, messages, kw)
        except EmptyAnswer:
            if st.recoveries >= MAX_RECOVERIES_PER_SCOPE:
                st.degraded = True
                raise
        st.recoveries += 1
        await self._recover(epoch)
        try:
            return await self._call(st, messages, kw)
        except EmptyAnswer:
            st.degraded = True
            raise
