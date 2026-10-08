"""authored_by_lane jeffa: ResilientChat bounded recovery (one unload+retry per scope, honest failure)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bcc.pit.ollama_native import EmptyAnswer
from bcc.pit.resilient_chat import ResilientChat


class Adapter:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0
        self.unloaded = []

    async def chat(self, model, messages, **kw):
        self.calls += 1
        await asyncio.sleep(0)               # let concurrent scopes interleave
        text = self.answers.pop(0) if self.answers else "ok"
        return SimpleNamespace(text=text, tokens_out=len(text))

    async def unload(self, model):
        self.unloaded.append(model)


def run(coro):
    return asyncio.run(coro)


def test_healthy_call_needs_no_recovery():
    a = Adapter(["hello"])
    rc = ResilientChat(a, "m", settle=0)
    assert run(rc.chat([{"role": "user", "content": "hi"}], scope="p1")).text == "hello"
    assert a.calls == 1 and a.unloaded == [] and rc.scope("p1").as_dict() == {
        "calls": 1, "empty_answers": 0, "recoveries": 0}


def test_empty_answer_triggers_exactly_one_unload_then_succeeds():
    a = Adapter(["", "recovered"])
    rc = ResilientChat(a, "m", settle=0)
    assert run(rc.chat([], scope="p1")).text == "recovered"
    assert a.unloaded == ["m"] and a.calls == 2
    assert rc.scope("p1").recoveries == 1 and rc.scope("p1").empty_answers == 1


def test_whitespace_only_answer_is_empty_not_success():
    a = Adapter(["   \n", "real"])
    assert run(ResilientChat(a, "m", settle=0).chat([], scope="s")).text == "real"


def test_second_empty_marks_scope_degraded_and_next_call_fails_fast_without_adapter():
    a = Adapter(["", ""])
    rc = ResilientChat(a, "m", settle=0)
    with pytest.raises(EmptyAnswer):
        run(rc.chat([], scope="p1"))
    assert rc.scope("p1").degraded and a.unloaded == ["m"]
    calls = a.calls
    with pytest.raises(EmptyAnswer):
        run(rc.chat([], scope="p1"))
    assert a.calls == calls                       # no further model traffic for a degraded scope
    assert run(rc.chat([], scope="p2")).text == "ok"   # other participants unaffected


def test_unload_failure_is_counted_but_retry_still_happens():
    class Bad(Adapter):
        async def unload(self, model):
            raise RuntimeError("runner gone")
    a = Bad(["", "after"])
    rc = ResilientChat(a, "m", settle=0)
    assert run(rc.chat([], scope="p")).text == "after"
    assert rc.unload_errors == 1 and rc.unloads == 0


def test_concurrent_scopes_share_one_unload():
    async def go():
        a = Adapter(["", "", "x", "y"])
        rc = ResilientChat(a, "m", settle=0.05)
        r = await asyncio.gather(rc.chat([], scope="a"), rc.chat([], scope="b"))
        return a, rc, r
    a, rc, r = run(go())
    assert len(a.unloaded) == 1 and rc.unloads == 1 and len(r) == 2


def test_end_scope_forgets_degraded_verdict_and_folds_counters():
    a = Adapter(["", ""])
    rc = ResilientChat(a, "m", settle=0)
    with pytest.raises(EmptyAnswer):
        run(rc.chat([], scope="p"))
    rc.end_scope("p")
    assert "p" not in rc.stats and rc.closed == {"calls": 2, "empty_answers": 2, "recoveries": 1}
    assert run(rc.chat([], scope="p")).text == "ok"
    assert len(rc.latencies()) == 1
