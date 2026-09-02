"""PASS 3 acceptance: LIVE effects need durable state (DURABLE-LIVE-001..005) and
owner approvals must be issued by the authenticated owner (OWNER-AUTH-001..005)."""
from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from bossman.apprentice import flags
from bossman.apprentice.composition import build_guards
from bossman.apprentice.durable import DurableSafetyStore
from bossman.apprentice.guards import ApprovalRegistry, DurableRequired, SideEffectLedger
from bossman.apprentice.outreach import OutreachGate, OutreachPackage, approve_outreach, build_lead_card, outreach_digest
from bossman.apprentice.owner_auth import OwnerApprovalIssuer, OwnerAuthRefused
from bossman.company.model import ApprovalDecision


@dataclass(frozen=True)
class FakePrincipal:                       # shape of bossman.remote_client.auth.Principal
    device_id: str
    scopes: frozenset

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


OWNER = FakePrincipal("owner-phone", frozenset({"approve", "chat"}))
CHAT_ONLY = FakePrincipal("kiosk", frozenset({"chat"}))


def authenticate(credential: str):
    return {"owner-cred": OWNER, "kiosk-cred": CHAT_ONLY}.get(credential)


from tests.test_apprentice_outreach import LISTING  # noqa: E402


def _package(recipient="hello@bluebakery.example", text="Hi Blue Bakery, here is a demo site for you."):
    card = build_lead_card(LISTING, site_probe={"status": "no_site"})
    return OutreachPackage(card=card, reason="no website found", demo_ref="demo://bluebakery-v1", proposal_text=text,
                           recipient=recipient, created_at=1000.0)


# ------------------------------------------------------------------ DURABLE-LIVE
def test_durable_live_001_live_effect_without_durable_store_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(flags.EXTERNAL_OUTREACH, "1")
    with pytest.raises(DurableRequired):
        SideEffectLedger(None, live=True)
    with pytest.raises(DurableRequired):
        ApprovalRegistry(store=None, live=True)
    with pytest.raises(DurableRequired):
        build_guards("LIVE", authenticate=authenticate)                       # no store path at all
    sent: list = []
    gate = OutreachGate(mode="LIVE", transport=lambda p: sent.append(p) or {"id": "1"})    # LIVE gate, memory ledger
    pkg = _package()
    ok = approve_outreach("t1", pkg, approver="human:owner", nonce="n1", expires_at=time.time() + 60)
    res = gate.send("t1", pkg, ok)
    assert not res.sent and "durable safety store" in res.reason and sent == []


def _reopen(path):
    return DurableSafetyStore(path)


def test_durable_live_002_005_state_survives_restart(tmp_path):
    db = tmp_path / "safety.sqlite"
    g = build_guards("LIVE", store_path=db, authenticate=authenticate)
    claimed, _ = g.ledger.claim("se-1"); assert claimed
    ch = g.issuer.challenge(task_id="t1", digest="d1", scope="t1")
    dec = g.issuer.redeem(ch.challenge_id, "owner-cred")
    assert g.approvals.validate(dec, digest="d1", scope="t1") == ""
    g.approvals.consume(dec)
    g.store.set_cooldown("hello@cafe-verde.example", time.time() + 3600)
    g.store.record_teacher_outcome("claude-code@1", -0.10)
    g.store.close()
    # "restart": brand-new store object on the same file
    g2 = build_guards("LIVE", store=_reopen(db), authenticate=authenticate)
    assert g2.ledger.claim("se-1") == (False, None)                                          # DURABLE-LIVE-002
    assert "replay" in g2.approvals.validate(dec, digest="d1", scope="t1")                   # DURABLE-LIVE-003
    assert g2.store.get_cooldown("hello@cafe-verde.example") > time.time()                   # DURABLE-LIVE-004
    assert g2.store.teacher_outcome("claude-code@1") == (0.4, 1)                             # DURABLE-LIVE-005


# ------------------------------------------------------------------ OWNER-AUTH
def _live(tmp_path):
    return build_guards("LIVE", store_path=tmp_path / "s.sqlite", authenticate=authenticate)


