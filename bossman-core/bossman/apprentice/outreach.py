"""Outreach boundary (flag BOSSMAN_EXTERNAL_OUTREACH).

Public business data only; the owner sees the full package (business found,
reason, current site link, demo, proposal text, intended recipient); a message
leaves only with a one-time ApprovalDecision whose digest binds task + recipient
+ content; duplicate external effects are prevented by side_effect_id; no mass
mailing, no re-sending, no bypassing blocks, no non-public personal data.
The default transport refuses (no live sends from tests)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from bossman.company.model import ApprovalDecision

from . import flags
from ._bootstrap import trace
from .errors import FlagDisabled, OutreachRefused, PersonalDataRefused
from .guards import ApprovalRegistry, SideEffectLedger
from .models import sha

PUBLIC_FIELDS = frozenset({"business_id", "name", "category", "city", "address", "phone", "website", "rating",
                           "reviews_count", "maps_url", "public_email", "contact_form_url", "hours", "source"})
_FORBIDDEN_TOKENS = ("personal", "private", "home_", "owner_", "dob", "birth", "ssn", "passport", "salary", "health")
PUBLIC_SOURCES = frozenset({"google_maps_public", "public_directory", "business_website"})
VERIFIED_PROBLEMS = frozenset({"no_website", "site_unreachable", "no_https", "outdated_site", "mobile_broken"})
DEFAULT_COOLDOWN_S = 30 * 24 * 3600


def _check_public(listing: dict) -> None:
    for k in listing:
        kl = str(k).lower()
        if kl not in PUBLIC_FIELDS or any(t in kl for t in _FORBIDDEN_TOKENS):
            raise PersonalDataRefused(f"field {k!r} is not public business data")
    if str(listing.get("source", "")) not in PUBLIC_SOURCES:
        raise PersonalDataRefused(f"source {listing.get('source')!r} is not a public business source")


@dataclass(frozen=True, slots=True)
class LeadCard:
    business_id: str
    name: str
    category: str
    city: str
    website: str
    phone: str
    public_email: str
    contact_form_url: str
    maps_url: str
    rating: float
    reviews_count: int
    problem: str
    problem_evidence: tuple[str, ...]
    verified: bool
    site_risk: str = "unknown"

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__} | {"problem_evidence": list(self.problem_evidence)}


def _site_risk(url: str) -> str:
    if not url:
        return "none"
    try:
        from bossman.toolkit.browser import domain_risk  # lazy: optional dependency for tests
        r = domain_risk(url)
        return str(getattr(r, "value", r))
    except Exception:  # noqa: BLE001
        return "unknown"


def build_lead_card(listing: dict, *, site_probe: dict | None) -> LeadCard:
    """`site_probe` is a fresh observation of the business site: {status: ok|no_site|unreachable, https: bool,
    last_updated_days: int, mobile_ok: bool}. Without a probe the problem is unverified and the card cannot be sent."""
    _check_public(listing)
    tr = trace()
    website = str(listing.get("website") or "")
    problem, evidence, verified = "unverified", [], False
    if site_probe:
        st = str(site_probe.get("status", ""))
        if not website or st == "no_site":
            problem, verified = "no_website", True; evidence.append("listing has no website / probe: no_site")
        elif st == "unreachable":
            problem, verified = "site_unreachable", True; evidence.append(f"probe status {st}")
        elif site_probe.get("https") is False:
            problem, verified = "no_https", True; evidence.append("probe: no https")
        elif site_probe.get("mobile_ok") is False:
            problem, verified = "mobile_broken", True; evidence.append("probe: mobile layout broken")
        elif int(site_probe.get("last_updated_days") or 0) > 730:
            problem, verified = "outdated_site", True; evidence.append(f"probe: last update {site_probe.get('last_updated_days')} days ago")
        else:
            problem, verified = "none", False; evidence.append("probe: site is fine — no outreach reason")
    return LeadCard(business_id=str(listing.get("business_id") or sha("biz", listing.get("name"), listing.get("city"))[:12]),
                    name=tr.redact_text(str(listing.get("name", ""))), category=str(listing.get("category", "")),
                    city=str(listing.get("city", "")), website=website, phone=str(listing.get("phone", "")),
                    public_email=str(listing.get("public_email", "")), contact_form_url=str(listing.get("contact_form_url", "")),
                    maps_url=str(listing.get("maps_url", "")), rating=float(listing.get("rating") or 0.0),
                    reviews_count=int(listing.get("reviews_count") or 0), problem=problem, problem_evidence=tuple(evidence),
                    verified=verified and problem in VERIFIED_PROBLEMS, site_risk=_site_risk(website))


@dataclass(frozen=True, slots=True)
class OutreachPackage:
    card: LeadCard
    reason: str
    demo_ref: str
    proposal_text: str
    recipient: str
    created_at: float = field(default_factory=time.time)

    @property
    def content_digest(self) -> str:
        return sha("content", self.proposal_text, self.demo_ref, self.recipient)

    def side_effect_id(self) -> str:
        return sha("outreach-effect", self.recipient, self.content_digest)[:32]

    def owner_view(self) -> dict:
        """Everything the owner must see before approving."""
        return {"business_found": {"name": self.card.name, "category": self.card.category, "city": self.card.city,
                                   "maps_url": self.card.maps_url, "rating": self.card.rating, "reviews": self.card.reviews_count},
                "reason": self.reason, "verified_problem": self.card.problem, "problem_evidence": list(self.card.problem_evidence),
                "current_site_link": self.card.website or "(none)", "site_risk": self.card.site_risk,
                "demo": self.demo_ref, "proposal_text": self.proposal_text, "intended_recipient": self.recipient,
                "content_digest": self.content_digest}


def outreach_digest(task_id: str, package: OutreachPackage) -> str:
    """Approval identity: task + recipient + content."""
    return sha("outreach", task_id, package.recipient, package.content_digest)


@dataclass(frozen=True, slots=True)
class SendResult:
    sent: bool
    reason: str
    side_effect_id: str
    recipient: str


def refusing_transport(package: OutreachPackage) -> dict:
    raise OutreachRefused("no live transport configured (simulated environment)")


class OutreachGate:
    def __init__(self, *, ledger: SideEffectLedger | None = None, approvals: ApprovalRegistry | None = None,
                 transport: Callable[[OutreachPackage], dict] | None = None, max_per_run: int = 5,
                 cooldown_s: int = DEFAULT_COOLDOWN_S, clock: Callable[[], float] = time.time) -> None:
        self.ledger = ledger if ledger is not None else SideEffectLedger()
        self.approvals = approvals or ApprovalRegistry(clock)
        self.transport = transport or refusing_transport
        self.max_per_run, self.cooldown_s, self.clock = max_per_run, cooldown_s, clock
        self.blocked: set[str] = set()
        self.sent_log: list[dict] = []
        self._last_sent: dict[str, float] = {}
        self._per_run: dict[str, int] = {}

    def block(self, recipient: str, reason: str = "") -> None:
        self.blocked.add(recipient.lower())
        if getattr(self.ledger, "store", None) is not None:
            self.ledger.store.block_recipient(recipient, reason)

    def refusal(self, task_id: str, package: OutreachPackage, approval: Any) -> str:
        """Empty string = may send. Every rule is checked before any external effect."""
        if not flags.enabled(flags.EXTERNAL_OUTREACH):
            return f"{flags.EXTERNAL_OUTREACH} is off"
        if not package.recipient or "@" not in package.recipient and not package.recipient.startswith("http"):
            return "recipient must be a public business email or contact form url"
        if not package.card.verified:
            return f"problem not verified ({package.card.problem}); no outreach without a verified reason"
        if package.recipient.lower() in self.blocked or (getattr(self.ledger, "store", None) is not None and self.ledger.store.recipient_blocked(package.recipient)):
            return "recipient is blocked; blocks are never bypassed"
        if self._per_run.get(task_id, 0) >= self.max_per_run:
            return f"per-run recipient cap {self.max_per_run} reached (no mass mailing)"
        if self.ledger.seen(package.side_effect_id()):
            return "duplicate external effect (same recipient + content already sent)"
        last = self._last_sent.get(package.recipient.lower())
        durable_until = self.ledger.store.get_cooldown(package.recipient) if getattr(self.ledger, "store", None) is not None else None
        if (last is not None and self.clock() - last < self.cooldown_s) or (durable_until is not None and self.clock() < durable_until):
            return "recipient contacted recently; no re-sending inside the cooldown"
        why = self.approvals.validate(approval, digest=outreach_digest(task_id, package), scope=task_id)
        if why:
            return f"approval invalid: {why}"
        return ""

    def send(self, task_id: str, package: OutreachPackage, approval: Any) -> SendResult:
        seid = package.side_effect_id()
        why = self.refusal(task_id, package, approval)
        if why:
            return SendResult(False, why, seid, package.recipient)
        claimed, _ = self.ledger.claim(seid)
        if not claimed:
            return SendResult(False, "duplicate external effect (concurrent claim)", seid, package.recipient)
        try:
            receipt = self.transport(package)
        except Exception as exc:  # noqa: BLE001 — transport failure: nothing sent, approval stays unconsumed
            self.ledger.abandon(seid)
            return SendResult(False, f"transport refused: {exc}", seid, package.recipient)
        self.approvals.consume(approval)
        self.ledger.complete(seid, {"receipt": trace().redact_obj(dict(receipt or {})), "at": self.clock()})
        self._last_sent[package.recipient.lower()] = self.clock()
        if getattr(self.ledger, "store", None) is not None:
            self.ledger.store.set_cooldown(package.recipient, self.clock() + self.cooldown_s)
        self._per_run[task_id] = self._per_run.get(task_id, 0) + 1
        self.sent_log.append({"task_id": task_id, "recipient": package.recipient, "content_digest": package.content_digest,
                              "side_effect_id": seid, "at": self.clock()})
        return SendResult(True, "sent", seid, package.recipient)


def approve_outreach(task_id: str, package: OutreachPackage, *, approver: str, nonce: str, expires_at: float | None) -> ApprovalDecision:
    """Helper for the owner UI: builds a correctly bound one-time approval (tests / owner surface)."""
    if not approver.startswith("human:"):
        raise OutreachRefused("outreach approvals must come from a human approver")
    return ApprovalDecision(True, approver, "owner approved outreach", digest=outreach_digest(task_id, package), scope=task_id,
                            expires_at=expires_at, nonce=nonce)
