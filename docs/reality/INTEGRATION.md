# Integration contract

## Runtime sequence

1. Planner proposes JSON → RealityCompiler.compile.
2. Trusted host resolves policy, targets, action risk, privacy, principal and verifier IDs.
3. Runtime.admit validates the current policy digest and persists the entire IR.
4. Existing scheduler enforces dependencies and leases.
5. Existing global cost ledger reserves the maximum allowed paid cost; RealityStore.reserve
   can additionally reserve per mission using a stable charge ID.
6. Runtime.execute checks exact args digest, claims durable escrow, checks actual owner/fence,
   calls host action adapter, then fresh observer; only bound proof confirms the effect.
7. Independently observe remaining obligations. Store receipts; receipt of an effect must
   include dispatch_binding=digest([mission.fingerprint, effect.id, stored_fence]).
8. All effects confirmed + all receipts current and verified + current policy match → PASS.
9. Host independently commits final task status with its current DB fence. This package
   cannot make a separate Bossman database transaction atomic with its SQLite transaction.
   Recheck completion during finalization and after recovery; never use a cached PASS alone.

## BCC integration inspected

`command-center/bcc/engine.py` has `gate_completion` as a critical hook and invokes
`_call_hooks("gate_completion", task, run_id, answer)` before finalization.
`FAIL` requires an explicit `requeue` field. `make_completion_hook` matches that contract.
`execute_tool` is used in the same engine; place dispatch integration at the effective
common tool boundary, not only one UI path. Inspect current code before changing it.
`bossman_shared/__init__.py` describes the existing dependency-free shared contracts.
`bossman-core/pyproject.toml` requires Python >=3.11.
No claim is made that Core/Compound/Fleet call sites were fully inspected in this package.

## Adapters

- Action: callable(mission, effect, arguments). It must enforce the existing policy and
  safe target handling, use provider idempotency, and return only after dispatch status
  is known. The return value is deliberately not evidence.
- Observer: callable(target). Must freshly retrieve normalized data from an independent
  authoritative source. Normalization must match the committed expected_digest exactly.
- Fence checker: callable(mission,effect,worker,local_fence) -> bool. Must also inspect
  the real Fleet lease/owner/fence; the local integer is not a global fencing token.
- Mission loader: callable(task,run_id) -> persisted Mission. Bind the task, tenant,
  owner, run and execution generation; never take the IR from final answer text.
- Reconciliation: trusted host reads provider request status, stops/joins old workers,
  determines whether late completion remains possible, then uses confirm or
  reconcile_absent. The latter's booleans are trusted internal API inputs, never an
  exposed model tool or authenticated-by-string substitute.

## Repair and unknown state

PREPARED → EFFECT_ESCROW → CONFIRMED
EFFECT_ESCROW → SAFE_TO_RETRY → EFFECT_ESCROW with incremented fence
EFFECT_ESCROW → MANUAL_REVIEW_REQUIRED

There is no timer-based escrow reclaim. On adapter exception escrow stays unresolved.
Manual review deliberately has no generic reset method. A production recovery operation
must require an owner-authorized decision, authoritative target check and append-only
record, then reconcile through the shared transaction model. Never edit SQLite rows by
hand as an operational retry procedure.

## Policy and rollout

Example policy limit $3 is illustrative; deployment loads the owner's existing grants.
Policy cannot be updated by an agent-facing tool. Constitution is an immutable value
object but system-level immutability requires process isolation and filesystem ACLs.
Autonomy scores restrict the owner's ceiling. All inputs are host measured, not the
agent's self-report. Paid-call caps apply cumulatively through existing global ledger.
Financial action classification must be host-owned; do not trust an action's arbitrary
name to decide whether it is financial. Block local clinical data at egress as well as
at MemoryCompiler. The package contains no network proxy or DLP replacement.

Rollback: disable admission of NEW missions; leave opted-in missions gated, drain or
reconcile escrow, preserve journals and keys. Never roll back by deleting state or by
returning PASS when the module is unavailable.

Registration in the inspected BCC engine API:

```python
engine.add_hook(
    'gate_completion',
    make_completion_hook(reality_runtime, load_persisted_mission),
    critical=True,
)
```

Observed finalization calls `bcc.finalize.finalize_task`. Also inspect that implementation
for the final transaction-bound check. The inspected FAIL/requeue=False branch marks the
run queued while setting task waiting_approval. Therefore recovery/queue claim MUST honor
that waiting task state: the hook alone does not establish non-reclaimability of the run.
Return uses `reason`; adapt to the existing UI's `reasons`/`feedback` fields if desired.
