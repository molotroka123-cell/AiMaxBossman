"""Stdlib HTTP client for the Jev (TypeSafe System-1) decision API.

Reliability contract (docs/JEV_DECISION_ENGINE.md, "Safety / reliability gates"):

* hard per-request timeout (``timeout_ms``) and a hard overall deadline;
* bounded retries — only for 429/503/529 and connection resets, never more
  than ``max_retries``; a decision request mutates nothing, so a retry is safe
  (browser MUTATIONS are never retried — that is browser_fastpath's rule);
* circuit breaker: after ``breaker_failures`` consecutive failures every call
  fails fast with ``circuit_open`` until ``breaker_cooldown_s`` passes, then
  ONE half-open trial decides whether it closes again;
* strict schema validation — anything unexpected raises ``JevInvalidResponse``
  and the caller falls back to the existing router. Never into an action.

Errors carry a short machine code (``reason``) and a message WITHOUT the key,
the request body or the response body. The key is attached only to the
Authorization header of the outgoing request.
"""
from __future__ import annotations

import json
import math
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config as jev_config

MAX_RESPONSE_BYTES = 256 * 1024
RETRYABLE_STATUS = frozenset({429, 503, 529})


class JevError(RuntimeError):
    """Base: every Jev failure means "use the existing path"."""

    reason = "error"

    def __init__(self, message: str, *, reason: str | None = None, status: int | None = None):
        super().__init__(message)
        if reason:
            self.reason = reason
        self.status = status


class JevDisabled(JevError):
    reason = "disabled"


class JevNoKey(JevError):
    reason = "no_key"


class JevUnavailable(JevError):
    reason = "unavailable"


class JevCircuitOpen(JevError):
    reason = "circuit_open"


class JevInvalidResponse(JevError):
    reason = "invalid_schema"


# ---------------------------------------------------------------- validation

