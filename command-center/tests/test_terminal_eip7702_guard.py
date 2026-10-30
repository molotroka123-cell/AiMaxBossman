"""Binance hot-wallet drain 29.10.2026 (EIP-7702 delegation signed with a stolen key): terminal.run refuses such work as T3."""
import pytest

from bcc.features.tools_terminal import hard_deny_reason


@pytest.mark.parametrize("cmd", [
    "node sign.js --eip7702 --delegate 0xabc",
    "python make_tx.py --authorization_list auth.json",
    "cast send --auth 0x1 # EIP-7702 setCode",
    "node build_setCodeTx.js",
    "python recover.py --mnemonic words.txt",
    "npm run drainer",
    "python sweeper.py --to 0xdead",
])
def test_wallet_delegation_and_drain_commands_are_hard_denied(cmd):
    assert hard_deny_reason(cmd)


@pytest.mark.parametrize("cmd", ["python -m pytest -q", "git status", "python analyze.py orders.csv",
                                 "python report.py --authorization-header-test"])
def test_ordinary_commands_are_not_affected(cmd):
    assert not hard_deny_reason(cmd)
