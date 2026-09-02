"""Outreach approval boundary + side-effect idempotency."""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bossman.apprentice import flags  # noqa: E402
from bossman.apprentice.errors import OutreachRefused, PersonalDataRefused  # noqa: E402
from bossman.apprentice.guards import ApprovalRegistry, SideEffectLedger  # noqa: E402
from bossman.apprentice.outreach import (LeadCard, OutreachGate, OutreachPackage, approve_outreach, build_lead_card,  # noqa: E402
                                         outreach_digest)
from bossman.company.model import ApprovalDecision  # noqa: E402

LISTING = {"business_id": "b1", "name": "Blue Bakery", "category": "bakery", "city": "Lisbon", "website": "",
           "phone": "+351 000", "public_email": "hello@bluebakery.example", "maps_url": "https://maps.example/b1",
           "rating": 4.6, "reviews_count": 120, "source": "google_maps_public"}
CLOCK = {"t": 1_000.0}


def _clock():
    return CLOCK["t"]


def _pkg(recipient="hello@bluebakery.example", text="Hi Blue Bakery, here is a demo site for you.", probe=None):
    card = build_lead_card(LISTING, site_probe=probe or {"status": "no_site"})
    return OutreachPackage(card=card, reason="no website found for a well-rated bakery", demo_ref="demo://bluebakery-v1",
                           proposal_text=text, recipient=recipient, created_at=_clock())


def _gate(sent, **kw):
    return OutreachGate(transport=lambda p: sent.append(p.recipient) or {"id": "msg1"}, clock=_clock, approvals=ApprovalRegistry(_clock), **kw)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(flags.EXTERNAL_OUTREACH, "1")
    CLOCK["t"] = 1_000.0


def test_outreach_flag_off_refuses(monkeypatch):
    monkeypatch.delenv(flags.EXTERNAL_OUTREACH, raising=False)
    sent = []
    pkg = _pkg()
    r = _gate(sent).send("t1", pkg, approve_outreach("t1", pkg, approver="human:owner", nonce="n", expires_at=None))
    assert not r.sent and "off" in r.reason and sent == []


def test_owner_sees_full_package_and_default_transport_refuses(on):
    pkg = _pkg()
    view = pkg.owner_view()
    assert set(view) >= {"business_found", "reason", "current_site_link", "demo", "proposal_text", "intended_recipient", "verified_problem"}
    assert view["intended_recipient"] == "hello@bluebakery.example" and view["current_site_link"] == "(none)"
    gate = OutreachGate(clock=_clock, approvals=ApprovalRegistry(_clock))
    r = gate.send("t1", pkg, approve_outreach("t1", pkg, approver="human:owner", nonce="n", expires_at=None))
    assert not r.sent and "no live transport" in r.reason and gate.sent_log == []


def test_outreach_digest_binds_recipient_and_content(on):
    sent = []
    gate = _gate(sent)
    pkg = _pkg()
    ap = approve_outreach("t1", pkg, approver="human:owner", nonce="n1", expires_at=None)
    wrong_recipient = replace(pkg, recipient="info@otherbusiness.example")
    r = gate.send("t1", wrong_recipient, ap)
    assert not r.sent and "digest" in r.reason and sent == []
    changed_text = replace(pkg, proposal_text=pkg.proposal_text + " PS: 50% off")
    assert not gate.send("t1", changed_text, ap).sent
    assert not gate.send("t2", pkg, ap).sent                          # another task/scope
    assert outreach_digest("t1", pkg) != outreach_digest("t1", wrong_recipient)
    assert gate.send("t1", pkg, ap).sent and sent == ["hello@bluebakery.example"]


