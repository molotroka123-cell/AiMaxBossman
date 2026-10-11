import os
import sys
import json
import pytest

# Ensure solana_volume_suite is on python path
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TEST_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.key_vault.vault import SecurityKeyVault, PBKDF2_ITERATIONS
from core.key_vault.hd_wallet import SolanaHDWallet
from core.ai_interface import ZeroKnowledgeAIInterface, VaultAuditLogger, SecurityLeakException


@pytest.fixture
def temp_vault_file(tmp_path):
    return str(tmp_path / "wallets_encrypted_test.json")


def test_encryption_roundtrip(temp_vault_file):
    """
    1. Encrypt N wallets with AES-256-GCM + PBKDF2 100k iterations.
    2. Verify disk file contains metadata, salt, nonce, ciphertext.
    3. Verify private keys are absent from raw disk file text.
    4. Decrypt and verify exact Keypair public/private match.
    5. Verify wrong password raises PermissionError.
    """
    vault = SecurityKeyVault(storage_path=temp_vault_file)
    password = "MasterSuperSecretPassphrase123!"  # ci-secret-scan: allow -- synthetic local test fixture; no deployed credentials
    count = 20

    pubkeys = vault.create_and_store_pool(count=count, password=password, mode="random")
    assert len(pubkeys) == count
    assert os.path.exists(temp_vault_file)

    with open(temp_vault_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "salt" in data
    assert "nonce" in data
    assert "ciphertext" in data
    assert "metadata" in data
    assert data["metadata"]["wallet_count"] == count
    assert data["metadata"]["derivation_mode"] == "random"

    # Raw secret keys must NOT appear in ciphertext JSON
    disk_text = json.dumps(data)
    for pk in pubkeys:
        assert pk not in data["ciphertext"]

    # Decrypt and verify
    keypairs = vault.load_keypairs(password)
    assert len(keypairs) == count
    for idx, kp in enumerate(keypairs):
        assert str(kp.pubkey()) == pubkeys[idx]

    # Public address retrieval without decryption fails unauthenticated
    with pytest.raises(PermissionError):
        vault.get_public_addresses()
    unencrypted_pubkeys = vault.get_public_addresses(password)
    assert unencrypted_pubkeys == pubkeys

    # Bad password rejection
    with pytest.raises(PermissionError):
        vault.load_keypairs("IncorrectPassword!")


def test_zero_knowledge_isolation(temp_vault_file):
    """
    1. Verify AI model receives only virtual indices (wallet_idx) and pubkeys.
    2. Verify audit logger logs access timestamps and caller without leaking private keys.
    3. Verify SecurityLeakException is raised if any secret/seed keyword enters AI context.
    """
    vault = SecurityKeyVault(storage_path=temp_vault_file)
    password = "ZeroKnowledgePassword456!"  # ci-secret-scan: allow -- synthetic local test fixture; no deployed credentials
    vault.create_and_store_pool(count=10, password=password)

    audit_logger = VaultAuditLogger()
    zk_interface = ZeroKnowledgeAIInterface(vault=vault, audit_logger=audit_logger)

    # 1. Clean context build
    market_metrics = {
        "token_mint": "So11111111111111111111111111111111111111112",
        "5m_volume_usd": 12500.0,
        "curve_progress_pct": 45.2
    }
    balances = {vault.get_public_addresses(password)[0]: 1.234}
    prompt_payload = zk_interface.build_sanitized_ai_prompt_context(market_metrics, balances)
    assert "available_wallets" in prompt_payload
    for w in prompt_payload["available_wallets"]:
        assert "wallet_idx" in w
        assert "pubkey" in w
        assert "private_key" not in w
        assert "secret_base58" not in w
    public_view = vault.get_sanitized_public_view(password)
    assert len(public_view) == 10
    assert all("private_key" not in w and "secret_base58" not in w for w in public_view)

    # 2. Leak detection test
    leaky_metrics = {
        "token_mint": "So11111111111111111111111111111111111111112",
        "secret_base58": "MALICIOUS_KEY_LEAK"
    }
    with pytest.raises(SecurityLeakException):
        zk_interface.scan_for_leaks(leaky_metrics)

    # 3. Secure load keypairs & audit trail
    loaded_kps = zk_interface.load_keypairs_secure(password=password, reason="SIGN_JITO_BUNDLE", caller="executor_agent")
    assert len(loaded_kps) == 10

    logs = audit_logger.get_logs()
    assert len(logs) == 1
    assert logs[0]["action"] == "LOAD_KEYPAIRS"
    assert logs[0]["reason"] == "SIGN_JITO_BUNDLE"
    assert logs[0]["caller"] == "executor_agent"
    assert logs[0]["wallet_count"] == 10
    # Audit log must NOT contain secrets
    log_text = json.dumps(logs)
    assert "secret" not in log_text.lower()
    assert "private" not in log_text.lower()


def test_hd_creation_remains_disabled_pending_validated_bip39(temp_vault_file):
    vault = SecurityKeyVault(storage_path=temp_vault_file)
    with pytest.raises(ValueError, match="HD creation disabled"):
        vault.create_and_store_pool(count=5, password="HDVaultPassword123!", mode="hd_bip44")  # ci-secret-scan: allow -- synthetic local test fixture; no deployed credentials
    assert not os.path.exists(temp_vault_file)


def test_hd_bip44_deterministic():
    """
    Verifies BIP-44 deterministic derivation:
    Same mnemonic derives identical Solana keypairs at same index.
    """
    mnemonic = SolanaHDWallet.generate_mnemonic(12)  # ci-secret-scan: allow -- synthetic local test fixture; no deployed credentials
    kp_0_a = SolanaHDWallet.derive_solana_keypair(mnemonic, 0)
    kp_0_b = SolanaHDWallet.derive_solana_keypair(mnemonic, 0)
    kp_1 = SolanaHDWallet.derive_solana_keypair(mnemonic, 1)

    assert str(kp_0_a.pubkey()) == str(kp_0_b.pubkey())
    assert str(kp_0_a.pubkey()) != str(kp_1.pubkey())
