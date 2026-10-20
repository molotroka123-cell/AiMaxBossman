"""bcc.pit.secret_filter: what Jeff redacts before text reaches memory, logs or a model (and what it does not)."""
from __future__ import annotations

import pytest

from bcc.pit.secret_filter import contains_secret_hint, redact_secrets

# Shapes only, assembled at run time so this file itself never contains a secret-looking literal (tools/ci_secret_scan.py).
_FILL = "Zk" * 12
SECRETS = {
    "openai-style": "key is " + "sk" + "-" + _FILL,
    "openrouter-style": "sk" + "-or-v1-" + _FILL,
    "telegram-bot": "bot " + "123456789" + ":" + "Qa" * 17,
    "private-key": "-----BEGIN " + "RSA PRIVATE" + " KEY-----\nMIIE",
    "password-en": "pass" + "word: hunter2",
    "password-ru": "Пароль = qwerty123",
    "seed": "seed" + " phrase: abandon abandon abandon",
    # provider shapes (gitleaks MIT / detect-secrets Apache-2.0 rules)
    "github-classic": "token " + "gh" + "p_" + "Gh7" * 12,
    "github-fine-grained": "github" + "_pat_" + "11ABCDEFG0" + "Gh7" * 10,
    "aws-key-id": "AK" + "IA" + "QWERTYUIOP" + "ASDFGH",
    "google-api": "AI" + "za" + "Sy" + "Gk" * 16 + "Q",
    "slack": "xo" + "xb-" + "1234567890-" + "Gk" * 8,
    "stripe": "sk" + "_live_" + "Gh7" * 10,
    "huggingface": "hf" + "_" + "Hg" * 17,
    "jwt": "ey" + "JhbGciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiIxMjM0In0." + "Sg" * 10,
}
FILLS = (_FILL, "Qa" * 17, "hunter2", "qwerty123", "Gh7" * 10, "QWERTYUIOP", "Gk" * 8, "Hg" * 17, "Sg" * 10)


@pytest.mark.parametrize("name,text", SECRETS.items())
def test_every_secret_shape_is_detected_and_removed(name, text):
    assert contains_secret_hint(text), name
    clean, changed = redact_secrets(text)
    assert changed and "[REDACTED_SECRET]" in clean, name
    assert not any(f in clean for f in FILLS), name


@pytest.mark.parametrize("text", [
    "", None, "обычная фраза про пароли без значения", "the sk- prefix alone", "ratio 12:30 at noon",
    "password is mentioned but nothing follows the colon here",
])
def test_ordinary_text_is_left_alone(text):
    assert not contains_secret_hint(text)
    clean, changed = redact_secrets(text)
    assert not changed and clean == str(text or "")


def test_redaction_keeps_the_surrounding_words_and_hits_every_secret():
    text = "login ok, pass" + "word: s3cr3t then key " + "sk" + "-" + _FILL + " done"
    clean, changed = redact_secrets(text)
    assert changed and clean.startswith("login ok, ") and clean.endswith(" done")
    assert clean.count("[REDACTED_SECRET]") == 2 and "s3cr3t" not in clean


@pytest.mark.parametrize("text", [
    "commit 05096a2d" + "4f2e0558" * 4,                 # a git SHA Jeff reports is not a secret
    "id 123e4567-e89b-12d3-a456-426614174000",          # a UUID
    "the prefixes gh" + "p_ AK" + "IA AI" + "za xo" + "xb- hf" + "_ ey" + "J alone",
    "gh" + "p_" + "short",
])
def test_a_shape_outside_the_patterns_is_NOT_caught(text):
    """Documented limit (negative control for the claims above): only fixed-prefix shapes of full length are filtered."""
    assert not contains_secret_hint(text)
