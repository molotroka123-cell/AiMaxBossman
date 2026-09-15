"""The exact-SHA owner preflight must refuse evidence that is not committed code.

These are not mocks. Each case builds a real Git repository, points the owner
entrypoint at it and calls the real gate, so the checks below fail if the gate
is ever loosened — which is exactly what happened to CI once already: the
workflow created its own results directory inside the checkout and then asked
preflight to certify a clean tree (OWNER-PREFLIGHT-WORKFLOW-ORDER-01). The fix
belongs in the workflow; the gate is correct and stays strict.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evening_owner_run as owner_run  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True, timeout=60,
    ).stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real committed checkout on the canonical branch name."""
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root.parent, "init", "-b", owner_run.CANONICAL_BRANCH, str(root))
    _git(root, "config", "user.email", "preflight@example.invalid")
    _git(root, "config", "user.name", "Preflight Test")
    (root / "shipped.txt").write_text("committed source\n", encoding="utf-8")
    _git(root, "add", "shipped.txt")
    _git(root, "commit", "-m", "committed source")
    monkeypatch.setattr(owner_run, "REPO", root)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setenv("BOSSMAN_ACCEPTANCE_SHA", _git(root, "rev-parse", "HEAD"))
    return root


def test_a_committed_checkout_is_accepted(repo: Path) -> None:
    """Positive control: without it, a gate that always fails would look good."""
    identity = owner_run._exact_identity(ci=True, require_remote=False)
    assert identity["sha"] == _git(repo, "rev-parse", "HEAD")
    assert identity["branch"] == owner_run.CANONICAL_BRANCH


def test_an_untracked_directory_is_refused(repo: Path) -> None:
    """The exact CI shape: a results directory created inside the checkout."""
    results = repo / "safety-results"
    results.mkdir()
    (results / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(owner_run.PreflightError) as caught:
        owner_run._exact_identity(ci=True, require_remote=False)
    assert "not clean" in str(caught.value)
    assert "safety-results" in str(caught.value)


def test_a_modified_tracked_file_is_refused(repo: Path) -> None:
    (repo / "shipped.txt").write_text("edited after the commit\n", encoding="utf-8")
    with pytest.raises(owner_run.PreflightError, match="not clean"):
        owner_run._exact_identity(ci=True, require_remote=False)


def test_an_ignored_file_does_not_block_the_owner_run(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preflight writes its own SHA-scoped state; ignored paths stay ignored.

    Otherwise the owner could never run the entrypoint twice in a row.
    """
    (repo / ".gitignore").write_text("/.bossman-state/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore owner run state")
    head = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setenv("BOSSMAN_ACCEPTANCE_SHA", head)
    state = repo / ".bossman-state" / "acceptance" / head
    state.mkdir(parents=True)
    (state / "OWNER_RUN_MANIFEST.json").write_text("{}", encoding="utf-8")
    assert owner_run._exact_identity(ci=True, require_remote=False)["sha"] == head


def test_a_different_commit_is_refused_in_ci(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOSSMAN_ACCEPTANCE_SHA", "0" * 40)
    with pytest.raises(owner_run.PreflightError, match="SHA mismatch"):
        owner_run._exact_identity(ci=True, require_remote=False)


def test_the_owner_path_refuses_a_foreign_branch(repo: Path) -> None:
    """The owner run is bound to the shared convergence branch, not any checkout."""
    owner_run._exact_identity(ci=False, require_remote=False)  # canonical: accepted
    _git(repo, "checkout", "-q", "-b", "some/other-branch")
    with pytest.raises(owner_run.PreflightError, match="wrong branch"):
        owner_run._exact_identity(ci=False, require_remote=False)
