# Opus correction — sandbox user-run findings + current V5 closure

Repository: `molotroka123-cell/AiMaxBossman`

You remain the single integrator. Fetch first; the repository may have advanced
since this handoff. The sandbox patch was prepared against production blobs that
were unchanged through integration SHA `0ad86d172c39fe5e4cb40deae2b0b26abcfe1c29`.

## Do not redo

Do not rebuild the existing AT-03 work, file/screen AT-01 obligations, Video CFR
one-frame tolerance, Video playback repaint/reconnect fixes, OpenRouter provider
isolation, canary attestation helper, skill pack integration, or CAS host-noise
measurement work unless a current-SHA negative control fails.

## Integrate and verify the sandbox runtime patch first

Read `docs/audits/SANDBOX_USER_RUN_20260907.md` and the ready-to-apply patch pack
under `patches/sandbox-runtime-hardening/`.

The following were reproduced by an actual unprivileged Command Center process,
not inferred from static code:

1. Stop -> Resume resurrected a stopped task. **P0 owner-control.**
2. Pause -> Resume during an in-flight model call duplicated inference/provider
   cost. Fencing stopped duplicate tool dispatch in the tested path, but not the
   duplicate model call.
3. Retry while active could create another run.
4. Two concurrent Run requests could create two runs (TOCTOU).
5. Crash recovery could undo persisted Stop/Pause.
6. Late Stop rewrote a completed task to stopped.
7. Terminal Action Contract veto could produce `task=failed / run=queued` orphan.
8. Failed run text could be exposed as aggregate `task.result`.
9. HTTP 200 malformed provider JSON escaped `ProviderError` and could strand a
   run until lease recovery.
10. Agent deletion during execution destroys durable agent provenance — still
    OPEN because it needs an execution-identity snapshot, not a cosmetic block.

Apply semantically, not blindly:

```text
git apply --check patches/sandbox-runtime-hardening/engine.patch
git apply --check patches/sandbox-runtime-hardening/api.patch
git apply --check patches/sandbox-runtime-hardening/providers.patch
git apply --check patches/sandbox-runtime-hardening/tests.patch
```

If any current file advanced, port only the same invariants into the newer code.
Do not reset newer Opus work to the patch preimage.

Required exact-current-SHA tests after application:

```text
pytest command-center/tests/test_sandbox_user_run_regressions.py -q
pytest command-center/tests/test_engine_stop.py \
       command-center/tests/test_queue_retry.py \
       command-center/tests/test_persistence.py \
       command-center/tests/test_worker_pool.py \
       command-center/tests/test_providers.py \
       command-center/tests/test_fence_fl01.py \
       command-center/tests/test_api.py -q
```

Then run full Command Center CI with canonical dependencies. Do not count
cancelled checks as PASS.

## Next — current V5 P0 order

After the sandbox lifecycle patch is green:

### P0-A — production canary caller
Wire the real revision rollout/broad-activation boundary through the existing
canary contract. The helper existing in tests is not implementation completion.
Require resolved evidence and fail closed at the caller.

### P0-B — SATISFIED evidence boundary
Finish the current `ObjectiveStore` work so arbitrary `evidence_ref="x"` cannot
purchase SATISFIED. Resolve and bind evidence to objective/revision/predicate,
producer, freshness and current attempt. The new freeze regression must pass.

### P0-C — UnknownEffect
Do not accept unrelated verified mutation as proof of an unnamed external
obligation. Add an effect-obligation/verifier contract or route the task to
owner reconciliation. Do not solve this with a screen-change heuristic.

## P1 after the three P0s

- Durable run-level agent execution identity snapshot. Deleting/disabling an
  agent must not erase who/system-prompt/tool-grants/permission revision executed
  an already running or historical run.
- N4: exercise fairness through the real scheduler loop, not only `rank()`.
- N5: real model baseline/candidate + disjoint holdout + retention + durable
  single-use promotion evidence.
- N6: owner-visible Objective create/edit/Evidence/Revisions/restart acceptance.
- N8: actual process restart, real canary broad-activation caller and executed
  rollback with independent post-state verification.
- owner Windows/local-model/human-speed acceptance on one exact RC SHA.
- RAW/SYSTEM/CONTEXT/FULL intelligence retention with sufficient paired evidence.
- PRIVATE egress and required soak/long-horizon evidence.

## Freeze discipline

Feature freeze stays ON. No repo cleanup, editor expansion, new provider UX,
architecture rewrites or wholesale stale PR merges in this line.

A code change creates a new candidate SHA. Rerun impacted hostile tests and
mandatory CI against that exact resulting SHA. Queued/cancelled/skipped required
checks are not PASS.

Report next:

```text
HEAD=
SANDBOX_LIFECYCLE_PATCH=
STOP_STICKY=
PAUSE_RESUME_SINGLE_INFERENCE=
RUN_RETRY_SINGLE_FLIGHT=
RECOVERY_OWNER_STATE=
TERMINAL_VETO_RUN_TRUTH=
TASK_RESULT_TRUTH=
PROVIDER_PROTOCOL_ERRORS=
AGENT_EXECUTION_PROVENANCE=
P0_A_CANARY_CALLER=
P0_B_SATISFIED_EVIDENCE=
P0_C_UNKNOWN_EFFECT=
N4=
N5=
N6=
N8=
INTELLIGENCE=
OWNER_WINDOWS=
LOCAL_MODEL=
OPEN_P0=
OPEN_P1=
FEATURE_FREEZE_READY=
RC_SHA=
```

Do not answer with a plan only. Fix, test, commit and push the remaining
repository-accessible blockers. Keep owner-machine-only evidence as NOT_RUN.
