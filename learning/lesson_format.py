"""The validated field set of a lesson, its status set, and the migration of old records.

Why this module exists: ``schemas/apprentice_skill.schema.json`` declares the ``lesson``
property as a bare ``{"type": "object"}``. Every field inside it was therefore
unvalidated — a lesson could be stored with an empty recipe, no way to check it, no
applicability, no provenance of the code it came from, and nothing would object. This
module is the missing validation. It does NOT introduce a store: a lesson is still one
record of ``learning.trace.LearningStore``, written through ``learning.lessons.LessonBook``.

The field set (owner spec §8.4):
  id/scope            ``lesson_id``, ``scope``, ``project_id``
  symptoms            ``symptoms`` (what is observed), ``error_text`` (verbatim error)
  verified cause      ``root_cause``
  failed approaches   ``failed_approaches`` — first-class negative knowledge
  compact recipe      ``recipe`` (ordered steps) + ``correction`` (one-line advice)
  applicability       ``applies_when`` {task_class, environment, app, app_version, runtime}
  check               ``check`` — how to tell the fix actually worked
  counterexample      ``counterexample`` — when this lesson must NOT be applied
  references          ``refs`` {code, test, commit, evidence}
  assistance level    ``assistance_level`` — how much help produced this lesson
  model/runtime       ``model``, ``runtime``
  freshness           ``valid_from``, ``expires_at``, ``supersedes``, ``superseded_by``

Statuses. A new lesson is always ``candidate``; only evidence moves it to ``verified``
(``LessonBook.verify``, which the store's own invariants police: independent verifier,
fresh bound evidence, no self-certification). Beyond those two the owner spec requires
``quarantined``, ``superseded``, ``expired`` and ``degraded``. All of them mean the same
thing for planning — NOT retrievable as preferred behaviour — but they mean different
things to a human reading the history, so they are kept apart rather than collapsed.

Memory never grants anything. No field here can carry a permission, an approval, a budget
or a cloud/LOCAL_ONLY decision, and every free-text field is run through the same poison
filter as the lesson body, at write time and again at read time.
"""
from __future__ import annotations

import calendar
import re
import time
from typing import Any

FORMAT_VERSION = 2

SCOPES = ("project", "global")

# candidate -> verified is the only promotion; the rest are demotions/retirements.
LESSON_STATUSES = ("candidate", "verified", "quarantined", "superseded",
                   "expired", "degraded", "withdrawn")
#: Statuses that may guide planning. Deliberately a one-element tuple.
RETRIEVABLE_STATUSES = ("verified",)

#: Lesson status -> the corpus status of ``learning.trace``. Only VERIFIED is served by
#: ``LearningStore.retrieve``; everything else stays visible in the history corpus but
#: never reaches a plan.
STATUS_TO_LEARNING = {
    "candidate": "UNVERIFIED",
    "verified": "VERIFIED",
    "quarantined": "REJECTED",
    "withdrawn": "REJECTED",
    "superseded": "PARTIAL",
    "expired": "PARTIAL",
    "degraded": "PARTIAL",
}

#: How much help produced the correction. ``reference_solution`` and ``teacher_patch``
#: can never be counted as the student solving it — the coaching runner relies on this.
ASSISTANCE_LEVELS = ("none", "hint", "teacher_patch", "reference_solution", "unknown")

REF_KINDS = ("code", "test", "commit", "evidence")

MAX_TEXT = 2000
MAX_LIST = 20


class LessonFormatError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts if ts is not None else time.time()))


