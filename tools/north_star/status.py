"""Read-only status of the North Star evidence folder: cycles, spend ledger, OpenRouter counters, soak heartbeat."""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ev = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/evidence/north-star-20261010")

for cyc in sorted(ev.glob("c*/cycle.json")):
    d = json.loads(cyc.read_text(encoding="utf-8"))
    st = d.get("stages", {})
    print(cyc.parent.name, d.get("result"), "|", d.get("why"), "| roles", d.get("roles"),
          "| zone", d.get("zone", {}).get("target"))
    for k, v in st.items():
        if isinstance(v, dict):
            print("   ", k, {kk: vv for kk, vv in v.items() if kk in ("task", "status", "steps", "verdict", "accepted", "why", "model")})
        else:
            print("   ", k, v)
spend = defaultdict(lambda: [0, 0.0])
sp = ev / "spend.jsonl"
if sp.is_file():
    for line in sp.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        spend[f"{r['provider']}:{r['model']}"][0] += 1
        spend[f"{r['provider']}:{r['model']}"][1] += float(r.get("cost_usd") or 0)
print("spend ledger (harness model calls only):", dict(spend))
try:
    import providers
    print("openrouter key counters:", providers.openrouter_usage())
except Exception as exc:  # noqa: BLE001
    print("openrouter usage FAIL", type(exc).__name__)
hb = Path(r"C:\Users\asd\Bossman\soak-20261010\heartbeat.jsonl")
if hb.is_file():
    lines = hb.read_text(encoding="utf-8").splitlines()
    print("soak heartbeats:", len(lines), "last:", lines[-1][:260])
