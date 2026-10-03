"""bcc.telegram_companion.owner_report: dry run by default, owner-only sendMessage, no secrets, honest exit codes."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bcc.telegram_companion import owner_report as orp

OWNER = SimpleNamespace(role="owner", chat_id=386321847, user_id=386321847)
SETTINGS = SimpleNamespace(enabled=True, bot_token="x")


class FakeTransport:
    instances: list["FakeTransport"] = []

    def __init__(self, settings, fail_on: int | None = None):
        self.settings, self.sent, self.closed, self.fail_on = settings, [], False, fail_on
        FakeTransport.instances.append(self)

    async def send(self, person, text, **kw):
        if self.fail_on is not None and len(self.sent) == self.fail_on:
            raise RuntimeError("telegram down")
        self.sent.append((person.chat_id, text))
        return 1000 + len(self.sent)

    async def close(self):
        self.closed = True


def _configured(monkeypatch):
    FakeTransport.instances = []
    monkeypatch.setattr(orp, "_load_owner", lambda config_path: (SETTINGS, OWNER))


def run(text, **kw):
    return asyncio.run(orp.send_report(text, **kw))


def test_dry_run_sends_nothing_and_masks_the_target(monkeypatch):
    _configured(monkeypatch)
    result = run("Чекпоинт 1\nвсё идёт", transport_factory=FakeTransport)
    assert result["exit"] == 0 and result["dry_run"] is True and result["sent"] is False
    assert result["chunks"] == 1 and result["target"].endswith("847") and "386321" not in result["target"]
    assert FakeTransport.instances == [], "a dry run never builds a transport"


def test_send_delivers_chunks_in_order_to_the_owner_only(monkeypatch):
    _configured(monkeypatch)
    text = "\n".join(f"строка {i} " + "я" * 90 for i in range(120))          # ~12 KB -> several chunks
    result = run(text, send=True, transport_factory=FakeTransport)
    (transport,) = FakeTransport.instances
    assert result["exit"] == 0 and result["sent"] is True and result["chunks"] == len(transport.sent) > 2
    assert {chat for chat, _ in transport.sent} == {OWNER.chat_id}
    assert all(len(part) <= orp.CHUNK_CHARS for _, part in transport.sent)
    assert "\n".join(part for _, part in transport.sent) == text.strip(), "no line lost, order kept"
    assert transport.closed is True


def test_secret_text_is_refused_and_nothing_is_built(monkeypatch):
    _configured(monkeypatch)
    for secret in ("ключ sk-or-v1-" + "a1" * 20, "token ghp_" + "A" * 30, "пароль: hunter2hunter2",
                   "bot 123456789:" + "A" * 35):       # ci-secret-scan: allow
        result = run("отчёт\n" + secret, send=True, transport_factory=FakeTransport)
        assert result["exit"] == orp.EXIT_REFUSED and result["ok"] is False
    assert FakeTransport.instances == []
    assert run("обычный отчёт без секретов", send=True, transport_factory=FakeTransport)["exit"] == 0   # negative control


def test_missing_config_is_exit_3_and_nothing_is_sent(tmp_path):
    FakeTransport.instances = []
    result = run("отчёт", send=True, config_path=tmp_path / "nope.json", transport_factory=FakeTransport)
    assert result["exit"] == orp.EXIT_NOT_CONFIGURED and "Пульта" in result["reason"]
    assert FakeTransport.instances == []


def test_delivery_failure_is_reported_with_partial_progress(monkeypatch):
    _configured(monkeypatch)
    text = "\n".join("я" * 3000 for _ in range(3))                           # three chunks

    result = run(text, send=True, transport_factory=lambda s: FakeTransport(s, fail_on=1))
    assert result["exit"] == orp.EXIT_SEND_FAILED and result["sent"] is True
    assert FakeTransport.instances[-1].closed is True


def test_empty_report_is_a_usage_error(monkeypatch):
    _configured(monkeypatch)
    assert run("   \n ")["exit"] == orp.EXIT_USAGE


def test_split_chunks_cuts_an_overlong_line_and_keeps_short_ones_whole():
    parts = orp.split_chunks("a" * 10 + "\n" + "b" * 25, limit=10)
    assert parts == ["a" * 10, "b" * 10, "b" * 10, "b" * 5]
    assert orp.split_chunks("один\nдва", limit=50) == ["один\nдва"]


def test_main_reads_a_file_and_dry_runs(tmp_path, monkeypatch, capsys):
    _configured(monkeypatch)
    report = tmp_path / "r.md"
    report.write_text("Отчёт\nстрока", encoding="utf-8")
    assert orp.main(["--file", str(report)]) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert orp.main(["--file", str(tmp_path / "missing.md")]) == orp.EXIT_USAGE
