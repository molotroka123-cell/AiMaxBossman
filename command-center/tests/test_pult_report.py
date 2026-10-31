"""Security audit 31.10 (1c) — pult.report secret redaction & safe HTML truncation."""
from __future__ import annotations

import re

import pytest

from bcc.features.pult_report import MAX_CHARS, contains_secret, format_telegram_html, redact


@pytest.mark.parametrize("secret", [
    "".join(("sk-", "ant-api03-", "abcdefghijklmnopqrstuvwx123456")),
    "".join(("sk-", "proj-", "abcdefghijklmnopqrstuvwx123456")),
    "".join(("sk_", "live_", "abcdefghijklmnopqrstuvwx1234")),
    "".join(("AKIA", "IOSFODNN7EXAMPLE")),
    "".join(("ASIA", "IOSFODNN7EXAMPLE")),
    "".join(("ghp_", "abcdefghijklmnopqrstuvwxyz0123456789")),
    "".join(("github_pat_", "abcdefghijklmnopqrstuvwxyz0123456789")),
    "".join(("glpat-", "abcdefghijklmnopqrstuvwxyz012345")),
    "".join(("stdl_", "abcdefghijklmnopqrstuvwxyz012345")),
    "".join(("7123456789:", "AAH_abcdefghijklmnopqrstuvwxyz012345")),
    ".".join(("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "eyJzdWIiOiIxMjM0NTY3ODkwIn0", "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U")),
    "Bearer super-secret-owner-token-987654321",
    '{"password": "my_short_secret_value"}',
    "api_key=short_key_val_123",
    "-----BEGIN OPENSSH " + "PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmU=\n-----END OPENSSH PRIVATE KEY-----",
    "0123456789abcdef" * 3,
])
def test_pult_report_redacts_all_secret_formats(secret: str):
    raw = f"Отчёт проверки: найден фрагмент {secret} в выводе."
    cleaned = redact(raw)
    assert "[скрыто]" in cleaned
    # Raw secret token value must not survive in the redacted output
    for token_part in ("abcdefghijklmnop", "AKIAIOSFODNN7EXAMPLE", "ASIAIOSFODNN7EXAMPLE",
                       "super-secret-owner-token", "my_short_secret_value", "short_key_val_123",
                       "b3BlbnNzaC1rZXktdjE", "0123456789abcdef0123456789abcdef0123456789abcdef"):
        assert token_part not in cleaned
    assert contains_secret(raw) is True


def test_format_telegram_html_long_report_never_breaks_tags():
    """Security audit 31.10 (1c): slicing `full[:MAX_CHARS]` after wrapping `<b>`, `<code>`,
    and `<blockquote>` broke Telegram HTML markup on long reports and dropped the signature."""
    findings = [
        f"Находка #{i}: `module_{i}.py` проверена, **статус OK**, sk-ant-abcdefghijklmnopqrstuvwx123456 скрыт, деталь {'X' * 140}"
        for i in range(60)
    ]
    quotes = [f"Цитата #{i}: длинный блок доказательства {'Й' * 180}" for i in range(20)]
    metrics = {f"metric_{i}": f"value_{i}_" + ("9" * 40) for i in range(20)}

    html = format_telegram_html(
        title="Полный аудит безопасности и длинный отчёт",
        summary="Проверка длинного отчёта свыше лимита Telegram 3900 символов с секретами AKIAIOSFODNN7EXAMPLE.",
        findings=findings,
        metrics=metrics,
        quotes=quotes,
        signature="Аргон",
    )

    assert len(html) <= MAX_CHARS
    assert "AKIAIOSFODNN7EXAMPLE" not in html
    assert "sk-ant-abcdefghijklmnop" not in html
    assert html.endswith("<blockquote>🤖 <b>Подпись:</b> Аргон</blockquote>")

    # Every opened HTML tag (<b>, <code>, <blockquote>) must be balanced and closed in order
    for tag in ("b", "code", "blockquote"):
        opens = len(re.findall(rf"<{tag}>", html))
        closes = len(re.findall(rf"</{tag}>", html))
        assert opens == closes, f"Unbalanced <{tag}> tags: {opens} opens vs {closes} closes in:\n{html[-400:]}"
    # No truncated tag `<...` at the end of any line
    assert not re.search(r"<[^>]*$", html)
