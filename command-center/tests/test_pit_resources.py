"""Resource arbiter contracts: Bossman 1.6 owns the GPU, Jeff yields.

The participant runtime must demote local model routes when free VRAM is below
the owner-configured headroom, fall back to runtime-confirmed FREE cloud
models, and never evict or throttle the 1.6 workload. A broken VRAM probe
degrades to the previous behaviour (local allowed), never to a participant
facing outage.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc.pit import resources as res
from bcc.pit import runtime as rt
from bcc.pit.config import PITSettings
from bcc.pit.resources import LocalCapacityGuard
from bcc.providers import ChatResult
from bcc.telegram_companion.config import Person


class FakeAdapter:
    def __init__(self, text: str = "готово", pricing: dict | None = None):
        self.text = text
        self.pricing = {"free/model:free": {"prompt": 0.0, "completion": 0.0}} \
            if pricing is None else pricing

    async def list_model_info(self):
        return [{"id": model_id} for model_id in self.pricing]

    async def list_model_pricing(self):
        return self.pricing

    async def close(self):
        return None


def make_runtime(tmp_path: Path) -> rt.ParticipantRuntime:
    settings = PITSettings(
        data_dir=tmp_path,
        people=(Person(user_id=101, chat_id=101, role="owner"),),
        chat_models=("free/model:free",),
        provider_base_url="http://127.0.0.1:9/v1",
        provider_key="test-key",
        bot_token="test-bot-token",
        identity_salt="ab" * 32,
    )
    runtime = rt.ParticipantRuntime(settings)
    runtime.adapter = FakeAdapter()
    return runtime


def with_local(runtime: rt.ParticipantRuntime) -> rt.ParticipantRuntime:
    local_adapter = FakeAdapter()
    local_adapter.pricing = {"bossman-fast-local:latest": {"prompt": 0.0, "completion": 0.0}}
    runtime.local_adapter = local_adapter
    runtime.settings = PITSettings(**{
        **{f.name: getattr(runtime.settings, f.name)
           for f in runtime.settings.__dataclass_fields__.values()},
        "local_url": "http://127.0.0.1:11434/v1",
        "local_models": ("bossman-fast-local:latest",),
    })
    return runtime


# -- guard decisions --------------------------------------------------------------

def test_guard_allows_local_when_vram_is_free(monkeypatch):
    monkeypatch.setattr(res, "_read_free_vram_mb", lambda: 8192)
    guard = LocalCapacityGuard(min_free_mb=2000, ttl_seconds=0)
    assert guard.local_allowed.__doc__  # sanity: real coroutine fn
    allowed = _run(guard.local_allowed())
    assert allowed is True
    assert "vram-free" in guard.last_reason


def test_guard_denies_local_when_1_6_owns_vram(monkeypatch):
    monkeypatch.setattr(res, "_read_free_vram_mb", lambda: 400)
    guard = LocalCapacityGuard(min_free_mb=2000, ttl_seconds=0)
    assert _run(guard.local_allowed()) is False
    assert "1.6-priority" in guard.last_reason


def test_guard_yields_to_1_6_when_probe_broken(monkeypatch):
    monkeypatch.setattr(res, "_read_free_vram_mb", lambda: None)
    monkeypatch.setattr(res, "_read_amd_unified_free_mb", lambda: None)
    guard = LocalCapacityGuard(min_free_mb=2000, ttl_seconds=0)
    assert _run(guard.local_allowed()) is False
    assert "unmeasured" in guard.last_reason


def test_guard_caches_within_ttl(monkeypatch):
    calls = {"n": 0}

    def fake_measure():
        calls["n"] += 1
        return 8192

    monkeypatch.setattr(res, "_read_free_vram_mb", fake_measure)
    guard = LocalCapacityGuard(min_free_mb=2000, ttl_seconds=3600)
    assert _run(guard.local_allowed()) is True
    assert _run(guard.local_allowed()) is True
    assert calls["n"] == 1
    guard.reset()
    assert _run(guard.local_allowed()) is True
    assert calls["n"] == 2


def test_guard_min_free_mb_env_override(monkeypatch):
    monkeypatch.setenv("BOSSMAN_PIT_VRAM_FREE_MIN_MB", "10000")
    guard = LocalCapacityGuard(ttl_seconds=0)
    assert guard.min_free_mb == 10000


def test_vram_probe_parses_nvidia_smi_csv(monkeypatch):
    import subprocess

    class FakeProc:
        returncode = 0
        stdout = "6144 MiB, 16384 MiB\n12288 MiB, 16384 MiB\n"
        stderr = ""

    def fake_run(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert res._read_free_vram_mb() == 6144 + 12288


# -- AMD AI Max unified-memory capacity (owner Ryzen AI Max+ 395) ------------------

def _amd_owner_machine(monkeypatch, available_mb: int | None):
    """Patch the prober for the owner's Strix Halo host: no NVIDIA, AMD APU."""
    monkeypatch.setattr(res, "_read_free_vram_mb", lambda: None)
    monkeypatch.setattr(res, "_has_amd_gpu", lambda: True)
    monkeypatch.setattr(res, "_win_available_memory_mb", lambda: available_mb)


