# BOSSMAN Visual V3 — Safe Mini Update Technical Specification

Date: 2026-09-08
Branch: `ux/bossman-visual-v3-miniupdate-20260908`
Base: `3d8814901dee343c442fb4cb34602064d73ce800` (`night/v7-convergence-20260908`, PR #58)

## 1. Goal

Create a premium, calmer Bossman visual layer that feels like a real AI operating system rather than a game UI, while preserving every existing route, API contract, permission/evidence boundary, task state transition, recovery flow, editor behavior and release gate.

This is a **UX mini-update**, not an architecture change.

Desired direction: **Windows-12 glass + Linear clarity + Jarvis-like liveness + Bossman identity**.

Two presentation modes:

- **Normal Mode** — default. Calm, dense, professional Command Center with light micro-motion only where it communicates state.
- **Living Mode** — optional and feature-flagged. Agents become visibly alive and collaboration is visualized, but execution truth still comes only from existing runtime state.

## 2. Non-negotiable safety boundary

The first implementation must stay inside the presentation layer.

Do not change:

- Bossman Core mission state or execution semantics;
- permissions, approvals, budgets, privacy, effect obligations or verification;
- AT-01 / AT-03 semantics;
- canary/N8, rollback or promotion authority;
- World State authority or freshness semantics;
- OpenHands isolation/containment;
- Apps owner policy;
- provider streaming/model-health logic;
- Video Studio CFR timing/container behavior;
- gateway provider architecture or circuit breaker;
- existing endpoint shapes, status codes or polling cadence.

In particular, do not touch the unresolved gateway integration files as part of this UX lane:

- `bossman-core/bossman/cli.py`
- `bossman-core/bossman/gateway/app.py`
- `bossman-core/bossman/gateway/backends.py`

No backend migration. No new persistent state. No new execution authority.

## 3. Current implementation constraint

Command Center is currently a lightweight static JS/CSS UI. Preserve that advantage.

Default rule:

- no React/Vue/Svelte migration;
- no Three.js/WebGL scene engine;
- no Lottie runtime dependency;
- no heavy animation framework;
- no new external runtime dependency unless a measured benchmark proves it is smaller/faster than the existing implementation and the owner explicitly accepts it.

Use existing JS/CSS architecture, CSS variables, transform/opacity animations and small event-driven state renderers.

## 4. Visual system

### 4.1 Base appearance

Reduce permanent neon by roughly 50–70% compared with concept art.

Use:

- near-black/navy background;
- restrained glass panels;
- thin cobalt/cyan focus edges;
- purple as secondary agent/intelligence accent;
- green only for confirmed healthy/success;
- amber for waiting/review/approval;
- red only for real failure/error/refusal;
- high-contrast neutral typography;
- soft depth, not constant bloom.

### 4.2 Bossman identity

Bossman is the orchestrator, not just another agent.

The UI may show a larger Bossman identity/avatar in Living Mode, but it must never imply an action happened unless the runtime proves it happened.

### 4.3 Agent avatars

Agent avatars are a UI projection of existing agent identity/state.

Required visual states:

- `IDLE`
- `THINKING`
- `WORKING`
- `WAITING`
- `NEEDS_APPROVAL`
- `ERROR`
- `RECOVERING`
- `DONE`
- `UNKNOWN`

If the backend/runtime cannot prove a state, render `UNKNOWN` or a neutral state. Never synthesize a fake healthy/working state from elapsed time alone.

### 4.4 Agent Room

Living Mode may provide an Agent Room / collaboration view:

- selected mission in the center;
- participating agents around it;
- active handoff edge only when a real handoff/event exists;
- clicking an agent opens its existing details, model/tool identity, current mission and observable status;
- no autonomous graph animation that invents agent collaboration.

## 5. Micro-animation rules

Motion exists to explain state, not decorate every pixel.

Preferred:

- 120–250 ms hover/focus transitions;
- 180–350 ms state transitions;
- very slow breathing/pulse only for active agent status;
- flow indicator only while an actual task/handoff is active;
- one short recovery animation on real recovery transition;
- success animation runs once, then settles.

Forbidden by default:

- perpetual large background animation;
- continuously moving gradients over the whole viewport;
- multiple independent `requestAnimationFrame` loops;
- layout animation via `top/left/width/height` on every frame;
- decorative canvas/WebGL loops;
- animation while the page is hidden;
- animation that continues when `prefers-reduced-motion: reduce` is set.

## 6. Performance gate — MUST HAPPEN BEFORE VISUAL IMPLEMENTATION

Before changing the UI, capture a baseline from the unmodified target SHA using the real browser/Playwright stack already used by Command Center tests.

Baseline scenario:

1. cold Command Center load;
2. warm load;
3. 60 seconds idle dashboard;
4. 60 seconds with six agents represented as active/updating;
5. navigate through all primary spaces;
6. open/close agent detail repeatedly;
7. trigger a synthetic UI-only error/recovery fixture without changing backend state;
8. background/restore tab;
9. run with `prefers-reduced-motion: reduce`.

Record at minimum:

- DOMContentLoaded and load timing;
- request count and transferred bytes;
- JS/CSS/image bytes introduced by the UX lane;
- long tasks >50 ms and >100 ms;
- p50/p95 frame time during motion;
- dropped/slow-frame count where browser APIs allow measurement;
- JS heap after load, after 60 s idle and after 60 s activity;
- idle CPU where the environment can measure it;
- polling/request frequency;
- console errors/warnings;
- dead clicks and navigation failures.

### 6.1 Acceptance budgets

UX implementation must satisfy all of the following versus the captured baseline:

- no new external runtime framework/dependency by default;
- production avatar/motion assets added to first load: target `<= 350 KB` compressed total; lazy-load noncritical avatars;
- generated concept/reference images are **never** shipped as full-size production dashboard assets;
- load median regression `<= 5%`; p95 regression must remain `<= 10%`;
- no new UX-attributable long task >100 ms;
- long-task count regression `<= 10%`;
- target p95 active animation frame time `<= 20 ms`; aim for 60 FPS on normal desktop hardware;
- JS heap regression after the two-minute scenario `<= 10 MB`;
- idle CPU should remain effectively flat; target `<= 2–3%` process CPU where measurable;
- existing API polling/request count must not increase;
- zero animation work while `document.hidden === true` except event bookkeeping;
- reduced-motion mode removes decorative motion;
- console errors = 0;
- dead clicks introduced by this lane = 0.

If the gate fails, reduce/disable motion. Do not compensate by weakening tests, polling less often in a way that changes product semantics, or changing backend behavior.

## 7. Feature flag and rollout

All new living-agent motion must sit behind a presentation-only flag:

`ui_living_agents_v1`

Rules:

- default = OFF;
- Normal Mode remains default;
- flag changes presentation only;
- no state migration;
- disabling the flag must restore the existing behavior immediately;
- no backend branch depends on this flag.

Static visual improvements that are proven zero-risk may ship separately from Living Mode.

## 8. Implementation order

### Phase 0 — Baseline only

No visual code. Add measurement harness/report and capture baseline.

### Phase 1 — Static visual cleanup

- design tokens;
- calmer surfaces;
- spacing/typography hierarchy;
- compact agent cards;
- static optimized agent avatars/icons;
- preserve all current DOM actions and routes.

### Phase 2 — Honest state mapping

Map existing runtime statuses to the nine visual states. Add `UNKNOWN` fallback. Do not add endpoints merely to make the UI prettier.

### Phase 3 — Micro-motion behind flag

Add CSS transform/opacity transitions, state pulse and one-shot recovery/success animations.

### Phase 4 — Optional Living Agent Room

Only if the performance gate is still green. It must be a projection of real events, not a simulation.

### Phase 5 — Regression / audit

Run full Command Center UI/browser regression and compare performance to Phase 0 baseline.

## 9. Regression requirements

The lane is not accepted unless:

- every pre-existing route still exists and renders;
- routes intentionally hidden from the menu remain directly reachable;
- no button loses its previous action;
- keyboard navigation/focus works;
- all existing API calls keep identical method/path/meaning;
- no new periodic polling is introduced;
- no 4xx/5xx is introduced by navigation;
- console errors = 0;
- `prefers-reduced-motion` is covered by test;
- `document.hidden` suspends decorative animation;
- agent `ERROR/WAITING/APPROVAL/DONE` mapping is fixture-tested;
- `UNKNOWN` is rendered honestly when state is missing;
- feature flag OFF produces the stable baseline behavior;
- the existing Command Center test suite remains green.

## 10. Visual reference variants

References live in `docs/ux/bossman_visual_v3/`:

1. `variant-01-command-center.webp` — balanced Command Center; safest baseline direction.
2. `variant-02-minimal-night.webp` — quieter/minimal variant; best reference for Normal Mode density.
3. `variant-03-living-agent-room.webp` — Living Mode / Agent Room concept.
4. `variant-04-executive-command.webp` — executive/mission-pipeline information architecture reference.

These are mood/layout references, not pixel-perfect specifications.

Recommended product direction: **V2 calm visual language as default + selected V1 information density + V3 Agent Room only as optional Living Mode**.

## 11. Rollback

Rollback must be presentation-only:

1. disable `ui_living_agents_v1`;
2. revert UX commits if needed;
3. no database rollback;
4. no backend/core rollback;
5. no effect on running missions.

## 12. Definition of done

Visual V3 is done only when it is observably prettier **and** no slower/no less reliable in the measured scenario.

A screenshot is not acceptance. A green performance/regression comparison on one frozen implementation SHA is acceptance.