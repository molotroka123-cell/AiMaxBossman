"""`bossman call …`: syntax in --help, secrets never in argv/output, exit codes, no peer on dial, STOP steps.

A fake API client stands in for the backend (no network, no worker); the real backend + real CLI run together in
``test_calls_e2e_offline.py``.
"""
from __future__ import annotations

import ast
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import bcc.terminal_cli.cli as cli
from bcc.terminal_cli import calls
from bcc.terminal_cli.api_client import BossmanError
from bcc.terminal_cli.cli import Out, global_stop, main
from bcc.terminal_cli.records import (EXIT_BLOCKED, EXIT_DISCONNECTED, EXIT_FAIL, EXIT_NOT_SUPPORTED, EXIT_OK, EXIT_PARTIAL,
                                      EXIT_STOPPED, EXIT_USAGE)

API_ID, API_HASH, PHONE, CODE, PASSWORD = "1234567", "0123456789abcdef0123456789abcdef", "+79001234567", "48213", "hunter2-2fa"
SECRETS = (API_HASH, PHONE, CODE, PASSWORD)
BASE = "/api/telegram/calls"


class FakeClient:
    """Records every request; answers from ``routes`` {(METHOD, path): value | callable(body) | Exception}."""

    def __init__(self, routes: dict | None = None):
        self.routes = dict(routes or {})
        self.calls: list[tuple[str, str, object]] = []
        self.target = SimpleNamespace(identity={}, data_dir=Path("."))

    def _go(self, method: str, path: str, body=None):
        self.calls.append((method, path, body))
        value = self.routes.get((method, path))
        if value is None:
            raise AssertionError(f"unexpected request {method} {path}")
        if isinstance(value, Exception):
            raise value
        return value(body) if callable(value) else value

    def get(self, path, params=None, **kw):
        return self._go("GET", path)

    def post(self, path, body=None, **kw):
        return self._go("POST", path, body)

    def delete(self, path, **kw):
        return self._go("DELETE", path)

    def request(self, method, path, json=None, **kw):
        return self._go(method, path, json)

    def bodies(self, method: str, path: str) -> list:
        return [b for m, p, b in self.calls if (m, p) == (method, path)]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def status(**over) -> dict:
    base = {"mode": "telegram", "transport": "telegram", "test_label": False, "enabled": True,
            "peer": {"user_id": 7, "label": "Второй аккаунт"},
            "account": {"state": "ready", "has_api": True, "api_id": "…4567", "phone": "+••••4567"},
            "worker": {"running": False}, "call": None, "call_active": False,
            "stop": {"call": False, "global": False, "active": False}, "uncertain_previous": False,
            "last_call": None, "last_error": None, "latency": {"n": 0, "p50": None, "p95": None, "last": None}, "models": {},
            "deps": {"ready": True, "missing": []}, "acl": {"ok": True, "problems": []}, "last_seq": 0}
    base.update(over)
    return base


@pytest.fixture
def fake(monkeypatch):
    def install(routes=None):
        client = FakeClient(routes)
        monkeypatch.setattr(cli, "connect", lambda args: client)
        return client
    return install


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def last_json(text: str) -> dict:
    return [json.loads(line) for line in text.splitlines() if line.strip().startswith("{")][-1]


# ----------------------------------------------------------------- registration and help


def test_call_is_a_terminal_command_in_both_dispatch_tables():
    import bossman.cli as core
    assert "call" in cli.TERMINAL_COMMANDS and "call" in core.TERMINAL_COMMANDS
    assert core.is_terminal_call(["call", "status"]) is True
    assert core.is_terminal_call(["call"]) is True
    assert core.is_terminal_call(["task", "x"]) is False          # Core commands keep their own parser
    assert "Терминал" in Path(core.__file__).read_text(encoding="utf-8")


def _string_tuples(node: ast.AST) -> list[list[str]]:
    rows = []
    if isinstance(node, (ast.Tuple, ast.List)):
        for item in node.elts:
            if isinstance(item, (ast.Tuple, ast.List)):
                rows.append([e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None for e in item.elts])
    return rows


