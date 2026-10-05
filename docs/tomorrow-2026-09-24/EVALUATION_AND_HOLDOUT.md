# WebDesigner Evaluation and Holdout

## Question

Did training make Bossman better at producing **beautiful, correct, production-ready websites**?

Training loss cannot answer this.

## Baselines

Freeze before training:
- Base model direct;
- Base model through Bossman WebDesigner loop;
- Xing/Qwen challenger as applicable;
- one strong Claude reference run using the same brief/assets/tool limits where technically possible.

Keep "model quality" separate from "Bossman loop quality".

## Holdout construction

### Micro holdout
20 tasks for pipeline debugging.
Frozen before the first H200 run.

### Main holdout
Minimum 100 tasks; 200 preferred.

Categories:
- SaaS;
- clinic/health;
- luxury/ecommerce;
- restaurant/hospitality;
- real estate;
- portfolio/creative;
- local service;
- fintech/crypto;
- editorial/content;
- dashboard/product UI.

Include:
- new build;
- existing repo edit;
- screenshot reference;
- brand-system reference;
- mobile-heavy task;
- animation task;
- data/form task.

No near-duplicate train example may enter holdout.

## Primary gates

### Functional
- production build;
- critical Playwright flows;
- no critical console errors;
- navigation;
- responsive overflow;
- keyboard primary flow;
- reduced-motion;
- factual integrity.

A visually nice broken site is not a win.

### Visual blind preference
Randomize A/B identities.

Judge rendered desktop + mobile, not code.

Labels:
- A clearly better;
- A slightly better;
- tie;
- B slightly better;
- B clearly better.

Record reason tags.

### Efficiency
- time-to-verified-result;
- number of repair cycles;
- model tokens;
- GPU/local memory;
- owner interventions;
- cloud cost.

## Rubric

Score 1–5 independently:
- hierarchy;
- typography;
- spacing/rhythm;
- composition;
- color/contrast;
- brand fidelity;
- originality / avoidance of generic AI look;
- motion;
- mobile;
- interaction polish.

Functional result remains a separate hard gate.

## Judges

Use at least:
1. automated functional verifier;
2. multimodal critic/judge;
3. blinded human/owner preference on a meaningful sample.

A model must not be the sole judge of its own outputs.

For high-confidence experiments, use two independent VLM judges and measure their disagreement with human labels.

## External benchmark

Use Vision2Web as an external reference when compatible:
https://vision2web-bench.github.io/

Do not optimize on hidden answers or convert leaderboard claims into Bossman PASS.

## Improvement thresholds

Pre-register before a major training run.

Suggested v1:
- functional success: no material regression vs Bossman base;
- pairwise visual win rate vs base: clear positive gain on the main holdout;
- time-to-verified-result: not >2x worse unless visual gain justifies it;
- tool/schema correctness: no regression.

### "Claude-level" rule

Do not use the phrase from a few screenshots.

A **CLAUDE_PARITY_CANDIDATE** requires:
- same frozen holdout;
- same factual inputs;
- comparable tool budget;
- blinded rendered comparison;
- no material functional deficit;
- no statistically meaningful visual deficit on the pre-registered primary preference metric.

A **CLAUDE_BEATEN_ON_WEBDESIGN_HOLDOUT** claim requires a positive statistically defensible advantage on that fixed holdout. It is a domain-specific claim only, not general intelligence.

## Regression set

Keep 20 boring but important cases:
- simple landing page;
- form;
- table;
- auth shell;
- dashboard;
- responsive navbar;
- modal;
- long text;
- localization;
- dark mode.

Aesthetic specialization must not destroy ordinary frontend reliability.

## Report

Every candidate report includes:

`MODEL | ADAPTER | DATASET | BUILD_PASS | PLAYWRIGHT | VISUAL_VS_BASE | VISUAL_VS_CLAUDE | TIME | TOKENS | COST | VERDICT`

Keep raw screenshots and task manifests for audit.
