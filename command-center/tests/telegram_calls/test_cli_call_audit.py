"""`peer --yes` / `dial --confirm-unknown` are the owner's confirmations: TTY answer or BOSSMAN_CALL_NONINTERACTIVE_OWNER=1."""
from __future__ import annotations

import sys

import pytest

pytest.importorskip("rich", reason="the terminal client needs rich")

from bcc.terminal_cli import call as call_mod  # noqa: E402

from .test_cli_call import FakeClient, fake, run  # noqa: E402,F401

ENV = "BOSSMAN_CALL_NONINTERACTIVE_OWNER"
PEER = ("POST", "/api/telegram/calls/peer")
DIAL = ("POST", "/api/telegram/calls/dial")


class Tty:
    def __init__(self, tty): self._t = tty
    def isatty(self): return self._t


def routes():
    return FakeClient(routes={PEER: {"peer": {"user_id": 4242, "label": "Second"}}, DIAL: {"call_id": "c1", "accepted": True}})


def test_dial_confirm_unknown_without_tty_or_env_is_refused_before_any_request(fake, capsys, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.setattr(sys, "stdin", Tty(False))
    c = fake(routes())
    code, out, err = run(["call", "dial", "--confirm-unknown"], capsys)
    assert code != 0 and ENV in (out + err)
    assert not [s for s in c.sent if s[0] == "POST"]


def test_dial_confirm_unknown_with_env_or_interactive_yes_goes_through(fake, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", Tty(False))
    c = fake(routes())
    monkeypatch.setenv(ENV, "1")
    assert run(["call", "dial", "--confirm-unknown"], capsys)[0] == 0
    assert c.sent[-1][2]["confirm_unknown"] is True
    monkeypatch.delenv(ENV)
    monkeypatch.setattr(sys, "stdin", Tty(True))
    monkeypatch.setattr(call_mod, "_ask", lambda prompt: "y")
    c.sent.clear()
    assert run(["call", "dial", "--confirm-unknown"], capsys)[0] == 0 and len(c.sent) == 1
    monkeypatch.setattr(call_mod, "_ask", lambda prompt: "n")          # interactive "no" cancels
    c.sent.clear()
    assert run(["call", "dial", "--confirm-unknown"], capsys)[0] != 0 and not [s for s in c.sent if s[0] == "POST"]


def test_plain_dial_needs_no_owner_confirmation(fake, capsys, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.setattr(sys, "stdin", Tty(False))
    c = fake(routes())
    assert run(["call", "dial"], capsys)[0] == 0 and c.sent[-1][2]["confirm_unknown"] is False


def test_peer_yes_without_tty_or_env_is_refused_and_selects_nothing(fake, capsys, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.setattr(sys, "stdin", Tty(False))
    c = fake(routes())
    code, out, err = run(["call", "peer", "4242", "--yes"], capsys)
    assert code != 0 and ENV in (out + err)
    assert not [s for s in c.sent if s[0] == "POST"]


def test_peer_yes_with_env_or_interactive_yes_confirms(fake, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", Tty(False))
    c = fake(routes())
    monkeypatch.setenv(ENV, "1")
    assert run(["call", "peer", "4242", "--yes"], capsys)[0] == 0
    assert ("POST", "/api/telegram/calls/peer/confirm", {"user_id": 4242}) in c.sent
    monkeypatch.delenv(ENV)
    monkeypatch.setattr(sys, "stdin", Tty(True))
    monkeypatch.setattr(call_mod, "_ask", lambda prompt: "n")
    c.sent.clear()
    run(["call", "peer", "4242", "--yes"], capsys)
    assert not [s for s in c.sent if s[1].endswith("/peer/confirm")]        # interactive "no" does not confirm
