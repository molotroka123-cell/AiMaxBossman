"""Native Windows cmd transport: actual effects, fresh reads and permission gates."""
import asyncio
import os
import sys
import uuid

import pytest

from bcc.v2 import terminal_control as tc
from bcc.v2.verification import ExpectedState, verify


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows cmd.exe")
async def test_cmd_nested_quotes_cyrillic_effect_and_fresh_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "host_shell", lambda: [os.environ.get("COMSPEC", "cmd.exe"), "/c"])
    work = tmp_path / "проект с пробелами"
    work.mkdir()
    target = work / f"проверка {uuid.uuid4().hex}.txt"
    marker = "доказательство " + uuid.uuid4().hex
    script = f"from pathlib import Path; Path({str(target)!r}).write_text({marker!r}, encoding='utf-8')"
    command = f'"{sys.executable}" -c "{script}"'
    mgr = tc.TerminalManager()
    policy = tc.TerminalPolicy([work], "project_host")
    with pytest.raises(PermissionError, match="approval"):
        await mgr.start(command, work, policy)
    assert not target.exists()
    expected = ExpectedState("file", str(target), {"contains": marker})
    assert (await verify(expected, svc=None, task={}, roots=[work])).status == "FAILED"
    session = await mgr.start(command, work, policy, approved=True)
    await asyncio.wait_for(session._reader, 20)
    assert session.exit_code == 0, session.output
    assert target.read_text(encoding="utf-8") == marker
    assert (await verify(expected, svc=None, task={}, roots=[work])).status == "VERIFIED"
    target.write_text("changed after execution", encoding="utf-8")
    assert (await verify(expected, svc=None, task={}, roots=[work])).status == "FAILED"
    with pytest.raises(PermissionError, match="denied"):
        await mgr.start("git reset --hard", work, policy, approved=True)


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows process tree")
async def test_kill_stops_shell_children_before_their_delayed_effect(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "host_shell", lambda: [os.environ.get("COMSPEC", "cmd.exe"), "/c"])
    work = tmp_path / "дочерний процесс с пробелами"
    work.mkdir()
    child = work / "worker.py"
    child.write_text(
        "import time\nfrom pathlib import Path\n"
        "Path('ready').write_text('ready')\n"
        "time.sleep(2)\nPath('effect').write_text('must not happen')\n",
        encoding="utf-8",
    )
    manager = tc.TerminalManager()
    session = await manager.start(
        f'"{sys.executable}" "{child}"', work,
        tc.TerminalPolicy([work], "project_host"), approved=True,
    )
    try:
        async with asyncio.timeout(10):
            while not (work / "ready").exists():
                assert not session.finished, session.output
                await asyncio.sleep(0.02)
        await asyncio.wait_for(manager.kill(session.id), 10)
        await asyncio.wait_for(session._reader, 10)
        assert session.finished and session.exit_code is not None
        await asyncio.sleep(2.1)
        assert not (work / "effect").exists(), "cancelled command left its child running"
    finally:
        if session.proc.returncode is None:
            session.proc.kill()
        await asyncio.wait_for(session.proc.wait(), 10)


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows directory junction")
async def test_windows_junction_cannot_escape_approved_root(tmp_path):
    import _winapi

    root = tmp_path / "разрешённый каталог"
    outside = tmp_path / "вне разрешённых корней"
    root.mkdir()
    outside.mkdir()
    marker = outside / "marker.txt"
    marker.write_text("unchanged", encoding="utf-8")
    link = root / "escape"
    _winapi.CreateJunction(str(outside), str(link))
    try:
        assert link.resolve() == outside.resolve()
        manager = tc.TerminalManager()
        with pytest.raises(PermissionError, match="outside allowed roots"):
            await manager.start(
                "echo overwritten > marker.txt", link,
                tc.TerminalPolicy([root], "project_host"), approved=True,
            )
        assert marker.read_text(encoding="utf-8") == "unchanged"
        assert manager.sessions == {}
    finally:
        os.rmdir(link)
