"""Bossman vs OpenCode benchmark aggregator (benchmark evidence).

Вход — JSONL, по одному прогону на строку. Считает сравнимые метрики на
исполнителя (executor): success rate, tests-green rate, средние human-
interventions / elapsed / cost / security-violations / retries. Чистые функции,
без I/O кроме load_jsonl. Даёт объективную таблицу вместо оценки «на глаз».
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_jsonl(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"line {n}: object required")
            rows.append(row)
    return rows


def summarize(rows: list[dict]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[str(r["executor"])].append(r)
    result: dict[str, dict[str, Any]] = {}
    for name, items in groups.items():
        n = len(items)

        def avg(key: str) -> float:
            return statistics.fmean(float(x.get(key, 0) or 0) for x in items)

        result[name] = {
            "runs": n,
            "success_rate": sum(bool(x.get("success")) for x in items) / n,
            "tests_green_rate": sum(bool(x.get("tests_green")) for x in items) / n,
            "avg_human_interventions": avg("human_interventions"),
            "avg_elapsed_s": avg("elapsed_s"),
            "avg_cost_usd": avg("cost_usd"),
            "avg_security_violations": avg("security_violations"),
            "avg_retries": avg("retries"),
        }
    return result


def compare(rows: list[dict], *, a: str = "bossman", b: str = "opencode") -> dict[str, Any]:
    """Прямое сравнение двух исполнителей по ключевым метрикам."""
    s = summarize(rows)
    if a not in s or b not in s:
        return {"summary": s, "verdict": "insufficient data",
                "have": sorted(s), "need": [a, b]}
    sa, sb = s[a], s[b]
    wins = {
        "success_rate": sa["success_rate"] >= sb["success_rate"],
        "tests_green_rate": sa["tests_green_rate"] >= sb["tests_green_rate"],
        "fewer_interventions": sa["avg_human_interventions"] <= sb["avg_human_interventions"],
        "cheaper": sa["avg_cost_usd"] <= sb["avg_cost_usd"],
        "fewer_security_violations": sa["avg_security_violations"] <= sb["avg_security_violations"],
    }
    return {"summary": s, f"{a}_vs_{b}": wins,
            "a_wins": sum(wins.values()), "criteria": len(wins)}


if __name__ == "__main__":  # pragma: no cover
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("jsonl", type=Path)
    args = p.parse_args()
    print(json.dumps(compare(load_jsonl(args.jsonl)), indent=2, ensure_ascii=False))
