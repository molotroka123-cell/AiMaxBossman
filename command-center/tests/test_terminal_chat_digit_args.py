"""`bossman chat`: числовой аргумент команды проверялся `str.isdigit()`, а он
пропускает «²», «①» и т.п., которые `int()` не принимает → ValueError в /tasks,
/approve, /deny, /pause, /resume, /stop, /expand. Цикл чата ловит только
BossmanError, поэтому одна опечатка закрывала всю беседу.
"""
from __future__ import annotations

import io
import re
import sys

import pytest

pytest.importorskip("rich", reason="rich is a runtime dependency of the terminal client")

from bcc.terminal_cli import slash  # noqa: E402
from bcc.terminal_cli.chat import EXIT_OK  # noqa: E402

from .test_terminal_chat_claude_parity import FakeClient, make_chat  # noqa: E402


def _run_lines(chat, monkeypatch, *lines: str) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(ln + "\n" for ln in lines)))
    return chat.run()


@pytest.mark.parametrize("line", ["/tasks ²", "/approve ²", "/deny ①", "/pause ²", "/resume ²",
                                  "/stop ²", "/expand ²", "/stop abc", "/approve abc", "/deny 1x"])
def test_bad_numeric_argument_is_refused_not_a_crash_and_not_a_guess(tmp_path, monkeypatch, line):
    client = FakeClient(tmp_path / "data")
    client.routes["/api/tasks"] = []
    client.add_task(5, prompt="идущая задача", result=None, status="running")
    client.routes["/api/approvals"] = [{"id": 9, "task_id": 5, "kind": "tool", "preview": "x",
                                        "status": "pending"}]
    chat, client, buf = make_chat(tmp_path, client)
    chat.last_task = client.tasks[5]["task"]
    assert _run_lines(chat, monkeypatch, line, "/exit") == EXIT_OK
    # ни мусорный id, ни «последняя задача / единственное разрешение» вместо него
    acted = [p for p, _ in client.posts if re.fullmatch(r"/api/(approvals/\d+|tasks/\d+/\w+)", p)]
    assert acted == [], client.posts


def test_ascii_digit_arguments_and_defaults_still_work(tmp_path):
    client = FakeClient(tmp_path / "data")
    client.add_task(5, prompt="x", result="y")
    client.add_task(6, prompt="z", result=None, status="running")
    chat, client, _buf = make_chat(tmp_path, client)
    chat.cmd_stop(slash.parse("/stop 5"))
    assert client.posts[-1][0] == "/api/tasks/5/stop"
    chat.last_task = client.tasks[6]["task"]
    chat.cmd_stop(slash.parse("/stop"))               # без аргумента — последняя задача, как и раньше
    assert client.posts[-1][0] == "/api/tasks/6/stop"
