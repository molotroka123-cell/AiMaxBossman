"""Audit 2026-09-28: IsolatedWorktree evidence decodes git output as UTF-8 (the host
code page, cp1251 here, cannot decode byte 0x98 of "И") and keeps renames."""
from __future__ import annotations

import subprocess
from pathlib import Path

from bossman.apprentice.isolated_worktree import IsolatedWorktree


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                   capture_output=True, timeout=60)


def _repo(tmp_path: Path) -> IsolatedWorktree:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "core.autocrlf", "false")
    (root / "note.txt").write_text("hello\n", encoding="utf-8")
    (root / "old.txt").write_text("keep me\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    wt = IsolatedWorktree(str(root), base_branch="HEAD")
    wt.root = root
    return wt


def test_diff_with_non_cp1251_bytes_is_captured(tmp_path):
    wt = _repo(tmp_path)
    (wt.root / "note.txt").write_text("Иван 😀\n", encoding="utf-8")
    evidence = wt.derive_evidence()
    assert "error" not in evidence
    assert evidence["modified_files"] == ["note.txt"]
    assert "Иван" in (evidence["patches"]["note.txt"] or "")


def test_a_staged_rename_is_reported_on_both_sides(tmp_path):
    wt = _repo(tmp_path)
    _git(wt.root, "mv", "old.txt", "new.txt")
    evidence = wt.derive_evidence()
    assert "old.txt" in evidence["deleted_files"] and "new.txt" in evidence["added_files"]
