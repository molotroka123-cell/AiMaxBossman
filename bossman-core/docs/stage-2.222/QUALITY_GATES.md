# Context Quality Gates — no silent degradation

The optimization target is **quality first, tokens second**.

## Mandatory invariants

- exact critical numbers/versions/paths can be traced to source;
- active decisions outrank superseded decisions;
- disputed memory is labelled disputed in prompt;
- retrieved evidence includes source IDs;
- raw source remains available after distillation/compact;
- recent transcript is preserved verbatim by Compact Skill;
- context compiler never exceeds configured input budget;
- weak retrieval returns fewer sources rather than padding with noise.

## Golden anchors

Every benchmark task should declare mandatory anchors such as `128 GB`, `browser.confirmed_click`, a commit SHA, a filename, or an exact decision ID. If optimized context loses any mandatory anchor needed to answer, the test fails even if token savings are high.

## Regression strategy

1. Baseline answer with sufficient raw context.
2. Optimized answer using Stage 2.222.
3. Deterministic assertions on facts/anchors/source refs.
4. Optional judge model for semantic quality only after deterministic checks.
5. Track token delta and latency as secondary metrics.

## Safe fallback

If retrieval confidence/quality gate fails: enlarge budget → retrieve more evidence → preserve more verbatim content. Never respond by inventing a more aggressive summary.
