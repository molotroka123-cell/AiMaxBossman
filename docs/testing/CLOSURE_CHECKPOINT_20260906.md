# Closure checkpoint — integration, not epoch completion

## Provenance

Candidate: `integration/continuity-steward-closure-20260906`.
Primary baseline: `876e2434b94a84be6bb534fd53d02f5ddc2c4909`.
Published hardening: `9d3f82ac0d26c5b88b408f0c470a3acfae700f98` (+6 commits).
Published Steward: `3f8c7171ff1f5033cdcfff426ca19c22cdbba124` (+11 commits).
Combined through PR #22: `945b8f86165ae5a76a32f1bd43a2149e1e1ac0ac`.
Source branches and primary have not been rewritten. This is NOT all old branches.
Additional Web/assistant changes on `954785f1` and cosmetic candidate `beea20fb`
are NOT claimed integrated by this checkpoint. Inspect their diffs before reuse.

## Confirmed defect and correction

Hardening root CI run `34028670388`, job `101474113063` failed collecting
`tests/test_fable_context_ablation.py`: `ModuleNotFoundError: bossman`.
Root CI installs the shared package, not Core. Move this Core-only test byte for
byte into `bossman-core/tests/`; no assertions removed, skipped or weakened.
Core's existing full suite discovers it using its declared dependencies.

## Test supervision hardening

`tools/pytest_watchdog.py` no longer interprets silence for 30 seconds or a printed
summary as completion. A quiet legitimate test can run to its explicit deadline.
Return real process exit; deadline=124, interruption=130, setup failure=2.
Retain a bounded tail, redact labelled secrets, optionally stream to a new log
without overwriting a previous run. Cleanup binds observed descendants to process
creation time via psutil, never scans/kills all python/node/chrome/ffmpeg processes.
Do not consider this a hostile-process containment sandbox: fast detached children
that escape before discovery still need OS-level job/cgroup isolation. No owner
computer process has been stopped by this session. Logs still require PII review.

## Checks actually executed locally

Linux, Python 3.13.5; source archive from Fable artifact `9987991521` at `9d3f82ac`.
Focused selection: watchdog + exact-SHA evaluator + MissionIR + ObjectiveSpec
existing foundation suites: **262 passed** after the watchdog correction.
These local counts are NOT the combined V5 integration regression, NOT Windows,
NOT full root/Core/Command Center and NOT any model intelligence measurement.
One initial watchdog regression test caught incomplete Bearer redaction; fixed
before committing, then all focused tests passed. No model API call was made.

The checkpoint CI archives the exact combined code and exercises shared/V5
contracts plus the relocated Core ablation. Full workflows remain authoritative.
Queued/in-progress runs are not PASS. The actual SHA is recorded by CI itself.

## Remaining gates

V4 M0/M11 and V5 N0 are not accepted by this merge. Keep standing autonomy off.
V5 scorecard remains V5_NOT_COMPLETE: canonical ports, supported Windows,
24h soak, live rollback, full egress and real same-model retention remain gates.
Fixture-policy/Treasury ports and in-process H01-H10 are not production proof.
Fleet remains EXPERIMENTAL. Do not alter historical scorecards to claim closure.

Use `handoffs/GLM53_RESIDUAL_ACCEPTANCE.md` for the remaining new/unproven functions
plus mandatory core smoke, safe host preparation, telemetry, stress and final CI.
