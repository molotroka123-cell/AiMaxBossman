"""Check actual provider locality and price on EVERY inference, including fallback."""
import math
from contextvars import ContextVar

from bossman_shared.privacy import assert_provider_egress
from .v2.model_router import derive_local
from .providers import ProviderError, is_local_url
from .fable_cap import CappedAdapter


# Owner memory (vault notes, lessons, facts) recalled at TASK_START is LOCAL data.
# It reaches a model only on this machine / the local network unless the task
# explicitly opts in (task.meta.memory_to_cloud). Enforced HERE, on every
# inference, because the Smart Router, the recovery ladder and fallback pick the
# model after recall — a check at recall time would miss them (P1 2026-09-24: a
# task on a free cloud endpoint that may retain prompts received 5 vault notes).
MEMORY_CONTEXT_MARKER = "[MEMORY CONTEXT"
memory_to_cloud_allowed: ContextVar[bool] = ContextVar("bossman_memory_to_cloud", default=False)
memory_withheld: ContextVar[list | None] = ContextVar("bossman_memory_withheld", default=None)


def _is_memory_context(message) -> bool:
    return (isinstance(message, dict) and message.get("role") == "system"
            and isinstance(message.get("content"), str)
            and message["content"].lstrip().startswith(MEMORY_CONTEXT_MARKER))


def withhold_local_memory(args: tuple, kwargs: dict) -> tuple[tuple, dict, int]:
    """(args, kwargs) of adapter.chat(model, messages, ...) without recalled memory."""
    if "messages" in kwargs:
        msgs = kwargs["messages"]
        kept = [m for m in msgs or [] if not _is_memory_context(m)]
        return args, {**kwargs, "messages": kept}, len(msgs or []) - len(kept)
    if len(args) >= 2 and isinstance(args[1], list):
        kept = [m for m in args[1] if not _is_memory_context(m)]
        return (args[0], kept, *args[2:]), kwargs, len(args[1]) - len(kept)
    return args, kwargs, 0


def known_prices(model):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and math.isfinite(v) and v >= 0 for v in (model.get("price_in"), model.get("price_out")))


class GovernedAdapter:
    def __init__(self, adapter, provider, model):
        self.adapter, self.provider, self.model = adapter, dict(provider), dict(model)

    def __getattr__(self, name):
        return getattr(self.adapter, name)

    async def chat(self, *args, **kwargs):
        p, m = self.provider, self.model
        assert_provider_egress(p["kind"], p.get("base_url") or "")
        local, _ = derive_local(m.get("kind", ""), p["kind"], p.get("base_url") or "")
        local = local or (p["kind"] == "anthropic" and is_local_url(p.get("base_url") or ""))
        # CappedAdapter has its own canonical tariff and rejects unknown models before dispatch.
        if not local and not isinstance(self.adapter, CappedAdapter) and (not m.get("pricing_known", False) or not known_prices(m)):
            raise ProviderError("unknown cloud pricing; refresh catalog before inference", kind="budget")
        # memory follows the actual destination: this machine / the local network
        memory_local = local or is_local_url(p.get("base_url") or "")
        if not memory_local and not memory_to_cloud_allowed.get():
            args, kwargs, removed = withhold_local_memory(args, kwargs)
            sink = memory_withheld.get()
            if removed and sink is not None:
                sink.append({"provider": p.get("name") or p["kind"], "model": m.get("alias") or m.get("name"),
                             "messages": removed})
        return await self.adapter.chat(*args, **kwargs)
