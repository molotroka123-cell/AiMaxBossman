"""Security Hardening V1.1: egress_guard на канальном choke-point telegram.send.

Все telegram-отправки проходят через _egress_guard_text. OFF by default →
без изменений; ON → секрет/эксфильтрация заменяются заглушкой (fail-closed).
"""
from bossman.notifications.telegram_transport import _egress_guard_text


def test_off_by_default_passthrough(monkeypatch):
    monkeypatch.delenv("BOSSMAN_CYBERSEC_V1_ENABLED", raising=False)
    t = "✅ задача #3 — done"
    assert _egress_guard_text(t) == t


def test_enabled_blocks_secret(monkeypatch):
    monkeypatch.setenv("BOSSMAN_CYBERSEC_V1_ENABLED", "1")
    out = _egress_guard_text("authorization: Bearer sk-live-SUPERSECRET-1234567890")  # ci-secret-scan: allow
    assert "SUPERSECRET" not in out and "задержан" in out


def test_enabled_allows_clean(monkeypatch):
    monkeypatch.setenv("BOSSMAN_CYBERSEC_V1_ENABLED", "1")
    t = "готово: задача #9"
    assert _egress_guard_text(t) == t
