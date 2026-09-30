"""Dual-approval gate: Claude AND Codex must APPROVE the same immutable candidate.

A candidate is the tuple (sha, diff_sha256, evidence_sha256) - the commit, the
exact diff bytes and the hash of the Bossman-executed test evidence. Rules:

* an approval counts only for the current tuple; a new commit, a different diff
  or different test evidence invalidates BOTH approvals (journaled);
* review order is sequential, Claude then Codex, read-only sessions;
* a review from the writer's own session is refused; when the author of the
  revision is one of the reviewing CLIs, its approval never stands alone - it
  counts only together with the other agent's independent approval of the same
  tuple (neither can approve its own unreviewed revision); Nemotron never approves;
* reviewer output is parsed strictly: exactly one JSON verdict object that echoes
  the exact sha and diff hash; anything else is not an approval;
* REJECT, a malformed or timed-out review, or a reviewer that wrote to its
  checkout -> BLOCKED; REQUEST_CHANGES -> a revision; disagreement (one approves,
  the other does not) counts, and reaching `max_disagreements` -> BLOCKED.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .types import Review
from .workers import json_objects

VERDICTS = ("APPROVE", "REQUEST_CHANGES", "REJECT")
REVIEWERS = ("claude", "codex")
_REVIEW_KEYS = {"verdict", "notes", "sha", "diff_sha256"}


class JournalPort(Protocol):
    def append(self, kind: str, payload: dict) -> str: ...


@dataclass(frozen=True)
class Candidate:
    sha: str
    diff_sha256: str
    evidence_sha256: str
    author: str                  # claude | codex | nemotron
    author_session: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.sha, self.diff_sha256, self.evidence_sha256)


def parse_review(text: str, *, goal_id: str, reviewer: str, candidate: Candidate) -> tuple[Review | None, str]:
    """Strict: exactly one verdict object; fields exactly the schema; the echoed sha
    and diff hash must be the candidate's. Returns (review, "") or (None, reason)."""
    if reviewer not in REVIEWERS:
        return None, "unknown reviewer"
    objs = json_objects(text or "", "verdict")
    if not objs:
        return None, "no verdict object"
    distinct = {(str(o.get("verdict")), str(o.get("sha"))) for o in objs}
    if len(distinct) > 1:
        return None, "conflicting verdict objects"
    obj = objs[-1]
    if set(obj) != _REVIEW_KEYS:
        return None, f"fields must be exactly {sorted(_REVIEW_KEYS)}"
    if obj["verdict"] not in VERDICTS:
        return None, "verdict must be APPROVE, REQUEST_CHANGES or REJECT"
    if not isinstance(obj["notes"], str):
        return None, "notes must be a string"
    if obj["sha"] != candidate.sha or obj["diff_sha256"] != candidate.diff_sha256:
        return None, "review is not bound to the candidate sha/diff"
    return Review(goal_id=goal_id, reviewer=reviewer, sha=candidate.sha, diff_sha256=candidate.diff_sha256,
                  verdict=obj["verdict"], notes=obj["notes"][:4000]), ""


@dataclass
class GateState:
    goal_id: str
    candidate: dict | None = None
    verdicts: dict[str, dict] = field(default_factory=dict)     # reviewer -> {verdict, notes, key, order}
    disagreements: int = 0
    invalidations: int = 0
    blocked_reason: str = ""
    history: list[dict] = field(default_factory=list)


