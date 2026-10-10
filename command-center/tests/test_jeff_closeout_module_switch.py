"""Jeff closeout 10.10, gap 3: the owner switches single Jeff 2.0 modules off (jeff-settings ``j2_modules``).

Before: only the global BOSSMAN_JEFF_J2=off existed. Fakes only (no network, no model).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from bcc.pit import jeff_settings as js
from bcc.pit.j2 import Advice, J2Pipeline, TurnContext
from bcc.pit.j2.contract import BaseModule

from .test_jeff_settings_overlay import pit_setup  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _fresh_overlay_cache(monkeypatch):
    monkeypatch.delenv(js.ENV_PATH, raising=False)
    monkeypatch.delenv("BOSSMAN_JEFF_J2", raising=False)
    monkeypatch.delenv("BOSSMAN_JEFF_TZ_MIN", raising=False)
    js._cache.clear()
    js._warned.clear()
    yield
    js._cache.clear()


def run(coro):
    return asyncio.run(coro)


def ctx(text="привет"):
    return TurnContext(person_key="p" * 64, who="tg:1", text=text)


class Probe(BaseModule):
    def __init__(self, name, order=50, reply=None):
        self.name, self.order, self._reply = name, order, reply
        self.calls: list[str] = []

    async def pre_route(self, c):
        self.calls.append("pre")
        return Advice(reply=self._reply) if self._reply else None

    async def augment(self, c):
        self.calls.append("augment")
        return Advice(notes=(f"note from {self.name}",))

    async def post_reply(self, c, reply):
        self.calls.append("post")
        return reply + f" [{self.name}]"


# ------------------------------------------------------------------------------ overlay schema
def test_overlay_writes_only_switched_off_modules_and_refuses_locked_or_unknown(tmp_path):
    path = js.settings_path(tmp_path)
    assert js.j2_module_enabled(tmp_path, "research") is True                 # no file = everything on
    js.write_overlay(path, {"version": 1, "j2_modules": {"research": False, "media": True}})
    assert json.loads(path.read_text(encoding="utf-8"))["j2_modules"] == {"research": False}
    assert js.j2_module_enabled(tmp_path, "research") is False
    assert js.j2_module_enabled(tmp_path, "media") is True
    js.write_overlay(path, {"version": 1, "j2_modules": {"research": True}})
    assert "j2_modules" not in json.loads(path.read_text(encoding="utf-8"))  # all on = not written
    for bad in ({"safety": False}, {"model_guard": False}, {"nope": False}, {"research": "off"}, ["research"]):
        with pytest.raises(js.OverlayError):
            js.normalize({"version": 1, "j2_modules": bad})
    path.write_text("{broken", encoding="utf-8")
    js._cache.clear()
    assert js.j2_module_enabled(tmp_path, "research") is True                 # unreadable file never switches off
    assert js.j2_module_enabled(tmp_path, "safety") is True


# ------------------------------------------------------------------------------ pipeline
def test_switched_off_module_runs_no_hook_and_others_still_run():
    off = {"research"}
    safety, research, director = Probe("safety", 1), Probe("research", 60, reply="из сети"), Probe("director", 30)
    pipe = J2Pipeline([safety, research, director], module_switch=lambda name: name not in off)

    assert run(pipe.pre_route(ctx())) is None                                  # research would have answered
    messages = run(pipe.augment(ctx(), [{"role": "user", "content": "x"}]))
    assert "note from research" not in messages[0]["content"] and "note from director" in messages[0]["content"]
    assert run(pipe.post_reply(ctx(), "ответ")) == "ответ [safety] [director]"
    assert research.calls == [] and director.calls == ["pre", "augment", "post"]
    row = {r["name"]: r for r in pipe.status()["modules"]}
    assert row["research"]["switched_on"] is False and row["research"]["calls"]["switched_off"] == 3

    off.clear()                                                                # re-read per turn: back on at once
    assert run(pipe.pre_route(ctx())) == "из сети"


def test_safety_layers_cannot_be_switched_off_by_the_switch():
    safety, guard = Probe("safety", 1, reply="стоп"), Probe("model_guard", 2)
    pipe = J2Pipeline([safety, guard], module_switch=lambda name: False)
    assert run(pipe.pre_route(ctx())) == "стоп"
    assert pipe.module_on("model_guard") is True


def test_a_failing_switch_reader_means_on():
    def broken(name):
        raise RuntimeError("disk gone")
    module = Probe("persona", 40, reply="я")
    assert run(J2Pipeline([module], module_switch=broken).pre_route(ctx())) == "я"


def test_discover_reads_the_owner_file_for_the_runtime(tmp_path):
    from types import SimpleNamespace
    js.write_overlay(js.settings_path(tmp_path), {"version": 1, "j2_modules": {"director": False}})
    pipe = J2Pipeline.discover(SimpleNamespace(vault=SimpleNamespace(data_dir=tmp_path)), names=())
    assert pipe.module_on("director") is False and pipe.module_on("persona") is True


def test_background_loop_does_not_tick_while_switched_off(tmp_path):
    from bcc.pit.j2.insights import InsightsCollector, InsightsModule
    ticks: list[int] = []
    state = {"on": False}

    async def fake_sleep(_s):
        if len(ticks) + state.setdefault("sleeps", 0) >= 3:
            raise asyncio.CancelledError
        state["sleeps"] += 1

    module = InsightsModule(InsightsCollector(tmp_path), sleep=fake_sleep)

    async def tick():
        ticks.append(1)
    module.tick = tick
    J2Pipeline([module], module_switch=lambda name: state["on"])

    with pytest.raises(asyncio.CancelledError):
        run(module._loop())
    assert ticks == []                                                         # switched off: never ticked
    state.update(on=True, sleeps=0)
    with pytest.raises(asyncio.CancelledError):
        run(module._loop())
    assert ticks                                                               # switched on: ticks again


# ------------------------------------------------------------------------------ owner API
async def test_api_lists_switches_saves_off_and_reset_keeps_it(env, pit_setup):
    c = env.client
    body = {"defaults": {"behavior_scales": {}, "system_extra": ""}}
    listed = {m["name"]: m for m in (await c.get("/api/jeff-settings")).json()["j2_modules"]}
    assert listed["safety"]["locked"] is True and listed["research"]["on"] is True
    r = await c.put("/api/jeff-settings", json={**body, "j2_modules": {"research": False, "proactive": False}})
    assert r.status_code == 200 and r.json()["settings"]["j2_modules"] == {"proactive": False, "research": False}
    r = await c.put("/api/jeff-settings", json={**body, "j2_modules": {"proactive": True}})
    assert r.json()["settings"]["j2_modules"] == {"research": False}          # merged, not replaced
    assert js.j2_module_enabled(pit_setup, "research") is False
    assert (await c.put("/api/jeff-settings", json={**body, "j2_modules": {"safety": False}})).status_code == 422
    await c.post("/api/jeff-settings/reset", json={})
    assert js.j2_module_enabled(pit_setup, "research") is False               # a style reset keeps feature switches
    listed = {m["name"]: m for m in (await c.get("/api/jeff-settings")).json()["j2_modules"]}
    assert listed["research"]["on"] is False
