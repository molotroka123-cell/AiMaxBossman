"""Jeff 1.1 privacy in code (audit UX-001 / UX-002): history is filtered BEFORE
routing, and /pause_memory really stops every write. Fakes only."""
from __future__ import annotations

import asyncio
import json

from bcc.pit.models import ConsentState

from .test_pit_runtime import FREE_ENDPOINT, make_runtime, message

MARKER = "HISTORY_MARKER_" + "7431"


def _setup(tmp_path, **consent):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(key, ConsentState(**consent))
    runtime.catalog = {FREE_ENDPOINT.id: FREE_ENDPOINT}
    runtime.catalog_checked_at = 1.0
    return runtime, person, key


def _sent(runtime) -> str:
    return json.dumps(runtime.adapter.calls, ensure_ascii=False)


def test_remote_never_gets_history_when_consent_file_is_missing(tmp_path):
    runtime, person, key = _setup(tmp_path, memory_enabled=True, remote_processing_enabled=True,
                                  remote_personalization_enabled=True)
    asyncio.run(runtime.handle(person, message(MARKER, message_id=1)))
    assert runtime.store.history(person.key)
    (runtime.vault.person_dir(key) / "consent.json").unlink()
    runtime.adapter.calls.clear()
    runtime.catalog = {FREE_ENDPOINT.id: FREE_ENDPOINT}
    # no consent on record: the turn is answered without any saved history
    asyncio.run(runtime.handle(person, message("second", message_id=2)))
    assert MARKER not in _sent(runtime)


def test_remote_gets_no_history_when_memory_is_off_even_if_personalization_is_on(tmp_path):
    runtime, person, key = _setup(tmp_path, memory_enabled=True, remote_processing_enabled=True,
                                  remote_personalization_enabled=True)
    asyncio.run(runtime.handle(person, message(MARKER, message_id=1)))
    asyncio.run(runtime.handle(person, message("/pause_memory", message_id=2)))
    runtime.adapter.calls.clear()
    asyncio.run(runtime.handle(person, message("what did I say?", message_id=3)))
    assert runtime.adapter.calls
    assert MARKER not in _sent(runtime)


def test_remote_fallback_attempt_also_gets_no_history(tmp_path):
    runtime, person, key = _setup(tmp_path, memory_enabled=True, remote_processing_enabled=True,
                                  remote_personalization_enabled=True)
    asyncio.run(runtime.handle(person, message(MARKER, message_id=1)))
    asyncio.run(runtime.handle(person, message("/privacy personalization off", message_id=2)))
    runtime.adapter.calls.clear()
    asyncio.run(runtime.handle(person, message("again", message_id=3)))
    asyncio.run(runtime.handle(person, message("and again", message_id=4)))
    assert runtime.adapter.calls
    assert MARKER not in _sent(runtime)


def test_pause_memory_blocks_style_command_write(tmp_path):
    runtime, person, key = _setup(tmp_path, memory_enabled=True, remote_processing_enabled=True)
    asyncio.run(runtime.handle(person, message("/pause_memory", message_id=1)))
    before = runtime.vault.list_facts(key)
    answer = asyncio.run(runtime.handle(person, message("/style коротко и по делу", message_id=2)))
    assert runtime.vault.list_facts(key) == before
    assert "пауз" in answer.lower()


def test_pause_memory_blocks_correct_command_write(tmp_path):
    runtime, person, key = _setup(tmp_path, memory_enabled=True, remote_processing_enabled=True)
    asyncio.run(runtime.handle(person, message("/style коротко", message_id=1)))
    facts = runtime.vault.list_facts(key)
    assert facts
    asyncio.run(runtime.handle(person, message("/pause_memory", message_id=2)))
    answer = asyncio.run(runtime.handle(person, message("/correct коротко => длинно", message_id=3)))
    assert runtime.vault.list_facts(key) == facts
    assert "пауз" in answer.lower()


def test_pause_memory_still_allows_forget(tmp_path):
    runtime, person, key = _setup(tmp_path, memory_enabled=True, remote_processing_enabled=True)
    asyncio.run(runtime.handle(person, message("/style коротко", message_id=1)))
    asyncio.run(runtime.handle(person, message("/pause_memory", message_id=2)))
    asyncio.run(runtime.handle(person, message("/forget коротко", message_id=3)))
    assert not [f for f in runtime.vault.list_facts(key) if "коротко" in str(f.get("value"))]


def test_pause_memory_writes_nothing_on_chat_turn_or_learning(tmp_path):
    runtime, person, key = _setup(tmp_path, memory_enabled=True, remote_processing_enabled=True)
    asyncio.run(runtime.handle(person, message("/pause_memory", message_id=1)))
    facts = runtime.vault.list_facts(key)
    asyncio.run(runtime.handle(person, message("Меня зовут Пётр, я люблю сыр", message_id=2)))
    assert runtime.vault.list_facts(key) == facts
    for table in ("history", "learning_log"):
        assert runtime.store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_web_disclosure_does_not_claim_history_when_memory_is_paused(tmp_path):
    from .test_pit_web import RecordingAdapter, chat, client_for, make_app, signup

    adapter = RecordingAdapter("Ок.")
    app, _ = make_app(tmp_path, adapter)
    with client_for(app) as c:
        signup(c)
        chat(c, "первое сообщение " + MARKER)
        chat(c, "/pause_memory")
        res = chat(c, "второе сообщение").json()
    assert not any("Учёл предыдущие" in note for note in res["disclosure"])
    assert MARKER not in json.dumps(adapter.calls[-1], ensure_ascii=False)