def validate_choice(answer: Any, ids: Any) -> dict:
    """Strict check of one "choice" answer against the offered ids.

    Re-implements upstream's validator (MIT, see NOTICE): the choice must be an
    offered id; probabilities must cover EXACTLY the offered ids, be finite
    numbers in [0, 1] summing to 1±0.02; confidence finite in [0, 1]; and the
    chosen id must be (one of) the most probable. Booleans are not numbers here.
    """
    ids = {str(i) for i in ids}
    try:
        if not isinstance(answer, dict):
            raise TypeError
        probabilities = answer["probabilities"]
        confidence = answer["confidence"]
        choice = answer["choice"]
        if not isinstance(probabilities, dict) or not isinstance(choice, str):
            raise TypeError
        numbers = [*probabilities.values(), confidence]
        valid = (
            bool(ids)
            and choice in ids
            and set(probabilities) == ids
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[choice] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise JevInvalidResponse("Invalid Jev choice answer; no decision used.")
    return {"choice": choice, "confidence": float(confidence),
            "probabilities": {k: float(v) for k, v in probabilities.items()}}


def _usage(raw: Any) -> dict:
    """Only finite non-negative integer counters survive; anything else is dropped."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        if isinstance(key, str) and len(key) <= 64 and type(value) is int and value >= 0:
            out[key] = value
    return out


def validate_response(payload: Any, questions: dict) -> dict:
    """Validate the whole response for the questions that were asked."""
    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        raise JevInvalidResponse("Jev response has no answers object; no decision used.")
    model = payload.get("model")
    if not isinstance(model, str) or not model or len(model) > 200:
        raise JevInvalidResponse("Jev response has no model identity; no decision used.")
    return {"model": model, "answers": payload["answers"], "usage": _usage(payload.get("usage"))}


# ---------------------------------------------------------------- breaker

@dataclass
class CircuitBreaker:
    failures_to_open: int = 3
    cooldown_s: float = 60.0
    clock: Callable[[], float] = time.monotonic
    failures: int = 0
    opened_at: float | None = None
    half_open_inflight: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if self.clock() - self.opened_at >= self.cooldown_s:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        with self._lock:
            state = self.state
            if state == "closed":
                return True
            if state == "half_open" and not self.half_open_inflight:
                self.half_open_inflight = True        # exactly one trial
                return True
            return False

    def success(self) -> None:
        with self._lock:
            self.failures = 0
            self.opened_at = None
            self.half_open_inflight = False

    def failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.half_open_inflight or self.failures >= self.failures_to_open:
                self.opened_at = self.clock()
            self.half_open_inflight = False

    def snapshot(self) -> dict:
        return {"state": self.state, "consecutive_failures": self.failures,
                "failures_to_open": self.failures_to_open, "cooldown_s": self.cooldown_s}


# ---------------------------------------------------------------- transport

def _urllib_post(url: str, headers: dict, body: bytes, timeout_s: float) -> tuple[int, bytes, dict]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:   # noqa: S310
            data = response.read(MAX_RESPONSE_BYTES + 1)
            return response.status, data, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        try:
            data = exc.read(4096)
        except Exception:  # noqa: BLE001
            data = b""
        return exc.code, data, dict((exc.headers or {}).items())


@dataclass
class CallStats:
    calls: int = 0              # HTTP attempts actually sent
    ok: int = 0
    failures: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_reason: dict = field(default_factory=dict)


class JevClient:
    """One instance per process; thread-safe enough for the shadow workloads."""

    def __init__(self, cfg: jev_config.JevConfig | None = None, *,
                 transport: Callable[[str, dict, bytes, float], tuple[int, bytes, dict]] | None = None,
                 key_provider: Callable[[], str] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 breaker: CircuitBreaker | None = None):
        self.cfg = cfg or jev_config.load()
        self._transport = transport or _urllib_post
        self._key = key_provider or jev_config.api_key
        self._sleep = sleep
        self._clock = clock
        self.breaker = breaker or CircuitBreaker(self.cfg.breaker_failures,
                                                 float(self.cfg.breaker_cooldown_s), clock=clock)
        self.stats = CallStats()

    def _fail(self, exc: JevError) -> JevError:
        self.stats.failures += 1
        self.stats.by_reason[exc.reason] = self.stats.by_reason.get(exc.reason, 0) + 1
        return exc

    def ask(self, state: dict, questions: dict, *, force: bool = False) -> dict:
        """Send one decision request; return the validated envelope.

        ``force=True`` is used only by the owner's explicit ``--execute`` probe:
        it ignores BOSSMAN_JEV_ENABLED (never the kill file or the key check).
        """
        if jev_config.killed():
            raise self._fail(JevDisabled("Jev kill switch file present."))
        if not force and not self.cfg.enabled:
            raise self._fail(JevDisabled("BOSSMAN_JEV_ENABLED is off."))
        key = self._key()
        if not key:
            raise self._fail(JevNoKey("No Jev API key in the environment."))
        if not self.breaker.allow():
            raise self._fail(JevCircuitOpen("Jev circuit breaker is open."))
        body = json.dumps({"model": self.cfg.model, "state": state, "questions": questions},
                          ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                   "Accept": "application/json", "User-Agent": "bossman-jev-shadow/1"}
        timeout_s = self.cfg.timeout_ms / 1000.0
        deadline = self._clock() + timeout_s * (self.cfg.max_retries + 1) + 1.0
        attempt = 0
        while True:
            self.stats.calls += 1
            try:
                status, raw, resp_headers = self._transport(self.cfg.endpoint, headers, body, timeout_s)
            except (socket.timeout, TimeoutError):
                error: JevError = JevUnavailable("Jev request timed out.", reason="timeout")
                status, raw, resp_headers = 0, b"", {}
            except (urllib.error.URLError, ConnectionError, OSError) as exc:
                timed_out = isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError))
                error = (JevUnavailable("Jev request timed out.", reason="timeout") if timed_out
                         else JevUnavailable("Jev connection failed.", reason="connection"))
                status, raw, resp_headers = 0, b"", {}
            else:
                error = None
            if error is None:
                if status in RETRYABLE_STATUS:
                    error = JevUnavailable(f"Jev returned HTTP {status}.",
                                           reason="rate_limited" if status == 429 else "http_5xx",
                                           status=status)
                elif 400 <= status < 500:
                    error = JevUnavailable(f"Jev returned HTTP {status}.", reason="http_4xx", status=status)
                elif status >= 500 or status < 200 or status >= 300:
                    error = JevUnavailable(f"Jev returned HTTP {status}.", reason="http_5xx", status=status)
            # Retry only transport failures and 429/503/529; a plain 500 or any 4xx is final.
            retryable = error is not None and (status == 0 or status in RETRYABLE_STATUS)
            if error is not None:
                if retryable and attempt < self.cfg.max_retries:
                    wait = min(2.0, 0.25 * (2 ** attempt))
                    retry_after = str((resp_headers or {}).get("Retry-After", "")).strip()
                    if retry_after.isdigit():
                        wait = min(2.0, float(retry_after))
                    if self._clock() + wait < deadline:
                        attempt += 1
                        self.stats.retries += 1
                        self._sleep(wait)
                        continue
                self.breaker.failure()
                raise self._fail(error)
            if len(raw) > MAX_RESPONSE_BYTES:
                self.breaker.failure()
                raise self._fail(JevInvalidResponse("Jev response too large; no decision used."))
            try:
                payload = json.loads(raw.decode("utf-8"))
                envelope = validate_response(payload, questions)
            except (ValueError, UnicodeDecodeError):
                self.breaker.failure()
                raise self._fail(JevInvalidResponse("Jev response is not JSON; no decision used."))
            except JevInvalidResponse as exc:
                self.breaker.failure()
                raise self._fail(exc)
            self.breaker.success()
            self.stats.ok += 1
            usage = envelope["usage"]
            self.stats.input_tokens += int(usage.get("input_tokens", usage.get("prompt_tokens", 0)))
            self.stats.output_tokens += int(usage.get("output_tokens", usage.get("completion_tokens", 0)))
            return envelope

    def status(self) -> dict:
        return {"breaker": self.breaker.snapshot(),
                "stats": {"calls": self.stats.calls, "ok": self.stats.ok,
                          "failures": self.stats.failures, "retries": self.stats.retries,
                          "input_tokens": self.stats.input_tokens,
                          "output_tokens": self.stats.output_tokens,
                          "by_reason": dict(self.stats.by_reason)}}


def estimate_cost(cfg: jev_config.JevConfig, calls: int, input_tokens: int,
                  output_tokens: int) -> float | None:
    """USD estimate, or None when the owner has not configured a price.

    None means UNKNOWN — never zero. Upstream notes TypeSafe responses carry token
    counts without a billed dollar amount.
    """
    parts = []
    if cfg.price_per_call_usd is not None:
        parts.append(cfg.price_per_call_usd * calls)
    if cfg.price_per_1k_input_usd is not None:
        parts.append(cfg.price_per_1k_input_usd * input_tokens / 1000)
    if cfg.price_per_1k_output_usd is not None:
        parts.append(cfg.price_per_1k_output_usd * output_tokens / 1000)
    return round(sum(parts), 8) if parts else None
