"""`bossman call ...` against a fake API client (no server, no Telegram) + the global STOP hangup step."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("rich", reason="the terminal client needs rich")

from bcc.terminal_cli import call as call_mod  # noqa: E402
from bcc.terminal_cli import cli  # noqa: E402
from bcc.terminal_cli.api_client import BossmanError  # noqa: E402

API_HASH = "0123456789abcdef0123456789abcdef"      # fixture shape only
PHONE = "+79001234567"
CODE = "48151"
PWD = "correct horse"


class FakeClient:
    def __init__(self, status=None, routes=None):
        self.status = status or {"account": {"state": "ready"}, "settings": {"enabled": True}, "stop": {}, "worker": {}}
        self.routes = routes or {}
        self.sent: list[tuple] = []
        self.target = SimpleNamespace(identity={})

    def __enter__(self): return self
    def __exit__(self, *a): return None

    def get(self, path, **kw):
        self.sent.append(("GET", path, kw.get("params")))
        if path.endswith("/status"):
            return self.status
        return self.routes.get(("GET", path), {})

    def post(self, path, body=None, **kw):
        self.sent.append(("POST", path, body))
        r = self.routes.get(("POST", path), {})
        if isinstance(r, BossmanError):
            raise r
        return r

    def delete(self, path, **kw):
        self.sent.append(("DELETE", path, None))
        return {}


@pytest.fixture
def fake(monkeypatch):
    holder = {}

    def install(client):
        holder["c"] = client
        monkeypatch.setattr(cli, "connect", lambda args: client)
        return client
    return install


def run(argv, capsys):
    code = cli.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


# ---------------------------------------------------------------- registration

def _core_terminal_commands() -> set[str]:
    """TERMINAL_COMMANDS of THIS checkout's bossman-core (the installed `bossman` may be another checkout)."""
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[3] / "bossman-core" / "bossman" / "cli.py"
    for node in ast.walk(ast.parse(src.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "TERMINAL_COMMANDS" for t in node.targets):
            return {c.value for c in ast.walk(node.value) if isinstance(c, ast.Constant)}
    raise AssertionError("TERMINAL_COMMANDS not found in bossman-core/bossman/cli.py")


def test_call_is_registered_in_both_terminal_command_tables():
    assert "call" in cli.TERMINAL_COMMANDS and "call" in _core_terminal_commands()
    assert set(cli.TERMINAL_COMMANDS) == _core_terminal_commands()      # the two copies must not drift


def test_every_subcommand_parses_and_dispatches():
    for sub in call_mod.SUBCOMMANDS:
        assert cli.build_parser().parse_args(["call", sub]).sub == sub


# ---------------------------------------------------------------- thin client behaviour

def test_status_and_history_are_plain_gets(fake, capsys):
    c = fake(FakeClient(routes={("GET", "/api/telegram/calls/history"): {"calls": [{"call_id": "c1", "outcome": "completed"}]}}))
    code, out, _ = run(["call", "status"], capsys)
    assert code == 0 and "подключён" in out
    code, out, _ = run(["call", "history", "--limit", "5", "--json"], capsys)
    assert code == 0 and json.loads(out.strip().splitlines()[-1])["calls"][0]["call_id"] == "c1"
    assert ("GET", "/api/telegram/calls/history", {"limit": 5}) in c.sent


def test_dial_sends_no_peer_and_the_parser_refuses_one(fake, capsys, monkeypatch):
    monkeypatch.setenv("BOSSMAN_CALL_NONINTERACTIVE_OWNER", "1")
    c = fake(FakeClient(routes={("POST", "/api/telegram/calls/dial"): {"call_id": "c1", "accepted": True}}))
    assert run(["call", "dial"], capsys)[0] == 0
    body = c.sent[-1][2]
    assert c.sent[-1][:2] == ("POST", "/api/telegram/calls/dial") and body["confirm_unknown"] is False
    assert run(["call", "dial", "--confirm-unknown"], capsys)[0] == 0
    assert c.sent[-1][2]["confirm_unknown"] is True
    assert c.sent[-1][2]["request_id"] and c.sent[-1][2]["request_id"] != body["request_id"]     # fresh per command
    n = len(c.sent)
    for bad in (["call", "dial", "4242"], ["call", "dial", "--peer", "4242"], ["call", "dial", "--phone", PHONE]):
        assert run(bad, capsys)[0] != 0
    assert len(c.sent) == n                     # nothing was sent for a refused invocation


def test_api_refusal_becomes_a_message_and_nonzero_exit(fake, capsys):
    err = BossmanError("Действует STOP: звонки заблокированы.", status=409, code="STOP_ACTIVE", hint="Снимите STOP", kind="conflict")
    fake(FakeClient(routes={("POST", "/api/telegram/calls/dial"): err}))
    code, out, errout = run(["call", "dial"], capsys)
    assert code != 0 and "STOP" in errout


def test_stop_hangup_resume_use_their_endpoints(fake, capsys):
    c = fake(FakeClient(routes={("POST", "/api/telegram/calls/stop"): {"stopped": True}}))
    for sub in ("stop", "hangup", "resume"):
        assert run(["call", sub], capsys)[0] == 0
    assert [p for m, p, _ in c.sent if m == "POST"] == [f"/api/telegram/calls/{s}" for s in ("stop", "hangup", "resume")]


def test_peer_select_confirms_only_with_yes_or_an_interactive_answer(fake, capsys, monkeypatch):
    c = fake(FakeClient(routes={("POST", "/api/telegram/calls/peer"): {"peer": {"user_id": 4242, "label": "Second"}}}))
    monkeypatch.setattr(call_mod, "_ask", lambda prompt: "n")
    assert run(["call", "peer", "4242"], capsys)[0] == 0
    assert not [s for s in c.sent if s[1].endswith("/peer/confirm")]
    monkeypatch.setattr(call_mod, "_ask", lambda prompt: "y")
    run(["call", "peer", "4242"], capsys)
    assert ("POST", "/api/telegram/calls/peer/confirm", {"user_id": 4242}) in c.sent
    c.sent.clear()
    monkeypatch.setenv("BOSSMAN_CALL_NONINTERACTIVE_OWNER", "1")
    run(["call", "peer", "4242", "--yes"], capsys)
    assert ("POST", "/api/telegram/calls/peer/confirm", {"user_id": 4242}) in c.sent
    run(["call", "peer", "--clear"], capsys)
    assert ("DELETE", "/api/telegram/calls/peer", None) in c.sent


def test_install_and_logout_need_confirmation(fake, capsys, monkeypatch):
    c = fake(FakeClient(routes={("POST", "/api/telegram/calls/install"): {"ok": True, "status": "PASS", "message": "ok"},
                                ("POST", "/api/telegram/calls/logout"): {"state": "no_credentials"}}))
    monkeypatch.setattr(call_mod, "_ask", lambda prompt: "")
    assert run(["call", "install"], capsys)[0] != 0 and run(["call", "logout"], capsys)[0] != 0
    assert not [s for s in c.sent if s[0] == "POST"]
    assert run(["call", "install", "--yes"], capsys)[0] == 0
    assert ("POST", "/api/telegram/calls/install", {"confirm": True}) in c.sent
    assert run(["call", "logout", "--yes"], capsys)[0] == 0


# ---------------------------------------------------------------- secrets

def test_login_walks_the_steps_with_hidden_prompts_only(fake, capsys, monkeypatch):
    c = fake(FakeClient(status={"account": {"state": "no_credentials"}}, routes={
        ("POST", "/api/telegram/calls/login/credentials"): {"state": "logged_out"},
        ("POST", "/api/telegram/calls/login/start"): {"state": "code_sent"},
        ("POST", "/api/telegram/calls/login/code"): {"state": "password_needed"},
        ("POST", "/api/telegram/calls/login/password"): {"state": "ready"}}))
    answers = iter(["123456", API_HASH, PHONE, CODE, PWD])
    prompts = []
    monkeypatch.setattr(call_mod, "_secret", lambda prompt: prompts.append(prompt) or next(answers))
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("a secret must never be read with input()"))
    code, out, err = run(["call", "login"], capsys)
    assert code == 0 and len(prompts) == 5
    posts = [(p, b) for m, p, b in c.sent if m == "POST"]
    assert posts[0][1] == {"api_id": 123456, "api_hash": API_HASH}
    assert posts[1][1] == {"phone": PHONE} and posts[2][1] == {"code": CODE} and posts[3][1] == {"password": PWD}
    for secret in (API_HASH, PHONE, CODE, PWD):
        assert secret not in out and secret not in err


