# Evening takeover — targeted safety checkpoint, not epoch acceptance

Historical audited main: 22e2ea304bcc2f4f51b24af41c881f0f9353d827.
Current parent at publication: f4b974ddc040ba17fd5d0f5019be058d613b6e7d.
This preserves PR32/34 merges and the existing total-local-acceptance specification.
AT-04 first landed in main at 50d127f581d39b02d19b4c3f30262eff040f2b02.
V4=NOT_COMPLETE; V5=NOT_COMPLETE; HUMAN_COMPARISON=NOT_RUN.

## Changes

AT-04: existing learning evidence ledger refuses capacity exhaustion rather than
evicting spent records; rejects corrupt/duplicate/malformed data; detects missing
initialized data; serializes cooperating processes across read/consume/write;
uses a unique fsynced atomic replacement; uncertain writes refuse promotion.
Legacy flat JSON retained. Default is still in-memory unless the durable path
is configured. Trusted directory required; consistent ledger+marker rollback is
not detected. No new permissions, automatic promotion or model-weight changes.

AT-02: preserve PAUSED/USER_CONTROL and terminal states across recovery; old
WAITING_APPROVAL becomes PAUSED and requires explicit Resume/current approval.
Reload after CAS conflicts, retain owner pause and revoke lease in finally.
A stale approved callback cannot turn parked state into FAILED or execute.
This does not solve every unknown in-flight effect/replay case.

Two old ledger tests explicitly expected unsafe fail-open/capacity behavior;
they now assert refusal AND retention. The mid-approval test retains no-effect
and zero-step assertions while requiring resumable PAUSED. No skip/xfail or
coverage reduction. Unrelated PR32 observation-aware policy and CANCELLED
reason persistence are preserved in the current manager preimage.

## Observed tests

Isolated Linux, Python3.13.5, actual files and independent interpreters with
fixture observers/planners. Before fixes: ledger 9 failed/2 passed; owner
recovery 4 failed/3 passed. These are cases, not 13 independent root causes.
After: 23 focused ledger cases exit0. Combined affected learning/operator/
recovery/audit001 selection: 155 passed, exit0. Latest source after reconciling
PR32 manager/policy changes: same 155 passed in7.91s, exit0.
Eight independent interpreters test both same-key single-use and distinct-key
preservation. Fresh interpreter tests verify owner states and zero actions.
Additional parallel test selections overlap this total and are NOT summed.

Source evidence came from GitHub Actions artifacts 9992597224 (22e2ea),
9993129262 (PR33 synthetic merge917a36df) and 9993647246 (PR32 synthetic merge
69cbcda). Relevant production preimage blobs were verified against current main.
This is targeted local evidence, NOT full current-main CI or Windows/UI testing.
Current-SHA Linux/Windows residual CI is configured, not claimed complete.

## Remaining and concurrency

AT-01 standalone false COMPLETE and AT-03 stale observation reuse remain open.
N0 and unattended autonomy are not accepted. PR33 interrupt/import changes are
not blindly substituted over main. Its Root/Core PR checks passed; Command
Center py3.11 failed while py3.12 passed. Intelligence lacks measured evidence.
PR32 work is merged; PR34's actual merge changes skip registry, not all the UX
promises in its description. New local safety changes preserve both merges.
No running owner-PC process or test worktree was changed.

No callable AI subagent launcher or autonomous delayed-code scheduler was
available. Concurrent tests are real TEST PROCESSES, not a claimed AI swarm.
No 20-minute automatic audit/coding task was installed. The next run starts at
`docs/release/FINAL_EVENING_RUN_20260906.md`, supplementing the already published
`docs/testing/TOTAL_LOCAL_ACCEPTANCE_20260906.md`.

## Preliminary diagnostic estimate, not release percentage

Historical equal-weight baseline in
`docs/audits/astra-7b1377a/02_SCORECARD_10X10.md`: 5050/10000.
Current engineering judgment: 6350/10000, uncertainty roughly +/-500.
Same ten axis names/equal weighting, but this is not a freshly complete
10-axis attestation or calibrated benchmark. The older ffd7d25 scorecard was
not independently reopened in this narrow pass. Numerical +1300 versus the
historical5050 is indicative, not verified acceleration or percent completion.

| Axis | /1000 | Qualification |
|---|---:|---|
| Execution Truth |600| Contracts improved; standalone COMPLETE still open |
| Security |650| Ledger hardened; broader security/egress still require evidence |
| Tooling/OS |700| Real media/Web adapters; target-host UI unproven |
| Organization |600| Integrated contracts; long-horizon organization unproven |
| Fleet |600| Local TLS/auth evidence; not production multi-node |
| Memory/Context/Learning |650| Provenance/scope and ledger; no model retention proof |
| Testing/CI |650| New hostile/process tests; current-main CI not yet certified |
| Observability |600| Audit catalogue plus merged telemetry; live corpus unqualified |
| Treasury |650| Bound proposals/reservations; cross-service acceptance remains |
| UX/Missions |650| Safer recovery and shared basis; user-flow acceptance remains |

Next meaningful qualification is one installed verified SHA, not more unrelated
green reports. The score does not supersede open safety findings or NOT_RUNs.
