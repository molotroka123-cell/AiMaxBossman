"""Jeff during a generation (owner 2026-10-18): free cloud answers only while the resource governor's heartbeat file is
fresh; otherwise the local-only rule of 2026-10-17 stays in force. Synthetic identities, fake adapters, no network."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import time

import pytest

from bcc.pit import config as pit_config
from bcc.pit import runtime as rt
from bcc.pit.config import (GENERATION_CLOUD_FILE, GENERATION_CLOUD_SCHEMA, config_path, generation_cloud_override,
                            load, pit_home, save_setup)
from bcc.telegram_companion.config import Person

from .test_pit_runtime import FakeAdapter, make_runtime, make_settings, message, warm

FREE = "nvidia/nemotron-3-ultra-550b-a55b:free"


def write_flag(home, models=(FREE,), beat=None, schema=GENERATION_CLOUD_SCHEMA):
    home.mkdir(parents=True, exist_ok=True)
    (home / GENERATION_CLOUD_FILE).write_text(json.dumps(
        {"schema": schema, "models": list(models), "heartbeat_at": time.time() if beat is None else beat}),
        encoding="utf-8")


def setup(tmp_path):
    path = config_path(tmp_path)
    save_setup(path, people=[Person(101, 101, "owner")], chat_models=[FREE],
               provider_base_url="https://openrouter.ai/api/v1", core_url="http://127.0.0.1:8800",
               bot_token="fixture-bot-token", provider_key="fixture-cloud-key",
               local_url="http://127.0.0.1:11434/v1", local_models=["gemma4-31b-uncensored:latest"])
    return path


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for name in ("BOSSMAN_PIT_LOCAL_URL", "BOSSMAN_PIT_LOCAL_MODELS", "BOSSMAN_PIT_PROVIDER_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_no_flag_means_local_only(tmp_path):
    s = load(setup(tmp_path))
    assert s.local_chat_only is True and s.generation_cloud is False and s.provider_key == ""


def test_fresh_flag_gives_free_cloud_and_no_local_model_at_all(tmp_path):
    path = setup(tmp_path)
    write_flag(pit_home(tmp_path))
    s = load(path)
    assert s.generation_cloud is True and s.local_chat_only is False
    assert s.chat_models == (FREE,)
    assert s.local_models == () and s.local_url == ""            # nothing can ever load the local model
    assert s.local_fallback_on_cloud_refusal is False and s.local_share_percent == 0
    assert s.provider_key == "fixture-cloud-key"


@pytest.mark.parametrize("flag", [
    dict(beat_offset=-301),                                       # stale: governor died
    dict(beat_offset=600),                                        # from the future
    dict(models=("some/paid-model",)),                            # not a :free id
    dict(models=("liquid/lfm-2.5-2.6b:free",)),                   # banned family
    dict(models=()),                                              # empty
    dict(models=(FREE, FREE)),                                    # duplicate
    dict(schema="other/1"),
])
def test_bad_flag_keeps_local_only(tmp_path, flag):
    path = setup(tmp_path)
    kw = dict(flag)
    if "beat_offset" in kw:
        kw["beat"] = time.time() + kw.pop("beat_offset")
    write_flag(pit_home(tmp_path), **kw)
    s = load(path)
    assert s.generation_cloud is False and s.local_chat_only is True
    assert s.local_models == ("gemma4-31b-uncensored:latest",) and s.provider_key == ""


def test_garbage_flag_keeps_local_only(tmp_path):
    path = setup(tmp_path)
    (pit_home(tmp_path) / GENERATION_CLOUD_FILE).write_text("{not json", encoding="utf-8")
    assert load(path).generation_cloud is False
    (pit_home(tmp_path) / GENERATION_CLOUD_FILE).write_text(json.dumps([1]), encoding="utf-8")
    assert generation_cloud_override(pit_home(tmp_path)) is None


def cloud_runtime(tmp_path):
    settings = dataclasses.replace(make_settings(tmp_path), chat_models=(FREE,), generation_cloud=True)
    runtime = make_runtime(tmp_path, settings=settings,
                           adapter=FakeAdapter(text="Привет.", pricing={FREE: {"prompt": 0.0, "completion": 0.0}}))
    warm(runtime, runtime.vault.key_for_telegram(101))
    return runtime


def test_first_cloud_answer_carries_the_marker_once(tmp_path):
    runtime = cloud_runtime(tmp_path)
    write_flag(pit_home(tmp_path))
    person = runtime.settings.people[0]
    first = asyncio.run(runtime.handle(person, message("расскажи о задаче", message_id=900)))
    second = asyncio.run(runtime.handle(person, message("и ещё немного", message_id=901)))
    assert first.startswith(pit_config.GENERATION_CLOUD_MARKER_RU)
    assert not second.startswith(pit_config.GENERATION_CLOUD_MARKER_RU)
    assert len(runtime.adapter.calls) == 2 and all(c[0] == FREE for c in runtime.adapter.calls)
    asyncio.run(runtime.close())


def test_dead_governor_stops_cloud_answers_and_never_falls_back_to_a_model(tmp_path):
    runtime = cloud_runtime(tmp_path)
    write_flag(pit_home(tmp_path), beat=time.time() - 3600)
    answer = asyncio.run(runtime.handle(runtime.settings.people[0], message("расскажи о задаче", message_id=910)))
    assert answer == rt.GENERATION_CLOUD_ENDED_RU
    assert runtime.adapter.calls == [] and runtime.local_adapter is None
    asyncio.run(runtime.close())


def test_normal_local_runtime_has_no_marker_and_no_check(tmp_path):
    runtime = make_runtime(tmp_path)
    assert runtime.settings.generation_cloud is False
    warm(runtime, runtime.vault.key_for_telegram(101))
    answer = asyncio.run(runtime.handle(runtime.settings.people[0], message("привет", message_id=920)))
    assert pit_config.GENERATION_CLOUD_MARKER_RU not in answer
    asyncio.run(runtime.close())


# ---------------------------------------------------------------- Bossman provider route (Experiential Labs style)
def provider_db(data_dir, models, key="fixture-explabs-key", base="https://gateway.example/v1"):
    import sqlite3
    from bcc.secrets import Vault
    con = sqlite3.connect(data_dir / "bcc.db")
    con.execute("create table providers (id integer primary key, base_url text, api_key_enc text)")
    con.execute("create table models (id integer primary key, provider_id int, name text, price_in real, price_out real,"
                " pricing_known int, status text)")
    con.execute("insert into providers values (13, ?, ?)", (base, Vault(data_dir).encrypt(key)))
    for i, (name, pin, pout, known, status) in enumerate(models):
        con.execute("insert into models values (?, 13, ?, ?, ?, ?, ?)", (100 + i, name, pin, pout, known, status))
    con.commit()
    con.close()


FREE_ROWS = [("deepseek-v4.1-flash-1017", 0.0, 0.0, 1, "online"), ("gemini-4-argon", 2.0, 10.0, 1, "online")]


def test_provider_route_loads_key_in_memory_and_keeps_only_free_priced_models(tmp_path):
    path = setup(tmp_path)
    provider_db(tmp_path, FREE_ROWS)
    write_flag_provider(pit_home(tmp_path), ["deepseek-v4.1-flash-1017", "gemini-4-argon"])
    s = load(path)
    assert s.generation_cloud and s.provider_base_url == "https://gateway.example/v1"
    assert s.provider_key == "fixture-explabs-key"
    assert s.chat_models == ("deepseek-v4.1-flash-1017",) == s.trusted_free_models      # the paid row is dropped
    assert s.local_models == () and s.local_url == "" and s.max_tokens >= 2048


def write_flag_provider(home, models, beat=None):
    home.mkdir(parents=True, exist_ok=True)
    (home / GENERATION_CLOUD_FILE).write_text(json.dumps(
        {"schema": GENERATION_CLOUD_SCHEMA, "models": list(models), "provider": {"bcc_provider_id": 13},
         "heartbeat_at": time.time() if beat is None else beat}), encoding="utf-8")


def test_provider_route_without_any_free_model_refuses_to_load_instead_of_going_local(tmp_path):
    path = setup(tmp_path)
    provider_db(tmp_path, [("gemini-4-argon", 2.0, 10.0, 1, "online"), ("deepseek-v4.1-flash-1017", 0.0, 0.0, 0, "online")])
    write_flag_provider(pit_home(tmp_path), ["gemini-4-argon", "deepseek-v4.1-flash-1017"])
    with pytest.raises(ValueError, match="generation cloud"):
        load(path)


@pytest.mark.parametrize("base", ["http://gateway.example/v1", "http://127.0.0.1:9/v1"])
def test_provider_route_must_be_https(tmp_path, base):
    path = setup(tmp_path)
    provider_db(tmp_path, FREE_ROWS, base=base)
    write_flag_provider(pit_home(tmp_path), ["deepseek-v4.1-flash-1017"])
    with pytest.raises(ValueError, match="https"):
        load(path)


def test_violation_file_closes_the_override_for_every_later_load(tmp_path):
    path = setup(tmp_path)
    write_flag(pit_home(tmp_path))
    assert load(path).generation_cloud is True
    pit_config.note_generation_cloud_violation(pit_home(tmp_path), FREE, 0.001)
    s = load(path)
    assert s.generation_cloud is False and s.local_chat_only is True


def trusted_runtime(tmp_path, cost):
    model = "deepseek-v4.1-flash-1017"
    settings = dataclasses.replace(make_settings(tmp_path), chat_models=(model,), generation_cloud=True,
                                   trusted_free_models=(model,))
    adapter = FakeAdapter(text="Привет.", pricing={})                    # no ':free' id and no catalog price at all

    async def chat(m, messages, **kw):
        adapter.calls.append((m, messages))
        from bcc.providers import ChatResult
        return ChatResult(text="Привет.", tokens_in=10, tokens_out=5, model=m,
                          provider_meta={"usage": {"cost": cost}} if cost is not None else {})
    adapter.chat = chat
    runtime = make_runtime(tmp_path, settings=settings, adapter=adapter)
    warm(runtime, runtime.vault.key_for_telegram(101))
    write_flag_provider(pit_home(tmp_path), [model])
    return runtime, adapter


@pytest.mark.parametrize("cost", [0, 0.0, None])
def test_trusted_free_route_answers_when_cost_is_zero_or_unreported(tmp_path, cost):
    runtime, adapter = trusted_runtime(tmp_path, cost)
    answer = asyncio.run(runtime.handle(runtime.settings.people[0], message("расскажи о задаче", message_id=930)))
    assert answer.startswith(pit_config.GENERATION_CLOUD_MARKER_RU) and "Привет" in answer
    assert len(adapter.calls) == 1
    assert not (pit_home(tmp_path) / pit_config.GENERATION_CLOUD_VIOLATION_FILE).exists()
    asyncio.run(runtime.close())


def test_trusted_free_route_with_positive_cost_discards_the_reply_and_closes_the_route(tmp_path):
    runtime, adapter = trusted_runtime(tmp_path, 0.0003)
    answer = asyncio.run(runtime.handle(runtime.settings.people[0], message("расскажи о задаче", message_id=931)))
    assert "Привет" not in answer                                         # the charged reply is never delivered
    assert (pit_home(tmp_path) / pit_config.GENERATION_CLOUD_VIOLATION_FILE).exists()
    again = asyncio.run(runtime.handle(runtime.settings.people[0], message("и ещё", message_id=932)))
    assert again == rt.GENERATION_CLOUD_ENDED_RU and len(adapter.calls) == 1   # no second (possibly paid) call
    asyncio.run(runtime.close())
