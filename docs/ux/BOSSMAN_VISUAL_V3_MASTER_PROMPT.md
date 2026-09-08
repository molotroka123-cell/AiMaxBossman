# Claude Opus Master Prompt — BOSSMAN Visual V3 Safe Mini Update

Use this prompt only after the UX reference/spec branch exists. It is intentionally conservative: performance and regression proof come before visual code.

---

You are implementing a **presentation-only BOSSMAN Visual V3 mini update**.

Repository: `molotroka123-cell/AiMaxBossman`
UX reference branch: `ux/bossman-visual-v3-miniupdate-20260908`
Reference base at creation: `3d8814901dee343c442fb4cb34602064d73ce800`
Primary spec: `docs/ux/BOSSMAN_VISUAL_V3_MINI_UPDATE_TZ.md`

## Mission

Upgrade Command Center from a neon concept-style UI into a premium daily-use AI OS:

- calmer Windows-12-like glass;
- stronger hierarchy and less permanent neon;
- optimized agent avatars;
- honest agent states;
- small state-driven microanimations;
- optional Living Agent Room;
- zero intentional change to runtime semantics.

**Do not trade reliability or responsiveness for visual spectacle.**

## 0. Establish current truth before touching code

Fetch the current implementation head and record:

- `BASE_SHA`
- Command Center UI files and tests
- current CI status
- current routes/navigation inventory
- current API request inventory
- existing polling intervals
- existing browser/Playwright acceptance tests
- unresolved merge/conflict state

The historical reference base above is not authority if the live implementation has moved. Rebase/port the UX lane only after identifying the real current target SHA.

Do not copy old UI files over newer ones.

## 1. Protected behavior — do not modify

Do not redesign, weaken, bypass, or opportunistically “clean up”:

- AT-01 effect-proof authority;
- AT-03 action-boundary freshness;
- approval/permission/budget/privacy/effect gates;
- canary/N8 cohort identity, rollback, terminal-run immutability;
- World State freshness/CONTESTED semantics or verifier authority;
- OpenHands isolation/containment;
- signed QA relay or SSRF containment;
- Apps owner policy;
- streaming B4 logic;
- model-health B5 logic;
- Video Studio CFR/container-slop behavior;
- Trading Lab semantics;
- provider routing or circuit-breaker semantics;
- running-mission persistence/restart semantics.

Hard no-touch during this UX lane unless the owner explicitly creates a separate integration task:

- `bossman-core/bossman/cli.py`
- `bossman-core/bossman/gateway/app.py`
- `bossman-core/bossman/gateway/backends.py`

Do not solve the existing gateway conflict as part of a visual commit.

## 2. Keep the existing lightweight stack

Command Center is plain/static JS + CSS. Preserve that.

Do NOT introduce React, Vue, Svelte, Three.js, Lottie, GSAP or another animation/runtime framework merely for this update.

Prefer:

- existing JS modules;
- CSS variables/tokens;
- CSS `transform` and `opacity` transitions;
- event-driven updates;
- optimized WebP/SVG assets;
- lazy loading for noncritical avatars;
- no network-hosted asset dependency.

Generated concept screenshots are references only. Never ship them as dashboard backgrounds or full-resolution product assets.

## 3. Phase 0 — performance baseline BEFORE implementation

Before changing presentation code, add/run a reproducible browser performance probe against the unmodified target SHA.

Exercise:

1. cold load;
2. warm load;
3. 60 s idle;
4. 60 s active six-agent fixture;
5. primary navigation;
6. repeated agent-detail open/close;
7. UI error -> recovery fixture;
8. hidden tab -> foreground;
9. `prefers-reduced-motion: reduce`.

Record to a machine-readable artifact and a short Markdown report:

- DCL/load timing;
- request count/transferred bytes;
- long tasks >50 ms and >100 ms;
- p50/p95 frame time during motion;
- slow/dropped frame count if measurable;
- JS heap after load / 60 s idle / 60 s activity;
- CPU if environment exposes it;
- current polling/request frequency;
- console errors/warnings;
- dead-click/navigation failures.

Do not modify product behavior to manufacture a better baseline.

## 4. Performance budget

The final UX implementation must beat or stay within these limits relative to Phase 0:

- no new runtime framework by default;
- first-load new production visual assets target `<=350 KB` compressed;
- median load regression `<=5%`;
- p95 load regression `<=10%`;
- no new UX-attributable >100 ms long task;
- long-task count regression `<=10%`;
- p95 motion frame time `<=20 ms` target;
- JS heap regression after the two-minute scenario `<=10 MB`;
- no increase in API polling/request count;
- no decorative animation work while `document.hidden`;
- reduced-motion disables decorative motion;
- console errors = 0;
- UX-introduced dead clicks = 0.

