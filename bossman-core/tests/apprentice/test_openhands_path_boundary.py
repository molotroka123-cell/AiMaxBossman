"""What the scope check actually contains, and what it does not.

`_validate_scope` is an EVIDENCE boundary: it decides whether the repository
changes a run produced are the ones the run was allowed to make. It is not, and
cannot be, a containment boundary — the agent holds a terminal tool, so nothing
a path check does stops it from writing outside the repository. Containment is
the sandbox's job.

Saying that plainly matters, because a path check described as containment is
the kind of claim that gets believed. These tests pin the part that is real:
every traversal form is refused, and a symlink is not a way to have a change
counted as in-scope.
"""
from __future__ import annotations

import subprocess

import pytest

from bossman.apprentice import openhands_client as oc


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


# ---------------------------------------------------------- traversal forms

@pytest.mark.parametrize("path", [
    "../escape", "../../escape", "a/../../b", "sub/../ok.md",
    "/absolute/path", "C:/windows", "c:\\windows", "\\\\server\\share",
    "with\x00null", "",
])
def test_every_traversal_form_is_refused(path):
    with pytest.raises(oc.OpenHandsError):
        oc._normalize_repo_path(path)


@pytest.mark.parametrize("path,expected", [
    ("ok/path.md", "ok/path.md"), ("./ok.md", "ok.md"), ("././ok.md", "ok.md"),
    ("dir\\file.md", "dir/file.md"), ("trailing/", "trailing"),
])
def test_ordinary_paths_survive_normalisation(path, expected):
    assert oc._normalize_repo_path(path) == expected


def test_a_prefix_is_a_directory_not_a_string_prefix():
    """`src` must not authorise `srcfoo`."""
    assert oc._in_scope("src/a.py", ["src"]) is True
    assert oc._in_scope("src", ["src"]) is True
    assert oc._in_scope("srcfoo/a.py", ["src"]) is False


def test_an_empty_allowlist_fails_closed():
    with pytest.raises(oc.OpenHandsError):
        oc._validate_scope(("a.md",), (), ())


def test_a_protected_path_beats_an_allowed_one():
    """Overlap must resolve to refusal, not to whichever list is checked first."""
    with pytest.raises(oc.OpenHandsError):
        oc._validate_scope(("src/secret.py",), ("src",), ("src/secret.py",))


# --------------------------------------------------------------- symlinks

def test_a_symlink_is_a_changed_file_like_any_other(repo):
    """git reports the link itself, so an out-of-scope link is refused by the
    same rule as an out-of-scope file — a symlink is not a way in."""
    (repo / "LINK.md").symlink_to("/etc/hostname")
    changed = oc._changed_files(repo)
    assert "LINK.md" in changed
    with pytest.raises(oc.OpenHandsError) as exc:
        oc._validate_scope(changed, ("README.md",), ())
    assert "LINK.md" in str(exc.value)


def test_a_symlink_named_outside_the_allowlist_cannot_smuggle_a_change(repo):
    (repo / "sub").mkdir()
    (repo / "sub" / "LINK.md").symlink_to("/etc/hostname")
    changed = oc._changed_files(repo)
    with pytest.raises(oc.OpenHandsError):
        oc._validate_scope(changed, ("docs",), ())


def test_untracked_files_are_seen_at_all(repo):
    """The check is only as good as the change list feeding it; a run whose
    whole output is new files must not look like a run that changed nothing."""
    (repo / "NEW.md").write_text("new\n", encoding="utf-8")
    assert "NEW.md" in oc._changed_files(repo)


def test_the_boundary_is_evidence_not_containment(repo):
    """Stated as a test so the claim cannot drift: a symlink INSIDE the
    allowlist passes the scope check, because git sees an in-scope path. What
    the target then receives is the sandbox's problem, not this function's —
    the agent has a terminal and never needed a symlink to reach outside."""
    (repo / "docs").mkdir()
    (repo / "docs" / "LINK.md").symlink_to("/etc/hostname")
    changed = oc._changed_files(repo)
    assert "docs/LINK.md" in changed
    oc._validate_scope(changed, ("docs",), ())   # accepted: it IS in scope
