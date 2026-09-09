# Independent Fleet closure

Scope: historical Fleet, lease, queue, resource, remote-auth and crash boundaries.
Runtime baseline: `024ec6b` (packaging branch descendant of PR61).
No owner hardware or real model measurement is claimed.

| ID | Severity | Reproduction / root cause | Fix | Regression |
|---|---|---|---|---|
| ASTRA-FLEET-01 | P1 | Node heartbeat=100, timeout=10, place(now=111) returned PLACED because admission assumed a separate watchdog tick. | Evaluate health in placement before selecting/acquiring. | `test_stale_heartbeat_refuses_placement_without_separate_watchdog_call` rejects stale then accepts a new valid heartbeat. |
| ASTRA-FLEET-02 | P2 | Eight expired lease/reacquire cycles left one live lease and eight reservation rows; acquire deleted leases directly, bypassing reservation cleanup. | Delete matching expired reservations in the same admission transaction; purge historical orphans at store initialization. | `test_expired_reacquisition_does_not_accumulate_memory_reservations` requires one lease and one reservation. |
| ASTRA-FLEET-03 | P1 | A copied lease with a forged work/node/fence/resource/mode could release the genuine lease because release deleted by lease_id alone. | Require full persisted identity on capability release; retain privileged store deletion for controller reclaim. | Five `test_release_requires_complete_persisted_lease_identity` negative controls; original capability still works. |
| ASTRA-FLEET-04 | P1 | Negative usage, NaN load and negative active-work became zero usage and admitted a 12GB task on a 16GB node; malformed raw values could also crash the scheduler or be hidden by max(0, value). | Reject invalid raw observations before arithmetic; invalid heartbeat marks node degraded and consumes headroom; valid later observation restores eligibility. | Parametrized invalid-heartbeat/raw-observation controls; positive valid recovery. |
| ASTRA-FLEET-05 | P1 | Dead-letter requeue reset claim_fence to 1. A stale same-node Claim(fence=1) completed newly assigned work; completed work_id reuse had the same ABA defect. Attempts also reset. | Constant-size durable monotonic fence counter survives deleted rows/restart; requeue preserves attempts. | Dead-letter/restart, completed-ID reuse and racing worker controls reject old claims and preserve exactly one owner. |

| ASTRA-FLEET-06 | P1 | Renewal started before expiry, blocked on SQLite's writer lock, then used its stale pre-wait timestamp to renew expired authority. A real thread/SQLite lock probe reproduced successful revival after expiry. | Acquire the writer transaction first and account for elapsed monotonic wait before the persisted expiry predicate. | `test_waiting_for_database_lock_cannot_revive_an_expired_lease` uses a real contending transaction and requires refusal. |

Renewal follow-up validation: **89 passed in 0.71s** (64 new controls plus Fleet core/safety proofs).

The queue counter is seeded in milliseconds at migration so old small row-local
fences cannot alias new authority, while values remain exactly representable by
JavaScript. Existing row fences are also included when advancing the counter.

Validation after the fixes: **211 passed in 38.75s** across the new 63 controls,
Fleet attacks, safety proofs, fence receipts, core/E2E, crash matrix, remote auth,
real loopback mTLS RPC, resource brain and resource stress. An earlier bounded
Command Center pass had **47 passed** (resource pressure/unmeasured strategy,
engine fences and queue retries); it is not presented as final-SHA evidence.

Test correction: the shared Fleet E2E helper used a heartbeat at Unix timestamp
1000 while executing against the real current clock. Admission freshness correctly
exposed this stale fixture. The helper now registers a genuinely current timestamp;
no assertions were removed or weakened, and an explicit stale-heartbeat negative
control was added. `Heartbeat.active_work=None` is documented as an omitted report,
so it remains compatible; malformed supplied values are rejected.

Run from `bossman-core`:

```bash
python -m pytest -q tests/test_astra_fleet_closure.py tests/test_fable_fleet_attack.py tests/test_v3_fleet_safety_proofs.py tests/test_v3_fence_receipts.py tests/test_v3_fleet_core.py tests/test_v3_fleet_e2e.py tests/test_fable_crash_matrix.py tests/test_fleet_remote_auth.py tests/test_fleet_remote_rpc.py tests/test_resource_brain.py tests/test_stage9_resource_stress.py --timeout=90
```

Final convergence must rerun against its own exact SHA. Physical unified-memory
load/unload, Windows host and live models remain owner-environment acceptance.

Timestamp fuzz follow-up: a persisted NaN heartbeat also bypassed staleness (`now - NaN > timeout` is false). Admission now rejects malformed/nonfinite/future heartbeat state before arithmetic; seven new persisted-state controls require refusal and successful recovery after a genuine heartbeat.