def _terminal_subparser_names() -> set[str]:
    """Names given to ``sub.add_parser(...)`` in build_parser: literals and the loop variables of `for a, b in ((..), ..)`."""
    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    builders = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in ("build_parser", "_build_call_parser")]
    assert len(builders) == 2, "the parser is built by build_parser and _build_call_parser"
    names: set[str] = set()

    def visit(node: ast.AST, loops: list[ast.For]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef):
                continue                        # nested helpers (leaf) attach to other parsers, not to `sub`
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "add_parser" \
                    and isinstance(child.func.value, ast.Name) and child.func.value.id == "sub" and child.args:
                arg = child.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names.add(arg.value)
                elif isinstance(arg, ast.Name):
                    for loop in reversed(loops):
                        targets = [t.id for t in loop.target.elts] if isinstance(loop.target, ast.Tuple) else [loop.target.id]
                        if arg.id in targets:
                            idx = targets.index(arg.id)
                            names.update(row[idx] for row in _string_tuples(loop.iter) if row[idx])
                            break
            visit(child, loops + [child] if isinstance(child, ast.For) else loops)

    for fn in builders:
        visit(fn, [])
    return names


def _core_terminal_set() -> set[str]:
    import bossman.cli as core
    tree = ast.parse(Path(core.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "TERMINAL_COMMANDS" for t in node.targets):
            call = node.value
            return {e.value for e in call.args[0].elts if isinstance(e, ast.Constant)}
    raise AssertionError("TERMINAL_COMMANDS not found in bossman-core")


def test_every_terminal_subparser_name_is_routed_by_the_core_entry_point():
    """A subcommand missing from Core's set never reaches the terminal client: `bossman <name>` fails in Core's parser."""
    names = _terminal_subparser_names()
    assert len(names) >= 20 and {"call", "keys", "exec", "review", "rate"} <= names
    real = set(next(a for a in cli.build_parser()._subparsers._group_actions).choices)      # cross-check the AST reading
    assert names == real
    assert names <= _core_terminal_set(), sorted(names - _core_terminal_set())
    assert set(cli.TERMINAL_COMMANDS) == names


def test_the_ast_check_notices_a_command_missing_from_core():
    """Negative control of the check itself: a set without `call` is reported."""
    without = _core_terminal_set() - {"call"}
    assert "call" in (_terminal_subparser_names() - without)


def test_help_shows_the_real_syntax(capsys):
    code, out, _ = run(capsys, "call", "--help")
    assert code == 0
    for token in ("setup [--stdin]", "peer set <id|@юзернейм> --confirm", "dial [--confirm-unknown] [--wait]", "hangup | stop | resume",
                  "events [--after N] [--follow]", "history [--limit N]", "doctor | selftest [сценарий]", "install [--dry-run]",
                  "contacts [запрос]", "enable | disable", "logout", "save-memory <id> | draft-tasks <id>"):
        assert token in out, token
    code, out, _ = run(capsys, "call", "setup", "--help")
    assert code == 0 and "--stdin" in out
    assert "--api-hash" not in out and "--phone" not in out and "--password" not in out, "secret flags are not advertised"
    code, out, _ = run(capsys, "call", "peer", "set", "--help")
    assert code == 0 and "--confirm" in out


# ----------------------------------------------------------------- secrets never travel in argv


@pytest.mark.parametrize("argv", [
    ["--api-hash", API_HASH], ["--api-id", API_ID], ["--phone", PHONE], ["--code", CODE], ["--password", PASSWORD],
    [API_ID, API_HASH], [PHONE],
])
def test_setup_refuses_secrets_in_the_command_line_without_echoing_them(capsys, fake, argv):
    client = fake()          # no routes: any request would raise AssertionError
    code, out, err = run(capsys, "call", "setup", *argv)
    assert code == EXIT_USAGE
    assert client.calls == [], "nothing may be sent (or even looked up) when a secret came in argv"
    for secret in SECRETS + (API_ID,):
        assert secret not in out and secret not in err
    assert "не принимаю" in err


def test_setup_refusal_is_machine_readable_and_still_secret_free(capsys, fake):
    fake()
    code, out, err = run(capsys, "call", "setup", "--api-hash", API_HASH, "--json")
    rec = last_json(out)
    assert code == EXIT_USAGE and rec["type"] == "error" and rec["kind"] == "usage"
    assert API_HASH not in out + err


def _setup_client(states: list[str], *, password: bool = False, code_error: BossmanError | None = None):
    """A backend that walks the account through the states the real flow has."""
    step = {"i": 0}
    order = states

    def next_state(_body=None):
        step["i"] = min(step["i"] + 1, len(order) - 1)
        return {"state": order[step["i"]], "phone": "+••••4567"}

    def st(_b=None):
        return status(account={"state": order[step["i"]], "has_api": order[step["i"]] != "no_credentials", "api_id": "…4567", "phone": "+••••4567"})

    def code_route(body):
        if code_error is not None and not step.get("failed"):
            step["failed"] = True
            raise code_error
        return next_state()

    routes = {("GET", f"{BASE}/status"): st, ("POST", f"{BASE}/credentials"): lambda b: (next_state(), {"ok": True})[1],
              ("POST", f"{BASE}/login/start"): next_state, ("POST", f"{BASE}/login/code"): code_route,
              ("POST", f"{BASE}/login/password"): next_state}
    return FakeClient(routes)


def test_setup_from_stdin_reaches_the_api_and_prints_no_secret(capsys, monkeypatch):
    client = _setup_client(["no_credentials", "logged_out", "code_sent", "ready"])
    monkeypatch.setattr(cli, "connect", lambda args: client)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join([API_ID, API_HASH, PHONE, CODE]) + "\n"))
    code, out, err = run(capsys, "call", "setup", "--stdin", "--json")
    assert code == EXIT_OK, (out, err)
    assert client.bodies("POST", f"{BASE}/credentials") == [{"api_id": int(API_ID), "api_hash": API_HASH}]
    assert client.bodies("POST", f"{BASE}/login/start") == [{"phone": PHONE}]
    assert client.bodies("POST", f"{BASE}/login/code") == [{"code": CODE}]
    rec = last_json(out)
    assert rec["type"] == "call_setup" and rec["stages"] == ["credentials_saved", "code_sent", "code_accepted", "connected"]
    for secret in SECRETS:
        assert secret not in out and secret not in err


