# Pillar 3 — Temporal Knowledge Fabric

Goal: replace flat memory/RAG with evidence-linked knowledge that knows **what,
why, when, source, confidence and what happened afterward**.

Entities:
Project, Person, File, Commit, Workflow, Skill, Model, Experiment, MarketEpisode,
Decision, Claim, Evidence, Outcome.

Edges are temporal:
`SUPPORTED_BY, CONTRADICTED_BY, DERIVED_FROM, SUPERSEDES, CAUSED_BY,
USED_MODEL, PRODUCED, FAILED_BECAUSE, VALID_DURING`.

## Bitemporal truth
Store:
- valid_time: when the fact was true in the world;
- recorded_time: when Bossman learned it.

This prevents later knowledge from leaking into historical decisions.

## Retrieval
Question -> entity/time scope -> graph candidates -> vector/text candidates ->
evidence rerank -> answer with provenance.

## Contradiction handling
Never average incompatible claims. Store both and attach evidence/outcomes.
Supersede only through explicit verified evidence.

## Compression
Old raw traces may be compacted only after immutable evidence references remain.
Canonical decisions/CASEs are never silently rewritten.
