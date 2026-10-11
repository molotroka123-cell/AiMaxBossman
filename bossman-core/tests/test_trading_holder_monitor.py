"""Holder distribution monitor: metrics, fixtures, and the read-only adapter."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from bossman.trading_learning import holder_monitor as hm
from bossman.trading_learning.holder_monitor import (Holder, HolderMonitorError,
                                                     ReadOnlyRpcAdapter, analyse, gini, hhi,
                                                     load_holders, panel_text)

FIX = Path(__file__).parent / "fixtures" / "trading_lab"
NOW = 1790000000.0


def test_gini_and_hhi_known_values():
    assert gini([1, 1, 1, 1]) == pytest.approx(0.0)
    assert gini([0, 0, 0, 100]) == pytest.approx(0.0)       # zero balances are not holders
    assert gini([1, 1, 1, 97]) == pytest.approx(0.72, abs=0.01)
    assert hhi([1, 1, 1, 1]) == pytest.approx(0.25)
    assert hhi([100]) == pytest.approx(1.0)


def test_concentrated_fixture_is_flagged_and_infra_excluded():
    rep = analyse(load_holders(FIX / "holders_concentrated.json"), now_ts=NOW)
    assert rep.excluded_infra == 1                      # the pool is not a "holder"
    assert rep.risk_level in {"HIGH", "VERY_HIGH"}
    assert rep.top_n_share[1] > 0.3
    assert rep.new_wallet_share is not None and rep.new_wallet_share > 0.5
    assert rep.gini > 0.7


def test_distributed_fixture_is_low_risk():
    rep = analyse(load_holders(FIX / "holders_distributed.json"), now_ts=NOW)
    assert rep.risk_level in {"LOW", "MODERATE"}
    assert rep.top_n_share[10] < 0.5
    assert rep.new_wallet_share is not None and rep.new_wallet_share < 0.3
    assert rep.effective_holders > 30


def test_no_age_data_is_reported_unknown_not_zero():
    rep = analyse([Holder("a", 5), Holder("b", 3)], now_ts=NOW)
    assert rep.new_wallet_share is None
    assert any("age" in n for n in rep.notes)


def test_empty_and_negative_inputs():
    assert analyse([]).risk_level == "UNKNOWN"
    with pytest.raises(HolderMonitorError):
        analyse([Holder("a", -1)])


def test_panel_text_never_suggests_hiding_or_evasion():
    for name in ("holders_concentrated.json", "holders_distributed.json"):
        text = panel_text(analyse(load_holders(FIX / name), now_ts=NOW)).lower()
        for banned in ("hide", "hiding", "obscure", "evade", "bubblemap", "disguise",
                       "split across", "sever", "untraceable", "avoid detection", "cluster detection"):
            assert banned not in text
        assert "risk" in text


def test_module_source_has_no_obfuscation_vocabulary_or_write_methods():
    src = Path(hm.__file__).read_text(encoding="utf-8").lower()
    for banned in ("bubblemap", "sendtransaction", "signtransaction", "requestairdrop",
                   "keypair", "private_key", "secret"):
        assert banned not in src.replace("no keys", "")
    assert not (hm.READ_ONLY_RPC_METHODS & {"sendTransaction", "simulateTransaction",
                                            "requestAirdrop"})


def test_rpc_adapter_off_by_default_and_makes_no_request(monkeypatch):
    calls = []
    a = ReadOnlyRpcAdapter(transport=lambda *x: calls.append(x) or b"{}")
    with pytest.raises(HolderMonitorError, match="OFF"):
        a.largest_holders("MINT")
    # enabled flag alone is not enough: env switch must also be set
    monkeypatch.delenv("BOSSMAN_HOLDER_RPC", raising=False)
    b = ReadOnlyRpcAdapter(enabled=True, transport=lambda *x: calls.append(x) or b"{}")
    with pytest.raises(HolderMonitorError, match="OFF"):
        b.largest_holders("MINT")
    assert calls == []


def test_rpc_adapter_allowlist_and_rate_limit(monkeypatch):
    monkeypatch.setenv("BOSSMAN_HOLDER_RPC", "1")
    seen = []

    def transport(endpoint, body, timeout):
        seen.append(json.loads(body)["method"])
        return json.dumps({"result": {"value": [{"address": "T1", "uiAmount": 10.0},
                                                {"address": "T2", "uiAmount": 5.0}]}}).encode()

    a = ReadOnlyRpcAdapter(enabled=True, transport=transport, min_interval_s=0.0)
    holders = a.largest_holders("MINT")
    assert [h.balance for h in holders] == [10.0, 5.0]
    assert seen == ["getTokenLargestAccounts"]
    with pytest.raises(HolderMonitorError, match="allowlist"):
        a._call("sendTransaction", ["x"])
    assert seen == ["getTokenLargestAccounts"]

    import time
    slow = ReadOnlyRpcAdapter(enabled=True, transport=transport, min_interval_s=0.2)
    t0 = time.monotonic()
    slow.largest_holders("MINT")
    slow.largest_holders("MINT")
    assert time.monotonic() - t0 >= 0.15


def test_adapter_carries_no_credentials_in_request(monkeypatch):
    monkeypatch.setenv("BOSSMAN_HOLDER_RPC", "1")
    bodies = []
    a = ReadOnlyRpcAdapter(enabled=True, min_interval_s=0.0,
                           transport=lambda e, b, t: bodies.append(b) or b'{"result":{"value":[]}}')
    a.largest_holders("MINT")
    assert not re.search(rb"(api[-_]?key|secret|private|signature)", bodies[0], re.I)
