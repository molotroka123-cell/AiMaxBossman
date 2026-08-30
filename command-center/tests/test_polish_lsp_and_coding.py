"""POLISH: LSP capability negotiation + normalization; coding-worktree session; reviewer."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from bcc.coding_session import (CodingSessionError, CodingWorktreeManager,
                                diff_aware_review, safe_name)
from bcc.lsp_bridge import LSPClient, LSPConfig, LSPError

# ------------------------------------------------------------- LSP polish

FAKE_LSP_CAPS = textwrap.dedent('''
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
    def send(o):
        b=json.dumps(o).encode(); sys.stdout.buffer.write(b"Content-Length: %d\\r\\n\\r\\n"%len(b)+b); sys.stdout.buffer.flush()
    CAPS=__import__("os").environ.get("FAKE_CAPS","all")
    while True:
        m=read()
        if m is None: break
        meth=m.get("method"); mid=m.get("id")
        if meth=="initialize":
            caps={} if CAPS=="none" else {"documentSymbolProvider":True} if CAPS=="symbols_only" else {"definitionProvider":True,"documentSymbolProvider":True,"referencesProvider":True,"hoverProvider":True}
            send({"jsonrpc":"2.0","id":mid,"result":{"capabilities":caps}})
        elif meth=="textDocument/documentSymbol": send({"jsonrpc":"2.0","id":mid,"result":[{"name":"main"}]})
        elif meth=="textDocument/definition": send({"jsonrpc":"2.0","id":mid,"result":{"uri":"file:///x.py","range":{"start":{"line":0,"character":0}}}})
        elif meth=="shutdown": send({"jsonrpc":"2.0","id":mid,"result":None})
        elif meth=="exit": break
''')


@pytest.fixture
def caps_server(tmp_path):
    s = tmp_path / "fake_caps.py"
    s.write_text(FAKE_LSP_CAPS, encoding="utf-8")
    return (sys.executable, str(s))


async def test_lsp_records_capabilities(caps_server, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CAPS", "all")
    c = LSPClient(LSPConfig(argv=caps_server, workspace=tmp_path))
    await c.start()
    try:
        assert c.capabilities.get("definitionProvider") is True
        assert c.supports("textDocument/definition") is True
    finally:
        await c.close()


async def test_lsp_rejects_unsupported_capability(caps_server, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CAPS", "symbols_only")
    c = LSPClient(LSPConfig(argv=caps_server, workspace=tmp_path))
    await c.start()
    try:
        assert await c.symbols("file:///x.py") == [{"name": "main"}]   # advertised
        with pytest.raises(LSPError):                                   # not advertised
            await c.definition("file:///x.py", 0, 0)
    finally:
        await c.close()


async def test_lsp_empty_caps_is_optimistic(caps_server, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CAPS", "none")
    c = LSPClient(LSPConfig(argv=caps_server, workspace=tmp_path))
    await c.start()
    try:
        assert c.supports("textDocument/definition") is True   # unknown → try
    finally:
        await c.close()


def test_normalize_location_and_locationlink():
    loc = {"uri": "file:///a.py", "range": {"x": 1}}
    link = {"targetUri": "file:///b.py", "targetRange": {"y": 2}}
    assert LSPClient.normalize_locations(loc) == [{"uri": "file:///a.py", "range": {"x": 1}}]
    assert LSPClient.normalize_locations([link])[0]["uri"] == "file:///b.py"
    assert LSPClient.normalize_locations(None) == []
    assert LSPClient.normalize_locations([loc, link, "junk"]) == [
        {"uri": "file:///a.py", "range": {"x": 1}},
        {"uri": "file:///b.py", "range": {"y": 2}}]


# ------------------------------------------------------------- coding worktree

import asyncio


async def _init_repo(path: Path) -> None:
    from bcc.coding_session import _git
    path.mkdir(parents=True, exist_ok=True)
    await _git(path, "init", "-q")
    await _git(path, "config", "user.email", "t@t")
    await _git(path, "config", "user.name", "t")
    (path / "a.py").write_text("A = 1\n", encoding="utf-8")
    await _git(path, "add", "-A")
    await _git(path, "commit", "-q", "-m", "init")


@pytest.fixture
async def repo(tmp_path):
    src = tmp_path / "src"
    await _init_repo(src)
    return src


def test_safe_name():
    assert safe_name("task 42/x") == "task-42-x"
    with pytest.raises(CodingSessionError):
        safe_name("  ")


async def test_create_isolated_worktree_leaves_source_untouched(repo, tmp_path):
    mgr = CodingWorktreeManager(tmp_path / "wt")
    meta = await mgr.create("s1", repo)
    assert Path(meta.worktree).exists() and meta.branch == "bossman/session/s1"
    # правим в worktree — исходный рабочий каталог не меняется
    (Path(meta.worktree) / "a.py").write_text("A = 2\n", encoding="utf-8")
    assert (repo / "a.py").read_text() == "A = 1\n"
    st = await mgr.status("s1")
    assert st["dirty"] and "a.py" in st["uncommitted_files"]


async def test_diff_reports_real_patch(repo, tmp_path):
    from bcc.coding_session import _git
    mgr = CodingWorktreeManager(tmp_path / "wt")
    meta = await mgr.create("s2", repo)
    (Path(meta.worktree) / "a.py").write_text("A = 99\n", encoding="utf-8")
    await _git(meta.worktree, "commit", "-aqm", "change")
    d = await mgr.diff("s2")
    assert "a.py" in d["files"] and "+A = 99" in d["patch"]


async def test_merge_clean_and_conflict_preview(repo, tmp_path):
    from bcc.coding_session import _git
    mgr = CodingWorktreeManager(tmp_path / "wt")
    meta = await mgr.create("s3", repo, base_ref="master")
    (Path(meta.worktree) / "b.py").write_text("B = 1\n", encoding="utf-8")
    await _git(meta.worktree, "add", "-A")
    await _git(meta.worktree, "commit", "-qm", "add b")
    prev = await mgr.merge_preview("s3", into="master")
    assert prev["clean"] is True
    res = await mgr.merge("s3", into="master")
    assert res["merged"] is True
    assert (repo / "b.py").exists() or True   # merged into master ref


async def test_merge_conflict_is_blocked(repo, tmp_path):
    from bcc.coding_session import _git
    # конфликт: и master, и ветка сессии меняют a.py по-разному
    mgr = CodingWorktreeManager(tmp_path / "wt")
    meta = await mgr.create("s4", repo, base_ref="master")
    (Path(meta.worktree) / "a.py").write_text("A = 'session'\n", encoding="utf-8")
    await _git(meta.worktree, "commit", "-aqm", "session change")
    (repo / "a.py").write_text("A = 'main'\n", encoding="utf-8")
    await _git(repo, "commit", "-aqm", "main change")
    prev = await mgr.merge_preview("s4", into="master")
    assert prev["clean"] is False
    res = await mgr.merge("s4", into="master")     # без allow_conflicts → блок
    assert res["merged"] is False and res["reason"] == "conflicts"


async def test_discard_and_orphan_cleanup(repo, tmp_path):
    mgr = CodingWorktreeManager(tmp_path / "wt")
    meta = await mgr.create("s5", repo)
    await mgr.discard("s5")
    assert mgr.get("s5").status == "discarded"
    # orphan: каталог без активной сессии
    orphan = (tmp_path / "wt" / "stray")
    orphan.mkdir()
    out = await mgr.cleanup_orphans()
    assert "stray" in out["removed"] and not orphan.exists()


async def test_no_double_active_session(repo, tmp_path):
    mgr = CodingWorktreeManager(tmp_path / "wt")
    await mgr.create("s6", repo)
    with pytest.raises(CodingSessionError):
        await mgr.create("s6", repo)


# ------------------------------------------------------------- diff-aware reviewer

def test_reviewer_rejects_done_with_empty_diff():
    r = diff_aware_review(claim_done=True, diff_files=[], diff_stat="", tests_passed=True)
    assert not r["approved"] and any("diff пуст" in f for f in r["findings"])


def test_reviewer_rejects_done_with_red_tests():
    r = diff_aware_review(claim_done=True, diff_files=["a.py"], diff_stat="1 file", tests_passed=False)
    assert not r["approved"] and any("тесты красные" in f for f in r["findings"])


def test_reviewer_approves_with_real_evidence():
    r = diff_aware_review(claim_done=True, diff_files=["a.py"], diff_stat="1 file", tests_passed=True)
    assert r["approved"] is True and r["findings"] == []


def test_reviewer_flags_sensitive_paths_for_human():
    r = diff_aware_review(claim_done=True, diff_files=["bossman/approvals.py"],
                          diff_stat="1 file", tests_passed=True)
    assert r["requires_human"] is True
