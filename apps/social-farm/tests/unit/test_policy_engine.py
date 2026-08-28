"""Политика: AUTO / ASK / DENY, и что она НЕ может.

Спека задаёт три механизма разрешения конфликтов сразу — специфичность,
приоритет и порядок — и не говорит, как они соотносятся. Здесь закреплён
выбор из `PRE_IMPLEMENTATION_AUDIT.md` §5.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from social_farm.domain.capability import CapabilitySnapshot
from social_farm.domain.policy import (NOT_AVAILABLE, Decision, PolicyError, PolicyProfile,
                                       conditions_hold)

SPEC = Path(__file__).resolve().parents[3].parent / "_staging" / "social_farm"


def _snapshot(**caps: str) -> CapabilitySnapshot:
    return CapabilitySnapshot.from_dict({
        "account_id": "acct", "provider": "instagram", "observed_at": "t",
        "capabilities": [{"name": n, "status": s} for n, s in caps.items()]})


def _profile(*rules: dict) -> PolicyProfile:
    return PolicyProfile.from_dict({"profile_id": "p", "version": 3,
                                    "rules": list(rules)})


def _eval(profile: PolicyProfile, capability: str, snapshot=None, **context):
    return profile.evaluate(capability=capability, provider="instagram",
                            account_id="acct", snapshot=snapshot, context=context)


# ------------------------------------------------------------------ возможность выше политики

def test_missing_capability_is_not_available_not_denied():
    """«Нечем» и «нельзя» — разные ответы, и владельцу нужны разные действия.

    Смешать их значит отправить человека менять политику там, где надо
    получать возможность.
    """
    profile = _profile({"capability": "media.publish.image", "decision": "AUTO"})
    result = _eval(profile, "media.publish.image",
                   _snapshot(**{"media.publish.image": "REQUIRES_APP_REVIEW"}))
    assert result.decision == NOT_AVAILABLE
    assert result.available is False
    assert "проверка приложения" in result.reason
    assert result.matched_rule_ids == []          # до правил дело не дошло


def test_auto_cannot_conjure_a_capability():
    profile = _profile({"capability": "engagement.like", "decision": "AUTO"})
    result = _eval(profile, "engagement.like",
                   _snapshot(**{"engagement.like": "NOT_SUPPORTED"}))
    assert result.decision == NOT_AVAILABLE


# ------------------------------------------------------------------ классы безопасности

def test_security_class_cannot_be_opened_by_a_rule():
    """Пароль и второй фактор выведены из автоматизации совсем.

    Правило с AUTO на них — ошибка конфигурации, и она обязана быть шумной:
    молча проигнорированное правило оставит владельца в уверенности, что
    автоматизация настроена.
    """
    for decision in ("AUTO", "ASK"):
        with pytest.raises(PolicyError, match="политикой не открывается"):
            _profile({"capability": "account.password.change", "decision": decision})
    # DENY на них допустим — это то, что и так происходит
    _profile({"capability": "account.password.change", "decision": "DENY"})


def test_defaults_follow_the_safety_class_when_no_rule_matches():
    profile = _profile()
    snap = _snapshot(**{"media.read": "SUPPORTED_OFFICIAL",
                        "media.publish.image": "SUPPORTED_OFFICIAL",
                        "messages.reply": "SUPPORTED_OFFICIAL",
                        "relationships.follow": "SUPPORTED_BROWSER"})
    assert _eval(profile, "media.read", snap).decision == "AUTO"
    assert _eval(profile, "media.publish.image", snap).decision == "ASK"
    assert _eval(profile, "messages.reply", snap).decision == "ASK"
    # массовые подписки: дефолт решает всё
    assert _eval(profile, "relationships.follow", snap).decision == "DENY"


def test_unknown_capability_gets_the_strictest_default():
    """Возможность, которую никто не классифицировал, безопасной не считается."""
    profile = _profile()
    snap = _snapshot(**{"something.new": "SUPPORTED_OFFICIAL"})
    assert _eval(profile, "something.new", snap).decision == "ASK"


# ------------------------------------------------------------------ разрешение конфликтов

def test_hard_deny_beats_a_more_specific_rule():
    profile = _profile(
        {"id": "sys_no", "scope": "SYSTEM", "capability": "media.delete",
         "decision": "DENY", "hard_deny": True},
        {"id": "acct_yes", "scope": "ACTION", "capability": "media.delete",
         "decision": "AUTO"})
    result = _eval(profile, "media.delete", _snapshot(**{"media.delete": "SUPPORTED_OFFICIAL"}))
    assert result.decision == "DENY"
    assert result.matched_rule_ids == ["sys_no"]


def test_narrower_scope_wins():
    profile = _profile(
        {"id": "sys", "scope": "SYSTEM", "capability": "media.publish.image",
         "decision": "ASK"},
        {"id": "act", "scope": "ACTION", "capability": "media.publish.image",
         "decision": "AUTO"})
    result = _eval(profile, "media.publish.image",
                   _snapshot(**{"media.publish.image": "SUPPORTED_OFFICIAL"}))
    assert result.decision == "AUTO" and result.matched_rule_ids == ["act"]


def test_conditional_rule_is_tried_before_the_unconditional_fallback():
    """Порядок из примера спецификации: условное AUTO, затем безусловное ASK.

    Если бы безусловное правило выигрывало, условное не сработало бы никогда —
    и весь пример из `examples/account_policy.yaml` был бы мёртвым.
    """
    profile = _profile(
        {"id": "auto_if_scheduled", "capability": "media.publish.image",
         "decision": "AUTO", "conditions": {"content_approved": True, "scheduled": True}},
        {"id": "ask_otherwise", "capability": "media.publish.image", "decision": "ASK"})
    snap = _snapshot(**{"media.publish.image": "SUPPORTED_OFFICIAL"})

    ok = _eval(profile, "media.publish.image", snap, content_approved=True, scheduled=True)
    assert ok.decision == "AUTO" and ok.matched_rule_ids == ["auto_if_scheduled"]

    manual = _eval(profile, "media.publish.image", snap,
                   content_approved=True, scheduled=False)
    assert manual.decision == "ASK" and manual.matched_rule_ids == ["ask_otherwise"]


def test_priority_breaks_a_tie_within_the_same_scope():
    profile = _profile(
        {"id": "low", "scope": "ACCOUNT", "capability": "comments.reply",
         "decision": "AUTO", "priority": 1},
        {"id": "high", "scope": "ACCOUNT", "capability": "comments.reply",
         "decision": "ASK", "priority": 9})
    result = _eval(profile, "comments.reply",
                   _snapshot(**{"comments.reply": "SUPPORTED_OFFICIAL"}))
    assert result.matched_rule_ids == ["high"]


def test_disabled_rule_does_not_apply():
    profile = _profile(
        {"id": "off", "capability": "media.publish.image", "decision": "AUTO",
         "enabled": False})
    result = _eval(profile, "media.publish.image",
                   _snapshot(**{"media.publish.image": "SUPPORTED_OFFICIAL"}))
    assert result.decision == "ASK" and result.matched_rule_ids == []


def test_rule_for_another_account_does_not_leak():
    profile = _profile(
        {"id": "other", "scope": "ACCOUNT", "account_id": "acct_other",
         "capability": "media.publish.image", "decision": "AUTO"})
    result = _eval(profile, "media.publish.image",
                   _snapshot(**{"media.publish.image": "SUPPORTED_OFFICIAL"}))
    assert result.decision == "ASK", "правило чужого аккаунта сработало на нашем"


# ------------------------------------------------------------------ условия

def test_missing_context_key_never_satisfies_a_condition():
    """Неизвестное не открывает действие.

    Иначе сбой сбора контекста превращался бы в разрешение — самый тихий
    способ получить автоматическую публикацию там, где её не хотели.
    """
    assert conditions_hold({"scheduled": True}, {}) is False
    assert conditions_hold({"classifier": {"confidence_gte": 0.9}}, {}) is False
    assert conditions_hold({"classifier": {"confidence_gte": 0.9}},
                           {"classifier": {}}) is False


def test_nested_operators_from_the_spec_example():
    conditions = {"classifier": {"class_in": ["faq"], "confidence_gte": 0.95},
                  "external_link": False}
    good = {"classifier": {"class": "faq", "confidence": 0.96}, "external_link": False}
    assert conditions_hold(conditions, good) is True

    for bad in ({"classifier": {"class": "complaint", "confidence": 0.99},
                 "external_link": False},
                {"classifier": {"class": "faq", "confidence": 0.5},
                 "external_link": False},
                {"classifier": {"class": "faq", "confidence": 0.99},
                 "external_link": True}):
        assert conditions_hold(conditions, bad) is False


def test_uncomparable_values_do_not_satisfy_a_condition():
    assert conditions_hold({"c": {"confidence_gte": 0.9}},
                           {"c": {"confidence": "высокая"}}) is False


def test_condition_without_an_operation_is_a_configuration_error():
    with pytest.raises(PolicyError, match="не содержит операции"):
        conditions_hold({"c": {"confidence": 0.9}}, {"c": {"confidence": 0.95}})


# ------------------------------------------------------------------ аудит решения

def test_evaluation_carries_everything_needed_to_explain_it_later():
    """Вопрос «почему система так решила» задают ровно тогда, когда что-то
    пошло не так. К этому моменту контекст уже потерян, если его не сохранить."""
    profile = _profile({"id": "r1", "capability": "comments.reply", "decision": "ASK"})
    result = _eval(profile, "comments.reply",
                   _snapshot(**{"comments.reply": "SUPPORTED_OFFICIAL"}),
                   confidence=0.81, classification="complaint")
    data = result.to_dict()
    assert data["decision"] == "ASK"
    assert data["profile_version"] == 3 and data["profile_id"] == "p"
    assert data["matched_rule_ids"] == ["r1"]
    assert data["conditions_snapshot"] == {"confidence": 0.81,
                                           "classification": "complaint"}
    assert data["capability_status"] == "SUPPORTED_OFFICIAL"
    assert data["evaluated_at"]


@pytest.mark.skipif(not (SPEC / "examples" / "account_policy.yaml").exists(),
                    reason="пакет спецификации не распакован рядом")
def test_the_specs_own_policy_example_behaves_as_written():
    """Профиль из спецификации, разобранный без сторонних библиотек."""
    text = (SPEC / "examples" / "account_policy.yaml").read_text(encoding="utf-8")
    assert "relationships.follow" in text and "DENY" in text
    # Поведение того же профиля, выраженного явно:
    profile = _profile(
        {"id": "read", "capability": "account.read", "decision": "AUTO"},
        {"id": "pub_auto", "capability": "media.publish.image", "decision": "AUTO",
         "conditions": {"content_approved": True, "scheduled": True}},
        {"id": "pub_ask", "capability": "media.publish.image", "decision": "ASK"},
        {"id": "reply_auto", "capability": "comments.reply", "decision": "AUTO",
         "conditions": {"classifier": {"class_in": ["faq"], "confidence_gte": 0.95},
                        "external_link": False}},
        {"id": "reply_ask", "capability": "comments.reply", "decision": "ASK"},
        {"id": "msg", "capability": "messages.reply", "decision": "ASK"},
        {"id": "del", "capability": "media.delete", "decision": "ASK"},
        {"id": "follow", "capability": "relationships.follow", "decision": "DENY"})
    snap = _snapshot(**{"account.read": "SUPPORTED_OFFICIAL",
                        "media.publish.image": "SUPPORTED_OFFICIAL",
                        "comments.reply": "SUPPORTED_OFFICIAL",
                        "messages.reply": "SUPPORTED_OFFICIAL",
                        "media.delete": "SUPPORTED_OFFICIAL",
                        "relationships.follow": "SUPPORTED_BROWSER"})
    assert _eval(profile, "account.read", snap).decision == "AUTO"
    assert _eval(profile, "relationships.follow", snap).decision == "DENY"
    assert _eval(profile, "messages.reply", snap).decision == "ASK"
    assert _eval(profile, "comments.reply", snap,
                 classifier={"class": "faq", "confidence": 0.97},
                 external_link=False).decision == "AUTO"
    assert _eval(profile, "comments.reply", snap,
                 classifier={"class": "faq", "confidence": 0.97},
                 external_link=True).decision == "ASK"
