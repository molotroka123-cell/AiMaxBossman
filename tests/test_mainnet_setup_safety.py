"""Offline regression checks of the real wizard function, without financial imports."""
import ast
import getpass
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import Mock

import pytest


@pytest.fixture
def wizard(tmp_path):
    source = Path(__file__).resolve().parents[1] / "solana_volume_suite/setup_mainnet.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_setup_wizard")
    vault = Mock()
    vault.get_public_addresses.side_effect = ValueError("synthetic unlock failure")
    scope = {
        "Optional": Optional, "os": os, "getpass": getpass,
        "SUITE_ROOT": str(tmp_path), "DEFAULT_VAULT_PATH": "existing.vault",
        "ENV_PATH": str(tmp_path / ".env"), "DEFAULT_MAINNET_RPC": "https://invalid.example",
        "DEFAULT_JITO_ENGINE": "https://invalid.example/jito",
        "test_rpc_connection": Mock(return_value=False),
        "test_jito_connection": Mock(), "SecurityKeyVault": Mock(return_value=vault),
        "Pubkey": SimpleNamespace(from_string=Mock()),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), scope)
    return scope, vault


@pytest.mark.parametrize("password", [None, "", "short", "x" * 11])
def test_invalid_password_rejected_before_network_or_vault(wizard, password):
    scope, vault = wizard
    with pytest.raises(ValueError, match="explicit vault password"):
        scope["run_setup_wizard"](password=password, non_interactive=True)
    scope["test_rpc_connection"].assert_not_called()
    scope["test_jito_connection"].assert_not_called()
    scope["SecurityKeyVault"].assert_not_called()
    vault.create_and_store_pool.assert_not_called()


def test_unlock_failure_preserves_existing_file_and_configuration(wizard):
    scope, vault = wizard
    path = Path(scope["SUITE_ROOT"]) / "existing.vault"
    original = b"harmless preexisting ciphertext fixture"
    path.write_bytes(original)
    env = Path(scope["ENV_PATH"])
    env.write_bytes(b"preexisting configuration")
    with pytest.raises(RuntimeError, match="original file preserved"):
        scope["run_setup_wizard"](password="test-only-password", non_interactive=True)  # ci-secret-scan: allow -- synthetic password for stubbed vault
    assert path.read_bytes() == original
    assert env.read_bytes() == b"preexisting configuration"
    vault.create_and_store_pool.assert_not_called()
    scope["test_jito_connection"].assert_not_called()


def test_interactive_password_uses_hidden_prompt_before_network(wizard, monkeypatch):
    scope, _ = wizard
    prompt = Mock(return_value="short")
    monkeypatch.setattr(getpass, "getpass", prompt)
    with pytest.raises(ValueError):
        scope["run_setup_wizard"]()
    prompt.assert_called_once()
    scope["test_rpc_connection"].assert_not_called()
