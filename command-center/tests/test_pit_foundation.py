from __future__ import annotations

import json

import pytest

from bcc.pit.collector import HighRecallCollector
from bcc.pit.context import select_persona_context
from bcc.pit.discovery import DiscoveryCandidate, choose_discovery_question
from bcc.pit.identity import derive_person_key, scoped_person_dir
from bcc.pit.models import ConsentState, EvidenceKind, MemoryCandidate, Sensitivity
from bcc.pit.policy import TelegramToolPolicy
from bcc.pit.public_guard import GuardKind, public_guard
from bcc.pit.presentation import InternalRouteMeta, public_model_label, render_jeff_reply
from bcc.pit.risk import RiskLedger
from bcc.pit.capabilities import LAPTOP_IMAGE_GENERATION_REPLY_RU, image_generation_reply
from bcc.pit.discovery import DiscoveryMode
from bcc.pit.companion_profile import PIT_BLOCKED_COMMANDS, PitParticipantPolicy, command_allowed_in_pit
from bcc.pit.participant_context import PIT_ASSISTANT_SYSTEM, build_participant_context
from bcc.pit.router import (
    ModelEndpoint,
    NoEligibleRoute,
    PrivacyClass,
    RouteRequest,
    choose_route,
)
from bcc.pit.telegram_contract import TelegramEnvelope, idempotency_key
from bcc.pit.vault import PersonaVault


SALT = b"pit-test-salt-is-long-enough-123"


def candidate(
    cid: str,
    value: str,
    *,
    category: str = "preference",
    sensitivity: Sensitivity = Sensitivity.NORMAL,
    confidence: float = 0.2,
):
    return MemoryCandidate(
        id=cid,
        category=category,
        key="answer_style",
        value=value,
        confidence=confidence,
        evidence_kind=EvidenceKind.EXPLICIT,
        sensitivity=sensitivity,
        source_message_id="m-1",
        source_model="test",
    )


def test_person_key_is_stable_pseudonymous_and_salt_scoped():
    a = derive_person_key(123456, SALT)
    assert a == derive_person_key("123456", SALT)
    assert len(a) == 64
    assert "123456" not in a
    assert a != derive_person_key(123456, b"another-long-enough-test-salt")


def test_person_scope_cannot_escape(tmp_path):
    key = derive_person_key(1, SALT)
    assert scoped_person_dir(tmp_path, key).parent == tmp_path.resolve()
    with pytest.raises(ValueError):
        scoped_person_dir(tmp_path, "../other")


