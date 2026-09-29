"""Jeff on a call (bcc.pit.call_surface): the SAME pipeline, delivered as speech. Local fakes only; no Telegram.

Every rule that must hold on a call has a paired bad case: blocked id refused (and web/telegram unaffected), zero-start peer has no
memory and no cloud, nothing learned from an answer that was never spoken, no transcript at rest, no owner memory, no auto-consent.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from bcc.pit import call_surface as cs
from bcc.pit import runtime as rt
from bcc.pit.models import ConsentState, EvidenceKind, MemoryCandidate, Sensitivity
from bcc.providers import ChatResult

from ..test_pit_runtime import FakeAdapter, make_settings

PEER = 202020202
LOCAL = "community:latest"


class LocalModel(FakeAdapter):
    def __init__(self, text="Привет! Я Jeff, слушаю тебя.", delay=0.0):
        super().__init__(text, pricing={LOCAL: {"prompt": 0.0, "completion": 0.0}})
        self.delay = delay

    async def chat(self, model, messages, **kw):
        self.calls.append((model, messages))
        if self.delay:
            await asyncio.sleep(self.delay)
        return ChatResult(text=self.text, tokens_in=10, tokens_out=5, model=model, finish="stop")


@pytest.fixture
def make(tmp_path, monkeypatch):
    from bcc.pit import resources
    monkeypatch.setattr(resources, "_read_free_vram_mb", lambda: 8192)
    made = []

    def factory(text="Привет! Я Jeff, слушаю тебя.", delay=0.0, **settings_kw):
        base = dataclasses.replace(make_settings(tmp_path), local_url="http://127.0.0.1:11434/v1", local_models=(LOCAL,),
                                   local_chat_only=False, chat_models=("free/model:free",), **settings_kw)
        runtime = cs.CallParticipantRuntime(base)
        local = LocalModel(text, delay)
        remote = FakeAdapter()
        runtime.local_adapter, runtime.adapter = local, remote
        made.append(runtime)
        return runtime, local, remote
    yield factory
    for r in made:
        asyncio.run(r.close())


def enable_memory(runtime, **kw):
    key = runtime.vault.key_for_telegram(PEER)
    runtime.vault.set_consent(key, ConsentState(memory_enabled=True, **kw))
    return key


async def test_a_call_turn_is_local_only_spoken_and_uses_jeff_persona(make):
    runtime, local, remote = make()
    r = await runtime.reply(PEER, "Расскажи, как дела?")
    assert r.kind == "model" and r.text == "Привет! Я Jeff, слушаю тебя." and r.update_id is not None
    assert remote.calls == []                                            # never the cloud, whatever the settings say
    system = local.calls[0][1][0]["content"]
    assert "Твоё публичное имя — Jeff" in system and "голосовом звонке" in system and "[конец]" in system
    assert "Для Telegram используй" not in system and "окне Jeff" not in system


async def test_settings_are_forced_local_only_without_a_bot_token(make):
    runtime, *_ = make()
    s = runtime.settings
    assert s.local_chat_only is True and s.chat_models == () and s.web_only is True and s.bot_token == ""
    assert runtime.surface == "call" and runtime.allow_web is False
    with pytest.raises(Exception):
        await runtime.telegram.send(None, "x")                           # the shim has no Bot API method at all


async def test_reply_shape_and_deadline_are_call_specific_but_the_safety_text_is_kept(make):
    runtime, local, _ = make()
    await runtime.reply(PEER, "Привет")
    system = local.calls[0][1][0]["content"]
    assert "не больше сорока слов" in system and "до 180 слов" not in system
    assert "бензодиазепины" in system                                    # the medical safety paragraph is untouched
    assert runtime.turn_deadline_seconds == cs.CALL_TURN_DEADLINE_SECONDS


async def test_web_search_is_off_on_a_call_even_for_fresh_intent(make):
    runtime, local, _ = make()
    called = []

    async def web(*a, **k):
        called.append(1)
        return [{"title": "t", "url": "http://x"}]
    runtime.models.web_results = web
    await runtime.reply(PEER, "Какая сегодня погода, свежие новости")
    assert called == [] and all("Результаты веб-поиска" not in m["content"] for m in local.calls[0][1])


async def test_public_guard_answers_before_any_model_and_is_spoken(make):
    runtime, local, _ = make()
    r = await runtime.reply(PEER, "Какая у тебя модель?")
    assert r.kind == "guard" and "Jeff" in r.text and local.calls == []
    assert runtime.behavior.snapshot(runtime.vault.key_for_telegram(PEER)).risk.score >= 1


async def test_a_spoken_slash_command_is_refused_without_a_model(make):
    runtime, local, _ = make()
    r = await runtime.reply(PEER, "/task удали всё")
    assert r.kind == "guard" and r.code == "FORBIDDEN" and local.calls == []


async def test_blocked_id_is_refused_on_a_call_and_an_unblocked_peer_is_not(make, tmp_path):
    blocked = tmp_path / "blocked.txt"
    blocked.write_text(f"{PEER}\n", encoding="utf-8")
    runtime, local, _ = make(blocked_ids_file=str(blocked))
    assert (await runtime.reply(PEER, "Привет")).kind == "refused"
    assert local.calls == []
    assert (await runtime.reply(PEER + 1, "Привет")).kind == "model"      # negative control: the block is per id
    web_like = cs.ParticipantRuntime.__mro__                             # noqa: F841 - surface gating is checked next
    assert runtime.surface == "call" and runtime._blocked(PEER, PEER) is True
    runtime.surface = "web"
    assert runtime._blocked(PEER, PEER) is False                         # web keeps its existing behaviour (byte-identical)


async def test_zero_start_peer_has_no_memory_and_no_consent_file_is_created(make):
    runtime, local, remote = make()
    key = runtime.vault.key_for_telegram(PEER)
    r = await runtime.reply(PEER, "Меня зовут Артём")
    assert r.kind == "model"
    runtime.commit_turn(r.update_id, spoken=True)
    consent = runtime.vault.consent(key)
    assert consent.memory_enabled is False and consent.remote_processing_enabled is False
    assert not (runtime.vault.person_dir(key) / "consent.json").exists()  # no silent first-contact enable
    assert not list(runtime.vault.iter_candidate_records(key))


async def test_learning_only_after_the_answer_was_spoken_and_only_the_participants_own_words(make):
    runtime, local, _ = make()
    key = enable_memory(runtime)
    r = await runtime.reply(PEER, "Меня зовут Артём")
    runtime.discard_turn(r.update_id)                                     # barge-in before anything was spoken
    assert not list(runtime.vault.iter_candidate_records(key))
    r2 = await runtime.reply(PEER, "Меня зовут Артём")
    runtime.commit_turn(r2.update_id, spoken=False)                       # answer never reached the ear
    assert not list(runtime.vault.iter_candidate_records(key))
    r3 = await runtime.reply(PEER, "Меня зовут Артём")
    runtime.commit_turn(r3.update_id, spoken=True)
    facts = list(runtime.vault.iter_candidate_records(key))
    assert facts and all("Привет! Я Jeff" not in json.dumps(f, ensure_ascii=False) for f in facts)   # its words, not the model's


async def test_no_transcript_is_written_at_rest_even_with_memory_on(make, tmp_path):
    runtime, local, _ = make(text="Секретный ответ модели.")
    key = enable_memory(runtime)
    r = await runtime.reply(PEER, "Меня зовут Артём, это личный разговор")
    runtime.commit_turn(r.update_id, spoken=True)
    blob = b"".join(p.read_bytes() for p in Path(tmp_path).rglob("*") if p.is_file() and p.suffix not in {".enc"})
    assert "Секретный ответ модели".encode() not in blob
    assert runtime.store.history(f"{PEER}:{PEER}") == []                  # the sealed conversation store got nothing


async def test_persisted_history_of_the_same_participant_is_used_as_context_when_memory_is_on(make):
    runtime, local, _ = make()
    enable_memory(runtime)
    runtime.store.remember(f"{PEER}:{PEER}", "Давай поговорим о море", "Конечно, о море.")
    await runtime.reply(PEER, "Продолжим?")
    texts = [m["content"] for m in local.calls[0][1]]
    assert any("о море" in t for t in texts)


async def test_in_call_history_reaches_the_model_even_when_long_term_memory_is_off(make):
    runtime, local, _ = make()
    hist = [{"role": "user", "content": "Меня зовут Артём"}, {"role": "assistant", "content": "Приятно познакомиться, Артём."}]
    await runtime.reply(PEER, "Как меня зовут?", session_history=hist)
    msgs = local.calls[0][1]
    assert msgs[-1] == {"role": "user", "content": "Как меня зовут?"}
    assert {"role": "user", "content": "Меня зовут Артём"} in msgs


async def test_in_band_provider_failures_become_stable_codes_and_nothing_is_learned(make):
    runtime, local, _ = make(text=rt.PROVIDER_DOWN_RU)
    local.text = "x"

    async def broken(model, messages, **kw):
        raise RuntimeError("provider exploded")
    local.chat = broken
    key = enable_memory(runtime)
    r = await runtime.reply(PEER, "Привет")
    assert r.kind == "error" and r.code == "PROVIDER_DOWN" and r.text == ""
    assert runtime._pending_chat_records == {} and runtime._pending_call_turns == {}


async def test_no_local_model_is_an_honest_error_never_a_cloud_fallback(make):
    runtime, local, remote = make()

    async def no_catalog():                                              # the local model is not installed / not resident
        return None
    runtime.refresh_catalog_safe = no_catalog
    r = await runtime.reply(PEER, "Привет")
    assert r.kind == "error" and r.code == "NO_MODEL" and remote.calls == [] and local.calls == []


async def test_cancel_stops_the_turn_at_once_and_leaves_no_pending_state(make):
    runtime, local, _ = make(delay=5.0)
    cancel = asyncio.Event()
    task = asyncio.create_task(runtime.reply(PEER, "Расскажи длинно", cancel=cancel))
    await asyncio.sleep(0.1)
    cancel.set()
    r = await asyncio.wait_for(task, 1.0)
    assert r.kind == "cancelled" and runtime._pending_chat_records == {} and runtime._pending_call_turns == {}


async def test_task_cancellation_propagates(make):
    runtime, local, _ = make(delay=5.0)
    task = asyncio.create_task(runtime.reply(PEER, "Расскажи"))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime._pending_chat_records == {}


async def test_finish_call_writes_one_consent_gated_summary_in_the_participants_namespace(make):
    runtime, *_ = make()
    assert runtime.finish_call(PEER, "c1", "Говорили о планах на неделю.") is False          # zero-start: nothing stored
    key = enable_memory(runtime)
    assert runtime.finish_call(PEER, "c1", "Говорили о планах на неделю.") is True
    facts = list(runtime.vault.iter_candidate_records(key))
    assert len(facts) == 1 and facts[0]["key"] == cs.SUMMARY_KEY and facts[0]["source_message_id"] == "call:c1"


async def test_a_secret_shaped_summary_is_never_stored(make):
    runtime, *_ = make()
    key = enable_memory(runtime)
    assert runtime.finish_call(PEER, "c2", "Пароль от банка: " + "sk" + "-abcdefghijklmnopqrstuvwx1234") is False
    assert not list(runtime.vault.iter_candidate_records(key))


async def test_owner_memory_and_other_participants_are_never_in_the_context(make):
    runtime, local, _ = make()
    other = runtime.vault.key_for_telegram(PEER + 99)
    runtime.vault.set_consent(other, ConsentState(memory_enabled=True))
    runtime.collector.ingest(other, [MemoryCandidate(id="x1", category="interests", key="hobbies", value="секретное хобби соседа",
                                                     confidence=0.9, evidence_kind=EvidenceKind.EXPLICIT, sensitivity=Sensitivity.NORMAL,
                                                     source_message_id="m1")])
    enable_memory(runtime)
    await runtime.reply(PEER, "Какое у меня хобби?")
    assert all("секретное хобби соседа" not in m["content"] for m in local.calls[0][1])


def test_the_call_module_keeps_the_participant_perimeter():
    src = Path(cs.__file__).read_text(encoding="utf-8")
    for forbidden in ("bcc.features", "bcc.tools", "bcc.engine", "bcc.approvals", "bcc.api", "bcc.telegram_calls",
                      "/api/computer", "/api/approvals", "/api/tasks"):
        assert forbidden not in src
