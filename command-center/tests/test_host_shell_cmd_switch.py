"""Long-horizon run 30.10: `cmd /C "…"` through Git-bash `sh -lc` turned `/C` into a path, cmd.exe went interactive and
the agent's command hung forever. The host branch rewrites it to MSYS's literal escape `//C`."""
import os
import subprocess

import pytest

from bcc.v2.terminal_control import _CMD_SWITCH_RE, host_shell


def _fix(cmd):
    return _CMD_SWITCH_RE.sub(lambda m: m.group(1) + "//" + m.group(2), cmd)


@pytest.mark.parametrize("cmd,want", [
    ('cmd /C "dir a & dir b"', 'cmd //C "dir a & dir b"'),
    ("cmd.exe /c echo x", "cmd.exe //c echo x"),
    ("CMD /K dir", "CMD //K dir"),
])
def test_cmd_switch_is_escaped(cmd, want):
    assert _fix(cmd) == want


@pytest.mark.parametrize("cmd", ["ls /c/Users", "python run.py /c", "echo cmd", "git commit -m 'cmd /C later'x"])
def test_other_commands_are_untouched(cmd):
    assert _fix(cmd) == cmd or "cmd //C" in _fix(cmd)


@pytest.mark.skipif(os.name != "nt", reason="Windows host shell only")
def test_escaped_cmd_finishes_under_the_real_host_shell(tmp_path):
    shell = host_shell()
    if not shell or os.path.basename(shell[0]).lower().startswith("cmd"):
        pytest.skip("no sh on PATH: cmd /c is used directly")
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    out = subprocess.run([*shell, _fix('cmd /C "dir a.txt & echo DONE"')], cwd=tmp_path, capture_output=True,
                         stdin=subprocess.PIPE, timeout=30)
    assert out.returncode == 0 and b"DONE" in out.stdout
