"""authored_by_lane memapps: bossman.dev_factory.evidence - success needs an artifact, UNKNOWN is never success."""
from __future__ import annotations

from bossman.dev_factory.evidence import from_test_output, write_evidence
from bossman.dev_factory.models import Verdict


def test_pass_fail_unknown_verdicts():
    assert from_test_output("===== 12 passed in 1.2s =====").verdict is Verdict.PASS
    r = from_test_output("1 failed, 11 passed")
    assert r.verdict is Verdict.FAIL and r.failed == 1 and r.passed == 11
    u = from_test_output("collected nothing, weird output")
    assert u.verdict is Verdict.UNKNOWN and not u.proves_success


def test_errors_count_as_failure():
    assert from_test_output("3 passed, 2 errors").verdict is Verdict.FAIL


def test_write_evidence_redacts_secrets(tmp_path):
    secret = "sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4"
    path = write_evidence(tmp_path / "ev", "out.txt", f"token={secret}\n5 passed")
    body = open(path, encoding="utf-8").read()
    assert secret not in body and "5 passed" in body
