"""CI-AUTOREPAIR-REPORT-001 / CI-AUTOREPAIR-REF-001 contract tests.

The auto-repair workflow must never fabricate success and must actually test
the candidate commit on pull_request events. These are text-level contracts on
.github/workflows/bossman-v2-repair.yml (root CI has no pyyaml by design).
"""
from __future__ import annotations

from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "bossman-v2-repair.yml"


def _text() -> str:
    return WF.read_text(encoding="utf-8")


def test_autorepair_report_001_no_false_success_claims():
    """A repair PR is created on failure(); its body must not claim tests pass."""
    body_start = _text().find("body: |")
    assert body_start != -1, "auto-repair PR body block missing"
    body = _text()[body_start:]
    forbidden = ["Verified all tests pass", "all tests pass", "tests pass", "Fixed PostgreSQL schema"]
    for phrase in forbidden:
        assert phrase not in body, f"false-success claim in auto-repair PR body: {phrase!r}"
    assert "REPAIR ATTEMPTED, NOT VERIFIED" in body
    assert "FAILED" in body


def test_autorepair_ref_001_candidate_commit_is_tested():
    """The auto-repair job must not hard-code the target branch as checkout ref:
    on pull_request the candidate merge commit has to be what runs."""
    text = _text()
    checkout_idx = text.find("actions/checkout@v4")
    assert checkout_idx != -1
    checkout_block = text[checkout_idx:checkout_idx + 400]
    assert "ref: claude/bossman-control" not in checkout_block, (
        "hard-coded ref means pull_request events test the branch head, not the candidate commit"
    )
