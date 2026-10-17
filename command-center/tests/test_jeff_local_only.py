"""Jeff answers ONLY through a local (loopback) model: owner decision 2026-10-17.

config.load() is the production path (bossman pit start / Jeff window / calls); it must never yield a cloud route.
Synthetic identities, fake local adapters, an HTTP tripwire for any non-loopback request. No network."""
from __future__ import annotations

import asyncio
import ipaddress
import json

import httpx
import pytest

from bcc.pit import config as pit_config
from bcc.pit import runtime as rt
from bcc.pit.config import DEFAULT_LOCAL_MODEL, DEFAULT_LOCAL_URL, config_path, load, save_setup
from bcc.pit.models import ConsentState
from bcc.providers import ProviderError
from bcc.telegram_companion.config import Person

CLOUD = ["nvidia/nemotron-3-ultra-550b-a55b:free", "google/gemma-4-31b-it:free"]


def setup(tmp_path, **kw):
    path = config_path(tmp_path)
    save_setup(path, people=[Person(101, 101, "owner")], chat_models=CLOUD,
               provider_base_url="https://openrouter.ai/api/v1", core_url="http://127.0.0.1:8800",
               bot_token="fixture-bot-token", provider_key="fixture-cloud-key", **kw)
    return path


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for name in ("BOSSMAN_PIT_LOCAL_URL", "BOSSMAN_PIT_LOCAL_MODELS", "BOSSMAN_PIT_PROVIDER_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def tripwire(monkeypatch):
    """Records every httpx request; a non-loopback host is a test failure (and never leaves the machine)."""
    hosts: list[str] = []
    real_send = httpx.AsyncClient.send

    async def send(self, request, *args, **kwargs):
        host = request.url.host
        hosts.append(host)
        try:
            loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise AssertionError(f"Jeff tried to reach a cloud host: {host}")
        return await real_send(self, request, *args, **kwargs)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return hosts


def test_configured_cloud_models_are_ignored_and_the_owner_local_model_wins(tmp_path):
    setup(tmp_path, local_url="http://127.0.0.1:11434/v1",
          local_models=["bossman-community-qwen-uncensored:latest", "other:latest"])
    settings = load(config_path(tmp_path))
    assert settings.local_chat_only is True
    assert settings.chat_models == ()
    assert settings.local_models == ("bossman-community-qwen-uncensored:latest",)
    assert settings.ignored_cloud_models == tuple(CLOUD)            # negative control: they were configured
    assert settings.provider_key == "" and settings.cloud_daily_request_budget == 0
    assert settings.local_fallback_on_cloud_refusal is False


def test_without_a_local_model_jeff_uses_the_local_uncensored_default(tmp_path):
    setup(tmp_path)
    settings = load(config_path(tmp_path))
    assert settings.local_url == DEFAULT_LOCAL_URL and settings.local_models == (DEFAULT_LOCAL_MODEL,)
    assert "uncensored" in DEFAULT_LOCAL_MODEL


def test_env_override_picks_the_local_model(tmp_path, monkeypatch):
    setup(tmp_path)
    monkeypatch.setenv("BOSSMAN_PIT_LOCAL_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("BOSSMAN_PIT_LOCAL_MODELS", "bossman-community-qwen-uncensored-ctx64k:latest")
    monkeypatch.setenv("BOSSMAN_PIT_PROVIDER_KEY", "env-cloud-key")
    settings = load(config_path(tmp_path))
    assert settings.local_models == ("bossman-community-qwen-uncensored-ctx64k:latest",)
    assert settings.provider_key == ""


def test_a_cloud_endpoint_disguised_as_the_local_url_is_refused(tmp_path):
    path = setup(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(local_url="https://openrouter.ai/api/v1", local_models=["nvidia/nemotron-3-super-120b-a12b:free"])
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="loopback"):
        load(path)


def test_route_resolution_never_returns_a_cloud_provider(tmp_path):
    setup(tmp_path, local_url="http://127.0.0.1:11434/v1", local_models=["bossman-uncensored-chat:latest"])
    runtime = rt.ParticipantRuntime(load(config_path(tmp_path)))
    assert isinstance(runtime.adapter, rt.CloudDisabledAdapter)

    class Local:
        async def list_model_info(self):
            return [{"id": "bossman-uncensored-chat:latest"}]
    runtime.local_adapter = Local()

    async def allowed():
        return True
    runtime.capacity_guard.local_allowed = allowed
    asyncio.run(runtime.refresh_catalog())
    assert all(endpoint.local for endpoint in runtime.catalog.values()) and runtime.catalog
    model, is_local = runtime._free_route()
    assert (model, is_local) == ("bossman-uncensored-chat:latest", True)
    assert runtime._remote_fallbacks(model) == ()
    status = runtime.model_route_status()
    assert status["free_remote"] == [] and status["local_only"] is True and status["cloud_ignored"] == CLOUD


def test_cloud_adapter_refuses_every_call():
    with pytest.raises(Exception, match="CLOUD_DISABLED_FOR_JEFF"):
        asyncio.run(rt.CloudDisabledAdapter().chat("nvidia/nemotron-3-ultra-550b-a55b:free", []))


def test_local_down_is_an_honest_error_and_never_a_cloud_call(tmp_path, tripwire):
    setup(tmp_path, local_url="http://127.0.0.1:11434/v1", local_models=["bossman-uncensored-chat:latest"])
    runtime = rt.ParticipantRuntime(load(config_path(tmp_path)))
    runtime.local_settle_seconds = 0.0
    calls_before = rt.CloudDisabledAdapter.calls

    class Down:
        async def list_model_info(self):
            raise ProviderError("локальная модель не отвечает", kind="network")

        async def chat(self, *a, **kw):
            raise ProviderError("локальная модель не отвечает", kind="network")
    runtime.local_adapter = Down()
    person = runtime.settings.people[0]
    runtime.vault.set_consent(runtime.vault.key_for_telegram(person.user_id),
                              ConsentState(memory_enabled=True, remote_processing_enabled=True))
    answer = asyncio.run(runtime.handle(person, {"_user_id": 101, "_chat_id": 101, "_message_id": 5,
                                                 "text": "Сколько планет в Солнечной системе?", "_photo": "",
                                                 "_document": None, "_voice": False}))
    assert answer in {rt.NO_MODEL_RU, rt.PROVIDER_DOWN_RU}
    assert "недоступ" in answer or "нет доступной" in answer
    assert rt.CloudDisabledAdapter.calls == calls_before                       # the cloud adapter was never asked
    assert all(host in {"127.0.0.1", "localhost", "::1"} for host in tripwire)   # and no cloud host was reached


def test_studio_media_uses_only_local_engines():
    from bcc.pit.studio_image_edit import LOCAL_STUDIO_PROVIDERS
    assert "openrouter" not in LOCAL_STUDIO_PROVIDERS and {"sdcpp", "comfyui"} <= LOCAL_STUDIO_PROVIDERS


def test_the_policy_switch_is_on():
    assert pit_config.JEFF_LOCAL_ONLY is True
