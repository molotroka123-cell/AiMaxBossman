"""Print a saved task's diff (repr of CR presence + text)."""
import json
import sys
from pathlib import Path

r = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
d = r.get("diff") or ""
print("CR count:", d.count("\r"), "len:", len(d))
print(d[: int(sys.argv[2]) if len(sys.argv) > 2 else 6000])
