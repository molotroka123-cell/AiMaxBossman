"""Classic Python-sandbox escape gadgets against the code-mode dialect.

Static layer: the validator must refuse them. Dynamic layer (defence in depth): anything the
validator lets through must still not run (`error`/`rejected`, never `ok`) because the names are not
in the restricted builtins. Plus resource/identity properties of the child process.
"""
from __future__ import annotations

import pytest

from bcc.code_mode import sandbox as sb
from bcc.code_mode._child import CodeRejected, validate_code
from bcc.code_mode.sandbox import SandboxLimits, run_sandboxed

TOOLS = frozenset({"echo"})

GADGETS = [
    "[].__class__.__base__.__subclasses__()",
    "''.__class__.__mro__[1].__subclasses__()",
    "(lambda: 0).__globals__",
    "(lambda: 0).__code__",
    "def f():\n    return 1\nf.__closure__",
    "g = (i for i in [1])\ng.gi_frame.f_globals",
    "[x for x in [1]].__class__",
    "f'{().__class__}'",
    "tools.echo.__globals__",
    "print.__self__",
    "str.__dict__",
    "dict.fromkeys",
    "'%s' % ().__class__",
    "try:\n    1/0\nexcept Exception as e:\n    e.__traceback__.tb_frame",
    "chr(65)",
    "type(1)",
    "vars()",
    "__builtins__",
    "x = __builtins__",
    "breakpoint()",
    "super()",
    "memoryview(b'x')",
    "b'abc'",
    "1j",
    "...",
    "tools.echo(text=tools)",
    "[tools][0].echo(text='x')",
    "def tools(): pass",
    "for tools in [1]: pass",
    "lambda tools: tools.echo",
]


async def _host(name, args):
    return {"ok": True, "content": "echo"}


@pytest.mark.parametrize("code", GADGETS, ids=[f"g{i}" for i in range(len(GADGETS))])
async def test_escape_gadget_is_refused_statically_or_at_least_fails_at_runtime(code):
    try:
        validate_code(code, TOOLS)
    except CodeRejected:
        return
    outcome = await run_sandboxed(code, {"echo": "echo"}, _host, SandboxLimits(wall_seconds=5))
    assert outcome.status in ("error", "rejected"), (code, outcome)


async def test_the_sandbox_has_no_interpreter_internals_even_via_a_tool_result():
    """A tool result is plain JSON data (dict of str/bool): no object from the parent leaks in."""
    async def host(name, args):
        return {"ok": True, "content": "x", "data": object()}      # extra keys are dropped by the bridge
    out = await run_sandboxed("r = tools.echo(text='a')\nsorted(r.keys())", {"echo": "echo"}, host)
    assert out.status == "ok"
    assert out.value == ["content", "ok"]


async def test_child_environment_and_working_directory_are_clean(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-env-value-1")
    monkeypatch.setenv("BCC_TOKEN", "dummy-env-value-2")
    env = sb._child_env()
    assert "OPENROUTER_API_KEY" not in env and "BCC_TOKEN" not in env
    assert set(env) <= {"PYTHONIOENCODING", "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE", "SYSTEMROOT",
                        "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}


_EVIL_CHILDREN = {
    # asks for a tool that was never offered
    "unlisted": 'print(json.dumps({"t": "call", "id": 1, "tool": "evil", "args": {}}), flush=True)',
    # skips the call id sequence
    "bad_id": 'print(json.dumps({"t": "call", "id": 9, "tool": "echo", "args": {}}), flush=True)',
    # arguments that are not an object
    "bad_args": 'print(json.dumps({"t": "call", "id": 1, "tool": "echo", "args": [1]}), flush=True)',
    # not JSON at all
    "garbage": 'print("this is not json", flush=True)',
    # an unknown message type
    "unknown_type": 'print(json.dumps({"t": "exec", "cmd": "calc"}), flush=True)',
}


@pytest.mark.parametrize("kind", sorted(_EVIL_CHILDREN))
async def test_a_compromised_child_cannot_make_the_parent_call_anything(kind, monkeypatch, tmp_path):
    """The parent trusts nothing the child says beyond the line protocol: a bad message kills the
    child and ends the run as a violation; the host callback (= the engine pipeline) is not reached."""
    evil = tmp_path / "evil_child.py"
    evil.write_text("import json, sys\nsys.stdin.readline()\n" + _EVIL_CHILDREN[kind] + "\nsys.stdin.readline()\n",
                    encoding="utf-8")
    monkeypatch.setattr(sb, "CHILD_PATH", evil)
    calls = []

    async def host(name, args):
        calls.append(name)
        return {"ok": True, "content": ""}
    out = await run_sandboxed("print(1)", {"echo": "echo"}, host, SandboxLimits(wall_seconds=5))
    assert out.status == "violation", out
    assert calls == []


async def test_a_call_limit_is_enforced_by_the_parent_too(monkeypatch, tmp_path):
    evil = tmp_path / "chatty_child.py"
    evil.write_text(
        "import json, sys\nsys.stdin.readline()\n"
        "for i in range(1, 50):\n"
        "    print(json.dumps({'t': 'call', 'id': i, 'tool': 'echo', 'args': {}}), flush=True)\n"
        "    sys.stdin.readline()\n", encoding="utf-8")
    monkeypatch.setattr(sb, "CHILD_PATH", evil)
    calls = []

    async def host(name, args):
        calls.append(name)
        return {"ok": True, "content": ""}
    out = await run_sandboxed("print(1)", {"echo": "echo"}, host, SandboxLimits(wall_seconds=5, max_calls=3))
    assert out.status == "violation" and len(calls) == 3


async def test_the_child_is_killed_after_the_run(monkeypatch):
    pids = []
    real = sb._spawn

    async def spy(workdir, memory_mb):
        proc, job = await real(workdir, memory_mb)
        pids.append(proc)
        return proc, job
    monkeypatch.setattr(sb, "_spawn", spy)
    await run_sandboxed("print(1)", {}, _host)
    await run_sandboxed("while True:\n    pass", {}, _host, SandboxLimits(wall_seconds=1))
    assert len(pids) == 2 and all(p.returncode is not None for p in pids)
