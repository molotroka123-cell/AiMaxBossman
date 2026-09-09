"""F1 (Astra/Codex, 2026-09-08, reproduced 2/2 on 45027d3): the untrusted
producer must not control the evidence used to admit its own result.

The sidecar ran `git update-index --assume-unchanged protected.txt`, rewrote
the protected file and returned `completed`; `git diff HEAD` believed the index
and Bossman admitted the change with `changed_files=()` and an empty diff.

The evidence is now derived from the working tree against HEAD's tree — the
index has no say — and every way a sidecar could make git look away is tried
here: index flags, ignore files, deletion, rename, symlinks, untracked files,
path forms. The positive controls pin that an honest in-scope run still passes
and still produces a non-empty diff.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from bossman.apprentice import openhands_client as oc
from bossman.apprentice.openhands_client import OpenHandsClient, OpenHandsError, OpenHandsRequest


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "audit@example.invalid")
    git(tmp_path, "config", "user.name", "Breaker")
    (tmp_path / "protected.txt").write_text("owner baseline\n", encoding="utf-8")
    (tmp_path / "allowed").mkdir()
    (tmp_path / "allowed" / "keep.txt").write_text("keep\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "fixture")
    return tmp_path


def sidecar(body: str) -> list[str]:
    """A sidecar that does `body` inside the workspace and reports completed."""
    code = (
        "import json,sys,subprocess,pathlib,os; "
        "p=json.load(sys.stdin); w=pathlib.Path(p['workspace']); os.chdir(w); "
        f"{body}; "
        "print(json.dumps({'schema':'bossman.openhands.v1','status':'completed'}))"
    )
    return [sys.executable, "-c", code]


def run(repo, body, *, allowed=("allowed",), protected=("protected.txt",)):
    return OpenHandsClient(sidecar(body)).run(
        OpenHandsRequest("audit", repo, tuple(allowed), tuple(protected)))


# ------------------------------------------------------------- the reproduction

@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_a_protected_file_hidden_by_an_index_flag_is_refused(repo, flag):
    body = (f"subprocess.run(['git','update-index','{flag}','protected.txt'],check=True); "
            "pathlib.Path('protected.txt').write_text('unauthorized change\\n')")
    with pytest.raises(OpenHandsError) as err:
        run(repo, body)
    assert "protected.txt" in str(err.value)
    # The bytes did change — the refusal is about evidence, not about undoing.
    assert (repo / "protected.txt").read_text() == "unauthorized change\n"


def test_an_index_flag_alone_is_a_refusal_even_without_a_content_change(repo):
    """Setting the flag has exactly one purpose: making git stop looking."""
    with pytest.raises(OpenHandsError, match="index flags"):
        run(repo, "subprocess.run(['git','update-index','--assume-unchanged','protected.txt'],check=True)")


def test_the_working_tree_is_compared_to_head_not_to_the_index(repo):
    """`_worktree_delta` alone, with the index poisoned: it still sees the change."""
    git(repo, "update-index", "--assume-unchanged", "protected.txt")
    (repo / "protected.txt").write_text("changed\n", encoding="utf-8")
    assert git(repo, "diff", "--name-only", "HEAD") == ""          # git looks away
    delta = oc._worktree_delta(repo)
    assert delta.changed == ("protected.txt",)                       # we do not


# --------------------------------------------------------- other ways to hide

def test_a_protected_untracked_file_cannot_hide_behind_info_exclude(repo):
    body = ("pathlib.Path('.git/info/exclude').write_text('secret.key\\n'); "
            "pathlib.Path('secret.key').write_text('leak')")
    with pytest.raises(OpenHandsError, match="secret.key"):
        run(repo, body, allowed=("allowed",), protected=("secret.key",))


def test_a_new_gitignore_cannot_hide_the_files_it_lists(repo):
    body = ("pathlib.Path('allowed/.gitignore').write_text('*\\n'); "
            "pathlib.Path('allowed/hidden.txt').write_text('x'); "
            "pathlib.Path('outside.txt').write_text('x')")
    # Refused either way: the files git now hides are named in the refusal.
    with pytest.raises(OpenHandsError, match="hidden.txt|outside.txt"):
        run(repo, body)


def test_editing_a_tracked_gitignore_disables_ignore_filtering(repo):
    """The repo's own rules are trusted only when the run left them alone."""
    body = ("pathlib.Path('.gitignore').write_text('*.log\\n*.txt\\n'); "
            "pathlib.Path('leak.txt').write_text('x')")
    with pytest.raises(OpenHandsError) as err:
        run(repo, body, allowed=("allowed",))
    assert "leak.txt" in str(err.value)
    delta = oc._worktree_delta(repo)
    assert delta.ignore_rules_touched and "leak.txt" in delta.changed and ".gitignore" in delta.changed


