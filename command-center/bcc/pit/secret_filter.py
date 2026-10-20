from __future__ import annotations

import re

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),  # Telegram bot-token shape
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:password|passwd|пароль|2fa|otp|seed phrase|private key)\s*[:=]\s*\S+", re.I),
    # Provider token shapes ported from the gitleaks default rules (MIT, github.com/gitleaks/gitleaks,
    # config/gitleaks.toml) and Yelp detect-secrets plugins (Apache-2.0): fixed prefixes, so ordinary
    # prose and commit SHAs are not touched.
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{22,}\b"),   # GitHub
    re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b"),               # AWS access key id
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),                                          # Google API key
    re.compile(r"\bxox[abposr]-[0-9A-Za-z-]{10,}"),                                    # Slack
    re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{24,}\b"),                       # Stripe
    re.compile(r"\bhf_[A-Za-z]{34,}\b"),                                               # Hugging Face
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),    # JWT
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
