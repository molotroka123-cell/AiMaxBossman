"""OUTREACH-LIVE real acceptance: public business directory (OSM Nominatim) ->
verified website problem -> demo artifact -> owner-issued authenticated
approval -> WAIT_APPROVAL. NO message is ever sent (transport refuses).

Env-gated: ordinary CI never performs external network calls; set
BOSSMAN_OUTREACH_LIVE=1 to run. Public data only; the evidence file contains
no personal data beyond what the public directory itself publishes.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import pytest

from bossman.apprentice import flags
from bossman.apprentice.durable import DurableSafetyStore
from bossman.apprentice.guards import ApprovalRegistry, SideEffectLedger
from bossman.apprentice.outreach import (OutreachGate, OutreachPackage, build_lead_card, outreach_digest,
                                         refusing_transport)
from bossman.apprentice.owner_auth import OwnerApprovalIssuer

pytestmark = [pytest.mark.live, pytest.mark.timeout(600)]

NOMINATIM = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "bossman-outreach-acceptance/1.0 (local research, no mailing)"}


class _OwnerDevice:
    def __init__(self) -> None:
        self.device_id = "owner-device-1"
        self.scopes = ("approve",)

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def _overpass(bbox: str) -> list[dict]:
    q = f'[out:json][timeout:25];node["amenity"~"cafe|bakery|restaurant"]["website"]({bbox});out center 12;'
    r = httpx.post("https://overpass-api.de/api/interpreter", data={"data": q}, headers=HEADERS, timeout=40)
    r.raise_for_status()
    out = []
    for el in r.json().get("elements", []):
        tags = el.get("tags") or {}
        if tags.get("name") and tags.get("website"):
            out.append({"name": tags["name"], "website": tags["website"], "osm_type": "node", "osm_id": el.get("id"),
                        "type": tags.get("amenity", ""), "display_name": ", ".join(
                            str(tags.get(k, "")) for k in ("addr:street", "addr:housenumber", "addr:city") if tags.get(k)),
                        "url": f"https://www.openstreetmap.org/node/{el.get('id')}"})
    return out


def _search(query: str) -> list[dict]:
    r = httpx.get(NOMINATIM, params={"q": query, "format": "jsonv2", "limit": 8, "addressdetails": 0},
                  headers=HEADERS, timeout=25)
    r.raise_for_status()
    return [x for x in r.json() if x.get("website")]


def _probe(url: str) -> dict:
    try:
        r = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        return {"status": "ok", "https": url.lower().startswith("https://"), "code": r.status_code}
    except Exception as exc:  # noqa: BLE001 — unreachable IS the verified problem
        return {"status": "unreachable", "https": url.lower().startswith("https://"), "error": type(exc).__name__}


def test_outreach_real_research_to_wait_approval(tmp_path: Path):
    if os.environ.get("BOSSMAN_OUTREACH_LIVE") != "1":
        pytest.skip("outreach live acceptance requires BOSSMAN_OUTREACH_LIVE=1 (owner-authorized)")
    flags.environ if hasattr(flags, "environ") else None
    os.environ[flags.EXTERNAL_OUTREACH] = "1"

    # 1. discovery on a real public source (bounded: two sources max)
    candidates: list[dict] = []
    try:
        candidates = _overpass("45.42,10.95,45.46,11.02")          # small bbox, public OSM data
    except Exception:  # noqa: BLE001 — source failure falls through to the next one
        candidates = []
    if not candidates:
        try:
            candidates = _overpass("45.42,10.95,45.46,11.02")
        except Exception:  # noqa: BLE001
            candidates = []
    if not candidates:
        for query in ("cafe in Verona", "bakery in Porto"):
            try:
                candidates = _search(query)
            except Exception:  # noqa: BLE001 — network failure is an honest block
                continue
            if candidates:
                break
    if not candidates:
        pytest.skip("BLOCKED_BY_ENVIRONMENT: public directory unreachable")

    # 2-6. pick a candidate whose public site has a verifiable problem
    card, probe, listing = None, None, None
    for c in candidates[:12]:
        website = str(c.get("website") or "")
        probe = _probe(website)
        if probe["status"] == "unreachable" or probe["https"] is False:
            listing = {"business_id": f"osm-{c.get('osm_type','')}{c.get('osm_id','')}", "name": c.get("name", "business"),
                       "category": str(c.get("type", "")), "city": str((c.get("display_name") or "").split(",")[-2:]),
                       "address": c.get("display_name", ""), "phone": "", "website": website,
                       "public_email": "", "contact_form_url": "", "maps_url": c.get("url", ""),
                       "rating": 0.0, "reviews_count": 0, "hours": "", "source": "public_directory"}
            card = build_lead_card(listing, site_probe=probe)
            if card.verified:
                break
    if card is None or not card.verified:
        pytest.skip("BLOCKED_BY_ENVIRONMENT: no candidate with a verifiable public-site problem in this sweep")

    # 7. demo/improvement artifact (local file, referenced by the package)
    demo = tmp_path / "demo_landing.html"
    demo.write_text("<!doctype html><html><head><title>Demo</title></head>"
                    f"<body><h1>{card.name}: fast, secure landing demo</h1>"
                    f"<p>Proposal: replace the {card.problem} site with a simple secure page.</p></body></html>",
                    encoding="utf-8")

    # 8. package for the owner
    package = OutreachPackage(card=card, reason=f"public site problem: {card.problem} (evidence: {list(card.problem_evidence)})",
                              demo_ref=str(demo), proposal_text="Short, factual note offering the demo. No follow-ups.",
                              recipient=card.public_email or card.contact_form_url or card.website)
    task_id = "LFZ-OUTREACH-001"
    digest = outreach_digest(task_id, package)
    owner_view = package.owner_view()

    # 9-10. authenticated owner approval: challenge -> credential -> issued decision
    store = DurableSafetyStore(tmp_path / "outreach.db")
    issuer = OwnerApprovalIssuer(store, authenticate=lambda cred: _OwnerDevice() if cred == "owner-device-credential" else None)
    registry = ApprovalRegistry(store=store, live=True)
    gate = OutreachGate(ledger=SideEffectLedger(store=store), approvals=registry, transport=refusing_transport)
    challenge = issuer.challenge(task_id=task_id, digest=digest, scope=task_id)
    approval = issuer.redeem(challenge.challenge_id, "owner-device-credential")

    # WAIT_APPROVAL: the gate would allow THIS exact package, and we do not send.
    assert gate.refusal(task_id, package, approval) == "", "issued owner approval must validate the exact package"
    assert gate.sent_log == []                                   # nothing was sent
    assert all(k in owner_view for k in ("business_found", "reason", "demo", "proposal_text", "intended_recipient"))

    # 11. invariants: model-forged, replayed, tampered, expired are all denied
    from bossman.company.model import ApprovalDecision
    forged = ApprovalDecision(True, "human:owner", "self issued", digest=digest, scope=task_id, nonce="model-minted")
    assert "not issued by the trusted owner issuer" in gate.refusal(task_id, package, forged)
    replay = issuer.redeem.__self__  # noqa: F841 — replay via second redeem of the SAME challenge must fail
    with pytest.raises(PermissionError):
        issuer.redeem(challenge.challenge_id, "owner-device-credential")
    tampered = OutreachPackage(card=card, reason=package.reason, demo_ref=package.demo_ref,
                               proposal_text=package.proposal_text, recipient="someone-else@example.test")
    assert gate.refusal(task_id, tampered, approval) != "", "modified recipient must be denied (digest binding)"
    expired = ApprovalDecision(True, approval.approver, approval.reason, digest=digest, scope=task_id,
                               expires_at=time.time() - 1, nonce=approval.nonce)
    assert "expired" in registry.validate(expired, digest=digest, scope=task_id)

    # 12. evidence artifact (public data + digests only)
    evidence = {"source": "OSM Nominatim (public directory)", "listing": {k: listing[k] for k in
                ("business_id", "name", "category", "website", "maps_url", "source")},
                "probe": {k: probe[k] for k in ("status", "https") if k in probe},
                "verified_problem": card.problem, "problem_evidence": list(card.problem_evidence),
                "demo_ref": str(demo), "content_digest": package.content_digest, "approval_digest": digest,
                "state": "WAIT_APPROVAL", "sent": False}
    out = Path(os.environ.get("BOSSMAN_OUTREACH_EVIDENCE", str(tmp_path / "outreach_evidence.json")))
    out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
