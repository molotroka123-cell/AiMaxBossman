"""Shared fail-closed virtual runtime policy and structured audit events."""
import json
import logging
import os
import secrets
from datetime import datetime, timezone

logger = logging.getLogger("solana_volume_suite.security")


def audit(event: str, **fields):
    logger.warning(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(),
                               "event": event, **fields}, ensure_ascii=True))


def require_virtual_mode():
    expected = {"LIVE_EXECUTION_ENABLED": "false", "PAPER_TRADING": "true",
                "GEMINI_REAL_MONEY_READY": "false"}
    for name, safe in expected.items():
        if os.getenv(name, safe).strip().lower() != safe:
            audit("SECURITY_VIOLATION", reason="VIRTUAL_ONLY", setting=name)
            raise PermissionError("VIRTUAL_ONLY: live execution is permanently disabled in this build")
    for name in ("SOLANA_RPC_URL", "SOLANA_WSS_URL", "JITO_BLOCK_ENGINE_URL"):
        value = os.getenv(name, "")
        if value and not value.startswith("mock://"):
            audit("SECURITY_VIOLATION", reason="EXTERNAL_RPC_DISABLED", setting=name)
            raise PermissionError("VIRTUAL_ONLY: external RPC configuration is forbidden")


def generate_password():
    return secrets.token_urlsafe(32)


def validate_password(password):
    if (not isinstance(password, str) or not 32 <= len(password) <= 256
            or len(set(password)) < 12
            or any(word in password.lower() for word in ("changeme", "placeholder", "supersecret", "replace_with"))):
        raise ValueError("Generate a fresh mock vault password using secrets.token_urlsafe(32)")


def valid_bearer(header):
    expected = os.getenv("DASHBOARD_API_TOKEN", "")
    try:
        validate_password(expected)
    except ValueError:
        return False
    scheme, _, token = (header or "").partition(" ")
    return scheme.lower() == "bearer" and secrets.compare_digest(token.encode(), expected.encode())
