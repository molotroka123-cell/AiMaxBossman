"""Aggregate Agent15 local evidence without reinterpreting model responses."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def summarize(paths: list[Path]) -> dict:
    rows = []
    sources = []
    for path in paths:
        content = path.read_bytes()
        sources.append({"path": str(path.resolve()), "sha256": hashlib.sha256(content).hexdigest()})
        rows.extend(json.loads(line) for line in content.decode("utf-8").splitlines() if line.strip())
    summary: dict = {"sources": sources, "models": {}}
    for model in sorted({row["model"] for row in rows}):
        summary["models"][model] = {}
        for arm in ("baseline", "candidate"):
            sample = [row for row in rows if row["model"] == model and row["arm"] == arm]
            if not sample:
                continue
            gated = [row for row in sample if row["expected"]["action"] in {"OWNER_REQUIRED", "BLOCKED"}]
            summary["models"][model][arm] = {
                "n": len(sample),
                "full_pass": sum(all(row["score"].values()) for row in sample),
                "zone_correct": sum(row["score"]["zone"] for row in sample),
                "action_correct": sum(row["score"]["action"] for row in sample),
                "valid_json": sum(row["score"]["json"] for row in sample),
                "honest_not_done": sum(row["score"]["honesty"] for row in sample),
                "gated_n": len(gated),
                "gated_downgrade_to_read_or_plan": sum(
                    isinstance(row["answer"], dict) and row["answer"].get("action") in {"READ", "PLAN"}
                    for row in gated
                ),
                "false_done": sum(isinstance(row["answer"], dict) and row["answer"].get("claim") == "DONE"
                                  for row in sample),
                "mean_latency_s": round(sum(row["meta"]["latency_s"] for row in sample) / len(sample), 3),
            }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("responses", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.responses)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
