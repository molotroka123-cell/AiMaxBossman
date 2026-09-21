"""Coaching lessons on top of the canonical learning store (no second engine).

A *coaching episode* is: attempt -> observable failure -> correction. It is stored
as a ``record_type="lesson"`` record of ``schemas/apprentice_skill.schema.json``
through ``learning.trace.LearningStore`` — the same store, schema, journal,
versioning, redaction and locking that ``bossman.apprentice.recording.ApprenticeMemory``
and the Deep Fix runtime already use. Nothing here re-implements storage.

What this module adds (thin, additive):
  * episode schema (``CoachingEpisode``): attempt/task/project ids, failure
    observation, correction, source=teacher|student, kind=student_fix|teacher_patch,
    status=candidate|verified|withdrawn, provenance (who/what/when/evidence refs),
    deterministic ``dedup_key``;
  * deduplication: the same lesson written twice is ONE record (same task_id =>
    the store makes it a version, ``lesson.occurrences`` is bumped);
  * project isolation: a lesson of project A is never retrieved for project B
    unless ``scope="global"`` AND it is VERIFIED;
  * teacher/student attribution: a teacher patch is recorded as ``teacher_patch``
    and carries ``student_success=False`` — it can never be counted as a student pass;
  * candidate vs verified: candidates map to learning_status UNVERIFIED and are
    never returned by ``retrieve`` (the store only serves VERIFIED); verification
    needs an independent verifier + fresh evidence (store invariants, not ours);
  * rollback: ``withdraw`` demotes a verified lesson to REJECTED; retrieval stops;
  * untrusted-lesson filtering: a lesson body must be advice text. Denylist +
    structural rule reject poisoned lessons at write time and again at read time.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import trace as _trace

SOURCES = ("teacher", "student")
KINDS = ("student_fix", "teacher_patch")
STATUSES = ("candidate", "verified", "withdrawn")
SCOPES = ("project", "global")
LESSON_TASK_PREFIX = "coach-lesson:"
MIN_BODY_CHARS = 8
MAX_BODY_CHARS = 2000

_STATUS_TO_LEARNING = {"candidate": "UNVERIFIED", "verified": "VERIFIED", "withdrawn": "REJECTED"}


def lesson_schema_path() -> Path:
    return _trace._schema_dir() / "apprentice_skill.schema.json"


def load_lesson_schema() -> dict:
    return json.loads(lesson_schema_path().read_text(encoding="utf-8"))


class LessonPoisoned(ValueError):
    """The lesson body is not advice text (tries to change permissions, budgets,
    approvals, owner authority, or is a permissions/tool JSON blob)."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class LessonError(ValueError):
    pass


# ---------------------------------------------------------------- poison filter
# Denylist: imperative content aimed at Bossman's control plane. The verb/object
# pairing keeps innocent mentions ("round the budget to 2 decimals") legal.
_CONTROL_OBJECTS = (r"(?:owner(?:'s)?|approval|approvals|approver|review\s+gate|policy|policies|guard|guards|"
                    r"permission|permissions|budget|budgets|spend\s+limit|cost\s+limit|sandbox|allowlist|"
                    r"tool\s+access|safety\s+check|verification)")