def test_setup_asks_for_the_2fa_password_only_when_telegram_wants_it(capsys, monkeypatch):
    client = _setup_client(["logged_out", "code_sent", "password_needed", "ready"])
    monkeypatch.setattr(cli, "connect", lambda args: client)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join([PHONE, CODE, PASSWORD]) + "\n"))
    code, out, err = run(capsys, "call", "setup", "--stdin", "--json")
    assert code == EXIT_OK
    assert client.bodies("POST", f"{BASE}/login/password") == [{"password": PASSWORD}]
    assert client.bodies("POST", f"{BASE}/credentials") == [], "keys are already saved: they are not asked for again"
    assert PASSWORD not in out + err
    # paired case: no 2FA -> the password endpoint is never called
    client = _setup_client(["logged_out", "code_sent", "ready"])
    monkeypatch.setattr(cli, "connect", lambda args: client)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join([PHONE, CODE]) + "\n"))
    assert run(capsys, "call", "setup", "--stdin", "--json")[0] == EXIT_OK
    assert client.bodies("POST", f"{BASE}/login/password") == []


def test_setup_without_a_terminal_and_without_stdin_flag_is_a_usage_error(capsys, monkeypatch):
    client = _setup_client(["no_credentials", "logged_out"])
    monkeypatch.setattr(cli, "connect", lambda args: client)

    class NoTty(io.StringIO):
        def isatty(self):
            return False
    monkeypatch.setattr(sys, "stdin", NoTty(""))
    code, out, err = run(capsys, "call", "setup")
    assert code == EXIT_USAGE and "--stdin" in err
    assert client.bodies("POST", f"{BASE}/credentials") == []


