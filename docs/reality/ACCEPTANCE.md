# Deployment acceptance — Fable must run after wiring

Unit PASS is necessary but not a substitute for these host-bound tests.

1. Core and BCC: final model text says done, no receipts → cannot become completed.
2. Core and BCC: valid controlled file mission → actual file read proves expected content.
3. Compound resume: changed args/target/expected value/actor/run → reject full-IR mismatch.
4. Process killed after a controlled external write, before receipt: restart reads target;
   confirms existing effect; action counter remains exactly one.
5. Process killed before external write: absence alone cannot retry while old request may
   still arrive. Demonstrate terminal-attempt reconciliation before any retry.
6. Fleet: expired/stolen owner cannot dispatch or finalize; another worker cannot reclaim
   escrow via ordinary retry. Confirm checks on shared backend, not only SQLite.
7. Evidence: wrong mission, run, code SHA, verifier identity, expected tree or stale timestamp
   cannot satisfy completion. Reviewer and executor cannot share effective identity.
8. Budget: Core and BCC racing paid reservations cannot exceed existing GLOBAL owner cap.
   Test retries and crash reservations; an ambiguous paid call remains charged/reserved.
9. Privacy: a PUBLIC derived fact with a LOCAL clinical dependency cannot reach cloud.
   Test actual provider egress path, not just support-library functions.
10. Feature flag/module error: opted-in runs fail closed through ALL finalization paths.
    Existing nonparticipating tasks retain their prior behavior and protections.
11. Routing: quarantined skill cannot enter through fallback routing or Fleet reassignment.
12. Benchmark candidate changes suite digest or regresses a previously passing case → no promotion.
13. Post-state divergence persists a restricted autonomy state across worker restart.
14. Controlled git mission: independently inspect bare remote SHA and exact patch/tree;
    locally queued push or Git command text alone cannot prove remote change.
15. Packaging: both installed applications import the module on the actual user's runtime.
    Run the existing regression suites at the final exact commit SHA.

Report each as PASS / FAIL / BLOCKED / NOT_RUN with evidence. Live hardware/API checks
must be labeled separately. No benchmark self-approval, baseline rewrites or skip inflation.
