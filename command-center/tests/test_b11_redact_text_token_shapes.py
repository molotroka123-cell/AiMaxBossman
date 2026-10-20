"""B11 #1 — `plugin_security.redact_text` знает те же формы токенов, что общий
список `bcc.pit.secret_filter.TOKEN_PATTERNS` (gitleaks MIT / detect-secrets
Apache-2.0), и вычищает PEM-блок целиком и пароль в userinfo URL.

Дефект: redact_text — фильтр для превью одобрений, событий, journal и ответов
плагинов — не знал Telegram bot-токен (пульт!), github_pat_, JWT, Stripe и
приватный ключ: такие значения уезжали в журнал и в ленту как есть.
"""
from __future__ import annotations

import pytest

from bcc.plugin_security import redact, redact_text

TG = "123456789:" + "AAH" + "x" * 32                         # форма bot-токена Telegram
GH_PAT = "github_pat_" + "A1b2C3d4" * 4
JWT = "eyJ" + "hbGciOiJIUzI1" + ".eyJ" + "zdWIiOiIxMjM0" + ".abcdefgh12345678"
STRIPE = "sk_live_" + "0123456789abcdefABCDEFgh"
# Собирается из частей: CI secret-scan (tools/ci_secret_scan.py) не должен видеть
# в тесте заголовок настоящего ключа; тело — заведомо фиктивное.
_PEM_KIND = "RSA " + "PRIVATE KEY"
PEM = ("-----BEGIN " + _PEM_KIND + "-----\n" + "MIIEfakebody" + "A" * 52 + "\n"
       + "-----END " + _PEM_KIND + "-----")


@pytest.mark.parametrize("secret", [TG, GH_PAT, JWT, STRIPE])
def test_token_shapes_from_shared_list_are_redacted(secret):
    out = redact_text(f"ответ сервера: {secret} конец")
    assert secret not in out
    assert "ответ сервера:" in out and "конец" in out


def test_whole_pem_block_is_redacted_not_only_header():
    out = redact_text(f"key:\n{PEM}\nok")
    assert "MIIEfakebody" not in out
    assert out.endswith("ok")


def test_url_userinfo_password_is_redacted():
    out = redact_text("clone https://bot:hunter2secret@git.example.org/repo.git failed")
    assert "hunter2secret" not in out
    assert "git.example.org/repo.git" in out


def test_redact_scrub_text_uses_same_shapes():
    out = redact({"preview": f"send {TG}"}, scrub_text=True)
    assert TG not in out["preview"]


def test_idempotent_and_prose_untouched():
    text = "commit 4453e2a6 at 12:30, see https://example.org/a:b"
    assert redact_text(text) == text
    once = redact_text(f"x {TG} {PEM}")
    assert redact_text(once) == once
