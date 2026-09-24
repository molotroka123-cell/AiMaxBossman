"""Claude-Code-style commands of `bossman chat` (owner request 2026-09-23):
/compact /context /cost /export /doctor and the /permissions, /usage aliases.

Everything goes through the SAME Bossman: /compact is an ordinary Bossman task of
the current agent; /cost and /export read the tasks back from the backend. The
fake client below stands in for the Command Center API.
"""
from __future__ import annotations

import io
import json
import os
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("rich", reason="rich is a runtime dependency of the terminal client")

from bcc import conversation_context  # noqa: E402
from bcc.terminal_cli import slash  # noqa: E402
from bcc.terminal_cli.api_client import BossmanError  # noqa: E402
from bcc.terminal_cli.chat import (SUMMARY_LABEL, Chat, Session, approx_tokens,  # noqa: E402
                                   context_preamble, export_target)
from bcc.terminal_cli.console import make_console  # noqa: E402
from bcc.terminal_cli.human import View  # noqa: E402

AGENT = {"id": 1, "name": "Основной", "enabled": True, "model_id": 7, "tools": []}
MODELS = [{"id": 7, "alias": "qwen-local", "name": "qwen3", "provider_id": 1, "context_window": 32768,
           "pricing_known": False},
          {"id": 8, "alias": "claude-cloud", "name": "claude", "provider_id": 2, "context_window": 200000,
           "pricing_known": True}]
PROVIDERS = [{"id": 1, "kind": "openai_compat", "base_url": "http://127.0.0.1:8080"},
             {"id": 2, "kind": "anthropic", "base_url": ""}]


class FakeClient:
    """Command Center API in memory. `routes` override GET answers; a value that is
    a BossmanError is raised (e.g. a 404 of an endpoint this build lacks)."""

    def __init__(self, data_dir: Path, *, compact_outcome=("completed", "Резюме: владелец считает 7*8=56.")):
        self.target = SimpleNamespace(url="http://127.0.0.1:8800", data_dir=data_dir,
                                      identity={"version": "1.2.0", "build_sha_short": "abc1234"})
        self.tasks: dict[int, dict] = {}
        self.routes: dict[str, object] = {}
        self.posts: list[tuple[str, dict]] = []
        self.compact_outcome = compact_outcome

    # history helpers
    def add_task(self, tid: int, *, prompt: str, result: str | None, status: str = "completed",
                 runs: list[dict] | None = None, title: str = "") -> None:
        self.tasks[tid] = {"task": {"id": tid, "status": status, "title": title or prompt[:40],
                                    "prompt": prompt},
                           "runs": runs if runs is not None else [], "result": result, "error": None}

    # API
    def get(self, path, params=None):
        if path in self.routes:
            value = self.routes[path]
            if isinstance(value, BossmanError):
                raise value
            return value
        if path == "/api/agents":
            return [AGENT]
        if path == "/api/models":
            return MODELS
        if path == "/api/providers":
            return PROVIDERS
        if path == "/api/approvals":
            return []
        m = re.fullmatch(r"/api/tasks/(\d+)", path)
        if m:
            tid = int(m.group(1))
            if tid not in self.tasks:
                raise BossmanError("задача не найдена", status=404, kind="not_found")
            return self.tasks[tid]
        raise BossmanError(f"GET {path}: HTTP 404", status=404, kind="not_supported")

    def post(self, path, body=None):
        self.posts.append((path, body or {}))
        if path == "/api/tasks/preflight":
            return {"ok": True, "agent": {"id": AGENT["id"]}}
        if path == "/api/tasks":
            tid = max(max(self.tasks, default=0), 100) + 1
            self.add_task(tid, prompt=body["prompt"], result=None, status="draft", title=body["title"])
            return {"task": self.tasks[tid]["task"], "replayed": False}
        m = re.fullmatch(r"/api/tasks/(\d+)/(run|stop)", path)
        if m:
            tid = int(m.group(1))
            if m.group(2) == "run":
                status, text = self.compact_outcome
                self.tasks[tid]["task"]["status"] = status
                self.tasks[tid]["runs"] = [{"id": 1, "status": status, "model_alias": "qwen-local",
                                            "tokens_in": 900, "tokens_out": 120, "cost_usd": 0.0}]
                if status == "completed":
                    self.tasks[tid]["result"] = text
                else:
                    self.tasks[tid]["error"] = text
            else:
                self.tasks[tid]["task"]["status"] = "stopped"
            return {"ok": True, "status": self.tasks[tid]["task"]["status"]}
        raise BossmanError(f"POST {path}: HTTP 404", status=404, kind="not_supported")

    def patch(self, path, body=None):
        raise BossmanError("не нужно в этих тестах")

    def stream_task(self, task_id, *, after, stop: threading.Event, read_timeout=45.0):
        yield {"kind": "stream.open"}
        stop.wait(5.0)                  # no live events: the follower's poll decides


