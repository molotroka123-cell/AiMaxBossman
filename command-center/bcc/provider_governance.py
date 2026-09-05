"""Check actual provider locality and price on EVERY inference, including fallback."""
import math

from bossman_shared.privacy import assert_provider_egress
from .v2.model_router import derive_local
from .providers import ProviderError, is_local_url
from .fable_cap import CappedAdapter


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
        return await self.adapter.chat(*args, **kwargs)