def test_deleting_a_protected_file_is_a_change(repo):
    with pytest.raises(OpenHandsError, match="protected.txt"):
        run(repo, "pathlib.Path('protected.txt').unlink()")


def test_renaming_a_protected_file_into_scope_is_a_change_on_both_paths(repo):
    body = "subprocess.run(['git','mv','protected.txt','allowed/moved.txt'],check=True)"
    with pytest.raises(OpenHandsError, match="protected.txt"):
        run(repo, body)


def test_replacing_a_protected_file_with_a_symlink_is_a_change(repo):
    if os.name == "nt":
        pytest.skip("symlink creation needs privileges on Windows")
    body = ("pathlib.Path('protected.txt').unlink(); "
            "os.symlink('allowed/keep.txt','protected.txt')")
    with pytest.raises(OpenHandsError, match="protected.txt"):
        run(repo, body)


def test_a_symlink_created_in_scope_is_reported_as_the_link_itself(repo):
    if os.name == "nt":
        pytest.skip("symlink creation needs privileges on Windows")
    result = run(repo, "os.symlink('../protected.txt','allowed/link')")
    assert result.status == "completed" and result.changed_files == ("allowed/link",)
    assert (repo / "protected.txt").read_text() == "owner baseline\n"


def test_an_untracked_protected_file_is_seen(repo):
    with pytest.raises(OpenHandsError, match="notes/secret.md"):
        run(repo, "pathlib.Path('notes').mkdir(); pathlib.Path('notes/secret.md').write_text('x')",
            protected=("notes",))


def test_an_untracked_out_of_scope_file_is_seen(repo):
    with pytest.raises(OpenHandsError, match="stray.txt"):
        run(repo, "pathlib.Path('stray.txt').write_text('x')")


@pytest.mark.parametrize("path", [
    "../escape", "/absolute/path", "C:/windows", "c:\\windows", "\\\\server\\share", "a/../../b",
])
def test_traversal_and_absolute_forms_are_never_in_scope(path):
    with pytest.raises(OpenHandsError):
        oc._validate_scope((path,), ("allowed",), ())


# --------------------------------------------------------- positive controls

def test_an_honest_in_scope_edit_is_admitted_with_real_evidence(repo):
    result = run(repo, "pathlib.Path('allowed/keep.txt').write_text('edited\\n'); "
                       "pathlib.Path('allowed/new.txt').write_text('new\\n')")
    assert result.status == "completed"
    assert result.changed_files == ("allowed/keep.txt", "allowed/new.txt")
    assert "+edited" in result.diff and "+new" in result.diff
    assert (repo / "protected.txt").read_text() == "owner baseline\n"


def test_untouched_ignore_rules_still_drop_build_noise(repo):
    """Noise the repository itself ignores (`*.log`) is not a scope violation."""
    result = run(repo, "pathlib.Path('build.log').write_text('noise'); "
                       "pathlib.Path('allowed/keep.txt').write_text('edited\\n')")
    assert result.status == "completed" and result.changed_files == ("allowed/keep.txt",)