def make_chat(tmp_path: Path, client: FakeClient | None = None, session: str | None = None):
    client = client or FakeClient(tmp_path / "data")
    buf = io.StringIO()
    view = View(make_console(stream=buf, plain=True, width=160), plain=True)
    args = SimpleNamespace(cwd=str(tmp_path), session=session, agent=None, no_history=True)
    return Chat(args, client, view), client, buf


def with_turns(chat: Chat, client: FakeClient, n: int) -> None:
    for i in range(1, n + 1):
        client.add_task(i, prompt=f"вопрос {i}", result=f"ответ {i}", title=f"вопрос {i}",
                        runs=[{"id": i, "status": "completed", "model_alias": "qwen-local",
                               "tokens_in": 100 * i, "tokens_out": 10 * i, "cost_usd": 0.0}])
        chat.session.add(i, f"вопрос {i}")


# ----------------------------------------------------------------- parsing


@pytest.mark.parametrize("line,name", [
    ("/compact", "compact"), ("/compact оставь только решения", "compact"), ("/context", "context"),
    ("/cost", "cost"), ("/usage", "cost"), ("/export", "export"), ("/export C:\\tmp\\a.md --force", "export"),
    ("/doctor", "doctor"), ("/permissions", "tools"),
])
def test_new_commands_and_aliases_parse(line, name):
    p = slash.parse(line)
    assert p.kind == "command" and p.name == name
    assert "/" + name in slash.completions() or name == "tools"


def test_help_registry_lists_the_new_commands():
    for name in ("compact", "context", "cost", "export", "doctor"):
        usage, text = slash.COMMANDS[name]
        assert usage.startswith("/" + name) and len(usage) <= 44 and text
    assert slash.parse("/export C:\\tmp\\a.md").args == ["C:\\tmp\\a.md"]
    assert slash.parse("/compact оставь решения").text == "оставь решения"


# ----------------------------------------------------------------- /compact


def test_compact_success_stores_summary_and_later_context_is_summary_plus_newer_turns(tmp_path):
    chat, client, buf = make_chat(tmp_path)
    with_turns(chat, client, 4)
    chat.cmd_compact(slash.parse("/compact сохрани числа"))

    task_id = max(client.tasks)
    compact_task = client.tasks[task_id]
    # an ordinary Bossman task of the current agent, in the chat's context format
    assert ("/api/tasks/%d/run" % task_id, {}) in client.posts
    body = next(b for p, b in client.posts if p == "/api/tasks")
    assert body["agent_id"] == AGENT["id"] and body["run_now"] is False
    prompt = compact_task["task"]["prompt"]
    assert prompt.startswith(conversation_context.HEADER)
    for i in range(1, 5):                                    # every turn, not only the last 3
        assert f"вопрос {i}" in prompt and f"ответ {i}" in prompt
    request = conversation_context.current_request(prompt)
    assert "резюме" in request and "вопрос 1" not in request
    assert "Указание владельца к резюме (/compact): сохрани числа" in prompt
    assert "сохрани числа" not in request                 # a hint for the summary, not an action

    # stored in the SAME session file
    assert chat.session.summary == "Резюме: владелец считает 7*8=56."
    assert chat.session.compacted_at_turn == 4 and chat.session.compact_tasks == [task_id]
    saved = json.loads(chat.session.path.read_text(encoding="utf-8"))
    assert saved["summary"] == chat.session.summary and saved["compacted_at_turn"] == 4
    out = buf.getvalue()
    assert "сжата" in out and "→" in out and "токенов" in out and "оценка" in out

    # the next message carries the summary and nothing older
    pre = context_preamble(client, chat.session)
    assert pre.startswith(conversation_context.HEADER + SUMMARY_LABEL + chat.session.summary)
    assert "вопрос 4" not in pre
    client.add_task(5, prompt="вопрос 5", result="ответ 5")
    chat.session.add(5, "вопрос 5")
    pre = context_preamble(client, chat.session)
    assert "вопрос 5" in pre and "ответ 5" in pre and "вопрос 3" not in pre
    assert pre.index(SUMMARY_LABEL) < pre.index("вопрос 5")
    assert conversation_context.current_request(pre + "Какое число?") == "Какое число?"

    reopened = Session.open(tmp_path / "data", chat.session.id)
    assert (reopened.summary, reopened.compacted_at_turn, reopened.compact_tasks) == \
        (chat.session.summary, 4, [task_id])


