"""Check actual provider locality and price on EVERY inference, including fallback."""
import math
import os
from contextvars import ContextVar
from urllib.parse import urlsplit

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


ALLOW_PAID_CLOUD_ENV = "BOSSMAN_ALLOW_PAID_CLOUD"


def free_only_policy_active() -> bool:
    """Owner rule: product runtime cloud = OpenRouter ':free' or a free-tier preset, else local.

    Same switch as the core gateway (bossman.gateway.router); it is an explicit
    operator/test opt-out and is never implied by a registered key or price."""
    return os.getenv(ALLOW_PAID_CLOUD_ENV, "").strip().lower() not in {"1", "true", "yes"}


def _free_preset_hosts() -> frozenset:
    from .features.free_providers import PRESETS
    return frozenset((urlsplit(p.base_url).hostname or "").lower() for p in PRESETS.values())


def free_only_refusal(provider: dict, model: dict) -> str:
    """"" when the free-only rule lets this inference through, else why not.

    Local endpoints and the owner-capped Fable boundary (fable_cap, a separate
    owner-set hard limit) are not judged here. Any other cloud model must be
    provably free: OpenRouter with a ':free' id, or a model connected through a
    free-tier preset (caps.free_tier) on that preset's own host. A positive price
    is refused outright; a 0/0 an owner typed for an arbitrary host is not proof."""
    from .fable_cap import paid_fable_boundary
    if (not free_only_policy_active() or is_governed_local(provider, model)
            or paid_fable_boundary(provider) or is_local_url(provider.get("base_url") or "")):
        return ""
    name = str(model.get("name") or model.get("alias") or "?")
    prices = [v for v in (model.get("price_in"), model.get("price_out"))
              if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if any(v > 0 for v in prices):
        return (f"free-only policy: cloud model {name} has a positive price; only ':free' "
                f"OpenRouter models, free-tier providers or local models are allowed")
    host = (urlsplit(provider.get("base_url") or "").hostname or "").lower()
    if host.endswith("openrouter.ai") and name.endswith(":free"):
        return ""
    if (model.get("caps") or {}).get("free_tier") and host in _free_preset_hosts():
        return ""
    return (f"free-only policy: cloud model {name} on {host or 'default endpoint'} is not a ':free' "
            f"OpenRouter model or a connected free-tier provider model")


class FreeOnlyAdapter:
    """Refuses inference (never health/catalog reads) for a non-free cloud model."""

    def __init__(self, adapter, reason: str):
        self.adapter, self.reason = adapter, reason

    def __getattr__(self, name):
        return getattr(self.adapter, name)

    async def chat(self, *args, **kwargs):
        raise ProviderError(self.reason, kind="budget")


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
