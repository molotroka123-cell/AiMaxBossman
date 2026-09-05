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
