"""SpendFirewall: Pre-call zero-cost gate and Local Compute Cost Vector accounting.

Enforces:
1. Free-Only Governance: ANY cloud model invocation with non-zero pricing
   (pricing.prompt > 0 or pricing.completion > 0, price_in > 0, or non-free cloud)
   is FAIL-CLOSED blocked unless accompanied by a verified one-time owner authorization token.
2. Token Lifecycle: Owner tokens are single-use (consumed on authorization) to prevent replay.
3. LocalComputeCostVector: Tracks local hardware consumption (gpu_seconds, ram_gb_seconds,
   cpu_seconds) allowing unit economics comparison between local execution and cloud tariffs.
"""
from __future__ import annotations

import ipaddress
import os
import secrets
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping
from urllib.parse import urlsplit


class SpendFirewallError(RuntimeError):
    """Base exception for SpendFirewall violations."""


class PaidCallBlockedError(SpendFirewallError):
    """Raised when a non-zero priced model is called without an authorized owner token."""


class TokenInvalidOrConsumedError(SpendFirewallError):
    """Raised when an owner token is absent, malformed, or has already been consumed."""


@dataclass
class LocalComputeCostVector:
    """Tracks local hardware resource consumption."""
    gpu_seconds: float = 0.0
    ram_gb_seconds: float = 0.0
    cpu_seconds: float = 0.0

    def add(self, *, gpu_seconds: float = 0.0, ram_gb_seconds: float = 0.0, cpu_seconds: float = 0.0) -> None:
        self.gpu_seconds += max(0.0, float(gpu_seconds))
        self.ram_gb_seconds += max(0.0, float(ram_gb_seconds))
        self.cpu_seconds += max(0.0, float(cpu_seconds))

    def estimate_cost_usd(
        self,
        *,
        gpu_hour_rate_usd: float = 0.40,
        ram_gb_hour_rate_usd: float = 0.005,
        cpu_hour_rate_usd: float = 0.02,
    ) -> float:
        gpu_cost = (self.gpu_seconds / 3600.0) * gpu_hour_rate_usd
        ram_cost = (self.ram_gb_seconds / 3600.0) * ram_gb_hour_rate_usd
        cpu_cost = (self.cpu_seconds / 3600.0) * cpu_hour_rate_usd
        return round(gpu_cost + ram_cost + cpu_cost, 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gpu_seconds": round(self.gpu_seconds, 3),
            "ram_gb_seconds": round(self.ram_gb_seconds, 3),
            "cpu_seconds": round(self.cpu_seconds, 3),
            "estimated_cost_usd": self.estimate_cost_usd(),
        }


