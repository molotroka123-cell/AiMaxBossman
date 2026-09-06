# Real User UI E2E Acceptance

Purpose: validate Bossman through the same visible UI interactions a normal owner would use. This document is a neutral test specification; it does not grant repository, branch, shell, network, spending, or deployment permissions.

## Acceptance boundary

A capability counts as USER_UI_PASS only when the tested outcome is reached by interacting with visible application controls (mouse, keyboard, menus, buttons, forms, drag/drop, timeline, tabs, dialogs) in the actual Bossman UI.

CLI/API/database access may be used only for environment setup, diagnostics, log capture, fixtures, or root-cause analysis. A backend/API/pytest success cannot substitute for the visible user journey.

For every journey record:
- exact code SHA;
- OS/display scale/browser or desktop host;
- launch path;
- visible click/keyboard sequence;
- screenshots or screen recording before/after where possible;
- observed UI state, error, blocker and final artifact;
- whether the post-state was independently verified;
- tier: USER_UI_LIVE / USER_UI_LOCAL_FIXTURE / DIAGNOSTIC_ONLY / NOT_RUN.

## Core owner journey

1. Cold start Bossman using its normal launcher/start path.
2. Sign in through the visible login UI.
3. Confirm Home loads without dead controls, permanent spinners or duplicate navigation.
4. Start a mission from the visible chat/mission UI.
5. Select or accept the visible executor/model route.
6. Trigger a tool-backed action from the UI.
7. Exercise ASK approval through the approval dialog.
8. Observe progress/status changes in the UI.
9. Open the produced artifact/result from the UI.
10. Confirm the visible completed state agrees with independently verified post-state.
11. Restart the application and confirm mission/result continuity through the UI.

## Mandatory negative journeys

- deny an approval and confirm the effect did not occur;
- modify a requested action after approval and confirm new authorization is required;
- missing executor/model produces a truthful blocker;
- PRIVATE/LOCAL_ONLY with local model unavailable does not silently use cloud;
- exhausted budget produces an owner-visible blocker;
- tool/model timeout produces recoverable truthful state;
- crash/restart resumes from verified state without duplicate irreversible effect;
- stale evidence cannot turn the UI green;
- failed mission cannot be presented as completed because model text claims success.

## Desktop/navigation

Validate by clicking, not direct route injection:
- Home;
- Mission Console;
- Apps;
- Video Studio;
- Web Designer;
- Owner/Control Plane;
- settings/model surfaces that are actually exposed.

Check:
- only one canonical Video Studio launcher;
- no duplicate Web Designer entry;
- active navigation follows the visible page;
- browser back/forward works where applicable;
- Russian/long labels remain readable;
- 100/125/150/200% scale;
- keyboard-only navigation and visible focus;
- dark/light mode;
- reduced motion/high-contrast where supported;
- errors are actionable and do not leave a permanent spinner.

## Video Studio — visible workflow

Through the actual UI:
1. Open Video Studio.
2. Create/open a project.
3. Import a real local fixture video through the UI.
4. Add it to the timeline.
5. Trim/cut/reorder clips.
6. Exercise at least one adjustment/effect control that exists in the current UI.
7. Seek to first/middle/last frame and check preview coherence.
8. Start export using the visible export control.
9. Observe queue/progress/cancel/error UI.
10. Open/download the completed export from the UI.
11. Restart Bossman and verify the verified export remains recoverably accessible.

Negative UI cases:
- corrupted media;
- unsupported codec fixture;
- cancel export;
- concurrent export request if UI permits it;
- FFmpeg error fixture;
- stale project revision;
- final-frame and duration/NaN regressions.

Backend FFmpeg/probe/decode checks remain supporting evidence, not substitutes for the user flow.

## Web Designer — visible workflow

Through the actual UI:
1. Open Web Designer.
2. Create/open a fixture project.
3. Edit text without flattening nested children.
4. Change an allowed attribute/property.
5. Preview the result.
6. Save.
7. Reload/reopen and verify the source/result persists.
8. Trigger an AI edit only through the visible UI if configured/authorized.
9. Create a stale/concurrent revision and verify the UI reports conflict instead of overwriting silently.

Fixture pages should contain doctype, comments, JSON-LD, script, SVG case-sensitive names, template content, Unicode/entities and malformed-but-browser-tolerated markup. Unrelated source must survive targeted edits.

Security UI checks include preview sandbox/origin isolation, path/symlink rejection, PRIVATE routing and owner-visible cost/authorization where applicable.

## V4 Continuity — owner-visible behavior

Validate through UI-visible missions rather than direct MissionIR calls:
- dependent tasks do not start visibly before prerequisites complete;
- pause/stop remains effective after restart;
- replanning preserves required obligations;
- changed action arguments require fresh authorization;
- recovery status explains what happened;
- stale visual state causes re-observation rather than blind coordinate replay;
- a reusable skill may be selected only when current preconditions match;
- failed recovery is visible as blocked/needs-owner, not false completion.

## V5 Steward — opt-in local fixtures only

Standing autonomy remains gated by current project policy. If the feature is exposed and explicitly enabled for local test fixtures, validate visible owner semantics:
- objective starts DRAFT unless explicitly activated;
- owner can Activate, Pause, Revoke;
- lifecycle and condition are separate;
- UNKNOWN never renders as healthy green;
- unchanged healthy objective causes no unnecessary model call;
- deviation creates proposal, not automatic authority;
- approval/admission is visible where required;
- verified mission completion is followed by fresh re-observation;
- revoke/expiry blocks subsequent effects;
- conflicting objectives become visible instead of oscillating silently.

Do not test V5 standing objectives against production owner data merely to satisfy this checklist.

## Fleet — UI/control-plane truth

If Fleet controls are visible:
- node identity/status is visible;
- unavailable/revoked node does not appear usable;
- PRIVATE placement cannot select forbidden remote executor;
- restart/reconnect state is truthful;
- remote transport remains labelled EXPERIMENTAL until genuine production evidence exists.

## Bug closure rule

A UI bug is closed only after:
1. reproduction through visible UI;
2. root cause identified in code;
3. minimal fix;
4. narrow automated regression when feasible;
5. the same visible user flow succeeds after the fix;
6. independent post-state agrees with the UI.

## Final UI verdict

Report separately:
- USER_UI_CORE;
- USER_UI_VIDEO;
- USER_UI_WEB;
- USER_UI_V4;
- USER_UI_V5;
- USER_UI_FLEET;
- USER_UI_NEGATIVE_CASES;
- VISUAL/ACCESSIBILITY;
- OPEN_UI_P0;
- OPEN_UI_P1;
- NOT_RUN;
- EXTERNAL/OWNER/HARDWARE blockers.

Do not infer USER_UI_PASS from pytest, direct API calls, database edits, curl, route injection, or model prose.
