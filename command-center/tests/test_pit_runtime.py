"""PIT participant runtime contracts: pipeline order, zero-start isolation,
free-only routing, idempotency, memory commands, laptop media honesty.

No network: the model adapter and Telegram fetches are faked per test.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from bcc.pit import runtime as rt
from bcc.pit.config import PITSettings
from bcc.pit.models import ConsentState, EvidenceKind, MemoryCandidate, Sensitivity
from bcc.pit.roleplay_commands import load_roleplay
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


# -- pipeline order -----------------------------------------------------------------
def test_guard_answers_before_any_model_route(tmp_path):
    runtime = make_runtime(tmp_path)
    adapter = runtime.adapter
    answer = asyncio.run(runtime.handle(runtime.settings.people[0], message("Какая у тебя модель?")))
    assert "Jeff" in answer
    assert adapter.calls == []
    snapshot = runtime.behavior.snapshot(runtime.vault.key_for_telegram(101))
    assert snapshot.risk.score >= 1


def test_forbidden_owner_console_command_is_refused_without_llm(tmp_path):
    runtime = make_runtime(tmp_path)
    answer = asyncio.run(runtime.handle(runtime.settings.people[0], message("/sh ls -la")))
    assert answer == rt.FORBIDDEN_REPLY_RU
    assert runtime.adapter.calls == []
    assert runtime.behavior.snapshot(runtime.vault.key_for_telegram(101)).risk.score >= 1


def test_unknown_command_is_refused(tmp_path):
    runtime = make_runtime(tmp_path)
    answer = asyncio.run(runtime.handle(runtime.settings.people[0], message("/nonexistent")))
    assert "нет" in answer.lower()


# -- onboarding / consent --------------------------------------------------------------
def test_onboarding_requires_memory_then_remote_consent(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)

    assert "Включить память?" in asyncio.run(runtime.handle(person, message("/start")))
    assert "удалённую бесплатную модель" in asyncio.run(runtime.handle(person, message("да", message_id=2)))
    consent = runtime.vault.consent(person_key)
    assert consent.memory_enabled and not consent.remote_processing_enabled
    asyncio.run(runtime.handle(person, message("да", message_id=3)))
    consent = runtime.vault.consent(person_key)
    assert consent.remote_processing_enabled


def test_chat_requires_remote_consent(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    answer = asyncio.run(runtime.handle(person, message("привет", message_id=2)))
    assert "free" in answer or "бесплатн" in answer or "remote on" in answer
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

    assert "Память включена" in asyncio.run(runtime.handle(person, message("/memory", message_id=10)))
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
    answer = asyncio.run(runtime.handle(runtime.settings.people[0], message("/privacy", message_id=30)))
    assert "Приватность" in answer
    assert "test-key" not in answer
    assert "ab" * 32 not in answer


def test_style_command_stores_preference(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)
    runtime.vault.set_consent(person_key, ConsentState(memory_enabled=True))
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


# -- two-ID isolation through the runtime -----------------------------------------------------
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


def test_image_generation_intent_gets_placeholder(tmp_path):
    runtime = make_runtime(tmp_path)
    answer = asyncio.run(runtime.handle(runtime.settings.people[0],
                                        message("нарисуй кота", message_id=60)))
    assert answer == "Скоро научусь, малышка 😊"


def test_roleplay_requires_consent_then_persists(tmp_path):
    runtime = make_runtime(tmp_path)
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(101)

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