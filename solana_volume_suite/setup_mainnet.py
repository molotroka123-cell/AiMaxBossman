"""Retired mainnet setup: this build accepts mock configuration only."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from solana_volume_suite.core.security import audit


def run_setup_wizard(*args, **kwargs):
    audit("SECURITY_VIOLATION", reason="MAINNET_SETUP_BLOCKED")
    raise PermissionError("VIRTUAL_ONLY: mainnet setup disabled; no configuration was written")


def test_rpc_connection(*args, **kwargs):
    return run_setup_wizard()


def test_jito_connection(*args, **kwargs):
    return run_setup_wizard()


if __name__ == "__main__":
    run_setup_wizard()
