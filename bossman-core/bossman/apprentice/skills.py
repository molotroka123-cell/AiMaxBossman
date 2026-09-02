"""Verified skills: generalization from episodes, anchor matching against the
FRESH observation (READY | DEGRADED | INAPPLICABLE — never blind replay),
independent verification (deep_fix.Principal / Evidence), shadow replay,
promotion through learning_guard.autonomy_trainer and rollback.

Nothing here re-implements A/B, promotion stages, holdout or storage."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from bossman.computer_operator.models import ActionKind, ExpectedState, Observation
from bossman.deep_fix import Evidence, Principal
from bossman.learning_guard.autonomy_trainer import (AutonomyCandidate, Episode, evaluate_candidate, promote_candidate,
                                                     rollback_candidate)
from bossman.learning_guard.models import ABResult, RollbackInfo, SecuritySnapshot

from . import flags
from ._bootstrap import trace
from .errors import FlagDisabled, SelectorDrift, VerificationFailed
from .guards import resolve_target
from .models import AppIdentity, ApprenticeTask, Plan, PlanStep, RiskClass, SemanticTarget, sha
from .recording import ApprenticeMemory, assert_sanitized

SKILL_STATES = ("CANDIDATE", "SHADOW", "READY", "DEGRADED", "ROLLED_BACK", "REJECTED")


class SelfVerificationRefused(VerificationFailed):
    code = "self_verification_refused"


class SkillDegraded(SelectorDrift):
    code = "skill_degraded"


@dataclass(frozen=True, slots=True)
class EvidenceBinding:
    """Duck-typed `run` for deep_fix.Evidence.freshness_error: task / run / HEAD / environment binding."""
    task_id: str
    run_id: str = ""
    head_sha: str = ""
    environment: str = ""
    plan_bound_at: float = 0.0
    patched_at: float = 0.0


# ------------------------------------------------------------------ generalization
def generalize(episodes: list[dict], *, skill_id: str, title: str, task_type: str, environment: str, app: str,
               app_version: str, agent: str, model: str, principal_id: str, head_sha: str = "") -> dict:
    """Episode(s) -> candidate skill (UNVERIFIED / CANDIDATE). Semantic actions come only
    from steps that were verified ok; failures become failure branches with their recovery."""
    if not episodes:
        raise ValueError("at least one episode is required")
    actions: list[dict] = []
    seen: set[str] = set()
    checkpoints: list[dict] = []
    failure_branches: list[dict] = []
    recovery: list[dict] = []
    for ep in episodes:
        for r in ep.get("action_records") or []:
            ver = r.get("verification") or {}
            if r.get("result") == "ok" and ver.get("ok"):
                key = f"{r.get('step_id')}|{(r.get('action') or {}).get('kind')}|{(r.get('semantic_target') or {}).get('name')}"
                if key in seen:
                    continue
                seen.add(key)
                actions.append({"step_id": r.get("step_id", ""), "kind": (r.get("action") or {}).get("kind", ""),
                                "target": dict(r.get("semantic_target") or {}),
                                "app_identity": dict((r.get("application") or {}).get("expected") or {}),
                                "expected_transition": dict(r.get("expected_transition") or {}),
                                "checkpoint": r.get("checkpoint", ""), "risk_class": r.get("risk_class", "LOW"),
                                "side_effecting": bool(r.get("side_effect_id")),
                                "verification_method": ver.get("method", ""),
                                "text_redacted": (r.get("action") or {}).get("text_redacted", "")})
                if r.get("checkpoint") and r["checkpoint"] not in [c["name"] for c in checkpoints]:
                    checkpoints.append({"name": r["checkpoint"], "after_step": r.get("step_id", ""),
                                        "expected": dict(r.get("expected_transition") or {})})
            elif r.get("error_code"):
                failure_branches.append({"step_id": r.get("step_id", ""), "error_code": r.get("error_code", ""),
                                         "target": dict(r.get("semantic_target") or {})})
        for rec in ep.get("recovery") or []:
            recovery.append(dict(rec))
    first_app = actions[0]["app_identity"] if actions else {}
    skill = {
        "task_id": skill_id, "skill_id": skill_id, "record_type": "skill", "learning_status": "UNVERIFIED",
        "skill_state": "CANDIDATE", "title": title, "summary": f"generalized from {len(episodes)} episode(s)",
        "task_type": task_type, "environment": environment, "app": app, "app_version": app_version,
        "model": model, "agent": agent, "principal_id": principal_id,
        "run_id": "", "head_sha": head_sha, "start_sha": head_sha, "end_sha": head_sha,   # evidence binds to skill_id + HEAD
        "applicability": {"task_type": task_type, "app": app, "app_version": app_version, "environment": environment,
                          "window": first_app},
        "preconditions": [f"foreground app contains {first_app.get('app', app)!r}"] + (
            [f"window title contains {first_app['title_contains']!r}"] if first_app.get("title_contains") else []),
        "semantic_actions": actions, "semantic_anchors": [a["target"] | {"app": a["app_identity"].get("app", "")} for a in actions],
        "checkpoints": checkpoints, "expected_outcomes": [c["name"] for c in checkpoints],
        "failure_branches": failure_branches, "recovery": recovery,
        "rollback": {"prev_version": 0, "prev_ref": ""}, "version": 0,
        "source_episode_ids": [ep.get("task_id", "") for ep in episodes], "confidence": 0.0,
        "tags": {"domain": task_type, "risk": "medium" if any(a["side_effecting"] for a in actions) else "low"},
    }
    skill.pop("version")
    skill = trace().redact_obj(skill)
    assert_sanitized(skill, where="skill")
    return skill


# ------------------------------------------------------------------ independent verification
def attach_verification(record: dict, *, producer: Principal, verifier: Principal, evidence: Iterable[Evidence],
                        binding: EvidenceBinding, now: float | None = None, statement: str = "") -> dict:
    """Marks an episode or skill VERIFIED only with an independent verifier and fresh,
    bound, passing evidence. The producer can never verify itself."""
    ok, why = verifier.independent_of(producer)
    if not ok:
        raise SelfVerificationRefused(f"verifier {verifier.principal_id} is not independent of producer: {why}")
    evs = list(evidence)
    if not evs:
        raise VerificationFailed("no evidence")
    now = time.time() if now is None else now
    for e in evs:
        err = e.freshness_error(run=binding, now=now)
        if err:
            raise VerificationFailed(f"evidence {e.kind} rejected: {err}")
        if not e.passed:
            raise VerificationFailed(f"evidence {e.kind} did not pass: {e.detail}")
        if e.principal_id and e.principal_id == producer.principal_id:
            raise SelfVerificationRefused("evidence observed by the producer is not independent")
    out = dict(record)
    out["learning_status"] = "VERIFIED"
    out["verifiers"] = [{"principal_id": verifier.principal_id, "independence_class": verifier.independence_class,
                         "model_id": verifier.model_id, "run_id": verifier.run_id, "role": verifier.role}]
    out["verified_by"] = [verifier.principal_id]
    out["external_verification"] = statement or f"{verifier.principal_id}: {len(evs)} fresh evidence record(s) passed"
    out["evidence_records"] = [{"kind": e.kind, "source": e.source, "observed_at": e.at,
                                "collected_at": e.collected_at or e.at, "task_id": e.task_id, "run_id": e.run_id,
                                "principal_id": e.principal_id, "head_sha": e.head_sha, "environment": e.environment,
                                "expected": e.expected, "actual": e.actual, "passed": e.passed} for e in evs]
    out.setdefault("evidence", [])
    out["evidence"] = list(out["evidence"]) + [f"{e.kind}:{e.source}:{'pass' if e.passed else 'fail'}" for e in evs]
    out["principal_id"] = producer.principal_id
    out["model"] = out.get("model") or producer.model_id
    if out.get("record_type") == "skill":
        out["skill_state"] = "SHADOW"
    return out


# ------------------------------------------------------------------ matching (fresh observation wins)
@dataclass(slots=True)
class SkillMatch:
    state: str                       # READY | DEGRADED | INAPPLICABLE
    score: float
    checked: int
    unmatched: list[str] = field(default_factory=list)
    reason: str = ""


def match_skill(skill: dict, obs: Observation) -> SkillMatch:
    """Compare the skill's semantic anchors for the observed window with the FRESH UI tree."""
    fg = obs.foreground or {}
    app = str((skill.get("applicability") or {}).get("app") or skill.get("app") or "")
    if app and app.lower() not in str(fg.get("app", "")).lower():
        return SkillMatch("INAPPLICABLE", 0.0, 0, reason=f"foreground app {fg.get('app')!r} is not {app!r}")
    scores: list[float] = []
    unmatched: list[str] = []
    for a in skill.get("semantic_actions") or []:
        ident = AppIdentity(**{k: v for k, v in (a.get("app_identity") or {}).items() if k in AppIdentity.__slots__})
        if not ident.matches(fg)[0]:
            continue                                   # anchors of other screens cannot be checked now
        t = a.get("target") or {}
        if not (t.get("role") or t.get("name")):
            continue
        target = SemanticTarget.from_dict(t)
        res = resolve_target(target, obs)
        scores.append(res.score)
        if res.element is None:
            unmatched.append(target.label())
    if not scores:
        return SkillMatch("DEGRADED", 0.0, 0, reason="no anchor of this skill is visible on the fresh observation")
    score = round(sum(scores) / len(scores), 4)
    if unmatched:
        return SkillMatch("DEGRADED", score, len(scores), unmatched, "anchors missing on fresh observation: " + ", ".join(unmatched))
    return SkillMatch("READY", score, len(scores), [], "all visible anchors match")