def test_a_wrong_code_in_stdin_mode_fails_once_and_names_the_stable_code(capsys, monkeypatch):
    err_code = BossmanError("Код не подошёл.", status=422, code="LOGIN_CODE_INVALID", hint="Введите новый код", kind="api")
    client = _setup_client(["logged_out", "code_sent", "ready"], code_error=err_code)
    monkeypatch.setattr(cli, "connect", lambda args: client)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join([PHONE, CODE, CODE]) + "\n"))
    code, out, err = run(capsys, "call", "setup", "--stdin", "--json")
    assert code == EXIT_FAIL
    rec = last_json(out)
    assert rec["type"] == "error" and rec["code"] == "LOGIN_CODE_INVALID"
    assert len(client.bodies("POST", f"{BASE}/login/code")) == 1, "no silent retries of a login code"
    assert CODE not in out + err and PHONE not in out + err


# ----------------------------------------------------------------- exit codes


@pytest.mark.parametrize("api_code", ["NOT_LOGGED_IN", "NO_CREDENTIALS", "NOT_ENABLED", "STOP_ACTIVE", "UNCERTAIN_PREVIOUS_CALL",
                                      "PEER_NOT_SELECTED", "CALL_IN_PROGRESS"])
def test_guard_refusals_and_a_missing_telegram_login_are_blocked_never_auth(capsys, fake, api_code):
    fake({("GET", f"{BASE}/contacts"): BossmanError("Аккаунт не подключён.", status=409, code=api_code, hint="войдите", kind="conflict")})
    code, out, err = run(capsys, "call", "contacts", "--json")
    rec = last_json(out)
    assert code == EXIT_BLOCKED and rec["kind"] == "blocked" and rec["code"] == api_code
    assert rec["kind"] != "auth" and code != EXIT_DISCONNECTED
    assert "подсказка: войдите" in err


def test_a_real_auth_failure_of_bossman_itself_is_still_auth(capsys, fake):
    """Paired control: only Telegram-side states were moved out of 'auth'; the token check keeps its meaning."""
    fake({("GET", f"{BASE}/contacts"): BossmanError("нужна аутентификация", status=401, kind="auth")})
    code, out, _ = run(capsys, "call", "contacts", "--json")
    assert code == EXIT_DISCONNECTED and last_json(out)["kind"] == "auth"


def test_an_older_backend_without_the_calls_api_is_not_supported(capsys, fake):
    fake({("GET", f"{BASE}/status"): BossmanError("Not Found", status=404, kind="not_supported")})
    code, out, _ = run(capsys, "call", "status", "--json")
    assert code == EXIT_NOT_SUPPORTED and last_json(out)["kind"] == "not_supported"


# ----------------------------------------------------------------- status stages


def test_status_in_words_shows_stages_latency_models_and_the_test_label(capsys, fake):
    fake({("GET", f"{BASE}/status"): status(
        mode="offline_test", transport="loopback", test_label=True, call={"state": "active", "phase": "speaking"}, call_active=True,
        latency={"n": 3, "p50": 511.6, "p95": 517.0, "last": 512.0}, models={"stt": "whisper-small", "llm": "qwen", "tts": "piper-ru"},
        uncertain_previous=True, stop={"call": True, "global": False, "active": True},
        last_call={"outcome": "unknown", "turns": 2})})
    code, out, err = run(capsys, "call", "status")
    text = out + err
    assert code == EXIT_OK
    assert "ТЕСТ БЕЗ TELEGRAM" in text
    for stage in ("Подключение", "аккаунт: подключён (+••••4567)", "Тестовый собеседник: Второй аккаунт (id 7)",
                  "STOP звонков", "Звонок: идёт разговор, ", "p50 512 мс", "p95 517 мс", "замеров 3",
                  "распознавание whisper-small", "ответ qwen", "голос piper-ru", "исход неизвестен", "--confirm-unknown"):
        assert stage in text, stage


def test_status_of_an_empty_installation_is_calm_and_says_what_to_do(capsys, fake):
    fake({("GET", f"{BASE}/status"): status(enabled=False, peer=None, account={"state": "no_credentials", "has_api": False},
                                             deps={"ready": False, "missing": ["telethon"]})})
    code, out, _ = run(capsys, "call", "status")
    assert code == EXIT_OK
    assert "ключи api_id/api_hash: не сохранены" in out and "Тестовый собеседник: не выбран" in out
    assert "Звонки: выключены" in out and "не хватает: telethon" in out
    assert "ТЕСТ БЕЗ TELEGRAM" not in out


