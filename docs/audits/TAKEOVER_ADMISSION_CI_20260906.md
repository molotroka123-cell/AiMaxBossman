# Admission / CI takeover checkpoint — 2026-09-06

Base: `2919b8319c8da512c5a54408c35db04bea858f07`.
Exact source archive Git tree reconstructed: `f1b106aa5f026f820c72a30e08e71961867c42cc`.

## Implemented

* Terminal contextual read-only DENY runs before ASK, using existing owner roots
  and scratch ownership. Allowed host commands still require owner approval.
  The same contextual check runs again at execution, and existing handler
  containment checks are retained. Timeout/error is DENY, not permission.
* Owner Pause/Take control remain resumable and retain the stale-approval
  diagnosis. Stop stays terminal. Old assertions expecting FAILED for owner
  control now require PAUSED/USER_CONTROL, zero dispatch and zero used steps.
* Observation-expiry regression uses a module-local controlled monotonic clock,
  not a 1 ns wall-clock assumption that failed on Windows. This test correction
  does NOT close external-UI freshness AT-03.
* Skip registry regenerated from the actual current test tree (130 entries).
  No skip, xfail, coverage threshold or safety gate added/lowered.

## Local verification and limitations

Python 3.13.5/Linux. The source archive SHA-256 was verified, the full Git tree
was matched before editing. Old terminal regressions reproduced: 2 failed,
21 passed. Old owner Take control assertion reproduced: 1 failed, 1 passed.

After corrections, independently supervised processes:
Core targeted owner/recovery/evidence/audit001: **167 passed, exit 0**.
Command Center terminal/approval/finalizer/policy: **87 passed, 4 skipped,
exit 0**. The skips are existing Chromium/container availability cases; not
live acceptance. The two selection totals are distinct, not a full project run.
Secret scan and whitespace check pass.

A broad parallel root diagnostic encountered environment/watchdog failures;
its result is not presented as a new full root PASS. Required new-SHA CI and
owner Windows/UI/model checks remain separate evidence. Three parallel test
processes were real; no three-AI-agent swarm or delayed monitoring is claimed.

AT-01 (complete all obligations, not merely any effect), AT-03 (external UI
freshness), PR33/36 integration, measured same-model intelligence, live human
comparison and V4/V5 release acceptance remain open. Score target 8000 is not
awarded for this patch or by editing a scorecard. No owner runtime was restarted.

The last fetched owner-PC evidence branch was `kimi/final-residual-closure-20260906`
at `30fce6c44865ab2ca064644a6c0e21fcd5be4551`. That historical report is not evidence
for this candidate. New reports must be tied to their tested SHA, not silently
applied to an already running acceptance instance.