def plan_from_skill(skill: dict, task: ApprenticeTask, obs: Observation) -> Plan:
    """Only a READY, VERIFIED skill becomes a plan; DEGRADED needs adaptation (typed error)."""
    if skill.get("learning_status") != "VERIFIED" or skill.get("skill_state") not in ("READY", "SHADOW"):
        raise SkillDegraded(f"skill {skill.get('skill_id')} is {skill.get('learning_status')}/{skill.get('skill_state')}, not replayable")
    m = match_skill(skill, obs)
    if m.state != "READY":
        raise SkillDegraded(f"skill {skill.get('skill_id')} {m.state}: {m.reason}")
    steps: list[PlanStep] = []
    for a in skill.get("semantic_actions") or []:
        t = a.get("target") or {}
        exp = a.get("expected_transition") or {}
        steps.append(PlanStep(
            step_id=a.get("step_id") or f"sk{len(steps)}", kind=ActionKind(a["kind"]),
            app=AppIdentity(**{k: v for k, v in (a.get("app_identity") or {}).items() if k in AppIdentity.__slots__}),
            target=SemanticTarget.from_dict(t) if (t.get("role") or t.get("name")) else None,
            text=a.get("text_redacted", ""),
            expected=ExpectedState(**{k: exp.get(k) for k in ("contains_text", "window_title_contains",
                                                              "foreground_app_contains", "url_contains", "absent_text")}),
            risk=RiskClass(a.get("risk_class", "LOW")), side_effecting=bool(a.get("side_effecting")),
            checkpoint=a.get("checkpoint", ""), source=f"skill:{skill.get('skill_id')}"))
    if steps:
        last = steps[-1]
        steps[-1] = PlanStep(**{**{s: getattr(last, s) for s in PlanStep.__slots__}, "is_goal": True})
    return Plan(goal=task.goal, steps=steps, source=f"skill:{skill.get('skill_id')}", skill_ref=str(skill.get("skill_id")))


