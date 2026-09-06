import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "C:/Users/timur/AppData/Local/Temp/opencode/kimi-wt")
from bossman_shared.objective_spec import ObjectiveSpec
from bossman_shared.objective_store import ObjectiveStore

tmp = Path(tempfile.mkdtemp())
store = ObjectiveStore(tmp / "o.sqlite3")


def spec(i):
    return ObjectiveSpec.from_dict({
        "schema_version": 1, "owner_id": "owner", "scope_id": "project",
        "objective_id": f"obj-{i:03d}", "revision": 1, "previous_digest": None,
        "sources": [{"source_ref": "s", "source_revision": "v1", "max_age_seconds": 30}],
        "predicates": [{"predicate_id": "p", "source_ref": "s", "field": "exists",
                        "value_type": "boolean", "operator": "eq", "expected": True}],
        "expires_at": 10_000, "priority": 1, "allowed_triggers": ["source_change"],
        "permission_refs": ["g"], "conflict_keys": ["k"], "cooldown_seconds": 5,
        "limits": {"max_observations": 1, "max_missions": 1, "max_wall_seconds": 1,
                   "max_cost_usd": 0},
        "stop_conditions": ["r"],
    })


lat = []
for i in range(60):
    t0 = time.perf_counter()
    store.create(spec(i))
    lat.append((time.perf_counter() - t0) * 1000.0)
ms = sorted(lat)
print(f"n=60 min={ms[0]:.2f} med={statistics.median(ms):.2f} "
      f"p95={ms[int(len(ms)*0.95)]:.2f} max={ms[-1]:.2f} first={lat[0]:.2f}")