def test_owner_auth_001_model_made_human_owner_string_is_denied(tmp_path, monkeypatch):
    monkeypatch.setenv(flags.EXTERNAL_OUTREACH, "1")
    g = _live(tmp_path)
    pkg = _package()
    forged = ApprovalDecision(True, "human:owner", "looks approved", digest=outreach_digest("t1", pkg), scope="t1",
                              expires_at=time.time() + 60, nonce="model-made-nonce")
    assert "not issued by the trusted owner issuer" in g.approvals.validate(forged, digest=forged.digest, scope="t1")
    sent: list = []
    gate = g.outreach_gate(transport=lambda p: sent.append(p) or {"id": "1"})
    res = gate.send("t1", pkg, forged)
    assert not res.sent and sent == []
    with pytest.raises(OwnerAuthRefused):                                    # non-owner device cannot redeem
        g.issuer.redeem(g.issuer.challenge(task_id="t1", digest="x", scope="t1").challenge_id, "kiosk-cred")
    with pytest.raises(OwnerAuthRefused):
        g.issuer.redeem(g.issuer.challenge(task_id="t1", digest="x", scope="t1").challenge_id, "unknown-cred")


def test_owner_auth_002_authenticated_owner_decision_is_allowed_once(tmp_path, monkeypatch):
    monkeypatch.setenv(flags.EXTERNAL_OUTREACH, "1")
    g = _live(tmp_path)
    pkg = _package()
    ch = g.issuer.challenge(task_id="t1", digest=outreach_digest("t1", pkg), scope="t1")
    dec = g.issuer.redeem(ch.challenge_id, "owner-cred")
    assert dec.approver == "human:owner-phone" and dec.nonce and dec.expires_at
    sent: list = []
    gate = g.outreach_gate(transport=lambda p: sent.append(p) or {"id": "1"})
    assert gate.send("t1", pkg, dec).sent and len(sent) == 1
    with pytest.raises(OwnerAuthRefused):                                    # challenge is single-use
        g.issuer.redeem(ch.challenge_id, "owner-cred")


def test_owner_auth_003_changed_recipient_or_content_after_approval_is_denied(tmp_path, monkeypatch):
    monkeypatch.setenv(flags.EXTERNAL_OUTREACH, "1")
    g = _live(tmp_path)
    pkg = _package()
    dec = g.issuer.redeem(g.issuer.challenge(task_id="t1", digest=outreach_digest("t1", pkg), scope="t1").challenge_id, "owner-cred")
    other = _package(recipient="someone-else@example.test", text=pkg.proposal_text + " PS: pay now")
    sent: list = []
    gate = g.outreach_gate(transport=lambda p: sent.append(p) or {"id": "1"})
    res = gate.send("t1", other, dec)
    assert not res.sent and sent == []
    swapped = ApprovalDecision(True, dec.approver, dec.reason, digest=outreach_digest("t1", other), scope="t1",
                               expires_at=dec.expires_at, nonce=dec.nonce)               # same nonce, new digest
    assert "does not match the owner-issued decision" in g.approvals.validate(swapped, digest=swapped.digest, scope="t1")


def test_owner_auth_004_replay_after_restart_is_denied(tmp_path, monkeypatch):
    monkeypatch.setenv(flags.EXTERNAL_OUTREACH, "1")
    db = tmp_path / "s.sqlite"
    g = build_guards("LIVE", store_path=db, authenticate=authenticate)
    pkg = _package()
    dec = g.issuer.redeem(g.issuer.challenge(task_id="t1", digest=outreach_digest("t1", pkg), scope="t1").challenge_id, "owner-cred")
    gate = g.outreach_gate(transport=lambda p: {"id": "1"})
    assert gate.send("t1", pkg, dec).sent
    g.store.close()
    g2 = build_guards("LIVE", store=DurableSafetyStore(db), authenticate=authenticate)
    gate2 = g2.outreach_gate(transport=lambda p: {"id": "2"})
    res = gate2.send("t1", pkg, dec)
    assert not res.sent and ("replay" in res.reason or "duplicate" in res.reason or "already" in res.reason)


def test_owner_auth_005_expired_approval_is_denied(tmp_path, monkeypatch):
    monkeypatch.setenv(flags.EXTERNAL_OUTREACH, "1")
    now = {"t": 1000.0}
    g = build_guards("LIVE", store_path=tmp_path / "s.sqlite", authenticate=authenticate, clock=lambda: now["t"])
    pkg = _package()
    dec = g.issuer.redeem(g.issuer.challenge(task_id="t1", digest=outreach_digest("t1", pkg), scope="t1").challenge_id, "owner-cred")
    now["t"] += g.issuer.approval_ttl_s + 1
    assert "expired" in g.approvals.validate(dec, digest=dec.digest, scope="t1")
