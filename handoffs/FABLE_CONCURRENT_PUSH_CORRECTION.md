# Fable / Claude — correction on resumption

The owner reports Claude limits; do not assume another session is still executing.
The current integration candidate is `integration/continuity-steward-closure-20260906`.
It preserves hardening `9d3f82ac` and Steward `3f8c7171` through PR #22, `945b8f86`.
Fetch exact current refs before any work. The existing primary/source refs were not reset.

Do not repeat the already integrated objective modules, media/Fleet hardening,
context/authority tests or the root/Core ablation-placement correction.
The ablation test moved from root tests to bossman-core/tests byte for byte.
Read `docs/testing/CLOSURE_CHECKPOINT_20260906.md` and
`handoffs/GLM53_RESIDUAL_ACCEPTANCE.md`.

Continue in your own branch/worktree from the candidate. Never force-push or
replace the integration tree with a stale local snapshot. Inspect new diffs,
claim a narrow non-overlapping boundary, implement and test there, then open a PR.
Before each checkpoint fetch again; reconcile semantically, preserving stricter
policy, verification, privacy, budgets and restart safety. Do not weaken tests.

The next owner run uses OpenCode with the actually available GLM 5.3 provider/model.
It focuses on new/unproven interactions plus mandatory main smoke, safe test-process
cleanup, telemetry and bounded stress. No new epochs or duplicate kernels.
No runtime activation or release claim until the required current-SHA gates pass.
