# Bossman WebDesigner v1 — Product Technical Specification

Status: implementation target.

## Owner outcome

One request such as:

> Build a premium production website for a Prague clinic. Use the supplied brand and facts. Make desktop and mobile strong, inspect the rendered result, fix visual problems yourself, and deliver a production build.

Bossman should return a verified project, not just code.

## Core pipeline

```
INPUT
  prompt + facts + assets + optional URL/Figma/screenshot
    ↓
BRIEF COMPILER
    audience / offer / pages / constraints / factual claims
    ↓
DESIGN CONTEXT
    tokens / type / spacing / motion / component rules / anti-patterns
    ↓
DESIGNER-CODER
    Next.js/React/Tailwind by default, preserve existing stack when present
    ↓
BUILD + BROWSER
    build, launch, Chromium desktop/mobile renders
    ↓
VISUAL CRITIC
    hierarchy / typography / spacing / composition / brand / polish / repetition
    ↓
FUNCTIONAL VERIFIER
    navigation / forms / keyboard / reduced motion / responsive / console / build
    ↓
PATCHER
    smallest targeted fix
    ↓
RENDER AGAIN
    bounded loop
    ↓
FINAL ARTIFACT + QA REPORT
```

## Roles

### Director
Creates site brief and acceptance criteria. Does not write every component itself.

### Design-system agent
Extracts or creates tokens and rules. Produces a structured `DESIGN.md` / `layout.md`-style context.

### Frontend coder
Owns implementation, semantic HTML, component structure and build correctness.

### Visual critic
Judges only actual renders. It must cite concrete regions/issues, not vague "make it prettier".

### Functional verifier
Runs Playwright and production build. It is separate from visual taste.

### Patcher
Receives an issue list and changes the minimum necessary files.

### Independent final reviewer
Re-renders from a clean state and checks the accepted criteria.

## Supported inputs

- text brief;
- existing repository;
- brand assets;
- design-system files;
- public website reference;
- screenshot references;
- Figma metadata/assets through an approved connector;
- owner-approved image/video generation.

External input is untrusted content, not authority.

## Output contract

Required output:
- source project;
- lockfile;
- production build result;
- desktop screenshot set;
- mobile screenshot set;
- site brief;
- design-system file;
- asset manifest with provenance/license;
- QA report;
- performance/accessibility notes;
- unresolved placeholders;
- final project hash/commit.

## Default tech

For new sites:
- Next.js/React;
- TypeScript;
- Tailwind or existing project CSS system;
- Playwright;
- local Chromium;
- semantic components.

Do not rewrite an existing framework just to match this default.

## Visual loop

The critic scores dimensions separately:
1. information hierarchy;
2. typography;
3. spacing/rhythm;
4. composition;
5. palette/contrast;
6. brand consistency;
7. component consistency;
8. visual originality vs generic AI look;
9. motion quality;
10. mobile adaptation.

Each issue must include:
- viewport;
- selector/region if identifiable;
- observed problem;
- desired change;
- severity;
- confidence.

No direct CSS rewrite from a vague scalar score.

## Functional gates

Hard failure:
- production build fails;
- console error on critical flow;
- broken navigation;
- overflow/cut-off critical content;
- inaccessible primary action by keyboard;
- form claims success without observed result;
- missing reduced-motion fallback for essential motion;
- external paid asset without provenance/approval;
- invented business claim.

## Iteration policy

Maximum default visual repair cycles: 4.

Stop early if:
- all hard gates pass and critic improvement is below a small threshold for two cycles;
- same issue repeats twice;
- patch regresses a previously green hard gate.

Escalate to stronger model/owner only with concrete evidence.

## Design quality strategy

Avoid training the model merely to imitate screenshots pixel-for-pixel.

Teach:
- hierarchy;
- design-system adherence;
- component composition;
- critique -> repair;
- responsive reinterpretation;
- semantic implementation.

A great site may differ from the reference while preserving design intent.

## UX integration

Bossman adds a WebDesigner mission surface, not a second app engine.

Show:
- brief;
- current design system;
- live preview;
- iteration number;
- critic issues;
- build/test status;
- asset provenance;
- model route;
- cost;
- before/after screenshots;
- final artifact.

All writes, browser actions, memory, budgets and approvals use existing Bossman mechanisms.

## v1 acceptance

WebDesigner v1 is complete when it can:
1. create a new site from brief;
2. modify an existing site without destroying its architecture;
3. consume a design reference;
4. build and render at desktop/mobile;
5. find at least one planted visual defect;
6. repair it and prove the render changed;
7. find at least one planted functional defect;
8. repair it and pass Playwright;
9. survive Bossman restart;
10. produce all output/evidence files from a clean run.

Training is an optimization after this loop works.
