# V4 integration candidate

Base: `d6b43cea0a1127bba7fa2cdabbd80dfa6da681bc` from the primary branch.
The original user checkout at `279eeb6` and its untracked work were preserved.
This candidate uses the isolated `codex/astra-v4-integration` branch.

## Provenance and ownership

* Video Studio: exclusive feature sources from `ac2cde8` (following `19c0c70`
  and `dfce725`), then targeted closure fixes. Engine/resource integration was
  reconciled with primary executor admission and strict finalization; no old
  engine was substituted for the primary version.
* Web Designer: exclusive feature files from `54947dc`, including the preview
  isolation and history fixes in `10f0766` and `229f12e`. Shared UI registrations
  retain the current Control panel and load-time improvements.
* Windows evidence persistence: the binary-write/fsync hunk already present in
  `dbb65a298944acfdb0cedfa8b4cbfb39840e8a4e` was adopted and independently tested.
  Reality Compiler was not imported.
* Primary engine approval/tool-loop forensic failures remain Fable's lane.
  Remote primary was checked before engine integration; it remained `d6b43ce`.

## Ranked closure queue

| Finding | State | Evidence |
| --- | --- | --- |
| Current-head Command Center execution-truth/approval failures | OPEN | Baseline CI 33991192813; full local run also failed and timed out |
| Core full-suite/real Linux sandbox acceptance | OPEN | Baseline CI 33991192864; local Windows full run does not certify Linux |
| Video/Web feature branches absent from primary | FIXED_LOCAL | Source provenance above; both native UI registrations included |
| Binary evidence key changes on Windows restart | REGRESSION_PASS | Forced LF/CRLF/control-byte persistence test; ASTRA portable Core subset |
| Terminal cancellation leaves Windows child executing | REGRESSION_PASS | Real delayed child effect fails before fix, absent after tree termination |
| Verified export blocked by critical-hook decode/hash timeout | REGRESSION_PASS | Verification outside hook; current-run file identity receipt; recovery and tamper tests |
| Media derivative burst and repeated hashing | REGRESSION_PASS | Concurrent prepare dedupe, bounded semaphore, change-sensitive hash identity |
| Video duration disagreement and non-finite values | REGRESSION_PASS | Canonical Python/JS duration tests and real renderer tests |
| Old video worker releases successor's resources | REGRESSION_PASS | Fence takeover injected immediately before release SQL; conditional update rejects it |
| Web Designer silently rewrites unrelated HTML | REGRESSION_PASS | Source-span patches; complex HTML preservation; hostile/parser tests |
| Private project routes to cloud or preview inherits authority | REGRESSION_PASS | Local-only unavailable model blocks; real Chromium isolated preview test |
| Project switch/async save loses or misroutes edits | REGRESSION_PASS | Two-project Chromium save/switch/export/restore/reopen test |
| Task detail returns previous run's successful result | REGRESSION_PASS | API rejects stale result; current result only, history retained |
| Task list performs one run query per task | REGRESSION_PASS | 20-task request uses two task/run reads and zero writes |
| CRLF source fingerprint differs from real file bytes | REGRESSION_PASS | LF/CRLF/invalid-UTF8 raw-byte SHA regressions |
| Safety dashboard import creates financial execution objects/keys | REGRESSION_PASS | Optional SDKs forbidden during import; execution routes remain denied |
| Core coverage checkout omits historical benchmark SHA | FIXED_LOCAL | Full-history checkout and explicit fixture fetch; CI verification required |
| Renderer CI silently omits FFmpeg | FIXED_LOCAL | System FFmpeg/ffprobe install, executable versions, mandatory dependency gate |

## Acceptance limits

This file is a change/provenance record, **not a release certificate**. Full six
workflow acceptance on one final SHA is required for application closure.
Existing required coverage thresholds and execution-truth gates are retained.
Optional media adapters/weights, a real sandbox host and Windows file-symlink
privilege are reported as unavailable when absent, never as passing evidence.
No paid provider call is claimed. Provider Treasury execution reuses the existing
governed/capped adapter; deterministic tests do not certify a real paid edit.

Run XML/logs and the final exact-SHA report are kept in the local `.proof`
directory, outside the committed source tree. Historical feature-branch reports
are provenance only, not evidence of current candidate acceptance.