# ----------------------------------------------------------------- peer, enable


def test_peer_set_needs_the_explicit_confirmation_and_sends_nothing_without_it(capsys, fake):
    client = fake()
    code, out, err = run(capsys, "call", "peer", "set", "7")
    assert code == EXIT_USAGE and "--confirm" in err and client.calls == []


def test_peer_set_sends_an_id_or_a_username_and_never_enables_calls(capsys, fake):
    client = fake({("PUT", f"{BASE}/peer"): {"peer": {"user_id": 7, "label": "Второй"}, "enabled": False}})
    assert run(capsys, "call", "peer", "set", "7", "--confirm", "--json")[0] == EXIT_OK
    assert run(capsys, "call", "peer", "set", "@second_acc", "--confirm", "--json")[0] == EXIT_OK
    assert client.bodies("PUT", f"{BASE}/peer") == [{"user_id": 7, "confirm": True}, {"username": "second_acc", "confirm": True}]
    assert not [c for c in client.calls if c[1] == f"{BASE}/settings"], "choosing the peer does not switch calls on"


def test_enable_and_disable_only_touch_the_enabled_flag(capsys, fake):
    client = fake({("PUT", f"{BASE}/settings"): lambda body: {"enabled": body["enabled"]},
                   ("GET", f"{BASE}/status"): status(peer=None)})
    code, out, err = run(capsys, "call", "enable")
    assert code == EXIT_OK and "Тестовый собеседник ещё не выбран" in out
    assert run(capsys, "call", "disable", "--json")[0] == EXIT_OK
    assert client.bodies("PUT", f"{BASE}/settings") == [{"enabled": True}, {"enabled": False}]


# ----------------------------------------------------------------- dial


def test_dial_has_no_peer_parameter_anywhere(capsys, fake):
    client = fake({("GET", f"{BASE}/status"): status(), ("POST", f"{BASE}/call"): {"call_id": "c-1", "accepted": True, "transport": "telegram"}})
    assert run(capsys, "call", "dial", "--json")[0] == EXIT_OK
    assert client.bodies("POST", f"{BASE}/call") == [{}]
    for bad in (["--peer", "5"], ["--user-id", "5"], ["5"], ["@someone"], ["--to", "5"]):
        code, out, err = run(capsys, "call", "dial", *bad)
        assert code == EXIT_USAGE, bad
    assert len(client.bodies("POST", f"{BASE}/call")) == 1, "a rejected command line never reaches the API"


def test_dial_confirm_unknown_is_explicit_and_only_then_sent(capsys, fake):
    client = fake({("GET", f"{BASE}/status"): status(), ("POST", f"{BASE}/call"): {"call_id": "c-2", "accepted": True, "transport": "telegram"}})
    assert run(capsys, "call", "dial", "--confirm-unknown", "--json")[0] == EXIT_OK
    assert client.bodies("POST", f"{BASE}/call") == [{"confirm_unknown": True}]


def test_dial_refused_by_the_guard_is_blocked_and_says_why(capsys, fake):
    fake({("GET", f"{BASE}/status"): status(),
          ("POST", f"{BASE}/call"): BossmanError("Исход предыдущего звонка неизвестен.", status=409, code="UNCERTAIN_PREVIOUS_CALL",
                                                   hint="повторите с подтверждением", kind="conflict")})
    code, out, err = run(capsys, "call", "dial", "--json")
    assert code == EXIT_BLOCKED and last_json(out)["code"] == "UNCERTAIN_PREVIOUS_CALL"
    assert "подтверждением" in err


@pytest.mark.parametrize("outcome,expected", [("completed", EXIT_OK), ("stopped", EXIT_STOPPED), ("unknown", EXIT_PARTIAL),
                                              ("connection_lost", EXIT_PARTIAL), ("declined", EXIT_FAIL), ("no_answer", EXIT_FAIL)])
