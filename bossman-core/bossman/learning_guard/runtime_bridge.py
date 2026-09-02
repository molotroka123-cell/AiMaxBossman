"""Runtime wiring for the Autonomy Trainer (shadow) and the local cognitive-reuse
experiment — both OFF by default (audit P0: "connect to the real runtime under
OFF flags", not a parallel engine).

* Trainer: every learning record stored by the Deep Fix runtime becomes a sanitized
  ``Episode`` (typed decision + outcome, no prompts, no hidden reasoning) appended
  to ``autonomy_episodes.jsonl`` next to the learning corpus; the task-class
  candidate is re-evaluated through ``autonomy_trainer.evaluate_candidate`` with a
  MEASURED baseline (success rate of the earlier episodes of that class) — never a
  guessed one. Flag off → nothing is written, nothing is evaluated.
* Reuse: ``ExecutionCache.get`` consults a reuse gate only while
  BOSSMAN_COGNITIVE_REUSE_EXPERIMENT is on; the gate serves reuse for a task class
  only after ``allow_local_cognitive_reuse`` accepted a recorded same-model A/B.
  Flag off → the execution cache behaves exactly as before.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable

from .autonomy_trainer import (FLAG as TRAINER_FLAG, AutonomyCandidate, Episode, enabled as trainer_enabled,
                               evaluate_candidate, record_candidate)

REUSE_FLAG = "BOSSMAN_COGNITIVE_REUSE_EXPERIMENT"
EPISODES_FILE = "autonomy_episodes.jsonl"
MIN_BASELINE_EPISODES = 3


def reuse_experiment_enabled() -> bool:
    return os.getenv(REUSE_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------- learning package (installed or checkout)
def _learning():
    try:
        from learning import trace as lt  # noqa: WPS433  (bossman-shared distribution)
        return lt
    except Exception:  # noqa: BLE001
        import sys
        root = Path(__file__).resolve().parents[3]
        if str(root) not in sys.path and (root / "learning").is_dir():
            sys.path.insert(0, str(root))
        try:
            from learning import trace as lt  # noqa: WPS433
            return lt
        except Exception:  # noqa: BLE001
            return None


def default_episodes_path() -> Path | None:
    lt = _learning()
    if lt is None:
        return None
    return lt.LearningStore().data_dir / EPISODES_FILE


# ---------------------------------------------------------------- trainer
def episode_from_learning_record(rec: dict) -> Episode | None:
    """Typed episode from a learning record; None when the record cannot be typed."""
    task_id = str(rec.get("task_id") or "")
    if not task_id:
        return None
    verifiers = rec.get("verifiers") or []
    v0 = verifiers[0] if verifiers and isinstance(verifiers[0], dict) else {}
    evidence = rec.get("evidence_records") or []
    planner = str(rec.get("principal_id") or rec.get("agent") or "")
    return Episode(
        task_id=task_id,
        state_hash=hashlib.sha256(f"{task_id}|{rec.get('start_sha', '')}|{rec.get('end_sha', '')}".encode()).hexdigest()[:16],
        action_type=str(rec.get("bug_class") or rec.get("domain") or "fix"),
        semantic_anchor=str(rec.get("component") or task_id),
        fresh_observation=bool(evidence),
        verified_success=str(rec.get("learning_status")) == "VERIFIED",
        planner_principal=planner,
        verifier_principal=str(v0.get("principal_id") or ""),
        verifier_independence_class=str(v0.get("independence_class") or "same_run"),
        environment_fingerprint=str(rec.get("environment") or (evidence[0].get("environment") if evidence else "") or ""),
        model_version=str(rec.get("model") or ""),
        self_reported_only=not evidence,
        contains_hidden_cot=any(k in rec for k in ("chain_of_thought", "hidden_reasoning", "reasoning_trace")),
        false_success=bool(rec.get("false_success")),
        security_regression=bool(rec.get("security_regression")),
    )


def _read_episodes(path: Path) -> list[Episode]:
    if not path.exists():
        return []
    out: list[Episode] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            out.append(Episode(**{k: v for k, v in d.items() if k in Episode.__dataclass_fields__}))
        except (json.JSONDecodeError, TypeError):
            continue                       # corrupt tail is not evidence
    return out


def measured_baseline(history: list[Episode]) -> float | None:
    """Measured baseline VerifiedSuccess of the EARLIER episodes of the class; None until
    there are enough of them (INSUFFICIENT_EVIDENCE stays honest)."""
    if len(history) < MIN_BASELINE_EPISODES:
        return None
    return round(sum(1 for e in history if e.verified_success) / len(history), 4)


def observe_learning_record(rec: dict, *, episodes_path: Path | None = None) -> dict | None:
    """Runtime entry point: called after a learning record is stored. OFF flag → None."""
    if not trainer_enabled():
        return None
    lt = _learning()
    path = episodes_path or default_episodes_path()
    if lt is None or path is None:
        return None
    ep = episode_from_learning_record(rec)
    if ep is None:
        return None
    prior = [e for e in _read_episodes(path) if e.action_type == ep.action_type]
    store = lt.LearningStore(path.parent)
    store._append_atomic(path, lt.redact_obj(asdict(ep)))            # sanitized, atomic, same store mechanics
    cand = AutonomyCandidate(candidate_id=f"class:{ep.action_type}", kind="context",
                             scope={"task_class": ep.action_type, "risky": False},
                             hypothesis=f"verified episodes of {ep.action_type} generalise into a reusable method",
                             rollback_ref=f"learning_guard.autonomy_trainer.rollback_candidate:{path.name}")
    evaluated = evaluate_candidate(cand, prior + [ep], baseline_success=measured_baseline(prior))
    out = record_candidate(evaluated)
    if out is not None:
        out["reasons"] = list(evaluated.reasons)
        out["baseline_episodes"] = len(prior)
        out["flag"] = TRAINER_FLAG
    return out


# ---------------------------------------------------------------- local cognitive reuse
class ReuseGate:
    """Serves local reuse per task class only after a recorded same-model A/B passed
    ``allow_local_cognitive_reuse``. Without a verdict: refuse (fresh work wins)."""

    def __init__(self, decide: Callable[..., tuple[bool, str]] | None = None) -> None:
        self._outcomes: dict[str, Any] = {}
        self._decide = decide

    def record_ab(self, task_class: str, outcome: Any) -> None:
        self._outcomes[task_class] = outcome

    def verdict(self, task_class: str) -> tuple[bool, str]:
        o = self._outcomes.get(task_class)
        if o is None:
            return False, "no same-model A/B recorded for this task class"
        decide = self._decide
        if decide is None:
            try:
                from bossman_shared.cache_intelligence import allow_local_cognitive_reuse as decide  # noqa: WPS433
            except Exception:  # noqa: BLE001
                return False, "reuse contract unavailable"
        return decide(o)


_GATE: ReuseGate | None = None


def default_reuse_gate() -> ReuseGate:
    global _GATE
    if _GATE is None:
        _GATE = ReuseGate()
    return _GATE


def reuse_allowed(task_class: str) -> tuple[bool, str]:
    """Called by ExecutionCache.get while the experiment flag is on."""
    return default_reuse_gate().verdict(task_class)