def test_an_unchanged_workspace_reports_no_changes(repo):
    result = run(repo, "pass")
    assert result.status == "completed" and result.changed_files == () and result.diff == ""


def test_the_pre_run_cleanliness_check_uses_the_same_independent_scan(repo):
    git(repo, "update-index", "--assume-unchanged", "protected.txt")
    (repo / "protected.txt").write_text("dirty before\n", encoding="utf-8")
    with pytest.raises(OpenHandsError):
        run(repo, "pass")


# ----------------------------------------------- authority the sidecar lacks

def test_the_sidecar_still_cannot_commit_add_a_remote_or_touch_config(repo):
    with pytest.raises(OpenHandsError, match="HEAD"):
        run(repo, "pathlib.Path('allowed/keep.txt').write_text('x'); "
                  "subprocess.run(['git','-c','user.email=a@b','-c','user.name=n','commit','-qam','x'],check=True)")
    with pytest.raises(OpenHandsError, match="configuration"):
        run(repo, "subprocess.run(['git','config','core.fsmonitor','true'],check=True)")
    git(repo, "config", "--unset", "core.fsmonitor")
    # Last: the remote the sidecar adds stays in the fixture and poisons later runs.
    with pytest.raises(OpenHandsError, match="remote|configuration"):
        run(repo, "subprocess.run(['git','remote','add','origin','https://example.invalid/x.git'],check=True)")


def test_the_sidecar_cannot_mark_the_mission_complete_by_itself():
    """The client returns evidence for Bossman's verification; there is no
    field through which the sidecar could grant itself completion or
    permissions — the contract accepts exactly two statuses and nothing
    about authority."""
    fields = {f.name for f in oc.OpenHandsResult.__dataclass_fields__.values()}
    # `files` carries independently observed immutable bytes for the teacher;
    # it grants no mission/approval authority and never comes from sidecar JSON.
    assert fields == {"status", "changed_files", "diff", "sidecar", "files"}
    for forbidden in ("push", "merge", "deploy", "permissions", "authority", "approve"):
        assert forbidden not in fields


# ------------------------------------------ line-ending normalisation (Windows)

def test_a_crlf_checkout_under_autocrlf_is_not_a_change(tmp_path):
    """Core CI's Windows job: with `core.autocrlf=true` git checks files out with
    CRLF while the blob holds LF. Hashing the raw working-tree bytes then
    reported every tracked file as modified and refused an UNTOUCHED workspace
    as "evidence mismatch". The scan must hash through git's clean filter, the
    way git itself decides what a blob is."""
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "a@b")
    git(tmp_path, "config", "user.name", "n")
    git(tmp_path, "config", "core.autocrlf", "true")
    # The Windows shape exactly: files are WRITTEN with CRLF (text mode on
    # Windows), `git add` under autocrlf stores LF blobs, the index stat
    # matches the CRLF working tree, and git reports the tree clean.
    (tmp_path / "allowed").mkdir()
    (tmp_path / "allowed" / "keep.txt").write_bytes(b"one\r\ntwo\r\n")
    (tmp_path / "protected.txt").write_bytes(b"owner\r\nbaseline\r\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "lf blobs from crlf files")
    assert git(tmp_path, "status", "--porcelain") == ""          # git: clean
    assert "i/lf" in git(tmp_path, "ls-files", "--eol", "protected.txt")   # blob is LF
    assert (tmp_path / "protected.txt").read_bytes() == b"owner\r\nbaseline\r\n"  # tree is CRLF
    delta = oc._worktree_delta(tmp_path)
    assert delta.changed == (), delta                             # so must we
    result = run(tmp_path, "pass")
    assert result.status == "completed" and result.changed_files == ()
    # and a real change through the same normalisation is still seen
    (tmp_path / "protected.txt").write_bytes(b"owner\r\nchanged\r\n")
    assert oc._worktree_delta(tmp_path).changed == ("protected.txt",)