def test_dial_wait_reports_the_outcome_as_an_exit_code_with_latency_and_models(capsys, fake, monkeypatch, outcome, expected):
    monkeypatch.setattr(calls.time, "sleep", lambda s: None)
    final = status(last_call={"outcome": outcome, "turns": 2, "call_id": "c-3"}, latency={"n": 2, "p50": 500.0, "p95": 520.0, "last": 510.0},
                   models={"stt": "s", "llm": "l", "tts": "t"}, mode="offline_test", transport="loopback", test_label=True)
    seq = iter([status(last_seq=4), final])
    client = fake({("GET", f"{BASE}/status"): lambda b: next(seq),
                   ("POST", f"{BASE}/call"): {"call_id": "c-3", "accepted": True, "transport": "loopback", "test_label": True},
                   ("GET", f"{BASE}/events"): {"events": [{"seq": 5, "kind": "state", "state": "active"},
                                                          {"seq": 6, "kind": "record", "outcome": outcome, "call_id": "c-3"}], "last_seq": 6}})
    code, out, err = run(capsys, "call", "dial", "--wait", "--json")
    rows = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert code == expected
    assert [r["type"] for r in rows] == ["call_dial", "call_event", "call_event", "call_result"]
    result = rows[-1]
    assert result["outcome"] == outcome and result["latency"]["p50"] == 500.0 and result["models"]["tts"] == "t"
    assert result["test_label"] is True


def test_following_can_be_interrupted_without_touching_the_call(capsys, fake, monkeypatch):
    def boom(*a, **k):
        raise KeyboardInterrupt
    fake({("GET", f"{BASE}/status"): status(), ("POST", f"{BASE}/call"): {"call_id": "c-4", "accepted": True, "transport": "telegram"},
          ("GET", f"{BASE}/events"): boom})
    code, out, err = run(capsys, "call", "dial", "--wait")
    assert code == 130 and "звонок продолжается" in out + err


# ----------------------------------------------------------------- stop / resume / hangup


def test_stop_reports_what_was_confirmed_and_fails_loudly_when_it_was_not(capsys, fake):
    fake({("POST", f"{BASE}/stop"): {"stopped": True, "persisted": True, "hangup_confirmed": True, "terminated": False, "worker": "running"}})
    code, out, _ = run(capsys, "call", "stop", "--json")
    rec = last_json(out)
    assert code == EXIT_OK and rec["persisted"] is True and rec["hangup_confirmed"] is True
    fake({("POST", f"{BASE}/stop"): {"stopped": False, "persisted": False, "terminated": False}})
    code, out, err = run(capsys, "call", "stop")
    assert code == EXIT_FAIL and "НЕ подтверждён" in out + err


def test_stop_says_when_the_worker_had_to_be_terminated(capsys, fake):
    fake({("POST", f"{BASE}/stop"): {"persisted": True, "hangup_confirmed": None, "terminated": True}})
    code, out, err = run(capsys, "call", "stop")
    assert code == EXIT_OK and "принудительно" in out + err


def test_resume_warns_that_the_global_stop_still_blocks(capsys, fake):
    fake({("POST", f"{BASE}/resume"): {"stop_flag": False, "global_stop": True}})
    code, out, err = run(capsys, "call", "resume")
    assert code == EXIT_OK and "Общий STOP" in out + err


def test_global_stop_stops_the_call_explicitly_and_tolerates_a_backend_without_calls(capsys):
    stop_all = {"ok": True, "stopped": {}, "remaining": {}, "errors": []}
    client = FakeClient({("POST", f"{BASE}/stop"): {"persisted": True, "hangup_confirmed": True}, ("POST", "/api/control-plane/stop-all"): stop_all})
    assert global_stop(client, Out("json")) == EXIT_OK
    assert [c[1] for c in client.calls] == [f"{BASE}/stop", "/api/control-plane/stop-all"]
    older = FakeClient({("POST", f"{BASE}/stop"): BossmanError("Not Found", status=404, kind="not_supported"),
                        ("POST", "/api/control-plane/stop-all"): stop_all})
    assert global_stop(older, Out("json")) == EXIT_OK
    capsys.readouterr()
    rec_client = FakeClient({("POST", f"{BASE}/stop"): {"persisted": True}, ("POST", "/api/control-plane/stop-all"): stop_all})
    global_stop(rec_client, Out("json"))
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["calls"]["status"] == "stopped"


