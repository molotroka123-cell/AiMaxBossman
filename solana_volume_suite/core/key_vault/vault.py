import os
import json
import base64
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import transfer, TransferParams
from solders.instruction import Instruction
from spl.token.constants import TOKEN_PROGRAM_ID
import spl.token.instructions as spl_ix
import base58

from core.key_vault.hd_wallet import SolanaHDWallet

DEFAULT_VAULT_PATH = "wallets_encrypted.json"
PBKDF2_ITERATIONS = 100_000


class RentReclaimer:
    """
    Reclaims frozen rent (~0.002039 SOL per ATA) when token balance reaches zero.
    Builds close_account SPL instruction to refund lamports back to wallet.
    """

    @staticmethod
    def build_close_account_ix(
        token_account: Pubkey,
        destination_sol_wallet: Pubkey,
        owner: Pubkey,
        program_id: Pubkey = TOKEN_PROGRAM_ID
    ) -> Instruction:
        return spl_ix.close_account(
            spl_ix.models.CloseAccountParams(
                program_id=program_id,
                account=token_account,
                dest=destination_sol_wallet,
                owner=owner,
                signers=[]
            )
        )


class SecurityKeyVault:
    """
    Zero-Knowledge Prompting Vault with AES-256-GCM authenticated encryption.
    Keys are PBKDF2HMAC (SHA256, 100k iterations) encrypted on disk.
    Raw secret keys are NEVER exposed to AI LLM context.
    Supports both random Keypairs and deterministic BIP-44 HD derivation.
    """

    def __init__(self, storage_path: str = DEFAULT_VAULT_PATH):
        self.storage_path = storage_path

    @staticmethod
    def _derive_key(password: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=PBKDF2_ITERATIONS,
        )
        return kdf.derive(password.encode("utf-8"))

    def create_and_store_pool(
        self,
        count: int,
        password: str,
        mode: str = "random",
        mnemonic: Optional[str] = None
    ) -> List[str]:
        """
        Generates N wallets (random or hd_bip44), encrypts with AES-256-GCM,
        and saves payload to self.storage_path.
        Returns list of public addresses.
        """
        if type(count) is not int or not 1 <= count <= 1000:
            raise ValueError("Wallet count must be an integer between 1 and 1000")
        if not isinstance(password, str) or len(password) < 12:
            raise ValueError("Vault password must contain at least 12 characters")
        if mode not in {"random", "hd_bip44"}:
            raise ValueError("Unknown wallet mode")
        if mode == "hd_bip44":
            raise ValueError("HD creation disabled pending validated BIP-39 implementation")
        if os.path.exists(self.storage_path):
            raise FileExistsError("Refusing to overwrite an existing vault")

        salt = os.urandom(16)
        aes_key = self._derive_key(password, salt)
        aesgcm = AESGCM(aes_key)

        wallets_data = []
        pubkeys = []
        mnemonic_hash = None

        if mode == "hd_bip44":
            effective_mnemonic = mnemonic or SolanaHDWallet.generate_mnemonic()
            mnemonic_hash = SolanaHDWallet.hash_mnemonic(effective_mnemonic)
            for idx in range(count):
                kp = SolanaHDWallet.derive_solana_keypair(effective_mnemonic, idx)
                pk_str = str(kp.pubkey())
                pubkeys.append(pk_str)
                wallets_data.append({
                    "wallet_index": idx,
                    "pubkey": pk_str,
                    "secret_base58": base58.b58encode(bytes(kp)).decode("utf-8")
                })
        else:
            mode = "random"
            for idx in range(count):
                kp = Keypair()
                pk_str = str(kp.pubkey())
                pubkeys.append(pk_str)
                wallets_data.append({
                    "wallet_index": idx,
                    "pubkey": pk_str,
                    "secret_base58": base58.b58encode(bytes(kp)).decode("utf-8")
                })

        nonce = os.urandom(12)
        plaintext = json.dumps(wallets_data).encode("utf-8")
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        metadata = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "wallet_count": count,
            "derivation_mode": mode,
            "public_addresses": pubkeys
        }
        if mnemonic_hash:
            metadata["master_mnemonic_hash"] = mnemonic_hash

        payload = {
            "salt": base64.b64encode(salt).decode("utf-8"),
            "nonce": base64.b64encode(nonce).decode("utf-8"),
            "ciphertext": base64.b64encode(ciphertext).decode("utf-8"),
            "metadata": metadata
        }

        # Exclusive creation also protects against a concurrent creator.
        with open(self.storage_path, "x", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        return pubkeys

    def load_keypairs(self, password: str) -> List[Keypair]:
        """
        Decrypts vault in memory and returns Keypair objects for signing.
        Fails closed with PermissionError if password is incorrect.
        """
        if not os.path.exists(self.storage_path):
            raise FileNotFoundError(f"Vault storage file '{self.storage_path}' not found.")

        with open(self.storage_path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        salt = base64.b64decode(payload["salt"])
        nonce = base64.b64decode(payload["nonce"])
        ciphertext = base64.b64decode(payload["ciphertext"])

        aes_key = self._derive_key(password, salt)
        aesgcm = AESGCM(aes_key)
        try:
            decrypted = aesgcm.decrypt(nonce, ciphertext, None)
        except Exception as e:
            raise PermissionError("Decryption failed. Incorrect vault password or corrupted data.") from e

        wallets_data = json.loads(decrypted.decode("utf-8"))
        wallets_data.sort(key=lambda x: x.get("wallet_index", 0))

        keypairs = []
        for item in wallets_data:
            raw_bytes = base58.b58decode(item["secret_base58"])
            keypairs.append(Keypair.from_bytes(raw_bytes))
        return keypairs

    def get_public_addresses(self, password: Optional[str] = None) -> List[str]:
        """
        Derives public addresses from the authenticated encrypted payload.
        Existing vaults require a password: their plaintext metadata is untrusted.
        Decryption occurs in memory; private keys are excluded from the result.
        """
        if not os.path.exists(self.storage_path):
            return []

        if not password:
            raise PermissionError("Password required to authenticate vault public addresses")
        return [str(kp.pubkey()) for kp in self.load_keypairs(password)]

    def get_sanitized_public_view(self, password: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Returns indices, aliases, addresses and roles after password authentication.
        Private keys are excluded from the result. Unauthenticated metadata is
        never used as a source of public addresses.
        """
        if not os.path.exists(self.storage_path):
            return []

        pubkeys = self.get_public_addresses(password)
        return [
            {
                "wallet_index": idx,
                "alias": f"wallet_{idx}",
                "pubkey": pk,
                "role": "market_maker" if idx % 2 == 0 else "momentum_trader"
            }
            for idx, pk in enumerate(pubkeys)
        ]

    def build_sweep_all_instructions(
        self,
        cold_destination: Pubkey,
        wallet_balances: Dict[str, int],
        password: str,
        reserve_tx_fee_lamports: int = 5000
    ) -> List[Dict[str, Any]]:
        keypairs = self.load_keypairs(password)
        sweep_plans = []

        for idx, kp in enumerate(keypairs):
            pk_str = str(kp.pubkey())
            bal = wallet_balances.get(pk_str, 0)
            transferable = bal - reserve_tx_fee_lamports
            if transferable > 0:
                ix = transfer(
                    TransferParams(
                        from_pubkey=kp.pubkey(),
                        to_pubkey=cold_destination,
                        lamports=transferable
                    )
                )
                sweep_plans.append({
                    "wallet_index": idx,
                    "from_pubkey": pk_str,
                    "to_pubkey": str(cold_destination),
                    "lamports": transferable,
                    "instruction": ix,
                    "keypair": kp
                })

        return sweep_plans
