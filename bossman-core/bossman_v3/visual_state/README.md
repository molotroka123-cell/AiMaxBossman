# Visual observation foundation (Epoch 4 / M2)

This module is an isolated, opt-in foundation. It does not activate computer
control, execute actions, approve actions, or certify Windows support.

## Compatible fusion and strict action state

`VisualStateEngine()` preserves the existing snapshot/compact interface and
conflict reporting. It now rejects **each** stale fragment, all future times,
naive timestamps, and invalid confidence/freshness values. An old DOM cannot
be refreshed by adding a new screenshot. This intentional hardening can reject
previously accepted unsafe inputs; callers must reobserve the entire state.

`VisualStateEngine(require_identity=True)` additionally requires all fragments
to carry an identical `StateIdentity`, at least one structured source, sufficient
structured confidence, and no structured conflicts. `StateIdentity` contains
application/window/document IDs, navigation generation and state revision.
Adapters must advance revisions on focus, modal and relevant state changes;
document/navigation generations must not be recycled across navigations.
Structured comparisons use typed JSON equality (`true` does not equal `1`).
Strict snapshots detach caller-owned payloads and freeze nested containers.
`compact()` continues to return JSON-serializable data for those snapshots.
Vision fields remain namespaced hints and cannot satisfy semantic preconditions.

## Opt-in guard integration

`SemanticActionStateGuard.bind(action, fragments, required_state=..., now=...)`
creates an immutable binding to an existing `TypedAction` and semantic
preconditions. For an editor save button the adapter might require element ID,
label, enabled state, active focus and absence of a modal. The required fields
must come from structured observations. The action digest covers type, nested
arguments, scopes, side-effect class, idempotency key and source.

Immediately before dispatch, call
`guard.check(action, binding, fresh_fragments, now=...)`. It refuses changed
arguments, identities, semantic preconditions, stale/future fragments, mixed
windows, missing structured fields or conflicting observations. A result of
`None` only means this state check passed; it is not a completion receipt.

The production adapter must collect trusted observations itself and perform
this check **inside the existing execution guard**, while owning desktop input
and current window state. The adapter must own an immutable execution copy of the action across the
check-to-dispatch boundary. The guard checks its digest both before and after
consuming observations, but cannot protect a caller-owned mutable action after
returning. The collector and executor must agree on window,
element and revision semantics. A model cannot choose adapter identities or
supply its own binding as permission. A pure Python check cannot stop another
process changing OS focus between observation and click. That race requires
platform/adapter ownership and live certification, which remain unimplemented.
Existing policy, approval and independent post-effect verification still apply.
Never persist or replay screen coordinates after navigation or resume without
fresh resolution. This patch changes no UniversalComputerAgent call path.

## Evidence and remaining gates

Portable regression tests: `bossman-core/tests/test_v4_visual_state_guard.py`.
They use deterministic fragment fixtures, not actual browser/Windows state.
The original baseline accepted stale DOM + fresh screenshot and future DOM;
both reproductions fail closed after this patch. Adversarial cases cover
cross-window/document/generation state, semantic changes despite reused revision,
vision authority confusion, confidence, mutable arguments/expectations, and
strict snapshot mutation.

M0 closure, Mission IR integration, input ownership, real multi-application
Windows missions, snapshot privacy/retention and artifact-reference bounds remain
release gates. No claim that M2 or G03–G05 is complete.

Rollback: remove the opt-in guard from a disabled adapter or revert this module
batch. There are no data migrations or durable writes. Disabling a future
adapter must park queued coordinates rather than replay them.
