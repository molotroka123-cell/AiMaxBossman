"""The minimal memory lifecycle over the stores that already exist.

`docs/evo/DURABLE_MEMORY_OPERATING_CONTRACT.md` asks for one orchestration layer, not
another database, and names the boundaries where memory must act by itself:

    BOOT                  validate the stores and the derived indexes, report health
    TASK_START/BEFORE_PLAN retrieve the smallest relevant verified context AUTOMATICALLY
    RESUME                 durable checkpoint + reconciliation of what really happened
    AFTER_VERIFIED_RESULT  keep only what has durable value, in ONE canonical place
    CHECKPOINT             persist the continuation before anything can interrupt it

This module is that layer and nothing more. It owns no knowledge: notes stay in the
vault, facts stay in the fact store, episodes/lessons/skills stay in LearningStore. The
only file it writes on its own is a task checkpoint, which is task state, not knowledge.

Two properties are the whole point of the exercise:

*Automatic.* ``task_start`` retrieves before a plan exists and without anyone calling a
search tool. A manual ``memory.search`` is not evidence that Bossman remembers; it is
evidence that someone remembered to ask.

*Durable.* Everything ``task_start`` returns was read from disk in this process. A fresh
process over the same directories returns the same thing, which is what makes restart
recall provable rather than a property of a warm cache.

Memory is evidence, never authority. Nothing here can approve an action, grant a
permission, raise a budget, enable cloud access or weaken a policy, and the curator
refuses to store text that tries to.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import lesson_format as _fmt
from .retrieval import MemoryContext, RetrievalConfig, UnifiedRetriever, probe_embedder

HEALTHY = "MEMORY_HEALTHY"
DEGRADED = "MEMORY_DEGRADED"
READ_ONLY = "MEMORY_READ_ONLY"
NOT_CONFIGURED = "MEMORY_NOT_CONFIGURED"

#: What a curated record may be. Anything else is not durable value, it is chatter.
KEEP_KINDS = ("DECISION", "WORKED", "FAILED", "CONVENTION", "EVIDENCE", "NEXT")

CHECKPOINT_VERSION = 1


class MemoryLifecycleError(RuntimeError):
    pass


# ---------------------------------------------------------------- BOOT
@dataclass(slots=True)
class LayerHealth:
    name: str
    present: bool
    writable: bool
    detail: str
    count: int = 0

    def as_dict(self) -> dict:
        return {"name": self.name, "present": self.present, "writable": self.writable,
                "detail": self.detail, "count": self.count}


@dataclass(slots=True)
class MemoryHealth:
    status: str
    layers: list[LayerHealth]
    problems: list[str]
    rebuilt: list[str]
    embedder: dict

    def as_dict(self) -> dict:
        return {"status": self.status, "layers": [l.as_dict() for l in self.layers],
                "problems": list(self.problems), "rebuilt": list(self.rebuilt),
                "embedder": dict(self.embedder)}


# ---------------------------------------------------------------- checkpoints
@dataclass(slots=True)
class Checkpoint:
    task_id: str
    project_id: str
    next_action: str = ""
    phase: str = ""
    state: dict = field(default_factory=dict)
    effects_done: list[dict] = field(default_factory=list)
    effects_uncertain: list[dict] = field(default_factory=list)
    created_at: str = ""
    version: int = CHECKPOINT_VERSION

    def as_dict(self) -> dict:
        return {"task_id": self.task_id, "project_id": self.project_id,
                "next_action": self.next_action, "phase": self.phase, "state": dict(self.state),
                "effects_done": list(self.effects_done),
                "effects_uncertain": list(self.effects_uncertain),
                "created_at": self.created_at, "version": self.version}

    @classmethod
    def from_dict(cls, raw: dict) -> "Checkpoint":
        return cls(task_id=str(raw.get("task_id") or ""), project_id=str(raw.get("project_id") or ""),
                   next_action=str(raw.get("next_action") or ""), phase=str(raw.get("phase") or ""),
                   state=dict(raw.get("state") or {}),
                   effects_done=list(raw.get("effects_done") or []),
                   effects_uncertain=list(raw.get("effects_uncertain") or []),
                   created_at=str(raw.get("created_at") or ""),
                   version=int(raw.get("version") or CHECKPOINT_VERSION))


@dataclass(slots=True)
class TaskContext:
    """What BEFORE_PLAN hands to the planner."""
    task_id: str
    project_id: str
    goal: str
    memory: MemoryContext
    checkpoint: Checkpoint | None = None
    health: str = ""

    @property
    def text(self) -> str:
        return self.memory.text

    def sources(self) -> list[str]:
        return self.memory.sources()


@dataclass(slots=True)
class ResumeContext:
    task_id: str
    project_id: str
    checkpoint: Checkpoint | None
    memory: MemoryContext
    confirmed_effects: list[dict]
    uncertain_effects: list[dict]
    next_action: str
    notes: list[str]

    @property
    def resumable(self) -> bool:
        return self.checkpoint is not None


# ---------------------------------------------------------------- the orchestrator
class MemoryLifecycle:
    """One layer over the existing stores. Construct it with whichever ports exist;
    a missing layer degrades the report, it never raises."""

    def __init__(self, *, state_dir: Path | str, retriever: UnifiedRetriever | None = None,
                 notes: Any = None, facts: Any = None, lessons: Any = None,
                 config: RetrievalConfig | None = None, clock=time.time) -> None:
        self.state_dir = Path(state_dir)
        self.notes = notes
        self.facts = facts
        self.lessons = lessons
        self.clock = clock
        self.retriever = retriever or UnifiedRetriever(notes=notes, facts=facts,
                                                       lessons=lessons, config=config)

    # -------------------------------------------------------- paths
    @property
    def checkpoint_dir(self) -> Path:
        return self.state_dir / "checkpoints"

    def checkpoint_path(self, task_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", str(task_id))[:120] or "task"
        return self.checkpoint_dir / f"{safe}.json"

    # -------------------------------------------------------- BOOT
    def boot(self, *, reindex: bool = True) -> MemoryHealth:
        """Validate the stores, rebuild what is derived, and report honestly.

        A derived index is rebuilt; canonical state never is. If a layer is missing the
        answer is NOT_CONFIGURED or DEGRADED — never a silent HEALTHY over nothing.
        """
        layers: list[LayerHealth] = []
        problems: list[str] = []
        rebuilt: list[str] = []

        layers.append(self._boot_notes(reindex, problems, rebuilt))
        layers.append(self._boot_facts(problems))
        layers.append(self._boot_lessons(problems))

        present = [l for l in layers if l.present]
        if not present:
            status = NOT_CONFIGURED
        elif problems:
            status = DEGRADED
        elif not any(l.writable for l in present):
            status = READ_ONLY
        elif len(present) < len(layers):
            status = DEGRADED
            problems.append("not every memory layer is wired: "
                            + ", ".join(l.name for l in layers if not l.present))
        else:
            status = HEALTHY
        self.retriever.invalidate("boot")
        return MemoryHealth(status=status, layers=layers, problems=problems, rebuilt=rebuilt,
                            embedder=probe_embedder().as_dict())

    def _boot_notes(self, reindex: bool, problems: list[str], rebuilt: list[str]) -> LayerHealth:
        if self.notes is None:
            return LayerHealth("notes", False, False, "no notes port wired")
        try:
            count = sum(1 for _ in self.notes.iter_notes())
        except Exception as exc:  # noqa: BLE001
            problems.append(f"notes unreadable: {type(exc).__name__}: {exc}")
            return LayerHealth("notes", True, False, f"unreadable: {exc}")
        writable = bool(getattr(self.notes, "writable", lambda: False)())
        detail = "ok"
        if reindex:
            rebuild = getattr(self.notes, "reindex", None)
            if callable(rebuild):
                try:
                    rebuild()
                    rebuilt.append("notes index")
                except Exception as exc:  # noqa: BLE001 — a derived index is rebuildable, not critical
                    problems.append(f"notes index rebuild failed: {type(exc).__name__}: {exc}")
                    detail = f"index rebuild failed: {exc}"
        return LayerHealth("notes", True, writable, detail, count)

    def _boot_facts(self, problems: list[str]) -> LayerHealth:
        if self.facts is None:
            return LayerHealth("facts", False, False, "no facts port wired")
        try:
            rows = self.facts.search("", limit=1)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"facts unreadable: {type(exc).__name__}: {exc}")
            return LayerHealth("facts", True, False, f"unreadable: {exc}")
        return LayerHealth("facts", True, hasattr(self.facts, "add"), "ok", len(rows))

    def _boot_lessons(self, problems: list[str]) -> LayerHealth:
        if self.lessons is None:
            return LayerHealth("lessons", False, False, "no LearningStore wired")
        try:
            store = self.lessons.store
            # _sync() is the store's own repair path: it rebuilds the derived snapshots
            # from the authoritative journal, which is exactly a BOOT responsibility.
            store._sync()
            verified = len(store.verified())
            corrupt = int(getattr(store, "corrupt_lines", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"LearningStore unreadable: {type(exc).__name__}: {exc}")
            return LayerHealth("lessons", True, False, f"unreadable: {exc}")
        detail = "ok"
        if corrupt:
            detail = f"{corrupt} corrupt line(s) skipped; earlier records intact"
            problems.append(f"LearningStore has {corrupt} corrupt line(s) (tail ignored)")
        writable = os.access(store.data_dir, os.W_OK) if store.data_dir.exists() else True
        return LayerHealth("lessons", True, writable, detail, verified)

    # -------------------------------------------------------- TASK_START / BEFORE_PLAN
    def task_start(self, *, task_id: str, project_id: str, goal: str,
                   task_class: str | None = None, environment: str | None = None,
                   app_version: str | None = None, runtime: str | None = None,
                   config: RetrievalConfig | None = None,
                   write_checkpoint: bool = True) -> TaskContext:
        """Automatic retrieval at the task boundary. Nobody calls a search tool.

        ``goal`` is the query. Everything returned is read from the stores in this
        process, so a restarted process reaches the same context.
        """
        memory = self.retriever.search(goal, project_id=project_id, task_class=task_class,
                                       environment=environment, app_version=app_version,
                                       runtime=runtime, config=config, now=self.clock())
        existing = self.load_checkpoint(task_id)
        if write_checkpoint and existing is None:
            self.checkpoint(task_id=task_id, project_id=project_id, phase="task_start",
                            next_action=f"plan: {goal[:200]}")
            existing = self.load_checkpoint(task_id)
        return TaskContext(task_id=task_id, project_id=project_id, goal=goal,
                           memory=memory, checkpoint=existing)

    #: BEFORE_PLAN is the same retrieval at the same boundary; kept as a named entry
    #: point because the contract names the phase and callers hook it by name.
    before_plan = task_start

    # -------------------------------------------------------- RESUME
    def resume(self, *, task_id: str, project_id: str = "",
               observed_effects: list[dict] | None = None,
               config: RetrievalConfig | None = None) -> ResumeContext:
        """Restart/crash recovery from the durable checkpoint — never from chat history.

        ``observed_effects`` is what the world can be seen to contain right now (receipts,
        files, rows). Effects the checkpoint recorded as done and that are NOT observed
        come back as uncertain: memory refuses to assume they happened.
        """
        cp = self.load_checkpoint(task_id)
        project = project_id or (cp.project_id if cp else "")
        goal = (cp.next_action if cp and cp.next_action else task_id)
        memory = self.retriever.search(goal, project_id=project, config=config, now=self.clock())

        confirmed: list[dict] = []
        uncertain: list[dict] = list(cp.effects_uncertain) if cp else []
        notes: list[str] = []
        if cp is None:
            notes.append("no durable checkpoint for this task: resume has nothing to stand on")
        else:
            seen = {_effect_id(e) for e in (observed_effects or [])}
            for effect in cp.effects_done:
                if observed_effects is None:
                    notes.append("no observation supplied: recorded effects are reported as "
                                 "recorded, not as verified")
                    confirmed.append(effect)
                elif _effect_id(effect) in seen:
                    confirmed.append(effect)
                else:
                    uncertain.append({**effect, "reconciliation": "recorded as done but not "
                                                                 "observed after restart"})
            for effect in (observed_effects or []):
                known = {_effect_id(e) for e in cp.effects_done} | {_effect_id(e) for e in cp.effects_uncertain}
                if _effect_id(effect) not in known:
                    uncertain.append({**effect, "reconciliation": "observed but never recorded "
                                                                 "in the checkpoint"})
        return ResumeContext(task_id=task_id, project_id=project, checkpoint=cp, memory=memory,
                             confirmed_effects=confirmed, uncertain_effects=uncertain,
                             next_action=(cp.next_action if cp else ""), notes=notes)

    # -------------------------------------------------------- CHECKPOINT
    def checkpoint(self, *, task_id: str, project_id: str = "", next_action: str = "",
                   phase: str = "", state: dict | None = None,
                   effects_done: list[dict] | None = None,
                   effects_uncertain: list[dict] | None = None) -> Path:
        """Persist the continuation. Atomic: a crash mid-write leaves the previous
        checkpoint intact rather than a truncated one (the same discipline the canonical
        note writer uses — a half-written checkpoint is worse than none)."""
        previous = self.load_checkpoint(task_id)
        cp = Checkpoint(
            task_id=task_id,
            project_id=project_id or (previous.project_id if previous else ""),
            next_action=next_action or (previous.next_action if previous else ""),
            phase=phase or (previous.phase if previous else ""),
            state={**(previous.state if previous else {}), **(state or {})},
            effects_done=(previous.effects_done if previous else []) + list(effects_done or []),
            effects_uncertain=(previous.effects_uncertain if previous else []) + list(effects_uncertain or []),
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock())),
        )
        path = self.checkpoint_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, cp.as_dict())
        return path

    def load_checkpoint(self, task_id: str) -> Checkpoint | None:
        path = self.checkpoint_path(task_id)
        if not path.is_file():
            return None
        try:
            return Checkpoint.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def clear_checkpoint(self, task_id: str) -> bool:
        path = self.checkpoint_path(task_id)
        if path.is_file():
            path.unlink()
            return True
        return False

    # -------------------------------------------------------- AFTER_VERIFIED_RESULT
    def after_verified_result(self, *, task_id: str, project_id: str, kind: str, summary: str,
                              detail: str = "", evidence_refs: list[str] | None = None,
                              lesson: Any = None, note_title: str = "",
                              tags: list[str] | None = None) -> dict:
        """Curator: keep only durable value, in exactly ONE canonical place.

        ``kind`` is one of DECISION / WORKED / FAILED / CONVENTION / EVIDENCE / NEXT.
        A ``lesson`` (a ``CoachingEpisode``) goes to LearningStore as a CANDIDATE — this
        method never promotes anything to verified, because promotion needs an independent
        verifier and fresh evidence, which is ``LessonBook.verify``'s job.

        When both a lesson and a note are written, the note gets a POINTER to the lesson
        id, not a copy of its text: one canonical copy, links between stores.
        """
        from .lessons import LessonPoisoned, poison_reasons

        if kind not in KEEP_KINDS:
            raise MemoryLifecycleError(f"{kind} is not durable value; expected one of {KEEP_KINDS}")
        for text in (summary, detail):
            reasons = [r for r in poison_reasons(str(text or "")) if "too short" not in r]
            if reasons:
                # A "result" that tries to grant something is not a result worth keeping.
                raise LessonPoisoned(reasons)

        receipt: dict = {"task_id": task_id, "project_id": project_id, "kind": kind,
                         "lesson_id": "", "note_ref": "", "skipped": []}
        if lesson is not None:
            if self.lessons is None:
                receipt["skipped"].append("lesson: no LearningStore wired")
            else:
                rec = self.lessons.save(lesson)
                receipt["lesson_id"] = rec.get("task_id", "")
                receipt["lesson_status"] = (rec.get("lesson") or {}).get("status")

        if self.notes is not None and getattr(self.notes, "writable", lambda: False)():
            body = _curated_note(kind=kind, task_id=task_id, project_id=project_id,
                                 summary=summary, detail=detail,
                                 evidence_refs=evidence_refs or [],
                                 lesson_id=receipt["lesson_id"])
            try:
                receipt["note_ref"] = self.notes.write_note(
                    title=note_title or f"{kind}: {summary[:60]}",
                    content=body, kind=_note_kind(kind), project=project_id,
                    tags=list(tags or []) + [kind.lower()], source_run_id=task_id)
            except Exception as exc:  # noqa: BLE001 — a failed note must not lose the lesson
                receipt["skipped"].append(f"note: {type(exc).__name__}: {exc}")
        elif self.notes is not None:
            receipt["skipped"].append("note: the notes port is read-only")

        # Any write can change what retrieval must return: drop the cache now, not later.
        self.retriever.invalidate("after_verified_result")
        return receipt

    # -------------------------------------------------------- housekeeping
    def invalidate(self, reason: str = "") -> None:
        """Call after a write, a deletion, a supersession, a version change or an
        embedder change anywhere in the stores."""
        self.retriever.invalidate(reason)


# ---------------------------------------------------------------- helpers
def _note_kind(kind: str) -> str:
    return {"DECISION": "decision", "CONVENTION": "decision", "WORKED": "lesson",
            "FAILED": "lesson", "EVIDENCE": "fact", "NEXT": "task"}.get(kind, "note")


def _curated_note(*, kind: str, task_id: str, project_id: str, summary: str, detail: str,
                  evidence_refs: list[str], lesson_id: str) -> str:
    lines = [f"- kind: {kind}", f"- project: {project_id}", f"- task: {task_id}", "", summary.strip()]
    if detail.strip():
        lines += ["", detail.strip()]
    if evidence_refs:
        lines += ["", "## Evidence", *[f"- {ref}" for ref in evidence_refs[:20]]]
    if lesson_id:
        # A POINTER, never a second copy: the lesson text lives in LearningStore.
        lines += ["", "## Lesson", f"- learning-store lesson: `{lesson_id}`",
                  "- the lesson body is NOT copied here; LearningStore is its single "
                  "canonical location"]
    return "\n".join(lines)


def _effect_id(effect: dict) -> str:
    if not isinstance(effect, dict):
        return str(effect)
    for key in ("id", "effect_id", "side_effect_id", "receipt", "ref"):
        if effect.get(key):
            return str(effect[key])
    return json.dumps(effect, sort_keys=True, ensure_ascii=False)


def _atomic_write_json(dest: Path, payload: dict) -> None:
    """Write completely or not at all. A truncated checkpoint would send a resumed task
    down a path that never existed, which is worse than having no checkpoint."""
    tmp = dest.with_name(dest.name + f".tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


__all__ = ["CHECKPOINT_VERSION", "Checkpoint", "DEGRADED", "HEALTHY", "KEEP_KINDS", "LayerHealth",
           "MemoryHealth", "MemoryLifecycle", "MemoryLifecycleError", "NOT_CONFIGURED",
           "READ_ONLY", "ResumeContext", "TaskContext"]
