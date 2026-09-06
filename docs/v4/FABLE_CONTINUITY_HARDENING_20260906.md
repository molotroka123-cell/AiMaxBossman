# Continuity boundary implementation — 2026-09-06

**Status: implemented candidate; V4 NOT COMPLETE.** This extends the existing
M2/M3/M6 boundary code, not the epoch scope or a second runtime. Generation B/C,
3x qualification and the V4 release contract are not claimed complete here.

## Preserve current work

Prepared for `claude/fable-system-hardening-lpqq9r` on the published checkpoint
`9d3f82ac0d26c5b88b408f0c470a3acfae700f98`. All ten edited runtime-file baselines
were read by immutable SHA and matched against the local source bytes. Fable's
media, RPC, registry, recovery and context changes are not replaced by this patch.
The two prior RemoteNodeTransport changes in Fleet control_plane are retained.

## What is actually wired

**Independent journal high-water mark.** `memory/anchor.py` adds a CAS-only
adapter over NEW connections to the existing OrganizationStore database.
`org_journal_heads` is additive schema, not another database, executor, policy or
budget. The anchor stores a generation and exact signed-snapshot hash per
namespace/task. A valid signature no longer suffices to load an old journal when
that journal is enrolled in this mode.

TaskJournal start/load/save, V3ExecutionBridge, FleetExecutionBridge, skill trace
readback and the actual BCC organization service propagate the same witness.
Anchored journals use schema 4; schema 3 remains the existing unanchored mode.
An anchored journal without its witness is rejected, not silently downgraded.
The head is committed before publishing the fsynced snapshot. A crash in that
window requires reconciliation rather than replaying an old irreversible intent.

**Fresh dispatch authority.** UniversalComputerAgent detaches the full action,
checks its fingerprint around callbacks, rechecks current policy inside the
existing effect guard, and rejects new ASK without an approval. CompoundRunner
records durable intent after these pre-dispatch checks but before execution.
A denied action no longer leaves a phantom irreversible intent. A real crash
after dispatch still leaves explicit uncertainty and cannot silently replay.

The BCC adapter reads the current agent permissions/enabled state from the
canonical BCC database. Approval previews retain a full digest at their front,
binding normalized tool arguments, implementation generation, the complete
action/expectation/scopes, task, run and agent. Two long requests with identical
500-character readable prefixes cannot reuse an approval. A consumed approval
is rechecked for revocation and identity without consuming it twice.

**State-aware dispatch.** `BoundVisualDispatch` connects the existing semantic
state guard to the actual UCA pre-dispatch hook. Fresh capture runs inside the
host-owned execution guard; missing input ownership or changed window/document/
revision/state rejects dispatch. A real OS accessibility/input-lock adapter is
still required for Windows desktop acceptance. This is not a new UI executor.

**Context/measurement integrity.** Stable text dedup no longer drops a later
mandatory record because an optional copy appeared first. Invalid numeric metrics,
NaN, infinity, negative/bool/overflow inputs and non-finite retention ratios cannot
produce a promotion claim. Zero baselines cannot certify preservation. These are
measurement-code tests, **not evidence that a real model retained 98% quality**.

## Opt-in and migration

The default remains unchanged. On a clean, explicitly chosen canary data root,
with existing V3/Organization flags already enabled, the operator can select:

```text
BOSSMAN_V4_JOURNAL_ANCHOR=1
```

This patch does not enable the flag, standing V5 autonomy, paid calls or remote
Fleet. It does not auto-enroll old journals. A schema-3 history might itself be
an old valid copy, so automatic migration would undermine the new guarantee.
Keep existing missions in their current mode until an operator reconciles actual
effects. Do not delete journals/anchor rows to force a clean start.

Protect the canonical database and evidence signing key from model/tool writes.
Keep anchor state OUTSIDE journal-only backups. Rolling back BOTH the canonical
DB and the journal, or a privileged actor changing both, is outside this local
witness guarantee. It requires a separately protected monotonic witness/TPM or
external service. No claim of hardware anti-rollback is made.

Rollback: pause admissions, retain canonical DB and journal snapshots, reconcile
ambiguous effects, and keep schema-4 histories read-only if reverting to an older
reader. Never turn the flag off and treat schema-4 history as legacy trusted state.
A crash between witness commit and snapshot publish fails closed; automatic
repair of that gap is deliberately not provided.

## Reproduction and tests

New Core tests:
`test_v4_journal_anchor`, `test_v4_effect_boundary`, `test_v4_context_retention`,
`test_v4_bcc_authorization`, `test_v4_anchor_bridges`.
New CC test: `test_v4_organization_anchor`.

They exercise actual temporary files, canonical SQLite/approvals, two logical
in-process Fleet nodes, child-process termination, restart, complete old signed
snapshot rollback, deletion/recreation, concurrent CAS, grant revocation, changed
expectations, visual drift, and read-only skill extraction. No LLM calls are made.
The dedicated workflow records tested SHA, event, run and attempt; a PR merge
checkout is not presented as proof of a different source or target commit.

Local development uses Linux/Python 3.13 on an extracted integration source plus
these changes, not a complete current-Fable checkout. The edited baseline files
match current Fable; untouched dependencies may differ. Full current-tree proof
must come from the new exact-SHA workflow and the existing required CI. Completed
local result logs are supplied in the author handoff; interrupted broad runs do
not count as passing evidence. Supported 3.11/3.12 and Windows are CI targets,
not predeclared successes.

## Remaining V4 release work

The existing `EPOCH_4_PLAN.md` remains authoritative. This pass does not certify
M0–M11, real Windows computer use, application-wide UX, remote production Fleet,
24-hour soak, migration/canary rehearsals, 100 paired benchmark episodes, 3x
throughput/cost/intervention targets, or real Intelligence Preservation. Current
policy checks are at the host dispatch boundary; arbitrary external services
still need their own idempotency/authorization semantics. The witness does not
make distributed effects transactionally atomic.

No coverage threshold, test expectation, sandbox restriction, skill promotion
rule or canonical completion check has been lowered to declare a release.
