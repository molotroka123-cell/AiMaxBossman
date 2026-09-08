# Bossman Living UI — Mini Update TZ

Status: DESIGN-ONLY / POST-FREEZE IMPLEMENTATION CANDIDATE

## Goal
Upgrade Bossman from a neon dashboard into a premium, calm AI Operating System with living agents, while preserving every existing route, API, authority boundary, acceptance invariant and release proof.

Design direction: Windows-12-like glass + premium dark command center + restrained blue/violet accents. Normal Mode stays professional. Optional Living Mode visualizes active agents and hand-offs.

## Non-negotiable safety rule
This update is presentation-first. It MUST NOT change mission semantics, permissions, approvals, evidence, budgets, provider routing, World State authority, AT-01, AT-03, canary/N8, OpenHands containment, Video Studio CFR, or release gates.

No existing route may disappear. No hidden route may become unreachable. No UI status may claim success/healthy/recovered unless backed by the existing production state.

## Before implementation: performance baseline
Measure current Command Center on the same machine/build before touching UI:
- cold start and warm start;
- time to interactive;
- route-switch latency;
- idle CPU/GPU/RAM;
- active-task CPU/GPU/RAM;
- long-task memory growth;
- dropped frames / long tasks;
- browser console errors;
- network request count while idle;
- dashboard polling rate.

Store baseline and post-change results. If Living UI materially worsens responsiveness, default it OFF and keep Normal Mode as fallback.

Targets for the mini update:
- no measurable regression in backend task throughput;
- UI animation target 60 FPS where hardware/browser supports it;
- no continuous high-frequency animation when tab is hidden;
- pause decorative animation under prefers-reduced-motion;
- avoid WebGL unless profiling proves it is justified;
- prefer CSS transforms/opacity and lightweight SVG;
- lazy-load agent avatar assets;
- no GIF/video loops in production dashboard;
- cap concurrent decorative animated elements;
- no new background polling solely for visuals.

## Visual system
### Normal Mode
Calm dark glass UI, high information density, clear typography, minimal glow. Accent color represents state, not decoration.

### Living Mode
Optional visualization layer. Show Bossman coordinating agents and hand-offs without changing execution.

Agent states:
- IDLE
- THINKING
- WORKING
- WAITING_APPROVAL
- VERIFYING
- ERROR
- RECOVERING
- DONE

Animations must derive from existing state only. Never synthesize a state for visual effect.

## Agent cards
Each agent card may show:
- avatar;
- name;
- current state;
- current mission/task;
- model/provider;
- elapsed time;
- cost/tokens when already available;
- confidence only when backed by an existing measured field;
- tools in use;
- latest verified event.

Do not expose secrets, hidden chain-of-thought, credentials, raw private context, or unredacted paths.

## Living Agent Room
Add an optional Agent Room view or panel where active agents appear around a digital coordination surface.

Show hand-offs as transient lines:
Bossman -> Research -> Code -> Verify -> Deploy

The graph is a visualization of existing events, NOT a new scheduler or authority layer.

Clicking an agent opens its existing safe details/state; it must not grant permissions or bypass approvals.

## Errors & Recovery
Errors should be understandable:
1. agent/card transitions to ERROR;
2. show concise sanitized reason;
3. if the existing runtime starts recovery, transition to RECOVERING;
4. show actual fallback/retry event;
5. transition to DONE/WORKING only from real runtime state.

Never animate a fake successful recovery.

## Micro-interactions
Allowed:
- 100–300 ms hover/press transitions;
- subtle status pulse;
- progress-ring interpolation;
- connection-flow animation during real hand-off;
- brief success/error emphasis;
- avatar idle animation at low frequency.

Avoid:
- full-screen continuous particles;
- heavy blur on large moving surfaces;
- multiple permanent box-shadow animations;
- layout-changing animation;
- unbounded requestAnimationFrame loops.

## Accessibility
- prefers-reduced-motion disables nonessential motion;
- keyboard navigation remains intact;
- focus states remain visible;
- status cannot rely only on color;
- contrast remains readable on glass surfaces.

## Performance acceptance gate
Before enabling by default, compare baseline vs candidate on the same machine.

FAIL / rollback Living Mode default if any of these occur without an evidence-backed exception:
- meaningful route-switch responsiveness regression;
- sustained idle CPU/GPU increase caused by visuals;
- memory grows continuously while idle;
- new console errors;
- new failed network requests;
- task execution latency/throughput regression attributable to UI;
- visible jank during ordinary navigation.

Normal Mode must remain available as immediate fallback.

## Implementation sequence
1. Capture baseline.
2. Add design tokens and avatar asset loader without changing layout.
3. Add agent state presentation.
4. Add lightweight micro-interactions.
5. Add Living Mode behind a local UI feature flag, default OFF.
6. Add Agent Room as visualization-only.
7. Profile.
8. Run browser/UI regression suite.
9. Run existing freeze-critical regressions.
10. Compare performance.
11. Only then consider default-on.

## Regression requirements
At minimum rerun existing route/browser tests plus checks protecting:
- AT-01 effect proof;
- AT-03 action-boundary freshness;
- approval flow;
- provider health/streaming;
- signed QA relay;
- Apps control;
- Video Studio routes/CFR tests;
- Web Designer routes;
- Trading Lab honest UNWIRED behavior;
- OpenHands isolation;
- N8/canary/rollback regressions.

## Deliverables
- before/after performance JSON or Markdown;
- screenshots for Normal Mode and Living Mode;
- reusable agent avatar assets;
- state-to-animation mapping;
- browser regression evidence;
- rollback instructions;
- final changed-file inventory.

This mini-update must be removable without touching Bossman Core or changing mission truth.