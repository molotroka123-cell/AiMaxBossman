"""Does the lab Bossman recall a saved recipe for a given instruction? (retrieval only: a memory hit is NOT learning)"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "tools"))
import cycle as C  # noqa: E402
import tree_self_repair_cycle as tool  # noqa: E402

text = tool.CASES["goal-budget"]["wish"] if sys.argv[1] == "goal-budget" else sys.argv[1]
with C.client() as c:
    r = c.post("/api/coding-recipes/match", {"instruction": text, "project_id": "capability-tree", "limit": 5})
row = {"at": C.now(), "instruction": sys.argv[1], "memory_hit": r.get("memory_hit"),
       "ids": [i.get("id") or i.get("recipe_id") for i in r.get("items", [])]}
Path(sys.argv[2]).write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(row, ensure_ascii=False))
