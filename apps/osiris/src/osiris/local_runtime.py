"""Local model runtime. No cloud session cap. Policy stays in code.

Hook for a future local model: implement LocalModel and register it.
Level 0 tools are not part of this interface and will not be added.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


CLOUD_SESSION_TOKEN_CAP = 800_000


@dataclass
class RuntimeLimits:
    session_token_cap: int | None
    cloud_safety_overlay: bool
    prompt_cache: bool
    policy_in_code: bool = True


def limits_for(kind: str) -> RuntimeLimits:
    if kind == "local":
        return RuntimeLimits(
            session_token_cap=None,
            cloud_safety_overlay=False,
            prompt_cache=False,
            policy_in_code=True,
        )
    return RuntimeLimits(
        session_token_cap=CLOUD_SESSION_TOKEN_CAP,
        cloud_safety_overlay=True,
        prompt_cache=True,
        policy_in_code=True,
    )


class LocalModel(Protocol):
    name: str
    n_ctx: int

    def complete(self, prompt: str, *, system: str | None = None) -> str: ...


@dataclass
class EchoLocalModel:
    """Stand-in until a real local model is wired. Does not call the network."""

    name: str = "echo-local"
    n_ctx: int = 0

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        return f"[local:{self.name} n_ctx={self.n_ctx or 'native'}] {prompt[:240]}"


@dataclass
class LocalRuntime:
    model: LocalModel = field(default_factory=EchoLocalModel)
    limits: RuntimeLimits = field(default_factory=lambda: limits_for("local"))

    def complete(self, prompt: str, *, system: str | None = None) -> dict:
        if self.limits.session_token_cap is not None:
            raise RuntimeError("local runtime must not carry a cloud session cap")
        text = self.model.complete(prompt, system=system)
        return {
            "text": text,
            "model": self.model.name,
            "n_ctx": self.model.n_ctx or None,
            "session_token_cap": None,
            "cloud_safety_overlay": False,
            "policy_in_code": True,
        }
