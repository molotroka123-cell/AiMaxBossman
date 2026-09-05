"""ActionReceipt (TRUTH-003 §2, §11): подпись, свежесть, SIGNED != VERIFIED, tool_result_only != VERIFIED."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bossman_shared import evidence as ev
from bossman_shared.action_receipt import ActionReceipt, request_digest

T0 = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _key(tmp_path, monkeypatch):
    monkeypatch.setenv(ev.ENV_KEY_FILE, str(tmp_path / "k" / "evidence.key"))
    ev.reset_cache(); yield; ev.reset_cache()


def _r(**kw):
    base = dict(task_id="t", step_id="s1", capability="fs.write", tool="fs.write", effect_type="IDEMPOTENT_WRITE",
                started_at=T0.isoformat(), finished_at=(T0 + timedelta(seconds=1)).isoformat(),
                observed_at=(T0 + timedelta(seconds=2)).isoformat(), executor_status="executed",
                observation_type="post_state", verification_status="VERIFIED")
    base.update(kw)
    return ActionReceipt(**base)


def test_fresh_receipt_verified_and_signature_roundtrip():
    r = _r().sign(signer="bossman_v3.verifier")
    assert r.fresh() == (True, "fresh") and r.verified() and r.signature_valid()
    again = ActionReceipt.from_dict(r.to_dict())
    assert again.signature_valid() and again.verified()
    tampered = ActionReceipt.from_dict({**r.to_dict(), "verification_status": "VERIFIED", "observed_at": (T0 + timedelta(seconds=9)).isoformat()})
    assert not tampered.signature_valid()


def test_stale_evidence_is_rejected_even_when_signed():
    stale = _r(observed_at=(T0 + timedelta(milliseconds=500)).isoformat()).sign(signer="bossman_v3.verifier")
    ok, why = stale.fresh()
    assert not ok and "STALE_EVIDENCE_REJECTED" in why and not stale.verified() and stale.signature_valid()
    before_start = _r(observed_at=(T0 - timedelta(seconds=1)).isoformat())
    assert not before_start.fresh()[0]
    assert not _r(observed_at="").fresh()[0]


def test_signed_receipt_is_not_verification_and_tool_result_only_never_verifies():
    claimed = _r(verification_status="UNVERIFIED").sign(signer="bcc.v2.verification")
    assert claimed.signature_valid() and not claimed.verified()             # SIGNED_RECEIPT != VERIFIED_SIDE_EFFECT
    tool_only = _r(observation_type="tool_result_only", verification_status="VERIFIED")
    assert not tool_only.verified()                                          # TOOL_CALLED != SIDE_EFFECT_VERIFIED
    with pytest.raises(ValueError):
        _r().sign(signer="model:self-report")


def test_request_digest_ignores_expect_and_is_stable():
    a = request_digest("terminal.run", {"command": "ls", "expect": {"kind": "file"}})
    b = request_digest("terminal.run", {"command": "ls"})
    assert a == b and len(a) == 32 and a != request_digest("terminal.run", {"command": "ls -a"})
