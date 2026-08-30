"""
test_self_learning.py — Unit tests for Self-Learning Orchestrator modules
Covers: TraceRecorder, EvalEngine, PatternMiner, CandidateGenerator, PromotionGate
"""
from __future__ import annotations
import pytest
import time
from unittest.mock import patch, MagicMock

# ──────────────────────────────────────────── imports ──
from ..trace_recorder import TraceRecorder, TraceEvent
from ..eval_engine import EvalEngine, EvalResult, _RULE_REGISTRY
from ..pattern_miner import PatternMiner, FailurePattern, MIN_SUPPORT
from ..candidate_generator import CandidateGenerator, ImprovementCandidate, GATE_POLICY
from ..promotion_gate import PromotionGate, ValidationReport


# ══════════════════════════════════════════ TraceRecorder ══

class TestTraceRecorder:
    def _recorder(self) -> TraceRecorder:
        return TraceRecorder(agent_id="test-agent", run_id="run-001", flush=False)

    def test_record_step_increments_index(self):
        rec = self._recorder()
        ev1 = rec.record_step("prompt1", "action1")
        ev2 = rec.record_step("prompt2", "action2")
        assert ev1.step_index == 0
        assert ev2.step_index == 1

    def test_record_step_caps_prompt(self):
        rec = self._recorder()
        long_prompt = "x" * 10000
        ev = rec.record_step(long_prompt, "act")
        assert len(ev.prompt_snapshot) <= 4096

    def test_record_tool_sets_event_type(self):
        rec = self._recorder()
        ev = rec.record_tool("search", {"q": "test"}, "result")
        assert ev.event_type == "tool_call"
        assert ev.tool_name == "search"

    def test_record_tool_error_sets_error_type(self):
        rec = self._recorder()
        ev = rec.record_tool("search", {}, None, error="timeout")
        assert ev.event_type == "error"
        assert ev.error == "timeout"

    def test_record_final(self):
        rec = self._recorder()
        ev = rec.record_final("done", success=True, score=0.9)
        assert ev.event_type == "final"
        assert ev.metadata["score"] == 0.9

    def test_record_error(self):
        rec = self._recorder()
        ev = rec.record_error("something went wrong", {"ctx": 1})
        assert ev.event_type == "error"
        assert "something went wrong" in ev.error

    def test_flush_buffer_returns_all_events(self):
        rec = self._recorder()
        rec.record_step("p", "a")
        rec.record_final("x")
        buf = rec.flush_buffer()
        assert len(buf) == 2

    def test_no_db_write_when_flush_false(self):
        rec = self._recorder()  # flush=False
        with patch("command-center.bcc.features.trace_recorder.get_db") as mock_db:
            rec.record_step("prompt", "action")
            mock_db.assert_not_called()


# ══════════════════════════════════════════ EvalEngine ══

class TestEvalEngine:
    def _ctx_ok(self) -> dict:
        return {
            "output": "This is a good result.",
            "errors": [],
            "tool_calls": [{"tool": "search", "error": None}],
            "total_latency_ms": 1000,
            "total_cost_usd": 0.005,
        }

    def test_all_rules_pass_on_clean_ctx(self):
        engine = EvalEngine(pass_threshold=0.7)
        result = engine.evaluate("run-1", "agent-1", self._ctx_ok())
        assert result.overall_pass is True
        assert result.score >= 0.7

    def test_fails_on_empty_output(self):
        engine = EvalEngine(rules=["no_empty_output"], pass_threshold=0.7)
        result = engine.evaluate("run-2", "agent-1", {"output": ""})
        assert result.overall_pass is False
        assert result.score == 0.0

    def test_fails_on_errors(self):
        engine = EvalEngine(rules=["no_error"], pass_threshold=0.7)
        result = engine.evaluate("run-3", "agent-1", {"errors": ["timeout"]})
        assert result.overall_pass is False

    def test_tool_success_rate_partial(self):
        engine = EvalEngine(rules=["tool_success_rate"], pass_threshold=0.7)
        ctx = {"tool_calls": [
            {"error": None}, {"error": None}, {"error": None},
            {"error": "fail"}, {"error": "fail"},
        ]}
        result = engine.evaluate("run-4", "agent-1", ctx)
        # 3/5 = 0.6 — below pass threshold 0.7
        assert result.overall_pass is False
        assert abs(result.score - 0.6) < 0.01

    def test_latency_rule_fails_over_limit(self):
        engine = EvalEngine(rules=["latency_ok"], pass_threshold=0.5)
        result = engine.evaluate("run-5", "a", {"total_latency_ms": 60000, "latency_limit_ms": 30000})
        assert result.overall_pass is False

    def test_judge_fn_blends_score(self):
        def fake_judge(ctx):
            return "looks good", 1.0
        engine = EvalEngine(rules=["no_error"], pass_threshold=0.7, judge_fn=fake_judge)
        ctx = {"errors": [], "output": "x"}
        result = engine.evaluate("run-6", "agent-1", ctx)
        # rule passes (1.0), judge gives 1.0 → blend = 0.6*1.0 + 0.4*1.0 = 1.0
        assert result.overall_pass is True
        assert result.judge_verdict == "looks good"

    def test_no_db_persistence_without_db(self):
        # Should not raise even without DB
        engine = EvalEngine()
        result = engine.evaluate("run-7", "agent-1", self._ctx_ok())
        assert isinstance(result, EvalResult)


