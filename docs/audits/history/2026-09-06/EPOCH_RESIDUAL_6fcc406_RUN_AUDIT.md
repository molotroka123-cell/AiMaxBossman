# Residual boundary checkpoint — V3/V4/V5

## Scope and provenance

The tested source archive is upstream `6fcc40633152fb3559331e4153b80ffa4220b6ba`,
with its exact original Git tree `19dd6eebbc8d2c25c34b76d873a3a3cfaeb9c90e`
reconstructed and matched before editing. The integration branch was separately
observed at `c1e186b64a6782323d5e806f658733725299c372`. Subsequent documentation
and skip-registry changes are not runtime test evidence. This local fixture
history is NOT upstream history and must never replace the remote branch.

The V4 work is reused from the previously delivered
`Bossman_V4_GLM_Run_Pack_2026-09-06.zip`, not another duplicate runtime. Its patch
applied cleanly to this combined Fable/Steward source before the tests below.

## Reproduced defects and fixes

| Boundary | Failure reproduced | Correction |
|---|---|---|
| V5 proposal identity | Same id with substituted effect/target accepted | Full canonical payload checked against durable proposal before ports and after callbacks |
| V5 -> V4 mission | Admitted effect changed after approval | Decision binds complete proposal digest and generated mission intent |
| Mission scope/budget | Different owner or larger budget accepted | Exact admitted owner/scope, budget may only narrow reserved ceiling |
| Clock | Invalid/negative/bool clock bypassed temporal checks | Finite nonnegative monotonic-with-proposal checks; bad time never authorizes |
| Admission dedup | Replay refunded/released running objective's claim | SQLite claim-before-ports; one unresolved admission per objective |
| Current authority | Admission did not require current grants at effect | Explicit policy port; missing policy fails closed; revoke inside callback rechecked |
| Source enrollment | Withdrawn observation source still admitted work | Enrollment checked at admission and again at effect boundary |
| Port failure | Exceptions left unexplained reservations/claims | Owned holds compensated; uncertain external outcome remains non-authorizing PENDING |
| Settlement CAS | Two concurrent settlers both reported success | Conditional UPDATE must affect exactly one row before journal success |
| SQLite resources | Transaction context left connection open | Guaranteed close on success and rollback/error |

First hostile batch: **9 failed / 2 passed** on original code.
SQLite closing checks: **2 failed** before correction.
Forced concurrent settlement: **1 failed**, both workers reported success, before correction.
Enrollment withdrawal checks: **2 failed** before correction.
These are test cases, not 14 independent severity labels. Later positive and
negative tests bring the new residual test file to 33 cases.

## Imported V3/V4 corrections

The existing implementation now composes with this checkpoint: persistent
mission DAG binding, atomic parent/child materialization, worker cap, pause/stop
and deadline preservation; detached action arguments; fresh policy at dispatch;
full approval identity across task/run/agent/tool; actual executed action used
for receipts; dependency/block reasons in the existing mission UI.
No second policy, Treasury, finalizer, registry, runtime or evidence signer.

## Results actually measured locally

Environment: isolated Linux container, Python 3.13.5, real SQLite and local file/
subprocess fixtures. Core and Command Center source paths are explicitly pinned.
Runtime-sensitive suites were supervised with real exit codes, not interpreted
from a printed pytest summary. The owner's test computer was not touched.

| Selection | Final result | Boundary |
|---|---|---|
| Shared V4/V5 foundations + all V5 suites + residual tests | 502 passed, exit 0 | Real durable store; policy/Treasury/conflict ports are fixture implementations |
| Core affected V3/V4 adapters/recovery/visual contracts | 249 passed, exit 0, 156.31 s | Actual BCC fixtures and local effects; not a real desktop/provider test |
| Command Center affected mission/finalizer/approval suites | 88 passed, exit 0, 54.05 s | Actual local integration, not GUI-click acceptance |
| Mission UI Node contracts | 5 passed, exit 0 | Not screenshots, not owner UI acceptance |
| Secret-pattern scan, whitespace | PASS | No threshold or broad skip added |

Core's previously unproven process exit was observed as exit 0 in this run.
This is fresh evidence for this selection/environment, NOT a claim that every
historical shutdown issue is fixed. The watchdog recorded no residual owned PIDs.

## Compatibility and remaining gates

`reauthorize_at_effect_boundary(..., policy=...)` now needs a live policy adapter.
Existing positive fixture callers were updated; missing policy intentionally
returns False. Legacy reservations without ready/content binding do not gain
new authority and require explicit reconciliation. A refused/ambiguous attempt
cannot silently reuse the same proposal; a fresh authorized proposal or recovery
is required. Only one unresolved admission per objective is currently allowed,
because the existing conflict port identifies claims by objective, not token.

Cross-service atomicity is NOT proved. A throwing remote reserve can have an
unknown outcome, which stays PENDING for reconciliation. The effect recheck must
run under the executor's existing fence; it is not a distributed lock by itself.
Canonical production policy/Treasury adapters, stronger remote fencing and real
multi-node acceptance remain separate work. Standing autonomy remains gated off.

Full root/Core/CC regression, required exact-SHA CI, supported Python 3.11/3.12,
Windows, PostgreSQL concurrency, actual GUI journeys, long soak, real-model
retention and complete V4 M0–M11 / V5 N0–N8 acceptance are NOT certified here.
Other unreviewed residual bugs may remain. No new scorecard or P0/P1=0 claim.

VERDICT=TARGETED_BOUNDARIES_FIXED_AND_LOCALLY_VERIFIED
EPOCHS_COMPLETE=NO
REMOTE_PUSH=NOT_CONFIRMED
EXACT_SHA_CI=NOT_RUN_FOR_PATCH

