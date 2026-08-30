"""
candidate_generator.py — Self-Learning Orchestrator Layer 4
Generates improvement candidate proposals from failure patterns.
All candidates require validation before promotion — nothing auto-applies.
"""
from __future__ import annotations
import uuid
import time
import json
from dataclasses import dataclass, field
from typing import Optional

from .pattern_miner import FailurePattern

try:
    from ..db import get_db
except ImportError:
    get_db = None


# ──────────────────────────────────────────── gate policies ──

GATE_POLICY: dict[str, str] = {
    "prompt": "AUTO",         # can apply automatically after validation
    "memory": "AUTO",
    "tool_policy": "ASK",     # requires human/dashboard approval
    "workflow": "ASK",
    "skill_code": "DENY",     # always creates a ticket — never auto-applies
}


@dataclass
class ImprovementCandidate:
    candidate_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str = ""
    pattern_id: str = ""
    category: str = ""        # prompt | memory | tool_policy | workflow | skill_code
    gate: str = "ASK"         # AUTO | ASK | DENY
    title: str = ""
    description: str = ""
    diff_hint: str = ""        # human-readable diff suggestion
    confidence: float = 0.0
    status: str = "pending"   # pending | validated | approved | rejected | applied
    ts: float = field(default_factory=time.time)
    meta: dict = field(default_factory=dict)


class CandidateGenerator:
    """
    Generates one candidate per pattern.
    Candidates are never applied here — only stored for validation.

    Usage:
        gen = CandidateGenerator()
        candidates = gen.generate(patterns, agent_id="my-agent")
    """

    def generate(
        self,
        patterns: list[FailurePattern],
        agent_id: str = "",
    ) -> list[ImprovementCandidate]:
        candidates = []
        for pattern in patterns:
            candidate = self._build(pattern, agent_id)
            if candidate:
                candidates.append(candidate)
                self._persist(candidate)
        return candidates

    def _build(
        self, pattern: FailurePattern, agent_id: str
    ) -> Optional[ImprovementCandidate]:
        cat = pattern.suggested_fix_category or "prompt"
        gate = GATE_POLICY.get(cat, "ASK")

        title, description, diff_hint = self._craft_suggestion(pattern, cat)

        return ImprovementCandidate(
            agent_id=agent_id,
            pattern_id=pattern.pattern_id,
            category=cat,
            gate=gate,
            title=title,
            description=description,
            diff_hint=diff_hint,
            confidence=pattern.confidence,
            meta={
                "occurrences": pattern.occurrences,
                "example_run_ids": pattern.example_run_ids,
            },
        )

    @staticmethod
    def _craft_suggestion(
        pattern: FailurePattern, category: str
    ) -> tuple[str, str, str]:
        """Returns (title, description, diff_hint) for the candidate."""

        if category == "prompt":
            title = f"Prompt patch: fix {pattern.failure_type}"
            description = (
                f"Add an explicit instruction to the system prompt addressing "
                f"'{pattern.description}' (seen {pattern.occurrences}× in traces)."
            )
            diff_hint = (
                f"# system_prompt addition\n"
                f"+ When you encounter {pattern.failure_type}, always "
                f"[insert corrective instruction here]."
            )

        elif category == "memory":
            title = f"Memory rule: record {pattern.failure_type} lesson"
            description = (
                f"Insert a structured learning into the agent's long-term memory: "
                f"'{pattern.description}'."
            )
            diff_hint = (
                f"memory.insert(type='policy', "
                f"content='For {pattern.failure_type}: [corrective action]')"
            )

        elif category == "tool_policy":
            title = f"Tool policy: restrict/fix {pattern.pattern_id.split('::')[-1]}"
            description = (
                f"Update tool availability or retry policy for "
                f"'{pattern.pattern_id.split('::')[-1]}' (failing {pattern.occurrences}×)."
            )
            diff_hint = (
                f"agent.tool_policy['{pattern.pattern_id.split('::')[-1]}'] = "
                f"{{max_retries: 2, fallback: None}}"
            )

        elif category == "workflow":
            title = f"Workflow change: address {pattern.failure_type}"
            description = (
                f"Restructure task graph step related to '{pattern.description}'."
            )
            diff_hint = (
                f"# workflow graph patch\n"
                f"- step: {pattern.failure_type}\n"
                f"+ step: {pattern.failure_type} (with guard/retry/timeout)"
            )

        else:  # skill_code — always DENY → ticket
            title = f"[TICKET] Skill code fix: {pattern.failure_type}"
            description = (
                f"Code-level fix required for recurring failure "
                f"'{pattern.description}' ({pattern.occurrences}× occurrences). "
                f"Requires developer review — not auto-applied."
            )
            diff_hint = f"# TODO: investigate {pattern.pattern_id} and patch skill code"

        return title, description, diff_hint

    def _persist(self, candidate: ImprovementCandidate) -> None:
        if get_db is None:
            return
        try:
            db = get_db()
            db.execute(
                """
                INSERT INTO candidates (
                    candidate_id, agent_id, pattern_id, category, gate,
                    title, description, diff_hint, confidence, status, ts, meta
                ) VALUES (
                    :candidate_id, :agent_id, :pattern_id, :category, :gate,
                    :title, :description, :diff_hint, :confidence, :status, :ts, :meta
                )
                """,
                {
                    **candidate.__dict__,
                    "meta": json.dumps(candidate.meta),
                },
            )
            db.commit()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("CandidateGenerator persist failed: %s", exc)
