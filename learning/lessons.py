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

import calendar
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import lesson_format as _fmt
from . import trace as _trace

SOURCES = ("teacher", "student")
KINDS = ("student_fix", "teacher_patch")
#: Statuses a CoachingEpisode may be born with. The retirement statuses
#: (quarantined/superseded/expired/degraded) are reached through LessonBook methods,
#: never by writing them on a fresh episode — see learning.lesson_format.
STATUSES = ("candidate", "verified", "withdrawn")
SCOPES = ("project", "global")
LESSON_TASK_PREFIX = "coach-lesson:"
MIN_BODY_CHARS = 8
MAX_BODY_CHARS = 2000

_STATUS_TO_LEARNING = _fmt.STATUS_TO_LEARNING


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


# Red team 2026-09-21 (RT-L1/L2): the ASCII denylist was defeated by zero-width
# joiners, RTL overrides, Cyrillic homoglyphs, full-width Latin and base64
# wrapping. Every check below runs on a NORMALISED view of the body as well:
# NFKC (full-width → ASCII), Unicode format characters (Cf) stripped, common
# confusable Cyrillic/Greek letters mapped to Latin, HTML comments unwrapped,
# and base64-looking tokens decoded and scanned too.
_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ј": "j",
    "ѕ": "s", "һ": "h", "ԁ": "d", "ɡ": "g", "ν": "v", "ο": "o", "α": "a", "ε": "e",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Х": "X", "У": "Y", "І": "I", "Ј": "J",
    "Ѕ": "S", "Н": "H", "К": "K", "М": "M", "Т": "T", "В": "B",
})
_B64_TOKEN = re.compile(r"[A-Za-z0-9+/=_-]{24,}")


def _normalised_views(text: str) -> list[str]:
    import base64
    import unicodedata
    folded = unicodedata.normalize("NFKC", text)
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")
    folded = folded.translate(_CONFUSABLES)
    folded = re.sub(r"<!--(.*?)-->", r" \1 ", folded, flags=re.S)
    views = [folded]
    for token in _B64_TOKEN.findall(folded):
        try:
            raw = base64.b64decode(token + "=" * (-len(token) % 4), validate=False)
            decoded = raw.decode("utf-8")
        except Exception:  # noqa: BLE001 — не base64 или не текст
            try:
                decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("utf-8")
            except Exception:  # noqa: BLE001
                continue
        if decoded.isprintable() or "\n" in decoded:
            views.append(decoded)
    return views


def poison_reasons(text: str) -> list[str]:
    """Why ``text`` must not become a lesson (empty list = acceptable advice text)."""
    reasons: list[str] = []
    if not isinstance(text, str):
        return ["lesson body must be a string"]
    body = text.strip()
    for view in _normalised_views(body):
        if view.strip() == body:
            continue
        for reason in poison_reasons(view):
            tagged = f"normalised:{reason}"
            if tagged not in reasons and reason not in reasons:
                reasons.append(tagged)
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
    # --- v2 field set (learning.lesson_format). All optional: an episode recorded
    # without them still produces a valid lesson, it is simply a poorer one.
    symptoms: list[str] = field(default_factory=list)
    error_text: str = ""
    root_cause: str = ""
    failed_approaches: list[str] = field(default_factory=list)
    recipe: list[str] = field(default_factory=list)
    check: str = ""
    counterexample: str = ""
    refs: dict = field(default_factory=dict)          # {code|test|commit|evidence: [...]}
    assistance_level: str = ""
    runtime: str = ""
    app: str = ""
    app_version: str = ""
    expires_at: str = ""
    supersedes: list[str] = field(default_factory=list)

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
    for _text in (ep.root_cause, ep.check, ep.counterexample, *ep.symptoms,
                  *ep.failed_approaches, *ep.recipe):
        # Every free-text field reaches a model exactly like the body does, so every
        # one of them goes through the same filter. A poisoned "root cause" would
        # otherwise be a way in.
        if _text and not all("too short" in r for r in poison_reasons(str(_text))):
            assert_not_poisoned(str(_text))
    prov = ep.provenance.as_dict()
    prov["when"] = prov["when"] or _now_iso()
    rec = {
        "task_id": ep.lesson_id, "record_type": "lesson",
        "learning_status": _STATUS_TO_LEARNING[ep.status],
        "title": ep.title or f"lesson[{ep.task_class}]: {ep.correction[:72]}",
        "summary": ep.correction[:500],
        "task_type": ep.task_class, "environment": ep.environment,
        "agent": ep.agent, "model": ep.model or prov.get("model") or "unknown-model",
        "principal_id": prov["who"], "run_id": prov.get("run_id") or "",
        "start_sha": "", "end_sha": "",               # dedup identity must not depend on HEAD
        "lessons": [ep.correction],
        "lesson": _fmt.normalize({
            "lesson_id": ep.lesson_id,
            "dedup_key": ep.dedup_key, "project_id": ep.project_id, "scope": ep.scope,
            "task_class": ep.task_class, "status": ep.status, "source": ep.source, "kind": ep.kind,
            "student_success": False if ep.kind == "teacher_patch" else None,
            "failure_observation": ep.failure_observation[:2000], "correction": ep.correction,
            "attempt_ids": [ep.attempt_id], "source_task_ids": [ep.task_id],
            "occurrences": 1, "provenance": [prov], "verification": None, "withdrawal": None,
            "symptoms": ep.symptoms, "error_text": ep.error_text, "root_cause": ep.root_cause,
            "failed_approaches": ep.failed_approaches, "recipe": ep.recipe,
            "check": ep.check, "counterexample": ep.counterexample, "refs": ep.refs,
            "assistance_level": ep.assistance_level, "runtime": ep.runtime,
            "model": ep.model or prov.get("model") or "",
            "applies_when": {"task_class": ep.task_class, "environment": ep.environment,
                             "app": ep.app, "app_version": ep.app_version, "runtime": ep.runtime},
            "expires_at": ep.expires_at, "supersedes": ep.supersedes,
        }),
        "source_episode_ids": [ep.task_id],
        "outcome": "TEACHER_PATCH" if ep.kind == "teacher_patch" else "STUDENT_CORRECTION",
        "tags": {"domain": ep.task_class, "risk": "low"},
    }
    _fmt.assert_valid(rec["lesson"])
    return rec