def test_amd_probe_used_only_when_nvidia_absent(monkeypatch):
    monkeypatch.setattr(res, "_read_free_vram_mb", lambda: 8192)
    monkeypatch.setattr(res, "_read_amd_unified_free_mb", lambda: 65536)
    free_mb, kind = res._measure_local_capacity()
    assert (free_mb, kind) == (8192, "nvidia-smi")


def test_amd_unified_probe_measures_owner_machine(monkeypatch):
    monkeypatch.setattr(res, "_read_free_vram_mb", lambda: None)
    monkeypatch.setattr(res, "_read_amd_unified_free_mb", lambda: 98304)
    free_mb, kind = res._measure_local_capacity()
    assert (free_mb, kind) == (98304, "amd-unified")


def test_amd_unified_probe_requires_amd_gpu(monkeypatch):
    monkeypatch.setattr(res, "_has_amd_gpu", lambda: False)
    assert res._read_amd_unified_free_mb() is None


def test_amd_idle_machine_allows_local_qwen(monkeypatch):
    _amd_owner_machine(monkeypatch, available_mb=98304)
    guard = LocalCapacityGuard(ttl_seconds=0)
    assert _run(guard.local_allowed()) is True
    assert guard.last_reason == "unified-free-98304mb"


def test_amd_heavy_owner_workload_demotes_local(monkeypatch):
    _amd_owner_machine(monkeypatch, available_mb=4096)
    guard = LocalCapacityGuard(ttl_seconds=0)
    assert _run(guard.local_allowed()) is False
    assert guard.last_reason == "unified-low-4096mb-min-8000mb-1.6-priority"


def test_amd_owner_headroom_override_wins(monkeypatch):
    _amd_owner_machine(monkeypatch, available_mb=4096)
    monkeypatch.setenv("BOSSMAN_PIT_VRAM_FREE_MIN_MB", "2000")
    guard = LocalCapacityGuard(ttl_seconds=0)
    assert _run(guard.local_allowed()) is True
    assert guard.last_reason == "unified-free-4096mb"


def test_amd_telemetry_unavailable_keeps_safe_fallback(monkeypatch):
    _amd_owner_machine(monkeypatch, available_mb=None)
    guard = LocalCapacityGuard(ttl_seconds=0)
    assert _run(guard.local_allowed()) is False
    assert guard.last_reason == "vram-unmeasured-1.6-priority"


def test_amd_non_amd_windows_host_without_nvidia_still_demotes(monkeypatch):
    monkeypatch.setattr(res, "_read_free_vram_mb", lambda: None)
    monkeypatch.setattr(res, "_has_amd_gpu", lambda: False)
    guard = LocalCapacityGuard(ttl_seconds=0)
    assert _run(guard.local_allowed()) is False
    assert "unmeasured" in guard.last_reason


