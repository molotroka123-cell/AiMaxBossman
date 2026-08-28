"""Два флага повторяемости, которые нельзя сводить в один.

`retryable` — можно ли продолжить нашу работу.
`safe_to_retry_external` — безопасно ли повторить внешний эффект.

Они расходятся ровно там, где ошибка стоит дороже всего.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from social_farm.domain.errors import ALIASES, ErrorClass, ProviderError

SPEC = Path(__file__).resolve().parents[3].parent / "_staging" / "social_farm"


def test_unknown_external_state_is_retryable_but_not_externally():
    """Работу продолжить надо, публикацию повторить нельзя. Один флаг здесь
    означал бы либо потерянную работу, либо вторую публикацию."""
    err = ProviderError.of(ErrorClass.UNKNOWN_EXTERNAL_STATE)
    assert err.retryable is True
    assert err.safe_to_retry_external is False


def test_timeout_is_not_treated_as_delivery_failure():
    """Ответа нет — значит неизвестно, что случилось, а не «не дошло»."""
    assert ProviderError.of(ErrorClass.TIMEOUT).safe_to_retry_external is False


def test_rate_limit_is_safe_to_retry_because_nothing_happened():
    err = ProviderError.of(ErrorClass.RATE_LIMITED, retry_after_seconds=30)
    assert err.retryable is True and err.safe_to_retry_external is True
    assert err.retry_after_seconds == 30


def test_permission_and_policy_failures_are_not_retryable():
    for kind in (ErrorClass.PERMISSION_MISSING, ErrorClass.PROVIDER_POLICY_BLOCKED,
                 ErrorClass.CAPABILITY_UNAVAILABLE, ErrorClass.CONTENT_REJECTED):
        err = ProviderError.of(kind)
        assert err.retryable is False, kind
        assert err.safe_to_retry_external is False, kind


def test_codes_outside_the_closed_enum_are_mapped_not_invented():
    """Перечень классов — контракт, а не список удобных названий."""
    err = ProviderError.of("FAIL_MEDIA_MISSING")
    assert err.error_class is ErrorClass.STORAGE_ERROR
    assert err.safe_detail == "FAIL_MEDIA_MISSING"
    assert err.safe_to_retry_external is False

    for alias, (mapped, _) in ALIASES.items():
        assert ProviderError.of(alias).error_class is mapped


def test_serialised_error_matches_the_schema_shape():
    data = ProviderError.of(ErrorClass.UNKNOWN_EXTERNAL_STATE,
                            provider_request_id="req_xxx",
                            user_action="Reconcile provider state before retry.").to_dict()
    assert set(data) == {"class", "retryable", "safe_to_retry_external", "provider_code",
                         "provider_request_id", "retry_after_seconds", "user_action",
                         "safe_detail"}
    assert data["class"] == "UNKNOWN_EXTERNAL_STATE"


@pytest.mark.skipif(not (SPEC / "schemas" / "provider_error.schema.json").exists(),
                    reason="пакет спецификации не распакован рядом")
def test_our_enum_matches_the_spec_exactly():
    schema = json.loads((SPEC / "schemas" / "provider_error.schema.json").read_text(
        encoding="utf-8"))
    assert {e.value for e in ErrorClass} == set(schema["properties"]["class"]["enum"])


@pytest.mark.skipif(not (SPEC / "examples" / "provider_error_unknown_state.json").exists(),
                    reason="пакет спецификации не распакован рядом")
def test_the_specs_own_example_round_trips():
    raw = json.loads((SPEC / "examples" / "provider_error_unknown_state.json").read_text(
        encoding="utf-8"))
    err = ProviderError.of(ErrorClass(raw["class"]),
                           provider_request_id=raw["provider_request_id"],
                           user_action=raw["user_action"], safe_detail=raw["safe_detail"])
    assert err.retryable == raw["retryable"]
    assert err.safe_to_retry_external == raw["safe_to_retry_external"]
