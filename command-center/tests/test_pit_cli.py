"""Bossman pit CLI contracts: secret-free status, fail-closed doctor, lane routing."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bcc.pit import cli as pit_cli
from bcc.pit.config import config_path, credentials_path, looks_like_repo, pit_home
from bcc.telegram_companion.config import CompanionError


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


def test_running_probe_checks_byte_zero(tmp_path, monkeypatch):
    """The probe must lock the same byte the runtime locks, not EOF."""
    import msvcrt
    import os as _os
    if _os.name != "nt":
        pytest.skip("msvcrt probe")
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