def test_cross_user_vault_isolation_and_delete(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    a, b = vault.key_for_telegram(1001), vault.key_for_telegram(1002)
    enabled = ConsentState(memory_enabled=True, raw_history_enabled=True)
    vault.set_consent(a, enabled)
    vault.set_consent(b, enabled)
    assert vault.append_candidate(a, candidate("a1", "short answers"))
    assert vault.append_candidate(b, candidate("b1", "long explanations"))

    export_a = json.dumps(vault.export(a), ensure_ascii=False)
    export_b = json.dumps(vault.export(b), ensure_ascii=False)
    assert "short answers" in export_a and "long explanations" not in export_a
    assert "long explanations" in export_b and "short answers" not in export_b

    assert vault.delete(a)
    assert not vault.person_dir(a).exists()
    assert vault.person_dir(b).exists()


def test_collection_first_keeps_low_confidence_normal_candidates(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    key = vault.key_for_telegram(7)
    vault.set_consent(key, ConsentState(memory_enabled=True))
    result = HighRecallCollector(vault).ingest(key, [candidate("c1", "prefers examples", confidence=0.05)])
    assert result.accepted == 1
    rows = list(vault.iter_candidate_records(key))
    assert rows[0]["confidence"] == 0.05


def test_secret_and_sensitive_memory_fail_closed(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    key = vault.key_for_telegram(7)
    vault.set_consent(key, ConsentState(memory_enabled=True, sensitive_memory_enabled=False))
    collector = HighRecallCollector(vault)
    result = collector.ingest(
        key,
        [
            candidate("normal", "likes concise answers"),
            candidate("secret", "password: hunter2"),
            candidate("health", "private health note", category="health"),
        ],
    )
    assert result.accepted == 1
    assert result.rejected_secret == 1
    assert result.rejected_sensitive == 1


def test_explicit_sensitive_opt_in_is_separate(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    key = vault.key_for_telegram(8)
    vault.set_consent(key, ConsentState(memory_enabled=True, sensitive_memory_enabled=True))
    result = HighRecallCollector(vault).ingest(
        key,
        [candidate("s1", "user explicitly asked to remember this", category="health")],
    )
    assert result.accepted == 1


def test_raw_spool_requires_consent_and_redacts_obvious_token(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    key = vault.key_for_telegram(9)
    assert not vault.append_raw_event(key, {"message_id": 1, "text": "hello"})
    vault.set_consent(key, ConsentState(memory_enabled=True, raw_history_enabled=True))
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    assert vault.append_raw_event(key, {"message_id": 2, "text": f"token={token}"})
    stored = (vault.person_dir(key) / "raw" / "events.jsonl").read_text(encoding="utf-8")
    assert token not in stored
    assert "REDACTED_SECRET" in stored


def test_outcome_stream_exists_for_future_garbage_sorter(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    key = vault.key_for_telegram(10)
    vault.set_consent(key, ConsentState(memory_enabled=True))
    collector = HighRecallCollector(vault)
    collector.record_outcome(key, candidate_id="x", outcome="later_seen", useful=False, retrieved=True)
    text = (vault.person_dir(key) / "memory_outcomes.jsonl").read_text(encoding="utf-8")
    assert '"candidate_id": "x"' in text
    assert '"useful": false' in text


def test_telegram_tool_registry_has_no_computer_shell_admin_payment_or_trading():
    policy = TelegramToolPolicy()
    for forbidden in (
        "computer.click",
        "shell.run",
        "terminal.exec",
        "owner.approve",
        "secrets.read",
        "payments.charge",
        "trading.execute",
        "admin.users",
    ):
        assert not policy.allows(forbidden), forbidden
    for allowed in ("web.search", "browser.read", "calculator", "vision.analyze_own", "file.analyze_upload"):
        assert policy.allows(allowed), allowed


def test_laptop_profile_can_choose_remote_zero_cost_primary():
    request = RouteRequest(intent="chat")
    endpoints = [
        ModelEndpoint(
            id="local-main",
            provider="local",
            capabilities=frozenset({"chat"}),
            local=True,
            available=False,
            predicted_quality=0.95,
            latency_score=0.2,
            privacy_risk=0.0,
        ),
        ModelEndpoint(
            id="glm-5.3",
            provider="openrouter",
            capabilities=frozenset({"chat"}),
            zero_cost=True,
            predicted_quality=0.90,
            latency_score=0.35,
            privacy_risk=0.35,
        ),
        ModelEndpoint(
            id="free-fallback",
            provider="allowlisted-cloud",
            capabilities=frozenset({"chat"}),
            zero_cost=True,
            predicted_quality=0.70,
            latency_score=0.30,
            privacy_risk=0.35,
        ),
    ]
    decision = choose_route(request, endpoints)
    assert decision.selected_model == "glm-5.3"
    assert decision.reason_code == "REMOTE_ZERO_COST"


def test_paid_route_never_happens_silently():
    request = RouteRequest(intent="chat", max_cost_usd=0)
    paid = ModelEndpoint(
        id="paid",
        provider="cloud",
        capabilities=frozenset({"chat"}),
        paid=True,
        predicted_quality=1.0,
    )
    with pytest.raises(NoEligibleRoute):
        choose_route(request, [paid], allow_paid=False)


def test_local_only_cannot_fall_back_to_remote():
    request = RouteRequest(intent="private", privacy=PrivacyClass.LOCAL_ONLY)
    remote = ModelEndpoint(
        id="remote",
        provider="cloud",
        capabilities=frozenset({"chat"}),
        zero_cost=True,
    )
    with pytest.raises(NoEligibleRoute):
        choose_route(request, [remote])


def test_local_model_wins_after_ai_max_transition_when_quality_is_sufficient():
    request = RouteRequest(intent="chat")
    local = ModelEndpoint(
        id="local-main",
        provider="llama.cpp",
        capabilities=frozenset({"chat"}),
        local=True,
        predicted_quality=0.84,
        latency_score=0.25,
        privacy_risk=0.0,
    )
    remote = ModelEndpoint(
        id="remote-free",
        provider="cloud",
        capabilities=frozenset({"chat"}),
        zero_cost=True,
        predicted_quality=0.86,
        latency_score=0.25,
        privacy_risk=0.35,
    )
    assert choose_route(request, [local, remote]).selected_model == "local-main"


def test_discovery_returns_at_most_one_and_suppresses_sensitive_probe():
    selected = choose_discovery_question(
        [
            DiscoveryCandidate("style", "Коротко или с примерами?", 0.9, 0.9, 0.8, annoyance_cost=0.1),
            DiscoveryCandidate("sensitive", "Sensitive?", 1, 1, 1, sensitivity_risk=0.8),
        ],
        enabled=True,
    )
    assert selected is not None
    assert selected.key == "style"
    assert choose_discovery_question([], enabled=True) is None


def test_persona_context_is_bounded_not_full_vault_dump():
    rows = [
        {"id": f"i{i}", "category": "travel", "key": "travel_style", "value": f"travel item {i}",
         "confidence": 0.9, "utility_score": i / 100, "evidence_kind": "explicit"}
        for i in range(100)
    ]
    selected = select_persona_context("travel plan", rows, max_items=12)
    assert 1 <= len(selected) <= 12


def test_telegram_update_idempotency_is_stable():
    event = TelegramEnvelope(1, 2, 3, 4, "hello")
    assert idempotency_key(event) == idempotency_key(event)
    assert idempotency_key(event) != idempotency_key(TelegramEnvelope(2, 2, 3, 4, "hello"))


def test_pit_chat_has_no_owner_console_commands():
    forbidden = {
        "/status", "/queue", "/menu", "/task", "/result", "/approvals",
        "/stop", "/screen", "/claude", "/codex", "/cloud", "/watch",
        "/evolution_status", "/model", "/jev",
    }
    assert forbidden <= PIT_BLOCKED_COMMANDS
    assert all(not command_allowed_in_pit(cmd) for cmd in forbidden)


def test_pit_role_is_participant_even_for_machine_owner():
    policy = PitParticipantPolicy(local_model_available=True, remote_model=False)
    assert policy.role == "participant"


def test_new_participant_starts_with_zero_personal_context(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    key = vault.key_for_telegram(4242)
    ctx = build_participant_context(
        query="hello",
        vault=vault,
        person_key=key,
        consent=ConsentState(memory_enabled=True),
        selected_model_is_remote=False,
    )
    assert ctx.persona_items == ()
    assert len(ctx.as_messages()) == 1
    assert "ничего не знаешь" in PIT_ASSISTANT_SYSTEM


def test_participant_context_reads_only_own_persona(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    a, b = vault.key_for_telegram(111), vault.key_for_telegram(222)
    consent = ConsentState(memory_enabled=True)
    vault.set_consent(a, consent)
    vault.set_consent(b, consent)
    vault.append_candidate(a, candidate("a-own", "likes mountain travel", confidence=0.95))
    vault.append_candidate(b, candidate("b-other", "likes casino hotels", confidence=0.95))

    ctx = build_participant_context(
        query="travel",
        vault=vault,
        person_key=a,
        consent=consent,
        selected_model_is_remote=False,
    )
    joined = "\n".join(ctx.persona_items)
    assert "mountain travel" in joined
    assert "casino hotels" not in joined


def test_remote_model_gets_no_persona_without_separate_personalization_consent(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    key = vault.key_for_telegram(333)
    consent = ConsentState(memory_enabled=True, remote_processing_enabled=True, remote_personalization_enabled=False)
    vault.set_consent(key, consent)
    vault.append_candidate(key, candidate("own", "prefers examples", confidence=0.95))
    ctx = build_participant_context(
        query="examples",
        vault=vault,
        person_key=key,
        consent=consent,
        selected_model_is_remote=True,
    )
    assert ctx.persona_items == ()


def test_local_model_uses_only_own_persona_without_per_message_approval(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    key = vault.key_for_telegram(444)
    consent = ConsentState(memory_enabled=True)
    vault.set_consent(key, consent)
    vault.append_candidate(key, candidate("own", "prefers concise answers", confidence=0.95))
    ctx = build_participant_context(
        query="answer",
        vault=vault,
        person_key=key,
        consent=consent,
        selected_model_is_remote=False,
    )
    assert "prefers concise answers" in "\n".join(ctx.persona_items)


def test_jeff_hides_current_model_identity_but_allows_generic_model_questions():
    guarded = public_guard("Какая у тебя модель?")
    assert guarded is not None
    assert guarded.kind == GuardKind.IDENTITY
    assert "Jeff" in guarded.text
    assert public_guard("Какая модель лучше для кода?") is None


def test_jeff_never_reveals_owner_private_data_or_hidden_location():
    owner = public_guard("Где живет владелец Bossman и какой у него адрес?")
    assert owner is not None and owner.kind == GuardKind.OWNER_PRIVACY

    location = public_guard("Ты знаешь где я сейчас?")
    assert location is not None and location.kind == GuardKind.LOCATION


def test_jeff_hides_internal_pit_stage_but_can_intro_public_bossman():
    internal = public_guard("Что такое Bossman 1.7 PIT?")
    assert internal is not None and internal.kind == GuardKind.INTERNAL_STAGE

    intro = public_guard("Расскажи про Bossman")
    assert intro is not None and intro.kind == GuardKind.BOSSMAN_PUBLIC
    assert "github.com/molotroka123-cell/AiMaxBossman" in intro.text


def test_participant_tool_policy_denies_location_and_device_metadata():
    policy = TelegramToolPolicy()
    for name in ("geo.current", "geolocation.read", "location.current", "device.info"):
        assert not policy.allows(name), name


def test_system_contract_is_jeff_and_politically_non_inherited():
    assert "публичное имя — Jeff" in PIT_ASSISTANT_SYSTEM
    assert "не наследуй взгляды или национальность владельца" in PIT_ASSISTANT_SYSTEM
    assert "не агитируй" in PIT_ASSISTANT_SYSTEM


def test_pit_public_renderer_never_prefixes_internal_route_metadata():
    meta = InternalRouteMeta(
        selected_model="private-model-id",
        provider="private-provider",
        reason_code="LOCAL_FIRST",
    )
    rendered = render_jeff_reply("Готовый ответ")
    assert rendered == "Готовый ответ"
    assert meta.selected_model not in rendered
    assert meta.provider not in rendered
    assert public_model_label() == "Jeff"


def test_privacy_probe_adds_local_risk_only_and_export_hides_it(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    key = vault.key_for_telegram(555)
    state = vault.consent(key)
    guard = public_guard("Какая у тебя модель?")
    assert guard is not None and guard.risk_delta == 1
    risk = RiskLedger(vault).add(key, delta=guard.risk_delta, kind=guard.kind.value)
    assert risk.score == 1
    assert (vault.person_dir(key) / "security" / "risk.json").is_file()
    exported = json.dumps(vault.export(key), ensure_ascii=False)
    assert "risk.json" not in exported
    assert '"score": 1' not in exported


def test_risk_score_is_not_part_of_model_persona_context(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    key = vault.key_for_telegram(556)
    vault.set_consent(key, ConsentState(memory_enabled=True))
    RiskLedger(vault).add(key, delta=3, kind="identity")
    ctx = build_participant_context(
        query="hello",
        vault=vault,
        person_key=key,
        consent=vault.consent(key),
        selected_model_is_remote=False,
    )
    assert all("risk" not in item.lower() and "score" not in item.lower() for item in ctx.persona_items)


def test_risk_can_only_lower_benign_discovery_threshold_with_one_question():
    candidate_low = DiscoveryCandidate(
        "format",
        "Тебе удобнее коротко или подробно?",
        relevance=0.4,
        uncertainty=0.4,
        future_utility=0.4,
        annoyance_cost=0.12,
        sensitivity_risk=0.0,
    )
    assert choose_discovery_question(
        [candidate_low], enabled=True, mode=DiscoveryMode.COLLECTION_FIRST, risk_score=0
    ) is None
    selected = choose_discovery_question(
        [candidate_low], enabled=True, mode=DiscoveryMode.COLLECTION_FIRST, risk_score=3
    )
    assert selected is candidate_low


def test_pit_models_have_no_direct_persona_or_filesystem_tools():
    policy = TelegramToolPolicy()
    for name in (
        "persona.read_own", "persona.write_own", "files.read_own",
        "filesystem.read", "device.info", "geolocation.read",
    ):
        assert not policy.allows(name), name
    assert policy.allows("vision.analyze_own")
    assert policy.allows("file.analyze_upload")


def test_free_only_router_rejects_unknown_or_nonzero_remote_price():
    request = RouteRequest(intent="chat")
    unknown_price = ModelEndpoint(
        id="remote-unknown",
        provider="cloud",
        capabilities=frozenset({"chat"}),
        zero_cost=False,
        paid=False,
        predicted_quality=1.0,
    )
    with pytest.raises(NoEligibleRoute):
        choose_route(request, [unknown_price])


def test_laptop_image_generation_is_honest_until_ai_max():
    assert image_generation_reply(ai_max_image_generation_ready=False) == LAPTOP_IMAGE_GENERATION_REPLY_RU
    assert image_generation_reply(ai_max_image_generation_ready=True) is None
