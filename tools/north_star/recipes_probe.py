"""Print + append to <evidence>/restarts.jsonl the lab backend's identity and its VERIFIED recipe ids (restart proof)."""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cycle as C  # noqa: E402

label = sys.argv[1]
ev = Path(sys.argv[2]).resolve()
with C.client() as c:
    health = c.get("/health/live")
    items = c.get("/api/coding-recipes?project_id=capability-tree")["items"]
row = {"at": C.now(), "label": label, "health": {k: health.get(k) for k in ("status", "build_sha_short", "source")},
       "recipes": sorted(str(i.get("id") or i.get("recipe_id")) for i in items),
       "statuses": sorted({str(i.get("status")) for i in items})}
with (ev / "restarts.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
print(json.dumps(row, ensure_ascii=False))
