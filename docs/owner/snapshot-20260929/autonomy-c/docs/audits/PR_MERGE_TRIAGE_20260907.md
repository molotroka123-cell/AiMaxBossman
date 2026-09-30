# PR maintenance audit — 2026-09-07

This records owner-requested PR triage. It is not a V3/V4/V5 release certificate.
No owner-PC process, working directory, deployed runtime, credential, branch protection or Git history was modified.

## Actual actions

| PR | Inspected state / action | Evidence boundary |
|---|---|---|
| #1 | Kept OPEN; unsafe to merge as-is; review comment 5562630538 | Head 143f87f8456a0446a366a96f721bbe16b0fd3897 conflicts; a Pydantic class-definition failure was independently reproduced locally |
| #3 | Already MERGED before this session | setup-python 5 -> 7; merge d05318bbb1592638a7b0271d054a0cd1ec8ba995 at 2026-09-06T22:22:27Z; not our new merge |
| #4 | Rebase requested, comment 5562620437; NOT merged in this pass | checkout 4 -> 7; inspected head d5e3734113d5a7093d72580be47618a90b02819d conflicts and has a rebase notice |
| #5 | MERGED by this session with expected-head guard | cf1340bf19eb6b669a7979f1a9bb626bab4b4a72 -> merge ac245c738c261bd616b3d2ef1ebfac670c1e31eb |
| #6 | Rebase requested, comment 5562621665; NOT merged in this pass | upload-artifact 4 -> 7; inspected head 26d567690e5873400c3cd91f421baf93864ce507 has historical failed checks; needs current-base evidence |
| #26 | CLOSED administratively, NOT merged; comment 5562633998 | Historical checkpoint head 30fce6c44865ab2ca064644a6c0e21fcd5be4551 and its branch/files retained; findings are NOT declared fixed |
| #37 | Kept OPEN/DRAFT; triage comment 5562631978 | Active implementation, not a dead branch; current PR runs include failed Root/Intelligence and an unfinished CC run |

## PR #5 review

Reviewed the complete one-line diff in `.github/workflows/bossman-v2-repair.yml`:
`peter-evans/create-pull-request@v5` -> `@v8`. Current workflow uses ubuntu-latest and compatible action inputs. No permission, test threshold or approval gate was changed.

The prior head's Core and Command Center workflows succeeded. Auto-Repair run 33408480010 failed at `Fix PostgreSQL Schema` BEFORE the changed action; `Create PR if fixes needed` itself completed successfully (job 99541874835). This is limited historical compatibility evidence, not a successful new combined test run. Official v8 notes require Actions Runner >=2.327.1 for Node24 on self-hosted installations; do not extrapolate the hosted-runner review to unknown self-hosted machines.

NEW_MERGE_EXACT_SHA_CI=NOT_CERTIFIED_IN_THIS_PASS.
Do not reuse an older green badge as acceptance of merge ac245c7 or of this audit-publication commit. Do not call every major action update a security fix without examining its changes.

## PR #1: merge would introduce a startup defect

Created 2026-08-30, not a month before this audit. The title says 10 findings but the body enumerates RISK-1 through RISK-11; that list is historical claims, not a current vulnerability census.

The diff defines TelegramMessage and TelegramCallbackQuery with BOTH `model_config` and inner `class Config`. The current declared dependency is pydantic>=2.7. Reproduced exact TelegramMessage class form with installed Pydantic 2.13.4 in an isolated Python process:

```text
PydanticUserError: "Config" and "model_config" cannot be used together
```

This fails while defining the class; the proposed definitions sit in `bossman/api.py`. This was a class-definition reproduction, not a full API/server execution. Replace with one v2 configuration and an explicit alias for `from`, then test the webhook contract and auth. Preserve the canonical newer implementation rather than replacing the whole API file.

Additional static review items, not newly executed tests: private slowapi method is awaited and rate limiting silently disappears when the optional dependency is absent; `.gitignore` removes explicit gateway/browser-auth/WAL rules and replaces `/projects/` with a recursive `projects/` pattern. Port useful missing protections individually with regressions; do not merge this old file replacement wholesale. No history rewrite is part of this maintenance.

## PR #37: concrete current CI blocker

Inspected head 798e634383c7385099a5d5b8cf7a55f2bbffbc73. Its root run 34063483687 tested SYNTHETIC MERGE 2739c28aa21cb3efb868157cb17770ef3f1f63f0 (798e634 into 1bb39bf), not today's default branch.

Read job101568080611 through exit1:

```text
956 passed, 1 failed, 2 skipped
failed: tests/test_v5_human_speed.py::test_objective_cas_under_10ms_and_stale_write_is_denied
n=100; percentile=100; value_ms=82.191313; limit_ms=10.0
```

This is a max/P100 latency check, NOT p95. CAS/reopen/stale-write correctness assertions before the timing check passed. Investigate actual costs and runner scheduling with retained raw samples; do not hide the result using skip/xfail, threshold inflation or rerun-until-green. A revised performance contract must be explicit and retain separate representative target-host acceptance.

Core, Editors, Media/Fleet, ASTRA and Auto-Repair PR runs succeeded at inspection. Intelligence run34063483703 failed; CC run34063483699 was still running. This document does not diagnose the Intelligence failure beyond its observed status. Root failure caused its downstream README/registry/compile/secret/whitespace steps to skip, not pass. A fresh combined-base check is required after dependency merges.

## Historical checkpoint preservation / cleanup

PR26 targets the retired integration/continuity-steward-closure-20260906 base rather than the current default. Its 47 changed files include code, tests, logs and evidence. They were NOT blindly merged or deleted. The source remains on kimi/final-residual-closure-20260906; reports under docs/acceptance/20260906-kimi-779bb44 remain recoverable at the recorded SHA. Closing the checkpoint does not settle restart/M1, CFR or host-environment findings. Carry remaining work into active PR37/current acceptance reports with selective, tested ports.

No branches were deleted. Current connector actions expose no branch-delete operation; the local environment has no gh CLI. Plugin discovery found the existing GitHub connector but no additional configured GitHub administration action. No protection was removed to work around this limitation. Active acceptance, security and V5 branches are specifically retained. Merged Dependabot branches may be deleted only after rechecking their current refs, ancestry and absence of dependent open PRs; those deletion preconditions were not certified here.

## Source locators

- https://github.com/molotroka123-cell/AiMaxBossman/pull/1
- https://github.com/molotroka123-cell/AiMaxBossman/pull/3
- https://github.com/molotroka123-cell/AiMaxBossman/pull/4
- https://github.com/molotroka123-cell/AiMaxBossman/pull/5
- https://github.com/molotroka123-cell/AiMaxBossman/pull/6
- https://github.com/molotroka123-cell/AiMaxBossman/pull/26
- https://github.com/molotroka123-cell/AiMaxBossman/pull/37
- https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34063483687
- https://github.com/peter-evans/create-pull-request/releases/tag/v8.0.0

VERDICT=PARTIAL_MAINTENANCE_COMPLETED_WITH_EXPLICIT_BLOCKERS