def test_login_json_output_holds_no_secret(fake, capsys, monkeypatch):
    fake(FakeClient(status={"account": {"state": "logged_out"}}, routes={
        ("POST", "/api/telegram/calls/login/start"): {"state": "ready"}}))
    monkeypatch.setattr(call_mod, "_secret", lambda prompt: PHONE)
    code, out, err = run(["call", "login", "--json"], capsys)
    assert code == 0 and PHONE not in out + err


def test_secrets_are_refused_in_argv(fake, capsys):
    fake(FakeClient(status={"account": {"state": "no_credentials"}}))
    for bad in (["call", "login", "--api-hash", API_HASH], ["call", "login", "--phone", PHONE],
                ["call", "login", "--code", CODE], ["call", "login", API_HASH]):
        assert run(bad, capsys)[0] != 0, bad


def test_secret_prompt_needs_a_terminal_and_uses_getpass(monkeypatch):
    class Stdin:
        def __init__(self, tty): self._t = tty
        def isatty(self): return self._t
    monkeypatch.setattr(sys, "stdin", Stdin(False))
    with pytest.raises(BossmanError):
        call_mod._secret("x: ")
    monkeypatch.setattr(sys, "stdin", Stdin(True))
    seen = []
    monkeypatch.setattr(call_mod.getpass, "getpass", lambda prompt: seen.append(prompt) or "  value ")
    assert call_mod._secret("hidden: ") == "value" and seen == ["hidden: "]


# ---------------------------------------------------------------- global STOP reaches the call

class StopClient:
    def __init__(self, calls_error=None):
        self.posts: list[str] = []
        self.calls_error = calls_error

    def get(self, path, **kw):
        return [] if path == "/api/tasks" else {"items": []}

    def post(self, path, body=None):
        self.posts.append(path)
        if path == "/api/telegram/calls/stop":
            if self.calls_error:
                raise self.calls_error
            return {"stopped": True}
        return {"stopped": True}


def _global_stop(client, capsys):
    code = cli.global_stop(client, cli.Out("json"))
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_global_stop_also_hangs_up_the_telegram_call(capsys):
    c = StopClient()
    code, rec = _global_stop(c, capsys)
    assert code == 0 and rec["calls_stopped"] is True
    assert "/api/telegram/calls/stop" in c.posts and "/api/computer/stop" in c.posts


def test_global_stop_tolerates_a_build_without_calls_but_reports_a_real_failure(capsys):
    old = StopClient(BossmanError("Not Found", status=404, kind="not_supported"))
    code, rec = _global_stop(old, capsys)
    assert code == 0 and rec["calls_stopped"] is False and not rec.get("errors")
    broken = StopClient(BossmanError("процесс звонков не отвечает", status=503, kind="api"))
    code, rec = _global_stop(broken, capsys)
    assert code != 0 and rec["errors"] == [{"calls": "процесс звонков не отвечает"}]
