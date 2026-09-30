# Bossman 1.6 — Mathematical Memory Retrieval & Control Model

## Decision

Do not build memory as "vector search and dump top-k into the LLM".

Use a **bitemporal hybrid evidence graph + budgeted retrieval controller**.

Open-source references:
- Graphiti: https://github.com/getzep/graphiti
- Letta: https://github.com/letta-ai/letta
- Mem0: https://github.com/mem0ai/mem0
- LightRAG: https://github.com/HKUDS/LightRAG
- GraphZep: https://github.com/aexy-io/graphzep

### Why these matter

**Graphiti** is the closest architectural match to Bossman: incremental temporal
knowledge graph, provenance to source episodes, bi-temporal validity and hybrid
semantic/keyword/graph retrieval. Strong candidate for direct evaluation or
algorithm/reference reuse after license/dependency review.

**Letta** provides the useful separation between small in-context/core memory
and external archival memory retrieved on demand. Bossman should copy the
principle, not the entire agent runtime.

**Mem0** is useful for memory extraction, scoped semantic search, filters and
reranking. Current OSS graph support differs from the hosted Platform, so do not
design Bossman around a Platform-only feature by mistake.

**LightRAG** is useful as a graph+vector retrieval reference for entity/relation
retrieval and compact graph-assisted RAG.

**GraphZep** is a TypeScript temporal-memory reference implementing episodic,
semantic and procedural memory with hybrid/MMR retrieval.

## Memory types

Bossman stores four logically distinct memories:

1. **Episodic** — what happened: task, trade episode, workflow run, conversation.
2. **Semantic** — verified facts/rules distilled from episodes.
3. **Procedural** — skills/workflows/tool contracts.
4. **Working** — tiny current-task state.

Never rank all four identically.

## Bitemporal record

Each fact/edge carries:
- valid_from / valid_to: when it was true in the world;
- recorded_at: when Bossman learned it;
- source episode/evidence;
- confidence;
- verification state;
- domain/project;
- contradiction/supersession links.

Historical replay uses BOTH world time and knowledge time.

## Retrieval as constrained optimization

For query q and candidate memory i define normalized signals in [0,1]:

- S_i = semantic similarity;
- K_i = keyword/BM25 relevance;
- G_i = graph/entity/path relevance;
- T_i = temporal relevance;
- V_i = verification/evidence quality;
- N_i = novelty/non-redundancy;
- P_i = procedural/task-type fit;
- C_i = contradiction value;
- R_i = recency when recency is actually relevant;
- D_i = duplication penalty;
- X_i = stale/superseded penalty;
- token_i = context cost.

Base utility:

`U_i = wS*S + wK*K + wG*G + wT*T + wV*V + wN*N + wP*P + wC*C + wR*R - wD*D - wX*X`

Weights are task-class specific and frozen/versioned.

Selection is a budgeted maximum-coverage problem:

`maximize sum(U_i*x_i) + lambda*Coverage(x) - mu*Redundancy(x)`

subject to:

`sum(token_i*x_i) <= evidence_token_budget`

and mandatory safety/evidence items must be included.

This is knapsack/submodular-style selection; exact optimization is unnecessary
for normal use. Greedy marginal utility/token with MMR-style redundancy penalty
is the default.

## Temporal score

Do not blindly prefer newest memory.

For task timestamp t_q:

- reject facts not yet recorded by knowledge_time for historical replay;
- prefer facts whose valid interval contains t_q;
- otherwise decay by distance to nearest valid interval.

Example:

`T_i = exp(-ln(2) * delta_time / half_life_task)`

Half-life differs by domain:
- live market observation: minutes/hours;
- provider price/rate limit: hours/days;
- software architecture invariant: months;
- owner preference: long-lived until superseded.

## Evidence quality score

Suggested ordinal mapping, benchmarkable:
- raw/unverified = 0.20
- extracted with provenance = 0.40
- independently verified = 0.75
- outcome-verified/replayed = 0.90
- promoted invariant/skill with tests = 1.00

