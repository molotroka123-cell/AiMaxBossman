"""Owner-only remote access from the pult: /remote, /remote_status, /chatgpt, /claude_open, /reboot.

Everything runs against seams (no process is spawned, no shutdown is scheduled); the
Windows-only parts are covered by the branch that reports the platform limitation.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from bcc.telegram_companion import remote_access, service
from bcc.telegram_companion.agent_bridge import AGENT_DENIED
from bcc.telegram_companion.remote_access import RemoteAccessMixin


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class Store(dict):
    def get(self, key, default=None):  # noqa: D102
        return super().get(key, default)

    def put(self, key, value):
        self[key] = value


OWNER = SimpleNamespace(key="tg:1", chat_id=1, user_id=1, role="owner")
GUEST = SimpleNamespace(key="tg:2", chat_id=2, user_id=2, role="guest")


def settings(**over):
    base = dict(people=(OWNER,), claude_bridge=True, claude_cwd="", claude_permission_mode="bypassPermissions",
                authorize=lambda m: OWNER)
    base.update(over)
    return SimpleNamespace(**base)


def companion(tmp_path, monkeypatch, *, live=(), busy=(), **over):
    c = object.__new__(service.Companion)
    c.store = Store()
    c.store.path = str(tmp_path / "store.db")
    c.settings = settings(**over)
    c.policy_provider = lambda: c.settings
    c.telegram = SimpleNamespace()
    c.agent_jobs = {}
    c.button = lambda person, label, data: {"text": label, "callback_data": data}
    c.spawned, c.shutdowns = [], []
    c._remote_processes = lambda name: [r for r in live if name in r.get("CommandLine", "")]
    c._busy_processes = lambda: list(busy)
    c._spawn = lambda args, cwd: (c.spawned.append((args, cwd)) or 4242)
    c._run_shutdown = lambda args: c.shutdowns.append(args)
    monkeypatch.setattr(remote_access, "remote_dir", lambda: tmp_path / "remote")
    monkeypatch.setattr(remote_access, "LAUNCHER", tmp_path / "claude_remote_service.ps1")
    monkeypatch.setattr(remote_access, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(remote_access.os, "name", "nt")
    (tmp_path / "claude_remote_service.ps1").write_text("# launcher", encoding="utf-8")
    c.settings.claude_cwd = str(tmp_path)
    return c


def msg(text):
    return {"text": text, "from": {"id": 1}, "chat": {"id": 1}}


# ------------------------------------------------------------------ authority and switches
def test_a_guest_is_denied_and_nothing_is_spawned(tmp_path, monkeypatch):
    c = companion(tmp_path, monkeypatch)
    c.settings.authorize = lambda m: None
    assert run(c.remote_command(GUEST, "/remote", "", msg("/remote"))) == AGENT_DENIED
    assert c.spawned == []


def test_bridge_off_locally_refuses_every_remote_command(tmp_path, monkeypatch):
    c = companion(tmp_path, monkeypatch, claude_bridge=False)
    for command in ("/remote", "/remote_status", "/reboot", "/chatgpt"):
        text = run(c.remote_command(OWNER, command, "", msg(command)))
        assert "claude_bridge: false" in text
    assert c.spawned == [] and c.shutdowns == []


def test_stop_blocks_launch_and_reboot_but_not_status(tmp_path, monkeypatch):
    c = companion(tmp_path, monkeypatch)
    c.store.put("delegation_locked", True)
    assert "СТОП" in run(c.remote_command(OWNER, "/remote", "", msg("/remote")))
    assert "СТОП" in run(c.remote_command(OWNER, "/reboot", "", msg("/reboot")))
    assert "СТОП" in run(c.remote_command(OWNER, "/chatgpt", "", msg("/chatgpt")))
    assert "СТОП" in run(c.remote_command(OWNER, "/claude_open", "", msg("/claude_open")))
    reply = run(c.remote_command(OWNER, "/remote_status", "", msg("/remote_status")))
    assert "СТОП активен" in reply and c.spawned == []
    assert "отменена" in run(c.remote_command(OWNER, "/reboot_cancel", "", msg("/reboot_cancel")))


# ------------------------------------------------------------------ remote control
def test_remote_starts_the_launcher_once_with_the_configured_mode(tmp_path, monkeypatch):
    c = companion(tmp_path, monkeypatch)
    reply = run(c.remote_command(OWNER, "/remote", "", msg("/remote")))
    assert "Запускаю Remote Control" in reply and "bypassPermissions" in reply
    args, cwd = c.spawned[0]
    assert args[:2] == ["powershell", "-NoProfile"] and "-Once" in args
    assert args[args.index("-PermissionMode") + 1] == "bypassPermissions"
    assert args[args.index("-Name") + 1] == "Bossman-Owner-PC" and cwd == str(tmp_path)
    assert c.store.get("remote_launch")["pid"] == 4242


def test_a_live_remote_control_is_reported_and_never_duplicated(tmp_path, monkeypatch):
    live = [{"ProcessId": 777, "CommandLine": "claude.exe remote-control --name Bossman-Owner-PC --spawn=session"}]
    c = companion(tmp_path, monkeypatch, live=live)
    (tmp_path / "remote").mkdir()
    (tmp_path / "remote" / "claude-remote.log").write_text(
        "Connected.\nOpen https://claude.ai/code/session_ABC123 in the app\n", encoding="utf-8")
    reply = run(c.remote_command(OWNER, "/remote", "", msg("/remote")))
    assert "уже работает (PID 777)" in reply
    assert "https://claude.ai/code/session_ABC123" in reply
    assert c.spawned == []


def test_a_help_process_or_other_session_name_does_not_count_as_live(monkeypatch):
    rows = [{"ProcessId": 1, "CommandLine": "claude.exe remote-control --help"},
            {"ProcessId": 2, "CommandLine": "claude.exe remote-control --name Other-PC"},
            {"ProcessId": 3, "CommandLine": "claude.exe remote-control --name Bossman-Owner-PC"}]
    monkeypatch.setattr(remote_access, "list_processes", lambda pattern: rows)
    assert [r["ProcessId"] for r in remote_access.remote_processes("Bossman-Owner-PC")] == [3]


def test_missing_launcher_or_checkout_is_reported_without_spawning(tmp_path, monkeypatch):
    c = companion(tmp_path, monkeypatch)
    (tmp_path / "claude_remote_service.ps1").unlink()
    assert "Нет сценария запуска" in run(c.remote_command(OWNER, "/remote", "", msg("/remote")))
    c.settings.claude_cwd = str(tmp_path / "missing")
    assert "claude_cwd не найдена" in run(c.remote_command(OWNER, "/claude_open", "", msg("/claude_open")))
    assert c.spawned == []


def test_status_shows_process_link_connection_and_last_errors(tmp_path, monkeypatch):
    c = companion(tmp_path, monkeypatch)
    (tmp_path / "remote").mkdir()
    (tmp_path / "remote" / "claude-remote.log").write_text(
        "2026-10-11 attempt 1\nError: network unreachable\nhttps://claude.ai/code/session_X1\nConnected\n",
        encoding="utf-8")
    text = run(c.remote_command(OWNER, "/remote_status", "", msg("/remote_status")))
    assert "не запущен" in text and "Connected" in text
    assert "https://claude.ai/code/session_X1" in text and "network unreachable" in text


def test_status_without_any_log_says_so(tmp_path, monkeypatch):
    c = companion(tmp_path, monkeypatch)
    text = run(c.remote_command(OWNER, "/remote_status", "", msg("/remote_status")))
    assert "Журнал пуст" in text and "не найдена" in text


def test_remote_stop_only_touches_what_the_pult_started(tmp_path, monkeypatch):
    c = companion(tmp_path, monkeypatch)
    assert "чужой Remote Control не трогаю" in run(c.remote_command(OWNER, "/remote_stop", "", msg("/remote_stop")))
    assert c.shutdowns == []
    c.store.put("remote_launch", {"pid": 4242, "at": time.time(), "name": "Bossman-Owner-PC"})
    (tmp_path / "remote").mkdir(exist_ok=True)
    run(c.remote_command(OWNER, "/remote_stop", "", msg("/remote_stop")))
    assert c.shutdowns == [["taskkill", "/T", "/F", "/PID", "4242"]]
    assert (tmp_path / "remote" / "STOP").is_file() and c.store.get("remote_launch") is None


# ------------------------------------------------------------------ apps
def test_chatgpt_launch_states_the_dialog_limitation(tmp_path, monkeypatch):
    c = companion(tmp_path, monkeypatch)
    text = run(c.remote_command(OWNER, "/chatgpt", "", msg("/chatgpt")))
    assert c.spawned[0][0] == ["explorer.exe", "shell:AppsFolder\\" + remote_access.CHATGPT_APP_ID]
    assert "не умеет открыть конкретный диалог" in text


def test_claude_open_runs_the_cli_in_the_checkout(tmp_path, monkeypatch):
    c = companion(tmp_path, monkeypatch)
    text = run(c.remote_command(OWNER, "/claude_open", "", msg("/claude_open")))
    args, cwd = c.spawned[0]
    assert args[-2:] == ["/k", "claude"] and cwd == str(tmp_path) and "/remote" in text


def test_outside_windows_the_launches_report_the_platform(tmp_path, monkeypatch):
    c = companion(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_access.os, "name", "posix")
    assert "только на Windows" in run(c.remote_command(OWNER, "/chatgpt", "", msg("/chatgpt")))
    assert "только на Windows" in run(c.remote_command(OWNER, "/claude_open", "", msg("/claude_open")))
    assert c.spawned == []


# ------------------------------------------------------------------ reboot
def test_reboot_refuses_while_a_render_or_training_runs(tmp_path, monkeypatch):
    c = companion(tmp_path, monkeypatch, busy=["19864: python facefusion.py headless-run"])
    text = run(c.remote_command(OWNER, "/reboot", "", msg("/reboot")))
    assert "не начинаю" in text and "facefusion" in text and c.store.get("reboot_pending") is None


def test_reboot_needs_a_checkpoint_and_an_explicit_confirmation(tmp_path, monkeypatch):
    c = companion(tmp_path, monkeypatch)
    reply = run(c.remote_command(OWNER, "/reboot", "", msg("/reboot")))
    assert "Checkpoint сохранён" in reply
    assert [b["callback_data"] for b in reply.keyboard[0]] == ["/reboot_confirm", "/reboot_cancel"]
    checkpoints = list((tmp_path / "remote").glob("reboot-checkpoint-*.json"))
    assert len(checkpoints) == 1 and c.shutdowns == []
    text = run(c.remote_command(OWNER, "/reboot_confirm", "", msg("/reboot_confirm")))
    assert c.shutdowns[0][:4] == ["shutdown", "/r", "/t", str(remote_access.REBOOT_DELAY_S)]
    assert "Перезагрузка через" in text and c.store.get("reboot_pending") is None


def test_reboot_confirm_without_preparation_or_after_the_window_does_nothing(tmp_path, monkeypatch):
    c = companion(tmp_path, monkeypatch)
    assert "сначала /reboot" in run(c.remote_command(OWNER, "/reboot_confirm", "", msg("/reboot_confirm")))
    c.store.put("reboot_pending", {"until": time.time() - 1, "checkpoint": "x"})
    assert "сначала /reboot" in run(c.remote_command(OWNER, "/reboot_confirm", "", msg("/reboot_confirm")))
    assert c.shutdowns == []


def test_reboot_cancel_aborts_the_scheduled_shutdown(tmp_path, monkeypatch):
    c = companion(tmp_path, monkeypatch)
    run(c.remote_command(OWNER, "/reboot", "", msg("/reboot")))
    assert "отменена" in run(c.remote_command(OWNER, "/reboot_cancel", "", msg("/reboot_cancel")))
    assert c.shutdowns == [["shutdown", "/a"]] and c.store.get("reboot_pending") is None


# ------------------------------------------------------------------ phrases
@pytest.mark.parametrize("text, command, arg", [
    ("Включи удалённый Claude", "/remote", ""),
    ("статус удаленного доступа", "/remote_status", ""),
    ("Открой ChatGPT", "/chatgpt", ""),
    ("открой Claude", "/claude_open", ""),
    ("Продолжи Claude: проверь тесты", "/claude", "проверь тесты"),
    ("Перезагрузи компьютер", "/reboot", ""),
])
def test_owner_phrases_map_to_commands(text, command, arg):
    assert RemoteAccessMixin.phrase_command(text) == (command, arg)


def test_plan_mode_is_not_accepted_by_the_starter_and_falls_back_to_default():
    assert RemoteAccessMixin.remote_mode(SimpleNamespace(claude_permission_mode="plan")) == "default"
    assert RemoteAccessMixin.remote_mode(SimpleNamespace(claude_permission_mode="bypassPermissions")) == "bypassPermissions"


def test_unrelated_text_is_not_a_phrase():
    assert RemoteAccessMixin.phrase_command("открой окно и расскажи про claude") is None
    assert RemoteAccessMixin.phrase_command("/remote") is None