def parse_iso(stamp: Any) -> float | None:
    """UTC stamp -> epoch seconds. ``calendar.timegm`` is the inverse of ``gmtime``;
    ``time.mktime`` is not (it re-applies the host's DST offset) — see the D1 defect."""
    if not isinstance(stamp, str) or not stamp.strip():
        return None
    try:
        return float(calendar.timegm(time.strptime(stamp.strip(), "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, TypeError):
        return None


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return str(value or "").strip()[:limit]


def _text_list(value: Any, limit: int = MAX_LIST) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = _text(item)
        if text and text not in out:
            out.append(text)
    return out[:limit]


def _refs(value: Any) -> dict[str, list[str]]:
    src = value if isinstance(value, dict) else {}
    return {kind: _text_list(src.get(kind)) for kind in REF_KINDS}


def normalize(body: Any, *, now: float | None = None) -> dict:
    """An old or partial ``lesson`` object -> the full v2 field set.

    Total and deterministic: a v1 record (only ``correction``/``failure_observation``)
    comes out as a valid v2 body with the old text mapped onto the new fields, so
    migration never invents knowledge that was not recorded. Running it twice is a
    no-op — that is what makes ``migrate_record`` safe to re-run.
    """
    src: dict = dict(body) if isinstance(body, dict) else {}
    status = str(src.get("status") or "candidate")
    if status not in LESSON_STATUSES:
        status = "candidate"
    scope = str(src.get("scope") or "project")
    if scope not in SCOPES:
        scope = "project"
    level = str(src.get("assistance_level") or "")
    if level not in ASSISTANCE_LEVELS:
        # A teacher patch IS the assistance level when nothing else was recorded.
        level = "teacher_patch" if src.get("kind") == "teacher_patch" else "unknown"

    failure = _text(src.get("failure_observation"))
    correction = _text(src.get("correction"))
    applies = src.get("applies_when") if isinstance(src.get("applies_when"), dict) else {}

    out = dict(src)
    out.update({
        "format_version": FORMAT_VERSION,
        "status": status,
        "scope": scope,
        "project_id": _text(src.get("project_id"), 200),
        "task_class": _text(src.get("task_class"), 200) or "general",
        "failure_observation": failure,
        "correction": correction,
        # v1 recorded only the observation and the advice; they ARE the symptom and the
        # first recipe step. Nothing else is fabricated.
        "symptoms": _text_list(src.get("symptoms")) or ([failure] if failure else []),
        "error_text": _text(src.get("error_text")),
        "root_cause": _text(src.get("root_cause")),
        "failed_approaches": _text_list(src.get("failed_approaches")),
        "recipe": _text_list(src.get("recipe")) or ([correction] if correction else []),
        "applies_when": {
            "task_class": _text(applies.get("task_class"), 200) or _text(src.get("task_class"), 200) or "general",
            "environment": _text(applies.get("environment"), 200),
            "app": _text(applies.get("app"), 200),
            "app_version": _text(applies.get("app_version"), 200),
            "runtime": _text(applies.get("runtime"), 200),
        },
        "check": _text(src.get("check")),
        "counterexample": _text(src.get("counterexample")),
        "refs": _refs(src.get("refs")),
        "assistance_level": level,
        "model": _text(src.get("model"), 200),
        "runtime": _text(src.get("runtime"), 200),
        "valid_from": _text(src.get("valid_from"), 40) or _stamp_of(src) or _iso(now),
        "expires_at": _text(src.get("expires_at"), 40),
        "supersedes": _text_list(src.get("supersedes")),
        "superseded_by": _text(src.get("superseded_by"), 200),
        "retired_reason": _text(src.get("retired_reason"), 500),
    })
    return out


def _stamp_of(src: dict) -> str:
    ver = src.get("verification") if isinstance(src.get("verification"), dict) else {}
    return _text(ver.get("at"), 40)


#: Free-text fields a model will read. Each one is advice, so each one is poison-checked.
ADVICE_FIELDS = ("correction", "root_cause", "check", "counterexample")
ADVICE_LIST_FIELDS = ("symptoms", "failed_approaches", "recipe")


def validate(body: dict, *, require_full: bool = False) -> list[str]:
    """Errors in a normalized lesson body. ``require_full`` additionally demands the
    fields a VERIFIED lesson must have: a cause, a recipe and a way to check it."""
    from .lessons import poison_reasons          # lazy: lessons imports this module

    errs: list[str] = []
    if not isinstance(body, dict):
        return ["lesson body must be an object"]
    if body.get("status") not in LESSON_STATUSES:
        errs.append(f"status must be one of {LESSON_STATUSES}")
    if body.get("scope") not in SCOPES:
        errs.append(f"scope must be one of {SCOPES}")
    if body.get("assistance_level") not in ASSISTANCE_LEVELS:
        errs.append(f"assistance_level must be one of {ASSISTANCE_LEVELS}")
    if body.get("scope") == "project" and not str(body.get("project_id") or "").strip():
        errs.append("project_id is required for a project-scoped lesson")
    if not str(body.get("correction") or "").strip():
        errs.append("correction is required")
    refs = body.get("refs")
    if not isinstance(refs, dict) or set(refs) != set(REF_KINDS):
        errs.append(f"refs must carry exactly {REF_KINDS}")
    for name in ("valid_from", "expires_at"):
        stamp = body.get(name)
        if stamp and parse_iso(stamp) is None:
            errs.append(f"{name} must be an ISO-8601 UTC stamp (YYYY-MM-DDTHH:MM:SSZ)")
    valid_from, expires = parse_iso(body.get("valid_from")), parse_iso(body.get("expires_at"))
    if valid_from is not None and expires is not None and expires <= valid_from:
        errs.append("expires_at must be after valid_from")
    if body.get("superseded_by") and body.get("superseded_by") in (body.get("supersedes") or []):
        errs.append("a lesson cannot both supersede and be superseded by the same lesson")

    for field in ADVICE_FIELDS:
        text = str(body.get(field) or "")
        if text and poison_reasons(text) and not _only_length_reasons(poison_reasons(text)):
            errs.append(f"{field}: " + "; ".join(poison_reasons(text)))
    for field in ADVICE_LIST_FIELDS:
        for item in body.get(field) or []:
            reasons = poison_reasons(str(item))
            if reasons and not _only_length_reasons(reasons):
                errs.append(f"{field}: " + "; ".join(reasons))

    if require_full:
        for field in ("root_cause", "check"):
            if not str(body.get(field) or "").strip():
                errs.append(f"{field} is required for a verified lesson")
        if not (body.get("recipe") or []):
            errs.append("recipe is required for a verified lesson")
        if not any((body.get("refs") or {}).get(kind) for kind in ("test", "evidence")):
            errs.append("a verified lesson needs at least one test or evidence reference")
    return errs


def _only_length_reasons(reasons: list[str]) -> bool:
    """A short symptom line ("ImportError") is not poison — it is just short. The
    minimum-length rule exists for the lesson BODY, not for every field."""
    return all("too short" in r for r in reasons)


def assert_valid(body: dict, *, require_full: bool = False) -> None:
    errs = validate(body, require_full=require_full)
    if errs:
        raise LessonFormatError(errs)


# ---------------------------------------------------------------- applicability
def applicability(body: dict, *, project_id: str, now: float | None = None,
                  task_class: str | None = None, environment: str | None = None,
                  app_version: str | None = None, runtime: str | None = None,
                  max_age_s: float | None = None) -> tuple[bool, str]:
    """(applicable, reason). The reason is returned for BOTH answers so the context pack
    can show why a lesson was offered, and the operator can see why one was withheld."""
    now = time.time() if now is None else now
    status = str(body.get("status") or "candidate")
    if status not in RETRIEVABLE_STATUSES:
        return False, f"status={status}"

    expires = parse_iso(body.get("expires_at"))
    if expires is not None and now >= expires:
        return False, f"expired at {body.get('expires_at')}"
    if body.get("superseded_by"):
        return False, f"superseded by {body['superseded_by']}"

    scope = str(body.get("scope") or "project")
    if scope == "project" and str(body.get("project_id") or "") != project_id:
        return False, f"other project ({body.get('project_id')})"

    if max_age_s is not None:
        stamp = parse_iso(_stamp_of(body)) or parse_iso(body.get("valid_from"))
        if stamp is not None and (now - stamp) > max_age_s:
            return False, "older than the requested freshness window"

    applies = body.get("applies_when") if isinstance(body.get("applies_when"), dict) else {}
    for name, want in (("task_class", task_class), ("environment", environment),
                       ("app_version", app_version), ("runtime", runtime)):
        have = str(applies.get(name) or "").strip()
        # An unrecorded condition does not narrow anything; a recorded one must match.
        if have and want is not None and str(want).strip() and have != str(want).strip():
            return False, f"{name}={have} != {want}"

    where = "global" if scope == "global" else f"project {body.get('project_id')}"
    return True, f"verified, {where}"


def conflicts(bodies: list[dict]) -> list[dict]:
    """Pairs of applicable lessons that contradict each other, surfaced rather than
    silently resolved (contract: "Never silently resolve conflicting memories").

    Two lessons conflict when they answer the same question — same task class and
    overlapping symptoms — with different recipes and neither supersedes the other.
    """
    out: list[dict] = []
    for i, a in enumerate(bodies):
        for b in bodies[i + 1:]:
            if a.get("lesson_id") and a.get("lesson_id") == b.get("lesson_id"):
                continue
            if _applies_key(a) != _applies_key(b):
                continue
            if not (_sym_set(a) & _sym_set(b)):
                continue
            if _norm(a.get("correction")) == _norm(b.get("correction")):
                continue
            if b.get("lesson_id") in (a.get("supersedes") or []) or \
               a.get("lesson_id") in (b.get("supersedes") or []):
                continue
            newer = max((a, b), key=lambda x: parse_iso(x.get("valid_from")) or 0.0)
            out.append({"a": a.get("lesson_id"), "b": b.get("lesson_id"),
                        "task_class": _applies_key(a),
                        "shared_symptoms": sorted(_sym_set(a) & _sym_set(b))[:3],
                        "newer": newer.get("lesson_id"),
                        "note": "different recipes for the same symptom; neither supersedes "
                                "the other — resolve explicitly, do not assume the newer wins"})
    return out


def _applies_key(body: dict) -> str:
    applies = body.get("applies_when") if isinstance(body.get("applies_when"), dict) else {}
    return str(applies.get("task_class") or body.get("task_class") or "general")


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _sym_set(body: dict) -> set[str]:
    return {_norm(s) for s in (body.get("symptoms") or []) if _norm(s)}


# ---------------------------------------------------------------- migration
def migrate_record(rec: dict) -> dict | None:
    """A store record -> the same record with a v2 lesson body, or None if already v2.

    The record is returned as a NEW dict; the caller writes it back through the store,
    which turns it into a new version and tombstones the old one. History is preserved:
    migration never edits a file in place.
    """
    if not isinstance(rec, dict) or rec.get("record_type") != "lesson":
        return None
    body = rec.get("lesson")
    if isinstance(body, dict) and int(body.get("format_version") or 0) >= FORMAT_VERSION:
        return None
    migrated = dict(rec)
    normalized = normalize(body)
    # The corpus status is authoritative for what retrieval does; keep the two agreed.
    learning_status = str(rec.get("learning_status") or "UNVERIFIED")
    if learning_status == "VERIFIED" and normalized["status"] != "verified":
        normalized["status"] = "verified"
    if learning_status != "VERIFIED" and normalized["status"] == "verified":
        normalized["status"] = "candidate"
    normalized.setdefault("lesson_id", rec.get("task_id"))
    normalized["lesson_id"] = normalized.get("lesson_id") or rec.get("task_id")
    normalized["model"] = normalized["model"] or str(rec.get("model") or "")
    normalized["applies_when"]["environment"] = (normalized["applies_when"]["environment"]
                                                 or str(rec.get("environment") or ""))
    migrated["lesson"] = normalized
    for key in ("case_id", "version", "supersedes_version", "created_at"):
        migrated.pop(key, None)
    return migrated
