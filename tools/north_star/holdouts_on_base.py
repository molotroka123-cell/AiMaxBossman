"""Run the repo's three hidden self-repair holdouts against the lab clone's base and summarize (read-only on the repo)."""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
LAB = Path(r"C:\Users\asd\Bossman\ns-lab-20261010")
out_dir = Path(sys.argv[1]).resolve()
out_dir.mkdir(parents=True, exist_ok=True)
rows = {}
for name in ("discovery_nonfinite", "goal_budget_nonfinite", "atomic_json_replace"):
    out = out_dir / f"{name}.json"
    subprocess.run([str(LAB / "venv" / "Scripts" / "python.exe"), "-X", "utf8",
                    str(HERE / "tools" / "tree_holdout" / f"{name}.py"), "--repo", str(LAB / "repo"), "--out", str(out)],
                   capture_output=True, text=True, timeout=600)
    d = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    rows[name] = {k: d.get(k) for k in ("total", "failed", "passed")}
    rows[name]["failing_cases"] = sorted({f["case"].split("/")[0] for f in d.get("failures", [])})[:10]
print(json.dumps(rows, indent=1))
(out_dir / "summary.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
