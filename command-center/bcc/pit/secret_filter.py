from __future__ import annotations

import re

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),  # Telegram bot-token shape
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:password|passwd|пароль|2fa|otp|seed phrase|private key)\s*[:=]\s*\S+", re.I),
)


def contains_secret_hint(text: str) -> bool:
    value = str(text or "")
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


def redact_secrets(text: str) -> tuple[str, bool]:
    value = str(text or "")
    changed = False
    for pattern in _SECRET_PATTERNS:
        if pattern.search(value):
            value = pattern.sub("[REDACTED_SECRET]", value)
            changed = True
    return value, changed