If a metric fails, simplify the visual implementation. Never weaken the benchmark.

## 5. Implement in small reversible commits

### Commit A — baseline only

Performance harness/report. No visual behavior change.

### Commit B — static design system

Only safe visual cleanup:

- design tokens;
- surfaces;
- spacing;
- typography;
- borders/shadows;
- agent card layout;
- optimized static avatars.

Preserve DOM actions and existing selectors used by tests unless tests and compatibility adapters prove the new path equivalent.

### Commit C — honest agent state projection

Map only already-existing observable runtime status to:

`IDLE / THINKING / WORKING / WAITING / NEEDS_APPROVAL / ERROR / RECOVERING / DONE / UNKNOWN`

Missing/unmappable data => `UNKNOWN`.

Never infer HEALTHY, WORKING or DONE from elapsed time, animation state, local timers or optimistic client state.

### Commit D — micro-motion behind flag

Presentation flag:

`ui_living_agents_v1`

Default OFF.

Allowed motion:

- hover/focus 120–250 ms;
- state transition 180–350 ms;
- subtle active pulse;
- real handoff indicator;
- one-shot recovery/success animation.

Avoid perpetual global effects and multiple rAF loops.

### Commit E — optional Living Agent Room

Only after performance remains green.

Mission/agent edges appear only from real task/handoff events. It is a visualization, never a simulation or new execution path.

## 6. Normal Mode must remain the default

Recommended final visual blend:

- Variant 2 calm/minimal language for normal use;
- Variant 1 information density for dashboard layout;
- Variant 3 only for optional Living Agent Room;
- Variant 4 as information-architecture inspiration for mission/global status views.

Do not reproduce concept art literally.

## 7. Required regression proof after EACH functional UX commit

At minimum:

- existing Command Center tests;
- browser route/navigation coverage;
- all hidden-but-supported routes still directly reachable;
- keyboard/focus smoke;
- no console errors;
- no new unexpected 4xx/5xx;
- existing request/polling inventory unchanged;
- feature flag OFF behavior works;
- `prefers-reduced-motion` test;
- hidden-tab animation suspension test;
- status mapping fixture tests including `UNKNOWN`;
- dead-click smoke.

If an existing backend/API test fails, assume the UX commit is guilty until disproven. Do not edit backend merely to make the new UI pass.

## 8. Stop/revert conditions

Stop the implementation and revert only the latest UX change if any of these appear:

- an existing route disappears;
- button action changes unintentionally;
- API request shape/method/path changes;
- request/polling volume increases;
- a mission state appears healthier than the backend proves;
- a dead click appears;
- console error appears;
- performance budget fails;
- memory grows continuously during the two-minute scenario;
- reduced-motion still animates decorative elements;
- hidden tab continues running decorative animation;
- CI regression appears outside expected visual snapshots.

Do not “solve” such a regression by weakening Core, permissions, evidence, policy, provider or recovery code.

## 9. Merge/conflict rules

The UX lane can overlap with active convergence work. Before applying any commit:

- compare the current implementation SHA with the UX branch base;
- use path-level/semantic integration;
- never wholesale overwrite `command-center/ui` from an older branch;
- preserve all newer owner-acceptance/browser fixes;
- do not merge PR26/PR36/PR49 as a shortcut;
- do not touch the gateway three-file conflict.

## 10. Final acceptance

Freeze one final UX implementation SHA and produce:

`BASE_SHA=`
`UX_SHA=`
`RUNTIME_SEMANTIC_DELTA=NONE/EXPLAIN`
`NEW_RUNTIME_DEPENDENCIES=`
`FIRST_LOAD_ASSET_DELTA_KB=`
`LOAD_MEDIAN_DELTA=`
`LOAD_P95_DELTA=`
`LONG_TASK_DELTA=`
`P95_FRAME_MS=`
`HEAP_DELTA_MB=`
`REQUEST_COUNT_DELTA=`
`CONSOLE_ERRORS=`
`DEAD_CLICKS=`
`REDUCED_MOTION=`
`HIDDEN_TAB_SUSPENSION=`
`FEATURE_FLAG_OFF=`
`COMMAND_CENTER_TESTS=`
`ROUTES_PRESERVED=`
`API_CONTRACTS_PRESERVED=`
`ROLLBACK_PROVEN=`
`FINAL_VERDICT=`

`FINAL_VERDICT=PASS` is allowed only when the visual implementation is both prettier and measured no worse within the specified budgets.

Do not add more architecture. Do not fabricate performance evidence. Do not call screenshot quality a performance result.