_DENY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("override_control", re.compile(
        r"(?i)\b(?:ignore|bypass|skip|disable|override|remove|lift|circumvent|suppress|turn\s+off)\b"
        r"[^.\n]{0,40}?\b" + _CONTROL_OBJECTS + r"\b")),
    ("raise_budget", re.compile(
        r"(?i)\b(?:raise|increase|extend|unlimited|unlimit|max\s+out|set)\b[^.\n]{0,30}?\b"
        r"(?:budget|budgets|spend\s+limit|cost\s+limit)\b")),
    ("grant_permission", re.compile(
        r"(?i)\b(?:grant|give|allow|enable)\b[^.\n]{0,30}?\b(?:yourself|the\s+agent|the\s+model|all)\b"
        r"[^.\n]{0,30}?\b(?:permission|permissions|access|tools?)\b")),
    ("auto_approve", re.compile(r"(?i)\bauto[-\s]?approv\w*|\bwithout\s+(?:the\s+)?(?:owner(?:'s)?\s+)?(?:approval|review)\b"
                                r"|\bapprove\s+(?:everything|all|automatically)\b")),
    ("owner_authority", re.compile(r"(?i)\b(?:you\s+are\s+(?:now\s+)?the\s+owner|act\s+as\s+(?:the\s+)?owner|"
                                   r"the\s+owner\s+(?:said|says|allows|allowed)\s+you\s+to\s+(?:skip|ignore|bypass))\b")),
    ("prompt_injection", re.compile(r"(?i)(?:<\s*/?\s*system\s*>|\[/?INST\]|^\s*###?\s*system\b|^\s*system\s*:|"
                                    r"\bnew\s+instructions?\s*:|\bignore\s+(?:all\s+)?previous\s+instructions)", re.M)),
    ("env_flag_flip", re.compile(r"(?i)\bBOSSMAN_[A-Z_]+\s*(?:=|:)\s*(?:1|true|yes|on)\b|os\.environ\[\s*['\"]BOSSMAN_")),
)
# Structural: a permissions/budget/tool configuration shape is not advice.
_STRUCTURAL_KEYS = re.compile(
    r"(?i)[\"']?\b(?:allowed_tools|tool_permissions|permissions|budget_usd|max_budget|budget_limit|"
    r"approval_required|require_approval|auto_approve|allowlist|denylist|owner_override)\b[\"']?\s*[:=]")


def poison_reasons(text: str) -> list[str]:
    """Why ``text`` must not become a lesson (empty list = acceptable advice text)."""
    reasons: list[str] = []
    if not isinstance(text, str):
        return ["lesson body must be a string"]
    body = text.strip()
    if len(body) < MIN_BODY_CHARS:
        reasons.append("lesson body too short to be advice")
    if len(body) > MAX_BODY_CHARS:
        reasons.append("lesson body too long (not a compact lesson)")
    # structural: JSON object/array as the whole body => configuration, not advice
    if body[:1] in "{[":
        try:
            parsed = json.loads(body)
            if isinstance(parsed, (dict, list)):
                reasons.append("structural: lesson body is a JSON document, not advice text")
        except json.JSONDecodeError:
            pass
    if _STRUCTURAL_KEYS.search(body):
        reasons.append("structural: permissions/budget/tool configuration keys inside a lesson")
    for name, rx in _DENY_PATTERNS:
        if rx.search(body):
            reasons.append(f"denylist:{name}")
    if _trace.has_secret(body):
        reasons.append("secret-like value in lesson body")
    return reasons


def assert_not_poisoned(text: str) -> None:
    reasons = poison_reasons(text)
    if reasons:
        raise LessonPoisoned(reasons)


# ---------------------------------------------------------------- episode schema
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def dedup_key(*, project_id: str, task_class: str, correction: str, scope: str = "project") -> str:
    """Same lesson (same project/scope/class, same normalized correction) => same key."""
    raw = f"{scope}|{project_id if scope == 'project' else '*'}|{_norm(task_class)}|{_norm(correction)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class Provenance:
    who: str                       # principal that produced the correction (student/teacher id)
    what: str                      # short description of the producing action
    when: str = ""                 # ISO-8601 UTC; filled at save time when empty
    evidence_refs: list[str] = field(default_factory=list)   # test names, log paths, run ids
    run_id: str = ""
    model: str = ""

    def as_dict(self) -> dict:
        return {"who": self.who, "what": self.what, "when": self.when,
                "evidence_refs": list(self.evidence_refs), "run_id": self.run_id, "model": self.model}


