"""bcc.pit.secret_filter: what Jeff redacts before text reaches memory, logs or a model (and what it does not)."""
from __future__ import annotations

import pytest

from bcc.pit.secret_filter import contains_secret_hint, redact_secrets

# Shapes only: none of these is a real credential.
SECRETS = {
    "openai-style": "key is sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2",
    "openrouter-style": "sk-or-v1-" + "a1b2c3d4e5f6a7b8c9d0e1f2a3b4",
    "telegram-bot": "bot 123456789:" + "AAFakeFakeFakeFakeFakeFakeFakeFake12",
    "private-key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE",
    "password-en": "password: hunter2",
    "password-ru": "Пароль = qwerty123",
    "seed": "seed phrase: abandon abandon abandon",
}


@pytest.mark.parametrize("name,text", SECRETS.items())
def test_every_secret_shape_is_detected_and_removed(name, text):
    assert contains_secret_hint(text), name
    clean, changed = redact_secrets(text)
    assert changed and "[REDACTED_SECRET]" in clean, name
    assert "hunter2" not in clean and "qwerty123" not in clean and "AAFake" not in clean
    assert "a1b2c3d4e5f6a7b8c9d0" not in clean and "A1b2C3d4E5f6G7h8I9j0" not in clean


@pytest.mark.parametrize("text", [
    "", None, "обычная фраза про пароли без значения", "the sk- prefix alone", "ratio 12:30 at noon",
    "password is mentioned but nothing follows the colon here",
])
def test_ordinary_text_is_left_alone(text):
    assert not contains_secret_hint(text)
    clean, changed = redact_secrets(text)
    assert not changed and clean == str(text or "")


def test_redaction_keeps_the_surrounding_words_and_hits_every_secret():
    text = "login ok, password: s3cr3t then key sk-" + "Z" * 24 + " done"
    clean, changed = redact_secrets(text)
    assert changed and clean.startswith("login ok, ") and clean.endswith(" done")
    assert clean.count("[REDACTED_SECRET]") == 2 and "s3cr3t" not in clean


def test_a_secret_shape_outside_the_patterns_is_NOT_caught():
    """Documented limit (negative control for the claim above): only these shapes are filtered, a GitHub token is not."""
    assert not contains_secret_hint("ghp_" + "a" * 36)
