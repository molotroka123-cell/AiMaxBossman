"""Branch 5 «Cloud»: provider-client hardening. Every test uses fakes; no real API is called."""
from __future__ import annotations

import httpx
import pytest

from bcc import model_health as mh
from bcc.providers import OpenAICompatAdapter, ProviderError


# ------------------------------------------------------------------ timeouts are timeouts

async def test_a_provider_timeout_is_classified_as_timeout_not_provider_down():
    """The adapter words a timeout in Russian («не ответил за N с»); the health classifier only
    knew the English "timed out", so a slow free endpoint was filed as provider_down."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout", request=request)

    adapter = OpenAICompatAdapter(base_url="https://integrate.api.nvidia.com/v1", api_key="k",
                                  transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as info:
        await adapter.chat("m", [{"role": "user", "content": "hi"}], timeout=1)
    assert info.value.kind == "network" and info.value.timed_out is True
    assert mh.classify_exception(info.value)[0] == mh.TIMEOUT


def test_a_refused_connection_is_still_provider_down():
    """Negative control: only a real timeout moves to TIMEOUT."""
    assert mh.classify_exception(ProviderError("нет связи", kind="network"))[0] == mh.PROVIDER_DOWN