# ------------------------------------------------------------------ shadow replay (dry run)
def shadow_replay(skill: dict, screens: Iterable[Observation]) -> dict:
    """Dry-run: for each fresh screen check that the skill's anchors for that screen
    resolve. No actuation. Requires BOSSMAN_SKILL_SHADOW_REPLAY."""
    if not flags.enabled(flags.SKILL_SHADOW_REPLAY):
        raise FlagDisabled(f"{flags.SKILL_SHADOW_REPLAY} is off")
    runs = []
    for obs in screens:
        m = match_skill(skill, obs)
        runs.append({"observation_id": obs.id, "generation": int(obs.generation), "state": m.state, "score": m.score,
                     "checked": m.checked, "unmatched": list(m.unmatched)})
    ok = bool(runs) and all(r["state"] == "READY" for r in runs)
    return {"skill_id": skill.get("skill_id"), "runs": runs, "ok": ok, "screens": len(runs),
            "degraded_screens": sum(1 for r in runs if r["state"] != "READY")}


def ab_results_from_replays(task_class: str, replays: list[dict], baseline: dict[str, bool]) -> list[ABResult]:
    """guarded = shadow replay ok (skill), raw = baseline (without skill) for the same task id."""
    out = []
    for i, rep in enumerate(replays):
        tid = str(rep.get("task_id") or f"{task_class}-{i}")
        out.append(ABResult(task_id=tid, task_class=task_class, raw_verified=bool(baseline.get(tid, False)),
                            guarded_verified=bool(rep.get("ok"))))
    return out


# ------------------------------------------------------------------ promotion + rollback
def episode_to_training(ep: dict) -> Episode:
    recs = ep.get("action_records") or []
    fresh = bool(recs) and all((r.get("post_observation") or {}).get("generation", -1) > (r.get("pre_observation") or {}).get("generation", 0)
                               for r in recs if r.get("result") == "ok")
    ver = (ep.get("verifiers") or [{}])[0]
    return Episode(task_id=str(ep.get("task_id")), state_hash=str((recs[0].get("pre_observation") or {}).get("hash", "")) if recs else "",
                   action_type="skill", semantic_anchor=";".join(f"{a.get('role')}:{a.get('name')}" for a in ep.get("semantic_anchors") or []),
                   fresh_observation=fresh, verified_success=ep.get("learning_status") == "VERIFIED" and ep.get("outcome") == "SUCCEED",
                   planner_principal=str(ep.get("principal_id") or ""), verifier_principal=str(ver.get("principal_id") or ""),
                   verifier_independence_class=str(ver.get("independence_class") or "same_run"),
                   environment_fingerprint=str(ep.get("environment") or ""), model_version=str(ep.get("model") or ""),
                   stale_session=False, self_reported_only=not bool(ep.get("evidence_records")),
                   contains_hidden_cot=any(k in ep for k in ("chain_of_thought", "hidden_reasoning")),
                   false_success=(ep.get("outcome") == "SUCCEED" and not all((r.get("verification") or {}).get("ok") for r in recs if r.get("result") == "ok")),
                   security_regression=bool(ep.get("security_regression")))


