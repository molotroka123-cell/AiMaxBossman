"""Unit tests for bcc.code_mode catalog, validator and sandbox."""
from __future__ import annotations

import ast
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from bcc.code_mode.catalog import ToolCatalog, estimate_tokens, py_identifier
from bcc.code_mode.sandbox import (
    AbortRun,
    SandboxLimits,
    SandboxOutcome,
    run_sandboxed,
)
from bcc.code_mode import _child
from bcc.code_mode._child import CodeRejected, validate_code


# ----------------------------------------------------------------- fixtures

@dataclass
class FakeSpec:
    name: str
    api_name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    required: list = field(default_factory=list)
    context_deny: Any = None


def _api(name: str) -> str:
    return py_identifier(name)


def _spec(name: str, desc: str, schema: dict | None = None,
          required: list | None = None) -> FakeSpec:
    return FakeSpec(
        name=name,
        api_name=_api(name),
        description=desc,
        input_schema=schema or {},
        required=required or [],
    )


def _tool_specs() -> list[FakeSpec]:
    return [
        _spec("terminal.run", "Run a shell command in the terminal and return its output",
              {"command": {"type": "string", "description": "shell command to run"}},
              ["command"]),
        _spec("terminal.kill", "Kill a running terminal process by id"),
        _spec("fs.read", "Read a file from disk. Прочитать файл с диска",
              {"path": {"type": "string", "description": "file path to read"},
               "encoding": {"type": "string"}},
              ["path"]),
        _spec("fs.write", "Write text content to a file on disk",
              {"path": {"type": "string"}, "text": {"type": "string"}},
              ["path", "text"]),
        _spec("fs.search", "Search files by name pattern",
              {"pattern": {"type": "string"}}, ["pattern"]),
        _spec("fs.delete", "Delete a file from disk"),
        _spec("git.status", "Show git working tree status"),
        _spec("git.commit", "Commit staged changes to the git repository",
              {"message": {"type": "string", "description": "commit message"}},
              ["message"]),
        _spec("git.push", "Push commits to the remote git repository"),
        _spec("browser.open", "Open a web page in the browser",
              {"url": {"type": "string"}}, ["url"]),
        _spec("browser.click", "Click an element on the open page",
              {"selector": {"type": "string"}}, ["selector"]),
        _spec("browser.screenshot", "Take a screenshot of the current page",
              {"full_page": {"type": "boolean"}}),
        _spec("browser.type", "Type text into a field on the page"),
        _spec("plugin:gmail.send", "Send an email message via gmail",
              {"to": {"type": "string"}, "subject": {"type": "string"},
               "body": {"type": "string"}},
              ["to", "subject"]),
        _spec("plugin:gmail.read", "Read email messages from the gmail inbox"),
        _spec("plugin:calendar.create", "Create a calendar event at a given time",
              {"title": {"type": "string"}, "when": {"type": "string"}},
              ["title"]),
        _spec("plugin:calendar.list", "List upcoming calendar events"),
        _spec("memory.search", "Search stored memories and facts by query",
              {"query": {"type": "string"}}, ["query"]),
        _spec("memory.write", "Remember a fact: write a memory for later",
              {"fact": {"type": "string"}}, ["fact"]),
        _spec("web.search", "Search the web and return results",
              {"query": {"type": "string"}}, ["query"]),
        _spec("web.fetch", "Fetch a web page and return its text"),
        _spec("http.get", "Perform an HTTP GET request to a url",
              {"url": {"type": "string"}}, ["url"]),
        _spec("video.clip.trim", "Trim a video clip between start and end",
              {"start": {"type": "number"}, "end": {"type": "number"}},
              ["start", "end"]),
        _spec("video.export.start", "Start exporting a rendered video file"),
        _spec("computer.act", "Perform an action on the computer screen",
              {"action": {"type": "string"}}, ["action"]),
        _spec("computer.observe", "Observe the computer screen and describe it"),
        _spec("telegram.send", "Send a telegram message to a chat",
              {"chat": {"type": "string"}, "text": {"type": "string"}},
              ["chat", "text"]),
        _spec("telegram.read", "Read recent telegram messages"),
        _spec("notes.append", "Append a line to the notes document"),
        _spec("calc.compute", "Evaluate an arithmetic expression safely",
              {"expr": {"type": "string"}}, ["expr"]),
    ]


def _catalog(**effects: str) -> ToolCatalog:
    return ToolCatalog(_tool_specs(), effects=effects or None)


# ------------------------------------------------------------------ A. catalog

