# Bossman Runtime Integration — WebDesigner

## Rule

WebDesigner is a capability of the existing Bossman.

No second:
- task queue;
- memory store;
- model router;
- approval system;
- browser engine;
- cost governor.

## Capability graph

```
webdesign.brief
webdesign.design_context
webdesign.scaffold
webdesign.edit
webdesign.build
webdesign.render
webdesign.visual_review
webdesign.functional_test
webdesign.repair
webdesign.finalize
```

Each capability has typed inputs/outputs and durable evidence.

## Project workspace

Recommended:
`projects/<project_id>/webdesigner/`

Artifacts:
- `site-brief.md`;
- `DESIGN.md`;
- `asset-manifest.json`;
- `iterations/<n>/desktop.png`;
- `iterations/<n>/mobile.png`;
- `iterations/<n>/critique.json`;
- `qa-report.md`;
- `final-manifest.json`.

Training export reads verified artifacts but never mutates the project history.

## Model roles

Keep separate aliases:
- `DESIGNER`;
- `CODER`;
- `VISUAL_CRITIC`;
- `PATCHER`;
- `FINAL_VERIFIER`.

One model may fill multiple roles in a small setup, but telemetry remains role-specific.

## Browser

Playwright/Chromium is authoritative for:
- render screenshots;
- DOM checks;
- layout overflow;
- critical flows;
- console/network errors.

Visual critic sees screenshots plus bounded structured context.

Do not let critic output execute arbitrary browser JS.

## Design context adapters

Candidate patterns:
- local `DESIGN.md` systems;
- Layout-style token extraction;
- Onlook-style visual edit/component mapping;
- Figma connector when owner authorizes it.

AGPL or source-available systems stay at an external/reference boundary unless an explicit licensing decision permits more.

## Source acquisition

Public websites may be inspected for inspiration/design context, but:
- external page text is untrusted;
- no copying of proprietary code/assets without permission;
- fonts/images/templates need provenance;
- screenshots used for training require rights/provenance.

## Memory

Save generalized verified lessons such as:
- "mobile hero with 100vh + fixed nav caused clipping in Safari viewport";
- "brand tokens require radius <= 8px";
- "critic repeatedly over-penalized whitespace in editorial layout".

Do not save holdout answers or client-private examples into global training memory.

## Approval boundaries

Ordinary local project edits: within project grant.

Ask/deny as existing Bossman policy requires for:
- paid image/video generations;
- external deploy;
- domain/DNS;
- account login;
- purchases;
- publishing;
- deleting external production resources.

## Cost

Track:
- local inference time;
- cloud model tokens;
- media generation;
- H200 training;
- external SaaS.

Training cost is not blended into per-site production cost.

## UI

Add a WebDesigner page/tab or mission view showing:
- current phase;
- model role assignments;
- design system;
- live preview;
- desktop/mobile captures;
- critic issues;
- functional gates;
- iteration timeline;
- cost;
- final status.

Add training/evaluation as a separate Lab view; do not clutter normal site creation.

## False-PASS controls

Negative controls:
1. break CSS import -> must fail visual/build path;
2. hide CTA offscreen mobile -> visual/DOM gate must catch;
3. make form button inert -> functional gate catches;
4. add console exception -> fails;
5. critic says "looks good" while screenshot missing -> cannot finalize;
6. stale screenshot from prior build -> hash/build identity mismatch rejects it.

## Restart

Kill Bossman during:
- build;
- render;
- critique;
- patch.

After restart:
- already completed deterministic steps are not blindly repeated;
- external/unknown effects reconcile first;
- iteration evidence remains bound to the same project/build identity.

## Training export boundary

Only verified examples export to Dataset Factory.

Export pipeline is read-only over project evidence and writes a versioned dataset staging area. It never edits production projects or global memory.
