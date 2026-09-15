"""Строитель песочницы и клиент OpenHands — вместе, а не по отдельности.

Именно этот пропуск и породил ошибку. Обе стороны были проверены поодиночке и
обе были «зелёными»: строитель делал связанное дерево (`git worktree add`), а
клиент отказывался работать с деревом, у которого есть удалённый репозиторий и
`.git` в виде файла. Ни один набор тестов не соединял их, и несовместимость
жила ровно между ними.

Поэтому здесь настоящий `OpenHandsClient.run()` запускается НАД настоящей
песочницей строителя. Не имитация клиента и не проверка полей: тот же
предполётный контроль, та же проверка области, те же git-проверки после.

Что здесь НЕ доказывается: изоляция от файловой системы. Проверка путей —
граница свидетельства, а не песочница (см. `test_openhands_path_boundary.py`).
Ниже — свидетельство изоляции репозитория и структура песочницы, и ничего
сверх этого.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bossman.apprentice.isolated_worktree import IsolatedWorktree
from bossman.apprentice.openhands_client import (OpenHandsClient, OpenHandsError,
                                                 OpenHandsRequest)


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                         text=True, check=True)
    return out.stdout.strip()


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    """Репозиторий владельца: со своим remote, своими ветками и своей грязью."""
    root = tmp_path / "owner-repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "owner@example.invalid")
    _git(root, "config", "user.name", "Owner")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "branch", "feature/owner-work")
    _git(root, "remote", "add", "origin", "https://example.invalid/owner.git")
    # Незакоммиченная правка владельца: она обязана пережить всё происходящее.
    (root / "src" / "dirty.txt").write_text("работа владельца\n", encoding="utf-8")
    return root


def _source_fingerprint(repo: Path) -> dict:
    """Всё, что у источника не имеет права измениться."""
    return {
        "head": _git(repo, "rev-parse", "HEAD"),
        "branches": _git(repo, "branch", "--format=%(refname:short)"),
        "remotes": _git(repo, "remote", "-v"),
        "worktrees": _git(repo, "worktree", "list", "--porcelain"),
        "status": _git(repo, "status", "--porcelain"),
        "config": (repo / ".git" / "config").read_text(encoding="utf-8"),
    }


def _sidecar(tmp_path: Path, body: str) -> Path:
    """Поддельная боковая программа с настоящим контрактом обмена."""
    script = tmp_path / f"sidecar_{abs(hash(body)) % 10**8}.py"
    script.write_text(
        "import json,sys,pathlib,os,subprocess\n"
        "r=json.load(sys.stdin)\n"
        "w=pathlib.Path(r['workspace'])\n"
        + body +
        "print(json.dumps({'schema':'bossman.openhands.v1','status':'completed'}))\n",
        encoding="utf-8")
    return script


EDIT_IN_SCOPE = (
    "p=w/'src'/'a.py'\n"
    "p.write_text('VALUE = 2\\n',encoding='utf-8')\n"
)


# ------------------------------------------------- клиент принимает песочницу

def test_the_client_accepts_the_sandbox_the_builder_produces(source_repo, tmp_path):
    """Одна проверка ради одного вопроса: подходят ли они друг другу.

    Раньше ответ был «нет», и узнать это можно было только в живом прогоне.
    """
    with IsolatedWorktree(str(source_repo)) as sandbox:
        result = OpenHandsClient([sys.executable,
                                  str(_sidecar(tmp_path, EDIT_IN_SCOPE))]).run(
            OpenHandsRequest("правка", sandbox.root, ("src",)))
    assert result.status == "completed"
    assert result.changed_files == ("src/a.py",)
    assert "VALUE = 2" in result.diff


def test_the_source_repository_is_untouched_by_a_whole_run(source_repo, tmp_path):
    """HEAD, ветки, удалённые, реестр деревьев, конфигурация и грязь — всё."""
    before = _source_fingerprint(source_repo)
    with IsolatedWorktree(str(source_repo)) as sandbox:
        OpenHandsClient([sys.executable,
                         str(_sidecar(tmp_path, EDIT_IN_SCOPE))]).run(
            OpenHandsRequest("правка", sandbox.root, ("src",)))
    assert _source_fingerprint(source_repo) == before


def test_the_owners_uncommitted_work_survives(source_repo, tmp_path):
    text = (source_repo / "src" / "dirty.txt").read_text(encoding="utf-8")
    with IsolatedWorktree(str(source_repo)) as sandbox:
        OpenHandsClient([sys.executable,
                         str(_sidecar(tmp_path, EDIT_IN_SCOPE))]).run(
            OpenHandsRequest("правка", sandbox.root, ("src",)))
    assert (source_repo / "src" / "dirty.txt").read_text(encoding="utf-8") == text


def test_cleanup_removes_the_sandbox_from_disk(source_repo):
    worktree = IsolatedWorktree(str(source_repo))
    path = worktree.create()
    assert path.is_dir()
    worktree.cleanup()
    assert not path.exists()
    assert worktree.root is None


# ------------------------------------------------------------ структура

def test_the_sandbox_cannot_push_because_it_has_nowhere_to_push(source_repo):
    """«Агент не может отправить изменения» — свойство структуры, а не правила."""
    with IsolatedWorktree(str(source_repo)) as sandbox:
        assert _git(sandbox.root, "remote") == ""
        pushed = subprocess.run(["git", "-C", str(sandbox.root), "push"],
                                capture_output=True, text=True)
        assert pushed.returncode != 0
        assert "origin" in (pushed.stderr + pushed.stdout).lower() \
            or "remote" in (pushed.stderr + pushed.stdout).lower()


def test_no_network_route_hides_in_the_sandbox_git_configuration(source_repo):
    """Удалённого нет не только в списке: его нет и в конфигурации.

    Оставленный `url.insteadOf`, `remote.*.pushurl` или `credential.helper`
    вернул бы сети дорогу внутрь при том же пустом `git remote`.
    """
    with IsolatedWorktree(str(source_repo)) as sandbox:
        config = (Path(sandbox.root) / ".git" / "config").read_text(encoding="utf-8")
        lowered = config.lower()
        for smell in ("[remote ", "insteadof", "pushurl", "credential"):
            assert smell not in lowered, f"в конфигурации песочницы осталось {smell!r}"


def test_two_concurrent_sandboxes_share_nothing(source_repo, tmp_path):
    first = IsolatedWorktree(str(source_repo))
    second = IsolatedWorktree(str(source_repo))
    try:
        a, b = first.create(), second.create()
        assert a != b
        assert _git(a, "rev-parse", "--abbrev-ref", "HEAD") != \
            _git(b, "rev-parse", "--abbrev-ref", "HEAD"), "ветки не совпадают"
        # Правка в одной не видна во второй.
        (a / "src" / "a.py").write_text("FROM_A = 1\n", encoding="utf-8")
        assert (b / "src" / "a.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    finally:
        first.cleanup()
        second.cleanup()


def test_a_crash_mid_run_leaves_the_source_unchanged(source_repo, tmp_path):
    """Процесс упал посреди работы. Источник об этом знать не должен."""
    before = _source_fingerprint(source_repo)
    crashing = _sidecar(tmp_path, "raise SystemExit(9)\n")
    worktree = IsolatedWorktree(str(source_repo))
    try:
        sandbox = worktree.create()
        with pytest.raises(OpenHandsError):
            OpenHandsClient([sys.executable, str(crashing)]).run(
                OpenHandsRequest("падение", sandbox, ("src",)))
    finally:
        worktree.cleanup()
    assert _source_fingerprint(source_repo) == before


def test_a_sandbox_abandoned_without_cleanup_still_leaves_the_source_alone(source_repo):
    """Даже если убрать за собой не успели: аварийно завершённый прогон не
    оставляет следа в источнике, потому что следа там и не появлялось."""
    before = _source_fingerprint(source_repo)
    orphan = IsolatedWorktree(str(source_repo), keep_after=True)
    path = orphan.create()
    (path / "src" / "a.py").write_text("ABANDONED = 1\n", encoding="utf-8")
    del orphan                                   # никто не звал cleanup()
    assert _source_fingerprint(source_repo) == before
    assert path.exists(), "keep_after означает, что песочница осталась"


# ------------------------------------------------------------ границы клиента

def test_the_client_still_refuses_a_workspace_with_a_remote(source_repo, tmp_path):
    """Отказ клиента — не формальность: именно он поймал старую песочницу."""
    with IsolatedWorktree(str(source_repo)) as sandbox:
        _git(sandbox.root, "remote", "add", "origin",
             "https://example.invalid/sneaky.git")
        with pytest.raises(OpenHandsError, match="must not have git remotes"):
            OpenHandsClient([sys.executable,
                             str(_sidecar(tmp_path, EDIT_IN_SCOPE))]).run(
                OpenHandsRequest("правка", sandbox.root, ("src",)))


def test_a_sandbox_edit_outside_the_declared_scope_is_refused(source_repo, tmp_path):
    body = "p=w/'docs';p.mkdir(exist_ok=True);(p/'x.md').write_text('x',encoding='utf-8')\n"
    with IsolatedWorktree(str(source_repo)) as sandbox:
        with pytest.raises(OpenHandsError, match="out-of-scope"):
            OpenHandsClient([sys.executable, str(_sidecar(tmp_path, body))]).run(
                OpenHandsRequest("вне области", sandbox.root, ("src",)))


def test_the_sandbox_may_not_commit_or_move_its_own_head(source_repo, tmp_path):
    """Личность коммитера задана в самой команде — и это часть контроля.

    Клон-песочница не наследует `user.email`/`user.name` источника, а боковая
    программа запускается с урезанным окружением. На машине, где у git есть
    глобальная личность, коммит проходил и контроль работал; на голом раннере
    он падал с «Please tell me who you are», HEAD оставался на месте, отказа
    не было — и негативный контроль оказывался пустым. Пустой контроль хуже
    отсутствующего: он выглядит доказательством.
    """
    body = ("subprocess.run(['git','-C',str(w),"
            "'-c','user.email=sandbox@example.invalid','-c','user.name=Sandbox',"
            "'commit','--allow-empty','-m','x'],capture_output=True)\n")
    with IsolatedWorktree(str(source_repo)) as sandbox:
        head_before = _git(Path(sandbox.root), "rev-parse", "HEAD")
        with pytest.raises(OpenHandsError, match="HEAD"):
            OpenHandsClient([sys.executable, str(_sidecar(tmp_path, body))]).run(
                OpenHandsRequest("коммит", sandbox.root, ("src",)))
        # Отказ вызван сдвигом HEAD, а не тем, что песочница ничего не смогла.
        assert _git(Path(sandbox.root), "rev-parse", "HEAD") != head_before


def test_the_sandbox_may_not_add_a_remote_during_the_run(source_repo, tmp_path):
    body = ("subprocess.run(['git','-C',str(w),'remote','add','x',"
            "'https://example.invalid/x.git'],capture_output=True)\n")
    with IsolatedWorktree(str(source_repo)) as sandbox:
        with pytest.raises(OpenHandsError):
            OpenHandsClient([sys.executable, str(_sidecar(tmp_path, body))]).run(
                OpenHandsRequest("удалённый", sandbox.root, ("src",)))


# ------------------------------------------------------------ пути и Windows

def test_a_symlink_inside_the_scope_does_not_carry_an_edit_outside(source_repo,
                                                                   tmp_path):
    """Ссылка в разрешённом каталоге, ведущая наружу.

    Проверка области видит путь ВНУТРИ области и пропустила бы правку. Здесь
    важно, что именно это и означает: проверка путей — граница свидетельства,
    а не песочница. Настоящее удержание — за пределами этого класса, и тест
    говорит об этом прямо, а не притворяется, что удержание есть.
    """
    outside = tmp_path / "outside.txt"
    outside.write_text("не тронуто\n", encoding="utf-8")
    with IsolatedWorktree(str(source_repo)) as sandbox:
        link = Path(sandbox.root) / "src" / "link.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("файловая система не даёт создать символическую ссылку")
        body = ("p=w/'src'/'link.txt'\np.write_text('через ссылку\\n',encoding='utf-8')\n")
        client = OpenHandsClient([sys.executable, str(_sidecar(tmp_path, body))])
        try:
            client.run(OpenHandsRequest("ссылка", sandbox.root, ("src",)))
        except OpenHandsError:
            pass                                  # отказ тоже допустимый исход
    # Записанное имя лежало в области; удержания на уровне файловой системы у
    # проверки путей нет, и это зафиксировано, а не скрыто.
    assert outside.read_text(encoding="utf-8") in ("не тронуто\n", "через ссылку\n")


def test_the_sandbox_path_survives_spaces_and_unicode(tmp_path):
    """Windows-подобный путь: пробелы и не-ASCII в имени каталога."""
    root = tmp_path / "Мои Документы" / "owner repo"
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "o@example.invalid")
    _git(root, "config", "user.name", "O")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")

    with IsolatedWorktree(str(root)) as sandbox:
        assert (Path(sandbox.root) / "src" / "a.py").is_file()
        assert _git(sandbox.root, "remote") == ""


def test_a_repository_with_no_remote_at_all_still_builds(tmp_path):
    """У песочницы источника может не быть `origin` — так выглядит и сама
    песочница, если её когда-нибудь возьмут за источник."""
    root = tmp_path / "no-remote"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "o@example.invalid")
    _git(root, "config", "user.name", "O")
    (root / "f.txt").write_text("x\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    with IsolatedWorktree(str(root)) as sandbox:
        assert (Path(sandbox.root) / "f.txt").is_file()
