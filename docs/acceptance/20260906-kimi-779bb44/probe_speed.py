import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "C:/Users/timur/AppData/Local/Temp/opencode/kimi-wt")
from bossman_shared.objective_observer import (
    DirectoryStateObserver,
    EnrolledSource,
    FileStateObserver,
    collect,
)
from bossman_shared.objective_recovery import recover
from bossman_shared.objective_spec import ObjectiveSpec
from bossman_shared.objective_store import ObjectiveStore

tmp = Path(tempfile.mkdtemp())
(target := tmp / "report.txt").write_text("alpha", encoding="utf-8")
spec = ObjectiveSpec.from_dict({
    "schema_version": 1, "owner_id": "owner", "scope_id": "project",
    "objective_id": "spd", "revision": 1, "previous_digest": None,
    "sources": [{"source_ref": "s", "source_revision": "v1", "max_age_seconds": 30}],
    "predicates": [{"predicate_id": "p", "source_ref": "s", "field": "exists",
                    "value_type": "boolean", "operator": "eq", "expected": True}],
    "expires_at": 10_000, "priority": 1, "allowed_triggers": ["source_change"],
    "permission_refs": ["g"], "conflict_keys": ["k"], "cooldown_seconds": 5,
    "limits": {"max_observations": 1000, "max_missions": 100, "max_wall_seconds": 3600,
               "max_cost_usd": 0},
    "stop_conditions": ["r"],
})
enr = EnrolledSource(source_ref="s", source_revision="v1", owner_id="owner",
                     scope_id="project", objective_digest=spec.digest)
store = ObjectiveStore(tmp / "o.sqlite3")
st = store.create(spec)
st = store.enroll_sources(st.objective_id, ("s",), owner_id="owner",
                          expected_version=st.version)
obs = [FileStateObserver(enr, target),
       DirectoryStateObserver(enr, tmp, max_entries=50)]
t0 = time.perf_counter()
collect(obs, state=st, max_observations=10, now=100.0)
t1 = time.perf_counter()
print(f"observe_cycle_ms={(t1 - t0) * 1000.0:.2f}")

st = store.transition(st.objective_id, "ACTIVE", now=100.0, owner_id="owner",
                      expected_version=st.version)
store.reserve_once(reservation_id="r-1", objective_id=st.objective_id,
                   proposal_id="p-1", created_at=101.0,
                   payload={"effect_class": "IRREVERSIBLE"})
del store
t0 = time.perf_counter()
reopened = ObjectiveStore(tmp / "o.sqlite3")
rep = recover(reopened, st.objective_id, now=200.0,
              is_effect_applied=lambda r: "UNKNOWN")
t1 = time.perf_counter()
print(f"recovery_s={t1 - t0:.3f} disposition={rep.outcomes[0].disposition}")
