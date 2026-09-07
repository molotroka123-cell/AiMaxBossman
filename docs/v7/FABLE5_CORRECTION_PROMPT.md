# Fable 5 — narrow correction before V7

Repository: `molotroka123-cell/AiMaxBossman`

Do not start V7 implementation.

Finish the active V6 freeze line first.

Current V6 branch at the time of this handoff:
`v6/velocity-phase0-baseline-20260907`

Last observed V6 HEAD:
`5f75dc55ff0376ef7774526cbed88b50efd638ff`

Re-fetch before acting; repository truth wins if HEAD moved.

## What changed

Fable already:
- repaired README scorecard fail-closed behavior for `UNPROVEN` axes;
- ran a second sandbox Dashboard acceptance session versus owner session `6cbb17ce84db`;
- got 0 dead-click / 0 refused in the corrected detector and 4/4 tasks completed in the sandbox run;
- found and fixed V6 F3: resource planning/UI invented 128 GB before the first real measurement; current code now uses measured/live memory or fails closed.

## Do not redo

- lazy-page V6 optimization;
- Trading Lab lazy-wiring test fix;
- Golden Mission approval-watcher harness fix;
- old dead-click false positives already attributed to modal/toast detector blindness;
- closed Video CFR harness work without a new product repro;
- broad architecture work for V7.

## Finish now

1. Re-fetch exact V6 HEAD/tree.
2. Wait for or inspect the exact-SHA CI from the latest resource-truth commit.
3. If Command Center/root/Core/ASTRA/Solana/security shows a reproducible product/test-contract failure, fix narrowly and rerun.
4. Update `docs/v6/V6_FREEZE_REPORT.md` to the actual final tested code SHA.
5. Keep the following external evidence honest:
   - owner Windows acceptance;
   - real local-model/provider acceptance;
   - real Video Studio media + FFmpeg path;
   - hours-long session/soak;
   - GPU/unified-memory claims;
   - same-model intelligence retention where still required.
6. Do not manufacture passes for unavailable owner hardware/effects.

Final target:

`OPEN_REPO_P0=0`
`OPEN_REPO_P1=0`

and exact final verdict:

`PASS`
or
`REPO_COMPLETE_EXTERNAL_VALIDATION_PENDING`

Then stop V6 coding. Do not begin V7 unless the V7 README entry gate is satisfied and the owner explicitly starts implementation.
