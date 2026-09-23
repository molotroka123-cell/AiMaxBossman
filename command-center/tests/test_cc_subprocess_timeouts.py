"""A hung host child must never hang the Command Center or leave an orphan tree.

Open 1.0 defect "subprocess calls without a timeout / leaving orphans":

* bcc/desktop_install.py — the default runner of `install()` ran PowerShell
  (`subprocess.run(argv, check=False)`) with no timeout: a stuck COM call or a
  policy prompt hung `bcc-desktop --install-shortcut` forever.
* bcc/telegram_companion/claude_bridge.py — `_kill_tree` ran `taskkill` with no
  timeout, synchronously inside the event loop: a hung taskkill froze the whole
  companion right when it was trying to stop a runaway agent. Off Windows it
  raised FileNotFoundError instead of stopping the agent.

The children are real processes: a Python fake stands in for powershell /
taskkill / the agent CLI at the Popen boundary (everything else — subprocess.run,
pipes, the tree kill — stays real). A fake writes a heartbeat file every 50 ms;
"dead" means the heartbeat stops moving after the call returned. A fake gives up
by itself after LIFETIME seconds, so the unfixed code does not hang the suite.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bcc import desktop_install
from bcc.telegram_companion import claude_bridge

T = 2.0 if os.name == "nt" else 1.0          # timeout under test
BOUND = T + (3.0 if os.name == "nt" else 1.5)
GUARD = T + 6.0
LIFETIME = 9.0

_BEAT = r'''
import pathlib, sys, time
d = pathlib.Path(sys.argv[1]); name = sys.argv[2]; stop = time.monotonic() + float(sys.argv[3])
d.mkdir(parents=True, exist_ok=True)
n = 0
while time.monotonic() < stop:
    try:
        (d / (name + ".beat")).write_text(str(n))
    except OSError:
        pass
    n += 1
    time.sleep(0.05)
'''

_HUNG_TREE = r'''
import subprocess, sys
beat, markers, lifetime = @BEAT@, @MARKERS@, @LIFETIME@
subprocess.Popen([sys.executable, "-c", beat, markers, "grandchild", lifetime])
sys.argv = ["beat", markers, "child", lifetime]
exec(compile(beat, "<beat>", "exec"), {"__name__": "__main__"})
'''


def hung_tree(tmp_path: Path, name: str = "fake") -> tuple[Path, Path]:
    markers = tmp_path / f"{name}-beats"
    script = tmp_path / f"{name}.py"
    script.write_text(_HUNG_TREE.replace("@BEAT@", repr(_BEAT))
                      .replace("@MARKERS@", repr(str(markers)))
                      .replace("@LIFETIME@", repr(str(LIFETIME))), encoding="utf-8")
    return script, markers


def hung_single(tmp_path: Path, name: str = "single") -> tuple[Path, Path]:
    markers = tmp_path / f"{name}-beats"
    script = tmp_path / f"{name}.py"
    script.write_text(f"import sys\nsys.argv = ['beat', {str(markers)!r}, 'child', {str(LIFETIME)!r}]\n"
                      + _BEAT, encoding="utf-8")
    return script, markers


def redirect_popen(monkeypatch, name: str, script: Path) -> None:
    """Swap ONE host binary (`name`) for `python script` at the Popen boundary."""
    real = subprocess.Popen

    class Redirected(real):  # type: ignore[misc, valid-type]
        def __init__(self, args, *a, **kw):
            if isinstance(args, (list, tuple)) and args and Path(str(args[0])).stem.lower() == name:
                args = [sys.executable, str(script), *[str(x) for x in args[1:]]]
            super().__init__(args, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", Redirected)


def _read(p: Path) -> str | None:
    try:
        return p.read_text()
    except OSError:
        return None


def alive_after(markers: Path, names: tuple[str, ...]) -> list[str]:
    beats = {n: markers / f"{n}.beat" for n in names}
    missing = [n for n, p in beats.items() if not p.exists()]
    assert not missing, f"{missing} never started before the timeout"
    first = {n: _read(p) for n, p in beats.items()}
    time.sleep(0.6)
    return [n for n, p in beats.items() if _read(p) != first[n]]


# ---------------------------------------------------------------- desktop_install.py


@pytest.fixture
def win_home(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    return tmp_path, desktop


def test_desktop_install_powershell_timeout_is_a_clear_failure_and_kills_the_tree(tmp_path, monkeypatch, win_home):
    home, desktop = win_home
    script, beats = hung_tree(tmp_path)
    redirect_popen(monkeypatch, "powershell", script)
    monkeypatch.setattr(desktop_install, "POWERSHELL_TIMEOUT_S", T, raising=False)
    spec = desktop_install.build_spec(executable="python.exe", workdir=home)

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="ярлык") as ei:
        desktop_install.install(spec, home=home, system="Windows", desktop_resolver=lambda: str(desktop))
    assert time.monotonic() - started < BOUND
    assert "powershell" in str(ei.value)
    assert alive_after(beats, ("child", "grandchild")) == [], "powershell tree outlived the timeout"


def test_desktop_install_negative_control_default_runner_keeps_exit_code_semantics(tmp_path, monkeypatch, win_home):
    home, desktop = win_home
    spec = desktop_install.build_spec(executable="python.exe", workdir=home)
    ok = tmp_path / "ok.py"
    ok.write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    redirect_popen(monkeypatch, "powershell", ok)
    created = desktop_install.install(spec, home=home, system="Windows", desktop_resolver=lambda: str(desktop))
    assert created[0] == desktop / "BOSSMAN.lnk"

    bad = tmp_path / "bad.py"
    bad.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    redirect_popen(monkeypatch, "powershell", bad)
    with pytest.raises(RuntimeError, match="powershell код 3"):
        desktop_install.install(spec, home=home, system="Windows", desktop_resolver=lambda: str(desktop))


# ---------------------------------------------------------------- claude_bridge.py


@pytest.fixture
def victim():
    """A process for taskkill to aim at, so no real taskkill can ever hit the test runner."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc
    proc.kill()
    proc.wait(timeout=10)