@pytest.mark.parametrize("query,expected", [
    ("run a shell command", "terminal.run"),
    ("send an email", "plugin:gmail.send"),
    ("read a file", "fs.read"),
    ("take a screenshot of the page", "browser.screenshot"),
    ("search the web", "web.search"),
    ("remember this fact", "memory.write"),
    ("прочитай файл", "fs.read"),
    ("commit changes to git", "git.commit"),
    ("create a calendar event", "plugin:calendar.create"),
    ("trim a video clip", "video.clip.trim"),
    ("click on the page", "browser.click"),
    ("send a telegram message", "telegram.send"),
])
def test_search_ranking(query: str, expected: str) -> None:
    cat = _catalog()
    results = cat.search(query, k=5)
    names = [e.spec.name for e in results]
    assert expected in names[:3], f"{query!r} -> {names}"


def test_gibberish_query_and_families_render() -> None:
    cat = _catalog()
    assert cat.search("zzzqqqxxx wubbawubba", k=5) == []
    rendered = cat.render_search("zzzqqqxxx wubbawubba")
    assert "no tools match" in rendered
    assert "Tool families" in rendered
    assert "terminal(" in rendered


def test_effects_deny_and_ask() -> None:
    cat = _catalog(**{"fs.delete": "deny", "computer.act": "ask"})
    py_names = cat.py_names()
    assert "fs_delete" not in py_names
    assert cat.search("delete a file", k=10) == [
        e for e in cat.search("delete a file", k=10) if e.spec.name != "fs.delete"
    ]
    assert all(e.spec.name != "fs.delete" for e in cat.entries)
    act = cat.entry_for_py("computer_act")
    assert act is not None and act.effect == "ask"
    stub = cat.stub(act)
    assert "[needs owner approval]" in stub


def test_py_names_mapping_and_collisions() -> None:
    cat = _catalog()
    mapping = cat.py_names()
    assert mapping["terminal_run"] == "terminal_run"  # py_name -> api_name
    assert mapping["plugin_gmail_send"] == "plugin_gmail_send"
    for py_name in mapping:
        assert py_name.isidentifier(), py_name

    pair = [
        FakeSpec(name="my-tool", api_name="my_tool", description="one"),
        FakeSpec(name="my_tool", api_name="my_tool", description="two"),
    ]
    cat2 = ToolCatalog(pair)
    names = sorted(cat2.py_names())
    assert len(set(names)) == 2
    assert names == ["my_tool", "my_tool_2"]
    assert all(n.isidentifier() for n in names)


def test_stub_structure() -> None:
    cat = _catalog()
    entry = cat.entry_for_py("fs_read")
    assert entry is not None
    stub = cat.stub(entry)
    tree = ast.parse(stub)
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef) and fn.name == "fs_read"
    arg_names = [a.arg for a in fn.args.args]
    assert arg_names[0] == "path"
    assert arg_names[1] == "encoding"
    defaults = fn.args.defaults
    assert len(defaults) == 1
    assert defaults[0].value is None
    lines = stub.splitlines()
    assert len(lines) == 2  # signature + one-line docstring
    assert lines[1].lstrip().startswith('"""')
    assert "path: str" in lines[0]
    assert "encoding: str | None = None" in lines[0]


def test_k_clamped() -> None:
    cat = _catalog()
    few = cat.search("send message", k=0)
    assert len(few) == 1
    many = cat.search("send message", k=999)
    assert 1 <= len(many) <= 10


def test_len_and_estimate_tokens() -> None:
    cat = _catalog()
    assert len(cat) == len(_tool_specs())
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 401) == 100


# ------------------------------------------------------- B. sandbox validator

TOOLS = frozenset({"echo"})

_REJECTED = [
    "import os",
    "from os import path",
    "__import__('os')",
    "open('x')",
    "().__class__",
    "x = 1\nx.__class__",
    "eval('1')",
    "exec('1')",
    "getattr(1, 'real')",
    "class A: pass",
    "with open('f') as f: pass",
    "lambda: (yield)",
    "2 ** 10",
    "tools.nope()",
    "t = tools",
    "tools = 1",
    "def f(tools): pass",
    '"{0.__class__}".format(1)',
    "(1).real",
    '"a" * 10**9',
    "try:\n    pass\nexcept:\n    pass",
    "try:\n    pass\nexcept BaseException:\n    pass",
    "global x",
    "async def f(): pass",
    "await x",
    "x=1;\n" * 6000,  # > 20000 chars
]

_ACCEPTED = [
    "r = tools.echo(text='a')\nprint(r['content'])",
    "out = [i * 2 for i in range(5)]\nfor v in out:\n    print(f'v={v}')",
    "d = {}\nd.setdefault('a', []).append(1)",
    "id = 3\ntype = 'x'",
]


@pytest.mark.parametrize("code", _REJECTED, ids=[f"rej{i}" for i in range(len(_REJECTED))])
def test_validator_rejects(code: str) -> None:
    with pytest.raises(CodeRejected):
        validate_code(code, TOOLS)


@pytest.mark.parametrize("code", _ACCEPTED, ids=[f"acc{i}" for i in range(len(_ACCEPTED))])
def test_validator_accepts(code: str) -> None:
    validate_code(code, TOOLS)


