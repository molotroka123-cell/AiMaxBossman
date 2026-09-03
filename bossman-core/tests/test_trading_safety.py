"""Режим безопасности торгового модуля. Красные тесты на всё, что стоит денег."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from bossman.trading_learning import safety
from bossman.trading_learning.safety import (EvidenceClass, LiveExecutionForbidden,
                                             OwnerApproval, OwnerApprovalRequired,
                                             UnknownProviderPrice, assert_live_proof,
                                             assert_no_live_execution,
                                             assert_read_only_integration,
                                             execution_mode, require_owner_approval, utcnow)
from bossman.trading_learning.telemetry import ModelPrice, TokenLedger


def test_defaults_are_locked():
    assert safety.TRADING_EXECUTION == "OFF"
    assert safety.PAPER_TRADING_ONLY is True
    assert safety.OWNER_APPROVAL_REQUIRED is True
    assert safety.EXTERNAL_WRITE_ACTIONS == "DENY"


@pytest.mark.parametrize("action", sorted(safety.FORBIDDEN_ACTIONS))
def test_every_forbidden_action_is_refused(action):
    with pytest.raises(LiveExecutionForbidden):
        assert_no_live_execution(action)


def test_env_cannot_enable_live(monkeypatch):
    """Окружение не открывает live: режим остаётся OFF, попытка лишь видна."""
    monkeypatch.setenv("TRADING_EXECUTION", "ON")
    assert execution_mode() == "OFF"
    assert safety.env_requested_live() is True
    with pytest.raises(LiveExecutionForbidden):
        assert_no_live_execution("place_order")


def test_stage_outside_allowed_path_is_refused():
    with pytest.raises(LiveExecutionForbidden):
        assert_no_live_execution("analyze", stage="live_trading")
    assert_no_live_execution("analyze", stage="paper_trading")   # разрешённая стадия


def test_integration_must_be_read_only():
    with pytest.raises(LiveExecutionForbidden):
        assert_read_only_integration("binance", "write")
    assert_read_only_integration("binance", "read_only") is None


def test_model_cannot_approve_itself():
    """Одобрение, выданное моделью самой себе, — не одобрение."""
    for who in ("self", "claude-opus", "bossman-agent", "assistant", "gpt-4o"):
        with pytest.raises(OwnerApprovalRequired):
            OwnerApproval(subject="x", stage="historical_analysis",
                          granted_by=who, granted_at=utcnow())


def test_approval_must_match_subject_and_stage():
    ok = OwnerApproval(subject="/video.mp4", stage="historical_analysis",
                       granted_by="Timur", granted_at=utcnow())
    assert require_owner_approval(ok, subject="/video.mp4", stage="historical_analysis") is ok
    with pytest.raises(OwnerApprovalRequired):
        require_owner_approval(ok, subject="/other.mp4", stage="historical_analysis")
    with pytest.raises(OwnerApprovalRequired):
        require_owner_approval(ok, subject="/video.mp4", stage="paper_trading")
    with pytest.raises(OwnerApprovalRequired):
        require_owner_approval(None, subject="/video.mp4", stage="historical_analysis")


def test_live_proven_requires_real_execution_data():
    """Затравочный скриншот не даёт права на LIVE_PROVEN."""
    with pytest.raises(LiveExecutionForbidden) as exc:
        assert_live_proof({"venue": "binance", "size": 1.0})
    for field in ("entry_fills", "exit_fills", "fees_paid", "funding_paid", "realized_pnl"):
        assert field in str(exc.value)
    assert_live_proof({f: "x" for f in safety.LIVE_PROOF_FIELDS})


def test_unknown_provider_price_is_refused_not_guessed():
    ledger = TokenLedger()
    with pytest.raises(UnknownProviderPrice):
        ledger.record("extract_claims", "unknown-model", prefix_tokens=100,
                      variable_tokens=50, completion_tokens=10, require_price=True)
    ledger.set_price("known", ModelPrice(Decimal("0.000003"), Decimal("0.000015"),
                                         Decimal("0.0000003")))
    rec = ledger.record("extract_claims", "known", prefix_tokens=1000, variable_tokens=200,
                        completion_tokens=100, cached_tokens=800, require_price=True)
    assert rec.priced and rec.cost_usd > 0
    summary = ledger.summary()
    assert summary["cache_hit_ratio"] == pytest.approx(800 / 1200)
    assert summary["cost_status"] == "KNOWN"


def test_cached_tokens_without_cache_price_are_refused():
    ledger = TokenLedger()
    ledger.set_price("m", ModelPrice(Decimal("0.000001"), Decimal("0.000002")))
    with pytest.raises(UnknownProviderPrice):
        ledger.record("s", "m", prefix_tokens=10, cached_tokens=5)


def test_ledger_marks_unpriced_calls_instead_of_inventing_cost():
    ledger = TokenLedger()
    ledger.record("s", "no-price-model", prefix_tokens=10, completion_tokens=5)
    summary = ledger.summary()
    assert summary["calls_without_price"] == 1
    assert summary["cost_status"] == "PARTIAL_UNKNOWN"
    assert summary["cost_usd_known"] == "0"


def test_module_exposes_no_live_client():
    """В модуле не должно быть сетевого клиента биржи вообще."""
    import pathlib
    pkg = pathlib.Path(safety.__file__).parent
    banned = ("ccxt", "binance.client", "requests.post", "httpx.post", "aiohttp")
    for path in pkg.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{path.name} references {token}"