class SpendFirewall:
    """Central gateway enforcing $0.00 spend and tracking local hardware resource vectors."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_tokens: dict[str, dict[str, Any]] = {}
        self._consumed_tokens: set[str] = set()
        self._cost_vector = LocalComputeCostVector()
        self._audit_log: list[dict[str, Any]] = []

    def issue_one_time_owner_token(self, *, purpose: str, max_usd: float = 1.0) -> str:
        """Issue a single-use authorization token for an explicitly approved paid call."""
        token = f"spend-tok-{secrets.token_urlsafe(24)}"
        with self._lock:
            self._active_tokens[token] = {
                "purpose": purpose,
                "max_usd": float(max_usd),
                "created_at": os.environ.get("BOSSMAN_TEST_CLOCK") or "",
            }
        return token

    def register_owner_token(self, token: str, *, purpose: str = "registered_token", max_usd: float = 1.0) -> None:
        """Register an existing token (e.g. from test or external approval channel)."""
        if not token or not isinstance(token, str):
            raise SpendFirewallError("Invalid token format")
        with self._lock:
            if token in self._consumed_tokens:
                raise TokenInvalidOrConsumedError(f"Token {token} has already been consumed")
            self._active_tokens[token] = {
                "purpose": purpose,
                "max_usd": float(max_usd),
            }

    def record_local_compute(
        self,
        *,
        gpu_seconds: float = 0.0,
        ram_gb_seconds: float = 0.0,
        cpu_seconds: float = 0.0,
    ) -> LocalComputeCostVector:
        """Record local compute execution to the cost vector."""
        with self._lock:
            self._cost_vector.add(
                gpu_seconds=gpu_seconds,
                ram_gb_seconds=ram_gb_seconds,
                cpu_seconds=cpu_seconds,
            )
            return LocalComputeCostVector(
                gpu_seconds=self._cost_vector.gpu_seconds,
                ram_gb_seconds=self._cost_vector.ram_gb_seconds,
                cpu_seconds=self._cost_vector.cpu_seconds,
            )

    def get_cost_vector(self) -> LocalComputeCostVector:
        with self._lock:
            return LocalComputeCostVector(
                gpu_seconds=self._cost_vector.gpu_seconds,
                ram_gb_seconds=self._cost_vector.ram_gb_seconds,
                cpu_seconds=self._cost_vector.cpu_seconds,
            )

    @staticmethod
    def _host(url: str) -> str | None:
        """Hostname of a URL, parsed (not searched): `http://127.0.0.1@evil.example/` has the host evil.example."""
        if not url:
            return None
        try:
            host = urlsplit(url if "://" in url else "//" + url).hostname
        except ValueError:
            return None
        return host.lower().rstrip(".") if host else None

    @staticmethod
    def _is_loopback(host: str) -> bool:
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def is_free_model(
        self,
        provider: dict[str, Any] | str,
        model: dict[str, Any] | str,
        pricing: Mapping[str, Any] | None = None,
    ) -> bool:
        """Check if model invocation is provably $0.00 / free.

        Audit 28.10: this used to look for the text «127.0.0.1»/«localhost» ANYWHERE in the provider URL (a host label like
        127.0.0.1.attacker.example, a path or user@ part was enough), treated any model name ending in :free as free on every
        provider, and let a free flag win over a real price. Now: the URL is parsed and only a loopback HOST is local, `:free`
        counts only on OpenRouter, and an explicit non-zero price makes a non-local call paid whatever the flags say."""
        provider_kind = ""
        provider_url = ""
        if isinstance(provider, dict):
            provider_kind = str(provider.get("kind") or "").lower()
            provider_url = str(provider.get("base_url") or "")
        elif isinstance(provider, str):
            provider_url = provider
        host = self._host(provider_url)
        model_name = ""
        if isinstance(model, dict):
            model_name = str(model.get("name") or model.get("alias") or "")
        elif isinstance(model, str):
            model_name = model

        # 1. Local: a loopback host. Without any URL only the kind label is left to go on.
        if host is not None:
            is_local = self._is_loopback(host)
        else:
            is_local = provider_kind in ("ollama", "local", "llama_cpp", "vllm")
        if is_local and not any(tag in model_name.lower() for tag in (":cloud", "-cloud")):
            return True

        def _to_float(v: Any) -> float:
            if v is None:
                return 0.0
            try:
                return float(v)
            except (ValueError, TypeError):
                return 0.0

        prices: list[float] = []
        prompt_cost = completion_cost = price_in = price_out = None
        if pricing is not None:
            prompt_cost, completion_cost = pricing.get("prompt"), pricing.get("completion")
            price_in, price_out = pricing.get("price_in"), pricing.get("price_out")
            prices = [_to_float(prompt_cost), _to_float(completion_cost), _to_float(price_in), _to_float(price_out)]
        if any(v > 0.0 for v in prices):
            return False                              # an explicit price beats every flag and every name

        # 2. OpenRouter :free models (the suffix means nothing on any other provider)
        on_openrouter = provider_kind == "openrouter" or (host is not None and (host == "openrouter.ai" or host.endswith(".openrouter.ai")))
        if on_openrouter and model_name.endswith(":free"):
            return True

        # 3. Declared price of zero (a missing price is NOT zero)
        if pricing is not None and prices and all(v == 0.0 for v in prices) and (prompt_cost is not None or price_in is not None):
            return True

        return isinstance(model, dict) and model.get("free") is True

    def verify_pre_call_free_or_authorized(
        self,
        provider: dict[str, Any] | str,
        model: dict[str, Any] | str,
        pricing: Mapping[str, Any] | None = None,
        owner_token: str | None = None,
    ) -> dict[str, Any]:
        """Verify that inference call is either provably free ($0.00) or authorized by owner token.

        FAIL-CLOSED:
        - If price > 0 and no valid owner_token provided: raises PaidCallBlockedError.
        - If owner_token is already consumed or invalid: raises TokenInvalidOrConsumedError.
        - If authorized, consumes token so it cannot be reused.
        """
        p_name = provider.get("name", "provider") if isinstance(provider, dict) else str(provider)
        m_name = model.get("name", "model") if isinstance(model, dict) else str(model)

        if self.is_free_model(provider, model, pricing):
            record = {
                "status": "ALLOWED_FREE",
                "provider": p_name,
                "model": m_name,
                "paid": False,
                "cost_usd": 0.0,
            }
            with self._lock:
                self._audit_log.append(record)
            return record

        # Paid call detected!
        if not owner_token:
            msg = (
                f"SpendFirewall FAIL-CLOSED: Paid cloud call to {p_name}:{m_name} "
                f"blocked. Non-zero price requires explicit one-time owner authorization token."
            )
            with self._lock:
                self._audit_log.append({
                    "status": "BLOCKED_PAID",
                    "provider": p_name,
                    "model": m_name,
                    "reason": "missing_owner_token",
                })
            raise PaidCallBlockedError(msg)

        with self._lock:
            if owner_token in self._consumed_tokens:
                raise TokenInvalidOrConsumedError(f"Owner token {owner_token} has already been consumed")
            if owner_token not in self._active_tokens:
                raise TokenInvalidOrConsumedError(f"Owner token {owner_token} is invalid or unrecognized")

            # Consume token
            meta = self._active_tokens.pop(owner_token)
            self._consumed_tokens.add(owner_token)
            record = {
                "status": "AUTHORIZED_PAID",
                "provider": p_name,
                "model": m_name,
                "paid": True,
                "token_consumed": owner_token,
                "purpose": meta.get("purpose", ""),
                "max_usd": meta.get("max_usd", 1.0),
            }
            self._audit_log.append(record)
            return record


# Global singleton instance
_GLOBAL_FIREWALL = SpendFirewall()


def get_spend_firewall() -> SpendFirewall:
    return _GLOBAL_FIREWALL
