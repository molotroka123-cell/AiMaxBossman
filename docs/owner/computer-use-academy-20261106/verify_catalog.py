#!/usr/bin/env python3
"""Static catalog integrity checker. Does not run GUI or train any model."""
import collections
import json
from pathlib import Path

path = Path(__file__).with_name("SCENARIOS_500.jsonl")
rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
assert len(rows) == 500, f"Expected 500, found {len(rows)}"
assert len({r["id"] for r in rows}) == 500, "Duplicate scenario ID"
assert len({(r["family"], r["variant"]) for r in rows}) == 500, "Duplicate family/variant"
families = collections.defaultdict(set)
for r in rows:
    for field in ("task", "success_oracle", "execution_lane", "verification_method", "negative_control", "cleanup"):
        assert isinstance(r.get(field), str) and r[field].strip(), (r["id"], field)
    assert r["status"] == "NOT_RUN" and r["receipt"] is None, r["id"]
    assert r["split"] in {"train", "validation", "test"}, r["id"]
    assert r["variant"] in {"standard", "scaled", "distractor", "partial", "restart"}, r["id"]
    assert r["fixture"]["state"].strip(), r["id"]
    families[r["family"]].add(r["split"])
assert len(families) == 100 and all(len(v) == 1 for v in families.values()), "Family split leakage"
assert dict(collections.Counter(r["category"] for r in rows)) == {
    "windows": 125, "roblox": 125, "browser": 100, "crossapp": 75, "recovery": 75
}
assert dict(collections.Counter(r["split"] for r in rows)) == {
    "train": 300, "validation": 100, "test": 100
}
assert all(sum(r["family"] == f for r in rows) == 5 for f in families)
print("PASS: 500 cases, 100 families, fixed quotas/splits, zero fabricated results")
