# Context Budget & Cognitive Load Policy — Bossman 1.6

## Principle

Bossnet must increase capability **without increasing the amount of context each
local model has to read**.

A larger knowledge base, more agents, more providers and more workflows are
useful only if retrieval/routing makes the *active working set smaller and more
relevant*.

The local model is never expected to "know all of Bossman" in one prompt.

## Three memory planes

### 1. Cold evidence
Large immutable store:
raw videos, transcripts, frames, logs, CASE evidence, experiment traces,
documents, historical workflows.

Never injected wholesale.

### 2. Warm structured memory
Compact typed indexes:
entities, claims, outcomes, skills, workflow summaries, episode graphs,
capability/model stats, provider stats.

Used for retrieval/ranking.

### 3. Hot working context
Only what the current model needs for the current step.

Target structure:
- objective;
- constraints;
- current step;
- small relevant evidence bundle;
- required schema/tool contract;
- short prior-step result.

Everything else stays outside the prompt.

## Context compiler

Before every model call:

`task -> classify -> retrieve candidates -> rerank -> deduplicate -> compress ->
evidence budget -> prompt packet`.

The model never performs broad memory search by receiving a giant dump.

## Hard budgets

Every model adapter declares:
- physical context window;
- reserved output tokens;
- system/tool-schema reserve;
- maximum evidence tokens;
- safety reserve.

Use at most a configurable fraction of physical context by default. Larger
windows are not an excuse to fill them.

Suggested initial policy:
- FAST/router models: <=15% of context;
- specialist workers: <=30%;
- verifier: <=25%;
- exceptional deep-reasoning job: <=50% only with explicit router reason.

These are benchmarkable defaults, not universal truths.

## Progressive disclosure

Level 0: IDs + titles + one-line summaries.
Level 1: selected structured records.
Level 2: exact evidence excerpts/frames.
Level 3: raw artifact only if still necessary.

A worker asks for deeper evidence through tools instead of receiving everything
up front.

## Agent isolation

Coder does not receive trading history.
Market agent does not receive clinic documents.
Verifier receives the claim + evidence, not the producer's persuasive chain of
thought.
Router receives capability summaries, not full model documentation.

Persistent Agent Society means persistent *specialization/memory indexes*, not
persistent giant prompts.

## Workflow state

Do not carry the entire conversation between workflow steps.

Each step emits a typed artifact:
```
step_id
result
evidence_refs
uncertainties
next_requirements
```

The next worker receives this artifact plus retrieved evidence.

## Video learning

Do not place an entire transcript plus hundreds of frames into Qwen.

Use:
full transcript -> searchable index;
temporal selector -> candidate episode;
episode graph -> setup/trigger/management/outcome;
Qwen -> only required high-res ROIs + nearby transcript;
verifier -> raw evidence references.

Long-video memory grows on disk/graph, not inside model context.

## Knowledge Fabric

Temporal Knowledge Fabric is primarily an **external memory service**.

Retrieval must be:
1. time-aware;
2. project/domain scoped;
3. evidence-aware;
4. deduplicated;
5. novelty-aware.

Return top evidence under a token budget. If confidence remains low, fetch the
next layer rather than flooding the first call.

## Skills

A mature skill should REDUCE context.

Bad:
50 pages of "how we once built a website".

Good:
typed workflow + parameters + 10-line contract + tests + evidence links.

Successful trajectories are compiled into executable skills and the verbose raw
trace becomes cold evidence.

## Documentation

Human documentation is not automatically model context.

Docs are indexed and retrieved on demand. Critical runtime invariants should
also exist as machine-readable policy/tests so a local model does not need to
reread architecture prose every run.

## Provider / BOSSNET metadata

Workers receive only relevant node/provider candidates. Never dump the complete
fleet registry into every task.

Jev/router works on compact capability cards.

## Summarization safety

Compression may remove prose but may NOT remove:
- exact numeric evidence;
- uncertainty/UNKNOWN;
- source identity;
- timestamps;
- approvals;
- safety constraints;
- failed attempts relevant to the decision;
- contradictions.

Summaries link back to immutable raw evidence.

## Context cache

Cache compiled context by:
`task fingerprint + evidence versions + policy version + model adapter version`.

Reuse only when source versions still match.

## Context quality telemetry

Every model call records:
- input tokens;
- output tokens;
- physical context capacity;
- context utilization %;
- retrieved candidates;
- evidence actually cited/used;
- cache hit;
- latency;
- quality/verifier outcome.

Track:
`verified useful evidence tokens / total input tokens`.

The goal is higher quality with LESS active context.

## Anti-context-bloat acceptance

Bossman 1.6 fails if adding Knowledge Fabric/BOSSNET makes ordinary tasks require
larger prompts without measurable quality gain.

Acceptance tests:
1. 1.5 task replay on 1.6.
2. Equal or better verified result.
3. Median prompt tokens no higher than 1.5 for ordinary tasks.
4. Complex knowledge tasks may use more total retrieval calls, but each worker
   remains within its context budget.
5. "Need more context" triggers progressive retrieval, never automatic full dump.

## Desired outcome

Bossman may eventually hold terabytes of evidence while a local 8B/30B model
sees only the few kilobytes needed for its current decision.

**More memory outside the model; less noise inside the model.**