# ------------------------------------------------------- C. sandbox execution

def _limits(**kw: Any) -> SandboxLimits:
    base = dict(wall_seconds=5, max_calls=25, max_output_chars=8000,
                max_arg_chars=100_000, max_result_chars=200_000, memory_mb=256)
    base.update(kw)
    return SandboxLimits(**base)


def _host_factory(calls: list):
    async def host(api_name: str, args: dict) -> dict:
        calls.append(api_name)
        if api_name == "boom":
            raise AbortRun("approval", "ask")
        if api_name == "fail":
            raise RuntimeError("kaput")
        return {"ok": True, "content": "echo:" + str(args.get("text"))}
    return host


async def test_basic_round_trip() -> None:
    calls: list = []
    code = "r = tools.echo(text='hi')\nprint(r['content'])\nr"
    out = await run_sandboxed(code, {"echo": "echo"}, _host_factory(calls), _limits())
    assert out.status == "ok", out.error
    assert "echo:hi" in out.output
    assert out.value == {"ok": True, "content": "echo:hi"}
    assert out.calls == 1 and calls == ["echo"]


async def test_rejected_code_never_calls_host() -> None:
    calls: list = []
    out = await run_sandboxed("import os", {"echo": "echo"}, _host_factory(calls), _limits())
    assert out.status == "rejected"
    assert calls == []


async def test_escape_attempts_rejected() -> None:
    calls: list = []
    for snippet in ("import os", "().__class__", "open('x')"):
        out = await run_sandboxed(snippet, {"echo": "echo"}, _host_factory(calls), _limits())
        assert out.status == "rejected", snippet
    assert calls == []


async def test_infinite_loop_times_out() -> None:
    calls: list = []
    started = time.monotonic()
    out = await run_sandboxed(
        "while True: pass", {"echo": "echo"}, _host_factory(calls),
        _limits(wall_seconds=2))
    elapsed = time.monotonic() - started
    assert out.status == "timeout"
    assert elapsed < 6.0, elapsed


async def test_abort_run_stops_code() -> None:
    calls: list = []
    code = "r = tools.boom()\nprint('after')"
    out = await run_sandboxed(code, {"boom": "boom"}, _host_factory(calls), _limits())
    assert out.status == "aborted"
    assert out.abort_kind == "ask"
    assert out.abort_reason == "approval"
    assert "after" not in out.output
    assert calls == ["boom"]


async def test_output_cap_and_truncation() -> None:
    calls: list = []
    code = "for i in range(100000):\n    print('line', i)"
    out = await run_sandboxed(code, {"echo": "echo"}, _host_factory(calls),
                              _limits(max_output_chars=500))
    assert out.status == "ok"
    assert out.truncated is True
    assert len(out.output) <= 600


async def test_max_calls_limit() -> None:
    calls: list = []
    code = (
        "for i in range(10):\n"
        "    try:\n"
        "        tools.echo(text=str(i))\n"
        "    except Exception:\n"
        "        print('limit')\n"
        "        break\n"
    )
    out = await run_sandboxed(code, {"echo": "echo"}, _host_factory(calls),
                              _limits(max_calls=3))
    assert out.status == "ok", out.error
    assert "limit" in out.output
    assert len(calls) <= 3


async def test_tool_error_is_catchable() -> None:
    calls: list = []
    code = (
        "try:\n"
        "    tools.fail()\n"
        "except Exception as e:\n"
        "    print('caught')\n"
        "print('done')\n"
    )
    out = await run_sandboxed(code, {"fail": "fail"}, _host_factory(calls), _limits())
    assert out.status == "ok", out.error
    assert "caught" in out.output
    assert "done" in out.output


async def test_memory_bomb_killed() -> None:
    calls: list = []
    code = "x = [0] * 50000\ny = x * 50000\nprint(len(y))"
    out = await run_sandboxed(code, {"echo": "echo"}, _host_factory(calls),
                              _limits(memory_mb=128, wall_seconds=10))
    assert out.status in ("error", "violation")
    assert out.status != "ok"


def test_child_env_has_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from bcc.code_mode import sandbox
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-env-value-1")
    env = sandbox._child_env()
    assert "OPENROUTER_API_KEY" not in env
    assert env.get("PYTHONUTF8") == "1"


async def test_positional_tool_call_is_error() -> None:
    calls: list = []
    out = await run_sandboxed("tools.echo('x')", {"echo": "echo"},
                              _host_factory(calls), _limits())
    assert out.status == "error"
    assert "keyword" in out.error.lower()


async def test_cyrillic_output_round_trip() -> None:
    calls: list = []
    code = "print('Привет, мир — ёлка')"
    out = await run_sandboxed(code, {"echo": "echo"}, _host_factory(calls), _limits())
    assert out.status == "ok", out.error
    assert "Привет, мир — ёлка" in out.output
