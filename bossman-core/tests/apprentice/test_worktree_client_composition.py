"""The sandbox builder and the sandbox user have to fit each other.

`IsolatedWorktree` exists to build the checkout `OpenHandsClient` runs in, and
until this file nothing proved they compose — each had its own tests and the
two were never put together. They did not compose. `git worktree add` produced
a checkout that shared the source repository's config, so it arrived with
`origin` attached and with a `.git` FILE rather than a directory, and the
client refuses both on its very first checks. It also created its disposable
branch in the SOURCE repository and never removed it, so every run left an
`openhands_task_*` branch behind in the owner's repo.

These tests assert the contract in the direction that matters: whatever the
builder produces must satisfy every precondition the client enforces, and the
source repository must be untouched afterwards.
"""
from __future__ import annotations

import subprocess

import pytest

from bossman.apprentice.isolated_worktree import IsolatedWorktree


def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=60).stdout.strip()


@pytest.fixture
def source_repo(tmp_path):
    """A repository with a remote, like any real checkout the owner would use."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-q")
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=upstream, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=upstream, check=True)
    (upstream / "README.md").write_text("initial\n", encoding="utf-8")
    git(upstream, "add", "-A")
    git(upstream, "commit", "-qm", "init")

    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(upstream), str(work)], check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True)
    assert git(work, "remote") == "origin", "the fixture must model a real checkout"
    return work


# ------------------------------------------- the client's own preconditions

def test_the_sandbox_is_a_git_checkout(source_repo):
    with IsolatedWorktree(str(source_repo)) as sandbox:
        assert (sandbox.root / ".git").exists()


def test_the_sandbox_git_dir_is_a_directory_not_a_worktree_pointer(source_repo):
    """`OpenHandsClient.run()` reads `.git/config` as a path under a directory
    and snapshots it to detect tampering. A linked worktree's `.git` is a FILE,
    so that read raised before it could check anything."""
    with IsolatedWorktree(str(source_repo)) as sandbox:
        assert (sandbox.root / ".git").is_dir()
        assert (sandbox.root / ".git" / "config").read_bytes()


def test_the_sandbox_has_no_remotes(source_repo):
    """The client's first refusal: "workspace must not have git remotes". It is
    also what makes "the agent cannot push" structural rather than a rule —
    there is nowhere to push to."""
    with IsolatedWorktree(str(source_repo)) as sandbox:
        assert git(sandbox.root, "remote") == ""


def test_the_sandbox_starts_clean(source_repo):
    """The client refuses a workspace that is dirty before the run, because it
    could not then tell the agent's changes from someone else's."""
    with IsolatedWorktree(str(source_repo)) as sandbox:
        assert git(sandbox.root, "status", "--porcelain") == ""


def test_the_sandbox_carries_the_source_content(source_repo):
    with IsolatedWorktree(str(source_repo)) as sandbox:
        assert (sandbox.root / "README.md").read_text(encoding="utf-8") == "initial\n"


def test_every_client_precondition_holds_at_once(source_repo):
    """The four checks above are what `OpenHandsClient.run()` performs before it
    will start, in its order. Asserting them together is the composition claim."""
    with IsolatedWorktree(str(source_repo)) as sandbox:
        workspace = sandbox.root.resolve()
        assert (workspace / ".git").exists()
        assert git(workspace, "status", "--porcelain") == ""
        assert git(workspace, "remote") == ""
        assert git(workspace, "rev-parse", "HEAD")


# ------------------------------------------------- the source is not touched

def test_no_branch_is_left_behind_in_the_source(source_repo):
    """The regression this file was written for: `git worktree add -b` created
    the disposable branch in the SOURCE repository and cleanup never removed
    it, so every run littered the owner's repo."""
    before = set(git(source_repo, "branch", "--list").split())
    with IsolatedWorktree(str(source_repo)):
        pass
    after = set(git(source_repo, "branch", "--list").split())
    assert after == before, f"left behind: {sorted(after - before)}"


def test_the_source_keeps_its_own_remote(source_repo):
    """Detaching the sandbox must not detach the owner's checkout."""
    with IsolatedWorktree(str(source_repo)):
        pass
    assert git(source_repo, "remote") == "origin"


def test_the_source_stays_clean_and_unregistered(source_repo):
    with IsolatedWorktree(str(source_repo)) as sandbox:
        (sandbox.root / "README.md").write_text("changed in the sandbox\n", encoding="utf-8")
        (sandbox.root / "NEW.md").write_text("new in the sandbox\n", encoding="utf-8")
    assert git(source_repo, "status", "--porcelain") == ""
    assert (source_repo / "README.md").read_text(encoding="utf-8") == "initial\n"
    assert not (source_repo / "NEW.md").exists()
    assert str(sandbox.root) not in git(source_repo, "worktree", "list")


def test_work_in_the_sandbox_cannot_reach_the_upstream(source_repo, tmp_path):
    """No remote means no fetch and no push, checked by asking git to try."""
    with IsolatedWorktree(str(source_repo)) as sandbox:
        push = subprocess.run(["git", "push", "origin", "HEAD"], cwd=str(sandbox.root),
                              capture_output=True, text=True, timeout=60)
        assert push.returncode != 0
        assert "origin" in (push.stderr + push.stdout)


# ------------------------------------------------------- evidence still works

def test_evidence_still_names_modified_and_untracked_files(source_repo):
    """The clone must not cost the evidence the class exists to derive."""
    with IsolatedWorktree(str(source_repo)) as sandbox:
        (sandbox.root / "README.md").write_text("initial\nedited\n", encoding="utf-8")
        (sandbox.root / "NOTES.md").write_text("written by openhands\n", encoding="utf-8")
        evidence = sandbox.derive_evidence()
    assert "README.md" in evidence["modified_files"]
    assert "NOTES.md" in evidence["untracked_files"]
    assert evidence["head_before"] and evidence["head_after"]
    assert "edited" in evidence["patches"]["README.md"]


def test_two_sandboxes_do_not_collide(source_repo):
    """Branch names used to carry second resolution; two runs inside the same
    second produced the same name."""
    with IsolatedWorktree(str(source_repo)) as first:
        with IsolatedWorktree(str(source_repo)) as second:
            assert first.root != second.root
            assert git(first.root, "rev-parse", "--abbrev-ref", "HEAD") != \
                   git(second.root, "rev-parse", "--abbrev-ref", "HEAD")


def test_a_repository_on_master_is_supported(source_repo):
    """git still defaults to `master` in many installs; the base resolution has
    to find the branch the repository is actually on."""
    with IsolatedWorktree(str(source_repo)) as sandbox:
        assert git(sandbox.root, "rev-parse", "HEAD") == git(source_repo, "rev-parse", "HEAD")
