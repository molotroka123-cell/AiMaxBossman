---
name: memory-recall
description: Retrieve only the relevant prior decisions, lessons, conventions and project history needed for the current task, using progressive local memory search instead of loading whole histories.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.1"
  supersedes: "1.0 (obsidian memory addon)"
  category: memory
---

# Memory Recall

Use this skill when the current task may depend on prior work.

Typical triggers:
- existing project
- user says "we discussed this before"
- architecture or product decision may already exist
- task failed previously
- reviewer needs project conventions
- an autonomous mission resumes after interruption

## Goal

Retrieve the smallest useful historical context.

Do NOT:
- load whole Obsidian vault
- load whole conversation history
- retrieve every project note
- expose secrets or excluded memory roots

## Workflow

1. Rewrite the current need as a precise retrieval query.
2. Call `memory.search`.
3. Inspect the top summaries/snippets.
4. If necessary call `memory.expand` only on the strongest few results.
5. Prefer:
   - newer explicit decisions
   - project-specific evidence
   - verified outcomes
   over generic or older notes.
6. Detect conflicts between memories.
7. Return a compact context pack with source references.
8. Inject only the context required for the next reasoning step.

## Default budget

Target:
- 6–12 initial retrieval candidates
- 3–6 final evidence items
- <= ~7000 memory tokens unless mission policy explicitly allows more

## Conflict handling

If two memories disagree:

- do not silently choose one
- compare timestamps/provenance
- prefer explicit superseding decisions
- state uncertainty if unresolved

## Output

Provide:

- relevant decisions
- useful lessons
- unresolved conflicts
- source references
- confidence
- what was deliberately omitted as irrelevant

Memory is evidence, not infallible truth.
