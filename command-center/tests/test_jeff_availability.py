"""Jeff availability contract for the owner run (Bossman 1.9).

- free-only routing regression: Jeff never reaches a paid or unverified remote model;
- heartbeat: availability, latency, engines, queue, errors, poller count, no secrets;
- a task STOP does not stop Jeff, the global STOP does and leaves exactly zero pollers;
- heavy Studio/render work lives in other code paths and cannot block a Jeff reply.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from bcc.pit import heartbeat as hb
from bcc.pit import runtime as rt
from bcc.pit.router import ModelEndpoint
from bcc.providers import ChatResult
from bcc.telegram_companion.config import CompanionError

from .test_pit_runtime import FakeAdapter, make_runtime, make_settings, message


# -- free-only routing ---------------------------------------------------------------------------
def test_catalog_admits_only_verified_zero_price_free_models(tmp_path):
    pricing = {
        "vendor/paid-model": {"prompt": 0.000001, "completion": 0.000002},       # paid
        "vendor/renamed:free": {"prompt": 0.000001, "completion": 0.0},          # ':free' name, real price
        "vendor/zero-but-not-free": {"prompt": 0.0, "completion": 0.0},          # zero, but not a ':free' id
        "vendor/unknown-price:free": None,                                       # no price data
        "vendor/real:free": {"prompt": 0.0, "completion": 0.0},
    }
    adapter = FakeAdapter(pricing={k: v for k, v in pricing.items() if v is not None})
    adapter.pricing.pop("vendor/unknown-price:free", None)
    settings = dataclasses.replace(make_settings(tmp_path), chat_models=tuple(pricing))
    runtime = make_runtime(tmp_path, adapter=adapter, settings=settings)
    try:
        catalog = asyncio.run(runtime.refresh_catalog())
        assert set(catalog) == {"vendor/real:free"}
        assert all(e.zero_cost and not e.paid for e in catalog.values())
    finally:
        asyncio.run(runtime.close())


def test_a_failing_free_model_never_falls_back_to_a_paid_one(tmp_path):
    adapter_calls: list[str] = []

    class Flaky(FakeAdapter):
        async def chat(self, model, messages, **kw):
            adapter_calls.append(model)
            if model == "free/a:free":
                raise TimeoutError("slow")
            return ChatResult(text="Ответ из бесплатного резерва.", model=model)

    pricing = {"free/a:free": {"prompt": 0.0, "completion": 0.0},
               "free/b:free": {"prompt": 0.0, "completion": 0.0},
               "paid/x": {"prompt": 0.01, "completion": 0.01}}
    settings = dataclasses.replace(make_settings(tmp_path), chat_models=tuple(pricing))
    runtime = make_runtime(tmp_path, adapter=Flaky(pricing=pricing), settings=settings)
    try:
        asyncio.run(runtime.refresh_catalog())
        person = settings.people[0]
        from .test_pit_runtime import warm
        warm(runtime, runtime.vault.key_for_telegram(person.user_id))
        answer = asyncio.run(runtime.handle(person, message("Привет", message_id=31)))
        assert "бесплатного" in answer
        assert adapter_calls and set(adapter_calls) <= {"free/a:free", "free/b:free"}
        assert runtime._max_cost_usd() == 0.0
    finally:
        asyncio.run(runtime.close())


def test_master_parser_and_runtime_share_the_free_only_rule():
    parser = Path(rt.__file__).parent / "master_parser" / "engine.py"
    assert 'endswith(":free")' in parser.read_text(encoding="utf-8")
    src = Path(rt.__file__).read_text(encoding="utf-8")
    assert 'endswith(":free")' in src and "allow_paid=False" in src and "allow_paid=True" not in src


# -- heartbeat -----------------------------------------------------------------------------------
def test_heartbeat_records_engines_latency_errors_without_secrets(tmp_path):
    beat = hb.Heartbeat(tmp_path, "telegram")
    beat.note_route(model="free/a:free", provider="remote", ok=True, latency_ms=900)
    beat.note_route(model="free/a:free", provider="remote", ok=False, latency_ms=30000, error="timeout")
    beat.note_poll(True)
    payload = beat.write(queue=2, stt={"available": True, "engine": "local"}, tts={"available": False})
    got = hb.read(tmp_path)
    assert got["availability"] == "up" and got["replies_ok"] == 1 and got["replies_failed"] == 1
    assert got["last_reply_latency_ms"] == 900 and got["avg_latency_ms"] == 900
    assert got["llm"] == {"model": "free/a:free", "provider": "cloud_free"}
    assert got["stt"]["available"] is True and got["tts"]["available"] is False and got["queue"] == 2
    assert got["last_error"]["kind"] == "timeout" and got["telegram"]["last_poll_ok_at"]
    assert payload["pid"] > 0
    text = json.dumps(got)
    for banned in ("token", "key", "text", "user_id"):
        assert banned not in {k.lower() for k in got}, banned
    assert "Bearer" not in text


def test_heartbeat_goes_stale_and_stopped_is_distinct(tmp_path):
    beat = hb.Heartbeat(tmp_path, "web")
    payload = beat.snapshot()
    payload["at_epoch"] = int(time.time()) - hb.STALE_AFTER_SECONDS - 5
    hb.write_file(tmp_path / hb.FILE_NAME, payload)
    assert hb.read(tmp_path)["availability"] == "stale"
    beat.write(state="stopped")
    assert hb.read(tmp_path)["availability"] == "stopped"
    assert hb.read(tmp_path / "missing") is None


class _Idle:
    """Telegram stand-in: long-poll that never returns until released."""
    def __init__(self):
        self.release = asyncio.Event()

    async def preflight(self): return None
    async def call(self, method, payload):
        await self.release.wait()
        return []
    async def send(self, person, text, **kw): return 1
    async def close(self): return None


async def test_running_jeff_beats_and_global_stop_marks_it_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "HEARTBEAT_SECONDS", 0.05)
    runtime = make_runtime(tmp_path)
    runtime.telegram = _Idle()

    async def no_catalog():
        return None
    runtime.refresh_catalog_safe = no_catalog
    running = asyncio.create_task(runtime.run())
    try:
        deadline = time.monotonic() + 5
        while hb.read(runtime.home) is None and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        live = hb.read(runtime.home)
        assert live and live["availability"] == "up" and live["surface"] == "telegram"
        (runtime.home / rt.STOP_FLAG).write_text("owner STOP", encoding="utf-8")
        await asyncio.wait_for(asyncio.shield(running), timeout=5)
        assert hb.read(runtime.home)["availability"] == "stopped"
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await runtime.close()


def test_reply_updates_last_success_and_latency(tmp_path):
    runtime = make_runtime(tmp_path)
    try:
        asyncio.run(runtime.refresh_catalog())
        person = runtime.settings.people[0]
        from .test_pit_runtime import warm
        warm(runtime, runtime.vault.key_for_telegram(person.user_id))
        asyncio.run(runtime.handle(person, message("Привет", message_id=41)))
        snap = runtime.heartbeat_snapshot("running")
        assert snap["replies_ok"] >= 1 and snap["last_reply_at"] and snap["llm"]["model"] == "free/model:free"
        assert snap["queue"] >= 0
    finally:
        asyncio.run(runtime.close())


# -- STOP semantics ----------------------------------------------------------------------------------
async def test_task_stop_does_not_stop_jeff_but_global_stop_does(env, monkeypatch):
    from bcc.pit import cli as pit_cli
    from bcc.pit.runtime import STOP_FLAG
    from .helpers import make_stack
    home = env.settings.data_dir / "pit-v1.7"
    home.mkdir(parents=True, exist_ok=True)
    (home / "poller.lock").touch()
    monkeypatch.setattr(pit_cli, "_is_running", lambda h: True)
    stack = await make_stack(env.client)
    task_id = stack["task"]["id"]
    assert (await env.client.post(f"/api/tasks/{task_id}/stop")).status_code == 200
    assert not (home / STOP_FLAG).exists()                       # Jeff untouched by a task STOP
    preview = (await env.client.get("/api/control-plane/active")).json()
    assert preview["active"]["pit"] == ["Jeff"]                   # ... and still counted as live
    await env.client.post("/api/control-plane/stop-all")
    assert (home / STOP_FLAG).is_file()                           # the global STOP reaches Jeff


def test_after_stop_no_second_poller_can_start_and_the_lock_is_released(tmp_path):
    from bcc.telegram_companion.store import single_instance
    from bcc.pit.bot_guard import token_poller_lock
    home = tmp_path / "pit-v1.7"
    with single_instance(home):
        with pytest.raises(CompanionError):
            with single_instance(home):
                pass                                              # never two pollers on one home
        with token_poller_lock("123456:ABC-fixture"):
            with pytest.raises(CompanionError):
                with token_poller_lock("123456:ABC-fixture"):
                    pass                                          # nor on one bot token
    with single_instance(home):                                   # released after the stop
        with token_poller_lock("123456:ABC-fixture"):
            pass


async def test_a_blocked_render_thread_does_not_delay_a_jeff_reply(tmp_path):
    """Heavy work runs in threads/processes elsewhere; Jeff's loop keeps answering."""
    runtime = make_runtime(tmp_path)
    stop = threading.Event()
    busy = threading.Thread(target=lambda: stop.wait(5), daemon=True)   # stands in for a render
    busy.start()
    try:
        await runtime.refresh_catalog()
        person = runtime.settings.people[0]
        from .test_pit_runtime import warm
        warm(runtime, runtime.vault.key_for_telegram(person.user_id))
        started = time.monotonic()
        answer = await runtime.handle(person, message("Привет", message_id=51))
        assert answer and time.monotonic() - started < 3
    finally:
        stop.set()
        await runtime.close()


