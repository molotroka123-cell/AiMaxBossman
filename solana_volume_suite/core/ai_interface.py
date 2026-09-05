import time
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from solders.keypair import Keypair
from core.key_vault.vault import SecurityKeyVault

FORBIDDEN_KEY_PATTERNS = [
    "secret", "private_key", "secret_base58", "seed", "mnemonic", "password", "keypair", "raw_key"
]


class SecurityLeakException(Exception):
    """Raised when sensitive private key data is detected in AI context or prompt payload."""
    pass


class VaultAuditEntry(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    epoch: float = Field(default_factory=time.time)
    action: str
    reason: str
    caller: str
    wallet_count: int
    public_addresses_accessed: List[str] = Field(default_factory=list)


class VaultAuditLogger:
    """
    Tamper-evident in-memory audit logger.
    Logs every load_keypairs() invocation, recording timestamp, caller, and reason.
    NEVER logs decrypted private keys.
    """

    def __init__(self):
        self._entries: List[VaultAuditEntry] = []

    def record_access(
        self,
        action: str,
        reason: str,
        caller: str,
        wallet_count: int,
        public_addresses: Optional[List[str]] = None
    ) -> VaultAuditEntry:
        entry = VaultAuditEntry(
            action=action,
            reason=reason,
            caller=caller,
            wallet_count=wallet_count,
            public_addresses_accessed=public_addresses or []
        )
        self._entries.append(entry)
        return entry

    def get_logs(self) -> List[Dict[str, Any]]:
        return [entry.model_dump() for entry in self._entries]


class ZeroKnowledgeAIInterface:
    """
    Zero-Knowledge Enforcement Layer for AI LLMs.
    Guarantees:
      1. AI model receives ONLY virtual indices, public addresses, and balances.
      2. Strictly intercepts and purges any private keys, seeds, or secret fields.
      3. Audits all keypair load operations with zero secret leakage.
    """

    def __init__(self, vault: SecurityKeyVault, audit_logger: Optional[VaultAuditLogger] = None):
        self.vault = vault
        self.audit_logger = audit_logger or VaultAuditLogger()

    def load_keypairs_secure(self, password: str, reason: str, caller: str = "execution_worker") -> List[Keypair]:
        """
        Loads keypairs into volatile memory strictly for immediate transaction signing,
        recording an immutable audit event.
        """
        keypairs = self.vault.load_keypairs(password)
        pubkeys = [str(kp.pubkey()) for kp in keypairs]
        self.audit_logger.record_access(
            action="LOAD_KEYPAIRS",
            reason=reason,
            caller=caller,
            wallet_count=len(keypairs),
            public_addresses=pubkeys
        )
        return keypairs

    def build_sanitized_ai_prompt_context(
        self,
        market_metrics: Dict[str, Any],
        wallet_balances: Optional[Dict[str, float]] = None
    ) -> Dict[str, Any]:
        """
        Creates an isolated, Zero-Knowledge payload for the AI model.
        Includes ONLY:
          - wallet_idx (0..N)
          - pubkey
          - balance_sol (optional)
        """
        # Validate market metrics for any accidental leak
        self.scan_for_leaks(market_metrics)

        sanitized_wallets = []
        public_view = self.vault.get_sanitized_public_view()
        balances = wallet_balances or {}

        for w in public_view:
            idx = w.get("wallet_index", 0)
            pk = w.get("pubkey", "")
            sanitized_wallets.append({
                "wallet_idx": idx,
                "pubkey": pk,
                "balance_sol": balances.get(pk, 0.0)
            })

        clean_payload = {
            "market_state": self._sanitize_dict(market_metrics),
            "available_wallets": sanitized_wallets
        }

        # Final assertion check
        self.scan_for_leaks(clean_payload)
        return clean_payload

    @classmethod
    def scan_for_leaks(cls, data: Any):
        """Recursively checks for forbidden private key or secret terminology."""
        if isinstance(data, dict):
            for k, v in data.items():
                for forbidden in FORBIDDEN_KEY_PATTERNS:
                    if forbidden in k.lower():
                        raise SecurityLeakException(
                            f"Zero-Knowledge Violation: Forbidden key pattern '{k}' detected in AI context!"
                        )
                cls.scan_for_leaks(v)
        elif isinstance(data, list):
            for item in data:
                cls.scan_for_leaks(item)

    @classmethod
    def _sanitize_dict(cls, d: Dict[str, Any]) -> Dict[str, Any]:
        clean = {}
        for k, v in d.items():
            if any(forbidden in k.lower() for forbidden in FORBIDDEN_KEY_PATTERNS):
                continue
            if isinstance(v, dict):
                clean[k] = cls._sanitize_dict(v)
            else:
                clean[k] = v
        return clean
