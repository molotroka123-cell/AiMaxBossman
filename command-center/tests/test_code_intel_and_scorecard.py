"""Тесты code-intelligence (LSP-мост) и benchmark-aggregator.

LSP проверяется на РЕАЛЬНОМ subprocess-пути (argv-only) через крошечный
fake-LSP сервер, говорящий по Content-Length framing — без реального языкового
сервера, но с настоящим протоколом.
"""
from __future__ import annotations

import json
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

import bcc.features.code_intel as CI
from bcc.eval_scorecard import compare, load_jsonl, summarize
from bcc.lsp_bridge import LSPClient, LSPConfig, LSPError
from bcc.tools import REGISTRY, decide_effect

# ------------------------------------------------------------- fake LSP server

FAKE_LSP = textwrap.dedent('''
    import sys, json
    def read():
        length=None
        while True:
            line=sys.stdin.buffer.readline()
            if not line: return None
            if line in (b"\\r\\n", b"\\n"): break
            k,_,v=line.decode().partition(":")
            if k.lower()=="content-length": length=int(v.strip())
        if length is None: return None
        return json.loads(sys.stdin.buffer.read(length).decode())
    def send(obj):
        body=json.dumps(obj).encode()
        sys.stdout.buffer.write(b"Content-Length: %d\\r\\n\\r\\n"%len(body)+body)
        sys.stdout.buffer.flush()
    while True:
        msg=read()
        if msg is None: break
        m=msg.get("method"); mid=msg.get("id")
        if m=="initialize": send({"jsonrpc":"2.0","id":mid,"result":{"capabilities":{}}})
        elif m=="textDocument/documentSymbol": send({"jsonrpc":"2.0","id":mid,"result":[{"name":"main","kind":12}]})
        elif m=="textDocument/definition": send({"jsonrpc":"2.0","id":mid,"result":{"uri":msg["params"]["textDocument"]["uri"],"range":{"start":{"line":0,"character":0}}}})
        elif m=="shutdown": send({"jsonrpc":"2.0","id":mid,"result":None})
        elif m=="exit": break
''')


@pytest.fixture
def fake_server(tmp_path):
    script = tmp_path / "fake_lsp.py"
    script.write_text(FAKE_LSP, encoding="utf-8")
    return (sys.executable, str(script))


# ------------------------------------------------------------- LSP bridge

async def test_lsp_initialize_symbols_definition_over_real_pipes(fake_server, tmp_path):
    client = LSPClient(LSPConfig(argv=fake_server, workspace=tmp_path, timeout_s=8.0))
    await client.start()
    try:
        syms = await client.symbols("file:///x.py")
        assert syms == [{"name": "main", "kind": 12}]
        d = await client.definition("file:///x.py", 1, 2)
        assert d["uri"] == "file:///x.py"
    finally:
        await client.close()
    assert client.proc.returncode is not None      # graceful shutdown завершил процесс


async def test_lsp_empty_argv_rejected():
    with pytest.raises(ValueError):
        LSPClient(LSPConfig(argv=(), workspace=Path(".")))


async def test_lsp_negative_position_rejected(fake_server, tmp_path):
    client = LSPClient(LSPConfig(argv=fake_server, workspace=tmp_path))
    await client.start()
    try:
        with pytest.raises(ValueError):
            await client.definition("file:///x.py", -1, 0)
    finally:
        await client.close()


async def test_lsp_message_size_bounded(fake_server, tmp_path):
    # лимит пропускает initialize (~250 б), но режет заведомо большой запрос
    client = LSPClient(LSPConfig(argv=fake_server, workspace=tmp_path, max_message_bytes=4096))
    await client.start()
    try:
        with pytest.raises(LSPError):
            await client.request("textDocument/documentSymbol",
                                 {"textDocument": {"uri": "file:///" + "x" * 100_000}})
    finally:
        await client.close()


def test_lsp_uses_argv_not_shell():
    import ast
    import inspect
    import bcc.lsp_bridge as mod
    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "shell":
            assert not (isinstance(node.value, ast.Constant) and node.value.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"create_subprocess_shell", "system", "popen"}


# ------------------------------------------------------------- code_intel feature

async def test_code_intel_registers_readonly_caps():
    await CI.setup(None)
    for cap in CI.CAPS:
        spec = REGISTRY.get(f"code:{cap}")
        assert spec is not None
        assert decide_effect(spec, {}, {})[0] == "auto"       # read-only


async def test_code_intel_no_server_is_graceful(monkeypatch):
    monkeypatch.delenv("LSP_SERVERS", raising=False)
    await CI.setup(None)
    spec = REGISTRY.get("code:symbols")
    ctx = type("C", (), {"svc": None, "task": {}, "run_id": 1, "agent": {},
                         "workspace": ".", "call_id": "c", "step": 0})()
    res = await spec.handler({"lang": "python", "uri": "file:///x.py"}, ctx)
    assert res.error and "no LSP server" in res.content


async def test_code_intel_status_endpoint():
    out = await CI.code_intel_status()
    assert set(out["capabilities"]) == set(CI.CAPS)


# ------------------------------------------------------------- benchmark aggregator

def test_summarize_metrics():
    rows = [
        {"executor": "bossman", "success": True, "tests_green": True, "elapsed_s": 10, "cost_usd": .1},
        {"executor": "bossman", "success": False, "tests_green": False, "elapsed_s": 20, "cost_usd": .2},
        {"executor": "opencode", "success": True, "tests_green": True, "elapsed_s": 12, "cost_usd": .1},
    ]
    s = summarize(rows)
    assert s["bossman"]["success_rate"] == .5
    assert s["bossman"]["avg_elapsed_s"] == 15
    assert s["opencode"]["success_rate"] == 1


def test_compare_verdict():
    rows = [
        {"executor": "bossman", "success": True, "tests_green": True, "human_interventions": 0,
         "cost_usd": .1, "security_violations": 0},
        {"executor": "opencode", "success": True, "tests_green": False, "human_interventions": 3,
         "cost_usd": .3, "security_violations": 1},
    ]
    c = compare(rows)
    assert c["a_wins"] == c["criteria"]        # bossman выигрывает по всем критериям
    assert c["bossman_vs_opencode"]["fewer_security_violations"] is True


def test_compare_insufficient_data():
    c = compare([{"executor": "bossman", "success": True}])
    assert c["verdict"] == "insufficient data"


def test_load_jsonl_rejects_non_object(tmp_path):
    p = tmp_path / "runs.jsonl"
    p.write_text('{"executor":"x","success":true}\n[1,2,3]\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_jsonl(p)


def test_load_jsonl_roundtrip(tmp_path):
    p = tmp_path / "runs.jsonl"
    p.write_text('{"executor":"bossman","success":true}\n\n{"executor":"opencode","success":false}\n',
                 encoding="utf-8")
    rows = load_jsonl(p)
    assert len(rows) == 2 and rows[0]["executor"] == "bossman"