def test_claude_bridge_taskkill_is_bounded_and_does_not_freeze_the_companion(tmp_path, monkeypatch, victim):
    script, beats = hung_single(tmp_path, "taskkill")
    redirect_popen(monkeypatch, "taskkill", script)
    monkeypatch.setattr(claude_bridge, "TASKKILL_TIMEOUT_S", T, raising=False)
    monkeypatch.setattr(claude_bridge, "_WINDOWS", True, raising=False)   # the owner's platform branch

    started = time.monotonic()
    claude_bridge._kill_tree(victim.pid)
    assert time.monotonic() - started < BOUND, "a hung taskkill froze the event loop"
    assert alive_after(beats, ("child",)) == [], "the hung taskkill itself was left running"


async def test_claude_bridge_run_timeout_kills_the_agent_tree(tmp_path):
    script, beats = hung_tree(tmp_path, "agent")

    started = time.monotonic()
    code, out, err = await asyncio.wait_for(
        claude_bridge._run([sys.executable, str(script)], stdin=b"prompt", cwd=str(tmp_path), timeout=T), GUARD)
    assert (code, out, err) == (None, b"", b"")
    assert time.monotonic() - started < BOUND
    assert alive_after(beats, ("child", "grandchild")) == [], "the agent tree outlived the timeout"


async def test_claude_bridge_negative_control_fast_agent_output_is_unchanged(tmp_path):
    script = tmp_path / "echo.py"
    script.write_text("import sys; data = sys.stdin.read(); print('got:' + data); "
                      "sys.stderr.write('warn'); sys.exit(2)\n", encoding="utf-8")
    code, out, err = await claude_bridge._run([sys.executable, str(script)], stdin=b"hello",
                                              cwd=str(tmp_path), timeout=30)
    assert code == 2 and out.decode().strip() == "got:hello" and err == b"warn"