This score affects ranking but never converts a contradiction into truth.

## Contradictions

Contradictory evidence is valuable.

If top memories disagree:
- retrieve both;
- attach source/time/status;
- do not average facts;
- route to verifier if the answer depends on the disagreement.

C_i therefore raises the chance that a material contradiction is included in
context.

## Diversity / MMR

After initial hybrid retrieval, rerank iteratively:

`MMR(i) = alpha*U_i - (1-alpha)*max_similarity(i, selected)`

This prevents 8 near-identical K1M6A clips from consuming the context budget.

## Query planner

Jev/local router first predicts:
- domain;
- memory type needed;
- time scope;
- entity scope;
- task class;
- evidence strictness.

Then query only relevant indexes.

Examples:

"Why did build fail yesterday?"
-> episodic + procedural + Git evidence, recent time scope.

"How do I build clinic competitor analysis?"
-> procedural skill first, semantic support second.

"What did we know about BTC at 14:00?"
-> bitemporal market evidence; forbid future recorded_at.

## Two-stage retrieval

Stage A cheap:
- filters/time/entity;
- BM25;
- vector ANN;
- graph neighborhood.

Union candidates.

Stage B expensive only on candidate set:
- cross-encoder/local reranker or strong local model;
- evidence/verification adjustment;
- MMR;
- token-budget packing.

Do not use an LLM to scan the whole memory store.

## Adaptive retrieval depth

Start with a small packet.

If verifier/model reports:
- missing evidence;
- contradiction unresolved;
- low confidence;
- insufficient entity coverage;

then retrieve the next layer.

Stop when marginal expected information gain is below retrieval/context cost.

Practical proxy:

`continue if expected_quality_gain / added_tokens > threshold`.

## Memory write policy

Not every conversation sentence becomes memory.

Write when one of:
- owner-confirmed durable fact;
- verified outcome;
- reusable workflow/skill;
- important failure;
- decision + rationale/evidence;
- contradiction/update to existing fact.

Transient chatter remains episode/log only.

## Consolidation

Repeated verified episodes can propose a semantic rule.

`episodes -> candidate rule -> independent verifier -> benchmark -> promoted semantic/procedural memory`.

Raw episodes remain provenance.

## Forgetting / decay

Do not delete old evidence merely because it is old.

Decay **retrieval priority**, not provenance.

Superseded facts remain queryable for historical questions.

## Context packet

Final packet to local model:

1. objective + constraints;
2. current workflow state;
3. 3-8 highest-value evidence items;
4. contradiction block if needed;
5. procedural skill contract if available;
6. source refs;
7. explicit UNKNOWNs.

No raw memory dump.

## Evaluation

Build a frozen Bossman MemoryBench from real tasks:
- temporal questions;
- multi-hop project questions;
- exact owner facts;
- superseded facts;
- contradictory evidence;
- workflow reuse;
- trading historical replay;
- Git failure recall.

Metrics:
- Recall@K of required evidence;
- NDCG/MRR;
- temporal correctness;
- contradiction recall;
- provenance correctness;
- answer verifier score;
- input tokens;
- latency;
- useful-evidence-token ratio.

Primary optimization:
`verified_answer_quality / (input_tokens * latency_cost)`

with hard floors for temporal/provenance correctness.

## Adoption plan

Phase 1: implement Bossman native hybrid scorer over existing stores.
Phase 2: benchmark Graphiti alongside native implementation.
Phase 3: benchmark LightRAG/Letta/Mem0 patterns where relevant.
Phase 4: choose per-component winners, not one monolithic memory framework.
Phase 5: keep adapters so storage/retrieval backend remains replaceable.

Do not replace Bossman's evidence ledger with a third-party framework. External
OSS is a retrieval/indexing component; canonical evidence remains Bossman-owned.
