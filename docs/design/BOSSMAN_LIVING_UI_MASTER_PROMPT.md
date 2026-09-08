# Claude Opus Master Prompt — Bossman Living UI Mini Update

You are implementing a deliberately narrow visual upgrade to AiMaxBossman.

FIRST RULE: DO NOT BREAK OR REOPEN THE CURRENT RELEASE WORK.

This is not an architecture rewrite. It is a presentation-layer mini update with a strict performance gate and rollback path.

## 0. Establish truth before editing
- Fetch the current branch/HEAD and current PR/release state.
- Read `docs/design/BOSSMAN_LIVING_UI_MINI_UPDATE_TZ.md`.
- Inventory the current Command Center routes, menu, browser tests, agent/status components and performance-relevant polling/timers.
- Record the exact SOURCE_SHA.
- Do not assume screenshots are implementation truth; current production behavior and tests are authoritative.

## 1. Performance baseline BEFORE UI changes
Run a repeatable baseline on the current UI. Measure cold/warm start, time-to-interactive, route switching, idle CPU/GPU/RAM where measurable, active-task resource use, memory growth, long tasks/dropped frames, console errors, failed requests, request/poll frequency.

Write the baseline artifact before implementation. If a metric cannot be measured in the environment, mark NOT_RUN; do not invent it.

## 2. Preserve every authority boundary
Do NOT change semantics of:
- AT-01 effect proof;
- AT-03 fresh observation/action boundary;
- permissions/approvals;
- budgets/privacy;
- provider routing/health/streaming;
- World State authority;
- OpenHands isolation/containment;
- signed QA relay;
- Apps policy;
- Video Studio CFR;
- N8/canary/rollback;
- mission completion/evidence truth.

The UI may DISPLAY existing state. It may not create, infer or upgrade authority.

## 3. Implement in small reversible commits
A. design tokens / calm glass treatment;
B. agent avatar component + lazy asset loading;
C. state-driven agent presentation;
D. lightweight micro-interactions;
E. Living Mode feature flag DEFAULT OFF;
F. optional Agent Room visualization driven only by existing events.

Do not delete routes. Do not rename API contracts. Do not reorganize backend modules for aesthetics.

## 4. Animation performance rules
- CSS transform/opacity first.
- SVG for simple indicators.
- no production GIF/video loops.
- no unbounded requestAnimationFrame loops.
- pause decorative motion when document.hidden.
- honor prefers-reduced-motion.
- no extra polling solely for animation.
- lazy-load noncritical avatars.
- cap concurrent decorative effects.
- WebGL only if profiling proves a real need and fallback remains available.

## 5. State truth
Allowed visible states:
IDLE, THINKING, WORKING, WAITING_APPROVAL, VERIFYING, ERROR, RECOVERING, DONE.

Map these to existing production state/events. If a state cannot be proven, show UNKNOWN/IDLE-safe representation rather than inventing activity.

Never show DONE, RECOVERED, HEALTHY or VERIFIED merely because an animation finished.

## 6. Security/privacy
Never render secrets, credentials, hidden reasoning/chain-of-thought, unredacted host paths, private raw context or unsafe provider payloads in agent detail panels.

## 7. Tests before default-on
Run all relevant existing browser/route tests. Add tests for:
- every old route still renders/reachable;
- Living Mode OFF reproduces stable Normal Mode behavior;
- reduced-motion disables nonessential animation;
- hidden tab pauses decorative animation;
- ERROR -> RECOVERING -> DONE follows actual events, never timers alone;
- no visual state can mutate mission authority;
- no additional idle polling introduced;
- avatar load failure degrades to static fallback;
- feature flag rollback is immediate.

Then run applicable freeze-critical regressions including AT-01, AT-03, provider health/streaming, approvals, Apps, signed relay, Video Studio, Web Designer, Trading Lab UNWIRED, OpenHands isolation and N8/canary regression tests.

## 8. Post-change performance comparison
Repeat the exact baseline methodology on the candidate SHA.

If visual changes cause material responsiveness regression, sustained idle resource increase, memory growth, console/network errors, task-throughput regression or visible jank:
- keep Living Mode DEFAULT OFF;
- remove/disable the expensive effect;
- retest.

Never lower the performance gate to ship the visual.

## 9. Current integration warning
Do not mix this design update into unresolved gateway/release integration work unless explicitly instructed. If the active release/convergence branch has semantic conflicts with main, keep the design work isolated and rebase/port only after the release integrator selects the final code SHA.

Do not use ours/theirs wholesale across gateway or release-critical files.

## 10. Final output
Report:
SOURCE_SHA=
CANDIDATE_SHA=
CHANGED_FILES=
NORMAL_MODE=
LIVING_MODE=
PERF_BASELINE=
PERF_AFTER=
PERF_DELTA=
ROUTES_REGRESSION=
CONSOLE_ERRORS=
FAILED_REQUESTS=
AT01=
AT03=
VIDEO_CFR=
OPENHANDS_ISOLATION=
N8_CANARY=
ROLLBACK_TEST=
DEFAULT_LIVING_MODE=
OPEN_BLOCKERS=

No claim of PASS without executable evidence. Keep the mini update visually ambitious but technically boring, reversible and isolated.