def test_approval_replay_and_expiry_are_refused(on):
    sent = []
    gate = _gate(sent)
    pkg = _pkg()
    ap = approve_outreach("t1", pkg, approver="human:owner", nonce="once", expires_at=2_000.0)
    assert gate.send("t1", pkg, ap).sent
    again = _pkg(recipient="team@cafe.example")                         # different recipient, same nonce -> replay
    ap2 = ApprovalDecision(True, "human:owner", "ok", digest=outreach_digest("t1", again), scope="t1", expires_at=2_000.0, nonce="once")
    r = gate.send("t1", again, ap2)
    assert not r.sent and "replay" in r.reason
    CLOCK["t"] = 3_000.0
    other = _pkg(recipient="team@cafe.example")
    exp = approve_outreach("t1", other, approver="human:owner", nonce="n3", expires_at=2_500.0)
    assert "expired" in gate.send("t1", other, exp).reason and sent == ["hello@bluebakery.example"]
    with pytest.raises(OutreachRefused):
        approve_outreach("t1", pkg, approver="policy:auto", nonce="x", expires_at=None)


def test_outreach_blocks_mass_resend_and_blocked(on):
    sent = []
    gate = _gate(sent, max_per_run=2, cooldown_s=100)
    pkg = _pkg()
    ap = approve_outreach("t1", pkg, approver="human:owner", nonce="a", expires_at=None)
    assert gate.send("t1", pkg, ap).sent
    # duplicate effect (same recipient + content) even with a fresh approval
    dup = approve_outreach("t1", pkg, approver="human:owner", nonce="b", expires_at=None)
    assert "duplicate external effect" in gate.send("t1", pkg, dup).reason
    # resend to the same recipient with new content inside the cooldown
    v2 = replace(pkg, proposal_text="follow-up")
    assert "cooldown" in gate.send("t1", v2, approve_outreach("t1", v2, approver="human:owner", nonce="c", expires_at=None)).reason
    # mass mailing cap
    p2 = _pkg(recipient="a@one.example"); assert gate.send("t1", p2, approve_outreach("t1", p2, approver="human:owner", nonce="d", expires_at=None)).sent
    p3 = _pkg(recipient="b@two.example")
    assert "cap" in gate.send("t1", p3, approve_outreach("t1", p3, approver="human:owner", nonce="e", expires_at=None)).reason
    # blocked recipient never receives, approval or not
    gate.block("c@three.example")
    p4 = _pkg(recipient="c@three.example")
    assert "blocked" in gate.send("t2", p4, approve_outreach("t2", p4, approver="human:owner", nonce="f", expires_at=None)).reason
    assert sent == ["hello@bluebakery.example", "a@one.example"]


def test_concurrent_gates_share_side_effect_ledger(on):
    ledger = SideEffectLedger(); sent = []
    g1, g2 = _gate(sent, ledger=ledger), _gate(sent, ledger=ledger)
    pkg = _pkg()
    assert g1.send("t1", pkg, approve_outreach("t1", pkg, approver="human:owner", nonce="1", expires_at=None)).sent
    assert not g2.send("t1", pkg, approve_outreach("t1", pkg, approver="human:owner", nonce="2", expires_at=None)).sent and len(sent) == 1


def test_outreach_refuses_non_public_personal_data(on):
    with pytest.raises(PersonalDataRefused):
        build_lead_card({**LISTING, "owner_personal_email": "x@y"}, site_probe={"status": "no_site"})
    with pytest.raises(PersonalDataRefused):
        build_lead_card({**LISTING, "home_address": "..."}, site_probe={"status": "no_site"})
    with pytest.raises(PersonalDataRefused):
        build_lead_card({**LISTING, "source": "scraped_social_profile"}, site_probe={"status": "no_site"})


def test_unverified_problem_cannot_be_sent(on):
    sent = []
    gate = _gate(sent)
    for probe in (None, {"status": "ok", "https": True, "mobile_ok": True, "last_updated_days": 10}):
        card = build_lead_card({**LISTING, "website": "https://bluebakery.example"}, site_probe=probe)
        assert not card.verified
        pkg = OutreachPackage(card=card, reason="r", demo_ref="d", proposal_text="p", recipient="hello@bluebakery.example", created_at=_clock())
        assert "not verified" in gate.send("t1", pkg, approve_outreach("t1", pkg, approver="human:owner", nonce="z", expires_at=None)).reason
    weak = build_lead_card({**LISTING, "website": "http://bluebakery.example"}, site_probe={"status": "ok", "https": False})
    assert weak.verified and weak.problem == "no_https" and sent == []