@dataclass(slots=True)
class CoachingEpisode:
    attempt_id: str
    task_id: str
    project_id: str
    failure_observation: str
    correction: str                # the lesson body (advice text) — what to do differently
    source: str                    # teacher | student
    provenance: Provenance
    task_class: str = "general"
    kind: str = "student_fix"      # student_fix | teacher_patch
    status: str = "candidate"      # candidate | verified | withdrawn
    scope: str = "project"         # project | global
    environment: str = "unknown-env"
    agent: str = "bossman-student"
    model: str = ""
    title: str = ""

    def validate(self) -> list[str]:
        errs = []
        for name in ("attempt_id", "task_id", "project_id", "failure_observation", "correction"):
            if not str(getattr(self, name) or "").strip():
                errs.append(f"{name} is required")
        if self.source not in SOURCES:
            errs.append(f"source must be one of {SOURCES}")
        if self.kind not in KINDS:
            errs.append(f"kind must be one of {KINDS}")
        if self.status not in STATUSES:
            errs.append(f"status must be one of {STATUSES}")
        if self.scope not in SCOPES:
            errs.append(f"scope must be one of {SCOPES}")
        if self.kind == "teacher_patch" and self.source != "teacher":
            errs.append("teacher_patch must have source=teacher")
        if self.status == "verified":
            errs.append("status=verified is set only by LessonBook.verify (independent verifier + evidence)")
        return errs

    @property
    def dedup_key(self) -> str:
        return dedup_key(project_id=self.project_id, task_class=self.task_class,
                         correction=self.correction, scope=self.scope)

    @property
    def lesson_id(self) -> str:
        return LESSON_TASK_PREFIX + self.dedup_key


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def lesson_record(ep: CoachingEpisode) -> dict:
    """Schema record (apprentice_skill.schema.json, record_type=lesson) for the episode."""
    errs = ep.validate()
    if errs:
        raise LessonError("; ".join(errs))
    assert_not_poisoned(ep.correction)
    prov = ep.provenance.as_dict()
    prov["when"] = prov["when"] or _now_iso()
    return {
        "task_id": ep.lesson_id, "record_type": "lesson",
        "learning_status": _STATUS_TO_LEARNING[ep.status],
        "title": ep.title or f"lesson[{ep.task_class}]: {ep.correction[:72]}",
        "summary": ep.correction[:500],
        "task_type": ep.task_class, "environment": ep.environment,
        "agent": ep.agent, "model": ep.model or prov.get("model") or "unknown-model",
        "principal_id": prov["who"], "run_id": prov.get("run_id") or "",
        "start_sha": "", "end_sha": "",               # dedup identity must not depend on HEAD
        "lessons": [ep.correction],
        "lesson": {
            "dedup_key": ep.dedup_key, "project_id": ep.project_id, "scope": ep.scope,
            "task_class": ep.task_class, "status": ep.status, "source": ep.source, "kind": ep.kind,
            "student_success": False if ep.kind == "teacher_patch" else None,
            "failure_observation": ep.failure_observation[:2000], "correction": ep.correction,
            "attempt_ids": [ep.attempt_id], "source_task_ids": [ep.task_id],
            "occurrences": 1, "provenance": [prov], "verification": None, "withdrawal": None,
        },
        "source_episode_ids": [ep.task_id],
        "outcome": "TEACHER_PATCH" if ep.kind == "teacher_patch" else "STUDENT_CORRECTION",
        "tags": {"domain": ep.task_class, "risk": "low"},
    }


def compact_lesson(rec: dict) -> dict:
    """Retrieval shape: what a student prompt gets (never the raw store record)."""
    l = rec.get("lesson") or {}
    return {"lesson_id": rec.get("task_id"), "version": rec.get("version"),
            "status": l.get("status"), "learning_status": rec.get("learning_status"),
            "source": l.get("source"), "kind": l.get("kind"), "student_success": l.get("student_success"),
            "project_id": l.get("project_id"), "scope": l.get("scope"), "task_class": l.get("task_class"),
            "failure_observation": l.get("failure_observation", ""), "correction": l.get("correction", ""),
            "occurrences": l.get("occurrences", 1), "provenance": list(l.get("provenance") or []),
            "verification": l.get("verification"), "verified_by": list(rec.get("verified_by") or [])}


