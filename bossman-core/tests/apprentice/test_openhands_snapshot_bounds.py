"""CODING-SNAPSHOT-32MB (P1): evidence must scale with the CHANGE, not the repo.

The coding path read every workspace byte into memory and refused any
repository above 32 MB (the owner's own Bossman repo is ~74 MB tracked), and
any tracked file above 8 MB even when the run never touched it. Evidence is now
a streaming digest of every file; host-read bytes are held only for the
reviewed change set, under the old 32 MB budget, and are verified against the
digests. Over budget is an honest refusal, never truncation.
"""
from __future__ import annotations

import os

import pytest

from bossman.apprentice import openhands_client as oc
from test_openhands_evidence_independence import repo, run, git  # noqa: F401

MB = 1024 * 1024


def _commit_big_files(repo, sizes):
    (repo / "big").mkdir()
    for index, size in enumerate(sizes):
        (repo / "big" / f"blob{index}.bin").write_bytes(bytes([index + 1]) * size)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "big unchanged content")


def test_unchanged_content_above_32mb_does_not_refuse_a_small_edit(repo):
    _commit_big_files(repo, [7 * MB] * 5)                  # 35 MB, each file under 8 MB
    result = run(repo, "pathlib.Path('allowed/keep.txt').write_bytes(b'edited\\n')")
    assert result.changed_files == ("allowed/keep.txt",)
    assert set(result.files) == {"allowed/keep.txt"}
    assert result.files["allowed/keep.txt"].data == b"edited\n"
    assert "+edited" in result.diff and "big/" not in result.diff


def test_unchanged_file_above_the_per_file_bound_is_hashed_not_refused(repo):
    _commit_big_files(repo, [9 * MB])
    result = run(repo, "pathlib.Path('allowed/keep.txt').write_bytes(b'edited\\n')")
    assert result.changed_files == ("allowed/keep.txt",)
    assert "big/blob0.bin" not in result.diff and "big/blob0.bin" not in result.files


def test_a_changed_file_above_the_per_file_bound_is_still_refused(repo):
    _commit_big_files(repo, [9 * MB])
    with pytest.raises(oc.OpenHandsError, match="bounded read"):
        run(repo, "pathlib.Path('big/blob0.bin').write_bytes(b'x')", allowed=("big",))


def test_changed_bytes_over_budget_are_an_honest_refusal(repo, monkeypatch):
    monkeypatch.setattr(oc, "_MAX_CHANGED_BYTES", 1000)
    body = ("pathlib.Path('allowed/a.txt').write_bytes(b'a' * 600); "
            "pathlib.Path('allowed/b.txt').write_bytes(b'b' * 600)")
    with pytest.raises(oc.OpenHandsError, match="changed workspace evidence exceeds"):
        run(repo, body)


def test_changed_bytes_within_budget_are_complete(repo, monkeypatch):
    monkeypatch.setattr(oc, "_MAX_CHANGED_BYTES", 2000)
    body = ("pathlib.Path('allowed/a.txt').write_bytes(b'a' * 600); "
            "pathlib.Path('allowed/b.txt').write_bytes(b'b' * 600)")
    result = run(repo, body)
    assert result.files["allowed/a.txt"].data == b"a" * 600
    assert result.files["allowed/b.txt"].data == b"b" * 600


def test_file_modified_between_digest_and_evidence_read_is_refused(repo, monkeypatch):
    original = oc._snapshot
    calls = []

    def modify_after_post_run_digest(workspace):
        digests = original(workspace)
        calls.append(1)
        if len(calls) == 2:                                   # the post-run `captured` digest
            target = workspace / "allowed/keep.txt"
            before = target.stat()
            target.write_bytes(b"swapped!\n")                  # same size as the recorded edit
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        return digests

    monkeypatch.setattr(oc, "_snapshot", modify_after_post_run_digest)
    with pytest.raises(oc.OpenHandsError, match="between digest and evidence read"):
        run(repo, "pathlib.Path('allowed/keep.txt').write_bytes(b'recorded\\n')")


def test_pre_run_bytes_that_do_not_match_the_pre_run_digest_are_refused(repo, monkeypatch):
    monkeypatch.setattr(oc, "_committed_bytes", lambda workspace, commit, name, filters: b"forged\n")
    with pytest.raises(oc.OpenHandsError, match="pre-run bytes"):
        run(repo, "pathlib.Path('allowed/keep.txt').write_bytes(b'edited\\n')")


def test_pre_run_side_of_the_diff_is_the_host_observed_bytes(repo):
    result = run(repo, "pathlib.Path('allowed/keep.txt').write_bytes(b'edited\\n')")
    assert "-keep" in result.diff and "+edited" in result.diff


def test_snapshot_memory_is_digests_not_bytes(repo):
    _commit_big_files(repo, [3 * MB])
    snapshot = oc._snapshot(repo)
    entry = snapshot["big/blob0.bin"]
    assert entry.size == 3 * MB and not hasattr(entry, "data")
