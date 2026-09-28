"""Audit 2026-09-28: every git call on the sidecar workspace is bounded, and a
timeout becomes a typed refusal (or the documented conservative fallback)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bossman.apprentice import openhands_client as oc
from bossman.apprentice import openhands_teacher_client as otc


@pytest.fixture
def hung_git(monkeypatch):
    seen: list[dict] = []

    def fake_run(cmd, **kw):
        seen.append(kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(oc.subprocess, "run", fake_run)
    monkeypatch.setattr(otc.subprocess, "run", fake_run)
    return seen


def test_git_helpers_pass_a_timeout_and_map_it(hung_git, tmp_path: Path):
    with pytest.raises(oc.OpenHandsError, match="timed out"):
        oc._git(tmp_path, "rev-parse", "HEAD")
    with pytest.raises(oc.OpenHandsError, match="timed out"):
        otc._git(tmp_path, "status")
    assert oc._ignored_untracked(tmp_path, ["a.txt"]) == set()       # conservative: filter nothing
    assert oc._filtered_blob_ids(tmp_path, ["a.txt"]) == {}          # caller falls back to raw hashing
    assert len(hung_git) == 4 and all(kw.get("timeout") == oc.GIT_TIMEOUT_S for kw in hung_git)