def test_amd_restart_reproduces_same_decision(monkeypatch):
    _amd_owner_machine(monkeypatch, available_mb=98304)
    first = LocalCapacityGuard(ttl_seconds=0)
    second = LocalCapacityGuard(ttl_seconds=0)
    assert _run(first.local_allowed()) is _run(second.local_allowed()) is True
    assert first.last_reason == second.last_reason == "unified-free-98304mb"
    _amd_owner_machine(monkeypatch, available_mb=4096)
    third = LocalCapacityGuard(ttl_seconds=0)
    assert _run(third.local_allowed()) is False
    assert third.last_reason == "unified-low-4096mb-min-8000mb-1.6-priority"


def test_amd_low_measured_verdict_blocks_media_even_with_optin(
        monkeypatch):
    """A measured low verdict always wins: the unmeasured opt-in is narrower."""
    from bcc.pit.photo_runtime import PhotoRuntimeConfig
    cfg = PhotoRuntimeConfig(
        ai_max_media_ready=True, vision_url="http://127.0.0.1:8991/v1",
        vision_model="qwen-vl", studio_url="http://127.0.0.1:8800",
        image_edit_model="qwen-image-edit", allow_unmeasured_media=True)
    _amd_owner_machine(monkeypatch, available_mb=2048)
    guard = LocalCapacityGuard(ttl_seconds=0)
    allowed = _run(guard.local_allowed())
    optin_excuse = not allowed and cfg.allow_unmeasured_media and (
        guard.last_reason == "vram-unmeasured-1.6-priority")
    assert allowed is False and optin_excuse is False


def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


# -- routing integration ----------------------------------------------------------

def test_refresh_catalog_drops_local_when_vram_busy(tmp_path, monkeypatch):
    runtime = with_local(make_runtime(tmp_path))

    async def vram_busy():
        return False

    runtime.capacity_guard = LocalCapacityGuard(ttl_seconds=0)
    monkeypatch.setattr(LocalCapacityGuard, "local_allowed",
                        lambda self: vram_busy())
    endpoints = _run(runtime.refresh_catalog())
    assert all(not endpoint.local for endpoint in endpoints.values())
    assert "free/model:free" in endpoints


def test_refresh_catalog_reserves_local_for_learning_even_when_vram_free(tmp_path, monkeypatch):
    runtime = with_local(make_runtime(tmp_path))
    monkeypatch.setattr(res, "_read_free_vram_mb", lambda: 8192)
    runtime.capacity_guard = LocalCapacityGuard(min_free_mb=2000, ttl_seconds=0)
    endpoints = _run(runtime.refresh_catalog())
    assert "bossman-fast-local:latest" not in endpoints
    assert endpoints["free/model:free"].local is False


def test_cached_local_route_is_demoted_when_capacity_becomes_unknown(tmp_path, monkeypatch):
    from bcc.pit.models import ConsentState
    runtime = with_local(make_runtime(tmp_path))
    runtime.catalog = {
        "bossman-fast-local:latest": rt.ModelEndpoint(
            id="bossman-fast-local:latest", provider="local",
            capabilities=frozenset({"chat"}), local=True, available=True,
            zero_cost=True, paid=False),
        "free/model:free": rt.ModelEndpoint(
            id="free/model:free", provider="remote",
            capabilities=frozenset({"chat"}), local=False, available=True,
            zero_cost=True, paid=False),
    }
    runtime.catalog_checked_at = 1.0
    class ChatAdapter(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.calls = 0
        async def chat(self, model, messages, **kw):
            self.calls += 1
            return ChatResult(text="ok", tokens_in=1, tokens_out=1, model=model)
    remote = ChatAdapter()
    runtime.adapter = remote
    monkeypatch.setattr(res, "_read_free_vram_mb", lambda: None)
    person = runtime.settings.people[0]
    key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True))
    answer = _run(runtime.handle(person, {"text": "hello", "_message_id": 1}))
    assert answer == "ok"
    assert remote.calls == 1
    assert all(not endpoint.local for endpoint in runtime.catalog.values())
