"""Regression evidence for ASTRA-LIVE-04; actual subprocess and file reads."""
import asyncio
from types import SimpleNamespace

from bcc.features import tools_terminal
from bcc.v2.terminal_control import TerminalManager, TerminalPolicy
from .helpers import make_stack
from .test_action_contract import _allow_root

LIVE_COMMAND = 'python -c "from pathlib import Path; Path(\'glm_acceptance.txt\').write_text(\'GLM_OK\', encoding=\'utf-8\')"'


async def test_live_project_host_nested_quotes_write_verified_file(tmp_path):
    manager = TerminalManager()
    policy = TerminalPolicy([tmp_path], mode="project_host")
    session = await manager.start(LIVE_COMMAND, tmp_path, policy, approved=True)
    await asyncio.wait_for(session._reader, 10)
    assert session.exit_code == 0, "\n".join(session.output)
    assert (tmp_path / "glm_acceptance.txt").read_text(encoding="utf-8") == "GLM_OK"


async def test_terminal_nonzero_exit_is_error_for_run_and_status(env, tmp_path):
    await _allow_root(env, tmp_path)
    stack = await make_stack(env.client)
    ctx = SimpleNamespace(svc=env.svc, task=stack["task"], agent=stack["agent"],
                          workspace=str(tmp_path))
    (tmp_path / "fail.py").write_text("raise SystemExit(17)", encoding="utf-8")
    result = await tools_terminal._tool_run({"command": "python fail.py",
        "mode": "project_host", "cwd": str(tmp_path)}, ctx)
    assert result.data["exit_code"] == 17, result.content
    assert result.error is True
    status = await tools_terminal._tool_status({"session_id": result.data["session_id"]}, ctx)
    assert status.error is True