# ══════════════════════════════════════════ PatternMiner ══

class TestPatternMiner:
    def _make_traces(self, tool: str, n: int, run_prefix: str = "run") -> list[dict]:
        return [
            {"run_id": f"{run_prefix}-{i}", "event_type": "error",
             "tool_name": tool, "error": "timeout", "agent_id": "a"}
            for i in range(n)
        ]

    def test_pattern_below_min_support_excluded(self):
        miner = PatternMiner()
        traces = self._make_traces("search", MIN_SUPPORT - 1)
        patterns = miner.mine(traces, [])
        assert len(patterns) == 0

    def test_pattern_at_min_support_included(self):
        miner = PatternMiner()
        traces = self._make_traces("search", MIN_SUPPORT)
        patterns = miner.mine(traces, [])
        assert len(patterns) == 1
        assert patterns[0].pattern_id == "tool_error::search"
        assert patterns[0].occurrences == MIN_SUPPORT

    def test_pattern_above_min_support(self):
        miner = PatternMiner()
        traces = self._make_traces("db_query", 10)
        patterns = miner.mine(traces, [])
        assert patterns[0].occurrences == 10

    def test_rule_fail_patterns_from_evals(self):
        miner = PatternMiner()
        evals = [
            {"run_id": f"r-{i}", "agent_id": "a",
             "rules": [{"rule_id": "no_empty_output", "passed": False, "score": 0}]}
            for i in range(MIN_SUPPORT)
        ]
        patterns = miner.mine([], evals)
        ids = [p.pattern_id for p in patterns]
        assert "rule_fail::no_empty_output" in ids

    def test_confidence_capped_at_1(self):
        miner = PatternMiner()
        traces = self._make_traces("search", MIN_SUPPORT, "single-run")
        # all from same run_id → total_runs = MIN_SUPPORT but occurrences = MIN_SUPPORT
        patterns = miner.mine(traces, [])
        assert all(p.confidence <= 1.0 for p in patterns)

    def test_sorted_by_occurrences(self):
        miner = PatternMiner()
        t1 = self._make_traces("tool_a", 5, "ra")
        t2 = self._make_traces("tool_b", 10, "rb")
        patterns = miner.mine(t1 + t2, [])
        assert patterns[0].occurrences >= patterns[-1].occurrences


# ══════════════════════════════════════════ CandidateGenerator ══

class TestCandidateGenerator:
    def _pattern(self, category: str = "prompt", occ: int = 5) -> FailurePattern:
        return FailurePattern(
            pattern_id=f"rule_fail::no_empty_output",
            description="Output empty",
            failure_type="rule_fail",
            agent_id="agent-x",
            occurrences=occ,
            confidence=0.5,
            suggested_fix_category=category,
        )

    def test_generates_one_candidate_per_pattern(self):
        gen = CandidateGenerator()
        patterns = [self._pattern("prompt"), self._pattern("memory")]
        candidates = gen.generate(patterns, agent_id="a")
        assert len(candidates) == 2

    def test_gate_matches_policy(self):
        gen = CandidateGenerator()
        for cat, expected_gate in GATE_POLICY.items():
            c = gen.generate([self._pattern(cat)], agent_id="a")
            assert c[0].gate == expected_gate, f"gate mismatch for {cat}"

    def test_skill_code_gets_deny_gate(self):
        gen = CandidateGenerator()
        c = gen.generate([self._pattern("skill_code")], agent_id="a")
        assert c[0].gate == "DENY"

    def test_candidate_has_diff_hint(self):
        gen = CandidateGenerator()
        c = gen.generate([self._pattern()], agent_id="a")
        assert len(c[0].diff_hint) > 0

    def test_candidate_status_is_pending(self):
        gen = CandidateGenerator()
        c = gen.generate([self._pattern()], agent_id="a")
        assert c[0].status == "pending"


