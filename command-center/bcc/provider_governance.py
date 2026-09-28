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


#: Why a cloud price is unknown, as far as the provider's synchronized catalog
#: can tell (Registry.adapter_for). The gate is the same in every case; the
#: owner's next step is not — "refresh the catalog" cannot fix a model the
#: catalog no longer lists (RC19 audit: a removed OpenRouter model).
_UNKNOWN_PRICE_WHY = {
    "stale": ("unknown cloud pricing: model {name} is no longer in the provider catalog "
              "(removed by the provider?) — choose another model for this agent"),
    "absent": ("unknown cloud pricing: model {name} is not in the synchronized provider "
               "catalog — check the model id or choose another model"),
}


def is_governed_local(provider: dict, model: dict) -> bool:
    """Local for the price gate: this machine / the local network."""
    base = provider.get("base_url") or ""
    local, _ = derive_local(model.get("kind", ""), provider["kind"], base)
    return local or (provider["kind"] == "anthropic" and is_local_url(base))


def priced(model: dict) -> bool:
    return bool(model.get("pricing_known", False)) and known_prices(model)


def refuses_unknown_price(provider: dict, model: dict) -> bool:
    """Would GovernedAdapter refuse this model before any provider is asked?

    Same rule as `GovernedAdapter.chat`, for callers that must not queue or
    pick a model that is certain to be refused (admission, recovery)."""
    from .fable_cap import paid_fable_boundary
    return (not is_governed_local(provider, model) and not paid_fable_boundary(provider)
            and not priced(model))


def unknown_price_message(model: dict, catalog_state: str | None = None) -> str:
    name = model.get("name") or model.get("alias") or "?"
    template = _UNKNOWN_PRICE_WHY.get(catalog_state or "")
    return (template.format(name=name) if template
            else "unknown cloud pricing; refresh catalog before inference")


class GovernedAdapter:
    def __init__(self, adapter, provider, model, *, catalog_state: str | None = None):
        self.adapter, self.provider, self.model = adapter, dict(provider), dict(model)
        self.catalog_state = catalog_state

    def __getattr__(self, name):
        return getattr(self.adapter, name)

    async def chat(self, *args, **kwargs):
        p, m = self.provider, self.model
        assert_provider_egress(p["kind"], p.get("base_url") or "")
        local = is_governed_local(p, m)
        # CappedAdapter has its own canonical tariff and rejects unknown models before dispatch.
        if not local and not isinstance(self.adapter, CappedAdapter) and not priced(m):
            raise ProviderError(unknown_price_message(m, self.catalog_state), kind="budget")
        # memory follows the actual destination: this machine / the local network
        memory_local = local or is_local_url(p.get("base_url") or "")
        if not memory_local and not memory_to_cloud_allowed.get():
            args, kwargs, removed = withhold_local_memory(args, kwargs)
            sink = memory_withheld.get()
            if removed and sink is not None:
                sink.append({"provider": p.get("name") or p["kind"], "model": m.get("alias") or m.get("name"),
                             "messages": removed})
        return await self.adapter.chat(*args, **kwargs)
