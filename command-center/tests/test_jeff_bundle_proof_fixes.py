"""Regression tests for the defects the shipped-bundle proof (tools/jeff_bundle_proof.py, 12.10.2026) found.

D1 /forget left the forgotten words in the next model request - D2 a closed allowlist refused delivery to the
owner - D3 a real HTTP 402 did not close the route. Each test fails on the code it was written against and ends
with a negative control.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json

import httpx
import pytest

from bcc.pit import runtime as rt
from bcc.pit.j2.proactive import TelegramSender
from bcc.pit.vault import _append_jsonl
from bcc.providers import OpenAICompatAdapter
from bcc.telegram_companion.adapters import Telegram
from bcc.telegram_companion.config import Person

from .test_pit_runtime import FakeAdapter, make_runtime, make_settings, message, warm
from .test_pit_telegram_fake import FakeBotAPI

FORGOTTEN = "Зебра7"


def _say(runtime, person, text, message_id=1):
    return asyncio.run(runtime.handle(person, message(text, user_id=person.user_id, message_id=message_id)))


# ===================================================================================================== D1
def test_d1_forget_scrubs_the_words_from_the_next_model_request_and_every_local_copy(tmp_path):
    adapter = FakeAdapter("Хорошо, запомнил.")
    runtime = make_runtime(tmp_path, adapter=adapter)
    person = runtime.settings.people[0]
    key = runtime.vault.key_for_telegram(person.user_id)
    warm(runtime, key)
    _say(runtime, person, f"Я люблю разводить кактусы сорта {FORGOTTEN}", 1)
    _say(runtime, person, "Люблю шахматы и походы", 2)                         # unrelated turn: must survive
    _say(runtime, person, "/privacy personalization on", 3)
    assert [r for r in runtime.vault.list_facts(key) if FORGOTTEN.lower() in str(r.get("value", "")).lower()]
    # other local copies of the same words: display transcript, opt-in training set, quality sample
    runtime.store.transcript_add(person.key, "user", f"мои кактусы сорта {FORGOTTEN}")
    runtime.store.transcript_add(person.key, "user", "мои шахматы")
    base = runtime.vault.person_dir(key)
    _append_jsonl(base / rt.TRAINING_FILE, {"user": f"кактусы {FORGOTTEN}", "assistant": "ок"})
    _append_jsonl(base / rt.TRAINING_FILE, {"user": "шахматы", "assistant": "ок"})
    _append_jsonl(base / "quality" / "scores.jsonl", {"id": "a1", "overall": 0.9, "flags": [],
                                                      "sample": {"user": f"про {FORGOTTEN}", "reply": "ок"}})
    _append_jsonl(base / "quality" / "scores.jsonl", {"id": "b2", "overall": 0.8, "flags": [],
                                                      "sample": {"user": "про шахматы", "reply": "ок"}})
    # an older correction row that wrote the words down (the old /forget did exactly that)
    _append_jsonl(base / "corrections.jsonl", {"action": "forget", "candidate_id": "old-1", "query": FORGOTTEN})
    assert FORGOTTEN in (base / "corrections.jsonl").read_text(encoding="utf-8")

    assert "Забыл: удалено 1" in _say(runtime, person, f"/forget {FORGOTTEN}", 4)
    _say(runtime, person, "Что ты помнишь обо мне?", 5)

    request = " ".join(str(m.get("content", "")) for m in adapter.calls[-1][1])
    assert FORGOTTEN not in request and FORGOTTEN.lower() not in request.lower()      # the NEXT model request
    assert "шахматы" in request                                                       # negative control: history stays
    stored = " ".join(str(e) for e in runtime.store.log_entries(person.key))
    assert FORGOTTEN not in stored and "шахматы" in stored                            # learning log
    shown = " ".join(row["text"] for row in runtime.store.transcript(person.key))
    assert FORGOTTEN not in shown and "мои шахматы" in shown                          # display transcript
    training = (base / rt.TRAINING_FILE).read_text(encoding="utf-8")
    assert FORGOTTEN not in training and "шахматы" in training
    scores = [json.loads(line) for line in (base / "quality" / "scores.jsonl").read_text(encoding="utf-8").splitlines()]
    by_id = {r["id"]: r for r in scores}
    assert {"a1", "b2"} <= set(by_id) and len(scores) >= 2                            # no row was dropped: numbers kept
    assert by_id["a1"]["overall"] == 0.9 and "sample" not in by_id["a1"]              # ... only the matching text went
    assert by_id["b2"]["sample"]["user"] == "про шахматы"                             # unrelated sample untouched
    assert FORGOTTEN not in json.dumps(scores, ensure_ascii=False)
    corrections = (base / "corrections.jsonl").read_text(encoding="utf-8")
    assert FORGOTTEN not in corrections and '"candidate_id": "old-1"' in corrections  # the id block survives


def test_d1_a_forget_that_matches_nothing_removes_no_history(tmp_path):
    adapter = FakeAdapter("Хорошо.")
    runtime = make_runtime(tmp_path, adapter=adapter)
    person = runtime.settings.people[0]
    warm(runtime, runtime.vault.key_for_telegram(person.user_id))
    _say(runtime, person, "Люблю шахматы и походы", 1)
    before = runtime.store.history(person.key)
    reply = _say(runtime, person, "/forget Зебра7", 2)
    assert before and not reply.startswith("Забыл")                            # nothing was forgotten ...
    assert runtime.store.history(person.key) == before                        # ... and no history row went with it


# ===================================================================================================== D2
class _Sink(FakeBotAPI):
    pass


def _sender_runtime(tmp_path, *, allowlist_open):
    settings = dataclasses.replace(make_settings(tmp_path), allowlist_open=allowlist_open)
    api = _Sink([])
    runtime = rt.ParticipantRuntime(settings)
    runtime.telegram = Telegram(rt._transport_settings(settings), transport=api)
    return runtime, api


def test_d2_a_spawned_guest_worker_does_not_replace_the_configured_owner_when_the_allowlist_is_closed(tmp_path):
    runtime, api = _sender_runtime(tmp_path, allowlist_open=False)
    owner = runtime.settings.people[0]
    owner_key = runtime.vault.key_for_telegram(owner.user_id)
    runtime._spawned_workers = {f"{owner.user_id}:{owner.chat_id}"}             # the dynamic worker of the same id
    sender = TelegramSender(runtime)
    resolved = sender._resolve(owner_key)
    assert resolved == owner and resolved.role == "owner"                       # the configured Person won
    # The outbound guard (incident 19.10) lets Jeff write only into a chat that wrote within 30 min unless the owner
    # switch + opt-in allow it; this test is about WHO receives, so the owner has just written.
    runtime.outbound_guard.note_inbound(owner.chat_id)
    assert asyncio.run(sender(owner_key, "Задача готова: итог", "task-1")) == "sent"
    delivered = [row for row in api.sent if "Задача готова" in row.get("text", "")]
    assert len(delivered) == 1 and delivered[0]["chat_id"] == owner.chat_id     # exactly once, to the owner


def test_d2_guests_are_still_resolved_when_the_allowlist_is_open(tmp_path):
    runtime, api = _sender_runtime(tmp_path, allowlist_open=True)
    guest_id = 909
    runtime._spawned_workers = {f"{guest_id}:{guest_id}"}
    sender = TelegramSender(runtime)
    guest_key = runtime.vault.key_for_telegram(guest_id)
    resolved = sender._resolve(guest_key)
    assert resolved is not None and resolved.role == "guest" and resolved.user_id == guest_id
    runtime.outbound_guard.note_inbound(guest_id)                               # see the D2 owner test above
    assert asyncio.run(sender(guest_key, "Напоминание", "rem-1")) == "sent"
    assert [row["chat_id"] for row in api.sent] == [guest_id]
    # negative control: with the allowlist closed an unknown (never configured) guest is NOT deliverable
    closed, _api = _sender_runtime(tmp_path / "closed", allowlist_open=False)
    closed._spawned_workers = {f"{guest_id}:{guest_id}"}
    with pytest.raises(Exception):
        asyncio.run(TelegramSender(closed)(closed.vault.key_for_telegram(guest_id), "x", "k"))


# ===================================================================================================== D3
def _provider_runtime(tmp_path, status):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "free/model:free",
                                                       "pricing": {"prompt": "0", "completion": "0"}}]})
        calls.append(request.url.path)
        return httpx.Response(status, json={"error": {"message": "Insufficient credits"}})
    adapter = OpenAICompatAdapter(base_url="http://127.0.0.1:9/v1", api_key="test-key",
                                  transport=httpx.MockTransport(handler))
    runtime = make_runtime(tmp_path, adapter=adapter)
    person = runtime.settings.people[0]
    warm(runtime, runtime.vault.key_for_telegram(person.user_id))
    return runtime, person, calls


def test_d3_a_real_http_402_closes_the_route_after_one_request(tmp_path):
    runtime, person, calls = _provider_runtime(tmp_path, 402)
    _say(runtime, person, "Расскажи, как составить план недели", 1)
    assert len(calls) == 1
    _say(runtime, person, "А как составить план на месяц?", 2)
    assert len(calls) == 1                                                      # the second turn never hit the provider
    assert runtime.model_route_status()["rejected"]                             # and the owner can see why


def test_d3_other_http_errors_do_not_close_the_route(tmp_path):
    runtime, person, calls = _provider_runtime(tmp_path, 500)                   # negative control
    _say(runtime, person, "Расскажи, как составить план недели", 1)
    first = len(calls)
    _say(runtime, person, "А как составить план на месяц?", 2)
    assert first >= 1 and len(calls) > first                                    # a 5xx is retried on the next turn
