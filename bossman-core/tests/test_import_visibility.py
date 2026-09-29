"""RC19 audit regression guard: imports must come from THIS worktree.

Observed 2026-09-28: a stale editable install of another checkout hijacked
`import bossman` from this worktree, so the Fable-budget fix under test was
not the code under test (narrow tests failed with the OLD implementation).
These tests fail loudly on sys.path contamination instead of producing
misleading reds elsewhere.
"""
from __future__ import annotations

from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
REPO = CORE.parent


def test_bossman_package_comes_from_this_worktree():
    import bossman

    pkg = Path(bossman.__file__).resolve()
    assert CORE in pkg.parents, (
        f"bossman resolves outside this worktree: {pkg}. "
        "A stale editable install of another checkout is on sys.path; "
        "pin PYTHONPATH to this worktree or reinstall."
    )


def test_bossman_shared_comes_from_this_worktree():
    import bossman_shared

    pkg = Path(bossman_shared.__file__).resolve()
    assert REPO in pkg.parents, (
        f"bossman_shared resolves outside this worktree: {pkg}. "
        "A stale editable install of another checkout is on sys.path."
    )
