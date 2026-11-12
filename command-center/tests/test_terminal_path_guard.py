"""terminal.run path guard (architecture P0 step 1): denied classes are refused in 3 command forms each,
legit commands stay AUTO for the PC operator. Best-effort layer, see bcc/features/terminal_path_guard.py."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from bcc.features import pc_operator as po
from bcc.features import tools_terminal as tt
from bcc.features.terminal_path_guard import path_guard_reason
from bcc.tools import REGISTRY, decide_effect

WIN = sys.platform == "win32"
pytestmark = pytest.mark.skipif(not WIN, reason="Windows paths")

CHROME = r"C:\Users\x\AppData\Local\Google\Chrome\User Data\Default\Login Data"
TDATA = r"C:\Users\x\AppData\Roaming\Telegram Desktop\tdata\D877F783D5D3EF8C"
BCCDB = r"C:\Users\x\AppData\Local\Bossman\CommandCenter\bcc.db"
ENVF = r"D:\proj\.env"
GRECH = "D:" + chr(92) + "Гречка" + chr(92) + "notes.txt"
PICS = r"C:\Users\x\Pictures\me.jpg"
WALLET = r"D:\w\wallet.dat"
SAM = r"C:\Windows\System32\config\SAM"
PST = r"C:\Users\x\Documents\Outlook Files\a.pst"

CLASSES = {
    "FS_DENY_BROWSER": CHROME, "FS_DENY_CHAT": TDATA, "FS_DENY_SECRET": ENVF, "FS_DENY_GRECHKA": GRECH,
    "FS_DENY_MEDIA": PICS, "FS_DENY_HIVE": SAM, "FS_DENY_MAIL": PST,
}


def _forms(path):
    return [f'type "{path}"', f'powershell -NoProfile -Command "Get-Content -Path \'{path}\' -Raw"',
            f'python -c "print(open(r\'{path}\').read())"']


@pytest.mark.parametrize("code,path", list(CLASSES.items()))
def test_each_class_refused_in_three_forms(code, path):
    for cmd in _forms(path):
        r = path_guard_reason(cmd, r"C:\work")
        assert r.startswith(code), (cmd, r)


def test_more_verbs_and_secrets():
    for cmd in (f'copy "{CHROME}" C:/tmp/x', f"xcopy {TDATA} D:/o /s", f'robocopy "{os.path.dirname(CHROME)}" D:/o',
                f"sqlite3 {BCCDB} .dump", f'findstr /i token "{ENVF}"', f"Select-String -Path {WALLET} -Pattern a",
                f"Compress-Archive -Path {TDATA} -DestinationPath D:/a.zip", f"certutil -encode {WALLET} D:/o.txt",
                f"cat {PST} | head", f"more < {CHROME}"):
        assert path_guard_reason(cmd, r"C:\work").startswith("FS_DENY"), cmd


def test_env_vars_relative_and_flag_forms(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\x")
    for cmd in (r"type %LOCALAPPDATA%\Google\Chrome\User Data\Default\Cookies",
                r"Get-Content $env:LOCALAPPDATA\Google\Chrome\User` Data\Default\Cookies",
                r"type %USERPROFILE%\Pictures\a.jpg", r"type ~\.ssh\id_rsa", r"Get-Content -Path:C:\Users\x\.ssh\config"):
        assert path_guard_reason(cmd, r"C:\work").startswith("FS_DENY"), cmd
    # relative path resolved against the cwd of the call
    assert path_guard_reason("type Login Data", os.path.dirname(CHROME)).startswith("FS_DENY_BROWSER")
    assert path_guard_reason(r"type ..\..\Pictures\a.jpg", r"C:\Users\x\Documents\w").startswith("FS_DENY_MEDIA")


def test_junction_into_denied_class_refused(tmp_path):
    target = tmp_path / "AppData" / "Roaming" / "Telegram Desktop" / "tdata"
    target.mkdir(parents=True)
    (target / "key_datas").write_text("x")
    link = tmp_path / "innocent"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("cannot create junction")
    assert "junction" in path_guard_reason(f'type "{link}/key_datas"', str(tmp_path)).lower()


def test_wildcard_expands_on_disk(tmp_path):
    d = tmp_path / "AppData" / "Local" / "Zork" / "Mozilla"
    d.mkdir(parents=True)
    (d / "key4.db").write_text("x")
    assert path_guard_reason(f'type "{d}/key*.db"', str(tmp_path)).startswith("FS_DENY")


LEGIT = ["hostname", "dir C:\\", "git status", r"type C:\Windows\System32\drivers\etc\hosts", "echo hello",
         "powershell -Command \"Get-PSDrive D | Select Free\"", r"git -C D:\Bossman\wt log -1",
         "python -m pytest tests -q", "whoami", "type README.md", r"dir D:\Bossman"]


@pytest.mark.parametrize("cmd", LEGIT)
def test_legit_commands_not_flagged(cmd):
    assert path_guard_reason(cmd, r"D:\Bossman\wt") == "", cmd


def test_rollback_flag(monkeypatch):
    assert path_guard_reason(f'type "{CHROME}"', "C:\\")  # negative control: guard on
    monkeypatch.setenv("BCC_TERMINAL_PATH_GUARD", "0")
    assert path_guard_reason(f'type "{CHROME}"', "C:\\") == ""


async def test_operator_decision_deny_vs_auto(tmp_path):
    """Through decide_effect for the full-access operator: denied class -> deny, legit read -> not deny/ask-less."""
    spec = next(s for s in tt.SPECS if s.name == "terminal.run")
    agent = {"role": po.ROLE, "permissions": {p: True for p in po.TOTAL_CONTROL}}
    for cmd in _forms(CHROME):
        eff, why = decide_effect(spec, {"command": cmd, "mode": "project_host"}, agent)
        assert eff == "deny" and "FS_DENY_BROWSER" in why, (cmd, eff, why)
    for cmd in ("hostname", "dir C:\\", "git status", r"type C:\Windows\System32\drivers\etc\hosts"):
        eff, why = decide_effect(spec, {"command": cmd, "mode": "project_host"}, agent)
        assert eff == "auto", (cmd, eff, why)


async def test_handler_refuses_at_effect_boundary(tmp_path):
    class Ctx:
        svc = None
        task = {"id": 1}
        agent = {}
        workspace = str(tmp_path)
    # the guard fires before any cwd/db access in _tool_run's path only after _resolve_cwd; check the pure hook used there
    assert tt.path_guard_deny(f'type "{CHROME}"', str(tmp_path)).startswith("FS_DENY_BROWSER")
