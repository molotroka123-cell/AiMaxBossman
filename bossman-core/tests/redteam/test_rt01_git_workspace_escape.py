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


# ------------------------------------------------- RT-01 follow-up: реальная форма
#
# Производство строит рабочую папку как `settings.workspace_dir / agent.name`
# (`bossman/runner.py:349`). Проверяем именно эту форму, а не абстрактную.


def _production_shaped(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workdir = workspace / "coder"
    workdir.mkdir(parents=True)
    return workdir


async def test_case_a_agent_workdir_is_its_own_repository(tmp_path: Path):
    """A: рабочая папка агента САМА — разрешённый корень. Должно работать."""
    workdir = _production_shaped(tmp_path)
    _repo(workdir)
    (workdir / "note.txt").write_text("x\n", encoding="utf-8")
    ctx = ToolContext(agent="coder", workdir=workdir, run_id=1)
    assert not (await git({"op": "status"}, ctx)).error
    assert not (await git({"op": "add", "args": ["note.txt"]}, ctx)).error
    assert not (await git({"op": "commit", "message": "ok"}, ctx)).error


async def test_case_b_agent_workdir_nested_in_an_unauthorized_repository(tmp_path: Path):
    """B: рабочая папка внутри ЧУЖОГО репозитория — полномочия не наследуются."""
    outer = tmp_path / "outer"
    workdir = outer / "workspace" / "coder"
    workdir.mkdir(parents=True)
    _repo(outer)
    (outer / "a.py").write_text("x\n", encoding="utf-8")
    _sh("git", "add", "a.py", cwd=outer)
    _sh("git", "commit", "-qm", "outer", cwd=outer)
    ctx = ToolContext(agent="coder", workdir=workdir, run_id=1)
    assert (await git({"op": "status"}, ctx)).error


async def test_a_malicious_gitfile_cannot_borrow_another_repositorys_metadata(tmp_path: Path):
    """Совпадения верхнего уровня мало: `.git`-ФАЙЛ уводил команды в чужую базу.

    `--show-toplevel` возвращал разрешённую папку (потому что gitfile делает её
    корнем рабочего дерева), а `git branch` показывал ветки чужого репозитория.
    """
    victim = tmp_path / "victim"
    victim.mkdir()
    _repo(victim)
    (victim / "secret.txt").write_text("VICTIM\n", encoding="utf-8")
    _sh("git", "add", "secret.txt", cwd=victim)
    _sh("git", "commit", "-qm", "v", cwd=victim)
    _sh("git", "branch", "victim-branch", cwd=victim)

    workdir = _production_shaped(tmp_path)
    (workdir / ".git").write_text(f"gitdir: {victim}/.git\n", encoding="utf-8")
    ctx = ToolContext(agent="coder", workdir=workdir, run_id=1)

    result = await git({"op": "branch"}, ctx)
    assert result.error, "gitfile увёл команды в чужой репозиторий"
    assert "victim-branch" not in result.content


async def test_a_linked_worktree_is_refused_until_explicitly_authorized(tmp_path: Path):
    """Связанное рабочее дерево держит базу в ГЛАВНОМ репозитории, то есть снаружи.

    Отказ намеренный и fail-closed: явного способа авторизовать внешний
    репозиторий у ToolContext пока нет, а доверять `.git`-файлу, который мог
    написать сам агент, нельзя. Тест фиксирует это как решение, а не как случайность.
    """
    main = tmp_path / "main"
    main.mkdir()
    _repo(main)
    (main / "f.txt").write_text("x\n", encoding="utf-8")
    _sh("git", "add", "f.txt", cwd=main)
    _sh("git", "commit", "-qm", "m", cwd=main)
    linked = tmp_path / "linked"
    _sh("git", "worktree", "add", "-q", "-b", "feat", str(linked), cwd=main)
    ctx = ToolContext(agent="coder", workdir=linked, run_id=1)
    assert (await git({"op": "status"}, ctx)).error
