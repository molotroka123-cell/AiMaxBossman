"""Jeff 2.0 modules on a live VOICE call: deny by default (cross-lane patch `J2_call_surface_deny_by_default.patch`).

Independent audit 2026-09-30: the j2 layer ignored `surface`, so a spoken sentence was persisted in plain text by `proactive`
(a reminder), by `director` / `persona` (memory on), searched on the web by `research`, and sealed into the learning log by the
discovery-reply hook. The promise of the call surface is: no conversation text at rest, no network, no schedule.

Skipped (and reported as skipped, never as a pass) until `CALL_SAFE_MODULES` exists in ``bcc/pit/j2/pipeline.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc.pit.j2 import pipeline as pl
from bcc.pit.j2.contract import Advice, BaseModule, TurnContext

from .test_jeff_call_surface import PEER, enable_memory, make  # noqa: F401 - fixture re-export

pytestmark = pytest.mark.skipif(not hasattr(pl, "CALL_SAFE_MODULES"),
                                reason="cross-lane patch J2_call_surface_deny_by_default is not applied in bcc/pit/j2/pipeline.py")


class Probe(BaseModule):
    def __init__(self, name, *, call_safe=None):
        self.name = name
        self.seen: list[tuple[str, str]] = []
        if call_safe is not None:
            self.call_safe = call_safe

    async def pre_route(self, ctx):
        self.seen.append(("pre", ctx.surface))

    async def augment(self, ctx):
        self.seen.append(("augment", ctx.surface))
        return Advice(notes=(f"note from {self.name}",))

    async def post_reply(self, ctx, reply):
        self.seen.append(("post", ctx.surface))


def ctx(surface):
    return TurnContext(person_key="k", who="w", text="привет", surface=surface)


async def run_all(pipeline, surface):
    c = ctx(surface)
    await pipeline.pre_route(c)
    await pipeline.augment(c, [{"role": "user", "content": "привет"}])
    await pipeline.post_reply(c, "ответ")


async def test_only_call_safe_modules_run_on_a_call_and_all_of_them_on_telegram():
    unsafe = [Probe(n) for n in ("director", "persona", "research", "proactive", "media")]
    safe = [Probe(n) for n in sorted(pl.CALL_SAFE_MODULES)]
    opted_in = Probe("third_party", call_safe=True)
    opted_out = Probe("third_party_2", call_safe=False)
    pipeline = pl.J2Pipeline([*unsafe, *safe, opted_in, opted_out])
    await run_all(pipeline, "call")
    assert all(m.seen == [] for m in unsafe), "nothing that persists, searches or schedules runs on a call"
    assert opted_out.seen == []
    assert all({h for h, _ in m.seen} == {"pre", "augment", "post"} for m in (*safe, opted_in))
    assert {"safety", "model_guard"} <= pl.CALL_SAFE_MODULES, "the safety layers are never switched off on a call"
    await run_all(pipeline, "telegram")                                   # paired control: other surfaces are unchanged
    assert all({h for h, _ in m.seen if _ == "telegram"} == {"pre", "augment", "post"} for m in (*unsafe, opted_out))


def files_with(root: Path, *needles: str) -> list[str]:
    hits = []
    for path in root.rglob("*"):
        if path.is_file() and path.stat().st_size < 5_000_000:
            text = path.read_bytes().decode("utf-8", "ignore").lower()
            if any(n in text for n in needles):
                hits.append(str(path.relative_to(root)))
    return hits


async def test_a_spoken_reminder_is_not_written_to_disk_in_plain_text(make):
    runtime, *_ = make()
    await runtime.reply(PEER, "напомни завтра в десять позвонить маме Светлане насчёт операции")
    assert files_with(runtime.home, "светлан", "операци") == []


async def test_a_spoken_web_search_intent_starts_no_network_access(make):
    runtime, *_ = make()
    searched: list[str] = []

    async def web(query, *a, **k):
        searched.append(query)
        return []
    runtime.models.web_results = web
    await runtime.reply(PEER, "погугли пожалуйста курс биткоина и исследуй тему подробнее")
    assert searched == [], "the call surface has no web access, however the request is phrased"


async def test_with_memory_on_the_spoken_words_do_not_reach_director_or_persona_files(make):
    runtime, *_ = make()
    enable_memory(runtime)
    await runtime.reply(PEER, "расскажи мне про лечение ларисы и её диагноз")
    assert files_with(runtime.home, "ларис", "диагноз") == []


async def test_a_pending_discovery_question_does_not_swallow_a_spoken_answer_into_the_learning_log(make):
    runtime, *_ = make()
    enable_memory(runtime)
    person = runtime._person(PEER)
    runtime.store.put(f"discovery:{person.key}", {"key": "hobby", "question": "Чем увлекаешься?"})
    await runtime.reply(PEER, "Я увлекаюсь рыбалкой на озере Селигер")
    assert files_with(runtime.home, "селигер") == []
    assert runtime.store.get(f"discovery:{person.key}") is not None, "the Telegram question stays pending for Telegram"


async def test_paired_control_the_call_is_still_answered(make):
    runtime, *_ = make()
    r = await runtime.reply(PEER, "Расскажи, как дела?")
    assert r.kind == "model" and r.text