class SkillPromoter:
    """Candidate -> SHADOW (evaluate_candidate) -> PROMOTED (learning_guard via
    promote_candidate, owner + tested rollback) -> skill_state READY, new version."""

    def __init__(self, memory: ApprenticeMemory) -> None:
        self.memory = memory

    def evaluate(self, skill: dict, episodes: list[dict], *, holdout=None, baseline_success: float | None = None,
                 min_samples: int = 3) -> AutonomyCandidate:
        if not flags.enabled(flags.SKILL_PROMOTION):
            raise FlagDisabled(f"{flags.SKILL_PROMOTION} is off")
        cand = AutonomyCandidate(candidate_id=str(skill["skill_id"]), kind="skill",
                                 scope={"task_class": skill.get("task_type", ""), "environment": skill.get("environment", ""),
                                        "model_version": skill.get("model", ""), "risky": skill.get("tags", {}).get("risk") == "high"},
                                 hypothesis=str(skill.get("title", "")), rollback_ref=f"{skill['skill_id']}@v{int(skill.get('version') or 0)}")
        return evaluate_candidate(cand, [episode_to_training(e) for e in episodes], holdout=holdout,
                                  min_samples=min_samples, baseline_success=baseline_success)

    def promote(self, skill: dict, cand: AutonomyCandidate, ab: list[ABResult], *, security_before: SecuritySnapshot,
                security_after: SecuritySnapshot, shadow_runs: int, owner_approved: bool, rollback_tested: bool) -> tuple[dict, AutonomyCandidate]:
        if not flags.enabled(flags.SKILL_PROMOTION):
            raise FlagDisabled(f"{flags.SKILL_PROMOTION} is off")
        prev_version = int(skill.get("version") or 0)
        rb = RollbackInfo(prev_stage="SHADOW", prev_ref=f"{skill['skill_id']}@v{prev_version}", reason="apprentice skill promotion")
        final = promote_candidate(cand, ab, security_before=security_before, security_after=security_after,
                                  shadow_runs=shadow_runs, owner_approved=owner_approved, rollback_tested=rollback_tested, rollback=rb)
        if final.status != "PROMOTED":
            return skill, final
        promoted = {k: v for k, v in skill.items() if k not in ("version", "supersedes_version", "case_id", "created_at", "tombstone", "superseded_by_version")}
        promoted["skill_state"] = "READY"
        promoted["rollback"] = {"prev_version": prev_version, "prev_ref": rb.prev_ref, "tested": True}
        stored = self.memory.store_skill(promoted, expected_version=prev_version)
        return stored, final

    def rollback(self, skill_id: str, reason: str) -> dict:
        """Restore the previous version from history (tombstoned copy); if none, mark ROLLED_BACK/REJECTED."""
        cur = next((s for s in self.memory.skills(verified_only=False) if s.get("skill_id") == skill_id), None)
        if cur is None:
            raise ValueError(f"unknown skill {skill_id}")
        hist = [h for h in self.memory.store._read(self.memory.store.history_path)
                if h.get("case_id") == cur.get("case_id") and int(h.get("version") or 0) == int(cur.get("version") or 0) - 1]
        base = dict(hist[-1]) if hist else dict(cur)
        for k in ("version", "supersedes_version", "case_id", "created_at", "tombstone", "superseded_by_version"):
            base.pop(k, None)
        if not hist:
            base["skill_state"] = "ROLLED_BACK"; base["learning_status"] = "REJECTED"
        base["rollback"] = {"prev_version": int(cur.get("version") or 0), "prev_ref": f"{skill_id}@v{cur.get('version')}", "reason": reason}
        rollback_candidate(AutonomyCandidate(candidate_id=skill_id, kind="skill", scope={}, hypothesis="", rollback_ref=base["rollback"]["prev_ref"]), reason)
        return self.memory.store_skill(base, expected_version=int(cur.get("version") or 0))


def degrade_skill(memory: ApprenticeMemory, skill: dict, reason: str) -> dict:
    """Fresh observation contradicted the skill: persist DEGRADED (needs adaptation)."""
    d = {k: v for k, v in skill.items() if k not in ("version", "supersedes_version", "case_id", "created_at", "tombstone", "superseded_by_version")}
    d["skill_state"] = "DEGRADED"; d["degraded_reason"] = reason[:500]
    return memory.store_skill(d, expected_version=int(skill.get("version") or 0))
