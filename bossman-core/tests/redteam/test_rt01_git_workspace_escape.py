"""RT-01: git не должен наследовать полномочие вверх по дереву каталогов.

`git -C <workdir>` НЕ означает «репозиторий = workdir»: git сам ищет .git в
родителях. Рабочая папка агента, вложенная в больший репозиторий, давала
команду над родительским репозиторием. Здесь зафиксирована и сама атака, и
законный случай — чтобы починка не выродилась в «git вообще не работает».
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bossman.toolkit import ToolContext
from bossman.toolkit.gitops import git

pytestmark = pytest.mark.asyncio


def _sh(*argv: str, cwd: Path) -> None:
    subprocess.run(argv, cwd=cwd, check=True, capture_output=True)


def _repo(root: Path) -> None:
    _sh("git", "init", "-q", "-b", "main", cwd=root)
    _sh("git", "config", "user.email", "redteam@example.invalid", cwd=root)
    _sh("git", "config", "user.name", "redteam", cwd=root)


@pytest.fixture()
def nested(tmp_path: Path):
    """Рабочая папка агента ВНУТРИ чужого репозитория — сама постановка атаки."""
    outer = tmp_path / "outer"
    workdir = outer / "workspace" / "coder"
    workdir.mkdir(parents=True)
    _repo(outer)
    (outer / "app.py").write_text("SECRET = 'outer'\n", encoding="utf-8")
    _sh("git", "add", "app.py", cwd=outer)
    _sh("git", "commit", "-qm", "outer", cwd=outer)
    _sh("git", "branch", "outer-secret", cwd=outer)
    return outer, workdir, ToolContext(agent="coder", workdir=workdir, run_id=1)


async def test_nested_workdir_cannot_read_the_parent_repository(nested):
    _, _, ctx = nested
    result = await git({"op": "branch"}, ctx)
    assert result.error, "вложенная папка увидела ветки родительского репозитория"
    assert "outer-secret" not in result.content


async def test_nested_workdir_cannot_checkout_a_parent_branch(nested):
    outer, _, ctx = nested
    before = subprocess.run(["git", "-C", str(outer), "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    result = await git({"op": "checkout", "args": ["outer-secret"]}, ctx)
    after = subprocess.run(["git", "-C", str(outer), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    assert result.error
    assert before == after, "чужой репозиторий переключил ветку"


async def test_nested_workdir_cannot_stage_a_file_outside(nested):
    outer, _, ctx = nested
    (outer / "pwned.py").write_text("attacker\n", encoding="utf-8")
    result = await git({"op": "add", "args": ["../../pwned.py"]}, ctx)
    staged = subprocess.run(["git", "-C", str(outer), "diff", "--cached", "--name-only"],
                            capture_output=True, text=True).stdout.strip()
    assert result.error
    assert staged == "", f"файл снаружи попал в индекс чужого репозитория: {staged!r}"


async def test_nested_workdir_cannot_diff_an_outside_pathspec(nested):
    _, _, ctx = nested
    assert (await git({"op": "diff", "files": ["../../app.py"]}, ctx)).error


async def test_absolute_pathspec_is_refused(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _repo(repo)
    outside = tmp_path / "outside.txt"
    outside.write_text("x\n", encoding="utf-8")
    ctx = ToolContext(agent="coder", workdir=repo, run_id=1)
    assert (await git({"op": "add", "args": [str(outside)]}, ctx)).error


async def test_symlink_pathspec_pointing_outside_is_refused(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _repo(repo)
    secret = tmp_path / "secret.txt"
    secret.write_text("canary\n", encoding="utf-8")
    (repo / "link.txt").symlink_to(secret)
    ctx = ToolContext(agent="coder", workdir=repo, run_id=1)
    assert (await git({"op": "add", "args": ["link.txt"]}, ctx)).error


async def test_the_authorized_repository_root_still_works(tmp_path: Path):
    """Положительный контроль: строгость не должна ломать законный репозиторий."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _repo(repo)
    (repo / "f.py").write_text("x = 1\n", encoding="utf-8")
    ctx = ToolContext(agent="coder", workdir=repo, run_id=1)
    assert not (await git({"op": "status"}, ctx)).error
    assert not (await git({"op": "add", "args": ["f.py"]}, ctx)).error
    assert not (await git({"op": "commit", "message": "ok"}, ctx)).error
    log = await git({"op": "log"}, ctx)
    assert not log.error and "ok" in log.content
