"""The first Studio listing hashes multi-GB weights; it must not freeze the server.

Measured on the owner machine 2026-09-22: the first GET /api/studio/models took 62 s,
and for those 62 s every other request (task polls, UI, Telegram) timed out, because
runtime.models() called the sha256 health check synchronously inside the event loop.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from bcc.studio import runtime
from bcc.studio.providers import sdcpp

BLOCK_S = 1.0


async def test_hashing_during_listing_leaves_the_event_loop_responsive(env, monkeypatch):
    def slow_engine_files(cfg, model_id):
        time.sleep(BLOCK_S)                      # stands in for hashing gigabytes
        return {"model": Path("weights.gguf")}

    monkeypatch.setattr(sdcpp, "configuration", lambda *a, **k: {"bin": "sd-cli.exe", "manifest": {}})
    monkeypatch.setattr(sdcpp, "engine_files", slow_engine_files)
    if not any(m["provider"] == "sdcpp" for m in runtime.model_specs()):
        import pytest
        pytest.skip("no sd.cpp model in the catalog")

    ticks: list[float] = []

    async def ticker():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.05)

    t = asyncio.create_task(ticker())
    try:
        started = time.monotonic()
        listed = await runtime.models(env.svc)
        elapsed = time.monotonic() - started
    finally:
        t.cancel()
    assert any(m["provider"] == "sdcpp" and m["configured"] for m in listed)
    # the loop kept running while the hash ran: no gap between ticks near the hash time
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert elapsed >= BLOCK_S * 0.9
    assert gaps and max(gaps) < BLOCK_S * 0.5, f"event loop frozen for {max(gaps):.2f} s"
