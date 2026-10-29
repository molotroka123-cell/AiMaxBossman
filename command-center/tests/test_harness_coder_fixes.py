"""Harness coder fixes after the trio audit 29.10 (Codex H4-A/H4-B, Claude Hole #3, independent review F1).

Each test reproduces a hole on the real code path first (red), then pins the fix with a negative control.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from bcc.features import pc_agent


def _svc(tmp_path: Path):
    async def _publish(*_a, **_k):
        return None
    return SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path, port=8801), approvals=SimpleNamespace(),
                           db=None, bus=SimpleNamespace(publish=_publish, subscribe=lambda *_a, **_k: None))


def _wait_gone(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not psutil.pid_exists(pid):
            return True
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.1)
    return not psutil.pid_exists(pid)


# ------------------------------------------------------------------ Task 1 (Codex H4-B): descendants after a NORMAL exit

@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")
def test_run_command_kills_a_detached_descendant_after_a_normal_exit(tmp_path):
    svc = _svc(tmp_path)
    rt = pc_agent.get_runtime(svc)
    parent = rt.workspace_dir / "parent_spawns_child.py"
    parent.write_text(
        "import subprocess, sys\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],\n"
        "                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "print(c.pid)\n", encoding="utf-8")
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])     # NOT in the step's job
    try:
        res = asyncio.run(pc_agent.execute_pc_step(svc, rt, task_id=1, run_id=1, step_idx=0, tool="pc.run_command",
                                                   args={"argv": [sys.executable, str(parent)], "cwd": str(rt.workspace_dir)},
                                                   actor="owner", approved=True))
        assert res.get("exit_code") == 0, res
        child = int(str(res.get("stdout", "")).strip().split()[-1])
        assert _wait_gone(child), "the detached descendant outlived its finished pc.run_command step"
        assert psutil.pid_exists(bystander.pid) and bystander.poll() is None        # negative control: other processes live
    finally:
        bystander.kill()
