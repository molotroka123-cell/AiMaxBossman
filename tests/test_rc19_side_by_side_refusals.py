"""tools/rc19_side_by_side.ps1 refuses what would touch the owner's data or shortcuts.

The script is the mechanical RC19 install/start/stop/rollback and shortcut procedure. Its
guarantees are refusals, so they are tested as refusals: exit code 2 and nothing written.
Windows-only (the script drives Windows processes, ports and .lnk files).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "rc19_side_by_side.ps1"
SHA = "0" * 40

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only launcher script")


def _shell() -> str:
    found = shutil.which("pwsh") or shutil.which("powershell")
    if not found:
        pytest.skip("no PowerShell on this machine")
    return found


def _run(tmp_path: Path, *args: str, local_app_data: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if local_app_data is not None:
        env["LOCALAPPDATA"] = str(local_app_data)
    return subprocess.run([_shell(), "-NoProfile", "-NonInteractive", "-File", str(SCRIPT), *args,
                           "-Sha", SHA, "-Root", str(tmp_path / "root")],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=120)


@pytest.mark.parametrize("relative", [
    "Bossman/CommandCenter",                 # the owner data root itself
    "Bossman/CommandCenter/sub",             # inside it
    "Bossman",                               # a folder that contains it
    "Bossman/evening/../CommandCenter",      # a path that normalises to it
])
@pytest.mark.parametrize("action", ["Start", "Rehearse", "Shortcuts", "ShortcutTest", "Stop"])
def test_owner_data_root_is_refused(tmp_path, relative, action):
    local = tmp_path / "LocalAppData"
    owner = local / "Bossman" / "CommandCenter"
    owner.mkdir(parents=True)
    (owner / "bcc.db").write_bytes(b"owner")
    data_dir = local / Path(relative)
    done = _run(tmp_path, "-Action", action, "-DataDir", str(data_dir), "-Port", "8839",
                local_app_data=local)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "REFUSED" in done.stdout and "owner data root" in done.stdout
    assert sorted(p.name for p in owner.iterdir()) == ["bcc.db"]
    assert (owner / "bcc.db").read_bytes() == b"owner"


def test_foreign_shortcut_of_the_same_name_is_never_overwritten(tmp_path):
    shortcuts = tmp_path / "Desktop"
    shortcuts.mkdir()
    foreign = shortcuts / "Bossman 1.9 RC.lnk"
    create = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$l = $ws.CreateShortcut('{foreign}'); $l.TargetPath = $env:ComSpec; "
        "$l.Description = 'owner shortcut'; $l.Save()")
    subprocess.run([_shell(), "-NoProfile", "-NonInteractive", "-Command", create], check=True, timeout=60)
    before = foreign.read_bytes()
    done = _run(tmp_path, "-Action", "Shortcuts", "-DataDir", str(tmp_path / "rc-data"),
                "-ShortcutDir", str(shortcuts), "-Port", "8839")
    assert done.returncode == 2, done.stdout + done.stderr
    assert "not written by this script" in done.stdout
    assert foreign.read_bytes() == before
    assert sorted(p.name for p in shortcuts.iterdir()) == ["Bossman 1.9 RC.lnk"]


def test_a_busy_port_is_refused_before_anything_starts(tmp_path):
    import socket

    install = tmp_path / "root" / "rc19-install" / SHA[:8] / f"BOSSMAN-Windows-x64-{SHA[:12]}" / "runtime"
    install.mkdir(parents=True)
    (install / "python.exe").write_bytes(b"")      # never executed: the refusal comes first
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        if port < 1024:
            pytest.skip("ephemeral port below the script's range")
        done = _run(tmp_path, "-Action", "Start", "-DataDir", str(tmp_path / "rc-data"), "-Port", str(port))
    assert done.returncode == 2, done.stdout + done.stderr
    assert f"port {port} already has a listener" in done.stdout
    assert not (tmp_path / "rc-data" / "_rc19").exists()


def _fake_install(tmp_path: Path) -> None:
    runtime = tmp_path / "root" / "rc19-install" / SHA[:8] / f"BOSSMAN-Windows-x64-{SHA[:12]}" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "python.exe").write_bytes(b"")      # never executed: every case refuses first


def _hold_backend_lock(data_dir: Path, info: dict):
    """What bcc/backend_lock.py does in the serving process: byte-range lock + holder info."""
    import json
    import msvcrt
    data_dir.mkdir(parents=True, exist_ok=True)
    handle = open(data_dir / "backend.lock", "a+b")
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    (data_dir / "backend.json").write_text(json.dumps(info), encoding="utf-8")
    return handle


def test_a_data_dir_served_by_another_backend_is_refused(tmp_path):
    _fake_install(tmp_path)
    data = tmp_path / "rc-data"
    handle = _hold_backend_lock(data, {"pid": 4242, "host": "127.0.0.1", "port": 8835,
                                       "build_sha": "f" * 40, "kind": "server"})
    try:
        done = _run(tmp_path, "-Action", "Start", "-DataDir", str(data), "-Port", "8836")
    finally:
        handle.close()
    assert done.returncode == 2, done.stdout + done.stderr
    assert "ALREADY RUNNING" in done.stdout and "pid 4242" in done.stdout and ":8835" in done.stdout
    assert "held by another backend" in done.stdout
    assert not (data / "_rc19").exists()


def test_the_same_backend_already_serving_is_reported_not_restarted(tmp_path):
    _fake_install(tmp_path)
    data = tmp_path / "rc-data"
    handle = _hold_backend_lock(data, {"pid": 4243, "host": "127.0.0.1", "port": 8837,
                                       "build_sha": SHA, "kind": "desktop"})
    try:
        done = _run(tmp_path, "-Action", "Start", "-DataDir", str(data), "-Port", "8837")
    finally:
        handle.close()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ALREADY RUNNING" in done.stdout and "attach to it" in done.stdout
    assert not (data / "_rc19").exists()


def test_a_stale_backend_json_without_the_lock_is_ignored(tmp_path):
    import socket

    _fake_install(tmp_path)
    data = tmp_path / "rc-data"
    _hold_backend_lock(data, {"pid": 1, "port": 8838, "build_sha": SHA}).close()   # holder gone
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        if port < 1024:
            pytest.skip("ephemeral port below the script's range")
        done = _run(tmp_path, "-Action", "Start", "-DataDir", str(data), "-Port", str(port))
    assert done.returncode == 2, done.stdout + done.stderr
    assert "ALREADY RUNNING" not in done.stdout
    assert f"port {port} already has a listener" in done.stdout


def test_the_script_hardcodes_no_user_profile_path():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "Users\\asd" not in text and "Users/asd" not in text


def test_replace_owner_shortcuts_never_removes_one_before_the_install_is_proven(tmp_path):
    """-ReplaceOwnerShortcuts backs up and replaces only after the RC install checks pass:
    with no install the owner's shortcut must still be exactly where it was."""
    shortcuts = tmp_path / "Desktop"
    shortcuts.mkdir()
    foreign = shortcuts / "Bossman.lnk"
    create = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$l = $ws.CreateShortcut('{foreign}'); $l.TargetPath = $env:ComSpec; "
        "$l.Description = 'owner shortcut'; $l.Save()")
    subprocess.run([_shell(), "-NoProfile", "-NonInteractive", "-Command", create], check=True, timeout=60)
    before = foreign.read_bytes()
    done = _run(tmp_path, "-Action", "Shortcuts", "-DataDir", str(tmp_path / "rc-data"),
                "-ShortcutDir", str(shortcuts), "-Port", "8839", "-WindowName", "Bossman",
                "-CmdName", "Bossman CMD", "-JeffName", "Bossman Jeff", "-ReplaceOwnerShortcuts")
    assert done.returncode == 1, done.stdout + done.stderr          # install missing → Fail
    assert "bundled runtime missing" in done.stdout
    assert foreign.read_bytes() == before
    assert not (tmp_path / "root" / "evidence" / "rc19" / "old-shortcuts").exists()
