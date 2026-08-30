"""Dev Factory: патч считается без внешнего GNU diff.

На чистой Windows нет /usr/bin/diff — фабрика падала бы без Git Bash или WSL.
Здесь проверяется, что расчёт патча самодостаточен (difflib), семантика для
ревью сохранена, а результат ещё и детерминирован (раньше mtime в заголовках
менял sha256 при каждом прогоне одного и того же дерева).
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from bossman.dev_factory import workspace as ws_mod
from bossman.dev_factory.models import Verdict
from bossman.dev_factory.reviewer import AdversarialReviewer
from bossman.dev_factory.workspace import WorkspaceManager


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "keep.py").write_text("A=1\n", encoding="utf-8")
    (src / "gone.py").write_text("X=1\n", encoding="utf-8")
    (src / "same.py").write_text("S=1\n", encoding="utf-8")
    (src / "pkg" / "mod.py").write_text("M=1\n", encoding="utf-8")
    return src


@pytest.fixture
def manager(tmp_path):
    return WorkspaceManager(tmp_path / "ws")


def test_workspace_does_not_shell_out_to_gnu_diff():
    """Ни subprocess, ни внешнего `diff` в модуле расчёта патча."""
    tree = ast.parse(inspect.getsource(ws_mod))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in getattr(node, "names", [])]
            mod = getattr(node, "module", None)
            assert "subprocess" not in names and mod != "subprocess"
    assert "difflib" in {n.name for n in ast.walk(tree)
                         if isinstance(n, ast.alias)}


def test_diff_works_without_any_external_tools(repo, manager, monkeypatch):
    """PATH пуст — GNU diff недостижим, как на чистой Windows. Патч всё равно есть."""
    work = manager.prepare("j1", repo)
    (work / "added.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setenv("PATH", "")
    p = manager.diff("j1")
    assert "added.py" in p.diff and "+VALUE = 1" in p.diff
    assert p.files == ("added.py",)


def test_diff_covers_add_modify_delete(repo, manager):
    work = manager.prepare("j1", repo)
    (work / "added.py").write_text("N=1\n", encoding="utf-8")
    (work / "keep.py").write_text("A=2\n", encoding="utf-8")
    (work / "gone.py").unlink()
    p = manager.diff("j1")
    assert "+N=1" in p.diff                      # добавленный
    assert "-A=1" in p.diff and "+A=2" in p.diff  # изменённый
    assert "-X=1" in p.diff                       # удалённый показан
    assert "same.py" not in p.diff                # неизменённый молчит
    # удалённые файлы в списке затронутых не числятся (как и раньше)
    assert set(p.files) == {"added.py", "keep.py"}


def test_files_are_relative_paths_so_sensitive_check_matches(repo, manager):
    """Ревьюер ищет границы подстрокой — пути должны быть репо-относительными,
    иначе временный каталог воркспейса подмешивается в проверку."""
    work = manager.prepare("j1", repo)
    (work / "bossman").mkdir()
    (work / "bossman" / "approvals.py").write_text("BOUNDARY=1\n", encoding="utf-8")
    p = manager.diff("j1")
    assert "bossman/approvals.py" in p.files
    assert not any(f.startswith("/") or ":" in f for f in p.files)
    res = AdversarialReviewer().review(p, evidence_verdict=Verdict.PASS)
    assert not res.approved
    assert any("bossman/approvals.py" in f for f in res.findings)


def test_nested_directories_are_diffed(repo, manager):
    work = manager.prepare("j1", repo)
    (work / "pkg" / "mod.py").write_text("M=2\n", encoding="utf-8")
    p = manager.diff("j1")
    assert "pkg/mod.py" in p.files and "+M=2" in p.diff


def test_binary_files_reported_not_mangled(repo, manager):
    (repo / "bin.dat").write_bytes(b"\x00\x01\x02")
    work = manager.prepare("j1", repo)
    (work / "bin.dat").write_bytes(b"\x00\x09\x02")
    p = manager.diff("j1")
    assert "Binary files" in p.diff and "bin.dat" in p.diff
    assert "\x00" not in p.diff          # сырые байты в текст патча не попадают


def test_identical_binary_files_produce_no_diff(repo, manager):
    (repo / "bin.dat").write_bytes(b"\x00\x01\x02")
    manager.prepare("j1", repo)
    assert manager.diff("j1").diff == ""


def test_file_without_trailing_newline(repo, manager):
    work = manager.prepare("j1", repo)
    (work / "noeol.py").write_text("NOEOL=1", encoding="utf-8")
    p = manager.diff("j1")
    assert "+NOEOL=1" in p.diff
    assert p.diff.endswith("\n")         # патч остаётся построчно корректным


def test_diff_is_deterministic(repo, manager):
    """Один и тот же результат -> одинаковый sha256 (никаких mtime в заголовках)."""
    work = manager.prepare("j1", repo)
    (work / "keep.py").write_text("A=2\n", encoding="utf-8")
    first = manager.diff("j1")
    second = manager.diff("j1")
    assert first.sha256 == second.sha256 and first.diff == second.diff
    assert first.sha256


def test_no_changes_is_empty_patch(repo, manager):
    manager.prepare("j1", repo)
    p = manager.diff("j1")
    assert p.diff == "" and p.files == ()


def test_missing_workspace_returns_empty_patch(manager):
    p = manager.diff("nope")
    assert p.diff == "" and p.files == () and p.sha256 == ""


def test_windows_style_crlf_content_survives(repo, manager):
    work = manager.prepare("j1", repo)
    (work / "crlf.py").write_bytes(b"A=1\r\nB=2\r\n")
    p = manager.diff("j1")
    assert "crlf.py" in p.files and "+A=1" in p.diff
