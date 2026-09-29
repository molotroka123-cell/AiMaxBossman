"""RC19 owner run: Edge offered «Сохранить пароль?» for the Bossman access token.

Chromium ignores autocomplete=off on password inputs, so the token went to the
browser's password manager prompt. The field is now a masked text input that
password managers do not treat as a credential form.
"""
import re
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "ui" / "index.html"


def test_token_field_is_masked_but_not_a_password_input():
    html = INDEX.read_text(encoding="utf-8")
    tag = re.search(r'<input id="login-token"[^>]*>', html).group(0)
    assert 'type="password"' not in tag
    assert 'type="text"' in tag and "-webkit-text-security: disc" in tag
    assert 'autocomplete="one-time-code"' in tag
