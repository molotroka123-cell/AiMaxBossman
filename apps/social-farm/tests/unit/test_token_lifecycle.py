"""Жизненный цикл токена: состояния, обновление, отзыв, дрейф разрешений.

Главное, что здесь проверяется, — что отзыв и истечение НЕ одно и то же.
Истёкший токен обновляется сам; отозванный требует человека. Свести их значит
построить бесконечный цикл попыток обновления там, где нужно письмо владельцу.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from social_farm.accounts import (AccountAuthService, AccountRecord, AccountStatus,
                                  AccountType, InMemoryAccountDirectory, TokenRecord,
                                  TokenState)
from social_farm.accounts.lifecycle import ConnectError, StateNonceError
from social_farm.security.vault import InMemoryVault, SecretRevoked

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _service() -> AccountAuthService:
    return AccountAuthService(vault=InMemoryVault(),
                              directory=InMemoryAccountDirectory())


def _connected(service: AccountAuthService, *, scopes=("pages_show_list",),
               expires_at: str | None = None, refreshable: bool = True):
    _, nonce = service.begin_connect(account_id="acct_a", provider="instagram")
    return service.complete_connect(
        account_id="acct_a", state_nonce=nonce, token_value="TOKEN-VALUE-0001",
        provider_account_id="ig_1111", handle="studio",
        account_type=AccountType.BUSINESS, scopes=scopes,
        expires_at=expires_at or (NOW + timedelta(days=60)).isoformat(),
        refreshable=refreshable)


# ------------------------------------------------------------------ состояния

def test_state_is_derived_from_dates_not_stored_separately():
    token = TokenRecord(auth_ref="ref", account_id="acct_a",
                        issued_at=NOW.isoformat(),
                        expires_at=(NOW + timedelta(days=30)).isoformat())
    assert token.state(NOW) is TokenState.ACTIVE
    assert token.state(NOW + timedelta(days=31)) is TokenState.EXPIRED


def test_refresh_window_opens_before_expiry_not_after():
    """Планировщик создаёт AUTH_REFRESH ЗАРАНЕЕ. Обновление после истечения —
    это уже восстановление после отказа, а не обслуживание."""
    token = TokenRecord(auth_ref="ref", account_id="acct_a", refreshable=True,
                        issued_at=NOW.isoformat(),
                        expires_at=(NOW + timedelta(hours=2)).isoformat())
    assert token.state(NOW) is TokenState.NEEDS_REFRESH
    plan = token.refresh_plan(NOW)
    assert plan.due is True and plan.job == "AUTH_REFRESH"


def test_missing_expiry_is_unknown_not_eternal():
    """Провайдер не сообщил срока — это не «токен навсегда»."""
    token = TokenRecord(auth_ref="ref", account_id="acct_a")
    assert token.state(NOW) is TokenState.UNKNOWN
    assert token.allows_mutation(NOW) is False
    assert token.refresh_plan(NOW).job == "REAUTH"


def test_expired_but_non_refreshable_token_asks_for_a_human():
    token = TokenRecord(auth_ref="ref", account_id="acct_a", refreshable=False,
                        issued_at=NOW.isoformat(),
                        expires_at=(NOW - timedelta(days=1)).isoformat())
    assert token.refresh_plan(NOW).job == "REAUTH"


def test_revocation_is_not_a_kind_of_expiry():
    token = TokenRecord(auth_ref="ref", account_id="acct_a", refreshable=True,
                        issued_at=NOW.isoformat(),
                        expires_at=(NOW + timedelta(days=30)).isoformat()).revoked()
    assert token.state(NOW) is TokenState.REVOKED
    # Отозванный токен не пытаются обновить: обновление не поможет.
    assert token.refresh_plan(NOW).job == "REAUTH"
    assert token.allows_provider_calls(NOW) is False


# ------------------------------------------------------------------ ошибки провайдера

def test_permission_error_does_not_touch_the_token():
    """Токен цел, а разрешения нет. «Починить» токен значит сломать рабочее
    подключение ради ошибки, которая к нему не относится."""
    token = TokenRecord(auth_ref="ref", account_id="acct_a", refreshable=True,
                        issued_at=NOW.isoformat(),
                        expires_at=(NOW + timedelta(days=30)).isoformat())
    after = token.on_auth_error("PERMISSION_MISSING")
    assert after.state(NOW) is TokenState.ACTIVE
    assert after.last_error_class == "PERMISSION_MISSING"


def test_auth_required_error_demands_a_human_even_for_refreshable_tokens():
    token = TokenRecord(auth_ref="ref", account_id="acct_a", refreshable=True,
                        issued_at=NOW.isoformat(),
                        expires_at=(NOW + timedelta(days=30)).isoformat())
    after = token.on_auth_error("AUTH_REQUIRED")
    assert after.state(NOW) is TokenState.REAUTH_REQUIRED


# ------------------------------------------------------------------ дрейф разрешений

def test_lost_scope_demotes_the_account_immediately():
    """«Demote unsupported actions immediately» (52). Между потерей разрешения
    и пересбором возможностей есть окно, и в нём действия не предлагаются."""
    service = _service()
    _connected(service, scopes=("instagram_basic", "instagram_content_publish"))
    drift = service.apply_granted_scopes(account_id="acct_a",
                                         granted=["instagram_basic"])
    assert drift.removed == ("instagram_content_publish",)
    assert drift.demotes_capabilities is True
    assert service._directory.get("acct_a").status is AccountStatus.DEGRADED


def test_gained_scope_alone_does_not_degrade_the_account():
    service = _service()
    _connected(service, scopes=("instagram_basic",))
    drift = service.apply_granted_scopes(
        account_id="acct_a", granted=["instagram_basic", "instagram_manage_comments"])
    assert drift.added == ("instagram_manage_comments",)
    assert drift.demotes_capabilities is False
    assert service._directory.get("acct_a").status is AccountStatus.CONNECTED


# ------------------------------------------------------------------ подключение

def test_state_nonce_is_single_use():
    service = _service()
    _, nonce = service.begin_connect(account_id="acct_a", provider="instagram")
    service.complete_connect(account_id="acct_a", state_nonce=nonce,
                             token_value="V1", provider_account_id="ig_1")
    with pytest.raises(StateNonceError):
        service.complete_connect(account_id="acct_a", state_nonce=nonce,
                                 token_value="V2", provider_account_id="ig_1")


def test_unknown_state_nonce_is_refused():
    service = _service()
    service.begin_connect(account_id="acct_a", provider="instagram")
    with pytest.raises(StateNonceError):
        service.complete_connect(account_id="acct_a", state_nonce="forged",
                                 token_value="V", provider_account_id="ig_1")


def test_reconnect_keeps_one_record_when_provider_identity_matches():
    service = _service()
    _connected(service)
    _, nonce = service.begin_connect(account_id="acct_a", provider="instagram")
    outcome = service.complete_connect(
        account_id="acct_a", state_nonce=nonce, token_value="TOKEN-VALUE-0002",
        provider_account_id="ig_1111", account_type=AccountType.BUSINESS)
    assert len(service._directory.all()) == 1
    assert outcome.account.status is AccountStatus.CONNECTED


def test_reconnect_with_a_different_provider_account_is_refused():
    """Иначе две живые площадки окажутся в одной записи и в одном аудите."""
    service = _service()
    _connected(service)
    _, nonce = service.begin_connect(account_id="acct_a", provider="instagram")
    with pytest.raises(ConnectError):
        service.complete_connect(account_id="acct_a", state_nonce=nonce,
                                 token_value="V", provider_account_id="ig_9999")


def test_refresh_keeps_the_same_reference_and_changes_the_fingerprint():
    """Новая ссылка на каждое обновление разорвала бы историю аккаунта."""
    service = _service()
    first = _connected(service)
    before = service._directory.get("acct_a").token.token_fingerprint
    after = service.refresh(account_id="acct_a", token_value="TOKEN-VALUE-0002",
                            expires_at=(NOW + timedelta(days=60)).isoformat())
    assert after.auth_ref == first.auth_ref
    assert after.account.token.token_fingerprint != before


def test_revocation_stops_mutations_and_blocks_secret_resolution():
    service = _service()
    _connected(service)
    outcome = service.revoke("acct_a", reason="владелец отключил приложение")

    assert outcome.account.status is AccountStatus.AUTH_EXPIRED
    assert outcome.account.token_state() is TokenState.REVOKED
    assert outcome.account.allows_mutation() is False
    assert outcome.capabilities_stale is True
    with pytest.raises(ConnectError):
        service.resolve_for_adapter("acct_a")


def test_secret_is_gone_from_the_vault_after_revocation():
    vault = InMemoryVault()
    service = AccountAuthService(vault=vault, directory=InMemoryAccountDirectory())
    outcome = _connected(service)
    service.revoke("acct_a")
    with pytest.raises(SecretRevoked):
        vault.resolve_for_adapter(outcome.auth_ref, owner_account_id="acct_a")


def test_account_status_and_token_must_agree_before_a_mutation():
    """Статус CONNECTED при отозванном токене — рассинхронизация, и трактовать
    её в пользу действия нельзя."""
    token = TokenRecord(auth_ref="ref", account_id="acct_a").revoked()
    record = AccountRecord(id="acct_a", provider="instagram",
                           status=AccountStatus.CONNECTED, token=token)
    assert record.allows_mutation() is False
    assert "отозван" in record.why_not_mutating()


def test_health_reports_why_the_account_cannot_act():
    service = _service()
    _connected(service)
    service.revoke("acct_a")
    health = service.health("acct_a")
    assert health["allows_mutation"] is False
    assert health["token_state"] == "REVOKED"
    assert health["secret"]["usable"] is False
