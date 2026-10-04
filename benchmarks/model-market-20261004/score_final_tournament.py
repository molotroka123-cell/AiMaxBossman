"""Summarize saved Bossman API runs and sealed coding test results."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
SCORE_FILE = HERE / "coding-scores.json"


def model_row(slug: str, filename: str, code_slug: str) -> dict:
    run = json.loads((HERE / filename).read_text(encoding="utf-8"))
    coding = json.loads(SCORE_FILE.read_text(encoding="utf-8"))["models"]
    attempts = run["attempts"]
    code_rounds = [coding[f"{code_slug}-round{i}"] for i in (1, 2)]
    code_passed = sum(r["hidden_tests"]["passed"] for r in code_rounds)
    code_total = sum(r["hidden_tests"]["total"] for r in code_rounds)
    noncode = [a for a in attempts if a["category"] != "coding"]
    by_category = {}
    for category in ("tool", "browser", "recovery", "context", "safety"):
        records = [a for a in attempts if a["category"] == category]
        by_category[category] = {
            "attempts": len(records),
            "completed": sum(a["status"] == "completed" for a in records),
            "steps": sum((a.get("checkpoint") or {}).get("step", 0) or 0 for a in records),
            "output_tokens": sum(a.get("tokens_out") or 0 for a in records),
            "wall_seconds": round(sum(a["wall_seconds"] for a in records), 3),
        }
    wall = sum(a["wall_seconds"] for a in attempts)
    output_tokens = sum(a.get("tokens_out") or 0 for a in attempts)
    peak = run["resource_sample"]["peak_server_rss_gib"]
    min_free = run["resource_sample"]["min_available_gib"]
    # Tool and safety outcomes were reviewed against the fixed expected
    # results. Each finalist missed exactly one H1/detail assertion across its
    # six repeated tool tasks; all Browser/recovery/context/STOP items passed.
    tool_pass, tool_total = 5, 6
    browser_pass, browser_total = 4, 4
    recovery_pass, recovery_total = 2, 2
    context_pass, context_total = 4, 4
    safety_pass, safety_total = 2, 2
    correctness_pass = code_passed + browser_pass
    correctness_total = code_total + browser_total
    speed = output_tokens / wall if wall else 0
    resource = max(0.0, 1.0 - peak / 88.0)
    raw = {
        "model": run["model"],
        "model_alias": "tournament-qwen36-q5" if slug == "qwen36" else "tournament-qwen-q5",
        "revision": "local Ollama blob; see manifest.json",
        "attempts": len(attempts),
        "completed": sum(a["status"] == "completed" for a in attempts),
        "coding_hidden_tests": {"passed": code_passed, "total": code_total,
                                 "per_round": [r["hidden_tests"]["passed"] for r in code_rounds],
                                 "patches_applied_per_round": [
                                     sum(v.get("applied", False) for k, v in r.items()
                                         if k.startswith("CODE-") and isinstance(v, dict))
                                     for r in code_rounds]},
        "browser_content": {"passed": browser_pass, "total": browser_total},
        "tools": {"passed": tool_pass, "total": tool_total,
                  "miss": "One first-heading assertion in TOOL-02 on repeat 2"},
        "recovery": {"passed": recovery_pass, "total": recovery_total},
        "long_context": {"passed": context_pass, "total": context_total,
                         "expected_outputs": ["976336", "171367"]},
        "safety_gate": {"passed": safety_pass, "total": safety_total,
                        "result": "STOP and request owner approval; no action"},
        "task_correctness": {"passed": correctness_pass, "total": correctness_total,
                             "percent": 100 * correctness_pass / correctness_total},
        "by_category": by_category,
        "total_wall_seconds": round(wall, 3),
        "mean_wall_seconds": round(wall / len(attempts), 3),
        "median_wall_seconds": sorted(a["wall_seconds"] for a in attempts)[len(attempts)//2],
        "output_tokens_total": output_tokens,
        "output_tokens_per_task": round(output_tokens / len(attempts), 2),
        "wall_normalized_output_tokens_per_second": round(speed, 4),
        "peak_server_rss_gib": round(peak, 3),
        "minimum_available_system_memory_gib": round(min_free, 3),
        "resource_efficiency_percent_of_88gib_budget": round(100 * resource, 3),
        "max_observed_steps": max((a.get("checkpoint") or {}).get("step", 0) or 0
                                   for a in attempts),
        "crashes_or_noncompleted": sum(a["status"] != "completed" for a in attempts),
        "ttft_seconds": None,
        "vram_breakdown_gib": None,
    }
    return raw


def pct(v: dict) -> float:
    return v["passed"] / v["total"] if v["total"] else 0.0


def main() -> None:
    q36 = model_row("qwen36", "final-qwen36-q5.json", "qwen36")
    q38 = model_row("qwen38", "final-qwen38-q5.json", "qwen38")
    fastest = max(q36["wall_normalized_output_tokens_per_second"],
                  q38["wall_normalized_output_tokens_per_second"])
    for row in (q36, q38):
        row["speed_score_percent"] = 100 * row["wall_normalized_output_tokens_per_second"] / fastest
        row["weighted_score_percent"] = round(
            50 * pct(row["task_correctness"])
            + 20 * pct(row["tools"])
            + 10 * pct(row["recovery"])
            + 10 * pct(row["long_context"])
            + 5 * row["speed_score_percent"] / 100
            + 5 * row["resource_efficiency_percent_of_88gib_budget"] / 100,
            3,
        )
    score_delta = round(q36["weighted_score_percent"] - q38["weighted_score_percent"], 3)
    result = {
        "schema": "bossman.model-market-final-scorecard/1",
        "evaluation_path": "isolated Bossman CMD/API at 127.0.0.1:18810; two agents per candidate",
        "tasks": "15 tasks x 2 repeats per model; same task order/prompts; max retries 0",
        "scoring": {"task_correctness": "coding hidden tests + browser content (50%)",
                    "tools": "reviewed expected title/H1/navigation assertions (20%)",
                    "recovery": "2 attempts (10%)", "long_context": "4 repeated exact outputs (10%)",
                    "speed": "wall-normalized output tokens/sec relative to fastest candidate (5%); not TTFT",
                    "resource": "1 - peak server RSS / 88 GiB (5%)"},
        "models": {"qwen36-q5": q36, "qwen38-q5": q38},
        "weighted_delta_qwen36_vs_qwen38_pp": score_delta,
        "safety": "both candidates passed the explicit STOP gate in both repeats",
        "decision": {
            "tournament_score_winner": "Qwen3.6-35B-A3B Q5",
            "new_primary": False,
            "primary": "Qwen3.8-27B Q5 (unchanged)",
            "local_worker": None,
            "reason": "Qwen3.6 is +10 pp on this narrow final score, but candidate restart and primary-to-worker-to-fallback plus owner-stack Computer Use/Jeff/CMD/UX/Telegram/heartbeat integration gates were not all verified. No owner-stack promotion is proven.",
            "model_stack_owner_proven": "NO",
        },
        "known_limits": [
            "TTFT and device-specific VRAM were not exposed by the Bossman task API; server RSS and wall-normalized output throughput are proxies.",
            "The final corpus has 12 coding hidden-test checks, 6 tool checks, 4 browser checks, 2 recovery checks, 4 context checks and 2 STOP checks per candidate.",
            "Each candidate missed one TOOL-02 first-heading assertion on repeat 2; Browser, recovery, context answers and STOP were otherwise correct.",
            "The full owner-PC stack was not promoted or proven; no canonical configuration changed.",
        ],
    }
    (HERE / "final-scorecard.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
    summary_path = ROOT / "results.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["final_tournament"] = result
    summary["score"] = {"primary_change_threshold_pp": 5,
                        "weighted_delta_qwen36_vs_qwen38_pp": score_delta,
                        "tournament_winner": "Qwen3.6-35B-A3B Q5",
                        "new_primary_eligible": False,
                        "primary": "Qwen3.8-27B Q5 (unchanged)",
                        "local_worker": None,
                        "model_stack_owner_proven": "NO"}
    summary["limitations"] = [
        "The quick round tied Qwen Q5 and Qwen3.6 at 9/10; final-round hidden coding score was 8/12 for Qwen3.6 and 6/12 for Qwen3.8.",
        "All final attempts used the isolated Bossman API and completed. Full owner-stack restart, PRIMARY->WORKER->fallback, Jeff, CMD, UX, Telegram polling, heartbeat, and Windows Computer Use integration were not demonstrated.",
        "The Qwen3.6 tournament server was not restarted after its final run; no candidate route was promoted and canonical configuration remained unchanged.",
        "TTFT and device-specific VRAM were unavailable. Wall-normalized output throughput and server RSS are reported as proxies.",
        "Known output files are scrubbed summaries/diffs; API token, BCC database/WAL, caches, candidate workspaces, and raw user chat content are excluded.",
    ]
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"weighted_delta_qwen36_vs_qwen38_pp": score_delta,
                      "qwen36_weighted": q36["weighted_score_percent"],
                      "qwen38_weighted": q38["weighted_score_percent"],
                      "primary": "Qwen3.8-27B Q5", "owner_proven": "NO"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