def test_compact_request_is_not_an_action_for_the_backend_checks(tmp_path):
    from bcc.features.action_contract import classify_all
    from bcc.features.action_router import classify as route_classify
    chat, client, _ = make_chat(tmp_path)
    client.add_task(1, prompt="x", result="запомнил")
    chat.session.add(1, "Запомни кодовое слово «Влтава» и открой в браузере https://example.org")
    from bcc.terminal_cli.chat import compact_prompt
    for instruction in ("", "запомни главное и открой ссылки в браузере"):
        prompt = compact_prompt(client, chat.session, instruction)
        assert "MEMORY_ACTION" not in {c.name for c in classify_all(prompt)}, instruction
        assert route_classify(prompt) is None, instruction


def test_compact_failure_leaves_the_session_unchanged(tmp_path):
    client = FakeClient(tmp_path / "data", compact_outcome=("failed", "модель недоступна"))
    chat, client, buf = make_chat(tmp_path, client)
    with_turns(chat, client, 2)
    before_file = chat.session.path.read_text(encoding="utf-8")
    before_pre = context_preamble(client, chat.session)
    chat.cmd_compact(slash.parse("/compact"))
    assert chat.session.summary == "" and chat.session.compacted_at_turn == 0
    assert chat.session.path.read_text(encoding="utf-8") == before_file
    assert context_preamble(client, chat.session) == before_pre
    out = buf.getvalue()
    assert "не удался" in out and "не изменена" in out and "модель недоступна" in out


def test_old_session_json_without_summary_still_loads(tmp_path):
    root = tmp_path / "data" / "terminal" / "sessions"
    root.mkdir(parents=True)
    (root / "old-1234.json").write_text(json.dumps(
        {"id": "old-1234", "turns": [{"task_id": 3, "text": "привет", "at": "2026-09-22T10:00:00"}]},
        ensure_ascii=False), encoding="utf-8")
    s = Session.open(tmp_path / "data", "old-1234")
    assert s.summary == "" and s.compacted_at_turn == 0 and s.compact_tasks == []
    assert s.live_turns() == s.turns
    s.add(4, "ещё")                                   # saving an old session keeps its shape
    assert "summary" not in json.loads((root / "old-1234.json").read_text(encoding="utf-8"))


# ----------------------------------------------------------------- /context


def test_context_shows_what_the_next_message_carries(tmp_path):
    chat, client, buf = make_chat(tmp_path)
    with_turns(chat, client, 5)
    chat.cmd_context(slash.parse("/context"))
    pre = context_preamble(client, chat.session)
    out = buf.getvalue()
    assert "резюме /compact" in out and "нет" in out
    assert "ходов в контексте" in out and " 3 (" in out and "всего в сессии 5" in out
    assert f"{len(pre)} символов" in out and f"≈{approx_tokens(len(pre))} токенов" in out
    assert "оценка" in out and "Основной" in out and "qwen-local" in out and "32768" in out

    chat.session.compacted("кратко", 99)
    buf.truncate(0), buf.seek(0)
    chat.cmd_context(slash.parse("/context"))
    out = buf.getvalue()
    assert "да (после хода 5" in out and " 0 (" in out


# ----------------------------------------------------------------- /cost


def test_cost_reads_tasks_back_and_shows_unknown_cost_as_dash(tmp_path):
    chat, client, buf = make_chat(tmp_path)
    with_turns(chat, client, 2)                        # local model: pricing unknown
    chat.cmd_cost(slash.parse("/usage"))
    out = buf.getvalue()
    assert "#1" in out and "#2" in out and "100→10 tok" in out and "200→20 tok" in out
    assert "$0" not in out and "—" in out
    assert "итого за сессию: 2 задач · 300→30 tok · —" in out and "цена неизвестна для 2 из 2" in out

    client.add_task(3, prompt="облако", result="ок", runs=[
        {"id": 3, "status": "completed", "model_alias": "claude-cloud", "tokens_in": 1000,
         "tokens_out": 100, "cost_usd": 0.25}])
    chat.session.turns = []
    chat.session.add(3, "облако")
    buf.truncate(0), buf.seek(0)
    chat.cmd_cost(slash.parse("/cost"))
    out = buf.getvalue()
    assert "$0.25" in out and "итого за сессию: 1 задач · 1.0K→100 tok · $0.25" in out


# ----------------------------------------------------------------- /export


def test_export_writes_markdown_refuses_overwrite_and_sanitizes(tmp_path):
    chat, client, buf = make_chat(tmp_path)
    client.add_task(1, prompt="x", result="ответ\x1b[2J\x1b]52;c;ZXZpbA==\x07 чистый")
    chat.session.add(1, "вопрос\x1b[31m красный")
    chat.cmd_export(slash.parse("/export"))
    path = tmp_path / "data" / "terminal" / "exports" / f"{chat.session.id}.md"
    text = path.read_text(encoding="utf-8")
    assert "\x1b" not in text and "\x07" not in text
    assert "вопрос красный" in text and "ответ чистый" in text and "задача #1" in text
    assert "**Владелец:**" in text and "**Bossman** (completed):" in text
    assert b"\r\n" not in path.read_bytes()

    path.write_text("владелец правил руками", encoding="utf-8")
    chat.cmd_export(slash.parse("/export"))
    assert path.read_text(encoding="utf-8") == "владелец правил руками"
    assert "файл уже есть" in buf.getvalue()
    chat.cmd_export(slash.parse("/export --force"))
    assert "ответ чистый" in path.read_text(encoding="utf-8")

    chat.cmd_export(slash.parse("/export out\\s.md"))      # relative to the chat's cwd
    assert (tmp_path / "out" / "s.md").is_file()


