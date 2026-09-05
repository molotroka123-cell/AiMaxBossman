from __future__ import annotations

import json
from pathlib import Path

from .models import BenchmarkReport


def write_reports(report: BenchmarkReport, output_dir: str | Path) -> tuple[Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    jp, mp = out / "benchmark-report.json", out / "benchmark-report.md"
    data = report.to_dict()
    jp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    a = report.aggregate
    lines = ["# Bossman Benchmark Report (passive overlay)", "",
             f"- Git SHA: `{report.git_sha}`", f"- Mode: `{report.mode}`",
             f"- Missions: {a.get('mission_count', 0)}, verified: {a.get('verified_success_count', 0)}",
             f"- Hard failures: `{', '.join(a.get('hard_failures') or []) or 'none'}`",
             f"- Secondary aggregate (not authoritative): {a.get('total_score_secondary', 0)}/100", ""]
    for key in ("cost_per_verified_success", "tokens_per_verified_success", "gpu_seconds_per_verified_success",
                "human_interrupts_per_verified_mission", "false_success_rate", "recovery_success_rate",
                "team_overhead_ratio", "model_escalation_rate", "local_execution_rate", "cloud_avoidance_rate",
                "token_value_metric"):
        v = a.get(key)
        lines.append(f"- {key}: {'N/A' if v is None else v}")
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jp, mp
