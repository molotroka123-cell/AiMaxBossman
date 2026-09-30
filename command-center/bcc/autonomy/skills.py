"""Episodic traces -> verified, versioned, revocable Jev skills.

A trace records one executed operation: goal, starting state, instruction, exact
actions (each with the HandBroker request hash), observations, errors, recovery,
result, Claude/Codex reviews and the user decision.

A trace becomes a skill only when ALL hold (owner plan, 5 conditions + owner update):

1. the outcome passed the acceptance tests;
2. Claude AND Codex approved the exact skill ARTIFACT hash (Review.sha = artifact
   hash, Review.diff_sha256 = trace hash);
3. secrets and user-specific transient values are removed (redaction is a fixed
   point: redacting again changes nothing);
4. parameters, preconditions, failure detection, rollback and an explicit timeout
   are present;
5. it succeeded in staging at least once.

Web pages, chat text, quoted text and model output never become skills directly:
a trace whose origin is not an executed operation, or whose actions lack a
Bossman request hash, is refused.

Skills are versioned (same name -> next version), scoped (app, env), revocable,
linked to the source trace/evidence, and carry a confidence that drops on failure
and an expiry. Retrieval is by goal text, app, environment and confidence.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

from ..jev.decision import scrub
from ..pit.secret_filter import redact_secrets
from .workers import sha256_json

EXECUTED_ORIGINS = ("executed",)
UNTRUSTED_ORIGINS = ("web", "chat", "quoted", "model_output")
MIN_CONFIDENCE = 0.5
START_CONFIDENCE = 0.6
DEFAULT_TTL_S = 30 * 24 * 3600

_TRANSIENT = (
    (re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s\"']+"), r"<HOME>"),
    (re.compile(r"/(?:home|Users)/[^/\s\"']+"), "<HOME>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<EMAIL>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<UUID>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b"), "<TIME>"),
    (re.compile(r"\b(?:127\.0\.0\.1|localhost):\d{2,5}\b"), "localhost:<PORT>"),
    (re.compile(r"(?<![\w-])-?100\d{8,12}\b"), "<CHAT_ID>"),
)
_EVIDENCE_KEYS = {"request_hash", "sha", "trace_hash", "artifact_hash", "evidence", "source_evidence"}


def redact_text(text: str) -> str:
    out, _ = redact_secrets(str(text))
    out = scrub(out)
    for pattern, repl in _TRANSIENT:
        out = pattern.sub(repl, out)
    return out


def redact(obj: Any, _key: str = "") -> Any:
    """Recursive redaction. Evidence hashes (request_hash, sha, ...) are kept verbatim."""
    if isinstance(obj, dict):
        return {k: (v if k in _EVIDENCE_KEYS else redact(v, k)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v, _key) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def is_redacted(obj: Any) -> bool:
    return redact(obj) == json.loads(json.dumps(obj, default=str))


@dataclass
class EpisodeTrace:
    goal_id: str
    start_state: dict
    instruction: str
    actions: list[dict]              # each {"action", "target", "arguments", "request_hash", "ok"}
    observations: list[str]
    errors: list[str]
    recovery: list[str]
    result: dict
    reviews: list[dict]
    user_decision: str | None
    origin: str = "executed"
    app: str = ""
    env: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def redacted(self) -> "EpisodeTrace":
        return EpisodeTrace(**redact(self.as_dict()))

    def trace_hash(self) -> str:
        return sha256_json(self.as_dict())


def capture_trace(**kw: Any) -> EpisodeTrace:
    """Build a trace and redact it immediately: raw secrets never reach disk."""
    return EpisodeTrace(**kw).redacted()


@dataclass
class SkillSpec:
    name: str
    description: str
    app: str
    env: str
    parameters: dict[str, str]
    preconditions: list[str]
    steps: list[dict]
    failure_detection: list[str]
    rollback: list[str]
    timeout_s: int
    source_trace_hash: str
    evidence: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    version: int = 0
    confidence: float = START_CONFIDENCE
    expires_at: float = 0.0
    revoked: bool = False
    revoked_reason: str = ""
    successes: int = 0
    failures: int = 0

    ARTIFACT_FIELDS = ("name", "description", "app", "env", "parameters", "preconditions", "steps",
                       "failure_detection", "rollback", "timeout_s", "source_trace_hash", "evidence", "keywords")

    def artifact(self) -> dict:
        return {k: getattr(self, k) for k in self.ARTIFACT_FIELDS}

    def artifact_hash(self) -> str:
        return sha256_json(self.artifact())


class SkillRefused(ValueError):
    pass


def compile_skill(trace: EpisodeTrace, spec: SkillSpec, *, acceptance_passed: bool, approvals: Iterable[Any],
                  staging_successes: int) -> SkillSpec:
    """Return the compiled spec or raise SkillRefused with every failed condition."""
    problems: list[str] = []
    if trace.origin not in EXECUTED_ORIGINS:
        problems.append(f"origin {trace.origin!r} is not an executed operation (web/chat/model text never "
                        f"becomes a skill directly)")
    if not trace.actions or not all(isinstance(a, dict) and a.get("request_hash") for a in trace.actions):
        problems.append("every action needs a Bossman HandBroker request_hash")
    if not acceptance_passed:
        problems.append("1: acceptance tests did not pass")
    thash, ahash = trace.trace_hash(), spec.artifact_hash()
    if spec.source_trace_hash != thash:
        problems.append("spec is not linked to this trace")
    approved = {getattr(r, "reviewer", None) for r in approvals
                if getattr(r, "verdict", None) == "APPROVE" and getattr(r, "sha", None) == ahash
                and getattr(r, "diff_sha256", None) == thash and getattr(r, "goal_id", None) == trace.goal_id}
    if not {"claude", "codex"} <= approved:
        problems.append("2: Claude and Codex must both approve this exact artifact hash + trace hash")
    if not is_redacted(trace.as_dict()) or not is_redacted(spec.artifact()):
        problems.append("3: trace/spec still contain secrets or user-specific transient values")
    if not spec.parameters or not spec.preconditions or not spec.failure_detection or not spec.rollback \
            or not spec.steps:
        problems.append("4: parameters, preconditions, steps, failure detection and rollback must be explicit")
    if type(spec.timeout_s) is not int or not 1 <= spec.timeout_s <= 24 * 3600:
        problems.append("4: explicit timeout_s required")
    if staging_successes < 1:
        problems.append("5: no staging success yet")
    if problems:
        raise SkillRefused("; ".join(problems))
    return replace(spec, evidence=list(spec.evidence) or [thash])


_WORD = re.compile(r"[\w-]{3,}", re.U)


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "")}


class SkillStore:
    """JSON file per skill version under <root>/skills/<name>/v<N>.json."""

    def __init__(self, root: Path, *, clock=time.time, ttl_s: int = DEFAULT_TTL_S):
        self.root = Path(root) / "skills"
        self.clock, self.ttl_s = clock, ttl_s

    def _dir(self, name: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", name):
            raise ValueError("skill name must be a lowercase slug")
        return self.root / name

    def _write(self, spec: SkillSpec) -> None:
        d = self._dir(spec.name)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"v{spec.version}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(spec), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    def versions(self, name: str) -> list[SkillSpec]:
        d = self._dir(name)
        if not d.is_dir():
            return []
        out = [SkillSpec(**json.loads(p.read_text(encoding="utf-8"))) for p in d.glob("v*.json")]
        return sorted(out, key=lambda s: s.version)

    def add(self, spec: SkillSpec) -> SkillSpec:
        """Store a COMPILED spec as the next version of its name."""
        prior = self.versions(spec.name)
        new = replace(spec, version=(prior[-1].version + 1 if prior else 1),
                      expires_at=spec.expires_at or self.clock() + self.ttl_s, revoked=False)
        self._write(new)
        return new

    def get(self, name: str, version: int | None = None) -> SkillSpec | None:
        vs = self.versions(name)
        if version is not None:
            vs = [s for s in vs if s.version == version]
        return vs[-1] if vs else None

    def revoke(self, name: str, version: int, reason: str) -> SkillSpec:
        spec = self.get(name, version)
        if spec is None:
            raise KeyError(f"{name} v{version}")
        spec = replace(spec, revoked=True, revoked_reason=reason[:500])
        self._write(spec)
        return spec

    def record_outcome(self, name: str, version: int, *, success: bool) -> SkillSpec:
        """Success nudges confidence up (max 0.95); failure halves it. Below the
        minimum the skill is not retrieved (fallback to Claude/Codex guidance)."""
        spec = self.get(name, version)
        if spec is None:
            raise KeyError(f"{name} v{version}")
        if success:
            spec = replace(spec, confidence=round(min(0.95, spec.confidence + 0.05), 4), successes=spec.successes + 1)
        else:
            spec = replace(spec, confidence=round(spec.confidence * 0.5, 4), failures=spec.failures + 1)
        self._write(spec)
        return spec

    def all_latest(self) -> list[SkillSpec]:
        if not self.root.is_dir():
            return []
        out = []
        for d in sorted(p for p in self.root.iterdir() if p.is_dir()):
            vs = [s for s in self.versions(d.name) if not s.revoked]
            if vs:
                out.append(vs[-1])
        return out

    def retrieve(self, goal_text: str, *, app: str = "", env: str = "", min_confidence: float = MIN_CONFIDENCE,
                 limit: int = 5) -> list[SkillSpec]:
        now = self.clock()
        want = _tokens(goal_text)
        ranked = []
        for s in self.all_latest():
            if s.confidence < min_confidence or (s.expires_at and s.expires_at <= now):
                continue
            if app and s.app and s.app != app:
                continue
            if env and s.env and s.env != env:
                continue
            overlap = len(want & (_tokens(s.description) | set(s.keywords) | _tokens(s.name)))
            if want and overlap == 0:
                continue
            ranked.append((overlap, s.confidence, s.name, s))
        ranked.sort(key=lambda r: (-r[0], -r[1], r[2]))
        return [r[3] for r in ranked[:limit]]