def compact_lesson(rec: dict) -> dict:
    """Retrieval shape: what a student prompt gets (never the raw store record)."""
    l = _fmt.normalize(rec.get("lesson") or {})
    return {"lesson_id": rec.get("task_id"), "version": rec.get("version"),
            "status": l.get("status"), "learning_status": rec.get("learning_status"),
            "source": l.get("source"), "kind": l.get("kind"), "student_success": l.get("student_success"),
            "project_id": l.get("project_id"), "scope": l.get("scope"), "task_class": l.get("task_class"),
            "failure_observation": l.get("failure_observation", ""), "correction": l.get("correction", ""),
            "occurrences": l.get("occurrences", 1), "provenance": list(l.get("provenance") or []),
            "verification": l.get("verification"), "verified_by": list(rec.get("verified_by") or []),
            # v2 field set — advice and provenance only; never permissions or raw evidence
            "format_version": l.get("format_version"),
            "symptoms": list(l.get("symptoms") or []), "error_text": l.get("error_text", ""),
            "root_cause": l.get("root_cause", ""),
            "failed_approaches": list(l.get("failed_approaches") or []),
            "recipe": list(l.get("recipe") or []), "check": l.get("check", ""),
            "counterexample": l.get("counterexample", ""),
            "refs": dict(l.get("refs") or {}), "applies_when": dict(l.get("applies_when") or {}),
            "assistance_level": l.get("assistance_level"), "model": l.get("model", ""),
            "runtime": l.get("runtime", ""), "valid_from": l.get("valid_from", ""),
            "expires_at": l.get("expires_at", ""), "supersedes": list(l.get("supersedes") or []),
            "superseded_by": l.get("superseded_by", "")}


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
                 limit: int = 8, max_age_s: float | None = None,
                 environment: str | None = None, app_version: str | None = None,
                 runtime: str | None = None, now: float | None = None) -> list[dict]:
        """Verified, currently applicable lessons for ``project_id``.

        Goes through ``LearningStore.retrieve`` (VERIFIED only, tombstones never), then
        applies the v2 applicability rules (status, expiry, supersession, scope/project,
        recorded conditions, freshness) and the read-time poison filter."""
        pool = self.store.retrieve(domain=task_class, text=text, limit=10_000)
        out: list[dict] = []
        now = time.time() if now is None else now
        for rec in pool:
            if rec.get("record_type") != "lesson":
                continue
            if rec.get("learning_status") != "VERIFIED":
                continue
            l = _fmt.normalize(rec.get("lesson") or {})
            ok, why = _fmt.applicability(l, project_id=project_id, now=now, task_class=task_class,
                                         environment=environment, app_version=app_version,
                                         runtime=runtime, max_age_s=max_age_s)
            if not ok:
                continue
            body = str(l.get("correction") or "")
            texts = [body, *(l.get("symptoms") or []), *(l.get("recipe") or []),
                     str(l.get("root_cause") or ""), str(l.get("check") or ""),
                     str(l.get("counterexample") or "")]
            texts += [x for x in (rec.get("lessons") or []) if x != body]
            if any(_poisoned(x) for x in texts if x):
                self.filtered_at_read += 1
                continue
            compact = compact_lesson(rec)
            compact["applicability"] = why
            out.append(compact)
        out.sort(key=lambda c: (-int(c.get("occurrences") or 1), str(c.get("lesson_id"))))
        return out[:max(1, limit)]

    def conflicts(self, lessons: list[dict]) -> list[dict]:
        """Contradictions among already-retrieved lessons, surfaced not resolved."""
        return _fmt.conflicts([dict(l, lesson_id=l.get("lesson_id")) for l in lessons])

    # ------------------------------------------------------------ retirement
    def retire(self, lesson_id: str, *, status: str, by: str, reason: str,
               superseded_by: str = "") -> dict:
        """Move a lesson to a non-retrievable status: quarantined | superseded |
        expired | degraded | withdrawn. Nothing is deleted — the record becomes a new
        version, the previous one stays in history, and retrieval stops serving it."""
        if status not in ("quarantined", "superseded", "expired", "degraded", "withdrawn"):
            raise LessonError(f"{status} is not a retirement status")
        rec = self._require(lesson_id)
        merged = json.loads(json.dumps(rec))
        merged["learning_status"] = _fmt.STATUS_TO_LEARNING[status]
        merged["verified"] = False
        if status == "degraded":
            merged["degraded_reason"] = reason[:500]
        body = _fmt.normalize(merged.get("lesson") or {})
        body["status"] = status
        body["retired_reason"] = reason[:500]
        if superseded_by:
            body["superseded_by"] = superseded_by
        if status == "expired" and not body.get("expires_at"):
            body["expires_at"] = _now_iso()
        body["withdrawal"] = {"by": by, "reason": reason, "at": _now_iso(),
                              "previous_status": (rec.get("lesson") or {}).get("status")}
        merged["lesson"] = body
        for k in ("case_id", "version", "supersedes_version", "created_at"):
            merged.pop(k, None)
        return self.store.add(merged, write_markdown=False)

    def quarantine(self, lesson_id: str, *, by: str, reason: str) -> dict:
        return self.retire(lesson_id, status="quarantined", by=by, reason=reason)

    def expire(self, lesson_id: str, *, by: str, reason: str = "expired") -> dict:
        return self.retire(lesson_id, status="expired", by=by, reason=reason)

    def degrade(self, lesson_id: str, *, by: str, reason: str) -> dict:
        return self.retire(lesson_id, status="degraded", by=by, reason=reason)

    def supersede(self, old_id: str, *, by_lesson_id: str, by: str, reason: str = "") -> dict:
        """The old lesson stops guiding plans and points at its replacement. The old
        text stays readable: supersession is not deletion."""
        return self.retire(old_id, status="superseded", by=by,
                           reason=reason or f"superseded by {by_lesson_id}",
                           superseded_by=by_lesson_id)

    # ------------------------------------------------------------ migration
    def migrate(self) -> dict:
        """Bring every stored lesson up to the v2 field set.

        Each migrated record is written back through the store, so it becomes a new
        VERSION with the previous one tombstoned in history — no file is edited in
        place and nothing is lost. Idempotent: a second run migrates 0 records."""
        migrated, skipped = [], 0
        for rec in self.store.retrieve(include_failed=True, limit=10_000):
            if rec.get("record_type") != "lesson":
                continue
            updated = _fmt.migrate_record(rec)
            if updated is None:
                skipped += 1
                continue
            self.store.add(updated, write_markdown=False)
            migrated.append(rec.get("task_id"))
        return {"migrated": len(migrated), "already_current": skipped, "lesson_ids": migrated}

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


def _poisoned(text: str) -> bool:
    """Read-time filter. A field that is merely short ("ImportError") is not poison;
    only the lesson BODY has a minimum length, and that is enforced at write time."""
    reasons = poison_reasons(str(text))
    return bool(reasons) and not all("too short" in r for r in reasons)


def _too_old(rec: dict, l: dict, now: float, max_age_s: float) -> bool:
    """Возраст урока по его UTC-отметке.

    `time.mktime(struct) - time.timezone` — НЕ обратная функция к gmtime: mktime
    трактует struct как локальное время и сама применяет летний сдвиг (tm_isdst=-1),
    а вычитается при этом зимний `time.timezone`. На хосте с переходом на летнее
    время (замерено здесь: tz=28800, altzone=25200) отметка уезжает ровно на час
    в прошлое, и ТОЛЬКО ЧТО проверенный урок оказывается «старым»: retrieve с
    max_age_s молча возвращает пустой список. Обратная к gmtime — calendar.timegm.
    """
    ver = l.get("verification") or {}
    stamp = ver.get("at") or rec.get("created_at") or ""
    try:
        t = calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
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