def test_global_stop_is_not_green_when_the_call_stop_failed(capsys):
    """Paired negative control: a real failure of the calls step makes the whole STOP unconfirmed (never silent)."""
    stop_all = {"ok": True, "stopped": {}, "remaining": {}, "errors": []}
    for failure in (BossmanError("boom", status=500, kind="api"), RuntimeError("lost")):
        client = FakeClient({("POST", f"{BASE}/stop"): failure, ("POST", "/api/control-plane/stop-all"): stop_all})
        assert global_stop(client, Out("text")) == EXIT_FAIL
        assert "не подтверждён" in capsys.readouterr().out
        assert [c[1] for c in client.calls][-1] == "/api/control-plane/stop-all", "the rest of the STOP still runs"
    unconfirmed = FakeClient({("POST", f"{BASE}/stop"): {"persisted": False}, ("POST", "/api/control-plane/stop-all"): stop_all})
    assert global_stop(unconfirmed, Out("text")) == EXIT_FAIL


# ----------------------------------------------------------------- history, checks


def test_history_shows_outcome_summary_and_post_call_state_without_a_transcript(capsys, fake):
    item = {"call_id": "c-9", "transport": "loopback", "started_at": 1790000000, "outcome": "completed", "turns": [{}, {}, {}],
            "latency_ms": {"p50": 514.3}, "summary": {"text": "Коротко о разговоре.", "agreed_tasks": ["a"]},
            "postcall": {"memory": {"status": "written"}, "drafts": {"count": 1}}}
    fake({("GET", f"{BASE}/history"): {"items": [item]}})
    code, out, _ = run(capsys, "call", "history")
    assert code == EXIT_OK
    for text in ("c-9", "разговор завершён", "ТЕСТ БЕЗ TELEGRAM", "реплик 3", "p50 514 мс", "Коротко о разговоре.",
                 "итог записан в память", "предложено задач: 1, черновиков: 1"):
        assert text in out, text
    fake({("GET", f"{BASE}/history"): {"items": []}})
    assert "звонков ещё не было" in run(capsys, "call", "history")[1]


def test_save_memory_and_draft_tasks_are_owner_clicks_that_call_only_their_endpoints(capsys, fake):
    client = fake({("POST", f"{BASE}/history/c-1/save-memory"): {"memory": {"status": "written", "file": "telegram-call-c-1.md"}},
                   ("POST", f"{BASE}/history/c-1/draft-tasks"): {"drafts": {"ids": [5, 6], "count": 2}}})
    assert run(capsys, "call", "save-memory", "c-1")[0] == EXIT_OK
    code, out, _ = run(capsys, "call", "draft-tasks", "c-1")
    assert code == EXIT_OK and "ничего не запущено" in out
    assert [c[1] for c in client.calls] == [f"{BASE}/history/c-1/save-memory", f"{BASE}/history/c-1/draft-tasks"]


def test_doctor_exit_code_follows_blocked_rows_only(capsys, fake):
    fake({("POST", f"{BASE}/doctor"): {"verdict": "WARN", "rows": [{"check": "Зависимости", "status": "WARN", "detail": "нет", "remedy": "bossman call install"}]}})
    code, out, _ = run(capsys, "call", "doctor")
    assert code == EXIT_OK and "что делать: bossman call install" in out
    fake({("POST", f"{BASE}/doctor"): {"verdict": "BLOCKED", "rows": [{"check": "Права", "status": "BLOCKED", "detail": "шире", "remedy": ""}]}})
    assert run(capsys, "call", "doctor", "--json")[0] == EXIT_BLOCKED


