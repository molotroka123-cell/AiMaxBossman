"""
eval_engine.py — Self-Learning Orchestrator Layer 2
Evaluation engine: rule-based checks + optional LLM-judge scoring.
Produces structured EvalResult objects written to the evals table.
"""
from __future__ import annotations
import json
import re
import uuid
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

try:
    from ..db import get_db
except ImportError:
    get_db = None


# ──────────────────────────────────────────────────────────── data models ──

@dataclass
class RuleResult:
    rule_id: str
    passed: bool
    score: float          # 0.0 – 1.0
    reason: str = ""


@dataclass
class EvalResult:
    eval_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    agent_id: str = ""
    overall_pass: bool = False
    score: float = 0.0
    rules: list[RuleResult] = field(default_factory=list)
    judge_verdict: Optional[str] = None
    judge_score: Optional[float] = None
    ts: float = field(default_factory=time.time)
    meta: dict = field(default_factory=dict)


# ──────────────────────────────────────────────────────────── rule registry ──

RuleFunc = Callable[[dict], RuleResult]
_RULE_REGISTRY: dict[str, RuleFunc] = {}


def register_rule(rule_id: str):
    """Decorator to add a rule function to the global registry."""
    def decorator(fn: RuleFunc) -> RuleFunc:
        _RULE_REGISTRY[rule_id] = fn
        return fn
    return decorator


# ──────────────────────────────────────────── built-in rules ──

@register_rule("no_empty_output")
def rule_no_empty_output(ctx: dict) -> RuleResult:
    output = str(ctx.get("output", "")).strip()
    passed = len(output) > 0
    return RuleResult("no_empty_output", passed, 1.0 if passed else 0.0,
                      reason="" if passed else "Output is empty")


@register_rule("no_error")
def rule_no_error(ctx: dict) -> RuleResult:
    errors = ctx.get("errors", [])
    passed = len(errors) == 0
    return RuleResult("no_error", passed, 1.0 if passed else 0.0,
                      reason="" if passed else f"Errors: {errors}")


@register_rule("tool_success_rate")
def rule_tool_success_rate(ctx: dict) -> RuleResult:
    calls = ctx.get("tool_calls", [])
    if not calls:
        return RuleResult("tool_success_rate", True, 1.0, reason="No tool calls")
    ok = sum(1 for c in calls if not c.get("error"))
    rate = ok / len(calls)
    passed = rate >= 0.8
    return RuleResult("tool_success_rate", passed, rate,
                      reason=f"{ok}/{len(calls)} tool calls succeeded")


@register_rule("no_hallucinated_urls")
def rule_no_hallucinated_urls(ctx: dict) -> RuleResult:
    output = str(ctx.get("output", ""))
    urls = re.findall(r'https?://\S+', output)
    suspicious = [u for u in urls if "example.com" in u or "placeholder" in u]
    passed = len(suspicious) == 0
    return RuleResult(
        "no_hallucinated_urls", passed,
        1.0 if passed else 0.0,
        reason=f"Suspicious URLs: {suspicious}" if suspicious else ""
    )


@register_rule("latency_ok")
def rule_latency_ok(ctx: dict) -> RuleResult:
    latency = ctx.get("total_latency_ms", 0)
    limit = ctx.get("latency_limit_ms", 30_000)
    passed = latency <= limit
    return RuleResult("latency_ok", passed, 1.0 if passed else max(0.0, 1 - (latency - limit) / limit),
                      reason=f"{latency:.0f}ms vs limit {limit}ms")


@register_rule("cost_budget")
def rule_cost_budget(ctx: dict) -> RuleResult:
    cost = ctx.get("total_cost_usd", 0)
    budget = ctx.get("cost_budget_usd", 0.10)
    passed = cost <= budget
    return RuleResult("cost_budget", passed, 1.0 if passed else 0.0,
                      reason=f"${cost:.4f} vs budget ${budget:.4f}")


# ──────────────────────────────────────────────────────────── engine ──

class EvalEngine:
    """
    Usage:
        engine = EvalEngine(rules=["no_empty_output", "no_error", "tool_success_rate"])
        result = engine.evaluate(run_id, agent_id, ctx)
    """

    def __init__(
        self,
        rules: list[str] | None = None,
        pass_threshold: float = 0.7,
        judge_fn: Callable[[dict], tuple[str, float]] | None = None,
    ):
        self.rules = rules or list(_RULE_REGISTRY.keys())
        self.pass_threshold = pass_threshold
        self.judge_fn = judge_fn  # optional: (ctx) -> (verdict_str, score_0_to_1)

    def evaluate(self, run_id: str, agent_id: str, ctx: dict) -> EvalResult:
        """Run all configured rules against ctx, optionally call judge_fn."""
        rule_results: list[RuleResult] = []
        for rule_id in self.rules:
            fn = _RULE_REGISTRY.get(rule_id)
            if fn:
                try:
                    rule_results.append(fn(ctx))
                except Exception as exc:
                    rule_results.append(RuleResult(rule_id, False, 0.0, reason=f"Rule error: {exc}"))

        avg_score = sum(r.score for r in rule_results) / max(len(rule_results), 1)
        overall_pass = avg_score >= self.pass_threshold

        judge_verdict, judge_score = None, None
        if self.judge_fn:
            try:
                judge_verdict, judge_score = self.judge_fn(ctx)
                # Blend judge score (40%) with rule score (60%)
                avg_score = 0.6 * avg_score + 0.4 * judge_score
                overall_pass = avg_score >= self.pass_threshold
            except Exception as exc:
                judge_verdict = f"judge_error: {exc}"

        result = EvalResult(
            run_id=run_id,
            agent_id=agent_id,
            overall_pass=overall_pass,
            score=avg_score,
            rules=rule_results,
            judge_verdict=judge_verdict,
            judge_score=judge_score,
        )
        self._persist(result)
        return result

    def _persist(self, result: EvalResult) -> None:
        if get_db is None:
            return
        try:
            db = get_db()
            db.execute(
                """
                INSERT INTO evals (
                    eval_id, run_id, agent_id, overall_pass, score,
                    rules_json, judge_verdict, judge_score, ts
                ) VALUES (
                    :eval_id, :run_id, :agent_id, :overall_pass, :score,
                    :rules_json, :judge_verdict, :judge_score, :ts
                )
                """,
                {
                    "eval_id": result.eval_id,
                    "run_id": result.run_id,
                    "agent_id": result.agent_id,
                    "overall_pass": result.overall_pass,
                    "score": result.score,
                    "rules_json": json.dumps([vars(r) for r in result.rules]),
                    "judge_verdict": result.judge_verdict,
                    "judge_score": result.judge_score,
                    "ts": result.ts,
                },
            )
            db.commit()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("EvalEngine persist failed: %s", exc)
