"""Production wiring for OpenHands as an optional untrusted coding teacher.

This module deliberately does not replace TeacherSandbox or PatchVerifier.
OpenHands receives a sanitized disposable Git copy; TeacherFallback and the
existing independent verifier remain authoritative for acceptance, learning,
budgets and sanctions.
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Sequence

from . import flags
from .openhands_client import OpenHandsClient
from .openhands_teacher_client import OpenHandsTeacherClient
from .teacher import FallbackReason, TeacherFallback


class OpenHandsFallback(TeacherFallback):
    """TeacherFallback with its own opt-in feature flag."""

    def allowed(self, reason: FallbackReason | str, task: Any) -> str:
        if not flags.enabled(flags.OPENHANDS_CODE_FALLBACK):
            return f"{flags.OPENHANDS_CODE_FALLBACK} is off"
        try:
            r = FallbackReason(reason)
        except ValueError:
            return f"unknown fallback reason {reason!r}"
        if r is FallbackReason.OWNER_REQUESTED and not task.owner_requested_fallback:
            return "owner did not request the fallback"
        return ""


def openrouter_provider_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Select only the credential/config needed by the OpenRouter worker."""
    env = os.environ if source is None else source
    out: dict[str, str] = {}
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL"):
        value = env.get(key)
        if value:
            out[key] = str(value)
    return out


def build_openhands_fallback(*, workspace: Any, verifier: Any, teacher: Any,
                             command: Sequence[str] | None = None, model: str | None = None,
                             provider_env: Mapping[str, str] | None = None,
                             governor: Any = None, budget_context: Any = None,
                             estimated_usd: float = 0.5, max_calls: int = 2,
                             sanctions: Any = None, memory: Any = None, clock: Any = None) -> OpenHandsFallback:
    """Build the OpenHands backend without granting it real-repository authority."""
    selected_provider_env = openrouter_provider_env(provider_env) if provider_env is not None else openrouter_provider_env()
    selected_model = model or os.environ.get("BOSSMAN_OPENHANDS_MODEL")
    if not selected_model:
        raise ValueError("OpenHands fallback requires an explicit OpenRouter model")
    if not str(selected_model).startswith("openrouter/"):
        raise ValueError("Bossman OpenHands fallback is restricted to OpenRouter models")
    if not selected_provider_env.get("OPENROUTER_API_KEY"):
        raise ValueError("OpenHands fallback requires OPENROUTER_API_KEY")
    client = OpenHandsClient(command=command, env=selected_provider_env)
    adapter = OpenHandsTeacherClient(client, model=str(selected_model))
    kwargs = dict(client=adapter, workspace=workspace, verifier=verifier, teacher=teacher,
                  governor=governor, budget_context=budget_context, estimated_usd=estimated_usd,
                  max_calls=max_calls, sanctions=sanctions, memory=memory)
    if clock is not None:
        kwargs["clock"] = clock
    return OpenHandsFallback(**kwargs)