def test_selftest_is_labelled_and_uses_a_long_timeout(capsys, fake):
    seen = {}

    def selftest(body):
        seen["body"] = body
        return {"verdict": "PASS", "evidence_level": "loopback", "results": [{"scenario": "basic", "verdict": "PASS", "checks": {"first_reply_audio": True}}]}
    client = fake({("POST", f"{BASE}/selftest"): selftest})
    code, out, err = run(capsys, "call", "selftest", "basic")
    assert code == EXIT_OK and seen["body"] == {"scenario": "basic"}
    assert "ТЕСТ БЕЗ TELEGRAM" in out + err
    fake({("POST", f"{BASE}/selftest"): {"verdict": "FAIL", "results": [{"scenario": "echo", "verdict": "FAIL", "checks": {"no_false_barge_in": False}}]}})
    assert run(capsys, "call", "selftest", "echo")[0] == EXIT_FAIL
    assert run(capsys, "call", "selftest", "nonsense")[0] == EXIT_USAGE


def _addon(**over) -> dict:
    base = {"complete": False, "installer_supported": True, "missing": ["telethon==1.45.0"], "path": "P",
            "remedy": "bossman call install", "this_platform": "linux"}
    base.update(over)
    return base


def test_install_on_a_backend_without_an_installer_says_not_available_and_names_the_remedy(capsys, fake):
    fake({("GET", f"{BASE}/install"): BossmanError("Not Found", status=404, kind="not_supported")})
    code, out, err = run(capsys, "call", "install", "--json")
    rec = last_json(out)
    assert code == EXIT_NOT_SUPPORTED and rec["state"] == "NOT_AVAILABLE" and "bossman-command-center[calls]" in rec["remedy"]
    code, out, err = run(capsys, "call", "install")
    assert code == EXIT_NOT_SUPPORTED and "NOT_AVAILABLE" in out + err


def test_install_dry_run_only_lists_what_would_be_installed(capsys, fake):
    client = fake({("GET", f"{BASE}/install"): {"state": "idle", "addon": _addon()}})
    code, out, err = run(capsys, "call", "install", "--dry-run")
    assert code == EXIT_OK and "telethon==1.45.0" in out + err
    assert not [c for c in client.calls if c[0] == "POST"], "a dry run never starts the installer"


def test_install_starts_the_server_side_installer_once_and_reports_the_result(capsys, fake, monkeypatch):
    monkeypatch.setattr(calls.time, "sleep", lambda s: None)
    client = fake({("GET", f"{BASE}/install"): {"state": "idle", "addon": _addon()},
                   ("POST", f"{BASE}/install"): {"state": "done", "progress": ["download telethon"],
                                                  "result": {"status": "installed", "installed": ["telethon"], "path": "P"}}})
    code, out, err = run(capsys, "call", "install", "--json")
    assert code == EXIT_OK and last_json(out)["state"] == "installed"
    assert len([c for c in client.calls if c[0] == "POST"]) == 1


def test_install_leaves_a_complete_addon_alone_and_names_the_remedy_on_an_unsupported_platform(capsys, fake):
    client = fake({("GET", f"{BASE}/install"): {"addon": _addon(complete=True, missing=[])}})
    assert run(capsys, "call", "install")[0] == EXIT_OK
    assert [c[0] for c in client.calls] == ["GET"]
    client = fake({("GET", f"{BASE}/install"): {"addon": _addon(installer_supported=False, remedy='pip install "bossman-command-center[calls]"')}})
    code, out, err = run(capsys, "call", "install")
    assert code == EXIT_NOT_SUPPORTED and "pip install" in out + err
    assert not [c for c in client.calls if c[0] == "POST"]


def test_a_failed_install_is_not_green(capsys, fake, monkeypatch):
    monkeypatch.setattr(calls.time, "sleep", lambda s: None)
    fake({("GET", f"{BASE}/install"): {"addon": _addon()},
          ("POST", f"{BASE}/install"): {"state": "error", "error": {"code": "DEPENDENCIES_MISSING"}, "progress": []}})
    code, out, err = run(capsys, "call", "install", "--json")
    assert code == EXIT_FAIL and last_json(out)["state"] == "failed"


def test_call_without_a_subcommand_is_a_usage_error(capsys):
    code, out, err = run(capsys, "call")
    assert code == EXIT_USAGE and "нужна подкоманда" in err
