"""F4 (Astra/Codex, 2026-09-08): cleanup must not report success while the
sandbox is still on disk.

`shutil.rmtree(..., ignore_errors=True)` on the Windows host left the checkout
in place and said nothing. Cleanup now reports, retries a bounded number of
times, clears the read-only bit Windows sets on git objects, and never deletes
outside the sandbox root or inside the source repository.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from bossman.apprentice.isolated_worktree import IsolatedWorktree


@pytest.fixture
def source_repo(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=src, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=src, check=True)
    (src / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=src, check=True)
    return src


def test_cleanup_reports_removal_and_leaves_no_parent_behind(source_repo, monkeypatch, tmp_path):
    monkeypatch.setenv("TMPDIR", str(tmp_path)); monkeypatch.setenv("TEMP", str(tmp_path)); monkeypatch.setenv("TMP", str(tmp_path))
    import tempfile
    tempfile.tempdir = None
    wt = IsolatedWorktree(str(source_repo))
    root = wt.create()
    parent = root.parent
    assert root.exists()
    assert wt.cleanup() is True
    assert not root.exists() and not parent.exists()
    assert wt.cleanup_state() == {"removed": True, "path": None, "error": None, "kept_on_purpose": False}


def test_readonly_files_do_not_defeat_cleanup(source_repo, monkeypatch, tmp_path):
    """Git objects are read-only on Windows; on POSIX a read-only file inside a
    writable dir deletes anyway, so the read-only DIRECTORY is the portable
    version of the same obstacle."""
    monkeypatch.setenv("TMPDIR", str(tmp_path)); monkeypatch.setenv("TEMP", str(tmp_path)); monkeypatch.setenv("TMP", str(tmp_path))
    import tempfile
    tempfile.tempdir = None
    wt = IsolatedWorktree(str(source_repo))
    root = wt.create()
    locked = root / "locked"
    locked.mkdir()
    (locked / "f.txt").write_text("x", encoding="utf-8")
    os.chmod(locked / "f.txt", stat.S_IREAD)
    if os.name != "nt" and os.geteuid() != 0:
        os.chmod(locked, stat.S_IREAD | stat.S_IEXEC)
    try:
        assert wt.cleanup() is True
    finally:
        if locked.exists():
            os.chmod(locked, stat.S_IRWXU)
    assert not root.exists()


def test_a_failing_deletion_is_reported_not_swallowed(source_repo, monkeypatch, tmp_path):
    monkeypatch.setenv("TMPDIR", str(tmp_path)); monkeypatch.setenv("TEMP", str(tmp_path)); monkeypatch.setenv("TMP", str(tmp_path))
    import tempfile
    tempfile.tempdir = None
    wt = IsolatedWorktree(str(source_repo))
    root = wt.create()
    calls = {"n": 0}

    def locked_rmtree(path, *a, **kw):            # the Windows shape: PermissionError, tree stays
        calls["n"] += 1
        raise PermissionError(32, "The process cannot access the file because it is being used")

    monkeypatch.setattr(shutil, "rmtree", locked_rmtree)
    monkeypatch.setattr(IsolatedWorktree, "CLEANUP_ATTEMPTS", 3)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    assert wt.cleanup() is False
    assert calls["n"] == 3                          # bounded retry, then the truth
    assert root.exists()
    state = wt.cleanup_state()
    assert state["removed"] is False and "PermissionError" in state["error"] and state["path"] == str(root)
    # The root is kept so a later retry (or the owner) can find the leftover.
    assert wt.root == root
    monkeypatch.undo()
    assert wt.cleanup() is True and not root.exists()


def test_a_transient_lock_is_retried_and_then_succeeds(source_repo, monkeypatch, tmp_path):
    monkeypatch.setenv("TMPDIR", str(tmp_path)); monkeypatch.setenv("TEMP", str(tmp_path)); monkeypatch.setenv("TMP", str(tmp_path))
    import tempfile
    tempfile.tempdir = None
    wt = IsolatedWorktree(str(source_repo))
    root = wt.create()
    real = shutil.rmtree
    calls = {"n": 0}

    def flaky(path, *a, **kw):
        calls["n"] += 1
        if calls["n"] < 2:
            raise PermissionError(32, "locked")
        return real(path, *a, **kw)

    monkeypatch.setattr(shutil, "rmtree", flaky)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    assert wt.cleanup() is True and not root.exists() and calls["n"] == 2


def test_cleanup_never_deletes_outside_the_sandbox_root(source_repo, tmp_path):
    wt = IsolatedWorktree(str(source_repo))
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "data.txt").write_text("keep", encoding="utf-8")
    wt.root = victim                                # a path outside tempfile.gettempdir()
    assert wt.cleanup() is False
    assert victim.exists() and (victim / "data.txt").read_text() == "keep"
    assert "outside the sandbox root" in wt.cleanup_state()["error"]


def test_cleanup_never_deletes_the_source_repository(source_repo):
    wt = IsolatedWorktree(str(source_repo))
    wt.root = source_repo
    assert wt.cleanup() is False
    assert (source_repo / "README.md").exists()


def test_keep_after_is_reported_as_kept_on_purpose(source_repo, monkeypatch, tmp_path):
    monkeypatch.setenv("TMPDIR", str(tmp_path)); monkeypatch.setenv("TEMP", str(tmp_path)); monkeypatch.setenv("TMP", str(tmp_path))
    import tempfile
    tempfile.tempdir = None
    wt = IsolatedWorktree(str(source_repo), keep_after=True)
    root = wt.create()
    try:
        assert wt.cleanup() is True and root.exists()
        assert wt.cleanup_state()["kept_on_purpose"] is True
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)
