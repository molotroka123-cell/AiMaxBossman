# WebDesigner Dataset Factory

## Principle

Training data is a product artifact.

Do not start an 8xH200 run until the dataset can be rebuilt from manifests and all splits are frozen.

## Dataset layers

### A. Broad layout/code grammar
Source: curated WebSight v0.2.

Purpose:
- HTML/Tailwind structure;
- responsive layout patterns;
- basic visual-to-code grammar;
- diverse page composition.

Initial target: **25k–100k high-quality filtered examples**, not all 1.92M.

Filtering:
- renders successfully;
- no missing critical assets;
- no extreme text/code corruption;
- no near-duplicate screenshot/code;
- acceptable contrast/overflow;
- no obvious broken mobile;
- provenance/license present.

WebSight is synthetic. It is useful breadth, not the aesthetic gold standard.

### B. Golden production examples
Target first version: **2,000–5,000** examples.

Each record:
- brief;
- brand/design-system context;
- initial code;
- initial desktop/mobile render;
- structured critique;
- patch/diff;
- final code;
- final desktop/mobile render;
- build result;
- Playwright result;
- visual rubric;
- source/provenance.

Prefer examples where the final result is visibly and functionally better than the initial result.

### C. Repair examples
Target: **5,000–20,000** compact before/after repairs.

Plant or discover:
- weak hierarchy;
- generic hero;
- bad typography;
- spacing inconsistency;
- low contrast;
- mobile overflow;
- repeated card patterns;
- broken CTA;
- poor empty states;
- motion without reduced-motion;
- inaccessible keyboard flow;
- build/runtime errors.

Format:
`context + rendered-observation + issue-list -> minimal patch`.

This is especially valuable because Bossman will operate iteratively rather than one-shot.

### D. Preference pairs
Target: **5,000–15,000** pairs initially.

Pair:
- same brief;
- same factual content;
- candidate A;
- candidate B;
- screenshots;
- functional gate results;
- blind preference label;
- reason codes.

Reasons are structured:
`hierarchy, typography, spacing, originality, brand, motion, mobile, accessibility, correctness`.

Do not infer preference from "newer candidate = better".

### E. Holdout
See EVALUATION_AND_HOLDOUT.md.

Holdout is never exposed to:
- training;
- skills;
- memory;
- example retrieval;
- teacher prompts;
- reward model training.

## Example schema

Each example should be representable as JSON/Parquet:

```json
{
  "example_id": "...",
  "source": {"kind":"owner_generated","license":"owned","revision":"..."},
  "task": {"brief":"...","facts_hash":"...","design_context_hash":"..."},
  "input": {"repo_hash":"...","screenshots":["sha256:..."]},
  "trajectory": [
    {"role":"coder","action":"..."},
    {"role":"critic","issues":[...]},
    {"role":"patcher","diff_hash":"..."}
  ],
  "result": {
    "code_hash":"...",
    "desktop_render_hash":"...",
    "mobile_render_hash":"...",
    "build_pass":true,
    "playwright_pass":true
  },
  "scores": {...},
  "split":"train"
}
```

## Render factory

Use one pinned browser/container environment for training-data renders.

Record:
- Chromium version;
- viewport;
- DPR;
- fonts;
- network policy;
- reduced-motion mode;
- timestamp only as metadata, never as page content.

Minimum viewports:
- desktop 1440x1000;
- mobile 390x844.

Add tablet only if the task needs it.

## Teacher generation

A strong cloud model may generate candidate code/critique for dataset creation, but:
- teacher identity and cost are recorded;
- teacher output is not automatically gold;
- build/render/tests decide whether it enters the pool;
- teacher examples do not enter the holdout.

## Quality filter

An example is rejected if:
- project does not build;
- critical screenshot cannot render;
- page is mostly placeholder;
- factual claims are invented;
- accessibility hard gate fails;
- license/provenance is unknown;
- code contains secrets;
- output is a near duplicate of an existing example;
- visual score is below the dataset floor.

## Deduplication

Dedup by more than prompt text:
- perceptual screenshot hash;
- DOM/component structure;
- code MinHash/token similarity;
- asset hashes;
- brief semantic similarity.

Keep a `duplicate_cluster_id` so train/validation/holdout never split near duplicates across sets.

## Data versioning

Every training run names immutable:
- dataset manifest SHA;
- train split hash;
- validation split hash;
- tokenizer/template version;
- preprocessing code SHA.

No "latest dataset" in a training command.

## First-day target

Tomorrow, before H200 rental, produce:
- schema;
- render script;
- 50 local smoke examples;
- 20 repair examples;
- 20 preference pairs;
- frozen 20-task micro-holdout;
- manifest and hashes.

Then scale.