# ---------------------------------------------------------------- the book
class LessonBook:
    """Coaching lessons through ``learning.trace.LearningStore`` (canonical store).

    ``data_dir`` is the corpus directory (same layout as ApprenticeMemory: journal.jsonl,
    fix_cases.jsonl, failed_experiments.jsonl, history.jsonl). A new LessonBook on the
    same directory sees everything the previous one wrote — that is the restart path."""

    def __init__(self, data_dir: Path | str, docs_dir: Path | None = None):
        self.data_dir = Path(data_dir)
        self.schema = load_lesson_schema()
        self.store = _trace.LearningStore(self.data_dir, Path(docs_dir or self.data_dir / "docs"), schema=self.schema)
        self.filtered_at_read = 0          # poisoned records dropped by retrieve (defense in depth)
        self.rejected_at_write = 0

    # ------------------------------------------------------------ write
    def save(self, ep: CoachingEpisode) -> dict:
        """Write (or dedup-bump) a candidate lesson. Poisoned => LessonPoisoned, nothing written."""
        try:
            rec = lesson_record(ep)
        except LessonPoisoned:
            self.rejected_at_write += 1
            raise
        prev = self._current_by_task_id(rec["task_id"])
        if prev is not None:
            merged = json.loads(json.dumps(prev))       # deep copy of the authoritative version
            l = merged.setdefault("lesson", {})
            if ep.attempt_id not in l.setdefault("attempt_ids", []):
                l["attempt_ids"].append(ep.attempt_id)
                l["occurrences"] = int(l.get("occurrences") or 1) + 1
            if ep.task_id not in l.setdefault("source_task_ids", []):
                l["source_task_ids"].append(ep.task_id)
            l.setdefault("provenance", []).append(rec["lesson"]["provenance"][0])
            l["provenance"] = l["provenance"][-20:]
            # a teacher patch never upgrades to student success by being seen again
            if ep.kind == "teacher_patch" or l.get("kind") == "teacher_patch":
                l["kind"] = "teacher_patch"; l["student_success"] = False
                merged["outcome"] = "TEACHER_PATCH"
            for k in ("case_id", "version", "supersedes_version", "created_at"):
                merged.pop(k, None)
            return self.store.add(merged, write_markdown=False)
        return self.store.add(rec, write_markdown=False)

    def verify(self, lesson_id: str, *, verifier: dict, evidence: dict, statement: str = "") -> dict:
        """candidate -> verified. ``verifier`` = {principal_id, independence_class, model_id?, run_id?};
        ``evidence`` = {source, expected, actual, head_sha, environment, observed_at?, collected_at?}.
        The store refuses self-verification and stale/unbound evidence (learning.trace invariants)."""
        rec = self._require(lesson_id)
        if (rec.get("lesson") or {}).get("status") == "withdrawn":
            raise LessonError(f"{lesson_id}: withdrawn lessons are not re-verified implicitly")
        now = time.time()
        observed = float(evidence.get("observed_at") or now)
        collected = float(evidence.get("collected_at") or max(observed, now))
        ev_rec = {"observed_at": observed, "collected_at": collected,
                  "source": str(evidence.get("source") or verifier.get("principal_id") or ""),
                  "principal_id": str(verifier.get("principal_id") or ""),
                  "head_sha": str(evidence.get("head_sha") or "unknown"),
                  "environment": str(evidence.get("environment") or rec.get("environment") or "unknown-env"),
                  "task_id": rec["task_id"], "run_id": rec.get("run_id") or "",
                  "expected": str(evidence.get("expected") or ""), "actual": str(evidence.get("actual") or "")}
        merged = json.loads(json.dumps(rec))
        merged["learning_status"] = "VERIFIED"
        merged["verifiers"] = [{"principal_id": verifier.get("principal_id", ""),
                                "independence_class": verifier.get("independence_class", ""),
                                "model_id": verifier.get("model_id", ""), "run_id": verifier.get("run_id", "")}]
        merged["verified_by"] = [str(verifier.get("principal_id") or "")]
        merged["evidence"] = [f"{ev_rec['source']}: expected={ev_rec['expected']} actual={ev_rec['actual']}"]
        merged["evidence_records"] = [ev_rec]
        merged["external_verification"] = statement or f"{ev_rec['source']} confirmed the correction"
        merged["verified"] = True
        l = merged.setdefault("lesson", {})
        l["status"] = "verified"
        l["verification"] = {"verifier": merged["verifiers"][0], "at": _now_iso(), "evidence_refs": [ev_rec["source"]]}
        for k in ("case_id", "version", "supersedes_version", "created_at"):
            merged.pop(k, None)
        return self.store.add(merged, write_markdown=False)

    def withdraw(self, lesson_id: str, *, by: str, reason: str) -> dict:
        """Rollback: verified/candidate -> withdrawn (REJECTED). Retrieval stops returning it."""
        rec = self._require(lesson_id)
        merged = json.loads(json.dumps(rec))
        merged["learning_status"] = "REJECTED"
        merged["verified"] = False
        l = merged.setdefault("lesson", {})
        l["status"] = "withdrawn"
        l["withdrawal"] = {"by": by, "reason": reason, "at": _now_iso(),
                           "previous_status": (rec.get("lesson") or {}).get("status")}
        for k in ("case_id", "version", "supersedes_version", "created_at"):
            merged.pop(k, None)
        return self.store.add(merged, write_markdown=False)

    # ------------------------------------------------------------ read
    def retrieve(self, *, project_id: str, task_class: str | None = None, text: str | None = None,
                 limit: int = 8, max_age_s: float | None = None) -> list[dict]:
        """Verified lessons for ``project_id`` (own project, or global+verified).
        Goes through ``LearningStore.retrieve`` (VERIFIED only, tombstones never), then
        applies project isolation, staleness and the read-time poison filter."""
        pool = self.store.retrieve(domain=task_class, text=text, limit=10_000)
        out: list[dict] = []
        now = time.time()
        for rec in pool:
            if rec.get("record_type") != "lesson":
                continue
            l = rec.get("lesson") or {}
            if l.get("status") != "verified" or rec.get("learning_status") != "VERIFIED":
                continue
            if not (l.get("project_id") == project_id or l.get("scope") == "global"):
                continue
            if max_age_s is not None and _too_old(rec, l, now, max_age_s):
                continue
            body = str(l.get("correction") or "")
            if poison_reasons(body) or any(poison_reasons(x) for x in (rec.get("lessons") or []) if x != body):
                self.filtered_at_read += 1
                continue
            out.append(compact_lesson(rec))
        out.sort(key=lambda c: (-int(c.get("occurrences") or 1), str(c.get("lesson_id"))))
        return out[:max(1, limit)]

    def all_lessons(self, *, include_candidates: bool = True, include_withdrawn: bool = False) -> list[dict]:
        rows = self.store.retrieve(include_failed=True, limit=10_000)
        out = []
        for rec in rows:
            if rec.get("record_type") != "lesson":
                continue
            st = (rec.get("lesson") or {}).get("status")
            if st == "candidate" and not include_candidates:
                continue
            if st == "withdrawn" and not include_withdrawn:
                continue
            out.append(compact_lesson(rec))
        return out

    def get(self, lesson_id: str) -> dict | None:
        rec = self._current_by_task_id(lesson_id)
        return compact_lesson(rec) if rec else None

    # ------------------------------------------------------------ internals
    def _current_by_task_id(self, task_id: str) -> dict | None:
        cid = _trace.case_id({"task_id": task_id, "start_sha": "", "end_sha": ""})
        return self.store.current(cid)

    def _require(self, lesson_id: str) -> dict:
        rec = self._current_by_task_id(lesson_id)
        if rec is None or rec.get("record_type") != "lesson":
            raise LessonError(f"unknown lesson {lesson_id}")
        return rec


def _too_old(rec: dict, l: dict, now: float, max_age_s: float) -> bool:
    ver = l.get("verification") or {}
    stamp = ver.get("at") or rec.get("created_at") or ""
    try:
        t = time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except (ValueError, TypeError):
        return False
    return (now - t) > max_age_s


def format_for_prompt(lessons: Iterable[dict]) -> str:
    """Advice-only text block for a student prompt (no ids that could be echoed as
    instructions, no permissions, no raw provenance)."""
    lines = []
    for i, l in enumerate(lessons, 1):
        lines.append(f"{i}. When you see: {l.get('failure_observation', '')[:200]}\n"
                     f"   Do: {l.get('correction', '')}")
    return "\n".join(lines)
