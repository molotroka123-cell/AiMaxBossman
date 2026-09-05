from __future__ import annotations

from statistics import mean

from .hard_fail import HardFailGate
from .models import BenchmarkEvent, BenchmarkPolicy, BenchmarkReport, MissionScore

DIMS = (
    "mission_understanding", "organization_quality", "fleet_placement", "model_selection", "action_execution",
    "verification_truth", "recovery_idempotency", "context_continuity", "resource_efficiency", "autonomy_friction",
)


class BenchmarkScorer:
    def __init__(self, policy: BenchmarkPolicy | None = None) -> None:
        self.policy = policy or BenchmarkPolicy()
        self.gate = HardFailGate(self.policy)

    def score_mission(self, mission_id: str, events: list[BenchmarkEvent]) -> MissionScore:
        kinds: dict[str, list[BenchmarkEvent]] = {}
        for e in events:
            kinds.setdefault(e.kind, []).append(e)
        hard = self.gate.evaluate(events)

        def has(k, key=None, val=None):
            xs = kinds.get(k, [])
            return bool(xs) if key is None else any(x.data.get(key) == val for x in xs)

        placed = kinds.get("fleet.placed", [])
        scores = {
            "mission_understanding": 10.0 if has("mission.interpreted", "constraints_preserved", True) else 0.0,
            "organization_quality": 10.0 if has("organization.selected", "team_fit", "good") else 0.0,
            "fleet_placement": (10.0 if all(x.data.get("placement_fit") == "good" for x in placed) else 0.0) if placed else 5.0,
            "model_selection": 10.0 if has("model.selected", "model_fit", "good") else (5.0 if not has("model.selected") else 0.0),
            "action_execution": 10.0 if has("side_effect.executed") else 0.0,
            "verification_truth": 10.0 if has("verification.completed", "verified", True)
            and not has("verification.completed", "verified", False) else 0.0,
            "recovery_idempotency": 0.0 if "duplicate_side_effect" in hard else 10.0,
            "context_continuity": 10.0 if (not has("context.handoff") or has("context.handoff", "constraints_retained", True)) else 0.0,
            "resource_efficiency": self._resource_score(kinds.get("resource.usage", [])),
            "autonomy_friction": self._autonomy_score(kinds.get("approval.requested", [])),
        }
        verified = has("verification.completed", "verified", True) and not hard \
            and not has("verification.completed", "verified", False)
        return MissionScore(mission_id, scores, hard, verified, self._metrics(events, verified))

    def score_report(self, version: str, git_sha: str, mode: str, grouped: dict[str, list[BenchmarkEvent]],
                     metadata: dict | None = None) -> BenchmarkReport:
        ms = [self.score_mission(mid, evs) for mid, evs in sorted(grouped.items())]
        hard = sorted({x for m in ms for x in m.hard_failures})
        verified = sum(1 for m in ms if m.verified_success)
        n = len(ms)
        cost = sum(m.metrics["cost_usd"] for m in ms)
        tokens = sum(m.metrics["tokens"] for m in ms)
        gpu = sum(m.metrics["gpu_seconds"] for m in ms)
        hi = sum(m.metrics["human_interruptions"] for m in ms)
        resumed = [m for m in ms if m.metrics.get("resume_attempted")]
        local = sum(m.metrics.get("local_executions", 0) for m in ms)
        cloud = sum(m.metrics.get("cloud_executions", 0) for m in ms)
        cloud_eligible = sum(m.metrics.get("cloud_eligible", 0) for m in ms)
        team = sum(m.metrics.get("team_members", 0) for m in ms)
        executors = sum(m.metrics.get("executors", 0) for m in ms)
        escal = sum(m.metrics.get("model_escalations", 0) for m in ms)
        attempts = sum(m.metrics.get("attempts", 0) for m in ms)

        def per(x, d):
            return round(x / d, 6) if d else None

        agg = {
            "total_score_secondary": round(mean([m.total for m in ms]), 2) if ms else 0.0,
            "hard_failures": hard,
            "mission_count": n, "verified_success_count": verified,
            "false_success_count": sum("false_success" in m.hard_failures for m in ms),
            "duplicate_side_effect_count": sum("duplicate_side_effect" in m.hard_failures for m in ms),
            "privacy_violation_count": sum("privacy_violation" in m.hard_failures for m in ms),
            "permission_bypass_count": sum("permission_bypass" in m.hard_failures for m in ms),
            "scope_leak_count": sum("scope_leak" in m.hard_failures for m in ms),
            "review_bypass_count": sum("review_bypass" in m.hard_failures for m in ms),
            "cost_per_verified_success": per(cost, verified),
            "tokens_per_verified_success": per(tokens, verified),
            "gpu_seconds_per_verified_success": per(gpu, verified),
            "human_interrupts_per_verified_mission": per(hi, verified),
            "false_success_rate": per(sum("false_success" in m.hard_failures for m in ms), n),
            "recovery_success_rate": per(sum(1 for m in resumed if m.verified_success), len(resumed)),
            "team_overhead_ratio": per(team - executors, executors) if executors else None,
            "model_escalation_rate": per(escal, attempts),
            "local_execution_rate": per(local, local + cloud),
            "cloud_avoidance_rate": per(cloud_eligible - cloud, cloud_eligible) if cloud_eligible else None,
            "token_value_metric": self.token_value_metric(
                quality=(mean([m.total for m in ms]) / 100.0) if ms else 0.0,
                reliability=(verified / n) if n else 0.0, cost=cost),
        }
        return BenchmarkReport(version, git_sha, mode, ms, agg, metadata or {})

    @staticmethod
    def token_value_metric(*, quality: float, reliability: float, cost: float) -> float | None:
        """Quality × Reliability / Cost; при нулевой стоимости — None, не деление на ноль."""
        if cost <= 0:
            return None
        return round((quality * reliability) / cost, 6)

    def _resource_score(self, xs):
        if not xs:
            return 5.0
        penalty = sum(min(float(x.data.get("unnecessary_cloud_calls", 0)) * 2, 4)
                      + min(float(x.data.get("unnecessary_retries", 0)), 3)
                      + min(float(x.data.get("team_overhead_ratio", 0)) * 2, 3) for x in xs)
        return max(0.0, min(10.0, 10.0 - penalty))

    def _autonomy_score(self, xs):
        unnecessary = sum(int(bool(x.data.get("unnecessary", False))) for x in xs)
        return max(0.0, 10.0 - unnecessary * 2.0)

    def _metrics(self, events, verified):
        m: dict[str, float] = {"cost_usd": 0.0, "tokens": 0.0, "gpu_seconds": 0.0, "human_interruptions": 0.0,
                               "local_executions": 0.0, "cloud_executions": 0.0, "cloud_eligible": 0.0,
                               "team_members": 0.0, "executors": 0.0, "model_escalations": 0.0, "attempts": 0.0,
                               "resume_attempted": 0.0}
        for e in events:
            d = e.data
            if e.kind == "resource.usage":
                m["cost_usd"] += float(d.get("cost_usd", 0)); m["tokens"] += float(d.get("tokens", 0))
                m["gpu_seconds"] += float(d.get("gpu_seconds", 0))
            elif e.kind == "approval.requested":
                m["human_interruptions"] += 1.0
            elif e.kind == "fleet.placed":
                m["cloud_executions" if d.get("node_class") == "cloud" else "local_executions"] += 1.0
                m["cloud_eligible"] += 1.0 if d.get("cloud_eligible") else 0.0
            elif e.kind == "organization.selected":
                m["team_members"] += float(d.get("team_size", 0)); m["executors"] += float(d.get("executors", 0))
                m["attempts"] += float(d.get("attempts", 0)); m["model_escalations"] += float(d.get("escalations", 0))
            elif e.kind == "context.handoff":
                m["resume_attempted"] = 1.0
        m["verified_success"] = 1.0 if verified else 0.0
        return m
