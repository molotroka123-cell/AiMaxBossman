"""PIT participant runtime contracts: pipeline order, zero-start isolation,
free-only routing, idempotency, memory commands, laptop media honesty.

No network: the model adapter and Telegram fetches are faked per test.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from bcc.pit import runtime as rt
from bcc.pit.config import PITSettings
from bcc.pit.models import ConsentState, EvidenceKind, MemoryCandidate, Sensitivity
from bcc.pit.roleplay import RolePlayMode
from bcc.pit.roleplay_commands import load_roleplay, set_roleplay
from bcc.pit.router import ModelEndpoint
from bcc.pit.vault import PersonaVault
from bcc.telegram_companion.adapters import IMAGE_MAX_BYTES
from bcc.telegram_companion.config import Person
from bcc.providers import ChatResult

JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 64


def make_settings(tmp_path: Path, people=None) -> PITSettings:
    return PITSettings(
        data_dir=tmp_path,
        people=people or (Person(user_id=101, chat_id=101, role="owner"),),
        chat_models=("free/model:free",),
        provider_base_url="http://127.0.0.1:9/v1",
        provider_key="test-key",
        bot_token="test-bot-token",
        identity_salt="ab" * 32,
    )


class FakeAdapter:
    def __init__(self, text: str = "готово", pricing: dict | None = None):
        self.text = text
        self.calls: list[tuple[str, list[dict]]] = []
        self.pricing = {"free/model:free": {"prompt": 0.0, "completion": 0.0}} \
            if pricing is None else pricing

    async def chat(self, model, messages, **kw):
        self.calls.append((model, messages))
        return ChatResult(text=self.text, tokens_in=10, tokens_out=5, model=model)

    async def list_model_info(self):
        return [{"id": model_id} for model_id in self.pricing]

    async def list_model_pricing(self):
        return self.pricing

    async def close(self):
        return None


def make_runtime(tmp_path: Path, *, adapter: FakeAdapter | None = None,
                 settings: PITSettings | None = None) -> rt.ParticipantRuntime:
    settings = settings or make_settings(tmp_path)
    runtime = rt.ParticipantRuntime(settings)
    runtime.adapter = adapter or FakeAdapter()
    return runtime


def message(text: str, *, user_id: int = 101, message_id: int = 1, **extra) -> dict:
    body = {"_user_id": user_id, "_chat_id": user_id, "_message_id": message_id,
            "text": text, "_photo": "", "_document": None, "_voice": False}
    body.update(extra)
    return body


def candidate(value: str, cid: str, message_id: str = "9") -> MemoryCandidate:
    return MemoryCandidate(id=cid, category="interests", key="hobbies", value=value,
                           confidence=0.8, evidence_kind=EvidenceKind.EXPLICIT,
                           sensitivity=Sensitivity.NORMAL, source_message_id=message_id)


FREE_ENDPOINT = ModelEndpoint(id="free/model:free", provider="remote",
                              capabilities=frozenset({"chat"}), local=False,
                              available=True, zero_cost=True, paid=False)


def consented(runtime: rt.ParticipantRuntime, person_key: str, *, discovery=False):
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True, discovery_enabled=discovery))


# -- deterministic extraction -----------------------------------------------------
def test_extraction_uses_only_explicit_self_statements():
    rows = rt.extract_candidates("k" * 64, "42", "Меня зовут Мира и я люблю горы")
    assert rows
    text = " ".join(str(row.value).lower() for row in rows)
    assert "горы" in text or "мира" in text


def test_extraction_skips_questions_and_noise():
    assert rt.extract_candidates("k" * 64, "43", "какая погода в городе?") == []


def test_secret_shaped_value_is_refused_by_collector(tmp_path):
    vault = PersonaVault(tmp_path, b"\x01" * 32)
    key = vault.key_for_telegram(55)
    vault.set_consent(key, ConsentState(memory_enabled=True))
    rows = rt.extract_candidates("k" * 64, "44", "я люблю пароль= hunter2")
    collector = rt.HighRecallCollector(vault)
    result = collector.ingest(key, rows)
    assert result.accepted == 0 and result.rejected_secret >= 1
    assert list(vault.iter_candidate_records(key)) == []


def warm(runtime, person_key):
    """Skip the first-contact intro in command-focused tests."""
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True))


# -- pipeline order -----------------------------------------------------------------
def test_guard_answers_before_any_model_route(tmp_path):
    runtime = make_runtime(tmp_path)
    adapter = runtime.adapter
    person_key = runtime.vault.key_for_telegram(101)
    warm(runtime, person_key)
    answer = asyncio.run(runtime.handle(runtime.settings.people[0], message("Какая у тебя модель?")))
    assert "Jeff" in answer
    assert adapter.calls == []
    snapshot = runtime.behavior.snapshot(person_key)
    assert snapshot.risk.score >= 1


def test_forbidden_owner_console_command_is_refused_without_llm(tmp_path):
    runtime = make_runtime(tmp_path)
    answer = asyncio.run(runtime.handle(runtime.settings.people[0], message("/sh ls -la")))
    assert answer == rt.FORBIDDEN_REPLY_RU
    assert runtime.adapter.calls == []
    assert runtime.behavior.snapshot(runtime.vault.key_for_telegram(101)).risk.score >= 1


def test_unknown_command_is_refused(tmp_path):
    runtime = make_runtime(tmp_path)
    warm(runtime, runtime.vault.key_for_telegram(101))
    answer = asyncio.run(runtime.handle(runtime.settings.people[0], message("/nonexistent")))
    assert "нет" in answer.lower()


# -- onboarding / consent --------------------------------------------------------------
def test_first_contact_gets_short_intro_and_silent_memory(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)

    intro = asyncio.run(runtime.handle(person, message("/start")))
    assert "Jeff" in intro and "AiBossman" in intro
    assert "?" not in intro.split("🙂")[-1] or "да/нет" not in intro
    consent = runtime.vault.consent(person_key)
    assert consent.memory_enabled and consent.remote_processing_enabled
    # no consent maze: a chat message goes straight to the model route
    runtime.catalog = {"free/model:free": ModelEndpoint(
        id="free/model:free", provider="remote", capabilities=frozenset({"chat"}),
        local=False, available=True, zero_cost=True, paid=False)}
    answer = asyncio.run(runtime.handle(person, message("привет", message_id=2)))
    assert answer == "готово"


def test_chat_requires_remote_consent(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    # first contact auto-enables memory and remote with a short intro
    intro = asyncio.run(runtime.handle(person, message("привет", message_id=2)))
    assert "Jeff" in intro
    runtime.catalog = {}
    runtime.catalog_checked_at = 1.0
    answer = asyncio.run(runtime.handle(person, message("привет", message_id=3)))
    assert answer == rt.NO_MODEL_RU
    assert runtime.adapter.calls == []


def test_remote_model_never_receives_persona_without_opt_in(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True))
    runtime.catalog = {"free/model:free": ModelEndpoint(
        id="free/model:free", provider="remote", capabilities=frozenset({"chat"}),
        local=False, available=True, zero_cost=True, paid=False)}
    runtime.vault.append_candidate(person_key, candidate("маркер памяти АЛЬФА", "a1"))
    asyncio.run(runtime.handle(person, message("привет, кто я?", message_id=5)))
    assert runtime.adapter.calls
    _, messages = runtime.adapter.calls[0]
    joined = json.dumps(messages, ensure_ascii=False)
    assert "АЛЬФА" not in joined  # remote_personalization_enabled=False -> memory withheld


def test_personalization_off_excludes_prior_chat_history_from_remote_request(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True,
        remote_personalization_enabled=True))
    runtime.catalog = {FREE_ENDPOINT.id: FREE_ENDPOINT}
    runtime.catalog_checked_at = 1.0
    asyncio.run(runtime.handle(person, message("PRIVATE_CHAT_MARKER_001", message_id=51)))
    asyncio.run(runtime.handle(person, message("/privacy personalization off", message_id=52)))
    asyncio.run(runtime.handle(person, message("Как дела?", message_id=53)))
    assert "PRIVATE_CHAT_MARKER_001" not in json.dumps(runtime.adapter.calls[-1], ensure_ascii=False)


def test_pause_memory_stops_durable_history_and_learning_log_across_restart(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True,
        remote_personalization_enabled=True))
    runtime.catalog = {FREE_ENDPOINT.id: FREE_ENDPOINT}
    runtime.catalog_checked_at = 1.0
    asyncio.run(runtime.handle(person, message("first turn", message_id=54)))
    before_history = runtime.store.db.execute("SELECT count(*) FROM history").fetchone()[0]
    before_learning = runtime.store.db.execute("SELECT count(*) FROM learning_log").fetchone()[0]
    asyncio.run(runtime.handle(person, message("/pause_memory", message_id=55)))
    asyncio.run(runtime.handle(person, message("PAUSED_CHAT_MARKER_002", message_id=56)))
    assert runtime.store.db.execute("SELECT count(*) FROM history").fetchone()[0] == before_history
    assert runtime.store.db.execute("SELECT count(*) FROM learning_log").fetchone()[0] == before_learning
    asyncio.run(runtime.close())

    restarted = make_runtime(tmp_path)
    restarted.catalog = {FREE_ENDPOINT.id: FREE_ENDPOINT}
    restarted.catalog_checked_at = 1.0
    asyncio.run(restarted.handle(person, message("What do you know?", message_id=57)))
    assert "PAUSED_CHAT_MARKER_002" not in json.dumps(restarted.adapter.calls[-1], ensure_ascii=False)
    asyncio.run(restarted.close())


def test_chat_route_answers_with_local_placeholder_when_no_route(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True))
    runtime.catalog = {}
    runtime.catalog_checked_at = 1.0   # skip the live refresh; allowlist yields no route
    answer = asyncio.run(runtime.handle(person, message("привет", message_id=6)))
    assert answer == rt.NO_MODEL_RU
    assert runtime.adapter.calls == []


def test_chat_route_uses_only_zero_cost_endpoint(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True))
    runtime.catalog = {"free/model:free": ModelEndpoint(
        id="free/model:free", provider="remote", capabilities=frozenset({"chat"}),
        local=False, available=True, zero_cost=True, paid=False)}
    answer = asyncio.run(runtime.handle(person, message("расскажи про горы", message_id=6)))
    assert answer == "готово"
    assert runtime.adapter.calls[0][0] == "free/model:free"


def test_learning_after_answer_and_engagement_up(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True))
    runtime.catalog = {"free/model:free": ModelEndpoint(
        id="free/model:free", provider="remote", capabilities=frozenset({"chat"}),
        local=False, available=True, zero_cost=True, paid=False)}
    asyncio.run(runtime.handle(person, message("я люблю горы", message_id=7)))
    facts = list(runtime.vault.iter_candidate_records(person_key))
    assert facts
    assert runtime.behavior.snapshot(person_key).engagement > 50


def test_discovery_at_most_one_and_skipped_not_repeated(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True, discovery_enabled=True))
    runtime.catalog = {"free/model:free": ModelEndpoint(
        id="free/model:free", provider="remote", capabilities=frozenset({"chat"}),
        local=False, available=True, zero_cost=True, paid=False)}
    first = asyncio.run(runtime.handle(person, message("хочу купить ноутбук", message_id=8)))
    assert first.count("?") <= 1
    if "?" in first:
        second = asyncio.run(runtime.handle(person, message("не хочу отвечать", message_id=9)))
        assert second.count("?") <= 1
        # the declined question is recorded as skipped and will not repeat
        skipped_file = runtime.vault.person_dir(person_key) / "discovery.json"
        if second.count("?") == 0:
            assert skipped_file.is_file()


# -- memory commands -------------------------------------------------------------------
def test_memory_pause_resume_and_forget(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True))

    assert "ничего не записал" in asyncio.run(runtime.handle(person, message("/memory", message_id=10)))
    assert "паузе" in asyncio.run(runtime.handle(person, message("/pause_memory", message_id=11)))
    assert runtime.vault.consent(person_key).memory_enabled is False
    assert "активна" in asyncio.run(runtime.handle(person, message("/resume_memory", message_id=12)))
    runtime.vault.append_candidate(person_key, candidate("горы", "c1"))
    assert "Забыл" in asyncio.run(runtime.handle(person, message("/forget горы", message_id=13)))
    assert list(runtime.vault.iter_candidate_records(person_key)) == []


def test_why_memory_explains_last_retrieval(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True,
        remote_personalization_enabled=True))
    runtime.catalog = {"free/model:free": ModelEndpoint(
        id="free/model:free", provider="remote", capabilities=frozenset({"chat"}),
        local=False, available=True, zero_cost=True, paid=False)}
    runtime.vault.append_candidate(person_key, candidate("горы", "w1"))
    asyncio.run(runtime.handle(person, message("что ты помнишь про горы?", message_id=14)))
    answer = asyncio.run(runtime.handle(person, message("/why_memory", message_id=15)))
    assert "гор" in answer


def test_delete_me_requires_confirm_and_zero_starts(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True))
    runtime.vault.append_candidate(person_key, candidate("море", "c2"))

    assert "подтверждаю" in asyncio.run(runtime.handle(person, message("/delete_me", message_id=20)))
    answer = asyncio.run(runtime.handle(person, message("подтверждаю", message_id=21)))
    assert "zero-start" in answer
    assert not (runtime.vault.person_dir(person_key) / "consent.json").exists()
    assert list(runtime.vault.iter_candidate_records(person_key)) == []


def test_privacy_command_is_secret_free(tmp_path):
    runtime = make_runtime(tmp_path)
    warm(runtime, runtime.vault.key_for_telegram(101))
    answer = asyncio.run(runtime.handle(runtime.settings.people[0], message("/privacy", message_id=30)))
    assert "Приватность" in answer
    assert "test-key" not in answer
    assert "ab" * 32 not in answer


def test_remote_personalization_off_excludes_saved_chat_after_restart(tmp_path):
    settings = make_settings(tmp_path)
    runtime = make_runtime(tmp_path, settings=settings)
    person = settings.people[0]
    person_key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True,
        remote_personalization_enabled=True))
    runtime.catalog = {"free/model:free": FREE_ENDPOINT}
    runtime.catalog_checked_at = 1.0

    asyncio.run(runtime.handle(person, message("PRIVATE_CHAT_MARKER_572", message_id=32)))
    set_roleplay(runtime.vault, person_key, enabled=True, mode=RolePlayMode.CHARACTER,
                 participant_consented=True, persona_label="PRIVATE_ROLEPLAY_MARKER_572")
    assert runtime.store.history(person.key)
    assert "только текущий запрос" in asyncio.run(runtime.handle(
        person, message("/privacy personalization off", message_id=33)))
    asyncio.run(runtime.close())

    reopened = make_runtime(tmp_path, settings=settings)
    reopened.catalog = {"free/model:free": FREE_ENDPOINT}
    reopened.catalog_checked_at = 1.0
    assert reopened.store.history(person.key)  # Revocation retains existing memory.
    answer = asyncio.run(reopened.handle(person, message(
        "Новый вопрос", message_id=34,
        _reply_to={"from_bot": True, "text": "PRIVATE_QUOTE_MARKER_572"})))
    assert answer == "готово"
    payload = json.dumps(reopened.adapter.calls[-1][1], ensure_ascii=False)
    assert "Новый вопрос" in payload
    assert "PRIVATE_CHAT_MARKER_572" not in payload
    assert "PRIVATE_ROLEPLAY_MARKER_572" not in payload
    assert "PRIVATE_QUOTE_MARKER_572" not in payload
    asyncio.run(reopened.close())


def test_paused_memory_retains_old_data_without_new_durable_turns(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True,
        remote_personalization_enabled=True, discovery_enabled=True))
    runtime.catalog = {"free/model:free": FREE_ENDPOINT}
    runtime.catalog_checked_at = 1.0
    runtime.store.remember(person.key, "RETAINED_CHAT_MARKER_472", "old reply")
    runtime.store.log(person.key, "RETAINED_LOG_MARKER_472", "old reply")
    runtime.store.put(f"last_context:{person.key}", ["retained context"])
    runtime.store.put(f"last_web:{person.key}", ["retained source"])
    runtime.store.put(f"discovery:{person.key}", {"key": "food", "question": "old question?"})
    history_before = runtime.store.history(person.key)
    logs_before = runtime.store.log_entries(person.key)
    behavior_before = runtime.behavior.snapshot(person_key).behavior

    assert "паузе" in asyncio.run(runtime.handle(person, message("/pause_memory", message_id=35)))
    answer = asyncio.run(runtime.handle(person, message("PAUSED_CHAT_MARKER_472", message_id=36)))
    assert answer == "готово"
    payload = json.dumps(runtime.adapter.calls[-1][1], ensure_ascii=False)
    assert "PAUSED_CHAT_MARKER_472" in payload
    assert "RETAINED_CHAT_MARKER_472" not in payload
    assert "Jeff" in asyncio.run(runtime.handle(person, message("кто ты?", message_id=39)))
    async def web_results(_query):
        return [{"title": "fresh source", "url": "https://example.org/fresh"}]
    runtime.models.web_results = web_results
    asyncio.run(runtime.handle(person, message("latest PAUSED_WEB_MARKER_472", message_id=40)))
    assert runtime.store.history(person.key) == history_before
    assert runtime.store.log_entries(person.key) == logs_before
    assert runtime.store.get(f"last_context:{person.key}") == ["retained context"]
    assert runtime.store.get(f"last_web:{person.key}") == ["retained source"]
    assert runtime.store.get(f"discovery:{person.key}") is None
    assert not (runtime.vault.person_dir(person_key) / "discovery.json").exists()
    assert runtime.behavior.snapshot(person_key).behavior == behavior_before
    asyncio.run(runtime.close())

    reopened = make_runtime(tmp_path)
    assert reopened.store.history(person.key) == history_before
    assert reopened.store.log_entries(person.key) == logs_before
    assert reopened.vault.consent(person_key).memory_enabled is False
    asyncio.run(reopened.close())


@pytest.mark.parametrize("resume_before_completion", [False, True])
def test_pause_during_provider_call_prevents_late_history_write(tmp_path, resume_before_completion):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True, discovery_enabled=True))
    runtime.catalog = {"free/model:free": FREE_ENDPOINT}
    runtime.catalog_checked_at = 1.0

    class PausingAdapter(FakeAdapter):
        async def chat(self, model, messages, **kw):
            await runtime.handle(person, message("/pause_memory", message_id=38))
            if resume_before_completion:
                await runtime.handle(person, message("/resume_memory", message_id=39))
            return await super().chat(model, messages, **kw)

    runtime.adapter = PausingAdapter()
    assert asyncio.run(runtime.handle(person, message("хочу купить ноутбук", message_id=37))) == "готово"
    assert runtime.store.history(person.key) == []
    assert runtime.store.log_entries(person.key) == []
    assert runtime.store.get(f"discovery:{person.key}") is None
    assert runtime.behavior.snapshot(person_key).behavior.events == 0
    asyncio.run(runtime.close())


def test_style_command_stores_preference(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    warm(runtime, person_key)
    answer = asyncio.run(runtime.handle(person, message("/style коротко и по делу", message_id=31)))
    assert "Принято" in answer
    values = " ".join(str(r.get("value")) for r in runtime.vault.iter_candidate_records(person_key))
    assert "коротко" in values


# -- idempotency --------------------------------------------------------------------------
def test_duplicate_update_ingests_once(tmp_path):
    home = tmp_path / "pit-v1.7"
    home.mkdir(parents=True)
    store = rt.PITStore(home)
    body = {"_user_id": 101, "_chat_id": 101, "_message_id": 5, "text": "привет",
            "_photo": "", "_document": None, "_voice": False}
    assert store.ingest(5, "101:101", body) is True
    assert store.ingest(5, "101:101", body) is False
    assert store.ingest(4, "101:101", body) is False
    assert store.claim("101:101", "chat") is not None
    store.finish(5, "done")
    store.close()


def test_pit_commands_land_on_control_lane(tmp_path):
    home = tmp_path / "pit-v1.7"
    home.mkdir(parents=True)
    store = rt.PITStore(home)
    assert store.lane({"text": "/memory"}) == "control"
    assert store.lane({"text": "привет"}) == "chat"
    store.close()


def test_open_allowlist_accepts_new_zero_start_participants(tmp_path):
    import dataclasses
    person_a = Person(user_id=101, chat_id=101, role="owner")
    settings = dataclasses.replace(make_settings(tmp_path, people=(person_a,)),
                                   allowlist_open=True)
    runtime = make_runtime(tmp_path, settings=settings)
    update = {"update_id": 100, "message": {
        "message_id": 7, "text": "привет",
        "from": {"id": 102, "is_bot": False}, "chat": {"id": 102, "type": "private"}}}
    runtime._ingest_update(update)
    assert runtime.store.claim("102:102", "chat") is not None

    bot_update = {"update_id": 101, "message": {
        "message_id": 8, "text": "привет",
        "from": {"id": 103, "is_bot": True}, "chat": {"id": 103, "type": "private"}}}
    runtime._ingest_update(bot_update)
    assert runtime.store.claim("103:103", "chat") is None
    runtime.store.close()


def test_open_allowlist_refuses_group_and_forwarded_material(tmp_path):
    import dataclasses
    person_a = Person(user_id=101, chat_id=101, role="owner")
    settings = dataclasses.replace(make_settings(tmp_path, people=(person_a,)),
                                   allowlist_open=True)
    runtime = make_runtime(tmp_path, settings=settings)
    group = {"update_id": 200, "message": {
        "message_id": 9, "text": "привет",
        "from": {"id": 103, "is_bot": False}, "chat": {"id": -100, "type": "group"}}}
    runtime._ingest_update(group)
    forwarded = {"update_id": 201, "message": {
        "message_id": 10, "text": "привет", "forward_from": {"id": 777},
        "from": {"id": 103, "is_bot": False}, "chat": {"id": 103, "type": "private"}}}
    runtime._ingest_update(forwarded)
    assert runtime.store.claim("103:103", "chat") is None
    runtime.store.close()


# -- two-ID isolation through the runtime -----------------------------------------------------
def test_participant_chat_uses_free_cloud_even_when_local_model_is_available(tmp_path, monkeypatch):
    from bcc.pit import resources
    monkeypatch.setattr(resources, "_read_free_vram_mb", lambda: 8192)
    settings = dataclasses.replace(make_settings(tmp_path),
                                   local_url="http://127.0.0.1:11434/v1",
                                   local_models=("bossman-fast:latest",))
    runtime = rt.ParticipantRuntime(settings)
    runtime.catalog_checked_at = 1.0
    local = FakeAdapter(text="локальный ответ")
    runtime.local_adapter = local
    runtime.adapter = FakeAdapter()
    runtime.catalog = {
        "bossman-fast:latest": ModelEndpoint(
            id="bossman-fast:latest", provider="local", capabilities=frozenset({"chat"}),
            local=True, available=True, zero_cost=True, paid=False),
        "free/model:free": ModelEndpoint(
            id="free/model:free", provider="remote", capabilities=frozenset({"chat"}),
            local=False, available=True, zero_cost=True, paid=False),
    }
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    warm(runtime, person_key)

    assert asyncio.run(runtime.handle(person, message("привет", message_id=80))) == "готово"
    assert len(local.calls) == 0 and len(runtime.adapter.calls) == 1

    class FailingLocal:
        def __init__(self):
            self.calls = 0
        async def chat(self, model, messages, **kw):
            self.calls += 1
            raise ProviderError("busy", kind="network")
        async def close(self):
            return None
    runtime.local_adapter = FailingLocal()
    answer = asyncio.run(runtime.handle(person, message("привет ещё", message_id=81)))
    assert answer == "готово"
    assert runtime.local_adapter.calls == 0 and len(runtime.adapter.calls) == 2
    # Route telemetry contains only remote answer attempts.
    log_path = settings.data_dir / "pit-v1.7" / "logs" / "route_log.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    assert rows
    assert rows[-1]["provider"] == "remote" and rows[-1]["ok"] is True
    assert all(row["provider"] == "remote" for row in rows)
    assert "context_tokens_est" in rows[-1]
    assert all("привет" not in json.dumps(row, ensure_ascii=False) for row in rows)


def test_local_failure_does_not_send_private_turn_to_cloud_without_consent(tmp_path, monkeypatch):
    from bcc.pit import resources
    from bcc.providers import ProviderError

    monkeypatch.setattr(resources, "_read_free_vram_mb", lambda: 8192)
    settings = dataclasses.replace(make_settings(tmp_path),
                                   local_url="http://127.0.0.1:11434/v1",
                                   local_models=("bossman-fast:latest",))
    runtime = rt.ParticipantRuntime(settings)
    runtime.catalog_checked_at = 1.0
    runtime.catalog = {
        "bossman-fast:latest": ModelEndpoint(
            id="bossman-fast:latest", provider="local", capabilities=frozenset({"chat"}),
            local=True, available=True, zero_cost=True, paid=False),
        "free/model:free": FREE_ENDPOINT,
    }
    class FailingLocal(FakeAdapter):
        async def chat(self, model, messages, **kw):
            raise ProviderError("down", kind="network")
    runtime.local_adapter = FailingLocal()
    remote = FakeAdapter()
    runtime.adapter = remote
    person = settings.people[0]
    person_key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=False))
    answer = asyncio.run(runtime.handle(person, message("личный запрос", message_id=82)))
    assert answer == rt.NO_REMOTE_RU
    assert remote.calls == []


def test_cloud_only_route_excludes_personal_memory_after_revocation(tmp_path):
    settings = dataclasses.replace(make_settings(tmp_path),
                                   local_url="http://127.0.0.1:11434/v1",
                                   local_models=("bossman-fast:latest",))
    runtime = rt.ParticipantRuntime(settings)
    runtime.catalog_checked_at = 1.0
    runtime.catalog = {
        "bossman-fast:latest": ModelEndpoint(
            id="bossman-fast:latest", provider="local", capabilities=frozenset({"chat"}),
            local=True, available=True, zero_cost=True, paid=False),
        "free/model:free": FREE_ENDPOINT,
    }
    local = FakeAdapter()
    runtime.local_adapter = local
    remote = FakeAdapter()
    runtime.adapter = remote
    person = settings.people[0]
    person_key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True,
        remote_personalization_enabled=True))
    runtime.vault.append_candidate(person_key, candidate("PRIVATE_MARKER_927", "private"))
    runtime.store.remember(person.key, "PRIOR_CHAT_MARKER_927", "old answer")
    assert asyncio.run(runtime.handle(person, message("расскажи обо мне", message_id=83))) == "готово"
    assert local.calls == []  # Local models are for learning, never participant replies.
    assert remote.calls and remote.calls[-1][0] == "free/model:free"
    assert "PRIOR_CHAT_MARKER_927" in json.dumps(remote.calls[-1], ensure_ascii=False)
    assert "только текущий запрос" in asyncio.run(runtime.handle(
        person, message("/privacy personalization off", message_id=84)))
    assert runtime.vault.consent(person_key).remote_personalization_enabled is False
    assert asyncio.run(runtime.handle(person, message("Новый вопрос", message_id=85))) == "готово"
    payload = json.dumps(remote.calls[-1], ensure_ascii=False)
    assert "PRIVATE_MARKER_927" not in payload
    assert "PRIOR_CHAT_MARKER_927" not in payload
    assert local.calls == []
    asyncio.run(runtime.close())


def test_pause_resume_during_cloud_call_does_not_record_late_turn(tmp_path):
    settings = dataclasses.replace(make_settings(tmp_path),
                                   local_url="http://127.0.0.1:11434/v1",
                                   local_models=("bossman-fast:latest",))
    runtime = rt.ParticipantRuntime(settings)
    runtime.catalog_checked_at = 1.0
    runtime.catalog = {
        "bossman-fast:latest": ModelEndpoint(
            id="bossman-fast:latest", provider="local", capabilities=frozenset({"chat"}),
            local=True, available=True, zero_cost=True, paid=False),
        "free/model:free": FREE_ENDPOINT,
    }
    person = settings.people[0]
    person_key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(person_key, ConsentState(
        memory_enabled=True, remote_processing_enabled=True,
        remote_personalization_enabled=True))
    runtime.store.remember(person.key, "PRE_PAUSE_MARKER_927", "old answer")
    history_before = runtime.store.history(person.key)
    logs_before = runtime.store.log_entries(person.key)

    class PausingRemote(FakeAdapter):
        async def chat(self, model, messages, **kw):
            await runtime.handle(person, message("/pause_memory", message_id=85))
            await runtime.handle(person, message("/resume_memory", message_id=86))
            return await super().chat(model, messages, **kw)

    local = FakeAdapter()
    runtime.local_adapter = local
    remote = PausingRemote()
    runtime.adapter = remote
    assert asyncio.run(runtime.handle(person, message("новый вопрос", message_id=87))) == "готово"
    assert remote.calls and remote.calls[0][0] == "free/model:free"
    assert local.calls == []
    assert runtime.store.history(person.key) == history_before
    assert runtime.store.log_entries(person.key) == logs_before
    asyncio.run(runtime.close())


def test_photo_memory_starts_only_after_verified_telegram_delivery(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(key, ConsentState(memory_enabled=True))
    events = []
    asset = type("Asset", (), {"person_key": key})()
    runtime._pending_photo_memory[(person.key, "7")] = (asset, "caption")
    claims = iter([(101, message("photo answer", message_id=7))])
    def claim(*args):
        try:
            return next(claims)
        except StopIteration:
            raise asyncio.CancelledError
    monkeypatch.setattr(runtime.store, "claim", claim)
    monkeypatch.setattr(runtime.store, "finish", lambda *args: events.append("finish"))
    async def handle(*args, **kwargs):
        return "На фото кот."
    async def send(*args, **kwargs):
        events.append("send")
        assert kwargs["reply_to_message_id"] == 7
        return 42
    monkeypatch.setattr(runtime, "handle", handle)
    monkeypatch.setattr(runtime.telegram, "send", send)
    monkeypatch.setattr(runtime.photo_pipeline, "schedule_background_after_delivery",
                        lambda *args, **kwargs: events.append("memory"))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime._worker(person, "chat"))
    assert events == ["send", "finish", "memory"]
    runtime.store.close()


@pytest.mark.parametrize("pause_stage", ["before_analysis", "during_analysis"])
def test_pause_resume_invalidates_scheduled_photo_memory(tmp_path, pause_stage):
    from bcc.pit.photo_pipeline import PhotoPipeline

    async def go():
        runtime = make_runtime(tmp_path)
        person = runtime.settings.people[0]
        person_key = runtime.vault.key_for_telegram(person.user_id)
        warm(runtime, person_key)
        idle_entered = asyncio.Event()
        analysis_entered = asyncio.Event()
        release = asyncio.Event()
        analysis_calls = []
        ingest_calls = []

        class Vision:
            async def analyze_for_memory(self, _data, _mime, _caption):
                analysis_entered.set()
                if pause_stage == "during_analysis":
                    await release.wait()
                analysis_calls.append("analyzed")
                return {"scene": "красная машина"}

        pipe = PhotoPipeline(runtime.vault, vision=Vision(), ai_max_ready=True)
        runtime.photo_pipeline = pipe
        asset = pipe.store.ingest(person_key, 65, JPEG_BYTES)
        original_ingest = pipe.collector.ingest

        def ingest_spy(key, candidates):
            ingest_calls.append(key)
            return original_ingest(key, candidates)

        pipe.collector.ingest = ingest_spy
        if pause_stage == "before_analysis":
            async def wait_for_idle(*, max_wait_seconds=30.0):
                idle_entered.set()
                await release.wait()
            pipe._wait_for_idle = wait_for_idle

        pipe.schedule_background_after_delivery(asset, caption="что здесь?")
        signal = idle_entered if pause_stage == "before_analysis" else analysis_entered
        await asyncio.wait_for(signal.wait(), 2)
        await runtime.handle(person, message("/pause_memory", message_id=66))
        await runtime.handle(person, message("/resume_memory", message_id=67))
        release.set()
        await pipe.drain_background(timeout=2)

        assert analysis_calls == ([] if pause_stage == "before_analysis" else ["analyzed"])
        assert ingest_calls == []
        assert list(runtime.vault.iter_candidate_records(person_key)) == []
        await runtime.close()

    asyncio.run(go())


def test_delete_then_new_profile_cannot_receive_old_photo_memory(tmp_path):
    from bcc.pit.photo_pipeline import PhotoPipeline

    async def go():
        runtime = make_runtime(tmp_path)
        person = runtime.settings.people[0]
        person_key = runtime.vault.key_for_telegram(person.user_id)
        warm(runtime, person_key)
        analysis_entered = asyncio.Event()
        release = asyncio.Event()
        ingest_calls = []

        class Vision:
            async def analyze_for_memory(self, _data, _mime, _caption):
                analysis_entered.set()
                await release.wait()
                return {"scene": "красная машина"}

        pipe = PhotoPipeline(runtime.vault, vision=Vision(), ai_max_ready=True)
        runtime.photo_pipeline = pipe
        asset = pipe.store.ingest(person_key, 68, JPEG_BYTES)
        original_ingest = pipe.collector.ingest

        def ingest_spy(key, candidates):
            ingest_calls.append(key)
            return original_ingest(key, candidates)

        pipe.collector.ingest = ingest_spy
        pipe.schedule_background_after_delivery(asset, caption="что здесь?")
        await asyncio.wait_for(analysis_entered.wait(), 2)
        await runtime.handle(person, message("/delete_me", message_id=69))
        assert "zero-start" in await runtime.handle(person, message("подтверждаю", message_id=70))
        await runtime.handle(person, message("привет", message_id=71))
        assert runtime.vault.consent(person_key).memory_enabled is True
        release.set()
        await pipe.drain_background(timeout=2)

        assert ingest_calls == []
        assert list(runtime.vault.iter_candidate_records(person_key)) == []
        await runtime.close()

    asyncio.run(go())


def test_remote_route_used_when_local_catalog_down(tmp_path):
    settings = dataclasses.replace(make_settings(tmp_path),
                                   local_url="http://127.0.0.1:9/v1",
                                   local_models=("bossman-fast:latest",))
    runtime = rt.ParticipantRuntime(settings)
    runtime.local_adapter = FakeAdapter()
    async def dead_list():
        raise ProviderError("down", kind="network")
    runtime.local_adapter.list_model_info = dead_list
    runtime.adapter = FakeAdapter()
    runtime.catalog = {"free/model:free": ModelEndpoint(
        id="free/model:free", provider="remote", capabilities=frozenset({"chat"}),
        local=False, available=True, zero_cost=True, paid=False)}
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    warm(runtime, person_key)
    answer = asyncio.run(runtime.handle(person, message("привет", message_id=90)))
    assert answer == "готово"
    assert len(runtime.adapter.calls) == 1


def test_two_ids_retrieve_only_own_memory(tmp_path):
    person_a = Person(user_id=101, chat_id=101, role="owner")
    person_b = Person(user_id=102, chat_id=102, role="guest")
    settings = make_settings(tmp_path, people=(person_a, person_b))
    runtime = make_runtime(tmp_path, settings=settings)
    runtime.catalog = {"free/model:free": ModelEndpoint(
        id="free/model:free", provider="remote", capabilities=frozenset({"chat"}),
        local=False, available=True, zero_cost=True, paid=False)}
    vault = runtime.vault
    key_a, key_b = vault.key_for_telegram(101), vault.key_for_telegram(102)
    vault.set_consent(key_a, ConsentState(memory_enabled=True))
    vault.append_candidate(key_a, candidate("секретный маркер АЛЬФА", "a1"))
    vault.set_consent(key_b, ConsentState(memory_enabled=True, remote_processing_enabled=True))

    answer = asyncio.run(runtime.handle(person_b, message(
        "расскажи про мои увлечения", user_id=102, message_id=40)))
    assert answer == "готово"
    model, messages = runtime.adapter.calls[-1]
    assert "АЛЬФА" not in json.dumps(messages, ensure_ascii=False)
    assert list(vault.iter_candidate_records(key_b)) == []


# -- laptop media honesty ----------------------------------------------------------------------
def test_photo_on_laptop_is_stored_but_not_claimed(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)

    async def fake_fetch(file_id, max_bytes=IMAGE_MAX_BYTES):
        return JPEG_BYTES
    monkeypatch.setattr(runtime.telegram, "fetch_file", fake_fetch)

    answer = asyncio.run(runtime.handle(person, message("", _photo="fileid", message_id=50)))
    assert "AI Max" in answer
    assert (runtime.vault.person_dir(person_key) / "media" / "latest.json").is_file()


def test_paused_photo_is_analyzed_without_new_media_retention(tmp_path, monkeypatch):
    from bcc.pit.photo_pipeline import PhotoPipeline

    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(person.user_id)
    warm(runtime, person_key)
    old = runtime.photo_pipeline.store.ingest(person_key, 50, JPEG_BYTES)
    fresh_bytes = b"\xff\xd8\xff" + b"\x01" * 64

    class Vision:
        async def analyze_fast(self, data, mime, user_prompt):
            assert data == fresh_bytes and mime == "image/jpeg"
            return "На фото тестовый объект."

    runtime.photo_pipeline = PhotoPipeline(runtime.vault, vision=Vision(), ai_max_ready=True)

    async def fetch_file(_file_id, _limit):
        return fresh_bytes

    monkeypatch.setattr(runtime.telegram, "fetch_file", fetch_file)
    asyncio.run(runtime.handle(person, message("/pause_memory", message_id=51)))
    answer = asyncio.run(runtime.handle(person, message(
        "что на фото?", _photo="fresh-photo", message_id=52)))
    assert "На фото тестовый объект" in answer
    assert "не сохраняю" in answer
    assert runtime.photo_pipeline.store.latest(person_key).sha256 == old.sha256
    assert list((runtime.vault.person_dir(person_key) / "media" / "inbox").iterdir()) == [old.path]
    assert runtime._pending_photo_memory == {}

    refusal = asyncio.run(runtime.handle(person, message(
        "/reference", _photo="new-reference", message_id=53)))
    assert "не сохраняю" in refusal
    assert runtime.photo_pipeline.store.references(person_key) == ()
    asyncio.run(runtime.close())

    reopened = make_runtime(tmp_path)
    assert reopened.photo_pipeline.store.latest(person_key).sha256 == old.sha256
    assert reopened.photo_pipeline.store.references(person_key) == ()
    assert list((reopened.vault.person_dir(person_key) / "media" / "inbox").iterdir()) == [old.path]
    asyncio.run(reopened.close())


def test_paused_photo_caption_edit_uses_current_bytes_without_retaining_them(tmp_path, monkeypatch):
    from bcc.pit.photo_edit import PhotoEditPipeline
    from bcc.pit.studio_image_edit import EditedImage

    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(person.user_id)
    warm(runtime, person_key)
    old = runtime.photo_pipeline.store.ingest(person_key, 60, JPEG_BYTES)
    fresh_bytes = b"\xff\xd8\xff" + b"\x01" * 64
    edited = b"\xff\xd8\xff" + b"\x02" * 64
    seen = []

    class Broker:
        async def edit(self, **kw):
            assert kw["image_bytes"] == fresh_bytes
            assert kw["references"] == ()
            seen.append("edited")
            return EditedImage(edited, "image/jpeg", "sha", "run", 1)

    runtime.photo_edit = PhotoEditPipeline(runtime.vault, broker=Broker(), ai_max_ready=True)

    async def fetch_file(_file_id, _limit):
        return fresh_bytes

    async def send_photo(_person, data, _caption):
        assert data == edited
        seen.append("sent")

    monkeypatch.setattr(runtime.telegram, "fetch_file", fetch_file)
    monkeypatch.setattr(runtime.telegram, "send_photo", send_photo)
    asyncio.run(runtime.handle(person, message("/pause_memory", message_id=61)))
    assert asyncio.run(runtime.handle(person, message(
        "убери фон", _photo="fresh-photo", message_id=62))) == ""
    assert seen == ["edited", "sent"]
    assert runtime.photo_pipeline.store.latest(person_key).sha256 == old.sha256
    assert list((runtime.vault.person_dir(person_key) / "media" / "inbox").iterdir()) == [old.path]
    asyncio.run(runtime.close())


def test_reference_caption_does_not_replace_latest_or_enter_model(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    key = runtime.vault.key_for_telegram(101)
    warm(runtime, key)
    target = runtime.photo_pipeline.store.ingest(key, 40, JPEG_BYTES)

    async def fake_fetch(file_id, max_bytes=IMAGE_MAX_BYTES):
        return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x22" * 60
    monkeypatch.setattr(runtime.telegram, "fetch_file", fake_fetch)
    answer = asyncio.run(runtime.handle(person, message(
        "/reference", _photo="own-reference", message_id=41)))
    assert "Референс" in answer
    assert runtime.photo_pipeline.store.latest(key).sha256 == target.sha256
    assert len(runtime.photo_pipeline.store.references(key)) == 1
    assert runtime.adapter.calls == []


def test_photo_edit_unavailable_returns_text_instead_of_crashing(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    warm(runtime, runtime.vault.key_for_telegram(101))

    async def fake_fetch(file_id, max_bytes=IMAGE_MAX_BYTES):
        return JPEG_BYTES
    monkeypatch.setattr(runtime.telegram, "fetch_file", fake_fetch)
    answer = asyncio.run(runtime.handle(person, message(
        "убери фон", _photo="target", message_id=42)))
    assert "Скоро" in answer


def test_generation_sends_verified_studio_bytes_only_when_licensed(tmp_path, monkeypatch):
    from bcc.pit.studio_image_edit import EditedImage
    import hashlib
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    calls = []

    class Broker:
        async def generate(self, *, prompt):
            calls.append(prompt)
            return EditedImage(JPEG_BYTES, "image/jpeg", hashlib.sha256(JPEG_BYTES).hexdigest(), "run", 1)

    async def free_capacity():
        return True

    async def fake_send_photo(target, data, caption):
        calls.append((target.key, data, caption))

    runtime.photo_services.edit = Broker()
    runtime.photo_services.config = dataclasses.replace(
        runtime.photo_services.config, ai_max_media_ready=True,
        image_license_mode="research_eval")
    monkeypatch.setattr(runtime.capacity_guard, "local_allowed", free_capacity)
    monkeypatch.setattr(runtime.telegram, "send_photo", fake_send_photo)
    assert "Напиши, что" in asyncio.run(runtime._generate_image(person, "Генерацию фото"))
    assert calls == []
    assert asyncio.run(runtime._generate_image(person, "кот")) == ""
    assert calls[0] == "кот" and calls[1][1] == JPEG_BYTES

    runtime.settings = dataclasses.replace(runtime.settings, allowlist_open=True)
    assert "не включена" in asyncio.run(runtime._generate_image(person, "кот"))
    assert len(calls) == 2


def test_image_generation_intent_gets_placeholder(tmp_path):
    runtime = make_runtime(tmp_path)
    warm(runtime, runtime.vault.key_for_telegram(101))
    answer = asyncio.run(runtime.handle(runtime.settings.people[0],
                                        message("нарисуй кота", message_id=60)))
    assert answer == "Скоро научусь, малышка 😊"
    assert asyncio.run(runtime.handle(runtime.settings.people[0],
                                      message("генерацию фото", message_id=61))) == answer


def test_unreadable_advisory_behavior_file_does_not_break_chat_or_memory(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    key = runtime.vault.key_for_telegram(person.user_id)
    warm(runtime, key)
    runtime.catalog = {FREE_ENDPOINT.id: FREE_ENDPOINT}
    runtime.catalog_checked_at = 1.0

    def denied(*args, **kwargs):
        raise PermissionError("advisory security telemetry denied")

    monkeypatch.setattr(runtime.behavior.behavior, "read", denied)
    monkeypatch.setattr(runtime.behavior.behavior, "apply", denied)
    assert asyncio.run(runtime.handle(person, message("привет", message_id=62))) == "готово"
    assert runtime.vault.consent(key).memory_enabled is True
    assert runtime.store.history(person.key)


def test_roleplay_requires_consent_then_persists(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    warm(runtime, person_key)

    assert "Включить" in asyncio.run(runtime.handle(person, message("/roleplay капитан", message_id=70)))
    assert "включён" in asyncio.run(runtime.handle(person, message("да", message_id=71)))
    state = load_roleplay(runtime.vault, person_key)
    assert state.enabled and state.participant_consented
    assert "выключен" in asyncio.run(runtime.handle(person, message("/roleplay off", message_id=72)))
    assert not load_roleplay(runtime.vault, person_key).enabled


def test_extract_candidate_ids_are_unique_per_message():
    first = rt.extract_candidates("k" * 64, "1", "я люблю горы")
    second = rt.extract_candidates("k" * 64, "2", "я люблю горы")
    assert {r.id for r in first} != {r.id for r in second}


def test_sticker_input_gets_emoji_sticker_reply(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    warm(runtime, runtime.vault.key_for_telegram(101))
    for message_id in range(1, 4):
        answer = asyncio.run(runtime.handle(
            person, message("", message_id=message_id, _sticker="😀")))
        assert answer in rt.STICKER_REPLIES
    assert runtime.adapter.calls == []


def test_reply_to_message_is_used_as_context(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    warm(runtime, person_key)
    consent = runtime.vault.consent(person_key)
    consent.remote_personalization_enabled = True
    runtime.vault.set_consent(person_key, consent)
    runtime.catalog = {"free/model:free": ModelEndpoint(
        id="free/model:free", provider="remote", capabilities=frozenset({"chat"}),
        local=False, available=True, zero_cost=True, paid=False)}
    asyncio.run(runtime.handle(person, message(
        "а теперь без таблиц", message_id=120,
        _reply_to={"from_bot": False, "text": "Васька живёт у бабушки"})))
    _, sent = runtime.adapter.calls[-1]
    joined = json.dumps(sent, ensure_ascii=False)
    assert "Васька живёт у бабушки" in joined
    assert "не инструкции" in joined


def test_history_window_covers_thirty_messages(tmp_path):
    home = tmp_path / "pit-v1.7"
    home.mkdir(parents=True)
    store = rt.PITStore(home)
    for index in range(20):
        store.remember("101:101", f"вопрос {index}", f"ответ {index}")
    window = store.history("101:101")
    assert len(window) >= 30                      # owner requirement: >= 30 messages
    assert len(window) <= 32                      # bounded: 16 pairs
    assert "вопрос 19" in window[-2]["content"]
    assert "ответ 19" in window[-1]["content"]
    store.close()


def test_photo_analysis_runs_local_vision_when_ready(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    warm(runtime, person_key)

    class FakeVision:
        async def analyze_fast(self, data, mime, user_prompt):
            return "На фото рыжий кот на подоконнике"
        async def analyze_for_memory(self, data, mime, caption):
            return {"scene": "кот на подоконнике", "memory_hints": ["пользователь показывает кота"]}
        async def close(self):
            return None

    from bcc.pit.photo_pipeline import PhotoPipeline
    runtime.photo_pipeline = PhotoPipeline(runtime.vault, vision=FakeVision(),
                                           ai_max_ready=True)
    async def fake_fetch(file_id, max_bytes=IMAGE_MAX_BYTES):
        return JPEG_BYTES
    monkeypatch.setattr(runtime.telegram, "fetch_file", fake_fetch)

    answer = asyncio.run(runtime.handle(person, message("кто это?", _photo="fid", message_id=130)))
    assert "кот" in answer
    latest = runtime.vault.person_dir(person_key) / "media" / "latest.json"
    assert latest.is_file()
