# Planned V5 surfaces — specifications, not live tests

Files here are preserved verbatim but deliberately **not** under a collected
test path. Each one specifies a surface that does not exist in production yet.
They are kept because the contract they state is correct and should be honoured
when the surface is built — not deleted, and not weakened into something that
passes against nothing.

## `test_v5_fleet_rollout_caller.py.txt`

Specifies an **objective-fleet rollout** door: `activate_broadly`,
`apply_broad_revision`, `rollback` on the activation gate, plus a
`revision_digest(candidates)` that binds every fleet member's candidate spec so
swapping one member changes the digest and a non-spec value is refused.

It is not a live test because that surface does not exist. Per
`docs/V4_V5_PRODUCTION_CALL_GRAPH_83a2a77.md` §A.4, `ObjectiveStore.revise` has
only test callers and `command-center/bcc/features/objectives.py` exposes no
revise route (`STANDING_AUTONOMY_ENABLED = False`, and the file ends with
`# tick отсутствует намеренно`). Inventing a production fleet rollout merely so
the canary would have something to gate is exactly the wrong direction, so the
missing methods were not stubbed.

The **live** production door for P0-1 is the Command Center skill promoter, and
its test is `command-center/tests/test_v5_canary_production_caller.py`.

Keeping this file inside `tests/` broke collection of the whole root suite with
`ImportError: cannot import name 'revision_digest'`, which is why it moved here
rather than staying red.
