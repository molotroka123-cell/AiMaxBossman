"""Lane-authored behaviour tests: one test per `bossman chat` slash command (capability-tree leaves slash-*).

Real Chat + real slash parser + real View; only the Command Center HTTP API is an in-memory fake
(the dependency, never the unit under test). Each test id is `test_slash_<name>` so a leaf can be
proven by `-k test_slash_<name>`. No product code is touched.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("rich", reason="rich is a runtime dependency of the terminal client")

from bcc.terminal_cli import slash  # noqa: E402
from bcc.terminal_cli.api_client import BossmanError  # noqa: E402

from .test_terminal_chat_claude_parity import AGENT, FakeClient, make_chat  # noqa: E402


class Api(FakeClient):
    """FakeClient plus the extra routes the slash commands read and write."""

    def __init__(self, data_dir):
        super().__init__(data_dir)
        self.patches: list[tuple[str, dict]] = []
        self.post_routes: dict[str, object] = {}

    def post(self, path, body=None):
        if path in self.post_routes:
            self.posts.append((path, body or {}))
            value = self.post_routes[path]
            if isinstance(value, BossmanError):
                raise value
            return value
        return super().post(path, body)

    def patch(self, path, body=None):
        self.patches.append((path, body or {}))
        return {"ok": True}


def chat_for(tmp_path):
    chat, client, buf = make_chat(tmp_path, Api(tmp_path / "data"))
    client.posts.clear()                               # setup traffic of Chat.__init__ is not under test
    return chat, client, buf


def run(chat, line):
    parsed = slash.parse(line)
    assert parsed.kind == "command", line
    getattr(chat, "cmd_" + parsed.name)(parsed)


def test_slash_help(tmp_path):
    chat, _, buf = chat_for(tmp_path)
    run(chat, "/help")
    out = buf.getvalue()
    for name, (usage, text) in slash.COMMANDS.items():
        assert usage in out and text in out, name
    assert slash.parse("/?").name == "help"          # alias


def test_slash_status(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    client.routes["/api/tasks"] = [{"id": 5, "status": "running", "title": "t"},
                                   {"id": 6, "status": "paused", "title": "u"}]
    run(chat, "/status")
    out = buf.getvalue()
    assert "активных задач: 2" in out and "#5 running" in out and "#6 paused" in out
    assert "1.2.0" in out                              # identity of the connected build in the header


def test_slash_tasks(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    client.routes["/api/tasks"] = [{"id": 11, "status": "completed", "title": "первая"},
                                   {"id": 12, "status": "failed", "title": "вторая"}]
    run(chat, "/tasks 5")
    out = buf.getvalue()
    assert "#11" in out and "completed" in out and "первая" in out and "#12" in out and "вторая" in out


def test_slash_models(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    run(chat, "/models")
    out = buf.getvalue()
    assert "qwen-local" in out and "claude-cloud" in out
    run(chat, "/models use claude-cloud")
    assert client.patches == [(f"/api/agents/{AGENT['id']}", {"model_id": 8})]
    assert "claude-cloud" in buf.getvalue() and "постоянная настройка" in buf.getvalue()
    buf.truncate(0), buf.seek(0)
    run(chat, "/models use нет-такой")                 # unknown model: error, nothing patched
    assert "модель не найдена" in buf.getvalue() and len(client.patches) == 1


def test_slash_agent(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    run(chat, "/agent")
    assert "Основной" in buf.getvalue()
    run(chat, "/agent Основной")
    assert "агент: Основной" in buf.getvalue()
    buf.truncate(0), buf.seek(0)
    run(chat, "/agent призрак")
    assert "агент не найден" in buf.getvalue()


def test_slash_skills(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    client.routes["/api/skills"] = [{"id": "sk-a", "status": "prepared", "source": "x"},
                                    {"id": "sk-b", "status": "prepared", "source": "y"}]
    client.routes["/api/skill-catalog/select"] = [{"id": "sk-a", "title": "Навык A", "score": 3, "status": "prepared"}]
    run(chat, "/skills")
    out = buf.getvalue()
    assert "prepared: 2" in out and "sk-a" in out and "sk-b" in out
    buf.truncate(0), buf.seek(0)
    run(chat, "/skills починить тесты")
    assert "sk-a — Навык A" in buf.getvalue() and "score 3" in buf.getvalue()


def test_slash_tools(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    run(chat, "/tools")
    assert "у агента нет инструментов" in buf.getvalue()
    chat.agent = dict(AGENT, tools=["fs.read", "shell.exec"])
    client.routes["/api/capabilities"] = {"capabilities": [
        {"tool": "fs.read", "approval_requirement": "auto"},
        {"tool": "shell.exec", "approval_requirement": "deny"}]}
    chat._caps = None
    buf.truncate(0), buf.seek(0)
    run(chat, "/permissions")                          # alias of /tools
    out = buf.getvalue()
    assert re.search(r"fs\.read\s+\S+", out) and re.search(r"shell\.exec\s+\S+", out)
    assert out.index("fs.read") < out.index("shell.exec")


def test_slash_memory(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    run(chat, "/memory")                               # no query: usage, no backend call
    assert "/memory <запрос>" in buf.getvalue() and not client.posts
    client.post_routes["/api/memory/search"] = {"items": [
        {"source": "notes.md", "heading": "Решение", "score": 0.9, "content": "владелец выбрал вариант Б"}]}
    client.routes["/api/memory/facts"] = {"items": [{"id": 3, "statement": "Любит краткие ответы"}]}
    buf.truncate(0), buf.seek(0)
    run(chat, "/memory вариант")
    out = buf.getvalue()
    assert client.posts[-1] == ("/api/memory/search", {"query": "вариант"})
    assert "notes.md" in out and "владелец выбрал вариант Б" in out and "факт #3" in out


def test_slash_diff(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    client.routes["/api/coding-tasks"] = {"items": []}
    run(chat, "/diff")
    assert "coding-задач нет" in buf.getvalue()
    client.routes["/api/coding-tasks"] = {"items": [{"id": "ct-1"}]}
    client.routes["/api/coding-tasks/ct-1"] = {"status": "done", "diff": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n"}
    buf.truncate(0), buf.seek(0)
    run(chat, "/diff")
    out = buf.getvalue()
    assert "a = 2" in out and "a = 1" in out


def test_slash_code(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    run(chat, "/code почини сложение")                 # no --allow: refused, nothing submitted
    out = buf.getvalue()
    assert "область правок (--allow) задаётся явно" in out
    assert not [p for p, _ in client.posts if "coding" in p]
    assert chat.mode == "chat"
    p = slash.parse("/code --allow src\\calc.py --verify tests\\t.py почини")
    assert p.args[:2] == ["--allow", "src\\calc.py"]    # Windows path survives the parser


def test_slash_rave(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    client.routes["/api/rave"] = {"items": [{"id": "rv-1", "status": "running", "created_at": 1759744800.0,
                                              "prompt": "сделай калькулятор", "agents": {"mock:a": "done"}}]}
    run(chat, "/rave list")
    out = buf.getvalue()
    assert "rv-1" in out and "running" in out and "сделай калькулятор" in out and "mock:a:done" in out
    buf.truncate(0), buf.seek(0)
    client.routes["/api/rave"] = {"items": []}
    run(chat, "/rave list")
    assert "рейвов нет" in buf.getvalue()


def test_slash_approve(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    client.routes["/api/approvals"] = [{"id": 9, "task_id": 1, "kind": "shell", "preview": "rm x"}]
    client.post_routes["/api/approvals/9"] = {"status": "approved"}
    run(chat, "/approve")                              # exactly one pending: that one
    assert client.posts[-1] == ("/api/approvals/9", {"approve": True, "by": "owner:terminal"})
    assert "разрешение #9: approved" in buf.getvalue()
    n = len(client.posts)
    buf.truncate(0), buf.seek(0)
    run(chat, "/approve девять")                       # a typo must never approve the single pending one
    assert len(client.posts) == n and "id — число" in buf.getvalue()


def test_slash_deny(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    client.post_routes["/api/approvals/4"] = {"status": "denied"}
    run(chat, "/deny 4")
    assert client.posts[-1] == ("/api/approvals/4", {"approve": False, "by": "owner:terminal"})
    assert "разрешение #4: denied" in buf.getvalue()
    n = len(client.posts)
    buf.truncate(0), buf.seek(0)
    run(chat, "/deny")                                 # nothing pending: explicit message, no call
    assert len(client.posts) == n and "ожидающих разрешений нет" in buf.getvalue()


def test_slash_approvals(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    run(chat, "/approvals")
    assert "ожидающих разрешений нет" in buf.getvalue()
    client.routes["/api/approvals"] = [{"id": 2, "task_id": 7, "kind": "shell", "preview": "git push\x1b[31m"}]
    buf.truncate(0), buf.seek(0)
    run(chat, "/approvals")
    out = buf.getvalue()
    assert "#2" in out and "задача 7" in out and "git push" in out and "\x1b" not in out


def _task_action(tmp_path, name, status):
    chat, client, buf = chat_for(tmp_path)
    client.post_routes[f"/api/tasks/21/{name}"] = {"status": status}
    run(chat, f"/{name} 21")
    assert client.posts[-1][0] == f"/api/tasks/21/{name}"
    assert f"задача 21: {name} → {status}" in buf.getvalue()
    n = len(client.posts)
    buf.truncate(0), buf.seek(0)
    run(chat, f"/{name} x1")                           # a typo is not "the last task"
    assert len(client.posts) == n and f"/{name} <task>" in buf.getvalue()


def test_slash_pause(tmp_path):
    _task_action(tmp_path, "pause", "paused")


def test_slash_resume(tmp_path):
    _task_action(tmp_path, "resume", "running")


def test_slash_stop(tmp_path):
    _task_action(tmp_path, "stop", "stopped")
    chat, client, buf = chat_for(tmp_path)             # `/stop all` is the global STOP, not a task id
    client.post_routes["/api/control-plane/stop-all"] = {"ok": True, "stopped": {"tasks": [1, 2]}, "remaining": {}, "errors": []}
    run(chat, "/stop all")
    assert "/api/control-plane/stop-all" in [p for p, _ in client.posts]


def test_slash_computer(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    client.routes["/api/computer/status"] = {"available": True, "stopped": False, "busy": False, "detail": "ok"}
    run(chat, "/computer")
    assert "доступно: True" in buf.getvalue() and "STOP: False" in buf.getvalue()
    client.post_routes["/api/computer/stop"] = {"persisted": True}
    client.post_routes["/api/computer/resume"] = {}
    buf.truncate(0), buf.seek(0)
    run(chat, "/computer stop")
    assert "STOP (сохранён)" in buf.getvalue()
    run(chat, "/computer resume")
    assert "продолжено" in buf.getvalue()
    assert [p for p, _ in client.posts] == ["/api/computer/stop", "/api/computer/resume"]


def test_slash_evolve(tmp_path, capsys):
    chat, client, buf = chat_for(tmp_path)
    client.routes["/api/evolution/status"] = {"phase": "VERIFY", "cycles": 3}
    run(chat, "/evolve")
    out = capsys.readouterr().out
    assert '"phase": "VERIFY"' in out and '"cycles": 3' in out
    assert slash.parse("/evolution report").name == "evolve"
    client.routes["/api/evolution/report"] = BossmanError("нет", status=404, kind="not_found")
    buf.truncate(0), buf.seek(0)
    with pytest.raises(BossmanError, match="evolution report"):   # honest 'not in this build', never fake data
        run(chat, "/evolve report")


def test_slash_keys(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    run(chat, "/keys set openai sk-live-secret-value")  # a secret in the command line is refused
    out = buf.getvalue()
    assert "ключ в строке команды не принимаю" in out and "sk-live-secret-value" not in out
    assert not client.posts and not client.patches
    buf.truncate(0), buf.seek(0)
    run(chat, "/keys выбросить")                       # unknown action: usage
    assert "/keys [set <vendor>" in buf.getvalue()


def test_slash_panel(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    client.routes["/api/memory/stats"] = {"stats": {"files": 4, "chunks": 40}}
    client.routes["/api/memory/facts"] = {"total": 7, "items": []}
    run(chat, "/panel")
    out = buf.getvalue()
    for title in ("Workspace", "Active Task", "Tools Enabled", "Memory (summary)", "Budget (session)", "Model"):
        assert title in out, title
    assert "4 заметок" in out and "7 фактов" in out and "qwen-local" in out


def test_slash_compact(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    run(chat, "/compact")                              # nothing to compact: says so, creates no task
    assert "сжимать нечего" in buf.getvalue() and not client.posts
    chat.agent = None
    buf.truncate(0), buf.seek(0)
    run(chat, "/compact")
    assert "нет агента с моделью" in buf.getvalue()


def test_slash_context(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    run(chat, "/context")
    out = buf.getvalue()
    assert "резюме /compact" in out and "нет" in out and "ходов в контексте" in out and " 0 (" in out
    assert "окно контекста модели" in out and "32768" in out


def test_slash_cost(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    run(chat, "/cost")
    assert "в этой сессии задач ещё нет" in buf.getvalue()
    client.add_task(1, prompt="p", result="r", runs=[{"id": 1, "status": "completed", "model_alias": "claude-cloud",
                                                      "tokens_in": 2000, "tokens_out": 500, "cost_usd": 0.5}])
    chat.session.add(1, "p")
    buf.truncate(0), buf.seek(0)
    run(chat, "/usage")
    assert "$0.50" in buf.getvalue() and "итого за сессию: 1 задач" in buf.getvalue()


def test_slash_export(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    client.add_task(1, prompt="p", result="ответ")
    chat.session.add(1, "вопрос")
    target = tmp_path / "e.md"
    run(chat, f'/export "{target}"')
    text = target.read_text(encoding="utf-8")
    assert "вопрос" in text and "ответ" in text and "беседа сохранена" in buf.getvalue()
    buf.truncate(0), buf.seek(0)
    run(chat, f'/export "{target}"')                    # never silently overwrites
    assert "файл уже есть" in buf.getvalue()


def test_slash_doctor(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    client.routes["/api/coding-tasks/readiness"] = {"available": True}
    client.routes["/api/memory/config"] = {"configured": True, "backend": "fts"}
    client.routes["/api/computer/status"] = {"stopped": True}
    run(chat, "/doctor")
    out = buf.getvalue()
    assert "abc1234" in out and "готов" in out and "подключена (fts)" in out and "STOP" in out
    assert "ждут разрешения" in out and "Основной / qwen-local" in out


def test_slash_expand(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    run(chat, "/expand")
    assert "разворачивать нечего" in buf.getvalue()
    chat.view.state.blocks.append({"kind": "thinking", "name": "", "text": "строка1\nстрока2"})
    buf.truncate(0), buf.seek(0)
    run(chat, "/expand 1")
    out = buf.getvalue()
    assert "[1] thinking" in out and "строка1" in out and "строка2" in out
    buf.truncate(0), buf.seek(0)
    run(chat, "/expand 9")
    assert "нет блока 9; всего 1" in buf.getvalue()


def test_slash_history(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    chat.history_on = True
    run(chat, "/history")
    assert "включена" in buf.getvalue() and "history.txt" in buf.getvalue()
    run(chat, "/history off")
    assert chat.history_on is False and "не сохраняется" in buf.getvalue()
    run(chat, "/history on")
    assert chat.history_on is True


def test_slash_clear(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    run(chat, "/clear")                                # clears and reprints the header of the connected build
    assert "1.2.0" in buf.getvalue()


def test_slash_exit(tmp_path):
    chat, client, buf = chat_for(tmp_path)
    for alias in ("/exit", "/quit", "/q"):
        assert slash.parse(alias).name == "exit"
    assert chat.exit_chat() == 0                       # tasks keep running; session id is printed for resume
    assert chat.session.id in buf.getvalue() and "bossman resume" in buf.getvalue()