# The owner types Windows paths; the same command must mean the same file on the
# Linux CI runner and on the owner's Windows machine (release blocker 2026-09-24:
# `out\s.md` became ONE file named "out\s.md" on POSIX).
@pytest.mark.parametrize("raw", ["out\\s.md", "out/s.md", "out\\\\s.md", ".\\out\\s.md", "x\\..\\out/s.md"])
def test_export_target_treats_both_separators_as_separators(tmp_path, raw):
    assert export_target(raw, str(tmp_path)) == (tmp_path / "out" / "s.md").resolve()


@pytest.mark.parametrize("raw", ["..\\x.md", "../x.md", "a\\..\\..\\x.md", "a/../../x.md",
                                 "..\\..\\..\\..\\..\\..\\etc\\x.md"])
def test_export_target_refuses_relative_escape_from_cwd(tmp_path, raw):
    with pytest.raises(ValueError, match="за пределы"):
        export_target(raw, str(tmp_path / "work"))


def test_export_target_keeps_absolute_paths_absolute(tmp_path):
    target = tmp_path / "elsewhere" / "беседа.md"
    assert export_target(str(target), str(tmp_path / "work")) == target
    assert export_target(str(target).replace(os.sep, "\\") if os.name == "nt" else str(target),
                         str(tmp_path / "work")) == target


@pytest.mark.skipif(os.name == "nt", reason="drive letters are real on Windows")
def test_export_target_refuses_windows_drive_path_on_posix(tmp_path):
    with pytest.raises(ValueError, match="диск"):
        export_target("C:\\Users\\x.md", str(tmp_path))


@pytest.mark.skipif(os.name != "nt", reason="drive-relative / root-relative paths exist only on Windows")
@pytest.mark.parametrize("raw", ["C:x.md", "\\x.md", "/x.md"])
def test_export_target_refuses_ambiguous_windows_paths(tmp_path, raw):
    with pytest.raises(ValueError):
        export_target(raw, str(tmp_path))


def test_export_unicode_spaces_mixed_separators_and_directory(tmp_path):
    chat, client, buf = make_chat(tmp_path)
    client.add_task(1, prompt="x", result="ответ")
    chat.session.add(1, "вопрос")
    chat.cmd_export(slash.parse('/export "папка с пробелом\\вложенная/файл отчёта.md"'))
    written = tmp_path / "папка с пробелом" / "вложенная" / "файл отчёта.md"
    assert written.is_file() and b"\r\n" not in written.read_bytes()
    assert "ответ" in written.read_text(encoding="utf-8")

    chat.cmd_export(slash.parse("/export ..\\сбежал.md --force"))   # --force never allows escape
    assert not (tmp_path.parent / "сбежал.md").exists()
    assert "за пределы" in buf.getvalue()

    (tmp_path / "каталог").mkdir()
    chat.cmd_export(slash.parse("/export каталог --force"))          # a directory is not a file
    assert (tmp_path / "каталог").is_dir() and "каталог" in buf.getvalue()


# ----------------------------------------------------------------- /doctor


def test_doctor_tolerates_missing_endpoints(tmp_path):
    chat, client, buf = make_chat(tmp_path)
    # this build has none of the three endpoints: the fake answers 404 (not_supported)
    chat.cmd_doctor(slash.parse("/doctor"))
    out = buf.getvalue()
    assert "1.2.0" in out and "abc1234" in out and str(tmp_path / "data") in out
    assert "Основной / qwen-local (local)" in out
    assert out.count("эта сборка не сообщает") == 3 and "ждут разрешения" in out

    client.routes.update({"/api/coding-tasks/readiness": {"available": False, "reason": "нет git"},
                          "/api/memory/config": {"configured": True, "backend_class": "SqliteBackend"},
                          "/api/computer/status": {"available": True, "stopped": True},
                          "/api/approvals": [{"id": 1}, {"id": 2}]})
    buf.truncate(0), buf.seek(0)
    chat.cmd_doctor(slash.parse("/doctor"))
    out = buf.getvalue()
    assert "не готов: нет git" in out and "подключена (SqliteBackend)" in out and "STOP" in out
    assert re.search(r"ждут разрешения\s+2", out)