def test_jeff_process_does_not_load_the_studio_or_render_code():
    code = ("import sys, bcc.pit.runtime, bcc.pit.web; "
            "bad=[m for m in sys.modules if m.startswith(('bcc.features.video_studio','bcc.features.studio',"
            "'bcc.features.music_studio','bcc.engine'))]; print('BAD' if bad else 'CLEAN', bad)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                         cwd=str(Path(__file__).resolve().parents[1]), timeout=120)
    assert out.stdout.strip().startswith("CLEAN"), (out.stdout, out.stderr[-1500:])


# -- exposure through the existing backend ---------------------------------------------------------
async def test_owner_status_and_pit_status_expose_the_heartbeat(env, capsys):
    from .test_jeff_settings_overlay import TG_A, TG_B
    from bcc.pit.config import config_path, pit_home, save_setup
    from bcc.telegram_companion.config import Person
    data = Path(env.settings.data_dir)
    save_setup(config_path(data), people=[Person(TG_A, TG_A, "owner"), Person(TG_B, TG_B, "guest")],
               chat_models=["free/model:free"], provider_base_url="http://127.0.0.1:9/v1",
               core_url="http://127.0.0.1:8800", bot_token="fixture-bot-token", provider_key="fixture-key")
    hb.Heartbeat(pit_home(data), "telegram").write(queue=1)
    got = (await env.client.get("/api/jeff-settings/status")).json()
    assert got["heartbeat"]["telegram"]["availability"] == "up" and got["heartbeat"]["window"] is None
    assert got["heartbeat"]["poller_processes"] in (0, 1) and "fixture-bot-token" not in json.dumps(got)
    from bcc.pit import cli
    cli.cmd_status(config_path(data))
    report = json.loads(capsys.readouterr().out)
    assert report["heartbeat"]["telegram"]["availability"] == "up"


def test_window_health_endpoint_carries_the_heartbeat(tmp_path):
    from .test_pit_web import RecordingAdapter, client_for, make_app
    app, _ = make_app(tmp_path, RecordingAdapter("Ок."))
    with client_for(app) as client:
        body = client.get("/api/jeff/health").json()
    assert body["ok"] is True and body["heartbeat"]["surface"] == "web"
    assert set(body["heartbeat"]) >= {"state", "last_reply_at", "llm", "stt", "tts", "queue", "pid"}
