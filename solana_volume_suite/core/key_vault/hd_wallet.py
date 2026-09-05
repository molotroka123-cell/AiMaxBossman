import hmac
import hashlib
import struct
import secrets
from typing import List, Tuple
from solders.keypair import Keypair

# Standard BIP-39 English wordlist sample (first 128 words for standalone generation if offline)
# Full BIP-39 algorithm supports any standard 12 or 24 word mnemonic.
BIP39_SAMPLE_WORDS = [
    "abandon", "ability", "able", "about", "above", "absent", "absorb", "abstract",
    "absurd", "abuse", "access", "accident", "account", "accuse", "achieve", "acid",
    "acoustic", "acquire", "across", "act", "action", "actor", "actress", "actual",
    "adapt", "add", "addict", "address", "adjust", "admit", "adult", "advance",
    "advice", "aerobic", "affair", "afford", "afraid", "again", "age", "agent",
    "agree", "ahead", "aim", "air", "airport", "aisle", "alarm", "album",
    "alcohol", "alert", "alien", "all", "alley", "allow", "almost", "alone",
    "alpha", "already", "also", "alter", "always", "amateur", "amazing", "among",
    "amount", "amused", "analyst", "anchor", "ancient", "anger", "angle", "angry",
    "animal", "ankle", "announce", "annual", "another", "answer", "antenna", "antique",
    "anxiety", "any", "apart", "apology", "appear", "apple", "approve", "april",
    "arch", "arctic", "area", "arena", "argue", "arm", "armed", "armor",
    "army", "around", "arrange", "arrest", "arrive", "arrow", "art", "artefact",
    "artist", "artwork", "ask", "aspect", "assault", "asset", "assist", "assume",
    "asthma", "athlete", "atom", "attack", "attend", "attitude", "attract", "auction",
    "audit", "august", "aunt", "author", "auto", "autumn", "average", "avocado"
]


class SolanaHDWallet:
    """
    BIP-44 / SLIP-0010 Hierarchical Deterministic (HD) Wallet Derivation for Solana.
    Derivation Path: m/44'/501'/0'/0'/i' (hardened ed25519)
    """

    @staticmethod
    def generate_mnemonic(word_count: int = 12) -> str:
        """Generates a random 12-word mnemonic phrase."""
        selected = [secrets.choice(BIP39_SAMPLE_WORDS) for _ in range(word_count)]
        return " ".join(selected)

    @staticmethod
    def mnemonic_to_seed(mnemonic: str, passphrase: str = "") -> bytes:  # ci-secret-scan: allow -- type annotation only; no credential value
        """
        Derives a 64-byte binary seed from mnemonic using PBKDF2-HMAC-SHA512 (2048 iterations).
        Standard BIP-39 specification.
        """
        salt = ("mnemonic" + passphrase).encode("utf-8")
        return hashlib.pbkdf2_hmac(
            hash_name="sha512",
            password=mnemonic.encode("utf-8"),
            salt=salt,
            iterations=2048
        )

    @staticmethod
    def hash_mnemonic(mnemonic: str) -> str:  # ci-secret-scan: allow -- type annotation only; no credential value
        """Returns SHA-256 fingerprint of the mnemonic for metadata verification without storing raw words."""
        return hashlib.sha256(mnemonic.strip().encode("utf-8")).hexdigest()

    @classmethod
    def derive_solana_keypair(
        cls,
        mnemonic: str,  # ci-secret-scan: allow -- type annotation only; no credential value
        wallet_index: int,
        passphrase: str = ""
    ) -> Keypair:
        """
        SLIP-0010 Ed25519 Key Derivation:
        Path: m/44'/501'/0'/0'/wallet_index'
        """
        seed = cls.mnemonic_to_seed(mnemonic, passphrase)

        # Master key generation
        h = hmac.new(b"ed25519 seed", seed, hashlib.sha512).digest()
        key, chain_code = h[:32], h[32:]

        # Standard Solana BIP-44 path elements: 44' -> 501' -> 0' -> 0' -> index'
        path_elements = [44, 501, 0, 0, wallet_index]

        for elem in path_elements:
            hardened_index = elem | 0x80000000
            data = b"\x00" + key + struct.pack(">I", hardened_index)
            h = hmac.new(chain_code, data, hashlib.sha512).digest()
            key, chain_code = h[:32], h[32:]

        # Key is 32-byte ed25519 seed
        return Keypair.from_seed(key)
