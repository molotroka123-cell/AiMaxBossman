"""
pattern_miner.py — Self-Learning Orchestrator Layer 3
Mines recurring failure patterns from traces + evals.
Only promotes patterns with evidence >= MIN_SUPPORT runs.
"""
from __future__ import annotations
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

MIN_SUPPORT = 3  # minimum occurrences before a pattern is considered real


@dataclass
class FailurePattern:
    pattern_id: str
    description: str
    failure_type: str          # tool_error | empty_output | latency | cost | hallucination | custom
    agent_id: str
    occurrences: int
    example_run_ids: list[str] = field(default_factory=list)
    avg_score: float = 0.0
    confidence: float = 0.0    # occurrences / total_runs (capped at 1.0)
    suggested_fix_category: str = ""  # prompt | memory | tool_policy | workflow | skill_code


class PatternMiner:
    """
    Usage:
        miner = PatternMiner()
        patterns = miner.mine(traces, eval_results)
    """

    def mine(
        self,
        traces: list[dict],
        eval_results: list[dict],
        agent_id: str = "",
    ) -> list[FailurePattern]:
        """
        traces: list of dicts with keys matching TraceEvent fields
        eval_results: list of dicts with keys matching EvalResult fields
        Returns patterns with occurrences >= MIN_SUPPORT.
        """
        total_runs = len({t.get("run_id") for t in traces})
        buckets: dict[str, list[dict]] = defaultdict(list)

        # ── tool error patterns ──
        for t in traces:
            if t.get("event_type") == "error" or t.get("error"):
                tool = t.get("tool_name") or "unknown"
                key = f"tool_error::{tool}"
                buckets[key].append(t)

        # ── empty output patterns ──
        for ev in eval_results:
            rules = ev.get("rules") or []
            if isinstance(rules, str):
                try:
                    rules = json.loads(rules)
                except Exception:
                    rules = []
            for r in rules:
                if isinstance(r, dict) and not r.get("passed") and r.get("rule_id"):
                    key = f"rule_fail::{r['rule_id']}"
                    buckets[key].append({"run_id": ev.get("run_id"), "agent_id": ev.get("agent_id"), "rule": r})

        # ── promote patterns meeting MIN_SUPPORT ──
        patterns: list[FailurePattern] = []
        for key, items in buckets.items():
            if len(items) < MIN_SUPPORT:
                continue

            run_ids = list({i.get("run_id", "") for i in items})[:5]
            failure_type, detail = key.split("::", 1) if "::" in key else (key, key)

            patterns.append(FailurePattern(
                pattern_id=key,
                description=self._describe(failure_type, detail),
                failure_type=failure_type,
                agent_id=agent_id,
                occurrences=len(items),
                example_run_ids=run_ids,
                confidence=min(1.0, len(items) / max(total_runs, 1)),
                suggested_fix_category=self._suggest_category(failure_type),
            ))

        return sorted(patterns, key=lambda p: p.occurrences, reverse=True)

    # ──────────────────────────────────────────── helpers ──

    @staticmethod
    def _describe(failure_type: str, detail: str) -> str:
        if failure_type == "tool_error":
            return f"Tool '{detail}' fails repeatedly across runs"
        if failure_type == "rule_fail":
            return f"Eval rule '{detail}' fails repeatedly"
        return f"Recurring failure: {detail}"

    @staticmethod
    def _suggest_category(failure_type: str) -> str:
        mapping = {
            "tool_error": "tool_policy",
            "rule_fail::no_empty_output": "prompt",
            "rule_fail::no_error": "skill_code",
            "rule_fail::latency_ok": "workflow",
            "rule_fail::cost_budget": "workflow",
            "rule_fail::no_hallucinated_urls": "prompt",
        }
        return mapping.get(failure_type, "prompt")


def mine_from_db(agent_id: str, window_hours: int = 24) -> list[FailurePattern]:
    """
    Convenience function: pull traces + evals from DB and mine.
    Falls back gracefully if DB is unavailable.
    """
    try:
        from ..db import get_db
        db = get_db()
        cutoff = __import__("time").time() - window_hours * 3600

        traces = db.execute(
            "SELECT * FROM traces WHERE agent_id = ? AND ts > ?",
            (agent_id, cutoff)
        ).fetchall()
        evals = db.execute(
            "SELECT * FROM evals WHERE agent_id = ? AND ts > ?",
            (agent_id, cutoff)
        ).fetchall()

        trace_dicts = [dict(t) for t in traces]
        eval_dicts = [dict(e) for e in evals]

        return PatternMiner().mine(trace_dicts, eval_dicts, agent_id=agent_id)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("mine_from_db failed: %s", exc)
        return []
