"""Print a compact view of one or more coding-task records saved by cycle.py (no secrets in these records)."""
import json
import sys
from pathlib import Path

for arg in sys.argv[1:]:
    p = Path(arg)
    r = json.loads(p.read_text(encoding="utf-8"))
    s = r.get("sidecar") or {}
    print(f"== {p}  id={r.get('id')} worker={r.get('worker')} status={r.get('status')} base={str(r.get('base_commit'))[:10]}")
    print("  error:", (r.get("error") or "")[:400])
    print("  stop:", s.get("stop_reason"), "steps:", s.get("steps"), "calls:", s.get("tool_calls_total"),
          "changed:", r.get("changed_files"), "verification:", {k: (r.get("verification") or {}).get(k)
                                                               for k in ("ran", "runner", "passed", "exit_code")})
    print("  summary:", str(s.get("summary"))[:700])
    print("  stderr:", str(s.get("stderr_tail"))[-500:])
    calls = s.get("tool_calls") or []
    print("  tools:", [(c.get("tool") or c.get("name"), c.get("ok")) for c in calls][:45])
