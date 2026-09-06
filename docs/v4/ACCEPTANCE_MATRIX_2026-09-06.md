# V4/V5 acceptance matrix — measured, 2026-09-06

`START_SHA=22e2ea304bcc2f4f51b24af41c881f0f9353d827` (integration head, contains
merged PR #31). Branch `claude/v4-v5-local-models-optimization-ya3utu`.

This is an evidence table, not a plan. Every row says what exists, what was run,
and what is still missing. Statuses are the mission's:

```
V4 = NOT_COMPLETE
V5 = NOT_COMPLETE
WINDOWS_ACCEPTANCE = NOT_RUN
HUMAN_COMPARISON = NOT_MEASURED
INTELLIGENCE_PRESERVATION = INSUFFICIENT_EVIDENCE
EXACT_SHA_CI = PENDING (see the final SHA's runs)
```

## Workflow truth at START_SHA, diagnosed from logs

| Workflow | Result at 22e2ea30 | Cause read from the log | Status here |
|---|---|---|---|
| root-ci | FAIL | Collection error, both jobs: `ModuleNotFoundError: No module named 'fastapi'`. root-ci installs only `-e .` + pytest/pytest-timeout/psutil/httpx by design; two root tests merged in PR #31 reached `bossman.gateway` and `bossman.computer_operator`, whose `__init__` pulls the web stack in. **Introduced by PR #31.** | FIXED |
| Bossman Core CI (`pytest rest`) | FAIL | `1 failed, 1984 passed`: `test_c7_operator_invalidation_between_approval_and_execution[stop]` expected FAILED, got CANCELLED — a stale expectation left over from the owner-control fix in PR #31. **Introduced by PR #31.** | FIXED |
| Bossman V2 Auto-Repair | FAIL | Runs the same Core suite; same single failure. | FIXED by the same commit |
| Intelligence Preservation | FAIL | `INTELLIGENCE_PRESERVATION=INSUFFICIENT_EVIDENCE / Missing docs/benchmark/intelligence-preservation-current.json`, exit 2. **Not a model regression and not infrastructure noise** — the gate refuses to report what nobody measured. Also failed at f892d6b, so it predates PR #31. | STILL RED — evidence does not exist |
| ASTRA acceptance | PASS | — | — |
| Solana safety gates | PASS | — | — |
| Fable media and Fleet acceptance | PASS | — | — |
| Command Center CI | FAIL | Not yet diagnosed from its log; locally the CC suite is 1790 passed / 126 skipped with 35 failures whose causes are: 31 × `ffmpeg is unavailable` (absent in the container; pass once installed), 2 × absent `mcp` optional extra (pass once installed), 2 × web-designer Playwright which **reproduce identically at 45d9004**, i.e. predate this work. | 33/35 environment, 2 pre-existing |

## Local suite results on this branch

| Suite | Command | Result |
|---|---|---|
| root | `python -m pytest tests -q` | see final run in the PR body |
| root under root-ci's dependency set | `tests/test_root_dependency_boundary.py` (blocks fastapi/yaml/pydantic/sqlalchemy/…) | 796 collected, 0 import errors |
| bossman-core | `PYTHONPATH=.. python -m pytest tests -q` (in `bossman-core/`) | **2489 passed, 36 skipped, 0 failed** |
| `bossman-core/tests/audit001` | `python -m pytest tests/audit001 -q` | **71 passed, 0 xfail** (was 69 + 2 strict xfail) |
| command-center | `python -m pytest tests -q -c pyproject.toml` | 1790 passed, 126 skipped, 35 failed → 33 environment, 2 pre-existing (table above) |

## V4 (Continuity)

| Requirement | Implementation | Evidence | Missing |
|---|---|---|---|
| Immutable Mission IR, typed effects, revision/digest | `bossman_shared/mission_ir.py` | `tests/test_epoch4_mission_ir.py`, CC bridge test against a real filesystem verifier | Runtime dispatch still not wired; the IR is a candidate contract |
| Owner interruption stops dispatch | `computer_operator/manager.py` interrupt latch + effect-boundary refusal | `test_computer_operator_owner_control.py` (21 cases); falsified against the pre-fix loop: 3 lost-command cases and 5 stop-latency cases fail | — |
| Owner command survives a concurrent write | `JsonTaskStore.save` compare-and-set on `ComputerTask.revision` | same file; pre-fix the task reaches COMPLETED after Pause | — |
| Effect outcome durable across an owner stop | `_persist_effect_outcome` merges onto the authoritative row | `test_an_interrupt_never_cancels_an_effect_that_is_already_running` | — |
| Fresh observation before action, staleness rejected | existing generation/`plan_generation` checks + bounded reuse window | `test_stage13_operator_redteam.py`, `test_computer_operator_owner_control.py` | Windows semantic identity (window/frame/overlay) is unverified on real hardware |
| Serialized desktop input | `ControlLease` (single holder, TTL, heartbeat, revoke) | `test_lease_ttl_expiry_takeover_and_heartbeat`, `test_lease_lost_to_another_holder_still_fails_the_task_honestly` | — |
| Real supported Windows adapter | `adapters/windows.py` with `snapshot()`, bounded BFS walk | `test_computer_observation_pipeline.py` (bounded walk, fallback, single window resolution) | **NOT_RUN on Windows**: `_active_window()` needs a real UIA host |
| Per-phase latency instrumentation | `manager.phase_seconds` / `phase_report()`, `tools/operator_step_profile.py` | `bossman-core/tests/test_operator_step_profile.py` | — |
| Threefold performance | `bossman_shared/epoch4_metrics.py` (+ per-family gate at f892d6b) | synthetic arithmetic tests only | `TRIPLE_PERFORMANCE=NOT_MEASURED`: needs 100+ paired runs on frozen hardware |
| Migration / canary / rollback rehearsal | not implemented | — | **MISSING** |
| Signed-history rollback anchor | not implemented | — | **MISSING**: needs independent durable monotonic state; another signature is not a solution |

## V5 (Steward)

Unchanged by this branch except where noted; `docs/v5/V5_RELEASE_SCORECARD.md`
holds the per-node table. N1–N3 PASS, N4–N8 PARTIAL, N0 NOT_RUN. Two of its
`NOT_RUN` suite rows are now measured (CORE, COMMAND_CENTER above). Soak,
Windows, PRIVATE egress and Fleet rows remain `NOT_RUN`.

## Latency, measured

`tools/operator_step_profile.py`, every sample retained, no trimming. Scope:
framework only — observation, planning and action costs are **declared inputs**,
because their true cost belongs to the owner's host and model.

| Target (mission section 5) | Measured | Verdict |
|---|---|---|
| Stop acknowledgement p95 ≤ 200 ms | p50 0.32 ms, p95 0.46 ms, max 0.47 ms over 20 trials, with the stop landing inside an observation that would have run 3000 ms | PASS |
| No dispatch after cancellation, prevention p95 ≤ 250 ms | 0 dispatches after stop; same latency as above | PASS |
| Ready-action dispatch overhead p95 ≤ 150 ms (excl. inference and app response) | framework overhead 2.2 ms/step at 12 steps, 5.3 ms/step at 60 | PASS |
| Simple observe→act→verify p50 ≤ 1 s / p95 ≤ 2 s with no new reasoning | not measurable here: it is dominated by the host's observation cost, which this container cannot produce | NOT_RUN on target hardware |
| UI feedback p95 ≤ 100 ms | out of scope for this path (no UI transport measured) | NOT_RUN |

Phase breakdown at zero declared costs named the framework's own bottleneck: the
journal. 5.3 writes/step → 4.3, and `JsonTaskStore` no longer re-parses the whole
journal per write (11.0 → 5.3 ms/step at 60 steps). The residual is still linear
in step count and is documented in
[OPERATOR_STEP_LATENCY_20260906.md](../testing/OPERATOR_STEP_LATENCY_20260906.md).

## What blocks each remaining status, precisely

| Status | Exact blocker | Exact next action |
|---|---|---|
| `WINDOWS_ACCEPTANCE=NOT_RUN` | No Windows host in this environment; `WindowsDesktop._req()` refuses off-Windows by design | On the owner's Windows 11 host: `python -m pytest bossman-core/tests/test_stage13_windows_adapter.py -q` (its skip reason names the requirement), then `python tools/operator_step_profile.py --steps 40 --observe-ms <measured> --plan-ms <measured> --act-ms <measured> --json-out artifacts/operator_step_profile.json` |
| `HUMAN_COMPARISON=NOT_MEASURED` | Requires the owner at the same machine performing the same tasks; no agent can supply the human arm | Protocol in [REAL_USER_UI_E2E_ACCEPTANCE.md](../testing/REAL_USER_UI_E2E_ACCEPTANCE.md); record agent and human medians on the same starting state, disposable files only |
| `INTELLIGENCE_PRESERVATION=INSUFFICIENT_EVIDENCE` | `docs/benchmark/intelligence-preservation-current.json` does not exist. The gate, the schema and the diagnosis codes all exist; the measured run does not | Run the four same-model lanes (raw→system→context→full) over one dataset on the owner's local model, write the payload per [INTELLIGENCE_PRESERVATION.md](../benchmark/INTELLIGENCE_PRESERVATION.md) with `evaluated_sha` = the release SHA, then `python tools/intelligence_preservation_gate.py docs/benchmark/intelligence-preservation-current.json --core-retention-min 0.98 --tool-retention-min 1.0 --min-samples 20 --expect-sha <SHA>`. The workflow now prints this command and states INSUFFICIENT_EVIDENCE vs NO_GO in its job summary so a red X is not misread as a regression |
| `TRIPLE_PERFORMANCE=NOT_MEASURED` | Needs ≥100 paired runs per declared aggregate on frozen hardware/model/permissions | `bossman_shared.epoch4_metrics.evaluate` is ready to score them; the corpus and the runs are the missing half |
| V4 migration/canary/rollback | Not implemented | Own milestone (M11); not attempted here rather than claimed |
