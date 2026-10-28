"""AppActionPassport: a passport CONTRACT for child apps. NOT WIRED (audit 28.10): no app and no backend module imports it, only
tests/test_mobile_dag_devices_upgrade.py does; the receipt is a plain SHA-256 over the fields (an HMAC only when a key is passed).
It becomes a «unified contract for all child apps» only when each app creates and checks a passport on its real actions.

Intended contract for all Bossman child applications.

Standardized passport contract for child apps:
- War Terminal
- Social Farm
- Accountant
- Shorts Factory
- BossBlocks
- Poker Vision

Every action dispatched to or from child apps carries an AppActionPassport with:
- task_id (bound to Task Kernel)
- app_name
- action_type
- payload (sanitized, zero T3 secrets)
- stop_check callback / flag
- sha256_receipt (verifiable cryptographic action receipt)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

SUPPORTED_APPS = frozenset({
    "war_terminal",
    "social_farm",
    "accountant",
    "shorts_factory",
    "bossblocks",
    "poker_vision",
})

CANONICAL_APP_NAMES = {
    "war terminal": "war_terminal",
    "war_terminal": "war_terminal",
    "social farm": "social_farm",
    "social_farm": "social_farm",
    "accountant": "accountant",
    "shorts factory": "shorts_factory",
    "shorts_factory": "shorts_factory",
    "bossblocks": "bossblocks",
    "boss blocks": "bossblocks",
    "poker vision": "poker_vision",
    "poker_vision": "poker_vision",
}

_T3_SUSPICIOUS_KEYS = frozenset({
    "private_key",
    "seed_phrase",
    "mnemonic",
    "master_password",
    "t3_secret",
    "bank_token",
    "root_cert_key",
})


class PassportValidationError(ValueError):
    """Raised when an AppActionPassport fails contract validation."""


class T3SecretLeakError(SecurityError if "SecurityError" in globals() else ValueError):
    """Raised when T3 secret material is detected in an action passport."""


def normalize_app_name(app_name: str) -> str:
    cleaned = str(app_name).strip().lower().replace("-", "_")
    if cleaned in CANONICAL_APP_NAMES:
        return CANONICAL_APP_NAMES[cleaned]
    if cleaned in SUPPORTED_APPS:
        return cleaned
    raise PassportValidationError(
        f"Unsupported child application: {app_name!r}. Must be one of {sorted(SUPPORTED_APPS)}"
    )


def compute_sha256_receipt(
    *,
    task_id: str,
    app_name: str,
    action_type: str,
    payload_hash: str,
    timestamp: float,
    secret_key: str | bytes | None = None,
) -> str:
    """Computes a deterministic SHA-256 or HMAC-SHA256 receipt for an action passport."""
    raw = f"{task_id}|{app_name}|{action_type}|{payload_hash}|{timestamp:.6f}".encode("utf-8")
    if secret_key:
        key = secret_key.encode("utf-8") if isinstance(secret_key, str) else secret_key
        return hmac.new(key, raw, hashlib.sha256).hexdigest()
    return hashlib.sha256(raw).hexdigest()


def check_for_t3_secrets(obj: Any) -> None:
    """Fail-closed inspection to prevent leaking T3 secrets into child app payloads."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            k_lc = str(k).lower()
            if any(t3 in k_lc for t3 in _T3_SUSPICIOUS_KEYS):
                raise T3SecretLeakError(f"T3 secret key forbidden in AppActionPassport: {k!r}")
            check_for_t3_secrets(v)
    elif isinstance(obj, list):
        for item in obj:
            check_for_t3_secrets(item)


@dataclass
class AppActionPassport:
    """Unified action passport contract for child apps."""

    task_id: str
    app_name: str
    action_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    stop_check: Callable[[], bool] | bool = False
    timestamp: float = field(default_factory=time.time)
    sha256_receipt: str = ""
    status: str = "INITIALIZED"

    def __post_init__(self):
        if not self.task_id or not isinstance(self.task_id, str):
            raise PassportValidationError("task_id must be a non-empty string")
        self.app_name = normalize_app_name(self.app_name)
        if not self.action_type or not isinstance(self.action_type, str):
            raise PassportValidationError("action_type must be a non-empty string")
        if not isinstance(self.payload, dict):
            raise PassportValidationError("payload must be a dict")
        check_for_t3_secrets(self.payload)
        if not self.sha256_receipt:
            self.sha256_receipt = self.compute_receipt()

    @property
    def payload_hash(self) -> str:
        serialized = json.dumps(self.payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def compute_receipt(self, secret_key: str | bytes | None = None) -> str:
        return compute_sha256_receipt(
            task_id=self.task_id,
            app_name=self.app_name,
            action_type=self.action_type,
            payload_hash=self.payload_hash,
            timestamp=self.timestamp,
            secret_key=secret_key,
        )

    def verify_receipt(self, secret_key: str | bytes | None = None) -> bool:
        expected = self.compute_receipt(secret_key=secret_key)
        return hmac.compare_digest(self.sha256_receipt, expected)

    def is_stopped(self) -> bool:
        """Evaluates stop condition. Returns True if STOP has been signaled."""
        if callable(self.stop_check):
            try:
                return bool(self.stop_check())
            except Exception:
                return True
        return bool(self.stop_check)

    def assert_not_stopped(self) -> None:
        if self.is_stopped():
            self.status = "STOPPED"
            raise RuntimeError(f"Action passport {self.task_id} halted: STOP triggered")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "app_name": self.app_name,
            "action_type": self.action_type,
            "payload": self.payload,
            "payload_hash": self.payload_hash,
            "timestamp": self.timestamp,
            "sha256_receipt": self.sha256_receipt,
            "status": self.status,
            "is_stopped": self.is_stopped(),
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        stop_check: Callable[[], bool] | bool = False,
    ) -> "AppActionPassport":
        if not isinstance(data, dict):
            raise PassportValidationError("data must be a dictionary")
        return cls(
            task_id=str(data.get("task_id") or ""),
            app_name=str(data.get("app_name") or ""),
            action_type=str(data.get("action_type") or ""),
            payload=dict(data.get("payload") or {}),
            stop_check=stop_check,
            timestamp=float(data.get("timestamp") or time.time()),
            sha256_receipt=str(data.get("sha256_receipt") or ""),
            status=str(data.get("status") or "INITIALIZED"),
        )
