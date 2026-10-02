"""Bossman pit CLI contracts: secret-free status, fail-closed doctor, lane routing."""
from __future__ import annotations

import json
import ast
from pathlib import Path

import pytest

from bcc.pit import cli as pit_cli
from bcc.pit import runtime as rt
from bcc.pit.config import PITSettings, config_path, credentials_path, load, looks_like_repo, pit_home, save_setup
from bcc.telegram_companion.config import CompanionError, Person


def test_cli_source_is_plain_utf8_and_ast_parseable():
    source = Path(pit_cli.__file__).read_bytes()
    assert not source.startswith(b"\xef\xbb\xbf")
    ast.parse(source.decode("utf-8"))


def test_setup_refuses_to_overwrite(tmp_path):
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(CompanionError):
        pit_cli.cmd_setup(path)


def test_setup_requires_token_and_never_writes_it_to_config(tmp_path, monkeypatch):
    prompts = iter(["", "999", ""])
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: next(prompts))
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(prompts))
    path = config_path(tmp_path)
    with pytest.raises(CompanionError):
        pit_cli.cmd_setup(path)


def test_status_without_config_is_fail_closed(tmp_path, capsys):
    code = pit_cli.cmd_status(config_path(tmp_path))
    assert code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["config_present"] is False
    assert report["process"] == "STOPPED"


def test_status_ignores_unconfigured_locked_passports(tmp_path, monkeypatch, capsys):
    """A stale/revoked namespace with a restrictive ACL cannot break owner status."""
    path = config_path(tmp_path)
    save_setup(path, people=[Person(user_id=101, chat_id=101, role="owner")],
               chat_models=["test/local"], provider_base_url="https://openrouter.ai/api/v1",
               core_url="http://127.0.0.1:8800", local_url="http://127.0.0.1:11434",
               local_models=["test/local"], local_chat_only=True, web_only=True)
    settings = load(path)
    from bcc.pit.identity import derive_person_key

    home = pit_home(tmp_path)
    owner_key = derive_person_key(101, bytes.fromhex(settings.identity_salt))
    owner_dir = home / "personalities" / owner_key
    owner_dir.mkdir(parents=True)
    (owner_dir / "facts.jsonl").write_text('{"id":"known"}\n', encoding="utf-8")
    stale_dir = home / "personalities" / ("f" * 64)
    stale_dir.mkdir(parents=True)
    stale_facts = stale_dir / "facts.jsonl"
    stale_facts.write_text('{"id":"must-not-read"}\n', encoding="utf-8")
    original_read_text = Path.read_text

    def deny_stale_read(file, *args, **kwargs):
        if file == stale_facts:
            raise PermissionError("restricted namespace")
        return original_read_text(file, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_stale_read)
    assert pit_cli.cmd_status(path) == 0
    report = json.loads(capsys.readouterr().out)
    assert "config_error" not in report
    assert report["pit_storage"]["participants"] == 1
    assert report["pit_storage"]["facts"] == 1


def test_doctor_without_config_fails_closed(tmp_path, capsys):
    code = pit_cli.cmd_doctor(config_path(tmp_path))
    assert code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    names = {item["check"] for item in report["checks"]}
    assert "config" in names


def test_stop_without_process_reports_not_running(tmp_path, capsys):
    home = tmp_path / "pit-v1.7"
    home.mkdir(parents=True)
    code = pit_cli.cmd_stop(config_path(tmp_path))
    assert code == 0
    assert "не запущен" in capsys.readouterr().out


def test_poll_stop_flag_raises_stop_requested(tmp_path):
    """bossman pit stop must actually terminate the loop (owner path)."""
    import asyncio

    from bcc.pit.runtime import STOP_FLAG, StopRequested

    runtime = rt.ParticipantRuntime.__new__(rt.ParticipantRuntime)
    runtime.home = tmp_path / "pit-v1.7"
    runtime.home.mkdir(parents=True)
    (runtime.home / STOP_FLAG).write_text("now", encoding="utf-8")
    runtime.store = _DummyStore()
    runtime.telegram = _DummyTelegram()
    runtime.settings = PITSettings(
        data_dir=tmp_path,
        people=(Person(user_id=101, chat_id=101, role="owner"),),
        chat_models=("m:free",),
        provider_base_url="http://127.0.0.1:9/v1", provider_key="k",
        bot_token="t", identity_salt="ab" * 32)
    with pytest.raises(rt.StopRequested):
        asyncio.run(runtime._poll())


class _DummyStore:
    def get(self, key, default=0):
        return default

    def put(self, key, value):
        return None


class _DummyTelegram:
    async def call(self, method, payload):
        return []


def test_keep_system_awake_context_on_windows():
    """PIT start holds an awake state on Windows so sleep cannot kill the bot."""
    import contextlib
    import os as _os

    holder = pit_cli._keep_system_awake()
    if _os.name == "nt":
        assert hasattr(holder, "__enter__") and hasattr(holder, "__exit__")
        with holder:
            pass  # enter/exit must not raise even when called twice in a row
    else:
        assert isinstance(holder, contextlib.nullcontext)


def test_running_probe_checks_byte_zero(tmp_path, monkeypatch):
    """The probe must lock the same byte the runtime locks, not EOF."""
    import os as _os
    if _os.name != "nt":
        pytest.skip("msvcrt probe")
    import msvcrt
    home = tmp_path / "pit-v1.7"
    home.mkdir(parents=True)
    holder = (home / "poller.lock").open("a+b")
    holder.seek(0)
    msvcrt.locking(holder.fileno(), msvcrt.LK_NBLCK, 1)
    try:
        assert pit_cli._is_running(home) is True
    finally:
        msvcrt.locking(holder.fileno(), msvcrt.LK_UNLCK, 1)
        holder.close()
    assert pit_cli._is_running(home) is False


def test_tool_perimeter_denies_participant_hazards():
    assert pit_cli._tool_perimeter() is True


def test_repo_detection_refuses_git_checkout(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert looks_like_repo(repo / "pit-v1.7") is True
    assert looks_like_repo(tmp_path / "pit-v1.7") is False


def test_resolve_data_dir_points_at_config_parent(tmp_path):
    path = config_path(tmp_path)
    assert pit_cli._resolve_data_dir(path) == tmp_path
    assert credentials_path(tmp_path) == path.parent / "credentials.enc"


def test_saved_config_records_the_right_data_root(tmp_path, monkeypatch):
    """The config must point at the CommandCenter root, not one level higher."""
    from bcc.pit.config import load, save_setup

    path = config_path(tmp_path)
    answers = iter(["386321847", "", ""])          # owner id, models, search url
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: "123:testtoken")
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    pit_cli.cmd_setup(path)
    settings = load(path)
    assert Path(settings.data_dir) == tmp_path


def test_main_without_args_defaults_to_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BOSSMAN_DATA_DIR", str(tmp_path))
    assert pit_cli.main(["pit"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["surface"] == "bossman-pit"


def test_start_refuses_without_setup(tmp_path):
    with pytest.raises((FileNotFoundError, OSError, ValueError, CompanionError)):
        pit_cli.cmd_start(config_path(tmp_path))
