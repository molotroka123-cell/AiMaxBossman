"""Skill recording: ActionRecords -> Task Episode (factual), semantic anchors,
sanitization, and ApprenticeMemory — a thin wrapper over learning/trace.py
LearningStore (redaction, validation, versioning, tombstones, atomic writes,
locking are NOT re-implemented here).

Raw logs never become a skill automatically: this module only writes episodes
(UNVERIFIED / FAILED_EXPERIMENT / PARTIAL) and negative lessons; VerifiedSkill
records are produced by skills.py after independent verification + shadow replay.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import flags
from ._bootstrap import SKILL_SCHEMA_PATH, load_json, trace
from .errors import FlagDisabled, SecretInRecord
from .models import ActionRecord, ApprenticeState, ApprenticeTask, TaskResult, sha

_FORBIDDEN_KEYS = frozenset({"chain_of_thought", "hidden_reasoning", "thoughts", "scratchpad", "raw_prompt",
                             "raw_prompts", "raw_log", "screenshot_bytes"})
MAX_RECORDS_PER_EPISODE = 200


def skill_schema() -> dict:
    return load_json(SKILL_SCHEMA_PATH)


def _walk_keys(obj: Any) -> Iterable[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def assert_sanitized(obj: Any, *, where: str) -> None:
    """Strict gate: a secret or hidden-reasoning field reaching memory is an
    upstream failure — reject (typed), never launder silently."""
    tr = trace()
    for k in _walk_keys(obj):
        kl = k.lower()
        if kl in _FORBIDDEN_KEYS:
            raise SecretInRecord(f"{where}: forbidden field {k!r} (hidden reasoning / raw prompt)")
    for s in tr._walk_strings(obj):
        if tr.has_secret(s):
            raise SecretInRecord(f"{where}: secret-like value present")


def semantic_anchors(records: Iterable[ActionRecord | dict]) -> list[dict]:
    """Unique (app, target) anchors seen in the records. Semantic only."""
    seen: dict[str, dict] = {}
    for r in records:
        d = r.to_dict() if isinstance(r, ActionRecord) else dict(r)
        t = d.get("semantic_target") or {}
        if not (t.get("role") or t.get("name")):
            continue
        app = (d.get("application") or {}).get("app", "")
        key = f"{app}|{t.get('role')}|{t.get('name')}"
        if key not in seen:
            seen[key] = {"app": app, "role": t.get("role", ""), "name": t.get("name", ""), "text": t.get("text", ""),
                         "description": t.get("description", ""), "anchors": list(t.get("anchors") or []),
                         "action_kind": (d.get("action") or {}).get("kind", ""),
                         "window_title_contains": ((d.get("application") or {}).get("expected") or {}).get("title_contains", "")}
    return list(seen.values())


@dataclass(slots=True)
class EpisodeRecorder:
    """Attach `recorder.on_record` to the engine; call `finish(result)` afterwards."""
    task: ApprenticeTask
    agent: str
    model: str
    principal_id: str
    app: str = ""
    app_version: str = ""
    records: list[dict] = field(default_factory=list)
    dropped: int = 0

    def on_record(self, rec: ActionRecord) -> None:
        d = rec.to_dict()
        try:
            assert_sanitized(d, where="action record")
        except SecretInRecord:
            self.dropped += 1
            raise
        if len(self.records) < MAX_RECORDS_PER_EPISODE:
            self.records.append(d)

    def finish(self, result: TaskResult) -> dict:
        errors = [{"step_id": r.get("step_id", ""), "error_code": r.get("error_code", ""), "result": r.get("result", "")}
                  for r in self.records if r.get("result") != "ok"]
        status = "UNVERIFIED" if result.state is ApprenticeState.SUCCEED else (
            "PARTIAL" if result.checkpoints_reached else "FAILED_EXPERIMENT")
        ep = {
            "task_id": self.task.task_id, "record_type": "episode", "learning_status": status,
            "title": f"episode: {self.task.goal[:80]}", "summary": result.reason[:500],
            "task_type": self.task.task_type, "environment": self.task.environment or "unknown-env",
            "app": self.app, "app_version": self.app_version, "model": self.model, "agent": self.agent,
            "run_id": self.task.run_id, "session_id": self.task.session_id, "principal_id": self.principal_id,
            "head_sha": self.task.head_sha, "start_sha": self.task.head_sha, "end_sha": self.task.head_sha,
            "outcome": result.state.value, "checkpoints_reached": list(result.checkpoints_reached),
            "action_records": list(self.records), "errors": errors,
            "semantic_anchors": semantic_anchors(self.records),
            "recovery": [{"step_id": e["step_id"], "error_code": e["error_code"]} for e in errors if e["error_code"]],
            "evidence": [f"{r['record_id']}:{r['result']}" for r in self.records if r.get("verification")],
            "confidence": 0.0,
            "tags": {"domain": self.task.task_type},
        }
        ep = trace().redact_obj(ep)
        assert_sanitized(ep, where="episode")
        return ep


def negative_lesson(*, task: ApprenticeTask, record: dict, why_dangerous: str, agent: str, model: str,
                    principal_id: str, verified_by: list[str] | None = None) -> dict:
    """A verified example of what failed or was dangerous (consulted before acting)."""
    t = record.get("semantic_target") or {}
    label = f"{t.get('role', '')}:{t.get('name') or t.get('text', '')}"
    lesson = {
        "task_id": f"lesson_{sha(task.task_id, record.get('record_id'), why_dangerous)[:16]}",
        "record_type": "lesson", "learning_status": "FAILED_EXPERIMENT",
        "title": f"lesson: {why_dangerous[:80]}", "summary": why_dangerous[:500],
        "task_type": task.task_type, "environment": task.environment or "unknown-env",
        "app": (record.get("application") or {}).get("app", ""), "model": model, "agent": agent,
        "run_id": task.run_id, "principal_id": principal_id,
        "lesson": {"app": (record.get("application") or {}).get("app", ""), "target_label": label,
                   "action_kind": (record.get("action") or {}).get("kind", ""), "what_failed": record.get("result", ""),
                   "why_dangerous": why_dangerous, "error_code": record.get("error_code", "")},
        "source_episode_ids": [task.task_id], "verified_by": list(verified_by or []),
        "tags": {"domain": task.task_type, "risk": "high"},
    }
    lesson = trace().redact_obj(lesson)
    assert_sanitized(lesson, where="lesson")
    return lesson


class ApprenticeMemory:
    """Episodes, skills and lessons through LearningStore. Records that fail the
    schema on read are skipped (corrupted trace never applied)."""

    def __init__(self, data_dir: Path, docs_dir: Path | None = None) -> None:
        tr = trace()
        self.schema = skill_schema()
        self.store = tr.LearningStore(Path(data_dir), Path(docs_dir or Path(data_dir) / "docs"), schema=self.schema)
        self.skipped_invalid = 0

    # ------------------------------------------------------------ write
    def record_episode(self, episode: dict) -> dict | None:
        if not flags.enabled(flags.SKILL_RECORDING):
            return None
        assert_sanitized(episode, where="episode")
        return self.store.add(dict(episode), write_markdown=False)

    def record_lesson(self, lesson: dict) -> dict | None:
        if not flags.enabled(flags.SKILL_RECORDING):
            return None
        assert_sanitized(lesson, where="lesson")
        return self.store.add(dict(lesson), write_markdown=False)

    def store_skill(self, skill: dict, *, expected_version: int | None = None) -> dict:
        """Used by skills.py only after independent verification + shadow replay."""
        assert_sanitized(skill, where="skill")
        if skill.get("record_type") != "skill":
            raise ValueError("record_type must be 'skill'")
        return self.store.add(dict(skill), write_markdown=False, expected_version=expected_version)

    # ------------------------------------------------------------ read
    def _valid(self, cases: Iterable[dict]) -> list[dict]:
        tr = trace()
        out = []
        for c in cases:
            errs = tr.validate(c, schema=self.schema)
            errs = [e for e in errs if not e.startswith("case_id does not match")]      # id is checked by the store
            if errs or c.get("tombstone"):
                self.skipped_invalid += 1 if errs else 0
                continue
            out.append(c)
        return out

    def all_current(self) -> list[dict]:
        return self._valid(self.store._read(self.store.verified_path) + self.store._read(self.store.failed_path))

    def episodes(self) -> list[dict]:
        return [c for c in self.all_current() if c.get("record_type") == "episode"]

    def skills(self, *, verified_only: bool = True) -> list[dict]:
        return [c for c in self.all_current() if c.get("record_type") == "skill"
                and (not verified_only or c.get("learning_status") == "VERIFIED")]

    def lessons(self) -> list[dict]:
        return [{"lesson_id": c["task_id"], **(c.get("lesson") or {}), "summary": c.get("summary", "")}
                for c in self.all_current() if c.get("record_type") == "lesson"]

    def current(self, cid: str) -> dict | None:
        return self.store.current(cid)

    @property
    def corrupt_lines(self) -> int:
        return self.store.corrupt_lines

    # ------------------------------------------------------------ proposal P5
    def export_evidence_bundle(self, task_id: str) -> dict:
        if not flags.enabled(flags.EVIDENCE_EXPORT):
            raise FlagDisabled(f"{flags.EVIDENCE_EXPORT} is off")
        eps = [e for e in self.episodes() if e.get("task_id") == task_id]
        bundle = {"task_id": task_id, "episodes": eps,
                  "lessons": [l for l in self.lessons() if task_id in json.dumps(l)],
                  "skills": [s for s in self.skills(verified_only=False) if task_id in (s.get("source_episode_ids") or [])]}
        bundle = trace().redact_obj(bundle)
        assert_sanitized(bundle, where="evidence bundle")
        return bundle
