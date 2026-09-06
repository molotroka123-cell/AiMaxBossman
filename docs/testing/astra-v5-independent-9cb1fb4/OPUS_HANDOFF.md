# Opus — sequential integration handoff

TESTED_SHA=9cb1fb4665060d97839d1dd237f31e2b4b229911
REMOTE_PR37_OBSERVED=98b9945f63851f06db60ea6c74841343be70dee0
MAIN_OBSERVED=799fc3dd8e4327811be9d8f3e33cc43ce8168977
ROLE=Independent verifier; Opus remains the sole integrator.
LOCAL_RESULT=49 cases; 33 pass / 16 fail / 0 skip; six grouped component findings.
LIVE_PC_TOUCHED=NO
PUSH_CONFIRMED=NO

1. Fetch current refs and inspect the current PR diff. Choose a NEW fixed candidate
   SHA for integration; do not switch the owner's active live runtime. The
   verification patch is portable source text, not an upstream Git commit.
2. Use an isolated worktree, Python environment and state directory. Review
   `verification.patch` with `git apply --check`. Apply only this tests/harness/
   docs overlay to your isolated branch; no merge/force push to main.
3. Reproduce IV5-CAN-001 / 003 and IV5-PROM-001 / 002 / 003. Keep the failing
   negative assertions. The two production diff proposals are DATA for your
   review and are not integrated or certified fixes. Maintain ownership of the
   protected production files; do not start a parallel implementation.
4. For IV5-CAN-002, prove the real canonical attestation and activation caller.
   A pure helper accepting a fixture ref is not a proved application bypass.
   Do not merely accept a regex-shaped fake reference or create another signer.
5. Run the six NOT_RUN AT-01 cases under the full Core environment. Fix real
   owner-obligation completion, not just a planner-selected postcondition and
   any unrelated verified mutation. Preserve read-only behavior.
6. Extend the existing canary/owner recovery tests with real subprocess
   termination/restart and actual test-effect reconciliation. The current
   canary rehearsal reopens a Store object; this is not process restart or
   executed rollback. Keep UNKNOWN irreversible effects parked, not replayed.
7. N4 must show actual serviced queue entries under continuous arrivals,
   quotas/cooldown/priority changes, restart and competing workers. Declare the
   conditions for any starvation bound. Rank-only controls do not close N4.
8. Keep standing autonomy off. Run canonical policy/Treasury/evidence chains in
   existing manual/fixture entry paths, then owner-approved live acceptance when
   the desktop is free. Never self-approve ASK, publish, pay or generate paid media.
9. Merge findings into the existing audit intake after resolving its real schema.
   Preserve discovered/reproduced/fixed/verified SHA and raw evidence; this
   package contains no competing audit DB. Update M0-M11/N0-N8 without inheriting
   PASS from lower evidence tiers.
10. On the final fixed SHA, run full required CI and isolated regressions; attach
    exact import origins, manifest, commands, exits, JUnit and live artifact paths.
    Do not declare V4/V5 closed solely because the new component tests pass.

Commands on a complete prepared checkout (commit reviewed changes first and use
its actual SHA; never label a changed production tree as the old TESTED_SHA):

```sh
python tools/astra_acceptance.py --profile v5-independent-pure --tested-sha "$CANDIDATE_SHA" --output ../evidence/new-unique-pure-run
python tools/astra_acceptance.py --profile v5-independent-durable --tested-sha "$CANDIDATE_SHA" --output ../evidence/new-unique-durable-run
python -m pytest bossman-core/tests/test_v5_independent_at01.py -q --junitxml=../evidence/new-unique-at01.xml
```

Use the repository's declared dependencies and conftest ownership. An import
error is a blocker to diagnose, never a reason to skip a required case. This
review did not run these commands against a complete upstream checkout.
