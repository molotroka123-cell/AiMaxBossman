"""Experience of a cycle -> retrieval memory (LessonBook), never model training.

At every stop of a cycle (USER_APPROVAL, COMPLETE, ROLLED_BACK, BLOCKED) the loop writes ONE lesson CANDIDATE
(`learning.lessons.LessonBook`, status UNVERIFIED) into ``<root>/lessons``:

* **retrieval context, not training**: the lesson is text that may be shown to a later task. The model weights are
  never changed (``weights: WEIGHTS_UNCHANGED``, ``learning_kind: retrieval_context``); saving history is not
  fine-tuning and is never called that;
* **provenance**: who produced it (the writer CLI of the cycle), and the evidence it stands on (trace hash, candidate
  sha, journal head), so every lesson can be traced back to a hash-chained journal entry;
* **dedup**: the advice text is deterministic for the same (tier, outcome, scope), so the same experience made twice is
  ONE lesson whose ``occurrences`` grows (`LessonBook.save`);
* **poisoning**: the advice is built only from structured facts (tier, state, reason CATEGORY, scope globs). Free text
  from a model (writer summary, reviewer notes, blocked reason details) never enters a lesson; the goal's problem text is
  used only if it passes the lesson poison filter; `LessonBook` runs the filter again on write and on read;
* **verification is the owner's**: a candidate is never retrieved. ``verify`` (owner command, interactive terminal, no
  agent session) promotes it to VERIFIED with the owner as the independent human verifier; ``withdraw`` retires it.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

from . import constitution as const
from .types import Goal

PROJECT_ID = "bossman-autonomy"
WEIGHTS = "WEIGHTS_UNCHANGED"
LEARNING_KIND = "retrieval_context"
CONFIRM_CHARS = 8
_CATEGORY_RX = re.compile(r"^[A-Za-z][A-Za-z ./_-]{2,60}")

#: state -> advice text. Deterministic on purpose (dedup) and free of control-plane vocabulary (poison filter).
_ADVICE = {
    "COMPLETE": ("A bounded change of tier {tier} limited to {scope} passed its acceptance tests, both reviews and "
                 "the staged evaluation. Keep the diff small and run the same acceptance tests before proposing it."),
    "USER_APPROVAL": ("A bounded change of tier {tier} limited to {scope} reached the owner gate with passing tests, "
                      "both reviews and a staged evaluation. Keep the diff small; the owner decides about the release."),
    "ROLLED_BACK": ("A change of tier {tier} limited to {scope} regressed a measured metric after release and was "
                    "rolled back. Measure the protected metrics on the staged candidate before proposing it again."),
    "REJECTED": ("A change of tier {tier} limited to {scope} was rejected by the owner at the release gate. Read the "
                 "owner's note before proposing a similar change and keep the next attempt smaller."),
    "BLOCKED": ("A cycle of tier {tier} limited to {scope} stopped on: {category}. Report the stop to the owner and "
                "wait; do not widen the scope and do not repeat the same attempt unchanged."),
}


def _clean_category(reason: str) -> str:
    """The reason CATEGORY only (text before the first colon), never the model-written detail after it."""
    m = _CATEGORY_RX.match((reason or "").strip())
    return re.sub(r"\s+", " ", m.group(0)).strip(" .:-") if m else "unspecified"


def _scope_text(goal: Goal) -> str:
    paths = [c.split(":", 1)[1].strip() for c in goal.constraints if c.lower().startswith("path:")]
    return ", ".join(paths[:4]) or "the goal's scope"


def _poisoned(text: str) -> bool:
    from learning.lessons import poison_reasons
    return any("too short" not in r for r in poison_reasons(text))


class ExperienceWriter:
    def __init__(self, root: str | Path, *, journal: Any = None, project_id: str = PROJECT_ID,
                 clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.journal = journal
        self.project_id = project_id
        self._clock = clock
        self._book: Any = None

    @property
    def book(self):
        if self._book is None:
            from learning.lessons import LessonBook
            self._book = LessonBook(self.root / "lessons")
        return self._book

    def _log(self, kind: str, payload: dict) -> None:
        if self.journal is not None:
            self.journal.append(kind, payload)

    # ------------------------------------------------------------ write (cycle stops)
    def record(self, *, goal: Goal, state: str, reason: str = "", trace_hash: str = "", sha: str = "",
               writer: str = "", journal_head: str = "") -> dict | None:
        """Write (or dedup-bump) the lesson CANDIDATE for one cycle stop. Returns
        {"lesson_id", "status", "dedup_key", "weights", "learning_kind"} or None when nothing could be written
        (journaled, never raises: experience must not break the loop)."""
        advice = _ADVICE.get(state)
        if advice is None:
            return None
        try:
            from learning.lessons import CoachingEpisode, LessonPoisoned, Provenance
        except ImportError:                                                 # pragma: no cover
            self._log("lesson.skipped", {"goal_id": goal.goal_id, "reason": "learning package unavailable"})
            return None
        category = _clean_category(reason) if state == "BLOCKED" else ""
        correction = advice.format(tier=goal.risk_tier, scope=_scope_text(goal), category=category or "n/a")
        problem = goal.problem.strip()[:300]
        observation = (f"goal {goal.goal_id} [{goal.risk_tier}] ended {state}"
                       + (f" ({category})" if category else "")
                       + ("" if not problem or _poisoned(problem) else f": {problem}"))
        teacher = writer in ("claude", "codex")
        refs = [f"trace:{trace_hash}" if trace_hash else "", f"sha:{sha}" if sha else "",
                f"journal:{journal_head}" if journal_head else ""]
        prov = Provenance(who=f"autonomy-cycle:{writer or 'unknown'}",
                          what=f"{LEARNING_KIND} ({WEIGHTS}): {state} of goal {goal.goal_id}",
                          evidence_refs=[r for r in refs if r], run_id=f"{goal.goal_id}@{(journal_head or 'none')[:12]}",
                          model=writer or "")
        ep = CoachingEpisode(
            attempt_id=f"{goal.goal_id}:{(sha or state.lower())[:12]}:{state}", task_id=goal.goal_id,
            project_id=self.project_id, failure_observation=observation, correction=correction,
            source="teacher" if teacher else "student", provenance=prov, task_class=goal.risk_tier,
            kind="teacher_patch" if teacher else "student_fix", status="candidate", scope="project",
            environment="autonomy-cycle", agent=f"autonomy-cycle:{writer or 'unknown'}", model=writer or "",
            title=f"autonomy experience [{LEARNING_KIND}, {WEIGHTS}]: {goal.goal_id} {state}",
            refs={"commit": [sha] if sha else [], "evidence": [r for r in refs if r]}, runtime="autonomy-cycle")
        try:
            rec = self.book.save(ep)
        except LessonPoisoned as exc:
            self._log("lesson.refused", {"goal_id": goal.goal_id, "state": state, "reason": "; ".join(exc.reasons)[:300]})
            return None
        except Exception as exc:  # noqa: BLE001 - a storage problem must not break the cycle
            self._log("lesson.skipped", {"goal_id": goal.goal_id, "state": state,
                                         "reason": f"{type(exc).__name__}: {str(exc)[:200]}"})
            return None
        lesson = rec.get("lesson") or {}
        out = {"lesson_id": rec.get("task_id") or ep.lesson_id, "status": "UNVERIFIED",
               "dedup_key": lesson.get("dedup_key") or ep.dedup_key, "occurrences": lesson.get("occurrences", 1),
               "weights": WEIGHTS, "learning_kind": LEARNING_KIND}
        self._log("lesson.candidate", {"goal_id": goal.goal_id, "state": state, **out, "evidence_refs": prov.evidence_refs})
        return out

    # ------------------------------------------------------------ owner commands
    def listing(self) -> list[dict]:
        rows = []
        for l in self.book.all_lessons(include_candidates=True, include_withdrawn=True):
            rows.append({"lesson_id": l.get("lesson_id"), "status": l.get("status"),
                         "learning_status": l.get("learning_status"), "occurrences": l.get("occurrences"),
                         "task_class": l.get("task_class"), "kind": l.get("kind"),
                         "correction": str(l.get("correction") or "")[:300], "weights": WEIGHTS,
                         "learning_kind": LEARNING_KIND})
        return sorted(rows, key=lambda r: str(r["lesson_id"]))

    def verify(self, lesson_id: str, *, by: str = "owner") -> dict:
        """candidate -> VERIFIED, the owner as the independent human verifier. Call only after `owner_gate`."""
        cur = self.book.get(lesson_id)
        if cur is None:
            raise KeyError(lesson_id)
        commit = next(iter((cur.get("refs") or {}).get("commit") or []), "")
        evidence = {"source": "owner-cli", "expected": "owner reviewed the cycle trace and journal",
                    "actual": "owner confirmed the lesson", "head_sha": commit or self._journal_head() or "owner-review",
                    "environment": "autonomy-root"}
        rec = self.book.verify(lesson_id, verifier={"principal_id": by, "independence_class": "human"},
                               evidence=evidence, statement="verified by the owner command `bossman autonomy lesson verify`")
        self._log("lesson.verified", {"lesson_id": lesson_id, "by": by, "weights": WEIGHTS,
                                      "learning_kind": LEARNING_KIND})
        return rec

    def withdraw(self, lesson_id: str, *, by: str = "owner", reason: str = "withdrawn by the owner") -> dict:
        rec = self.book.withdraw(lesson_id, by=by, reason=reason[:300])
        self._log("lesson.withdrawn", {"lesson_id": lesson_id, "by": by, "reason": reason[:300]})
        return rec

    def verified(self, *, task_class: str | None = None, limit: int = 8) -> list[dict]:
        """VERIFIED lessons only (what the planner may read)."""
        try:
            return self.book.retrieve(project_id=self.project_id, task_class=task_class, limit=limit)
        except Exception:  # noqa: BLE001 - no store yet
            return []

    def _journal_head(self) -> str:
        try:
            return self.journal.head() if self.journal is not None else ""
        except Exception:  # noqa: BLE001
            return ""


def owner_gate(expect: str, *, stdin: TextIO | None = None, stdout: TextIO | None = None,
               env: Mapping[str, str] | None = None, read_line: Callable[[], str] | None = None) -> tuple[bool, str]:
    """The same human-only gate as the constitution pin: no agent/automation session, an interactive terminal, and
    the person types the first characters of what they confirm. (ok, reason)."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    markers = const.agent_markers(env)
    if markers:
        return False, f"refused: agent/automation session ({', '.join(markers)}); only the owner verifies lessons"
    try:
        interactive = bool(stdin.isatty()) and bool(stdout.isatty())
    except (AttributeError, ValueError, OSError):
        interactive = False
    if not interactive:
        return False, "refused: not an interactive terminal (stdin and stdout must be a TTY)"
    stdout.write(f"Type the first {CONFIRM_CHARS} characters of '{expect}' to confirm (anything else cancels): ")
    stdout.flush()
    answer = (read_line or stdin.readline)().strip()
    if answer != expect[:CONFIRM_CHARS]:
        return False, "cancelled: confirmation did not match"
    return True, "confirmed"


__all__ = ["ExperienceWriter", "LEARNING_KIND", "PROJECT_ID", "WEIGHTS", "owner_gate"]