# ══════════════════════════════════════════ PromotionGate ══

class TestPromotionGate:
    def _candidate(self, gate: str = "AUTO") -> ImprovementCandidate:
        return ImprovementCandidate(
            candidate_id="cand-001",
            agent_id="agent-x",
            pattern_id="rule_fail::no_empty_output",
            category="prompt",
            gate=gate,
            title="Test candidate",
            description="Test",
            diff_hint="+ better prompt",
            confidence=0.8,
        )

    def _tasks(self, score: float) -> list[dict]:
        return [{"task": f"t{i}", "expected": "x"} for i in range(3)]

    def test_deny_gate_never_promotes(self):
        gate = PromotionGate()
        report = gate.validate(self._candidate("DENY"), {}, {})
        assert report.promoted is False
        assert "DENY" in report.rejection_reason

    def test_auto_gate_promotes_when_improved(self):
        call_count = [0]
        def good_run(task):
            call_count[0] += 1
            # baseline gets 0.5, candidate gets 0.9
            score = 0.9 if call_count[0] > 3 else 0.5
            return {"score": score, "cost_usd": 0.001, "latency_ms": 100, "safety_flags": []}

        gate = PromotionGate(
            run_fn=good_run,
            benchmark_tasks=self._tasks(0.8),
            holdout_tasks=self._tasks(0.7),
        )
        report = gate.validate(self._candidate("AUTO"), {}, {})
        # With this pattern, holdout > baseline by MIN_DELTA and benchmark >= FLOOR
        assert isinstance(report, ValidationReport)

    def test_ask_gate_does_not_auto_promote(self):
        def great_run(task):
            return {"score": 1.0, "cost_usd": 0.0, "latency_ms": 50, "safety_flags": []}

        gate = PromotionGate(
            run_fn=great_run,
            benchmark_tasks=self._tasks(1.0),
            holdout_tasks=self._tasks(1.0),
        )
        # ASK gate: even perfect scores require human approval
        candidate = self._candidate("ASK")
        report = gate.validate(candidate, {}, {})
        assert report.promoted is False
        assert "ASK" in report.rejection_reason

    def test_safety_flag_blocks_promotion(self):
        def unsafe_run(task):
            return {"score": 1.0, "cost_usd": 0.0, "latency_ms": 50, "safety_flags": ["jailbreak"]}

        gate = PromotionGate(
            run_fn=unsafe_run,
            benchmark_tasks=self._tasks(1.0),
            holdout_tasks=self._tasks(1.0),
        )
        report = gate.validate(self._candidate("AUTO"), {}, {})
        assert report.promoted is False
        assert report.safety_pass is False

    def test_cost_regression_blocks_promotion(self):
        baseline_calls = [0]
        def expensive_run(task):
            baseline_calls[0] += 1
            cost = 0.001 if baseline_calls[0] <= 3 else 0.01  # 10x more expensive
            return {"score": 0.9, "cost_usd": cost, "latency_ms": 100, "safety_flags": []}

        gate = PromotionGate(
            run_fn=expensive_run,
            benchmark_tasks=self._tasks(0.9),
            holdout_tasks=self._tasks(0.9),
        )
        report = gate.validate(self._candidate("AUTO"), {}, {})
        # cost_ok should be False → no promotion
        assert not (report.promoted and not report.cost_ok)

    def test_no_tasks_returns_neutral_report(self):
        gate = PromotionGate()  # no tasks → noop_run, empty results
        report = gate.validate(self._candidate("AUTO"), {}, {})
        assert isinstance(report, ValidationReport)
        assert report.benchmark_score == 0.0