class ReviewGate:
    def __init__(self, goal_id: str, *, max_disagreements: int = 1, journal: JournalPort | None = None,
                 state: dict | None = None):
        self.max_disagreements = max(1, int(max_disagreements))
        self.journal = journal
        self.s = GateState(goal_id=goal_id)
        if state:
            self.s = GateState(**state)

    # ---- persistence
    def to_dict(self) -> dict:
        return asdict(self.s)

    def _log(self, kind: str, payload: dict) -> None:
        self.s.history.append({"kind": kind, **payload})
        if self.journal is not None:
            self.journal.append(kind, {"goal_id": self.s.goal_id, **payload})

    @property
    def candidate(self) -> Candidate | None:
        return Candidate(**self.s.candidate) if self.s.candidate else None

    # ---- candidate
    def set_candidate(self, cand: Candidate) -> bool:
        """Bind the gate to a candidate. A different tuple invalidates every verdict.
        Returns True when earlier verdicts were invalidated."""
        old = self.candidate
        if old is not None and old.key == cand.key and old.author == cand.author:
            return False
        invalidated = sorted(self.s.verdicts)
        self.s.verdicts = {}
        self.s.candidate = asdict(cand)
        if old is not None:
            self.s.invalidations += 1
            self._log("approvals_invalidated", {"old": list(old.key), "new": list(cand.key),
                                                "invalidated": invalidated})
        return bool(invalidated)

    # ---- verdicts
    def submit(self, review: Review, *, evidence_sha256: str, session_id: str) -> str:
        """Record one parsed review. Returns accepted | stale | self_session | no_candidate | blocked."""
        cand = self.candidate
        if self.s.blocked_reason:
            return "blocked"
        if cand is None:
            return "no_candidate"
        if (review.sha, review.diff_sha256, evidence_sha256) != cand.key or review.goal_id != self.s.goal_id:
            self._log("review_stale", {"reviewer": review.reviewer, "sha": review.sha})
            return "stale"
        if cand.author_session and session_id == cand.author_session:
            self._log("review_refused", {"reviewer": review.reviewer, "reason": "writer session cannot review"})
            return "self_session"
        order = len(self.s.verdicts) + 1
        self.s.verdicts[review.reviewer] = {"verdict": review.verdict, "notes": review.notes,
                                            "key": list(cand.key), "order": order, "session_id": session_id,
                                            "own_revision": review.reviewer == cand.author}
        self._log("review_recorded", {"reviewer": review.reviewer, "verdict": review.verdict, "sha": cand.sha,
                                      "diff_sha256": cand.diff_sha256, "evidence_sha256": cand.evidence_sha256,
                                      "own_revision": review.reviewer == cand.author})
        if review.verdict == "REJECT":
            self._block(f"rejected by {review.reviewer}")
            return "accepted"
        verdicts = {r: v["verdict"] for r, v in self.s.verdicts.items()}
        if "APPROVE" in verdicts.values() and "REQUEST_CHANGES" in verdicts.values():
            self.s.disagreements += 1
            self._log("reviewers_disagree", {"count": self.s.disagreements, "verdicts": verdicts})
            if self.s.disagreements >= self.max_disagreements:
                self._block(f"reviewers disagree ({self.s.disagreements}x)")
        return "accepted"

    def record_failure(self, reviewer: str, reason: str) -> None:
        """Malformed, timed out or writing reviewer: not an approval, and the state is
        ambiguous -> BLOCKED."""
        self._log("review_failed", {"reviewer": reviewer, "reason": reason})
        self._block(f"review by {reviewer} unusable: {reason}")

    def _block(self, reason: str) -> None:
        if not self.s.blocked_reason:
            self.s.blocked_reason = reason
            self._log("review_blocked", {"reason": reason})

    # ---- outcome
    def approvals(self) -> dict[str, bool]:
        cand = self.candidate
        out = {}
        for r in REVIEWERS:
            v = self.s.verdicts.get(r)
            out[r] = bool(cand and v and v["verdict"] == "APPROVE" and tuple(v["key"]) == cand.key)
        return out

    def status(self) -> str:
        """PENDING | APPROVED | CHANGES_REQUESTED | BLOCKED."""
        if self.s.blocked_reason:
            return "BLOCKED"
        if any(v["verdict"] == "REQUEST_CHANGES" for v in self.s.verdicts.values()):
            return "CHANGES_REQUESTED"
        appr = self.approvals()
        if all(appr.values()):
            # an author approval stands only with the other agent's independent approval
            return "APPROVED"
        return "PENDING"

    @property
    def blocked_reason(self) -> str:
        return self.s.blocked_reason

    @property
    def disagreements(self) -> int:
        return self.s.disagreements

    def change_notes(self) -> str:
        return "\n".join(f"{r}: {v['notes']}" for r, v in sorted(self.s.verdicts.items())
                         if v["verdict"] == "REQUEST_CHANGES")

    def evidence(self) -> dict[str, Any]:
        cand = self.candidate
        return {"candidate": asdict(cand) if cand else None, "verdicts": dict(self.s.verdicts),
                "approvals": self.approvals(), "status": self.status(), "disagreements": self.s.disagreements,
                "invalidations": self.s.invalidations, "blocked_reason": self.s.blocked_reason}
