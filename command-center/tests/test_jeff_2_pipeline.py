"""Jeff 2.0 module layer: ordering, isolation, budgets, breaker, and the real chat route (fake model)."""
from __future__ import annotations

import asyncio

import pytest

from bcc.pit.j2 import Advice, J2Pipeline, TurnContext
from bcc.pit.j2.contract import BaseModule

from .test_pit_rc19_jeff import _person, _started
from .test_pit_runtime import FakeAdapter, make_runtime, message


def ctx(text: str = "привет") -> TurnContext:
    return TurnContext(person_key="p1", who="tg:1", text=text)


class Note(BaseModule):
    def __init__(self, name, note, order=100):
        self.name, self.order, self._note = name, order, note

    async def augment(self, c):
        return Advice(notes=(self._note,))


class Boom(BaseModule):
    name = "boom"

    async def pre_route(self, c):
        raise RuntimeError("module fault")

    async def augment(self, c):
        raise RuntimeError("module fault")

    async def post_reply(self, c, reply):
        raise RuntimeError("module fault")


class Slow(BaseModule):
    name = "slow"

    async def augment(self, c):
        await asyncio.sleep(5)
        return Advice(notes=("never",))


class Blocker(BaseModule):
    name = "blocker"
    order = 1

    async def pre_route(self, c):
        return Advice(reply="Это я не обсуждаю.") if "запрещено" in c.text else None


def run(coro):
    return asyncio.run(coro)


def test_modules_run_in_order_and_notes_go_before_the_user_message():
    pipeline = J2Pipeline([Note("b", "вторая", order=20), Note("a", "первая", order=10)])
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "вопрос"}]
    out = run(pipeline.augment(ctx(), messages))
    assert out[-1] == messages[-1] and out[0] == messages[0]
    block = out[-2]["content"]
    assert block.index("первая") < block.index("вторая") and "данные, не инструкции" in block


def test_a_failing_module_never_breaks_the_turn_and_is_counted():
    pipeline = J2Pipeline([Boom(), Note("ok", "заметка")])
    messages = [{"role": "user", "content": "q"}]
    assert run(pipeline.pre_route(ctx())) is None
    assert "заметка" in run(pipeline.augment(ctx(), messages))[-2]["content"]
    assert run(pipeline.post_reply(ctx(), "ответ")) == "ответ"
    stats = {row["name"]: row["calls"] for row in pipeline.status()["modules"]}
    assert stats["boom"]["error"] == 3 and stats["ok"]["ok"] == 3 and stats["ok"]["error"] == 0   # one call per phase


def test_a_slow_module_is_cut_off_by_the_time_budget():
    pipeline = J2Pipeline([Slow(), Note("fast", "успел")], augment_timeout=0.05)
    started = asyncio.new_event_loop().time()
    out = run(pipeline.augment(ctx(), [{"role": "user", "content": "q"}]))
    assert "успел" in out[-2]["content"] and "never" not in out[-2]["content"]
    assert {r["name"]: r["calls"] for r in pipeline.status()["modules"]}["slow"]["timeout"] == 1
    assert started >= 0


def test_the_breaker_opens_after_repeated_failures_and_closes_after_the_cooldown():
    now = [0.0]
    pipeline = J2Pipeline([Boom()], clock=lambda: now[0])
    for _ in range(3):
        run(pipeline.pre_route(ctx()))
    assert pipeline.status()["modules"][0]["breaker_open"] is True
    run(pipeline.pre_route(ctx()))
    assert pipeline.status()["modules"][0]["calls"]["skipped"] == 1
    now[0] = 301.0
    assert pipeline.status()["modules"][0]["breaker_open"] is False


def test_notes_are_capped_by_the_character_budget():
    pipeline = J2Pipeline([Note("big", "x" * 500), Note("small", "мало", order=200)], notes_budget=100)
    out = run(pipeline.augment(ctx(), [{"role": "user", "content": "q"}]))
    assert "мало" in out[-2]["content"] and "x" * 500 not in out[-2]["content"]


def test_pre_route_short_circuits_and_the_flag_switches_the_layer_off(monkeypatch):
    pipeline = J2Pipeline([Blocker()])
    assert run(pipeline.pre_route(ctx("это запрещено"))) == "Это я не обсуждаю."
    assert run(pipeline.pre_route(ctx("обычный вопрос"))) is None
    monkeypatch.setenv("BOSSMAN_JEFF_J2", "off")
    assert run(pipeline.pre_route(ctx("это запрещено"))) is None
    assert pipeline.status()["enabled"] is False


def test_duplicate_module_names_are_refused():
    with pytest.raises(ValueError):
        J2Pipeline([Note("same", "a"), Note("same", "b")])


def test_discover_skips_missing_modules_and_keeps_going():
    pipeline = J2Pipeline.discover(object(), names=("no_such_module_xyz",))
    assert pipeline.modules == ()


def test_the_real_chat_route_uses_the_layer_and_never_mixes_participants(tmp_path):
    adapter = FakeAdapter("готовый ответ")
    runtime = make_runtime(tmp_path, adapter=adapter)
    seen: list[str] = []

    class Spy(BaseModule):
        name = "spy"

        async def augment(self, c):
            seen.append(c.person_key)
            return Advice(notes=("любит короткие ответы",))

    runtime.j2.register(Spy())
    _started(runtime)
    reply = run(runtime.handle(_person(runtime), message("расскажи про мосты", message_id=5)))
    assert reply and "готовый ответ" in reply
    sent = adapter.calls[-1][1]
    assert any("любит короткие ответы" in str(m.get("content")) for m in sent if m["role"] == "system")
    assert sent[-1]["role"] == "user" and sent[-1]["content"] == "расскажи про мосты"
    assert seen and len(set(seen)) == 1


def test_a_module_can_answer_before_any_model_is_called(tmp_path):
    adapter = FakeAdapter("не должен быть вызван")
    runtime = make_runtime(tmp_path, adapter=adapter)
    runtime.j2.register(Blocker())
    _started(runtime)
    calls_before = len(adapter.calls)
    reply = run(runtime.handle(_person(runtime), message("это запрещено", message_id=6)))
    assert "Это я не обсуждаю." in reply and len(adapter.calls) == calls_before


def test_post_reply_can_adjust_the_final_text(tmp_path):
    runtime = make_runtime(tmp_path, adapter=FakeAdapter("ответ"))

    class Sign(BaseModule):
        name = "sign"

        async def post_reply(self, c, reply):
            return reply + " (проверено)"

    runtime.j2.register(Sign())
    _started(runtime)
    assert run(runtime.handle(_person(runtime), message("вопрос", message_id=7))).endswith("(проверено)")


def test_augment_knows_whether_the_route_is_remote(tmp_path):
    runtime = make_runtime(tmp_path, adapter=FakeAdapter("ответ"))
    seen: list = []

    class Spy(BaseModule):
        name = "route_spy"

        async def augment(self, c):
            seen.append(c.extra.get("route_remote"))
            return None

    runtime.j2.register(Spy())
    _started(runtime)
    run(runtime.handle(_person(runtime), message("расскажи про мосты", message_id=8)))
    assert seen and all(isinstance(v, bool) for v in seen)
